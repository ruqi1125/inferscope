import unittest

from inferscope.adapters.sglang.adapter import SGLangAdapter
from inferscope.adapters.vllm.adapter import VLLMAdapter
from inferscope.analyzers.request import summarize_requests


def span(name, request_id, start, end, attributes=None, parent=""):
    values = [{"key": "gen_ai.request.id", "value": {"stringValue": request_id}}]
    for key, value in (attributes or {}).items():
        encoded = {"doubleValue": value} if isinstance(value, float) else {"stringValue": value}
        values.append({"key": key, "value": encoded})
    return {"name": name, "startTimeUnixNano": str(start), "endTimeUnixNano": str(end), "parentSpanId": parent, "attributes": values}


class AdapterTests(unittest.TestCase):
    def test_vllm_maps_measured_latency_attributes_to_lifecycle(self):
        document = {"resourceSpans": [{"scopeSpans": [{"spans": [span(
            "vllm.request", "req-1", 1_000_000_000, 1_100_000_000,
            {"gen_ai.latency.time_in_queue": 0.01, "gen_ai.latency.time_in_model_prefill": 0.02},
        )]}]}]}

        events = VLLMAdapter().to_events(document)

        self.assertEqual([event.event_type for event in events], ["REQUEST_ARRIVED", "REQUEST_QUEUED", "REQUEST_SCHEDULED", "PREFILL_STARTED", "PREFILL_FINISHED", "REQUEST_FINISHED"])
        self.assertEqual(events[-1].timestamp_ns, 1_100_000_000)

    def test_vllm_request_span_with_parent_preserves_native_request_metrics(self):
        document = {"resourceSpans": [{"scopeSpans": [{"spans": [span(
            "llm_request", "req-3", 1_000_000_000, 1_150_000_000,
            {
                "gen_ai.latency.time_in_queue": 0.02,
                "gen_ai.latency.time_to_first_token": 0.055,
                "gen_ai.latency.time_in_model_prefill": 0.04,
                "gen_ai.latency.time_in_model_decode": 0.03,
                "gen_ai.latency.e2e": 0.15,
                "gen_ai.usage.prompt_tokens": 40,
                "gen_ai.usage.completion_tokens": 3,
            },
            parent="agent-span",
        )]}]}]}

        events = VLLMAdapter().to_events(document)

        self.assertEqual(
            [event.event_type for event in events],
            [
                "REQUEST_ARRIVED",
                "REQUEST_QUEUED",
                "REQUEST_SCHEDULED",
                "PREFILL_STARTED",
                "PREFILL_FINISHED",
                "REQUEST_FINISHED",
            ],
        )
        summary = summarize_requests(events)[0]
        self.assertEqual(summary.input_tokens, 40)
        self.assertEqual(summary.output_tokens, 3)
        self.assertEqual(summary.queue_ns, 20_000_000)
        self.assertEqual(summary.prefill_ns, 40_000_000)
        self.assertEqual(summary.ttft_ns, 55_000_000)
        self.assertEqual(summary.decode_ns, 30_000_000)
        self.assertEqual(summary.e2e_ns, 150_000_000)

    def test_sglang_maps_known_stage_spans_and_ignores_unknown_stages(self):
        document = {"resourceSpans": [{"scopeSpans": [{"spans": [
            span("request", "req-2", 10, 100),
            span("prefill_waiting", "req-2", 20, 40, parent="root"),
            span("prefill_forward", "req-2", 40, 70, parent="root"),
            span("new_unknown_stage", "req-2", 50, 60, parent="root"),
        ]}]}]}

        events = SGLangAdapter().to_events(document)

        self.assertIn("PREFILL_STARTED", [event.event_type for event in events])
        self.assertIn("PREFILL_FINISHED", [event.event_type for event in events])
        self.assertNotIn("new_unknown_stage", [event.event_type for event in events])


if __name__ == "__main__":
    unittest.main()
