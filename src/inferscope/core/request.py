"""Workload 请求数据模型。"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any


@dataclass(frozen=True, slots=True)
class WorkloadRequest:
    request_id: str
    timestamp: float
    input_token_ids: tuple[int, ...]
    output_tokens: int
    source_line: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not self.request_id.strip():
            raise ValueError("request_id 必须是非空字符串")
        if isinstance(self.timestamp, bool) or not isinstance(self.timestamp, (int, float)) or not isfinite(self.timestamp) or self.timestamp < 0:
            raise ValueError("timestamp 必须是有限的非负秒数")
        if not isinstance(self.input_token_ids, (tuple, list)) or any(isinstance(token, bool) or not isinstance(token, int) or token < 0 for token in self.input_token_ids):
            raise ValueError("input_token_ids 必须是非负整数序列")
        if isinstance(self.output_tokens, bool) or not isinstance(self.output_tokens, int) or self.output_tokens < 0:
            raise ValueError("output_tokens 必须是非负整数")
        object.__setattr__(self, "input_token_ids", tuple(self.input_token_ids))

    @classmethod
    def from_mapping(cls, row: dict[str, Any], source_line: int = 0) -> WorkloadRequest:
        for key in ("request_id", "timestamp", "input_token_ids", "output_tokens"):
            if key not in row:
                raise ValueError(f"缺少必填字段: {key}")
        request_id = row["request_id"]
        timestamp = row["timestamp"]
        tokens = row["input_token_ids"]
        output_tokens = row["output_tokens"]
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("request_id 必须是非空字符串")
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not isfinite(timestamp) or timestamp < 0:
            raise ValueError("timestamp 必须是有限的非负秒数")
        if not isinstance(tokens, list) or any(isinstance(token, bool) or not isinstance(token, int) or token < 0 for token in tokens):
            raise ValueError("input_token_ids 必须是非负整数数组")
        if isinstance(output_tokens, bool) or not isinstance(output_tokens, int) or output_tokens < 0:
            raise ValueError("output_tokens 必须是非负整数")
        return cls(request_id, float(timestamp), tuple(tokens), output_tokens, source_line)
