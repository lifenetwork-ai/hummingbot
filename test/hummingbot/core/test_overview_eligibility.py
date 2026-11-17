"""Eligibility is independent of readiness, elapsed coverage and quote turnover."""
import json
import random

import pytest

from hummingbot.core.mm_liquidity import LiquidityObserver, rolling_coverage


def sample(observer, at, *, restriction=None, valid=True, quotes=True, pending=False, generation=0):
    reasons = {'BUY': [restriction] if restriction else ['active_executor'], 'SELL': []}
    market = dict(valid=valid, eligible=True, slo_scope_valid=True, session_admitted=True, two_sided=quotes, connector='okx', pair='BTC-USDT',
                  bid_ids=['bid'] if quotes else [], ask_ids=['ask'] if quotes else [],
                  proposal=dict(observed_at=at, reasons=reasons, proposed={'BUY': 1, 'SELL': 1}),
                  pending_orders={'BUY': {'submission_pending': int(pending)}, 'SELL': {}},
                  working_orders=[], best_bid=None, best_ask=None, bid_notional=None, ask_notional=None,
                  spread_bps=None)
    return observer.observe(dict(session_id='session', generation=generation, profile_id='profile',
                                 started_at=1000, status='running'), {'spot': market},
                            now=at, monotonic_now=at, owner_generation=1)


def test_restriction_does_not_turn_elapsed_coverage_into_the_eligible_slo():
    observer = LiquidityObserver()
    for at in range(1000, 2801):
        frame = sample(observer, at, restriction='inventory_limit' if at <= 1900 else None, quotes=at > 1900)
    value = rolling_coverage(frame, 'spot', now=2800)
    # The state change itself is unknown; neither endpoint owns the whole interval.
    assert value['eligible_seconds'] == 899
    assert value['restricted_seconds'] == 900
    assert value['eligibility_unknown_seconds'] == 1
    assert value['conditional_eligible_fraction'] == 1
    assert value['conservative_eligible_fraction'] == pytest.approx(899 / 900)
    assert value['rolling_30m_fraction'] == pytest.approx(899 / 1800)
    assert value['restriction_seconds']['inventory'] == 900


def test_verified_restricted_and_eligible_buckets_keep_distinct_denominators():
    # Independently specified, fully proved one-second intervals. The observer
    # transition test above separately verifies its uncertain boundary is lost
    # to unknown, rather than optimistically inventing this complete evidence.
    buckets = {}
    for second in range(1000, 2800):
        eligible = int(second >= 1900)
        buckets[str(second)] = [second, second + 1, 1, eligible, eligible, eligible,
                                0, 1 - eligible, 0, 0, 0]
    frame = dict(started_at=1000, _coverage_buckets={"spot": buckets})
    value = rolling_coverage(frame, "spot", now=2800)
    assert value["eligible_seconds"] == value["restricted_seconds"] == 900
    assert value["two_sided_eligible_seconds"] == 900
    assert value["eligibility_unknown_seconds"] == 0
    assert value["rolling_30m_fraction"] == .5
    assert value["conditional_eligible_fraction"] == value["conservative_eligible_fraction"] == 1
    assert value["measurement_ready"] is True


def test_missing_evidence_never_becomes_successful_eligible_time():
    observer = LiquidityObserver()
    for at in range(1000, 2801):
        frame = sample(observer, at, valid=at <= 1900)
    value = rolling_coverage(frame, 'spot', now=2800)
    assert value['eligible_seconds'] == 900
    assert value['eligibility_unknown_seconds'] == 900
    assert value['conditional_eligible_fraction'] == 1
    assert value['conservative_eligible_fraction'] == .5
    assert value['measurement_ready'] is True


def test_pending_ack_is_missing_eligible_quotes_rather_than_a_guard_exception():
    observer = LiquidityObserver()
    sample(observer, 1000, quotes=False, pending=True)
    frame = sample(observer, 1002, quotes=False, pending=True)
    value = rolling_coverage(frame, 'spot', now=1002)
    assert value['eligible_seconds'] == 2
    assert value['two_sided_eligible_seconds'] == 0
    assert value['restricted_seconds'] == 0
    assert value['conditional_eligible_fraction'] == 0
    assert value['measurement_ready'] is False


def test_legacy_history_does_not_prove_eligibility_from_readiness_counters():
    frame = dict(started_at=1000, _coverage_history={'spot': [dict(start=1000, end=2800, two_sided=True)]})
    value = rolling_coverage(frame, 'spot', now=2800)
    assert value['rolling_30m_fraction'] == 1
    assert value['eligible_seconds'] == 0
    assert value['eligibility_unknown_seconds'] == 1800
    assert value['conditional_eligible_fraction'] is None
    assert value['measurement_ready'] is False


