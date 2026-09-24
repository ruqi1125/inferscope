import pytest

from inferscope.analyzers.request import summarize_requests
from inferscope.analyzers.workload import analyze_workload
from inferscope.cache.hash_cache import HashBlockCache
from inferscope.core.events import Event
from inferscope.core.request import WorkloadRequest
from inferscope.replay.engine import replay
from inferscope.cli.main import _run, build_parser
from inferscope.adapters.vllm.adapter import VLLMAdapter
from inferscope.cli.main import _summary
import json


def test_latency_sources_follow_measurement_boundary_and_missing_values():
    row = summarize_requests([
        Event(10, 'REQUEST_ARRIVED', 'r'),
        Event(20, 'REQUEST_SCHEDULED', 'r'),
        Event(50, 'REQUEST_FINISHED', 'r', {'queue_ns': 0}),
    ])[0].to_mapping()
    assert row['queue_ns'] == 0
    assert row['e2e_ns'] == 40
    assert row['prefill_ns'] is None
    assert row['latency_sources'] == dict(queue_ns='OBSERVED', prefill_ns='UNKNOWN',
        decode_ns='UNKNOWN', ttft_ns='UNKNOWN', e2e_ns='DERIVED')


def test_invalid_measurement_does_not_get_observed_label():
    row = summarize_requests([Event(10, 'REQUEST_ARRIVED', 'r'),
        Event(20, 'REQUEST_SCHEDULED', 'r'),
        Event(30, 'REQUEST_FINISHED', 'r', {'queue_ns': True})])[0].to_mapping()
    assert row['queue_ns'] == 10
    assert row['latency_sources']['queue_ns'] == 'DERIVED'


def test_replay_and_reuse_are_explicitly_simulated_and_repeatable():
    requests = [WorkloadRequest('a', 0, (1, 2, 3), 2), WorkloadRequest('b', 0, (1, 2, 3), 2)]
    first = replay(requests, HashBlockCache(2, 2), 'hash')
    second = replay(requests, HashBlockCache(2, 2), 'hash')
    assert first.to_mapping() == second.to_mapping()
    assert [r.request_id for r in first.request_results] == ['a', 'b']
    assert first.cached_tokens == 2
    assert first.to_mapping()['analysis_mode'] == 'SIMULATED'
    assert analyze_workload(requests, first, 2).to_mapping()['analysis_mode'] == 'SIMULATED'


@pytest.mark.parametrize('command', ['replay', 'compare'])
@pytest.mark.parametrize('as_json', [False, True])
def test_cli_labels_cache_results_as_simulated(tmp_path, capsys, command, as_json):
    path = tmp_path / 'workload.jsonl'
    path.write_text('{"request_id":"r","timestamp":0,"input_token_ids":[1,2],"output_tokens":0}\n', encoding='utf-8')
    args = [command, str(path), '--block-size', '2'] + (['--json'] if as_json else [])
    assert _run(build_parser().parse_args(args)) == 0
    output = capsys.readouterr().out
    assert ('SIMULATED' if as_json else '离线模拟') in output


def test_vllm_reconstructed_stage_timestamps_are_marked_derived(tmp_path, capsys):
    document = {'spans': [{'name': 'llm_request', 'startTimeUnixNano': '1000000000',
        'endTimeUnixNano': '1100000000', 'attributes': [
            {'key': 'gen_ai.request.id', 'value': {'stringValue': 'r'}},
            {'key': 'gen_ai.latency.time_in_queue', 'value': {'doubleValue': 0.01}},
            {'key': 'gen_ai.latency.time_in_model_prefill', 'value': {'doubleValue': 0.02}},
        ]}]}
    events = VLLMAdapter().to_events(document)
    stages = [e for e in events if e.event_type in ('REQUEST_QUEUED', 'REQUEST_SCHEDULED', 'PREFILL_STARTED', 'PREFILL_FINISHED')]
    assert len(stages) == 4
    assert all(e.attributes['timestamp_source'] == 'DERIVED' for e in stages)
    path = tmp_path / 'trace.jsonl'
    path.write_text('\n'.join(json.dumps(e.to_mapping()) for e in events), encoding='utf-8')
    assert _run(build_parser().parse_args(['trace', str(path)])) == 0
    assert 'DERIVED' in capsys.readouterr().out


def test_summary_aggregates_mixed_sources_and_inspect_agrees(tmp_path, capsys):
    events = [Event(10, 'REQUEST_ARRIVED', 'a'),
        Event(20, 'REQUEST_SCHEDULED', 'a'),
        Event(30, 'REQUEST_FINISHED', 'a', {'queue_ns': 5}),
        Event(10, 'REQUEST_ARRIVED', 'b'), Event(25, 'REQUEST_SCHEDULED', 'b'),
        Event(10, 'REQUEST_ARRIVED', 'c')]
    data = _summary(events)
    assert data['total_latency_ns']['queue_ns'] == 20
    assert data['latency_source_counts']['queue_ns'] == {'OBSERVED': 1, 'DERIVED': 1, 'UNKNOWN': 1}
    path = tmp_path / 'trace.jsonl'
    path.write_text('\n'.join(json.dumps(e.to_mapping()) for e in events), encoding='utf-8')
    assert _run(build_parser().parse_args(['inspect', str(path), 'b', '--json'])) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected['request'] == data['requests_detail'][1]
