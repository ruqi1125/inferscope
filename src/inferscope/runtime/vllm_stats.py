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


def _seconds(value: Any, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"{name} 必须是非负有限秒数")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{name} 必须是非负有限秒数")


def _validate_cache_stats(stats: Any, name: str) -> None:
    if stats is None:
        return
    if not isinstance(stats, dict):
        raise ValueError(f"{name} 必须是 object 或 null")
    for field in ("requests", "queries", "hits", "preempted_requests",
                  "preempted_queries", "preempted_hits"):
        _count(stats.get(field), f"{name}.{field}")
    if stats["hits"] > stats["queries"] or stats["preempted_hits"] > stats["preempted_queries"]:
        raise ValueError(f"{name} hits 不能超过 queries")
    if type(stats.get("reset")) is not bool:
        raise ValueError(f"{name}.reset 必须是 bool")


def _validate_iteration(iteration: Any) -> None:
    if iteration is None:
        return
    if not isinstance(iteration, dict):
        raise ValueError("iteration 必须是 object 或 null")
    for field in ("iteration_index", "context_requests", "context_tokens",
                  "generation_requests", "generation_tokens", "encoder_inputs",
                  "encoder_output_tokens"):
        _count(iteration.get(field), f"iteration.{field}")
    _seconds(iteration.get("elapsed_ms"), "iteration.elapsed_ms")
    if type(iteration.get("is_dummy")) is not bool:
        raise ValueError("iteration.is_dummy 必须是 bool")


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
        if "skipped_waiting_requests" in scheduler:
            _count(scheduler["skipped_waiting_requests"], "skipped_waiting_requests")
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
    for name in ("prefix_cache", "connector_prefix_cache"):
        _validate_cache_stats(row.get(name), name)
    _validate_iteration(row.get("iteration"))
    evictions = row.get("kv_evictions", [])
    if not isinstance(evictions, list):
        raise ValueError("kv_evictions 必须是列表")
    for event in evictions:
        if not isinstance(event, dict):
            raise ValueError("kv_evictions 项必须是 object")
        _seconds(event.get("lifetime_seconds"), "lifetime_seconds")
        _seconds(event.get("idle_seconds"), "idle_seconds")
        gaps = event.get("reuse_gaps_seconds")
        if not isinstance(gaps, list):
            raise ValueError("reuse_gaps_seconds 必须是列表")
        for gap in gaps:
            _seconds(gap, "reuse_gap_seconds")
    metrics_enabled = row.get("kv_cache_metrics_enabled")
    if metrics_enabled is not None and type(metrics_enabled) is not bool:
        raise ValueError("kv_cache_metrics_enabled 必须是 bool 或 null")
    sample_rate = row.get("kv_cache_metrics_sample_rate")
    if sample_rate is not None:
        invalid_rate = isinstance(sample_rate, bool) or not isinstance(sample_rate, (int, float))
        if not invalid_rate:
            invalid_rate = (sample_rate <= 0 or sample_rate > 1 or
                            isinstance(sample_rate, float) and not math.isfinite(sample_rate))
        if invalid_rate:
            raise ValueError("kv_cache_metrics_sample_rate 必须在 (0, 1] 内")
    return row


