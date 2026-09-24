"""将 vLLM GenAI OpenTelemetry request spans 转为统一事件。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from inferscope.adapters.base import TraceAdapter, integer, iter_spans, numeric, span_event
from inferscope.core.events import Event


class VLLMAdapter(TraceAdapter):
    framework = "vllm"

    def to_events(self, document: Mapping[str, Any]) -> list[Event]:
        events: list[Event] = []
        for raw in iter_spans(document):
            parsed = self._span(raw)
            if parsed is None:
                continue
            request_id, start, end, attrs, root = parsed
            request_span = attrs["name"] == "llm_request" or root
            if not request_span:
                events.append(span_event(start, "FRAMEWORK_SPAN", request_id, {"framework": self.framework, "span_name": attrs["name"], "duration_ns": end - start}))
                continue

            prompt_tokens = integer(attrs.get("gen_ai.usage.prompt_tokens"))
            arrived_attrs: dict[str, Any] = {"source": self.framework}
            if prompt_tokens is not None:
                arrived_attrs["input_tokens"] = prompt_tokens
            events.append(span_event(start, "REQUEST_ARRIVED", request_id, arrived_attrs))

            queue_seconds = self._duration(attrs.get("gen_ai.latency.time_in_queue"))
            prefill_seconds = self._duration(attrs.get("gen_ai.latency.time_in_model_prefill"))
            ttft_seconds = self._duration(attrs.get("gen_ai.latency.time_to_first_token"))
            if queue_seconds is not None:
                queued_at = start
                scheduled_at = start + queue_seconds
                events.extend((
                    span_event(queued_at, "REQUEST_QUEUED", request_id, {"source": self.framework}),
                    span_event(scheduled_at, "REQUEST_SCHEDULED", request_id, {"source": self.framework}),
                ))
                if prefill_seconds is not None:
                    events.extend((
                        span_event(scheduled_at, "PREFILL_STARTED", request_id, {"source": self.framework}),
                        span_event(scheduled_at + prefill_seconds, "PREFILL_FINISHED", request_id, {"source": self.framework}),
                    ))

            completion_tokens = integer(attrs.get("gen_ai.usage.completion_tokens"))
            finished_attrs: dict[str, Any] = {"source": self.framework}
            if completion_tokens is not None:
                finished_attrs["output_tokens"] = completion_tokens
            measured_durations = {
                "queue_ns": queue_seconds,
                "prefill_ns": prefill_seconds,
                "ttft_ns": ttft_seconds,
                "decode_ns": self._duration(attrs.get("gen_ai.latency.time_in_model_decode")),
                "e2e_ns": self._duration(attrs.get("gen_ai.latency.e2e")),
            }
            finished_attrs.update({key: value for key, value in measured_durations.items() if value is not None})
            events.append(span_event(end, "REQUEST_FINISHED", request_id, finished_attrs))
        return sorted(events, key=lambda event: event.timestamp_ns)

    @staticmethod
    def _duration(value: Any) -> int | None:
        seconds = numeric(value)
        if seconds is None or seconds < 0:
            return None
        return int(seconds * 1_000_000_000)
