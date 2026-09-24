import unittest

from inferscope.analyzers.kv_cache import analyze_kv_events
from inferscope.core.events import Event


class KVCacheAnalyzerTests(unittest.TestCase):
    def test_capacity_must_be_a_positive_integer(self):
        for capacity in (True, 1.5, 0):
            with self.subTest(capacity=capacity):
                with self.assertRaises(ValueError):
                    analyze_kv_events([], capacity_blocks=capacity)

    def test_tracks_reuse_free_and_eviction(self):
        events = [
            Event(1, "KV_ALLOCATE", "req-a", {"block_id": 7, "token_count": 4}),
            Event(2, "KV_REUSE", "req-b", {"block_id": 7}),
            Event(3, "KV_FREE", "req-a", {"block_id": 7}),
            Event(4, "KV_FREE", "req-b", {"block_id": 7}),
            Event(5, "KV_EVICT", "req-b", {"block_id": 7}),
        ]

        report = analyze_kv_events(events, capacity_blocks=4)

        self.assertEqual(report.allocated_blocks, 1)
        self.assertEqual(report.reused_blocks, 1)
        self.assertEqual(report.evictions, 1)
        self.assertEqual(report.peak_blocks, 1)
        self.assertEqual(report.current_blocks, 0)
        self.assertEqual(report.utilization, 0.0)
        self.assertEqual(report.to_mapping()["analysis_mode"], "DERIVED")
        self.assertEqual(report.to_mapping()["utilization_source"], "DERIVED")

    def test_utilization_without_capacity_is_unknown(self):
        report = analyze_kv_events([
            Event(1, "KV_ALLOCATE", "req-a", {"block_id": 7, "token_count": 4}),
        ])
        self.assertIsNone(report.utilization)
        self.assertEqual(report.to_mapping()["utilization_source"], "UNKNOWN")

    def test_rejects_free_by_request_that_does_not_hold_block(self):
        events = [
            Event(1, "KV_ALLOCATE", "req-a", {"block_id": 7, "token_count": 4}),
            Event(2, "KV_FREE", "req-b", {"block_id": 7}),
        ]

        with self.assertRaisesRegex(ValueError, "req-b"):
            analyze_kv_events(events, capacity_blocks=4)


if __name__ == "__main__":
    unittest.main()
