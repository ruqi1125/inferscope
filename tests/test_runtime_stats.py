import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from inferscope.runtime.vllm_stats import NativeStatsWriter, read_stats, summarize_stats

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "vllm-0.29.0-native-stats.jsonl"


def test_writer_preserves_request_cache_and_engine_scope(tmp_path):
    writer = NativeStatsWriter(tmp_path / "stats.jsonl", engine_index=2)
    request = SimpleNamespace(request_id="req-a", num_prompt_tokens=64,
                              num_generation_tokens=8, num_cached_tokens=32,
                              prompt="private input")
    scheduler = SimpleNamespace(num_running_reqs=1, num_waiting_reqs=3, kv_cache_usage=0.25)
    iteration = SimpleNamespace(finished_requests=[request], num_preempted_reqs=2)
    writer.record(scheduler, iteration)
    rows = read_stats(writer.path)
    report = summarize_stats(rows)
    assert report["requests"][0]["cached_tokens"] == 32
    assert report["requests"][0]["cached_tokens_source"] == "OBSERVED"
    assert report["requests"][0]["cached_fraction"] == 0.5
    assert report["engines"][0]["preemptions_in_capture"] == 2
    assert report["engines"][0]["scope"] == "ENGINE"
    assert "private input" not in writer.path.read_text()
    assert set(rows[0]["requests"][0]) == {"request_id", "input_tokens", "output_tokens", "cached_tokens"}


def test_missing_stats_and_zero_are_distinct(tmp_path):
    writer = NativeStatsWriter(tmp_path / "stats.jsonl")
    writer.record(None, None)
    report = summarize_stats(read_stats(writer.path))
    assert report["engines"][0]["preemptions_in_capture"] is None
    assert report["engines"][0]["scheduler_samples"] == []
    assert report["engines"][0]["kv_lifecycle_source"] == "UNKNOWN"
    writer.record(None, SimpleNamespace(finished_requests=[], num_preempted_reqs=0))
    report = summarize_stats(read_stats(writer.path))
    assert report["engines"][0]["preemptions_in_capture"] == 0


def test_duplicate_request_observations_are_ambiguous(tmp_path):
    writer = NativeStatsWriter(tmp_path / "stats.jsonl")
    iteration = SimpleNamespace(finished_requests=[SimpleNamespace(
        request_id="req", num_prompt_tokens=64, num_generation_tokens=8, num_cached_tokens=32)],
        num_preempted_reqs=0)
    writer.record(None, iteration)
    writer.record(None, iteration)
    row = summarize_stats(read_stats(writer.path))["requests"][0]
    assert row["ambiguous"] is True
    assert row["cached_tokens"] is None
    assert row["cached_tokens_source"] == "UNKNOWN"


@pytest.mark.parametrize("field,value", [("cached_tokens", 65), ("cached_tokens", -1),
                                        ("input_tokens", True), ("output_tokens", 1.5)])
def test_invalid_request_reports_file_and_line(tmp_path, field, value):
    row = {"schema_version": 1, "runtime_version": "0.29.0", "observed_at_ns": 1,
           "engine_index": 0, "scheduler": None, "preemptions": None,
           "requests": [{"request_id": "req", "input_tokens": 64, "output_tokens": 8,
                         "cached_tokens": 0}]}
    row["requests"][0][field] = value
    path = tmp_path / "bad.jsonl"
    path.write_text("\n" + json.dumps(row), encoding="utf-8")
    with pytest.raises(ValueError, match=r"bad.jsonl:2:"):
        read_stats(path)


def test_writer_does_not_overwrite_existing_capture(tmp_path):
    path = tmp_path / "stats.jsonl"
    path.write_text("existing")
    with pytest.raises(FileExistsError):
        NativeStatsWriter(path)
    assert path.read_text() == "existing"


@pytest.mark.parametrize("update", [
    {"schema_version": True}, {"runtime_version": "0.28.0"},
    {"preemptions": -1}, {"observed_at_ns": -1},
    {"scheduler": {"running_requests": 0, "waiting_requests": 0, "kv_cache_usage": float("nan")}},
    {"scheduler": {"running_requests": 0, "waiting_requests": 0, "kv_cache_usage": 1.1}},
    {"scheduler": {"running_requests": 0, "waiting_requests": 0, "kv_cache_usage": 10**400}},
])
def test_invalid_native_metadata_is_rejected(tmp_path, update):
    writer = NativeStatsWriter(tmp_path / "good.jsonl")
    writer.record(None, None)
    row = read_stats(writer.path)[0]
    row.update(update)
    path = tmp_path / "invalid.jsonl"
    path.write_text(json.dumps(row), encoding="utf-8")
    with pytest.raises(ValueError, match=r"invalid.jsonl:1:"):
        read_stats(path)


def test_runtime_stats_cli_json_and_text(tmp_path):
    writer = NativeStatsWriter(tmp_path / "stats.jsonl")
    writer.record(None, None)
    root = Path(__file__).resolve().parents[1]
    command = [sys.executable, "-m", "inferscope", "runtime-stats", str(writer.path)]
    environment = {**os.environ, "PYTHONPATH": str(root / "src"), "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(command + ["--json"], env=environment, capture_output=True,
                            text=True, encoding="utf-8", check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["engines"][0]["scope"] == "ENGINE"
    result = subprocess.run(command, env=environment, capture_output=True,
                            text=True, encoding="utf-8", check=False)
    assert result.returncode == 0, result.stderr
    assert "UNKNOWN" in result.stdout
    assert "服务级" in result.stdout


def test_real_vllm_capture_fixture_records_cold_and_warm_prefix_cache():
    rows = read_stats(FIXTURE)
    report = summarize_stats(rows)
    requests = {row["request_id"]: row for row in report["requests"]}

    assert len(rows) == 20
    assert requests["req-m3-cold"]["input_tokens"] == 360
    assert requests["req-m3-cold"]["output_tokens"] == 8
    assert requests["req-m3-cold"]["cached_tokens"] == 0
    assert requests["req-m3-warm"]["input_tokens"] == 360
    assert requests["req-m3-warm"]["output_tokens"] == 8
    assert requests["req-m3-warm"]["cached_tokens"] == 352
    assert len(report["engines"][0]["scheduler_samples"]) == 20
    assert report["engines"][0]["scope"] == "ENGINE"
    assert all(row["preemptions_source"] == "UNKNOWN" for row in requests.values())
