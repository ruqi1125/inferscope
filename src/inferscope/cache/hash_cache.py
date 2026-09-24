"""以固定 token block 哈希键模拟前缀缓存。"""

from __future__ import annotations

from collections import OrderedDict

from inferscope.cache.base import CacheLookup, CacheStats, classify_miss


class HashBlockCache:
    def __init__(self, block_size: int = 16, capacity_blocks: int = 4096):
        if block_size <= 0 or capacity_blocks < 0:
            raise ValueError("block_size 必须大于 0，capacity_blocks 不能为负数")
        self.block_size = block_size
        self.capacity_blocks = capacity_blocks
        self._blocks: OrderedDict[tuple[tuple[int, ...], ...], None] = OrderedDict()
        self.stats = CacheStats()

    @property
    def cached_blocks(self) -> int:
        return len(self._blocks)

    def lookup(self, tokens: tuple[int, ...]) -> CacheLookup:
        matched = 0
        prefix: list[tuple[int, ...]] = []
        for offset in range(0, len(tokens) - self.block_size + 1, self.block_size):
            block = tokens[offset : offset + self.block_size]
            prefix.append(block)
            key = tuple(prefix)
            if key not in self._blocks:
                break
            self._blocks.move_to_end(key)
            matched += self.block_size
        return CacheLookup(len(tokens), matched, classify_miss(len(tokens), matched, self.block_size, self.cached_blocks))

    def insert(self, tokens: tuple[int, ...]) -> None:
        prefix: list[tuple[int, ...]] = []
        for offset in range(0, len(tokens) - self.block_size + 1, self.block_size):
            block = tokens[offset : offset + self.block_size]
            prefix.append(block)
            key = tuple(prefix)
            self._blocks[key] = None
            self._blocks.move_to_end(key)
            while len(self._blocks) > self.capacity_blocks:
                oldest, _ = self._blocks.popitem(last=False)
                removed = 1
                for descendant in tuple(self._blocks):
                    if descendant[: len(oldest)] == oldest:
                        del self._blocks[descendant]
                        removed += 1
                self.stats.evictions += removed
            self.stats.peak_blocks = max(self.stats.peak_blocks, len(self._blocks))
