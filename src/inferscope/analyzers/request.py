"""请求生命周期和延迟阶段分析。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass

from inferscope.core.events import Event


@dataclass(frozen=True, slots=True)
class RequestSummary:
    request_id: str
    event_count: int
    arrived_ns: int | None
    scheduled_ns: int | None
    prefill_started_ns: int | None
    prefill_finished_ns: int | None
    finished_ns: int | None
    queue_ns: int | None
    prefill_ns: int | None
    decode_ns: int | None
    ttft_ns: int | None
    input_tokens: int | None
    output_tokens: int | None

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)


def summarize_requests(events: list[Event]) -> list[RequestSummary]:
    grouped: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        grouped[event.request_id].append(event)
    summaries: list[RequestSummary] = []
    for request_id, rows in grouped.items():
        rows.sort(key=lambda event: event.timestamp_ns)
        first: dict[str, Event] = {}
        for event in rows:
            first.setdefault(event.event_type, event)

        def stamp(event_name: str) -> int | None:
            event = first.get(event_name)
            return event.timestamp_ns if event else None

        arrived = stamp("REQUEST_ARRIVED")
        scheduled = stamp("REQUEST_SCHEDULED")
        prefill_start = stamp("PREFILL_STARTED")
        prefill_end = stamp("PREFILL_FINISHED")
        finished = stamp("REQUEST_FINISHED")
        queue_ns = scheduled - arrived if arrived is not None and scheduled is not None and scheduled >= arrived else None
        prefill_ns = prefill_end - prefill_start if prefill_start is not None and prefill_end is not None and prefill_end >= prefill_start else None
        ttft_ns = prefill_end - arrived if arrived is not None and prefill_end is not None and prefill_end >= arrived else None
        decode_ns = finished - prefill_end if prefill_end is not None and finished is not None and finished >= prefill_end else None
        lookup = first.get("PREFIX_LOOKUP")
        input_tokens = lookup.attributes.get("queried_tokens") if lookup else None
        output_tokens = first.get("REQUEST_FINISHED").attributes.get("output_tokens") if first.get("REQUEST_FINISHED") else None
        summaries.append(RequestSummary(request_id, len(rows), arrived, scheduled, prefill_start, prefill_end, finished, queue_ns, prefill_ns, decode_ns, ttft_ns, input_tokens, output_tokens))
    return sorted(summaries, key=lambda row: row.request_id)
