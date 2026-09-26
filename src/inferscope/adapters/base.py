"""OpenTelemetry JSON span 适配器的公共解析能力。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from inferscope.core.events import Event


def _attribute_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        for key in ("stringValue", "intValue", "doubleValue", "boolValue", "bytesValue"):
            if key in value:
                return value[key]
        if "value" in value:
            return _attribute_value(value["value"])
    return value


def _attributes(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return {str(key): _attribute_value(value) for key, value in raw.items()}
    result: dict[str, Any] = {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, Mapping) and "key" in item:
                result[str(item["key"])] = _attribute_value(item.get("value"))
    return result


def numeric(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float, Decimal, str)):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def integer(value: Any) -> int | None:
    number = numeric(value)
    if number is None or number != number.to_integral_value():
        return None
    return int(number)


def iter_spans(document: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    direct = document.get("spans")
    if isinstance(direct, list):
        yield from (span for span in direct if isinstance(span, dict))
        return
    for resource in document.get("resourceSpans", []):
        if not isinstance(resource, Mapping):
            continue
        groups = resource.get("scopeSpans", resource.get("instrumentationLibrarySpans", []))
        for group in groups:
            if isinstance(group, Mapping):
                yield from (span for span in group.get("spans", []) if isinstance(span, dict))


class TraceAdapter(ABC):
    framework = "unknown"

    @abstractmethod
    def to_events(self, document: Mapping[str, Any]) -> list[Event]:
        """将框架的 OTel 导出 JSON 映射为 InferScope 事件。"""

    def _span(
        self,
        raw: Mapping[str, Any],
        fallback_request_id: str | None = None,
    ) -> tuple[str, int, int, dict[str, Any], bool] | None:
        attrs = _attributes(raw.get("attributes", {}))
        request_id = attrs.get("gen_ai.request.id", attrs.get("request_id", attrs.get("req_id")))
        if request_id is None or not str(request_id).strip():
            request_id = fallback_request_id
        if request_id is None:
            return None
        try:
            start = int(raw.get("startTimeUnixNano", raw.get("start_time_unix_nano")))
            end = int(raw.get("endTimeUnixNano", raw.get("end_time_unix_nano")))
        except (TypeError, ValueError):
            return None
        if start < 0 or end < start:
            return None
        root = not raw.get("parentSpanId", raw.get("parent_span_id", ""))
        return str(request_id), start, end, {"name": str(raw.get("name", "")), **attrs}, root


def span_event(timestamp_ns: int, event_type: str, request_id: str, attributes: dict[str, Any] | None = None) -> Event:
    metadata = {"timestamp_source": "OBSERVED", **(attributes or {})}
    return Event(timestamp_ns, event_type, request_id, metadata)
