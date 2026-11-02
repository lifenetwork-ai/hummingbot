import json

from scripts.analyze_mm_load_trace import analyze_incidents


def fixture(path, engine, venue=(), *, enabled=True):
    directory = path / 'incidents'
    directory.mkdir()
    (path / 'result.json').write_text(json.dumps(dict(image_id='fixed', mm_activity_diagnostics=enabled, windows={
        'probe': dict(window_start_ns=0, window_end_ns=10_000_000_000, runtime={})})))
    markers = [dict(kind='probe_failure', role='engine', sequence=1, at_ns=2_000_000_000),
               dict(kind='probe_recovered', role='engine', sequence=2, at_ns=4_000_000_000)]
    (directory / 'engine-incidents.jsonl').write_text('\n'.join(json.dumps({**r, 'capturing_role': 'engine'}) for r in markers + engine))
    (directory / 'venue-activity.jsonl').write_text('\n'.join(map(json.dumps, venue)))


def cycle(at, previous, event_id=1):
    return dict(kind='mm_activity', process_id=30, event_id=event_id, instance=1, operation='strategy.tick',
                phase='start', at_ns=at, previous_start_ns=previous, gap_ms=(at - previous) / 1e6)


def snapshot(at, *, aligned=True, complete=True):
    return dict(kind='venue_quote_sample', at_ns=at, request_start_ns=at - 10, response_end_ns=at + 10,
                clock_aligned=aligned, tracking_complete=complete,
                orders=[dict(order_id='one', accepted_ns=0, age_ms=at / 1e6)], terminals=[])


def test_incident_exposes_mm_gap_and_independently_observed_venue_age_not_quote_downtime(tmp_path):
    tick = cycle(4_100_000_000, 1_000_000_000)
    submit = dict(kind='mm_activity', process_id=30, event_id=2, instance=2, operation='order.submit', phase='end',
                  start_ns=1_900_000_000, at_ns=4_100_000_000, duration_ms=2200, outcome='returned')
    fixture(tmp_path, [tick, tick, submit], [snapshot(1_500_000_000), snapshot(3_000_000_000), snapshot(4_500_000_000)])
    result = analyze_incidents(tmp_path, 'probe')
    data = result['episodes'][0]['trading_activity']
    assert data['status'] == 'observed' and data['max_strategy_tick_start_gap_ms'] == 3100
    assert data['max_observed_live_order_age_ms'] == 4500
    assert data['order_call_returns'][0]['duration_ms'] == 2200
    assert len(data['engine_events']) == 2
    assert data['has_venue_sample_before_failure'] and data['has_venue_sample_after_recovery']
    assert data['max_venue_sample_gap_ms'] == 1500
    assert data['continuous_quoting_proven'] is False
    assert 'not quote downtime' in data['interpretation']


def test_legacy_run_is_not_instrumented_instead_of_zero_stalls(tmp_path):
    fixture(tmp_path, [], enabled=False)
    data = analyze_incidents(tmp_path, 'probe')['episodes'][0]['trading_activity']
    assert data['status'] == 'not_instrumented'
    assert data['max_strategy_tick_start_gap_ms'] is None
    assert data['max_observed_live_order_age_ms'] is None


def test_invalid_clock_and_tracking_loss_cannot_claim_complete_observation(tmp_path):
    fixture(tmp_path, [cycle(4_100_000_000, 1_000_000_000)],
            [snapshot(3_000_000_000, aligned=False), snapshot(4_500_000_000, complete=False)])
    data = analyze_incidents(tmp_path, 'probe')['episodes'][0]['trading_activity']
    assert data['invalid_venue_clock_samples'] == 1
    assert data['venue_tracking_incomplete'] is True
    assert data['continuous_quoting_proven'] is False
    assert data['status'] == 'partial'


def test_incident_activity_lists_and_deduplication_are_bounded(tmp_path):
    fixture(tmp_path, [cycle(2_100_000_000 + i, 1_000_000_000, event_id=i) for i in range(400)],
            [snapshot(2_100_000_000 + i) for i in range(100)])
    data = analyze_incidents(tmp_path, 'probe')['episodes'][0]['trading_activity']
    assert len(data['engine_events']) <= 256 and len(data['venue_samples']) <= 32
    assert data['evidence_overflow'] and data['status'] == 'partial'


def test_one_event_overlapping_two_incidents_keeps_independent_intersection_flags(tmp_path):
    fixture(tmp_path, [cycle(4_100_000_000, 1_000_000_000)])
    path = tmp_path / 'incidents/engine-incidents.jsonl'
    with path.open('a') as out:
        out.write('\n' + json.dumps(dict(kind='probe_failure', role='engine', sequence=3, at_ns=5_000_000_000)))
        out.write('\n' + json.dumps(dict(kind='probe_recovered', role='engine', sequence=4, at_ns=6_000_000_000)))
    episodes = {r['failure_ns']: r for r in analyze_incidents(tmp_path, 'probe')['episodes']}
    assert episodes[2_000_000_000]['trading_activity']['engine_events'][0]['intersects_probe_transition'] is True
    assert episodes[5_000_000_000]['trading_activity']['engine_events'][0]['intersects_probe_transition'] is False
