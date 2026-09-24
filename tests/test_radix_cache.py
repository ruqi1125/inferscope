import unittest

from inferscope.cache.radix_cache import RadixPrefixCache


class RadixPrefixCacheTests(unittest.TestCase):
    def test_evicted_token_paths_are_reclaimed_from_the_trie(self):
        cache = RadixPrefixCache(block_size=2, capacity_blocks=2)
        for value in range(100):
            cache.insert((value * 2, value * 2 + 1))

        self.assertLessEqual(cache.stored_nodes, 4)
        self.assertEqual(cache.lookup((198, 199)).matched_tokens, 2)
        self.assertEqual(cache.lookup((0, 1)).matched_tokens, 0)

    def test_reclaim_keeps_a_shared_prefix_used_by_a_live_branch(self):
        cache = RadixPrefixCache(block_size=2, capacity_blocks=2)
        cache.insert((1, 2, 3, 4))
        cache.insert((1, 2, 5, 6))

        self.assertEqual(cache.lookup((1, 2, 5, 6)).matched_tokens, 4)
        self.assertEqual(cache.lookup((1, 2, 3, 4)).matched_tokens, 2)
        self.assertEqual(cache.stored_nodes, 4)

    def test_capacity_keeps_a_usable_prefix_when_prompt_exceeds_capacity(self):
        cache = RadixPrefixCache(block_size=2, capacity_blocks=2)
        tokens = (1, 2, 3, 4, 5, 6)
        cache.insert(tokens)

        result = cache.lookup(tokens)

        self.assertEqual(result.matched_tokens, 4)
        self.assertEqual(cache.cached_blocks, 2)
        self.assertEqual(result.miss_reason, "CAPACITY_PRESSURE")

    def test_divergent_prefix_evicts_old_prefix_and_keeps_new_one(self):
        cache = RadixPrefixCache(block_size=2, capacity_blocks=2)
        cache.insert((1, 2, 3, 4))
        cache.insert((9, 8, 7, 6))

        self.assertEqual(cache.lookup((9, 8, 7, 6)).matched_tokens, 4)
        self.assertEqual(cache.lookup((1, 2, 3, 4)).matched_tokens, 0)


if __name__ == "__main__":
    unittest.main()
