"""InferScope 命令行入口。"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from inferscope.analyzers.kv_cache import analyze_kv_events
from inferscope.analyzers.request import summarize_requests
from inferscope.analyzers.workload import analyze_workload
from inferscope.adapters.sglang.adapter import SGLangAdapter
from inferscope.adapters.vllm.adapter import VLLMAdapter
from inferscope.cache.hash_cache import HashBlockCache
from inferscope.cache.radix_cache import RadixPrefixCache
from inferscope.core.events import Event
from inferscope.replay.engine import ReplayReport, replay
from inferscope.runtime.correlation import correlate_runtime_stats
from inferscope.runtime.vllm_stats import read_stats, summarize_stats
from inferscope.storage.jsonl import read_events, read_workload, write_events


def _cache(name: str, block_size: int, capacity: int):
    cache_type = HashBlockCache if name == "hash" else RadixPrefixCache
    return cache_type(block_size=block_size, capacity_blocks=capacity)


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _print_replay(report: ReplayReport, reuse: dict[str, object], as_json: bool, include_requests: bool = False) -> None:
    data = report.to_mapping()
    data["workload_reuse"] = reuse
    if not include_requests:
        data.pop("request_results")
    if as_json:
        _print_json(data)
        return
    print("分析模式               离线模拟（SIMULATED）")
    print(f"缓存策略               {report.cache}")
    print(f"请求数                 {report.requests}")
    print(f"输入 tokens            {report.input_tokens}")
    print(f"命中 tokens            {report.cached_tokens}")
    print(f"重算 tokens            {report.computed_tokens}")
    print(f"命中率                 {report.hit_ratio:.1%}")
    print(f"Evictions              {report.evictions}")
    print(f"Peak blocks            {report.peak_blocks}/{report.capacity_blocks}")
    print(f"潜在复用 tokens        {reuse['potential_reuse_tokens']}")
    print(f"Lost reuse             {reuse['lost_reuse_tokens']}（原因未知部分保留为 UNKNOWN）")
    if include_requests:
        print("\n逐请求结果")
        for row in report.request_results:
            print(f"{row.request_id:<24} 命中 {row.matched_tokens:>6} / {row.input_tokens:<6}  miss={row.miss_reason}")


def _summary(events: list[Event], capacity_blocks: int | None = None) -> dict[str, Any]:
    rows = summarize_requests(events)
    phase_names = ("queue_ns", "prefill_ns", "decode_ns", "ttft_ns", "e2e_ns")
    totals = {
        phase: sum(getattr(row, phase) for row in rows if getattr(row, phase) is not None)
        for phase in phase_names
    }
    known_counts = {
        phase: sum(getattr(row, phase) is not None for row in rows)
        for phase in phase_names
    }
    kv_events = [event for event in events if event.event_type.startswith("KV_")]
    kv_cache = analyze_kv_events(kv_events, capacity_blocks).to_mapping() if kv_events else None
    return {
        "requests": len(rows),
        "events": len(events),
        "known_phase_counts": known_counts,
        "latency_source_counts": {
            phase: {source: sum(row.latency_sources[phase] == source for row in rows)
                    for source in ("OBSERVED", "DERIVED", "UNKNOWN")}
            for phase in phase_names
        },
        "total_latency_ns": totals,
        "requests_detail": [row.to_mapping() for row in rows],
        "kv_cache": kv_cache,
    }


def _format_summary(data: dict[str, Any], as_json: bool) -> None:
    if as_json:
        _print_json(data)
        return
    print(f"请求数                 {data['requests']}")
    print(f"事件数                 {data['events']}")
    labels = {
        "queue_ns": "Queue 总耗时",
        "prefill_ns": "Prefill 总耗时",
        "decode_ns": "Decode 总耗时",
        "ttft_ns": "TTFT 总耗时",
        "e2e_ns": "E2E 总耗时",
    }
    for phase, label in labels.items():
        count = data["known_phase_counts"][phase]
        total = data["total_latency_ns"][phase]
        shown = f"{total / 1_000_000:.3f} ms ({count} 个已知)" if count else "UNKNOWN"
        print(f"{label:<22}{shown}")
        sources = data["latency_source_counts"][phase]
        print(f"  来源：输入实测 {sources['OBSERVED']}，边界推导 {sources['DERIVED']}，未知 {sources['UNKNOWN']}")
    if data["kv_cache"] is not None:
        kv = data["kv_cache"]
        utilization = f"{kv['utilization']:.1%}" if kv["utilization"] is not None else "UNKNOWN（未提供容量）"
        print(f"KV 当前 blocks         {kv['current_blocks']}")
        print(f"KV 峰值 blocks         {kv['peak_blocks']}")
        print(f"KV evictions           {kv['evictions']}")
        print("KV 统计来源            输入事件推导（DERIVED）")
        print(f"KV 利用率              {utilization}")
    runtime_stats = data.get("runtime_stats")
    if runtime_stats is not None:
        correlation = runtime_stats["correlation"]
        print(
            "request ID 关联        "
            f"{correlation['matched_requests']} 个成功，"
            f"trace 未匹配 {len(correlation['unmatched_trace_request_ids'])} 个，"
            f"native 未匹配 {len(correlation['unmatched_runtime_request_ids'])} 个，"
            f"歧义 {len(correlation['ambiguous_request_ids'])} 个，"
            f"原生值未知 {len(correlation['unknown_native_value_request_ids'])} 个"
        )
        print("\n请求级缓存观测（vLLM 原生，按 request ID 关联）")
        for row in data["requests_detail"]:
            observation = row["runtime_stats"]
            if observation["status"] == "OBSERVED":
                input_tokens = row.get("input_tokens")
                input_shown = "UNKNOWN" if input_tokens is None else input_tokens
                shown = f"{observation['cached_tokens']}/{input_shown}"
                suffix = (
                    f"来源=OBSERVED，engine={observation['engine_index']}，"
                    f"observed_at_ns={observation['observed_at_ns']}"
                )
            else:
                shown = "UNKNOWN"
                suffix = f"原因={observation['reason']}"
            print(f"  {row['request_id']:<24} cached tokens={shown}  {suffix}")
        print("服务级 vLLM 原生统计（ENGINE scope，不归因到单请求）")
        for engine in runtime_stats["engines"]:
            prefix_hits = engine["prefix_cache_hits_in_capture"]
            prefix_queries = engine["prefix_cache_queries_in_capture"]
            prefix = f"{prefix_hits}/{prefix_queries}" if prefix_queries else "UNKNOWN"
            print(
                f"  engine={engine['engine_index']} 快照={len(engine['scheduler_samples'])} "
                f"Prefix Cache hits/queries={prefix} "
                f"KV 淘汰样本={len(engine['kv_eviction_samples'])} "
                f"来源={engine['kv_eviction_samples_source']}"
            )


def _add_common_cache_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--block-size", type=int, default=16, help="缓存 block 的 token 数，默认 16")
    parser.add_argument("--capacity-blocks", type=int, default=4096, help="缓存容量 block 数，默认 4096")
    parser.add_argument("--json", action="store_true", help="输出 JSON")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="inferscope", description="LLM Serving Runtime 分析工具")
    commands = parser.add_subparsers(dest="command", required=True)

    runtime_parser = commands.add_parser("runtime-stats", help="分析 vLLM 原生缓存与服务级调度统计")
    runtime_parser.add_argument("stats")
    runtime_parser.add_argument("--json", action="store_true")

    replay_parser = commands.add_parser("replay", help="回放 workload 并模拟前缀缓存")
    replay_parser.add_argument("workload")
    replay_parser.add_argument("--cache", choices=("hash", "radix"), default="radix")
    replay_parser.add_argument("--details", action="store_true", help="显示逐请求统计")
    _add_common_cache_args(replay_parser)

    compare_parser = commands.add_parser("compare", help="比较 hash 与 radix 缓存")
    compare_parser.add_argument("workload")
    _add_common_cache_args(compare_parser)

    summary_parser = commands.add_parser("summary", help="汇总 trace")
    summary_parser.add_argument("trace")
    summary_parser.add_argument("--capacity-blocks", type=int, help="KV cache 总容量，用于计算利用率")
    summary_parser.add_argument(
        "--runtime-stats", action="append", metavar="FILE",
        help="关联一个 vLLM 原生统计 JSONL；多 engine 可重复指定",
    )
    summary_parser.add_argument("--json", action="store_true")

    inspect_parser = commands.add_parser("inspect", help="查看单个请求")
    inspect_parser.add_argument("trace")
    inspect_parser.add_argument("request_id")
    inspect_parser.add_argument("--json", action="store_true")

    trace_parser = commands.add_parser("trace", help="按时间显示请求事件")
    trace_parser.add_argument("trace")
    trace_parser.add_argument("request_id", nargs="?")
    trace_parser.add_argument("--json", action="store_true")

    adapt_parser = commands.add_parser("adapt", help="将 vLLM/SGLang OpenTelemetry JSON 转为 InferScope trace JSONL")
    adapt_parser.add_argument("framework", choices=("vllm", "sglang"))
    adapt_parser.add_argument("otel_json")
    adapt_parser.add_argument("--output", "-o", required=True, help="输出 trace JSONL 路径")
    return parser


def _run(args: argparse.Namespace) -> int:
    if args.command == "runtime-stats":
        report = summarize_stats(read_stats(args.stats))
        if args.json:
            _print_json(report)
        else:
            for request in report["requests"]:
                cached = request["cached_tokens"]
                shown = "UNKNOWN" if cached is None else str(cached)
                print(f"请求 {request['request_id']}  缓存 tokens={shown}  来源={request['cached_tokens_source']}")
            for engine in report["engines"]:
                samples = engine["scheduler_samples"]
                preemptions = engine["preemptions_in_capture"]
                shown = "UNKNOWN" if preemptions is None else str(preemptions)
                latest = samples[-1] if samples else None
                if latest is None:
                    scheduler = "UNKNOWN"
                else:
                    scheduler = (
                        f"运行={latest['running_requests']} 普通等待={latest['waiting_requests']} "
                        f"延后等待={latest.get('skipped_waiting_requests', 'UNKNOWN')} "
                        f"KV利用率={latest['kv_cache_usage']:.1%}"
                    )
                print(f"Engine {engine['engine_index']}（服务级） 快照={len(samples)}  最新={scheduler}  采集内抢占数={shown}")
                if engine["prefix_cache_samples"]:
                    queries = engine["prefix_cache_queries_in_capture"]
                    hits = engine["prefix_cache_hits_in_capture"]
                    ratio = f"{hits / queries:.1%}" if queries else "UNKNOWN"
                    print(f"  Prefix Cache（服务级）命中={hits}/{queries} tokens  比例={ratio}")
                eviction_source = engine["kv_eviction_samples_source"]
                print(f"  KV block 淘汰样本={len(engine['kv_eviction_samples'])}  来源={eviction_source}")
            print("逐请求抢占、缓存来源、miss 原因和 KV 生命周期：UNKNOWN")
        return 0
    if args.command == "adapt":
        source = Path(args.otel_json)
        try:
            document = json.loads(source.read_text(encoding="utf-8"), parse_float=Decimal)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"无法读取 OpenTelemetry JSON {source}: {exc}") from exc
        if not isinstance(document, dict):
            raise ValueError("OpenTelemetry JSON 顶层必须是 object")
        adapter = VLLMAdapter() if args.framework == "vllm" else SGLangAdapter()
        events = adapter.to_events(document)
        write_events(args.output, events)
        print(f"已转换 {len(events)} 个事件到 {args.output}")
        return 0
    if args.command == "replay":
        requests = read_workload(args.workload)
        cache = _cache(args.cache, args.block_size, args.capacity_blocks)
        report = replay(requests, cache, args.cache)
        reuse = analyze_workload(requests, report, args.block_size).to_mapping()
        _print_replay(report, reuse, args.json, args.details)
        return 0
    if args.command == "compare":
        requests = read_workload(args.workload)
        reports = {}
        for name in ("hash", "radix"):
            report = replay(requests, _cache(name, args.block_size, args.capacity_blocks), name)
            reports[name] = report.to_mapping()
            reports[name].pop("request_results")
            reports[name]["workload_reuse"] = analyze_workload(requests, report, args.block_size).to_mapping()
        if args.json:
            _print_json(reports)
        else:
            print("分析模式：离线模拟（SIMULATED）")
            print(f"{'指标':<24}{'Hash':>14}{'Radix':>14}")
            for key, label in (("hit_ratio", "实际命中率"), ("cached_tokens", "命中 tokens"), ("computed_tokens", "重算 tokens"), ("evictions", "淘汰 blocks"), ("peak_blocks", "峰值 blocks")):
                left, right = reports["hash"][key], reports["radix"][key]
                if key == "hit_ratio":
                    left, right = f"{left:.1%}", f"{right:.1%}"
                print(f"{label:<24}{str(left):>14}{str(right):>14}")
            for key, label in (("potential_hit_ratio", "潜在命中率"), ("lost_reuse_tokens", "Lost reuse tokens")):
                left = reports["hash"]["workload_reuse"][key]
                right = reports["radix"]["workload_reuse"][key]
                if key == "potential_hit_ratio":
                    left, right = f"{left:.1%}", f"{right:.1%}"
                print(f"{label:<24}{str(left):>14}{str(right):>14}")
        return 0
    events = read_events(args.trace)
    if args.command == "summary":
        report = _summary(events, args.capacity_blocks)
        if args.runtime_stats:
            runtime_rows = [row for path in args.runtime_stats for row in read_stats(path)]
            report = correlate_runtime_stats(report, summarize_stats(runtime_rows))
        _format_summary(report, args.json)
        return 0
    if args.command == "inspect":
        request_events = [event for event in events if event.request_id == args.request_id]
        if not request_events:
            raise ValueError(f"trace 中不存在 request_id={args.request_id}")
        summary = next(row for row in summarize_requests(request_events))
        result = {"request": summary.to_mapping(), "events": [event.to_mapping() for event in sorted(request_events, key=lambda row: row.timestamp_ns)]}
        if args.json:
            _print_json(result)
        else:
            _print_json(result)
        return 0
    grouped: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        if args.request_id is None or event.request_id == args.request_id:
            grouped[event.request_id].append(event)
    result = {
        request_id: [event.to_mapping() for event in sorted(rows, key=lambda row: row.timestamp_ns)]
        for request_id, rows in sorted(grouped.items())
    }
    if args.json:
        _print_json(result)
    else:
        for request_id, rows in result.items():
            print(request_id)
            for event in rows:
                timestamp_source = event.get("timestamp_source", "UNKNOWN")
                print(f"  {event['timestamp_ns']:>16}  {event['event_type']}  时间来源={timestamp_source}")
    return 0


def main() -> None:
    args = build_parser().parse_args()
    try:
        raise SystemExit(_run(args))
    except (ValueError, OSError) as exc:
        print(f"inferscope: 错误: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
