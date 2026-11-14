from hummingbot.core.mm_liquidity import LiquidityObserver, rolling_coverage


def observer_sample(observer, *, at, mono, owner=1, generation=0, valid=True, quotes=True, quote_id='quote'):
    session = dict(session_id='session', generation=generation, profile_id='profile', started_at=1000, status='running')
    market = dict(valid=valid, eligible=True, two_sided=quotes,
                  bid_ids=[f'bid-{quote_id}'] if quotes else [], ask_ids=[f'ask-{quote_id}'] if quotes else [],
                  best_bid=None, best_ask=None, bid_notional=None, ask_notional=None,
                  spread_bps=None, connector='okx', pair='LIFE-USDT', working_orders=None,
                  pending_orders=None)
    return observer.observe(session, {'spot': market}, now=at, monotonic_now=mono, owner_generation=owner)


def test_partial_window_uses_known_history_and_unknown_gaps_in_denominator():
    observer = LiquidityObserver()
    observer_sample(observer, at=1000, mono=0)
    frame = observer_sample(observer, at=1002, mono=2)

    result = rolling_coverage(frame, 'spot', now=1002)

    assert result['window_start'] == 1000
    assert result['window_end'] == 1002
    assert result['elapsed_seconds'] == 2
    assert result['observed_seconds'] == 2
    assert result['two_sided_seconds'] == 2
    assert result['unknown_seconds'] == 0
    assert result['rolling_30m_fraction'] == 1
    assert result['target_fraction'] is None
    assert result['full_window'] is False


def test_full_window_exactly_clips_old_interval_at_rolling_edge():
    frame = {
        'session_id': 'session', 'started_at': 0, 'ended_at': None,
        '_coverage_history': {'spot': [
            {'start': 999, 'end': 1001, 'two_sided': True},
            {'start': 1001, 'end': 2800, 'two_sided': False},
        ]},
    }

    result = rolling_coverage(frame, 'spot', now=2800, target_fraction=0.9)

    assert result['window_start'] == 1000
    assert result['window_end'] == 2800
    assert result['elapsed_seconds'] == 1800
    assert result['observed_seconds'] == 1800
    assert result['two_sided_seconds'] == 1
    assert result['rolling_30m_fraction'] == 1 / 1800
    assert result['target_fraction'] == 0.9
    assert result['full_window'] is True
    assert result['per_minute'][0] == {
        'minute_ts': 960, 'observed_seconds': 20, 'unknown_seconds': 0, 'two_sided_seconds': 1,
    }


def test_invalid_observation_leaves_explicit_unknown_interval():
    observer = LiquidityObserver()
    observer_sample(observer, at=1000, mono=0)
    observer_sample(observer, at=1002, mono=2)
    observer_sample(observer, at=1004, mono=4, valid=False)
    frame = observer_sample(observer, at=1006, mono=6)

    result = rolling_coverage(frame, 'spot', now=1006)

    assert result['observed_seconds'] == 2
    assert result['two_sided_seconds'] == 2
    assert result['unknown_seconds'] == 4
    assert result['rolling_30m_fraction'] == 2 / 6


def test_restart_restores_history_but_does_not_credit_across_restart_gap():
    observer = LiquidityObserver()
    observer_sample(observer, at=1000, mono=0)
    checkpoint = observer_sample(observer, at=1002, mono=2)
    restarted = LiquidityObserver(checkpoint)
    observer_sample(restarted, at=1010, mono=0)
    frame = observer_sample(restarted, at=1012, mono=2)

    result = rolling_coverage(frame, 'spot', now=1012)

    assert result['observed_seconds'] == 4
    assert result['two_sided_seconds'] == 4
    assert result['unknown_seconds'] == 8


def test_legacy_checkpoint_never_reconstructs_history_from_cumulative_totals():
    legacy = {
        'session_id': 'session', 'started_at': 0, 'ended_at': None,
        'markets': {'spot': {'observed_seconds': 700, 'two_sided_seconds': 650}},
    }

    result = rolling_coverage(legacy, 'spot', now=2000)

    assert result['observed_seconds'] == 0
    assert result['two_sided_seconds'] == 0
    assert result['unknown_seconds'] == 1800
    assert result['rolling_30m_fraction'] == 0


def test_owner_and_generation_changes_leave_unknown_gaps():
    observer = LiquidityObserver()
    observer_sample(observer, at=1000, mono=0)
    observer_sample(observer, at=1002, mono=2)
    observer_sample(observer, at=1004, mono=4, owner=2, generation=1)
    frame = observer_sample(observer, at=1006, mono=6, owner=2, generation=1)

    result = rolling_coverage(frame, 'spot', now=1006)

    assert result['observed_seconds'] == 4
    assert result['two_sided_seconds'] == 4
    assert result['unknown_seconds'] == 2


def test_duplicate_and_out_of_order_wall_clock_samples_do_not_credit_intervals():
    observer = LiquidityObserver()
    observer_sample(observer, at=1000, mono=0)
    observer_sample(observer, at=1000, mono=1)
    duplicate_frame = observer_sample(observer, at=1002, mono=2)
    duplicate_result = rolling_coverage(duplicate_frame, 'spot', now=1002)
    assert duplicate_result['observed_seconds'] == 0

    rollback = LiquidityObserver()
    observer_sample(rollback, at=1000, mono=0)
    observer_sample(rollback, at=999, mono=1)
    rollback_frame = observer_sample(rollback, at=1002, mono=2)
    rollback_result = rolling_coverage(rollback_frame, 'spot', now=1002)
    assert rollback_result['observed_seconds'] == 0


def test_stopped_session_clips_window_to_recorded_end():
    frame = {
        'session_id': 'session', 'started_at': 0, 'ended_at': 1500,
        '_coverage_history': {'spot': [{'start': 0, 'end': 1500, 'two_sided': True}]},
    }

    result = rolling_coverage(frame, 'spot', now=3000)

    assert result['window_end'] == 1500
    assert result['window_start'] == 0
    assert result['elapsed_seconds'] == 1500
    assert result['full_window'] is False
    assert result['observed_seconds'] == 1500


def test_high_frequency_history_is_bounded_and_corrupt_history_is_unknown():
    observer = LiquidityObserver()
    frame = None
    for index in range(1802):
        frame = observer_sample(observer, at=1000 + index / 1000, mono=index / 1000,
                                quote_id=index // 2)
    assert len(frame['_coverage_history']['spot']) == 1800

    corrupt = {**frame, '_coverage_history': {'spot': [{'start': 1, 'end': float('nan'), 'two_sided': True}]}}
    result = rolling_coverage(corrupt, 'spot', now=2801.801)
    assert result['observed_seconds'] == 0
    assert result['unknown_seconds'] == result['elapsed_seconds']


def test_private_coverage_history_is_not_returned_by_existing_liquidity_api():
    from hummingbot.core.mm_liquidity import liquidity_view

    observer = LiquidityObserver()
    observer_sample(observer, at=1000, mono=0)
    frame = observer_sample(observer, at=1002, mono=2)
    row = dict(database_now=1002, heartbeat_age_s=0, owner_uuid='owner',
               mm_session=dict(id='session', generation=0), status={'mm_liquidity': frame})

    response = liquidity_view(row)

    assert '_coverage_history' not in response
