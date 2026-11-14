"""Tests for the bounded, reporting-only post-fill recovery observer."""
from copy import deepcopy

import pytest

from hummingbot.core.overview_recovery import PostFillRecoveryObserver


def fill(order_id, trade_id, timestamp=101):
    return dict(timestamp=timestamp, order_id=order_id, exchange_trade_id=trade_id)


def sample(observer, *, now, mono, events=(), order_ids=('order-1',), valid=True,
           two_sided=False, owner=1, generation=1, session='session'):
    return observer.observe(
        dict(id=session, generation=generation, started_at=100),
        {'spot': dict(events=list(events), order_ids=set(order_ids), valid=valid, two_sided=two_sided)},
        now=now, monotonic_now=mono, owner_generation=owner,
    )['spot']


def value(result):
    return result['value']


def test_fill_is_recovered_only_on_next_continuous_confirmed_two_sided_sample():
    observer = PostFillRecoveryObserver()
    first = sample(observer, now=100, mono=0, two_sided=True)
    assert value(first)['recovered_count'] == 0
    assert value(first)['history_complete'] is False

    seen = sample(observer, now=102, mono=2, events=[fill('order-1', 'trade-1')], two_sided=False)
    assert value(seen)['pending_count'] == 1

    recovered = sample(observer, now=104, mono=4, events=[fill('order-1', 'trade-1')], two_sided=True)
    assert value(recovered)['recovered_count'] == 1
    assert value(recovered)['pending_count'] == 0
    assert value(recovered)['p50_seconds'] == 3
    assert value(recovered)['p95_seconds'] == 3
    assert value(recovered)['history_complete'] is False


def test_duplicate_and_retry_events_are_idempotent_but_same_timestamp_trades_are_distinct():
    observer = PostFillRecoveryObserver()
    sample(observer, now=100, mono=0, two_sided=False)
    events = [fill('order-1', 'trade-1'), fill('order-1', 'trade-1'), fill('order-1', 'trade-2')]
    seen = sample(observer, now=101, mono=1, events=events)
    assert value(seen)['pending_count'] == 2
    recovered = sample(observer, now=102, mono=2, events=events, two_sided=True)
    assert value(recovered)['recovered_count'] == 2
    assert value(recovered)['p50_seconds'] == 1
    assert value(recovered)['p95_seconds'] == 1


def test_unattributed_wrong_session_and_partial_observation_do_not_invent_fills():
    observer = PostFillRecoveryObserver()
    sample(observer, now=100, mono=0)
    wrong_session = sample(observer, now=101, mono=1, events=[fill('old-order', 'old-trade')],
                           order_ids=())
    assert value(wrong_session)['recovered_count'] == 0
    assert value(wrong_session)['pending_count'] == 0
    partial = sample(observer, now=102, mono=2, events=[fill('order-1', 'trade-3')], valid=False)
    assert partial['value'] is None
    assert partial['unavailable_reason']
    after = sample(observer, now=103, mono=3, two_sided=True)
    assert value(after)['recovered_count'] == 0


@pytest.mark.parametrize('event', [
    {'timestamp': 101, 'order_id': 'order-1'},
    fill('order-1', 'trade-1', float('nan')),
    fill('order-1', 'trade-1', 105),
    fill('order-1', '', 101),
])
def test_invalid_attributed_fill_makes_market_observation_unavailable(event):
    observer = PostFillRecoveryObserver()
    sample(observer, now=100, mono=0)
    result = sample(observer, now=102, mono=2, events=[event])
    assert result['value'] is None
    assert result['unavailable_reason']


def test_restart_owner_or_generation_gaps_never_credit_hidden_recovery_time():
    observer = PostFillRecoveryObserver()
    sample(observer, now=100, mono=0)
    sample(observer, now=101, mono=1, events=[fill('order-1', 'trade-1')])
    restarted = PostFillRecoveryObserver(deepcopy(observer.checkpoint))
    gap = sample(restarted, now=105, mono=0, two_sided=True, owner=2)
    assert value(gap)['recovered_count'] == 0
    assert value(gap)['pending_count'] == 1
    generation_gap = sample(restarted, now=106, mono=1, two_sided=True, owner=2, generation=2)
    assert value(generation_gap)['recovered_count'] == 0
    assert value(generation_gap)['pending_count'] == 1


