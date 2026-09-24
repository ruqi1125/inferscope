"""缓存策略共享接口和统计数据。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class CacheLookup:
    queried_tokens: int
    matched_tokens: int
    miss_reason: str

    @property
    def recomputed_tokens(self) -> int:
        return self.queried_tokens - self.matched_tokens


@dataclass(slots=True)
class CacheStats:
    evictions: int = 0
    peak_blocks: int = 0


class PrefixCache(Protocol):
    block_size: int
    capacity_blocks: int
    stats: CacheStats

    def lookup(self, tokens: tuple[int, ...]) -> CacheLookup: ...
    def insert(self, tokens: tuple[int, ...]) -> None: ...
    @property
    def cached_blocks(self) -> int: ...


def classify_miss(queried: int, matched: int, block_size: int, cached_blocks: int) -> str:
    if matched == queried:
        return "NONE"
    if cached_blocks == 0:
        return "COLD_MISS"
    if queried % block_size and matched == queried - queried % block_size:
        return "BLOCK_ALIGNMENT"
    if matched:
        return "PREFIX_DIVERGENCE"
    return "UNKNOWN"
