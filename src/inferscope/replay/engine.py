"""确定性 prefix-cache workload 回放。"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from inferscope.cache.base import PrefixCache
from inferscope.core.request import WorkloadRequest


@dataclass(frozen=True, slots=True)
class RequestReplay:
    request_id: str
    input_tokens: int
    matched_tokens: int
    recomputed_tokens: int
    hit_ratio: float
    miss_reason: str


@dataclass(frozen=True, slots=True)
class ReplayReport:
    cache: str
    requests: int
    input_tokens: int
    cached_tokens: int
    computed_tokens: int
    hit_ratio: float
    evictions: int
    peak_blocks: int
    capacity_blocks: int
    block_size: int
    request_results: tuple[RequestReplay, ...]
    analysis_mode: str = "SIMULATED"

    def to_mapping(self) -> dict[str, object]:
        result = asdict(self)
        result["request_results"] = [asdict(row) for row in self.request_results]
        return result


def replay(requests: list[WorkloadRequest], cache: PrefixCache, cache_name: str) -> ReplayReport:
    ordered = sorted(enumerate(requests), key=lambda pair: (pair[1].timestamp, pair[0]))
    rows: list[RequestReplay] = []
    for _, request in ordered:
        lookup = cache.lookup(request.input_token_ids)
        rows.append(
            RequestReplay(
                request_id=request.request_id,
                input_tokens=len(request.input_token_ids),
                matched_tokens=lookup.matched_tokens,
                recomputed_tokens=lookup.recomputed_tokens,
                hit_ratio=lookup.matched_tokens / len(request.input_token_ids) if request.input_token_ids else 0.0,
                miss_reason=lookup.miss_reason,
            )
        )
        cache.insert(request.input_token_ids)
    input_tokens = sum(row.input_tokens for row in rows)
    cached_tokens = sum(row.matched_tokens for row in rows)
    return ReplayReport(
        cache=cache_name,
        requests=len(rows),
        input_tokens=input_tokens,
        cached_tokens=cached_tokens,
        computed_tokens=input_tokens - cached_tokens,
        hit_ratio=cached_tokens / input_tokens if input_tokens else 0.0,
        evictions=cache.stats.evictions,
        peak_blocks=cache.stats.peak_blocks,
        capacity_blocks=cache.capacity_blocks,
        block_size=cache.block_size,
        request_results=tuple(rows),
    )
