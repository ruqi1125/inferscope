"""严格、带行号诊断的 JSONL 读取器。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, TypeVar

from inferscope.core.events import Event
from inferscope.core.request import WorkloadRequest

T = TypeVar("T")


class JSONLInputError(ValueError):
    def __init__(self, path: Path, line: int, message: str):
        self.path = path
        self.line = line
        super().__init__(f"{path}:{line}: {message}")


def _read(path: str | Path, parser: Callable[[dict[str, Any], int], T]) -> list[T]:
    source = Path(path)
    rows: list[T] = []
    try:
        with source.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError("每行必须是 JSON object")
                    rows.append(parser(value, line_number))
                except (json.JSONDecodeError, ValueError, TypeError) as exc:
                    raise JSONLInputError(source, line_number, str(exc)) from exc
    except OSError as exc:
        raise ValueError(f"无法读取 {source}: {exc}") from exc
    return rows


def _parse_workload(row: dict[str, Any], line: int) -> WorkloadRequest:
    return WorkloadRequest.from_mapping(row, line)


def _parse_event(row: dict[str, Any], line: int) -> Event:
    del line
    return Event.from_mapping(row)


def read_workload(path: str | Path) -> list[WorkloadRequest]:
    return _read(path, _parse_workload)


def read_events(path: str | Path) -> list[Event]:
    return _read(path, _parse_event)


def write_events(path: str | Path, events: list[Event]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="\n") as stream:
        for event in events:
            stream.write(json.dumps(event.to_mapping(), ensure_ascii=False) + "\n")
