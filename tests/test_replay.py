import unittest

from inferscope.cache.hash_cache import HashBlockCache
from inferscope.core.request import WorkloadRequest
from inferscope.replay.engine import replay
from inferscope.storage.jsonl import read_workload
from pathlib import Path
from tempfile import TemporaryDirectory
from decimal import Decimal


class ReplayTests(unittest.TestCase):
    def test_timestamps_are_normalized_to_nanoseconds_for_replay_order(self):
        requests = [
            WorkloadRequest("later-in-ns", 0.0000000014, (1, 2), 0),
            WorkloadRequest("first-in-ns", 0.0000000011, (3, 4), 0),
        ]

        report = replay(requests, HashBlockCache(2, 0), "hash")

        self.assertEqual([row.request_id for row in report.request_results], ["later-in-ns", "first-in-ns"])
        self.assertEqual(requests[0].timestamp_ns, 1)
        self.assertEqual(requests[1].timestamp_ns, 1)

    def test_json_decimal_timestamp_is_not_rounded_through_binary_float(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "workload.jsonl"
            path.write_text(
                '{"request_id":"r","timestamp":100000000.0000000015,"input_token_ids":[],"output_tokens":0}\n',
                encoding="utf-8",
            )
            request = read_workload(path)[0]

        self.assertEqual(request.timestamp_ns, 100_000_000_000_000_002)

    def test_timestamp_conversion_keeps_precision_beyond_decimal_context(self):
        request = WorkloadRequest("r", Decimal("12345678901234567890.0000000015"), (), 0)
        self.assertEqual(request.timestamp_ns, 12_345_678_901_234_567_890_000_000_002)

    def test_rejects_timestamp_too_large_for_bounded_nanosecond_conversion(self):
        request = WorkloadRequest("r", Decimal("1e5000"), (), 0)
        with self.assertRaisesRegex(ValueError, "timestamp 超出支持范围"):
            _ = request.timestamp_ns

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
