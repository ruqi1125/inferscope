"""将 vLLM 原生请求观测与统一 trace 摘要按 request ID 精确关联。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def correlate_runtime_stats(
    summary: dict[str, Any], runtime_report: dict[str, Any]
) -> dict[str, Any]:
    """关联逐请求原生观测，同时完整保留 engine 级原生数据。

    仅对两侧都唯一、且缓存 token 明确为 OBSERVED 的 request ID 关联。
    不推导时间戳，不将 engine 级统计复制到请求上。
    """
    trace_requests = summary.get("requests_detail", [])
    native_requests = runtime_report.get("requests", [])
    native_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    trace_counts: dict[str, int] = defaultdict(int)
    trace_ids = set()

    for row in trace_requests:
        request_id = row["request_id"]
        trace_counts[request_id] += 1
        trace_ids.add(request_id)
    for row in native_requests:
        native_by_id[row["request_id"]].append(row)

    matched_ids: set[str] = set()
    ambiguous_ids: set[str] = set()
    unknown_value_ids: set[str] = set()
    correlated_rows = []
    for trace_row in trace_requests:
        request_id = trace_row["request_id"]
        matches = native_by_id.get(request_id, [])
        if trace_counts[request_id] > 1 or len(matches) > 1 or any(
            row.get("ambiguous") is True for row in matches
        ):
            observation = {
                "scope": "REQUEST",
                "status": "UNKNOWN",
                "reason": "AMBIGUOUS_RUNTIME_OBSERVATIONS",
            }
            ambiguous_ids.add(request_id)
        elif not matches:
            observation = {
                "scope": "REQUEST",
                "status": "UNKNOWN",
                "reason": "NO_RUNTIME_OBSERVATION",
            }
        else:
            native = matches[0]
            cached_tokens = native.get("cached_tokens")
            if (
                native.get("observations") != 1
                or native.get("cached_tokens_source") != "OBSERVED"
                or isinstance(cached_tokens, bool)
                or not isinstance(cached_tokens, int)
            ):
                observation = {
                    "scope": "REQUEST",
                    "status": "UNKNOWN",
                    "reason": "NATIVE_VALUE_UNKNOWN",
                }
                unknown_value_ids.add(request_id)
            else:
                observation = {
                    "scope": "REQUEST",
                    "status": "OBSERVED",
                    "engine_index": native["engine_index"],
                    "observed_at_ns": native.get("observed_at_ns"),
                    "cached_tokens": cached_tokens,
                    "cached_tokens_source": "OBSERVED",
                    "cached_fraction": native.get("cached_fraction"),
                    "cached_fraction_source": native.get("cached_fraction_source", "UNKNOWN"),
                    "cache_origin": native.get("cache_origin", "UNKNOWN"),
                    "miss_reason": native.get("miss_reason", "UNKNOWN"),
                }
                matched_ids.add(request_id)
        correlated_rows.append({**trace_row, "runtime_stats": observation})

    unmatched_trace = sorted(
        request_id for request_id in trace_ids if request_id not in native_by_id
    )
    unmatched_runtime = sorted(
        request_id for request_id in native_by_id if request_id not in trace_ids
    )
    runtime_stats = {
        "runtime": runtime_report.get("runtime", "UNKNOWN"),
        "runtime_version": runtime_report.get("runtime_version", "UNKNOWN"),
        "requests": native_requests,
        "engines": runtime_report.get("engines", []),
        "correlation": {
            "matched_requests": len(matched_ids),
            "unmatched_trace_request_ids": unmatched_trace,
            "unmatched_runtime_request_ids": unmatched_runtime,
            "ambiguous_request_ids": sorted(ambiguous_ids),
            "unknown_native_value_request_ids": sorted(unknown_value_ids),
        },
    }
    return {
        **summary,
        "requests_detail": correlated_rows,
        "runtime_stats": runtime_stats,
    }
