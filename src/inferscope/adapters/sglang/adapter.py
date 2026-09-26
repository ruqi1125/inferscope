"""将 SGLang request/stage spans 转为统一事件。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from inferscope.adapters.base import TraceAdapter, integer, iter_spans, numeric, span_event
from inferscope.core.events import Event
from inferscope.core.time import seconds_to_nanoseconds


class SGLangAdapter(TraceAdapter):
    framework = "sglang"

    _LATENCY_ATTRIBUTES = {
        "queue_ns": "gen_ai.latency.time_in_queue",
        "prefill_ns": "gen_ai.latency.time_in_model_prefill",
        "decode_ns": "gen_ai.latency.time_in_model_decode",
        "ttft_ns": "gen_ai.latency.time_to_first_token",
        "e2e_ns": "gen_ai.latency.e2e",
    }

    def to_events(self, document: Mapping[str, Any]) -> list[Event]:
        events: list[Event] = []
        decode_steps: dict[str, int] = defaultdict(int)
        spans = list(iter_spans(document))
        roots_by_trace: dict[str, list[str | None]] = defaultdict(list)
        for raw in spans:
            trace_id = raw.get("traceId", raw.get("trace_id"))
            parent_span_id = raw.get("parentSpanId", raw.get("parent_span_id", ""))
            if trace_id and not parent_span_id:
                parsed_root = self._span(raw)
                roots_by_trace[str(trace_id)].append(parsed_root[0] if parsed_root is not None else None)
        unique_root_by_trace = {
            trace_id: request_ids[0]
            for trace_id, request_ids in roots_by_trace.items()
            if len(request_ids) == 1 and request_ids[0] is not None
        }

        for raw in spans:
            direct = self._span(raw)
            trace_id = raw.get("traceId", raw.get("trace_id"))
            fallback_request_id = unique_root_by_trace.get(str(trace_id)) if trace_id else None
            parsed = self._span(raw, fallback_request_id=fallback_request_id)
            if parsed is None:
                continue
            request_id, start, end, attrs, root = parsed
            request_id_source = "SPAN_ATTRIBUTE" if direct is not None else "TRACE_ID_ASSOCIATION"
            event_source = {
                "source": self.framework,
                "request_id_source": request_id_source,
            }
            if root:
                arrived_attrs: dict[str, Any] = dict(event_source)
                prompt_tokens = integer(attrs.get("gen_ai.usage.prompt_tokens"))
                if prompt_tokens is not None:
                    arrived_attrs["input_tokens"] = prompt_tokens
                finished_attrs: dict[str, Any] = dict(event_source)
                completion_tokens = integer(attrs.get("gen_ai.usage.completion_tokens"))
                if completion_tokens is not None:
                    finished_attrs["output_tokens"] = completion_tokens
                cached_tokens = integer(attrs.get("gen_ai.usage.cached_tokens"))
                if cached_tokens is not None and cached_tokens >= 0:
                    finished_attrs["cached_tokens"] = cached_tokens
                    finished_attrs["cached_tokens_source"] = "OBSERVED"
                for field, attribute in self._LATENCY_ATTRIBUTES.items():
                    seconds = numeric(attrs.get(attribute))
                    if seconds is not None and seconds >= 0:
                        finished_attrs[field] = seconds_to_nanoseconds(seconds, field)
                events.extend((
                    span_event(start, "REQUEST_ARRIVED", request_id, arrived_attrs),
                    span_event(end, "REQUEST_FINISHED", request_id, finished_attrs),
                ))
                continue
            stage = attrs["name"].lower().rsplit(".", 1)[-1]
            if stage == "prefill_waiting":
                events.extend((
                    span_event(start, "REQUEST_QUEUED", request_id, event_source),
                    span_event(end, "REQUEST_SCHEDULED", request_id, event_source),
                ))
            elif stage in {"prefill_forward", "chunked_prefill"}:
                events.extend((
                    span_event(start, "PREFILL_STARTED", request_id, event_source),
                    span_event(end, "PREFILL_FINISHED", request_id, event_source),
                ))
            elif stage == "decode_forward":
                decode_steps[request_id] += 1
                events.append(span_event(start, "DECODE_STEP", request_id, {**event_source, "step": decode_steps[request_id]}))
            else:
                events.append(span_event(start, "FRAMEWORK_SPAN", request_id, {**event_source, "framework": self.framework, "span_name": attrs["name"], "duration_ns": end - start, "duration_source": "DERIVED"}))
        return sorted(events, key=lambda event: event.timestamp_ns)
