"""共享 token 前缀的 radix trie 缓存，按完整 block LRU 淘汰。"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field

from inferscope.cache.base import CacheLookup, CacheStats, classify_miss, validate_cache_config


@dataclass(slots=True)
class _Node:
    children: dict[int, _Node] = field(default_factory=dict)
    terminal: bool = False


class RadixPrefixCache:
    def __init__(self, block_size: int = 16, capacity_blocks: int = 4096):
        validate_cache_config(block_size, capacity_blocks)
        self.block_size = block_size
        self.capacity_blocks = capacity_blocks
        self._root = _Node()
        self._lru: OrderedDict[tuple[tuple[int, ...], ...], _Node] = OrderedDict()
        self.stats = CacheStats()

    @property
    def cached_blocks(self) -> int:
        return len(self._lru)

    @property
    def stored_nodes(self) -> int:
        count = 0
        pending = list(self._root.children.values())
        while pending:
            node = pending.pop()
            count += 1
            pending.extend(node.children.values())
        return count

    def _walk(self, tokens: tuple[int, ...], create: bool = False) -> _Node | None:
        node = self._root
        for token in tokens:
            child = node.children.get(token)
            if child is None:
                if not create:
                    return None
                child = node.children[token] = _Node()
            node = child
        return node

    def _prune(self, tokens: tuple[int, ...]) -> None:
        node = self._root
        path: list[tuple[_Node, int, _Node]] = []
        for token in tokens:
            child = node.children.get(token)
            if child is None:
                return
            path.append((node, token, child))
            node = child
        node.terminal = False
        for parent, token, child in reversed(path):
            if child.terminal or child.children:
                break
            del parent.children[token]

    def lookup(self, tokens: tuple[int, ...]) -> CacheLookup:
        node = self._root
        matched = 0
        prefix: list[tuple[int, ...]] = []
        for offset in range(0, len(tokens) - self.block_size + 1, self.block_size):
            block = tokens[offset : offset + self.block_size]
            prefix.append(block)
            for token in block:
                node = node.children.get(token)  # type: ignore[assignment]
                if node is None:
                    return CacheLookup(len(tokens), matched, classify_miss(len(tokens), matched, self.block_size, self.cached_blocks, self.capacity_blocks))
            if not node.terminal:
                return CacheLookup(len(tokens), matched, classify_miss(len(tokens), matched, self.block_size, self.cached_blocks, self.capacity_blocks))
            self._lru.move_to_end(tuple(prefix))
            matched += self.block_size
        return CacheLookup(len(tokens), matched, classify_miss(len(tokens), matched, self.block_size, self.cached_blocks, self.capacity_blocks))

    def insert(self, tokens: tuple[int, ...]) -> None:
        prefix: list[tuple[int, ...]] = []
        limit = min(len(tokens) // self.block_size, self.capacity_blocks)
        for offset in range(0, limit * self.block_size, self.block_size):
            block = tokens[offset : offset + self.block_size]
            prefix.append(block)
            key = tuple(prefix)
            prefix_tokens = tuple(token for chunk in prefix for token in chunk)
            node = self._walk(prefix_tokens, create=True)
            assert node is not None
            node.terminal = True
            self._lru[key] = node
            self._lru.move_to_end(key)
            while len(self._lru) > self.capacity_blocks:
                old_prefix, _ = self._lru.popitem(last=False)
                evicted = [old_prefix]
                for descendant in tuple(self._lru):
                    if descendant[: len(old_prefix)] == old_prefix:
                        self._lru.pop(descendant)
                        evicted.append(descendant)
                for prefix_key in sorted(evicted, key=len, reverse=True):
                    self._prune(tuple(token for block in prefix_key for token in block))
                self.stats.evictions += len(evicted)
            self.stats.peak_blocks = max(self.stats.peak_blocks, len(self._lru))
