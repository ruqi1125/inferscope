"""Workload 层面的复用潜力与实际复用分析。"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from inferscope.cache.radix_cache import RadixPrefixCache
from inferscope.core.request import WorkloadRequest
from inferscope.replay.engine import ReplayReport, replay


@dataclass(frozen=True, slots=True)
class WorkloadReuseReport:
    requests: int
    total_input_tokens: int
    potential_reuse_tokens: int
    actual_reuse_tokens: int
    lost_reuse_tokens: int
    potential_hit_ratio: float
    actual_hit_ratio: float
    lost_reuse_ratio: float
    lost_reuse_by_reason: dict[str, int]

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)


def analyze_workload(
    requests: list[WorkloadRequest],
    actual: ReplayReport,
    block_size: int,
) -> WorkloadReuseReport:
    if block_size <= 0:
        raise ValueError("block_size 必须大于 0")
    total = sum(len(request.input_token_ids) for request in requests)
    full_blocks = sum(len(request.input_token_ids) // block_size for request in requests)
    ideal_capacity = max(1, full_blocks)
    potential = replay(requests, RadixPrefixCache(block_size, ideal_capacity), "radix").cached_tokens
    actual_tokens = actual.cached_tokens
    lost = max(0, potential - actual_tokens)
    return WorkloadReuseReport(
        requests=len(requests),
        total_input_tokens=total,
        potential_reuse_tokens=potential,
        actual_reuse_tokens=actual_tokens,
        lost_reuse_tokens=lost,
        potential_hit_ratio=potential / total if total else 0.0,
        actual_hit_ratio=actual_tokens / total if total else 0.0,
        lost_reuse_ratio=lost / total if total else 0.0,
        lost_reuse_by_reason={"UNKNOWN": lost},
    )
