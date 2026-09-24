import pytest

from inferscope.analyzers.kv_cache import analyze_kv_events
from inferscope.analyzers.request import summarize_requests
from inferscope.cache.hash_cache import HashBlockCache
from inferscope.cache.radix_cache import RadixPrefixCache
from inferscope.core.events import Event
from inferscope.storage.jsonl import read_events, read_workload, JSONLInputError


@pytest.mark.parametrize('cache_type', [HashBlockCache, RadixPrefixCache])
def test_zero_capacity_never_reuses_tokens(cache_type):
    cache = cache_type(2, 0)
    cache.insert((1, 2, 3, 4))
    assert cache.lookup((1, 2, 3, 4)).matched_tokens == 0
    assert cache.cached_blocks == 0


@pytest.mark.parametrize('cache_type', [HashBlockCache, RadixPrefixCache])
def test_eviction_preserves_recent_independent_prefix(cache_type):
    cache = cache_type(2, 2)
    cache.insert((1, 2))
    cache.insert((3, 4))
    assert cache.lookup((1, 2)).matched_tokens == 2
    cache.insert((5, 6))
    assert cache.lookup((1, 2)).matched_tokens == 2
    assert cache.lookup((3, 4)).matched_tokens == 0
    assert cache.cached_blocks == 2
    assert cache.stats.evictions == 1


@pytest.mark.parametrize('events', [
    [Event(1, 'KV_REUSE', 'r', {'block_id': 0})],
    [Event(1, 'KV_ALLOCATE', 'r', {'block_id': 0, 'token_count': 4}),
     Event(2, 'KV_EVICT', 'r', {'block_id': 0})],
    [Event(1, 'KV_ALLOCATE', 'r', {'block_id': 0, 'token_count': 4}),
     Event(2, 'KV_ALLOCATE', 'r', {'block_id': 0, 'token_count': 4})],
])
def test_invalid_kv_transitions_are_rejected(events):
    with pytest.raises(ValueError):
        analyze_kv_events(events)


def test_reversed_stage_boundaries_remain_unknown():
    row = summarize_requests([Event(20, 'PREFILL_STARTED', 'r'),
        Event(10, 'PREFILL_FINISHED', 'r')])[0]
    assert row.prefill_ns is None
    assert row.latency_sources['prefill_ns'] == 'UNKNOWN'


@pytest.mark.parametrize('reader,record', [(read_events,
    '{"timestamp_ns":true,"event_type":"REQUEST_ARRIVED","request_id":"r"}'),
    (read_workload, '{"request_id":"r","timestamp":0,"input_token_ids":[true],"output_tokens":0}')])
def test_invalid_jsonl_retains_file_and_line(reader, record, tmp_path):
    path = tmp_path / 'bad.jsonl'
    path.write_text('\n' + record + '\n', encoding='utf-8')
    with pytest.raises(JSONLInputError) as caught:
        reader(path)
    assert caught.value.path == path
    assert caught.value.line == 2
