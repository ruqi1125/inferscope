"""InferScope 命令行入口。"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import asdict
from typing import Any

from inferscope.analyzers.request import summarize_requests
from inferscope.cache.hash_cache import HashBlockCache
from inferscope.cache.radix_cache import RadixPrefixCache
from inferscope.core.events import Event
from inferscope.replay.engine import ReplayReport, replay
from inferscope.storage.jsonl import read_events, read_workload


def _cache(name: str, block_size: int, capacity: int):
    cache_type = HashBlockCache if name == "hash" else RadixPrefixCache
    return cache_type(block_size=block_size, capacity_blocks=capacity)


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _print_replay(report: ReplayReport, as_json: bool, include_requests: bool = False) -> None:
    data = report.to_mapping()
    if not include_requests:
        data.pop("request_results")
    if as_json:
        _print_json(data)
        return
    print(f"缓存策略               {report.cache}")
    print(f"请求数                 {report.requests}")
    print(f"输入 tokens            {report.input_tokens}")
    print(f"命中 tokens            {report.cached_tokens}")
    print(f"重算 tokens            {report.computed_tokens}")
    print(f"命中率                 {report.hit_ratio:.1%}")
    print(f"Evictions              {report.evictions}")
    print(f"Peak blocks            {report.peak_blocks}/{report.capacity_blocks}")
    if include_requests:
        print("\n逐请求结果")
        for row in report.request_results:
            print(f"{row.request_id:<24} 命中 {row.matched_tokens:>6} / {row.input_tokens:<6}  miss={row.miss_reason}")


def _summary(events: list[Event]) -> dict[str, Any]:
    rows = summarize_requests(events)
    phase_names = ("queue_ns", "prefill_ns", "decode_ns", "ttft_ns")
    totals = {
        phase: sum(getattr(row, phase) for row in rows if getattr(row, phase) is not None)
        for phase in phase_names
    }
    known_counts = {
        phase: sum(getattr(row, phase) is not None for row in rows)
        for phase in phase_names
    }
    return {
        "requests": len(rows),
        "events": len(events),
        "known_phase_counts": known_counts,
        "total_latency_ns": totals,
        "requests_detail": [row.to_mapping() for row in rows],
    }


def _format_summary(data: dict[str, Any], as_json: bool) -> None:
    if as_json:
        _print_json(data)
        return
    print(f"请求数                 {data['requests']}")
    print(f"事件数                 {data['events']}")
    labels = {"queue_ns": "Queue 总耗时", "prefill_ns": "Prefill 总耗时", "decode_ns": "Decode 总耗时", "ttft_ns": "TTFT 总耗时"}
    for phase, label in labels.items():
        count = data["known_phase_counts"][phase]
        total = data["total_latency_ns"][phase]
        shown = f"{total / 1_000_000:.3f} ms ({count} 个已知)" if count else "UNKNOWN"
        print(f"{label:<22}{shown}")


def _add_common_cache_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--block-size", type=int, default=16, help="缓存 block 的 token 数，默认 16")
    parser.add_argument("--capacity-blocks", type=int, default=4096, help="缓存容量 block 数，默认 4096")
    parser.add_argument("--json", action="store_true", help="输出 JSON")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="inferscope", description="LLM Serving Runtime 分析工具")
    commands = parser.add_subparsers(dest="command", required=True)

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
    summary_parser.add_argument("--json", action="store_true")

    inspect_parser = commands.add_parser("inspect", help="查看单个请求")
    inspect_parser.add_argument("trace")
    inspect_parser.add_argument("request_id")
    inspect_parser.add_argument("--json", action="store_true")

    trace_parser = commands.add_parser("trace", help="按时间显示请求事件")
    trace_parser.add_argument("trace")
    trace_parser.add_argument("request_id", nargs="?")
    trace_parser.add_argument("--json", action="store_true")
    return parser


def _run(args: argparse.Namespace) -> int:
    if args.command == "replay":
        requests = read_workload(args.workload)
        cache = _cache(args.cache, args.block_size, args.capacity_blocks)
        report = replay(requests, cache, args.cache)
        _print_replay(report, args.json, args.details)
        return 0
    if args.command == "compare":
        requests = read_workload(args.workload)
        reports = {}
        for name in ("hash", "radix"):
            reports[name] = replay(requests, _cache(name, args.block_size, args.capacity_blocks), name).to_mapping()
            reports[name].pop("request_results")
        if args.json:
            _print_json(reports)
        else:
            print(f"{'指标':<24}{'Hash':>14}{'Radix':>14}")
            for key, label in (("hit_ratio", "命中率"), ("cached_tokens", "命中 tokens"), ("computed_tokens", "重算 tokens"), ("evictions", "淘汰 blocks"), ("peak_blocks", "峰值 blocks")):
                left, right = reports["hash"][key], reports["radix"][key]
                if key == "hit_ratio":
                    left, right = f"{left:.1%}", f"{right:.1%}"
                print(f"{label:<24}{str(left):>14}{str(right):>14}")
        return 0
    events = read_events(args.trace)
    if args.command == "summary":
        _format_summary(_summary(events), args.json)
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
                print(f"  {event['timestamp_ns']:>16}  {event['event_type']}")
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
