import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from inferscope.adapters.base import iter_spans
from inferscope.adapters.sglang.adapter import SGLangAdapter
from inferscope.analyzers.request import summarize_requests


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "sglang-0.5.20-otel.json"
LATENCIES = {
    "prefill_ns": ("gen_ai.latency.time_in_model_prefill", Decimal("0.18649996403837577"), 186_499_964),
    "decode_ns": ("gen_ai.latency.time_in_model_decode", Decimal("0.00004419498145580292"), 44_195),
    "ttft_ns": ("gen_ai.latency.time_to_first_token", Decimal("0.19170415302505717"), 191_704_153),
    "e2e_ns": ("gen_ai.latency.e2e", Decimal("0.19174834800651297"), 191_748_348),
}


def _load_fixture():
    document = json.loads(FIXTURE.read_text(encoding="utf-8"), parse_float=Decimal)
    roots = [span for span in iter_spans(document) if not span.get("parentSpanId")]
    assert len(roots) == 1
    return document, roots[0]


def _typed_attributes(span):
    return {
        item["key"]: next(iter(item["value"].values()))
        for item in span.get("attributes", [])
    }


def test_real_sglang_trace_matches_root_metrics_and_correlated_stages():
    document, raw = _load_fixture()
    raw_attrs = _typed_attributes(raw)
    events = SGLangAdapter().to_events(document)
    rows = summarize_requests(events)

    assert len(rows) == 1
    assert rows[0].request_id == raw_attrs["gen_ai.request.id"] == "req-sglang-001"
    assert rows[0].arrived_ns == int(raw["startTimeUnixNano"])
    assert rows[0].finished_ns == int(raw["endTimeUnixNano"])
    assert rows[0].input_tokens == 14
    assert rows[0].output_tokens == 8
    assert rows[0].cached_tokens == 1
    assert rows[0].cached_tokens_source == "OBSERVED"

    for field, (attribute, expected_seconds, expected_ns) in LATENCIES.items():
        assert raw_attrs[attribute] == expected_seconds
        assert getattr(rows[0], field) == expected_ns
        assert rows[0].latency_sources[field] == "OBSERVED"

    assert rows[0].queue_ns == 22_004_992
    assert rows[0].latency_sources["queue_ns"] == "DERIVED"
    events_by_type = {}
    for event in events:
        events_by_type.setdefault(event.event_type, []).append(event)
    assert len(events_by_type["FRAMEWORK_SPAN"]) == 6
    assert events_by_type["REQUEST_QUEUED"][0].timestamp_ns == 1_005_957_120
    assert events_by_type["REQUEST_SCHEDULED"][0].timestamp_ns == 1_027_962_112
    assert events_by_type["PREFILL_STARTED"][0].request_id == "req-sglang-001"
    assert events_by_type["DECODE_STEP"][0].request_id == "req-sglang-001"
    assert all(
        event.attributes["request_id_source"] == "TRACE_ID_ASSOCIATION"
        for event in events
        if event.event_type not in {"REQUEST_ARRIVED", "REQUEST_FINISHED"}
    )


def test_fixture_flows_through_cli(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    environment = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "PYTHONIOENCODING": "utf-8",
    }
    adapt = subprocess.run(
        [sys.executable, "-m", "inferscope", "adapt", "sglang", str(FIXTURE), "--output", str(trace_path)],
        cwd=ROOT, env=environment, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert adapt.returncode == 0, adapt.stderr
    summary = subprocess.run(
        [sys.executable, "-m", "inferscope", "summary", str(trace_path), "--json"],
        cwd=ROOT, env=environment, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert summary.returncode == 0, summary.stderr
    request = json.loads(summary.stdout)["requests_detail"][0]
    assert request["request_id"] == "req-sglang-001"
    assert request["cached_tokens"] == 1
    assert request["cached_tokens_source"] == "OBSERVED"
    assert request["queue_ns"] == 22_004_992

    readable = subprocess.run(
        [sys.executable, "-m", "inferscope", "summary", str(trace_path)],
        cwd=ROOT, env=environment, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert readable.returncode == 0, readable.stderr
    assert "cached=1/14" in readable.stdout
    assert "来源=OBSERVED" in readable.stdout


def test_fixture_is_sanitized_and_uses_only_trace_whitelist():
    document, _ = _load_fixture()
    allowed_attributes = {
        "gen_ai.request.id",
        "gen_ai.request.max_tokens",
        "gen_ai.usage.prompt_tokens",
        "gen_ai.usage.completion_tokens",
        "gen_ai.usage.cached_tokens",
        "gen_ai.latency.time_to_first_token",
        "gen_ai.latency.e2e",
        "gen_ai.latency.time_in_model_decode",
        "gen_ai.latency.time_in_model_inference",
        "gen_ai.latency.time_in_model_prefill",
    }
    for span in iter_spans(document):
        assert set(_typed_attributes(span)) <= allowed_attributes
        assert "spanId" not in span
        assert "host.name" not in _typed_attributes(span)
        assert "prompt" not in span
        assert "completion" not in span


def test_ambiguous_trace_roots_do_not_attach_child_to_either_request():
    document = {"spans": [
        {"name": "request-a", "traceId": "shared-trace", "startTimeUnixNano": "1", "endTimeUnixNano": "4",
         "attributes": {"gen_ai.request.id": "a"}},
        {"name": "request-b", "traceId": "shared-trace", "startTimeUnixNano": "2", "endTimeUnixNano": "5",
         "attributes": {"gen_ai.request.id": "b"}},
        {"name": "prefill_forward", "traceId": "shared-trace", "parentSpanId": "parent",
         "startTimeUnixNano": "3", "endTimeUnixNano": "4", "attributes": {}},
    ]}

    events = SGLangAdapter().to_events(document)

    assert {event.request_id for event in events} == {"a", "b"}
    assert not any(event.event_type == "PREFILL_STARTED" for event in events)


def test_unidentified_second_root_makes_trace_association_ambiguous():
    document = {"spans": [
        {"name": "request", "traceId": "shared-trace", "startTimeUnixNano": "1", "endTimeUnixNano": "4",
         "attributes": {"gen_ai.request.id": "a"}},
        {"name": "unidentified-root", "traceId": "shared-trace", "startTimeUnixNano": "2", "endTimeUnixNano": "5",
         "attributes": {}},
        {"name": "prefill_forward", "traceId": "shared-trace", "parentSpanId": "parent",
         "startTimeUnixNano": "3", "endTimeUnixNano": "4", "attributes": {}},
    ]}

    events = SGLangAdapter().to_events(document)

    assert {event.event_type for event in events} == {"REQUEST_ARRIVED", "REQUEST_FINISHED"}
