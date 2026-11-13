"""Physical HTTP faults: positive evidence resumes; absence stays inconclusive."""
import time


def scoped_positive_scenario(harness):
    import yaml

    path = harness.assets / 'life_mm_profiles.yaml'
    original = path.read_bytes()
    profiles = yaml.safe_load(original)
    profiles['spot_outflow'] = dict(window_seconds=300, max_net_outflow_quote='100', fee_allowance_rate='.01')
    path.write_text(yaml.safe_dump(profiles))
    harness.env['MM_SCOPED_RECOVERY_ENABLED'] = 'true'
    try:
        positive_scenario(harness)
    finally:
        path.write_bytes(original)


def scoped_scenario(harness):
    """Actual 50013/51603 transport, funded SELL, clean stop and cold restore."""
    import yaml

    from scripts.test_e2e_offline import http, poll
    print('Offline E2E: uncertain BUY retains its reservation while funded SELL survives cold restart', flush=True)
    path = harness.assets / 'life_mm_profiles.yaml'
    original = path.read_bytes()
    profiles = yaml.safe_load(original)
    profiles['spot_outflow'] = dict(window_seconds=300, max_net_outflow_quote='100', fee_allowance_rate='.01')
    path.write_text(yaml.safe_dump(profiles))
    harness.env.update(MM_SCOPED_RECOVERY_ENABLED='true', ADMIN_API_ENABLED='true', ADMIN_BASIC_AUTH_ENABLED='true',
                       ADMIN_API_USERNAME='operator', ADMIN_API_PASSWORD='offline-admin')
    harness.control(submission_busy_side='buy')
    first, port = harness.start('scoped-first', cpus=2, memory_mib=1024)
    harness.mm_command(port, 'start', wait=False, market_mode='spot', selection='fixed',
                       profile_id='follow_market', duration_seconds=600)

    def degraded(port):
        state, ledger = harness.state(), harness.ledger()
        recovery = state['recovery_state'].get('automatic_recovery', {})
        working = [row for row in ledger['orders'] if row['side'] == 'sell' and row['state'] == 'live']
        return (state, ledger) if recovery.get('isolated') and working and not state['recovery_state'].get('halt') else None

    state, ledger = poll(lambda: degraded(port), timeout=60, description='funded native SELL working during BUY uncertainty')
    identity = state['owner_uuid'], state['owner_generation']
    unknown = [row for row in ledger['requests'] if row.get('injected_submission_busy')]
    assert unknown and all(row.get('order', {}).get('side') == 'buy' for row in unknown)
    unknown_ids = {row['order']['clOrdId'] for row in unknown}
    assert len(unknown_ids) == len(unknown)
    assert http(port, '/startup')[0] == http(port, '/live')[0] == 200
    assert http(port, '/ready')[0] == 503
    assert harness.admin(port, '/status')[1]['automatic_recovery']['admission_mode'] == 'funded_spot_sell'
    harness.mm_command(port, 'stop')
    assert not harness.state()['recovery_state'].get('halt')
    assert int(harness.sql('SELECT count(*) FROM orders WHERE creation_pending')) == len(unknown_ids)
    harness.terminate(first)
    second, second_port = harness.start('scoped-cold', cpus=2, memory_mib=1024)
    harness.mm_command(second_port, 'start', wait=False, market_mode='spot', selection='fixed',
                       profile_id='follow_market', duration_seconds=600)
    resumed, _ = poll(lambda: degraded(second_port), timeout=60, description='cold successor retains BUY and admits funded SELL')
    assert (resumed['owner_uuid'], resumed['owner_generation']) != identity
    for _ in range(3):
        assert http(second_port, '/startup')[0] == http(second_port, '/live')[0] == 200
        assert http(second_port, '/ready')[0] == 503
        assert not harness.state()['recovery_state'].get('halt')
        time.sleep(.5)
    requests = harness.ledger()['requests']
    assert len([row for row in requests if row.get('injected_submission_busy')]) == len(unknown_ids)
    assert any(row['method'] == 'GET' and row['path'] == '/api/v5/trade/order' for row in requests)
    for unknown_id in unknown_ids:
        assert sum(row['method'] == 'POST' and row['path'] == '/api/v5/trade/order'
                   and row.get('order', {}).get('clOrdId') == unknown_id for row in requests) == 1
    assert not harness.ledger()['unknown']
    # A deploy/SIGTERM while desired_state is running must retain the same
    # unresolved obligation without requiring another operator Start.
    session_id = harness.state()['mm_session']['id']
    harness.terminate(second)
    third, third_port = harness.start('scoped-handoff', cpus=2, memory_mib=1024)
    handoff, _ = poll(lambda: degraded(third_port), timeout=60, description='running owner handoff restores quarantine without a new Start')
    assert handoff['mm_session']['id'] == session_id
    assert len([row for row in harness.ledger()['requests'] if row.get('injected_submission_busy')]) == len(unknown_ids)
    harness.mm_command(third_port, 'stop')
    assert not harness.state()['recovery_state'].get('halt')
    harness.terminate(third)
    harness.metrics['scoped_uncertainty'] = dict(cpus=2, memory_mib=1024, unresolved_count=len(unknown_ids),
                                                 funded_sell=True, cold_restore=True, running_handoff=True, no_duplicate_post=True,
                                                 clean_stop=True, simulated=True)
    path.write_bytes(original)


