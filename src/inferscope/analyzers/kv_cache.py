"""从统一事件流重建 KV block 状态。"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from inferscope.core.events import Event


@dataclass(slots=True)
class _BlockState:
    token_count: int
    references: dict[str, int]

    @property
    def reference_count(self) -> int:
        return sum(self.references.values())


@dataclass(frozen=True, slots=True)
class KVTimelinePoint:
    timestamp_ns: int
    event_type: str
    block_id: int
    request_id: str
    current_blocks: int
    active_references: int


@dataclass(frozen=True, slots=True)
class KVCacheReport:
    allocated_blocks: int
    reused_blocks: int
    freed_references: int
    evictions: int
    current_blocks: int
    peak_blocks: int
    capacity_blocks: int | None
    utilization: float | None
    timeline: tuple[KVTimelinePoint, ...]
    analysis_mode: str
    utilization_source: str

    def to_mapping(self) -> dict[str, object]:
        return {
            "allocated_blocks": self.allocated_blocks,
            "reused_blocks": self.reused_blocks,
            "freed_references": self.freed_references,
            "evictions": self.evictions,
            "current_blocks": self.current_blocks,
            "peak_blocks": self.peak_blocks,
            "capacity_blocks": self.capacity_blocks,
            "utilization": self.utilization,
            "timeline": [asdict(point) for point in self.timeline],
            "analysis_mode": self.analysis_mode,
            "utilization_source": self.utilization_source,
        }


def analyze_kv_events(events: list[Event], capacity_blocks: int | None = None) -> KVCacheReport:
    if capacity_blocks is not None and (isinstance(capacity_blocks, bool) or not isinstance(capacity_blocks, int) or capacity_blocks <= 0):
        raise ValueError("capacity_blocks 必须大于 0")
    blocks: dict[int, _BlockState] = {}
    allocated = reused = freed = evictions = peak = 0
    timeline: list[KVTimelinePoint] = []
    for event in sorted(events, key=lambda row: row.timestamp_ns):
        if not event.event_type.startswith("KV_"):
            continue
        block_id = event.attributes.get("block_id")
        if isinstance(block_id, bool) or not isinstance(block_id, int) or block_id < 0:
            raise ValueError(f"{event.request_id}: {event.event_type} 缺少有效 block_id")
        state = blocks.get(block_id)
        if event.event_type == "KV_ALLOCATE":
            token_count = event.attributes.get("token_count")
            if isinstance(token_count, bool) or not isinstance(token_count, int) or token_count <= 0:
                raise ValueError(f"{event.request_id}: KV_ALLOCATE 需要正整数 token_count")
            if state is not None:
                raise ValueError(f"{event.request_id}: block {block_id} 已存在，不能重复分配")
            blocks[block_id] = _BlockState(token_count, {event.request_id: 1})
            allocated += 1
        elif event.event_type == "KV_REUSE":
            if state is None:
                raise ValueError(f"{event.request_id}: block {block_id} 尚未分配，不能复用")
            state.references[event.request_id] = state.references.get(event.request_id, 0) + 1
            reused += 1
        elif event.event_type == "KV_FREE":
            if state is None or state.references.get(event.request_id, 0) == 0:
                raise ValueError(f"{event.request_id}: 没有持有 block {block_id}，不能释放")
            state.references[event.request_id] -= 1
            if state.references[event.request_id] == 0:
                del state.references[event.request_id]
            freed += 1
            # refcount 为零时，block 留在缓存中，直到 KV_EVICT 回收。
        elif event.event_type == "KV_EVICT":
            if state is None:
                raise ValueError(f"{event.request_id}: block {block_id} 不存在，不能淘汰")
            if state.reference_count > 0:
                raise ValueError(f"{event.request_id}: block {block_id} 仍有活动引用，不能淘汰")
            del blocks[block_id]
            evictions += 1
        else:
            raise ValueError(f"未知 KV 事件类型: {event.event_type}")
        peak = max(peak, len(blocks))
        timeline.append(KVTimelinePoint(event.timestamp_ns, event.event_type, block_id, event.request_id, len(blocks), sum(item.reference_count for item in blocks.values())))
    current = len(blocks)
    utilization = current / capacity_blocks if capacity_blocks is not None else None
    return KVCacheReport(allocated, reused, freed, evictions, current, peak, capacity_blocks, utilization, tuple(timeline), "DERIVED", "DERIVED" if capacity_blocks is not None else "UNKNOWN")
