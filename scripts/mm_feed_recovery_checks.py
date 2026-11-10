"""Packaged quiet-WS recovery and real stale-input withdrawal, on dummy keys."""
import json
import time


def scenario(harness):
    from scripts.test_e2e_offline import docker, http, poll

    print('Offline E2E: quiet ETH WS / fresh REST, stale REST, outage and WS return', flush=True)
    harness.env.update(ADMIN_API_ENABLED='true', ADMIN_BASIC_AUTH_ENABLED='true',
                       ADMIN_API_USERNAME='operator', ADMIN_API_PASSWORD='offline-admin')
    bot, port = harness.start('feed-recovery')
    harness.mm_command(port, 'start', market_mode='both', selection='fixed', profile_id='follow_market', duration_seconds=600)

    def working():
        return [row for row in harness.ledger()['orders'] if row['state'] in ('live', 'partially_filled')]

    poll(lambda: len(working()) == 8, description='both markets acknowledge two-sided quotes')
    session = harness.state()['mm_session']
    started_at = json.loads(docker('inspect', bot).stdout)[0]['State']['StartedAt']
    boundary = len(harness.ledger()['requests'])
    harness.control(stale_instruments=['ETH-USDT'])
    samples = []
    started = time.monotonic()
    while time.monotonic() - started < 13:
        state = harness.state()
        samples.append(dict(seconds=time.monotonic() - started, phase=state['status']['phase'], quotes=len(working())))
        assert state['status']['phase'] == 'running' and len(working()) == 8
        time.sleep(.5)
    reads = [row['params'].get('instId') for row in harness.ledger()['requests'][boundary:]
             if row['path'] == '/api/v5/market/books']
    assert 1 <= reads.count('ETH-USDT') <= 4, reads
    assert 'BTC-USDT' not in reads, reads
    # Both sources really stale: unchanged freshness guards must withdraw.
    harness.control(rest_stale_instruments=['ETH-USDT'])
    poll(lambda: harness.state()['status']['phase'] == 'waiting_for_mm_readiness', timeout=25,
         description='old REST timestamps cannot disguise stale WS')
    poll(lambda: not working(), description='stale reference cancels all resting quotes')
    assert http(port, '/live')[0] == 200 and http(port, '/ready')[0] == 503
    boundary = len(harness.ledger()['accepted'])
    request_boundary = len(harness.ledger()['requests'])
    harness.control(rest_failed_instruments=['ETH-USDT'])
    poll(lambda: any(row['path'] == '/api/v5/market/books' and row['params'].get('instId') == 'ETH-USDT'
                     for row in harness.ledger()['requests'][request_boundary:]), timeout=35,
         description='failed recovery read is actually exercised')
    assert not working() and len(harness.ledger()['accepted']) == boundary
    assert not harness.state()['recovery_state'].get('halt')
    # WS return wins even when REST is still failing.
    harness.control(stale_instruments=[])
    harness.wait_running(port)
    poll(lambda: len(working()) == 8, description='fresh WS resumes admitted quotes')
    restored = harness.state()['mm_session']
    assert all(restored[field] == session[field] for field in ('id', 'anchors', 'expires_at'))
    assert json.loads(docker('inspect', bot).stdout)[0]['State']['StartedAt'] == started_at
    harness.mm_command(port, 'stop')
    assert not working() and not harness.state()['recovery_state'].get('halt')
    harness.control(rest_failed_instruments=[], rest_stale_instruments=[])
    (harness.report / 'feed-recovery-observations.json').write_text(json.dumps(samples, indent=2))
    harness.metrics['feed_recovery'] = dict(quiet_seconds=13, samples=len(samples), eth_snapshot_reads=reads.count('ETH-USDT'),
                                            healthy_btc_snapshot_reads=0, stale_rest_withdrawal=True,
                                            rest_failure_stays_blocked=True, ws_recovered_same_process=True,
                                            session_and_deadline_preserved=True, stopped=True, simulated=True)
