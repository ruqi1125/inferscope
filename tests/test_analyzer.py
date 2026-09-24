import unittest

from inferscope.analyzers.request import summarize_requests
from inferscope.core.events import Event


class RequestAnalyzerTests(unittest.TestCase):
    def test_repeated_lifecycle_boundaries_are_reported_and_not_collapsed(self):
        events = [
            Event(1, "REQUEST_ARRIVED", "r"),
            Event(2, "REQUEST_ARRIVED", "r"),
            Event(3, "REQUEST_SCHEDULED", "r"),
            Event(4, "REQUEST_SCHEDULED", "r"),
            Event(5, "PREFILL_STARTED", "r"),
            Event(6, "PREFILL_FINISHED", "r"),
            Event(7, "REQUEST_FINISHED", "r", {"e2e_ns": 6}),
        ]

        summary = summarize_requests(events)[0]

        self.assertEqual(summary.ambiguous_event_types, ("REQUEST_ARRIVED", "REQUEST_SCHEDULED"))
        self.assertIsNone(summary.queue_ns)
        self.assertIsNone(summary.ttft_ns)
        self.assertEqual(summary.latency_sources["queue_ns"], "UNKNOWN")
        self.assertEqual(summary.latency_sources["e2e_ns"], "OBSERVED")

    def test_repeated_prefix_lookups_are_reported_as_ambiguous(self):
        summary = summarize_requests([
            Event(1, "PREFIX_LOOKUP", "r", {"queried_tokens": 4}),
            Event(2, "PREFIX_LOOKUP", "r", {"queried_tokens": 8}),
        ])[0]
        self.assertEqual(summary.ambiguous_event_types, ("PREFIX_LOOKUP",))
        self.assertIsNone(summary.input_tokens)

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
