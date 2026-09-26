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

    def test_queue_uses_queued_boundary_and_duplicate_queue_stays_unknown(self):
        summary = summarize_requests([
            Event(10, "REQUEST_ARRIVED", "r"),
            Event(20, "REQUEST_QUEUED", "r"),
            Event(30, "REQUEST_SCHEDULED", "r"),
            Event(40, "REQUEST_FINISHED", "r"),
        ])[0]

        self.assertEqual(summary.queue_ns, 10)
        self.assertEqual(summary.latency_sources["queue_ns"], "DERIVED")

        ambiguous = summarize_requests([
            Event(10, "REQUEST_ARRIVED", "r"),
            Event(20, "REQUEST_QUEUED", "r"),
            Event(21, "REQUEST_QUEUED", "r"),
            Event(30, "REQUEST_SCHEDULED", "r"),
        ])[0]
        self.assertIsNone(ambiguous.queue_ns)
        self.assertEqual(ambiguous.latency_sources["queue_ns"], "UNKNOWN")
        self.assertEqual(ambiguous.ambiguous_event_types, ("REQUEST_QUEUED",))

    def test_cached_tokens_keep_evidence_and_reject_impossible_values(self):
        observed_zero = summarize_requests([
            Event(1, "REQUEST_ARRIVED", "zero", {"input_tokens": 10}),
            Event(2, "REQUEST_FINISHED", "zero", {
                "cached_tokens": 0,
                "cached_tokens_source": "OBSERVED",
            }),
        ])[0]
        self.assertEqual(observed_zero.cached_tokens, 0)
        self.assertEqual(observed_zero.cached_tokens_source, "OBSERVED")

        impossible = summarize_requests([
            Event(1, "REQUEST_ARRIVED", "bad", {"input_tokens": 10}),
            Event(2, "REQUEST_FINISHED", "bad", {
                "cached_tokens": 11,
                "cached_tokens_source": "OBSERVED",
            }),
        ])[0]
        self.assertIsNone(impossible.cached_tokens)
        self.assertEqual(impossible.cached_tokens_source, "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
