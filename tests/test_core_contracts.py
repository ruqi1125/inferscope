import pytest

from inferscope.core.events import Event
from inferscope.core.request import WorkloadRequest
from inferscope.cache.hash_cache import HashBlockCache
from inferscope.cache.radix_cache import RadixPrefixCache


@pytest.mark.parametrize('timestamp', [True, 1.5, '1', -1, None])
def test_direct_event_rejects_invalid_nanosecond_timestamp(timestamp):
    with pytest.raises(ValueError):
        Event(timestamp, 'REQUEST_ARRIVED', 'r')


@pytest.mark.parametrize('field,value', [('event_type', ' '), ('request_id', 1), ('request_id', ' ')])
def test_event_identifiers_are_nonempty_strings(field, value):
    values = dict(timestamp_ns=0, event_type='REQUEST_ARRIVED', request_id='r')
    values[field] = value
    with pytest.raises(ValueError):
        Event(**values)


@pytest.mark.parametrize('field,value', [
    ('timestamp', True), ('timestamp', float('nan')), ('timestamp', float('inf')),
    ('timestamp', -1), ('input_token_ids', (True,)), ('input_token_ids', (-1,)),
    ('output_tokens', -1), ('output_tokens', True), ('request_id', ' '),
])
def test_direct_workload_enforces_input_contract(field, value):
    values = dict(request_id='r', timestamp=0, input_token_ids=(1, 2), output_tokens=0)
    values[field] = value
    with pytest.raises(ValueError):
        WorkloadRequest(**values)


@pytest.mark.parametrize('cache_type', [HashBlockCache, RadixPrefixCache])
@pytest.mark.parametrize('field,value', [('block_size', True), ('block_size', 1.5),
    ('capacity_blocks', True), ('capacity_blocks', 1.5)])
def test_cache_sizes_must_be_integers(cache_type, field, value):
    with pytest.raises(ValueError):
        cache_type(**{field: value})


def test_valid_direct_models_match_json_parsing():
    event = Event(0, 'REQUEST_ARRIVED', 'r')
    assert Event.from_mapping(event.to_mapping()) == event
    assert WorkloadRequest.from_mapping(dict(request_id='r', timestamp=0,
        input_token_ids=[1, 2], output_tokens=0)) == WorkloadRequest('r', 0, (1, 2), 0)


def test_event_extensions_cannot_replace_core_fields():
    with pytest.raises(ValueError):
        Event(0, 'REQUEST_ARRIVED', 'r', {'timestamp_ns': -1})