@pytest.mark.parametrize('reason,group', [('inventory_limit', 'inventory'),
                                          ('insufficient_quote_balance', 'balance'),
                                          ('bootstrap_band', 'bootstrap'),
                                          ('reference_deviation', 'guard')])
def test_fresh_restrictions_are_bounded_and_do_not_excuse_unknown_data(reason, group):
    observer = LiquidityObserver()
    sample(observer, 1000, restriction=reason, quotes=False)
    frame = sample(observer, 1002, restriction=reason, quotes=False)
    value = rolling_coverage(frame, 'spot', now=1002)
    assert value['restricted_seconds'] == 2
    assert value['restriction_seconds'][group] == 2
    assert value['conditional_eligible_fraction'] is None


def test_readiness_and_old_or_unproven_proposals_are_not_funding_evidence():
    from hummingbot.core.overview_coverage import eligibility

    observer = LiquidityObserver()
    sample(observer, 1000, quotes=False)
    observation = observer.previous['spot']
    assert eligibility(observation, now=1000, running=True) == 'eligible'
    observation['slo_scope_valid'] = False
    assert eligibility(observation, now=1000, running=True) == 'unknown'
    observation['slo_scope_valid'] = True
    observation['session_admitted'] = False
    assert eligibility(observation, now=1000, running=True) == 'unknown'
    observation['proposal']['reasons']['BUY'] = ['bootstrap_band']
    assert eligibility(observation, now=1000, running=True) == 'bootstrap'
    observation['proposal']['reasons']['BUY'] = ['active_executor']
    observation['session_admitted'] = True
    assert eligibility(observation, now=1006, running=True) == 'unknown'
    assert eligibility(observation, now=999, running=True) == 'unknown'
    observation['proposal']['proposed'] = {'BUY': 0, 'SELL': 0}
    assert eligibility(observation, now=1000, running=True) == 'unknown'
    observation['proposal']['reasons']['BUY'] = ['inventory_limit', 'account_balance_unavailable']
    assert eligibility(observation, now=1000, running=True) == 'unknown'


def test_full_window_high_frequency_churn_preserves_counts_with_bounded_checkpoints():
    """A hand-built interval oracle does not reuse projection/classifier helpers."""
    generator = random.Random(82)
    observer = LiquidityObserver()
    previous = None
    intervals = []
    for step in range(3603):
        at = 1000 + step / 2
        state = generator.choice(['eligible', 'inventory', 'unknown'])
        frame = sample(observer, at, restriction='inventory_limit' if state == 'inventory' else None,
                       valid=state != 'unknown')
        if previous is not None:
            old_time, old_state = previous
            if state != 'unknown' and old_state != 'unknown':
                intervals.append((old_time, at, state if state == old_state else 'unknown'))
        previous = (at, state)
    end = at
    # Fractional rolling-edge buckets are intentionally unknown, not proportionally guessed.
    start = end - 1800
    known_start = int(start) + (start != int(start))
    expected = {'eligible': 0., 'inventory': 0., 'unknown': 0.}
    observed = 0.
    for first, last, state in intervals:
        overlap = max(0., min(last, end) - max(first, known_start))
        observed += overlap
        expected[state] += overlap
    value = rolling_coverage(frame, 'spot', now=end)
    assert value['full_window'] is True
    assert value['observed_seconds'] == pytest.approx(observed)
    assert value['eligible_seconds'] == pytest.approx(expected['eligible'])
    assert value['restricted_seconds'] == pytest.approx(expected['inventory'])
    assert value['eligibility_unknown_seconds'] == pytest.approx(1800 - expected['eligible'] - expected['inventory'])
    assert sum(row['eligible_seconds'] for row in value['per_minute']) == pytest.approx(value['eligible_seconds'])
    assert len(frame['_coverage_buckets']['spot']) <= 1801
    assert len(json.dumps(frame)) < 512 * 1024


def test_noncanonical_bucket_keys_cannot_credit_the_same_second_twice():
    row = [1000, 1001, 1, 1, 1, 1, 0, 0, 0, 0, 0]
    frame = dict(started_at=1000, _coverage_buckets={'spot': {'1000': row, '01000': row}})
    value = rolling_coverage(frame, 'spot', now=2800)
    assert value['observed_seconds'] == 0
    assert value['eligible_seconds'] == 0
    assert value['eligibility_unknown_seconds'] == 1800
