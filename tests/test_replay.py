import unittest

from inferscope.cache.hash_cache import HashBlockCache
from inferscope.core.request import WorkloadRequest
from inferscope.replay.engine import replay


class ReplayTests(unittest.TestCase):
    def test_replays_by_timestamp_and_counts_only_input_tokens(self):
        requests = [
            WorkloadRequest("later", 2.0, (1, 2, 3, 4), 100),
            WorkloadRequest("earlier", 1.0, (1, 2, 3, 4), 50),
        ]

        report = replay(requests, HashBlockCache(block_size=2, capacity_blocks=8), "hash")

        self.assertEqual([row.request_id for row in report.request_results], ["earlier", "later"])
        self.assertEqual(report.cached_tokens, 4)
        self.assertEqual(report.computed_tokens, 4)
        self.assertEqual(report.input_tokens, 8)

    def test_empty_inputs_have_zero_hit_ratio(self):
        report = replay([], HashBlockCache(block_size=2, capacity_blocks=8), "hash")

        self.assertEqual(report.requests, 0)
        self.assertEqual(report.hit_ratio, 0.0)


if __name__ == "__main__":
    unittest.main()
