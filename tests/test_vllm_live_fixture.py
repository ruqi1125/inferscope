import copy
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from inferscope.adapters.base import iter_spans
from inferscope.adapters.vllm.adapter import VLLMAdapter
from inferscope.analyzers.request import summarize_requests


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "vllm-0.29.0-otel.json"
ALLOWED_ATTRIBUTES = {
    "gen_ai.request.id",
    "gen_ai.usage.prompt_tokens",
    "gen_ai.usage.completion_tokens",
    "gen_ai.latency.time_in_queue",
    "gen_ai.latency.time_in_model_prefill",
    "gen_ai.latency.time_in_model_decode",
    "gen_ai.latency.time_to_first_token",
    "gen_ai.latency.e2e",
}
LATENCIES = {
    "queue_ns": ("gen_ai.latency.time_in_queue", Decimal("0.000054589007049798965"), 54_589),
    "prefill_ns": ("gen_ai.latency.time_in_model_prefill", Decimal("0.057814636995317414"), 57_814_637),
    "decode_ns": ("gen_ai.latency.time_in_model_decode", Decimal("0.13830458300071768"), 138_304_583),
    "ttft_ns": ("gen_ai.latency.time_to_first_token", Decimal("0.11554598808288574"), 115_545_988),
    "e2e_ns": ("gen_ai.latency.e2e", Decimal("0.25359678268432617"), 253_596_783),
}


def _typed_attributes(attributes):
    return {
        item["key"]: next(iter(item["value"].values()))
        for item in attributes
    }


def _load_fixture():
    document = json.loads(FIXTURE.read_text(encoding="utf-8"), parse_float=Decimal)
    request_spans = [span for span in iter_spans(document) if span["name"] == "llm_request"]
    assert len(request_spans) == 1
    return document, request_spans[0]


def test_real_vllm_request_matches_raw_span():
    document, raw = _load_fixture()
    raw_attrs = _typed_attributes(raw["attributes"])
    events = VLLMAdapter().to_events(document)
    rows = summarize_requests(events)
    arrived = next(event for event in events if event.event_type == "REQUEST_ARRIVED")
    finished = next(event for event in events if event.event_type == "REQUEST_FINISHED")

    assert len(rows) == 1
    assert rows[0].request_id == raw_attrs["gen_ai.request.id"] == "req-vllm-029-001"
    assert arrived.timestamp_ns == int(raw["startTimeUnixNano"])
    assert finished.timestamp_ns == int(raw["endTimeUnixNano"])
    for metric, (attribute, expected_seconds, expected_ns) in LATENCIES.items():
        assert raw_attrs[attribute] == expected_seconds
        assert finished.attributes[metric] == expected_ns
        assert rows[0].latency_sources[metric] == "OBSERVED"

    assert raw_attrs["gen_ai.usage.prompt_tokens"] == "14"
    assert raw_attrs["gen_ai.usage.completion_tokens"] == "8"
    assert rows[0].input_tokens == 14
    assert rows[0].output_tokens == 8


def test_fixture_contains_only_whitelisted_request_data():
    document, _ = _load_fixture()

    for resource in document["resourceSpans"]:
        assert "attributes" not in resource.get("resource", {})
    for span in iter_spans(document):
        assert {item["key"] for item in span["attributes"]} <= ALLOWED_ATTRIBUTES
        assert _typed_attributes(span["attributes"]).get("gen_ai.request.id") == "req-vllm-029-001"
        assert "traceId" not in span
        assert "spanId" not in span


def test_missing_latency_fields_are_not_fabricated():
    document, raw = _load_fixture()
    without_latency = copy.deepcopy(document)
    for span in iter_spans(without_latency):
        span["attributes"] = [
            item for item in span["attributes"]
            if not item["key"].startswith("gen_ai.latency.")
        ]
    summary = summarize_requests(VLLMAdapter().to_events(without_latency))[0]

    for field in ("queue_ns", "prefill_ns", "decode_ns", "ttft_ns"):
        assert getattr(summary, field) is None
        assert summary.latency_sources[field] == "UNKNOWN"
    assert summary.e2e_ns == int(raw["endTimeUnixNano"]) - int(raw["startTimeUnixNano"])
    assert summary.latency_sources["e2e_ns"] == "DERIVED"


def test_real_vllm_fixture_flows_through_cli(tmp_path):
    _, raw = _load_fixture()
    trace_path = tmp_path / "trace.jsonl"
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    adapt = subprocess.run(
        [sys.executable, "-m", "inferscope", "adapt", "vllm", str(FIXTURE), "--output", str(trace_path)],
        cwd=ROOT, env=environment, capture_output=True, text=True, check=False,
    )
    assert adapt.returncode == 0, adapt.stderr
    summary_result = subprocess.run(
        [sys.executable, "-m", "inferscope", "summary", str(trace_path), "--json"],
        cwd=ROOT, env=environment, capture_output=True, text=True, check=False,
    )
    inspect_result = subprocess.run(
        [sys.executable, "-m", "inferscope", "inspect", str(trace_path), "req-vllm-029-001", "--json"],
        cwd=ROOT, env=environment, capture_output=True, text=True, check=False,
    )
    assert summary_result.returncode == 0, summary_result.stderr
    assert inspect_result.returncode == 0, inspect_result.stderr
    summary = json.loads(summary_result.stdout)["requests_detail"][0]
    inspected = json.loads(inspect_result.stdout)["request"]
    assert summary["request_id"] == inspected["request_id"] == "req-vllm-029-001"
    assert summary["arrived_ns"] == int(raw["startTimeUnixNano"])
    assert summary["finished_ns"] == int(raw["endTimeUnixNano"])
    assert summary["input_tokens"] == 14
    assert summary["output_tokens"] == 8
    for field, (_, _, expected_ns) in LATENCIES.items():
        assert summary[field] == expected_ns
        assert summary["latency_sources"][field] == "OBSERVED"
