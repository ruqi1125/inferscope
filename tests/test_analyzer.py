import unittest

from inferscope.analyzers.request import summarize_requests
from inferscope.core.events import Event


class RequestAnalyzerTests(unittest.TestCase):
    def test_computes_latency_stages_from_event_boundaries(self):
        events = [
            Event(1_000_000, "REQUEST_ARRIVED", "r1"),
            Event(3_000_000, "REQUEST_SCHEDULED", "r1"),
            Event(4_000_000, "PREFILL_STARTED", "r1"),
            Event(14_000_000, "PREFILL_FINISHED", "r1"),
            Event(22_000_000, "REQUEST_FINISHED", "r1", {"output_tokens": 4}),
        ]

        summary = summarize_requests(events)[0]

        self.assertEqual(summary.queue_ns, 2_000_000)
        self.assertEqual(summary.prefill_ns, 10_000_000)
        self.assertEqual(summary.decode_ns, 8_000_000)
        self.assertEqual(summary.ttft_ns, 13_000_000)
        self.assertEqual(summary.output_tokens, 4)

    def test_missing_stage_boundaries_remain_unknown(self):
        summary = summarize_requests([Event(1, "REQUEST_ARRIVED", "r2")])[0]

        self.assertIsNone(summary.queue_ns)
        self.assertIsNone(summary.prefill_ns)
        self.assertIsNone(summary.decode_ns)
        self.assertIsNone(summary.ttft_ns)


if __name__ == "__main__":
    unittest.main()