def scenario(harness):
    from scripts.test_e2e_offline import http, poll
    print('Offline E2E: busy submission retains uncertainty without a manual HALT', flush=True)
    harness.control(submission_busy=True)
    harness.env.update(ADMIN_API_ENABLED='true', ADMIN_BASIC_AUTH_ENABLED='true',
                       ADMIN_API_USERNAME='operator', ADMIN_API_PASSWORD='offline-admin')
    _, port = harness.start('submission-uncertainty')
    harness.mm_command(port, 'start', wait=False, market_mode='spot', selection='fixed',
                       profile_id='follow_market', duration_seconds=600)
    identity = None

    def paused_owner():
        nonlocal identity
        state = harness.state()
        if (not state['recovery_state'].get('halt')
                and state['recovery_state'].get('automatic_recovery', {}).get('phase') == 'reconciling'
                and state.get('owner_uuid')):
            current = state['owner_uuid'], state['owner_generation']
            if identity is None:
                identity = current
            assert current == identity, 'The reconciling owner changed before order proof was observed'
            # Publishing the pause precedes the asynchronous worker's HTTP GET.
            # Require both observations inside the original single 60s bound.
            ledger = harness.ledger()
            if any(r['path'] == '/api/v5/trade/order' and r['method'] == 'GET' for r in ledger['requests']):
                return state, ledger
        return None

    state, ledger = poll(paused_owner, timeout=60, description='uncertain owner remains reconciling and attempts order GET')
    requests = ledger['requests']
    attempts = [r for r in requests if r.get('injected_submission_busy')]
    assert attempts and not harness.ledger()['accepted'] and not harness.ledger()['fills']
    assert any(r['path'] == '/api/v5/trade/order' and r['method'] == 'GET' for r in requests)
    assert int(harness.sql('SELECT count(*) FROM orders WHERE creation_pending')) >= 1
    # Enough observer turns to expose the old persistent restart sequence.
    for _ in range(4):
        assert http(port, '/startup')[0] == http(port, '/live')[0] == 200
        assert http(port, '/ready')[0] == 503
        status = harness.admin(port, '/status')[1]
        assert not status['recovery']['halted']
        assert status['automatic_recovery']['phase'] == 'reconciling'
        current = harness.state()
        assert (current['owner_uuid'], current['owner_generation']) == identity
        time.sleep(.5)
    assert len([r for r in harness.ledger()['requests'] if r.get('injected_submission_busy')]) == len(attempts)
    harness.mm_command(port, 'stop')
    state = harness.state()
    assert state['desired_state'] == 'stopped'
    assert int(harness.sql('SELECT count(*) FROM orders WHERE creation_pending')) >= 1
    harness.metrics['submission_uncertainty'] = dict(
        busy_http_posts=len(attempts), accepted=0, fills=0, no_automatic_resubmission=True,
        stable_reconciling_owner=True, stop_acknowledged=True, pending_intents_preserved=True, simulated=True)


def positive_scenario(harness):
    from scripts.test_e2e_offline import poll
    print('Offline E2E: accepted physical order loses ACK and WS; GET evidence restores quotes', flush=True)
    harness.control(accepted_response_lost=1)
    harness.env.update(ADMIN_API_ENABLED='true', ADMIN_BASIC_AUTH_ENABLED='true',
                       ADMIN_API_USERNAME='operator', ADMIN_API_PASSWORD='offline-admin')
    _, port = harness.start('automatic-recovery')
    harness.mm_command(port, 'start', wait=False, market_mode='spot', selection='fixed',
                       profile_id='follow_market', duration_seconds=600)
    poll(lambda: any(r.get('injected_accepted_response_lost') for r in harness.ledger()['requests']),
         description='one real order POST is accepted without its ACK or private event')
    started = time.monotonic()
    first = harness.state()
    identity = first['owner_uuid'], first['owner_generation']

    def resumed():
        state = harness.state()
        ledger = harness.ledger()
        return state if (state['status'].get('running') and not state['recovery_state'].get('halt')
                         and state['recovery_state'].get('automatic_recovery', {}).get('phase') == 'idle'
                         and all(any(o['side'] == side and o['state'] == 'live' for o in ledger['orders'])
                                 for side in ('buy', 'sell'))) else None

    state = poll(resumed, timeout=30, description='verified order restores both quote sides without restarting the owner')
    assert state['recovery_state']['automatic_recovery']['cold_recovery_supported']
    assert harness.sql('SELECT count(*) FROM runtime_checkpoints') == '1'
    assert (state['owner_uuid'], state['owner_generation']) == identity
    ledger = harness.ledger()
    injected = [r for r in ledger['requests'] if r.get('injected_accepted_response_lost')]
    assert len(injected) == 1
    assert len({o['clOrdId'] for o in ledger['accepted']}) == len(ledger['accepted'])
    assert not ledger['unknown']
    assert harness.sql("SELECT count(*) FROM runtime_journal WHERE kind='intent'") != '0'
    assert harness.sql("SELECT count(*) FROM submission_attempts WHERE state='uncertain'") == '0'
    elapsed = time.monotonic() - started
    harness.mm_command(port, 'stop')
    assert not any(o['state'] in ('live', 'partially_filled') for o in harness.ledger()['orders'])
    assert not harness.state()['recovery_state'].get('halt')
    harness.metrics['automatic_recovery'] = dict(accepted_response_lost=1, restore_seconds=elapsed,
                                                 duplicate_submissions=0, owner_preserved=True,
                                                 quotes_restored=True, stopped=True, simulated=True)
