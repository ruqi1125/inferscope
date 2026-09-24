import unittest

from inferscope.cache.hash_cache import HashBlockCache


class HashBlockCacheTests(unittest.TestCase):
    def test_capacity_keeps_a_usable_prefix_when_prompt_exceeds_capacity(self):
        cache = HashBlockCache(block_size=2, capacity_blocks=2)
        tokens = (1, 2, 3, 4, 5, 6)
        cache.insert(tokens)

        result = cache.lookup(tokens)

        self.assertEqual(result.matched_tokens, 4)
        self.assertEqual(cache.cached_blocks, 2)
        self.assertEqual(result.miss_reason, "CAPACITY_PRESSURE")

    def test_classifies_partial_tail_as_alignment_miss(self):
        cache = HashBlockCache(block_size=2, capacity_blocks=2)
        cache.insert((1, 2, 3, 4))

        result = cache.lookup((1, 2, 3))

        self.assertEqual(result.matched_tokens, 2)
        self.assertEqual(result.miss_reason, "BLOCK_ALIGNMENT")


if __name__ == "__main__":
    unittest.main()