def test_zero_fills_is_not_complete_history_and_different_markets_are_isolated():
    observer = PostFillRecoveryObserver()
    result = observer.observe(
        dict(id='session', generation=1),
        {
            'spot': dict(events=[], order_ids=set(), valid=True, two_sided=False),
            'perpetual': dict(events=[fill('perp-order', 'perp-fill')], order_ids={'perp-order'}, valid=True,
                              two_sided=False),
        }, now=102, monotonic_now=0, owner_generation=1,
    )
    assert value(result['spot'])['recovered_count'] == 0
    assert value(result['spot'])['p50_seconds'] is None
    assert value(result['spot'])['history_complete'] is False
    assert value(result['perpetual'])['pending_count'] == 1
    assert observer.checkpoint['markets']['spot']['pending'] == []
    assert len(observer.checkpoint['markets']['perpetual']['pending']) == 1


def test_checkpoint_is_bounded_and_reports_truncation():
    observer = PostFillRecoveryObserver(max_samples=2)
    sample(observer, now=100, mono=0, two_sided=False)
    events = [fill('order-1', f'trade-{index}', 101 + index) for index in range(3)]
    result = sample(observer, now=104, mono=4, events=events)
    assert result['value'] is None
    assert result['unavailable_reason'] == 'sample_limit_exceeded'
    state = observer.checkpoint['markets']['spot']
    assert len(state['pending']) == 2
    assert len(state['seen']) <= 2


def test_missing_market_is_independently_unavailable():
    result = PostFillRecoveryObserver().observe(
        dict(id='session', generation=1),
        {'spot': dict(events=[], order_ids=set(), valid=True, two_sided=False)},
        now=100, monotonic_now=0, owner_generation=1,
    )
    assert result['perpetual']['value'] is None
    assert result['perpetual']['stale'] is True


def test_restart_same_owner_and_generation_still_breaks_continuity():
    observer = PostFillRecoveryObserver()
    sample(observer, now=100, mono=100, two_sided=False)
    sample(observer, now=101, mono=101, events=[fill('order-1', 'trade-1')], two_sided=False)
    restarted = PostFillRecoveryObserver(deepcopy(observer.checkpoint))

    result = sample(restarted, now=102, mono=102, two_sided=True)

    assert value(result)['recovered_count'] == 0
    assert value(result)['pending_count'] == 1
    assert restarted.checkpoint['markets']['spot']['last_valid'] is True
    assert restarted.checkpoint['markets']['spot']['pending'][0]['recoverable'] is False


def test_corrupt_nested_checkpoint_is_sanitized_without_crashing():
    checkpoint = {'markets': {'spot': {
        'session_id': 'session', 'generation': 1, 'owner_generation': 1,
        'window_start': 100, 'last_wall': 101, 'last_monotonic': 1, 'last_valid': True,
        'pending': 'not-a-list', 'recovered': ['not-a-record'], 'seen': [None, {'bad': 'key'}],
    }}}
    observer = PostFillRecoveryObserver(checkpoint)

    result = sample(observer, now=102, mono=2)

    assert value(result)['recovered_count'] == 0
    assert value(result)['pending_count'] == 0
    state = observer.checkpoint['markets']['spot']
    assert state['pending'] == []
    assert state['recovered'] == []
    assert state['seen'] == []
    assert state['last_valid'] is True


def test_checkpoint_restore_caps_combined_sample_memory():
    def pending(index):
        return dict(key=f'pending-{index}', timestamp=100 + index, recoverable=True)

    def recovered(index):
        return dict(key=f'recovered-{index}', timestamp=100 + index, seconds=float(index))

    checkpoint = {'markets': {'spot': {
        'session_id': 'session', 'generation': 1, 'owner_generation': 1,
        'window_start': 100, 'last_wall': 109, 'last_monotonic': 9, 'last_valid': True,
        'pending': [pending(index) for index in range(8)],
        'recovered': [recovered(index) for index in range(8)],
        'seen': [f'seen-{index}' for index in range(20)],
    }}}
    observer = PostFillRecoveryObserver(checkpoint, max_samples=2)
    state = observer.checkpoint['markets']['spot']

    assert len(state['pending']) + len(state['recovered']) <= 2
    assert len(state['seen']) <= 2
    result = sample(observer, now=110, mono=10, two_sided=True)
    assert value(result)['recovered_count'] <= 2
    assert value(result)['pending_count'] <= 2


def test_sample_limit_failure_recovers_after_old_window_expires():
    observer = PostFillRecoveryObserver(max_samples=1)
    sample(observer, now=100, mono=0)
    assert value(sample(observer, now=101, mono=1, events=[fill('order-1', 'first')]))['pending_count'] == 1
    capped = sample(observer, now=102, mono=2, events=[fill('order-1', 'second', 102)])
    assert capped['value'] is None and capped['unavailable_reason'] == 'sample_limit_exceeded'
    refreshed = sample(observer, now=1902, mono=1802, events=[fill('order-1', 'new-window', 1901)])
    assert value(refreshed)['pending_count'] == 1
    assert value(refreshed)['recovered_count'] == 0
