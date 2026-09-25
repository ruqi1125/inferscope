import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from inferscope.core.events import Event
from inferscope.runtime.correlation import correlate_runtime_stats
from inferscope.runtime.vllm_stats import NativeStatsWriter
from inferscope.storage.jsonl import write_events


ROOT = Path(__file__).resolve().parents[1]
NATIVE_FIXTURE = ROOT / "tests" / "fixtures" / "vllm-0.29.0-native-stats.jsonl"
OTEL_FIXTURE = ROOT / "tests" / "fixtures" / "vllm-0.29.0-otel.json"


def _record(writer, request_id, cached_tokens=0, input_tokens=64):
    request = SimpleNamespace(
        request_id=request_id,
        num_prompt_tokens=input_tokens,
        num_generation_tokens=8,
        num_cached_tokens=cached_tokens,
    )
    writer.record(None, SimpleNamespace(finished_requests=[request], num_preempted_reqs=0))


def _trace_events():
    events = []
    for index, request_id in enumerate(("req-match", "req-unmatched", "req-ambiguous", "req-cross-engine")):
        start = index * 100
        events.extend((
            Event(start, "REQUEST_ARRIVED", request_id, {"input_tokens": 64}),
            Event(start + 10, "PREFILL_FINISHED", request_id),
            Event(start + 20, "REQUEST_FINISHED", request_id, {"output_tokens": 8}),
        ))
    return events


def test_summary_correlates_only_unique_request_ids_and_keeps_engine_scope_separate(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    write_events(trace_path, _trace_events())

    stats_path = tmp_path / "stats.jsonl"
    engine_zero = NativeStatsWriter(stats_path, engine_index=0)
    _record(engine_zero, "req-match", 32)
    _record(engine_zero, "req-orphan", 0)
    _record(engine_zero, "req-ambiguous", 8)
    _record(engine_zero, "req-ambiguous", 16)
    _record(engine_zero, "req-cross-engine", 8)
    engine_one = NativeStatsWriter(tmp_path / "engine-one.jsonl", engine_index=1)
    _record(engine_one, "req-cross-engine", 16)

    environment = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "PYTHONIOENCODING": "utf-8",
    }
    command = [
        sys.executable, "-m", "inferscope", "summary", str(trace_path),
        "--runtime-stats", str(stats_path), "--runtime-stats", str(engine_one.path), "--json",
    ]
    result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True,
                            text=True, encoding="utf-8", check=False)

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    requests = {row["request_id"]: row for row in report["requests_detail"]}
    assert requests["req-match"]["runtime_stats"] == {
        "scope": "REQUEST",
        "status": "OBSERVED",
        "engine_index": 0,
        "observed_at_ns": requests["req-match"]["runtime_stats"]["observed_at_ns"],
        "cached_tokens": 32,
        "cached_tokens_source": "OBSERVED",
        "cached_fraction": 0.5,
        "cached_fraction_source": "DERIVED",
        "cache_origin": "UNKNOWN",
        "miss_reason": "UNKNOWN",
    }
    assert isinstance(requests["req-match"]["runtime_stats"]["observed_at_ns"], int)
    assert requests["req-unmatched"]["runtime_stats"]["status"] == "UNKNOWN"
    assert requests["req-unmatched"]["runtime_stats"]["reason"] == "NO_RUNTIME_OBSERVATION"
    assert requests["req-ambiguous"]["runtime_stats"]["reason"] == "AMBIGUOUS_RUNTIME_OBSERVATIONS"
    assert requests["req-cross-engine"]["runtime_stats"]["reason"] == "AMBIGUOUS_RUNTIME_OBSERVATIONS"
    assert report["runtime_stats"]["correlation"] == {
        "matched_requests": 1,
        "unmatched_trace_request_ids": ["req-unmatched"],
        "unmatched_runtime_request_ids": ["req-orphan"],
        "ambiguous_request_ids": ["req-ambiguous", "req-cross-engine"],
        "unknown_native_value_request_ids": [],
    }
    assert [engine["scope"] for engine in report["runtime_stats"]["engines"]] == ["ENGINE", "ENGINE"]
    assert "scheduler_samples" not in requests["req-match"]["runtime_stats"]


def test_summary_without_runtime_stats_keeps_existing_json_shape(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    write_events(trace_path, _trace_events()[:3])
    result = subprocess.run(
        [sys.executable, "-m", "inferscope", "summary", str(trace_path), "--json"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert "runtime_stats" not in report
    assert "runtime_stats" not in report["requests_detail"][0]


def test_summary_text_distinguishes_request_and_engine_observations(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    write_events(trace_path, _trace_events()[:3])
    stats_path = tmp_path / "stats.jsonl"
    writer = NativeStatsWriter(stats_path)
    _record(writer, "req-match", 32)

    result = subprocess.run(
        [sys.executable, "-m", "inferscope", "summary", str(trace_path),
         "--runtime-stats", str(stats_path)],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "请求级缓存观测" in result.stdout
    assert "req-match" in result.stdout and "32/64" in result.stdout
    assert "服务级" in result.stdout


def test_summary_text_preserves_known_zero_input_tokens(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    write_events(trace_path, [
        Event(1, "REQUEST_ARRIVED", "req-empty", {"input_tokens": 0}),
        Event(2, "REQUEST_FINISHED", "req-empty", {"output_tokens": 1}),
    ])
    stats_path = tmp_path / "stats.jsonl"
    _record(NativeStatsWriter(stats_path), "req-empty", input_tokens=0)

    result = subprocess.run(
        [sys.executable, "-m", "inferscope", "summary", str(trace_path),
         "--runtime-stats", str(stats_path)],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "cached tokens=0/0" in result.stdout


def test_separate_real_capture_fixtures_remain_unmatched(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    adapt = subprocess.run(
        [sys.executable, "-m", "inferscope", "adapt", "vllm", str(OTEL_FIXTURE),
         "--output", str(trace_path)],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert adapt.returncode == 0, adapt.stderr
    result = subprocess.run(
        [sys.executable, "-m", "inferscope", "summary", str(trace_path),
         "--runtime-stats", str(NATIVE_FIXTURE), "--json"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["runtime_stats"]["correlation"] == {
        "matched_requests": 0,
        "unmatched_trace_request_ids": ["req-vllm-029-001"],
        "unmatched_runtime_request_ids": ["req-m3-cold", "req-m3-warm"],
        "ambiguous_request_ids": [],
        "unknown_native_value_request_ids": [],
    }
    assert report["requests_detail"][0]["runtime_stats"]["status"] == "UNKNOWN"


def test_pure_correlation_keeps_unknown_native_values_and_does_not_mutate_inputs():
    summary = {"requests_detail": [{"request_id": "req", "input_tokens": 64}]}
    native_report = {
        "runtime": "vllm",
        "runtime_version": "0.29.0",
        "requests": [{
            "request_id": "req",
            "engine_index": 0,
            "observations": 1,
            "ambiguous": False,
            "cached_tokens": None,
            "cached_tokens_source": "UNKNOWN",
        }],
        "engines": [],
    }

    result = correlate_runtime_stats(summary, native_report)

    assert summary == {"requests_detail": [{"request_id": "req", "input_tokens": 64}]}
    assert result["requests_detail"][0]["runtime_stats"] == {
        "scope": "REQUEST",
        "status": "UNKNOWN",
        "reason": "NATIVE_VALUE_UNKNOWN",
    }
    assert result["runtime_stats"]["correlation"]["unknown_native_value_request_ids"] == ["req"]
