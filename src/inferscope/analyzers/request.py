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
    e2e_ns: int | None
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
        finished_event = first.get("REQUEST_FINISHED")

        def measured_duration(name: str) -> int | None:
            if finished_event is None:
                return None
            value = finished_event.attributes.get(name)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
            return None

        queue_ns = measured_duration("queue_ns")
        if queue_ns is None:
            queue_ns = scheduled - arrived if arrived is not None and scheduled is not None and scheduled >= arrived else None
        prefill_ns = measured_duration("prefill_ns")
        if prefill_ns is None:
            prefill_ns = prefill_end - prefill_start if prefill_start is not None and prefill_end is not None and prefill_end >= prefill_start else None
        ttft_ns = measured_duration("ttft_ns")
        if ttft_ns is None:
            ttft_ns = prefill_end - arrived if arrived is not None and prefill_end is not None and prefill_end >= arrived else None
        decode_ns = measured_duration("decode_ns")
        if decode_ns is None:
            decode_ns = finished - prefill_end if prefill_end is not None and finished is not None and finished >= prefill_end else None
        e2e_ns = measured_duration("e2e_ns")
        if e2e_ns is None:
            e2e_ns = finished - arrived if arrived is not None and finished is not None and finished >= arrived else None
        lookup = first.get("PREFIX_LOOKUP")
        arrived_event = first.get("REQUEST_ARRIVED")
        input_tokens = lookup.attributes.get("queried_tokens") if lookup else None
        if input_tokens is None and arrived_event is not None:
            input_tokens = arrived_event.attributes.get("input_tokens")
        output_tokens = finished_event.attributes.get("output_tokens") if finished_event else None
        summaries.append(RequestSummary(
            request_id=request_id,
            event_count=len(rows),
            arrived_ns=arrived,
            scheduled_ns=scheduled,
            prefill_started_ns=prefill_start,
            prefill_finished_ns=prefill_end,
            finished_ns=finished,
            queue_ns=queue_ns,
            prefill_ns=prefill_ns,
            decode_ns=decode_ns,
            ttft_ns=ttft_ns,
            e2e_ns=e2e_ns,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ))
    return sorted(summaries, key=lambda row: row.request_id)
