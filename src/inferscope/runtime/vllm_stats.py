"""vLLM 0.29.0 统计白名单；离线使用仅依赖标准库。"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from collections import defaultdict
from importlib.metadata import version
from pathlib import Path
from typing import Any

from inferscope.storage.jsonl import JSONLInputError

RUNTIME_VERSION = "0.29.0"


def _count(value: Any, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} 必须是非负整数")


def _validate(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError("每行必须是 JSON object")
    if type(row.get("schema_version")) is not int or row["schema_version"] != 1:
        raise ValueError("不支持的 schema_version")
    if row.get("runtime_version") != RUNTIME_VERSION:
        raise ValueError("仅支持 vLLM 0.29.0 原生统计")
    for name in ("observed_at_ns", "engine_index"):
        _count(row.get(name), name)
    for name in ("requests", "scheduler", "preemptions"):
        if name not in row:
            raise ValueError(f"缺少字段 {name}")
    if not isinstance(row["requests"], list):
        raise ValueError("requests 必须是列表")
    for request in row["requests"]:
        if not isinstance(request, dict):
            raise ValueError("request 必须是 object")
        if not isinstance(request.get("request_id"), str) or not request["request_id"].strip():
            raise ValueError("缺少有效 request_id")
        for name in ("input_tokens", "output_tokens", "cached_tokens"):
            _count(request.get(name), name)
        if request["cached_tokens"] > request["input_tokens"]:
            raise ValueError("cached_tokens 不能超过 input_tokens")
    scheduler = row["scheduler"]
    if scheduler is not None:
        if not isinstance(scheduler, dict):
            raise ValueError("scheduler 必须是 object 或 null")
        for name in ("running_requests", "waiting_requests"):
            _count(scheduler.get(name), name)
        usage = scheduler.get("kv_cache_usage")
        invalid_usage = isinstance(usage, bool) or not isinstance(usage, (int, float))
        if not invalid_usage:
            if isinstance(usage, int):
                invalid_usage = not 0 <= usage <= 1
            else:
                invalid_usage = not math.isfinite(usage) or not 0 <= usage <= 1
        if invalid_usage:
            raise ValueError("kv_cache_usage 必须是 [0, 1] 内有限数值")
    if row["preemptions"] is not None:
        _count(row["preemptions"], "preemptions")
    return row


class NativeStatsWriter:
    """每个文件由单个采集实例写入；观察时间不代替调度事件时间。"""

    def __init__(self, path: str | Path, engine_index: int = 0):
        _count(engine_index, "engine_index")
        self.path = Path(path)
        self.engine_index = engine_index
        self._lock = threading.Lock()
        with self.path.open("x", encoding="utf-8"):
            pass

    def record(self, scheduler_stats: Any, iteration_stats: Any) -> None:
        requests = []
        if iteration_stats is not None:
            for request in iteration_stats.finished_requests:
                requests.append({
                    "request_id": request.request_id,
                    "input_tokens": request.num_prompt_tokens,
                    "output_tokens": request.num_generation_tokens,
                    "cached_tokens": request.num_cached_tokens,
                })
        row = _validate({
            "schema_version": 1,
            "runtime_version": RUNTIME_VERSION,
            "observed_at_ns": time.time_ns(),
            "engine_index": self.engine_index,
            "requests": requests,
            "scheduler": None if scheduler_stats is None else {
                "running_requests": scheduler_stats.num_running_reqs,
                "waiting_requests": scheduler_stats.num_waiting_reqs,
                "kv_cache_usage": scheduler_stats.kv_cache_usage,
            },
            "preemptions": None if iteration_stats is None else iteration_stats.num_preempted_reqs,
        })
        with self._lock, self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def make_stat_logger(output_directory: str | Path):
    """供 AsyncLLM 的 stat_loggers 参数使用；仅此入口导入 vLLM。"""
    if version("vllm") != RUNTIME_VERSION:
        raise ValueError("采集器仅验证了 vLLM 0.29.0")
    from vllm.v1.metrics.loggers import StatLoggerBase

    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)

    class InferScopeStatLogger(StatLoggerBase):
        def __init__(self, vllm_config, engine_index=0):
            self.writer = NativeStatsWriter(
                directory / f"vllm-stats-{os.getpid()}-{engine_index}.jsonl", engine_index,
            )

        def record(self, scheduler_stats, iteration_stats, mm_cache_stats=None, engine_idx=0):
            if engine_idx != self.writer.engine_index:
                raise ValueError("统计回调 engine_index 不匹配")
            self.writer.record(scheduler_stats, iteration_stats)

        def log_engine_initialized(self):
            pass

    return InferScopeStatLogger


def read_stats(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    rows = []
    with source.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                rows.append(_validate(json.loads(line)))
            except (ValueError, TypeError) as exc:
                raise JSONLInputError(source, line_number, str(exc)) from exc
    return rows


def summarize_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    requests = defaultdict(list)
    engines = defaultdict(list)
    for row in rows:
        _validate(row)
        engines[row["engine_index"]].append(row)
        for request in row["requests"]:
            requests[(row["engine_index"], request["request_id"])].append(request)
    request_reports = []
    for (engine_index, request_id), observations in sorted(requests.items()):
        ambiguous = len(observations) != 1
        stats = observations[0]
        prompt = None if ambiguous else stats["input_tokens"]
        cached = None if ambiguous else stats["cached_tokens"]
        request_reports.append({
            "request_id": request_id, "engine_index": engine_index, "scope": "REQUEST",
            "observations": len(observations), "ambiguous": ambiguous,
            "input_tokens": prompt, "output_tokens": None if ambiguous else stats["output_tokens"],
            "cached_tokens": cached, "cached_tokens_source": "UNKNOWN" if ambiguous else "OBSERVED",
            "cached_fraction": cached / prompt if prompt else None,
            "cached_fraction_source": "DERIVED" if prompt else "UNKNOWN",
            "cache_origin": "UNKNOWN", "miss_reason": "UNKNOWN",
            "preemptions": None, "preemptions_source": "UNKNOWN",
        })
    engine_reports = []
    for engine_index, samples in sorted(engines.items()):
        preemptions = [sample["preemptions"] for sample in samples if sample["preemptions"] is not None]
        scheduler_samples = [
            {"observed_at_ns": sample["observed_at_ns"], **sample["scheduler"]}
            for sample in sorted(samples, key=lambda row: row["observed_at_ns"])
            if sample["scheduler"] is not None
        ]
        engine_reports.append({
            "engine_index": engine_index, "scope": "ENGINE",
            "scheduler_samples": scheduler_samples,
            "scheduler_source": "OBSERVED" if scheduler_samples else "UNKNOWN",
            "preemptions_in_capture": sum(preemptions) if preemptions else None,
            "preemptions_source": "DERIVED" if preemptions else "UNKNOWN",
            "capture_completeness": "UNKNOWN", "kv_lifecycle_source": "UNKNOWN",
        })
    return {"runtime": "vllm", "runtime_version": RUNTIME_VERSION,
            "requests": request_reports, "engines": engine_reports}
