import unittest

from inferscope.analyzers.workload import analyze_workload
from inferscope.cache.hash_cache import HashBlockCache
from inferscope.core.request import WorkloadRequest
from inferscope.replay.engine import replay


class WorkloadAnalyzerTests(unittest.TestCase):
    def test_reports_potential_and_actual_reuse_without_false_attribution(self):
        requests = [
            WorkloadRequest("req-a", 0.0, (1, 2, 3, 4), 1),
            WorkloadRequest("req-b", 1.0, (1, 2, 3, 4), 1),
        ]
        actual = replay(requests, HashBlockCache(block_size=2, capacity_blocks=8), "hash")

        report = analyze_workload(requests, actual, block_size=2)

        self.assertEqual(report.total_input_tokens, 8)
        self.assertEqual(report.potential_reuse_tokens, 4)
        self.assertEqual(report.actual_reuse_tokens, 4)
        self.assertEqual(report.lost_reuse_tokens, 0)
        self.assertEqual(report.lost_reuse_by_reason["UNKNOWN"], 0)


if __name__ == "__main__":
    unittest.main()
