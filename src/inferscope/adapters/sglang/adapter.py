"""将 SGLang request/stage spans 转为统一事件。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from inferscope.adapters.base import TraceAdapter, integer, iter_spans, span_event
from inferscope.core.events import Event


class SGLangAdapter(TraceAdapter):
    framework = "sglang"

    def to_events(self, document: Mapping[str, Any]) -> list[Event]:
        events: list[Event] = []
        decode_steps: dict[str, int] = defaultdict(int)
        for raw in iter_spans(document):
            parsed = self._span(raw)
            if parsed is None:
                continue
            request_id, start, end, attrs, root = parsed
            if root:
                arrived_attrs: dict[str, Any] = {"source": self.framework}
                prompt_tokens = integer(attrs.get("gen_ai.usage.prompt_tokens"))
                if prompt_tokens is not None:
                    arrived_attrs["input_tokens"] = prompt_tokens
                finished_attrs: dict[str, Any] = {"source": self.framework}
                completion_tokens = integer(attrs.get("gen_ai.usage.completion_tokens"))
                if completion_tokens is not None:
                    finished_attrs["output_tokens"] = completion_tokens
                events.extend((
                    span_event(start, "REQUEST_ARRIVED", request_id, arrived_attrs),
                    span_event(end, "REQUEST_FINISHED", request_id, finished_attrs),
                ))
                continue
            stage = attrs["name"].lower().rsplit(".", 1)[-1]
            if stage == "prefill_waiting":
                events.extend((
                    span_event(start, "REQUEST_QUEUED", request_id, {"source": self.framework}),
                    span_event(end, "REQUEST_SCHEDULED", request_id, {"source": self.framework}),
                ))
            elif stage in {"prefill_forward", "chunked_prefill"}:
                events.extend((
                    span_event(start, "PREFILL_STARTED", request_id, {"source": self.framework}),
                    span_event(end, "PREFILL_FINISHED", request_id, {"source": self.framework}),
                ))
            elif stage == "decode_forward":
                decode_steps[request_id] += 1
                events.append(span_event(start, "DECODE_STEP", request_id, {"step": decode_steps[request_id], "source": self.framework}))
            else:
                events.append(span_event(start, "FRAMEWORK_SPAN", request_id, {"framework": self.framework, "span_name": attrs["name"], "duration_ns": end - start}))
        return sorted(events, key=lambda event: event.timestamp_ns)