class NativeStatsWriter:
    """每个文件由单个采集实例写入；观察时间不代替调度事件时间。"""

    def __init__(self, path: str | Path, engine_index: int = 0,
                 kv_cache_metrics_enabled: bool | None = None,
                 kv_cache_metrics_sample_rate: float | None = None):
        _count(engine_index, "engine_index")
        if kv_cache_metrics_enabled is not None and type(kv_cache_metrics_enabled) is not bool:
            raise ValueError("kv_cache_metrics_enabled 必须是 bool 或 null")
        self.path = Path(path)
        self.engine_index = engine_index
        self.kv_cache_metrics_enabled = kv_cache_metrics_enabled
        self.kv_cache_metrics_sample_rate = kv_cache_metrics_sample_rate
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
        def cache_stats(value: Any) -> dict[str, Any] | None:
            if value is None:
                return None
            return {
                "requests": value.requests,
                "queries": value.queries,
                "hits": value.hits,
                "reset": value.reset,
                "preempted_requests": value.preempted_requests,
                "preempted_queries": value.preempted_queries,
                "preempted_hits": value.preempted_hits,
            }

        def iteration_detail(value: Any) -> dict[str, Any] | None:
            if value is None:
                return None
            return {
                "iteration_index": value.iteration_index,
                "context_requests": value.num_ctx_requests,
                "context_tokens": value.num_ctx_tokens,
                "generation_requests": value.num_generation_requests,
                "generation_tokens": value.num_generation_tokens,
                "elapsed_ms": value.elapsed_ms,
                "encoder_inputs": value.num_encoder_inputs,
                "encoder_output_tokens": value.num_encoder_output_tokens,
                "is_dummy": value.is_dummy,
            }

        scheduler = None
        prefix_cache = None
        connector_prefix_cache = None
        iteration = None
        kv_evictions = []
        if scheduler_stats is not None:
            scheduler = {
                "running_requests": scheduler_stats.num_running_reqs,
                "waiting_requests": scheduler_stats.num_waiting_reqs,
                "skipped_waiting_requests": scheduler_stats.num_skipped_waiting_reqs,
                "kv_cache_usage": scheduler_stats.kv_cache_usage,
            }
            prefix_cache = cache_stats(getattr(scheduler_stats, "prefix_cache_stats", None))
            connector_prefix_cache = cache_stats(
                getattr(scheduler_stats, "connector_prefix_cache_stats", None),
            )
            iteration = iteration_detail(getattr(scheduler_stats, "iteration_details", None))
            kv_evictions = [
                {
                    "lifetime_seconds": event.lifetime_seconds,
                    "idle_seconds": event.idle_seconds,
                    "reuse_gaps_seconds": list(event.reuse_gaps_seconds),
                }
                for event in getattr(scheduler_stats, "kv_cache_eviction_events", [])
            ]
        row = _validate({
            "schema_version": 1,
            "runtime_version": RUNTIME_VERSION,
            "observed_at_ns": time.time_ns(),
            "engine_index": self.engine_index,
            "requests": requests,
            "scheduler": scheduler,
            "prefix_cache": prefix_cache,
            "connector_prefix_cache": connector_prefix_cache,
            "iteration": iteration,
            "kv_evictions": kv_evictions,
            "preemptions": None if iteration_stats is None else iteration_stats.num_preempted_reqs,
            "kv_cache_metrics_enabled": self.kv_cache_metrics_enabled,
            "kv_cache_metrics_sample_rate": self.kv_cache_metrics_sample_rate,
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
            observability = vllm_config.observability_config
            self.writer = NativeStatsWriter(
                directory / f"vllm-stats-{os.getpid()}-{engine_index}.jsonl",
                engine_index,
                kv_cache_metrics_enabled=observability.kv_cache_metrics,
                kv_cache_metrics_sample_rate=observability.kv_cache_metrics_sample,
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
        ordered = sorted(samples, key=lambda row: row["observed_at_ns"])
        scheduler_samples = []
        for sample in ordered:
            if sample["scheduler"] is None:
                continue
            detail = sample.get("iteration")
            scheduler_sample = {"observed_at_ns": sample["observed_at_ns"], **sample["scheduler"]}
            if detail is not None:
                scheduler_sample["iteration"] = detail
            scheduler_samples.append(scheduler_sample)

        def cache_samples(field: str) -> list[dict[str, Any]]:
            report = []
            for sample in ordered:
                stats = sample.get(field)
                if stats is None:
                    continue
                report.append({
                    "observed_at_ns": sample["observed_at_ns"],
                    **stats,
                    "hit_fraction": stats["hits"] / stats["queries"] if stats["queries"] else None,
                    "hit_fraction_source": "DERIVED" if stats["queries"] else "UNKNOWN",
                })
            return report

        prefix_cache_samples = cache_samples("prefix_cache")
        connector_cache_samples = cache_samples("connector_prefix_cache")
        eviction_samples = [
            {"observed_at_ns": sample["observed_at_ns"], **event}
            for sample in ordered
            for event in sample.get("kv_evictions", [])
        ]
        metrics_configs = {sample.get("kv_cache_metrics_enabled") for sample in samples}
        metrics_enabled = metrics_configs.pop() if len(metrics_configs) == 1 else None
        sample_rates = {sample.get("kv_cache_metrics_sample_rate") for sample in samples}
        metrics_sample_rate = sample_rates.pop() if len(sample_rates) == 1 else None
        engine_reports.append({
            "engine_index": engine_index, "scope": "ENGINE",
            "scheduler_samples": scheduler_samples,
            "scheduler_source": "OBSERVED" if scheduler_samples else "UNKNOWN",
            "prefix_cache_samples": prefix_cache_samples,
            "prefix_cache_source": "OBSERVED" if prefix_cache_samples else "UNKNOWN",
            "prefix_cache_queries_in_capture": sum(item["queries"] for item in prefix_cache_samples),
            "prefix_cache_hits_in_capture": sum(item["hits"] for item in prefix_cache_samples),
            "connector_prefix_cache_samples": connector_cache_samples,
            "connector_prefix_cache_source": "OBSERVED" if connector_cache_samples else "UNKNOWN",
            "kv_eviction_samples": eviction_samples,
            "kv_eviction_samples_source": (
                "OBSERVED_SAMPLED" if metrics_enabled and eviction_samples
                else "NO_SAMPLES" if metrics_enabled
                else "DISABLED" if metrics_enabled is False
                else "UNKNOWN"
            ),
            "kv_cache_metrics_sample_rate": metrics_sample_rate,
            "preemptions_in_capture": sum(preemptions) if preemptions else None,
            "preemptions_source": "DERIVED" if preemptions else "UNKNOWN",
            "capture_completeness": "UNKNOWN", "kv_lifecycle_source": "UNKNOWN",
        })
    return {"runtime": "vllm", "runtime_version": RUNTIME_VERSION,
            "requests": request_reports, "engines": engine_reports}
