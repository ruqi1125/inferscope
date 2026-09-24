import unittest

from inferscope.adapters.sglang.adapter import SGLangAdapter
from inferscope.adapters.vllm.adapter import VLLMAdapter


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
