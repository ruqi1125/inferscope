"""统一的 InferScope 事件模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Event:
    timestamp_ns: int
    event_type: str
    request_id: str
    attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timestamp_ns < 0:
            raise ValueError("timestamp_ns 不能为负数")
        if not self.event_type:
            raise ValueError("event_type 不能为空")
        if not self.request_id:
            raise ValueError("request_id 不能为空")

    @classmethod
    def from_mapping(cls, row: dict[str, Any]) -> Event:
        required = {"timestamp_ns", "event_type", "request_id"}
        missing = required - row.keys()
        if missing:
            raise ValueError(f"缺少必填字段: {', '.join(sorted(missing))}")
        version = row.get("schema_version", 1)
        if isinstance(version, bool) or not isinstance(version, int) or version != 1:
            raise ValueError(f"不支持的 schema_version: {version}")
        if isinstance(row["timestamp_ns"], bool) or not isinstance(row["timestamp_ns"], int):
            raise ValueError("timestamp_ns 必须是整数")
        if not isinstance(row["event_type"], str) or not isinstance(row["request_id"], str):
            raise ValueError("event_type 和 request_id 必须是字符串")
        core = required | {"schema_version"}
        return cls(
            timestamp_ns=row["timestamp_ns"],
            event_type=str(row["event_type"]),
            request_id=str(row["request_id"]),
            attributes={key: value for key, value in row.items() if key not in core},
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "timestamp_ns": self.timestamp_ns,
            "event_type": self.event_type,
            "request_id": self.request_id,
            **self.attributes,
        }
