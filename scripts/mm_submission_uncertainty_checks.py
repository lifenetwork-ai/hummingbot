"""Real HTTP429 -> absent native order -> one replacement -> stable HALT."""
import time


def scenario(harness):
    from scripts.test_e2e_offline import http, poll
    print('Offline E2E: busy submission retains uncertainty and a usable halted owner', flush=True)
    harness.control(submission_busy=True)
    harness.env.update(ADMIN_API_ENABLED='true', ADMIN_BASIC_AUTH_ENABLED='true',
                       ADMIN_API_USERNAME='operator', ADMIN_API_PASSWORD='offline-admin')
    _, port = harness.start('submission-uncertainty')
    harness.mm_command(port, 'start', wait=False, market_mode='spot', selection='fixed',
                       profile_id='follow_market', duration_seconds=600)

    def halted_owner():
        state = harness.state()
        return state if (state['recovery_state'].get('halt') and state['status'].get('phase') == 'halted'
                         and state.get('owner_uuid')) else None

    state = poll(halted_owner, timeout=60, description='uncertain request hands over to a stable halted owner')
    identity = state['owner_uuid'], state['owner_generation']
    requests = harness.ledger()['requests']
    attempts = [r for r in requests if r.get('injected_submission_busy')]
    assert attempts and not harness.ledger()['accepted'] and not harness.ledger()['fills']
    assert any(r['path'] == '/api/v5/trade/order' and r['method'] == 'GET' for r in requests)
    assert int(harness.sql('SELECT count(*) FROM orders WHERE creation_pending')) >= 1
    # Enough observer turns to expose the old persistent restart sequence.
    for _ in range(4):
        assert http(port, '/startup')[0] == http(port, '/live')[0] == 200
        assert http(port, '/ready')[0] == 503
        assert harness.admin(port, '/status')[1]['recovery']['halted']
        current = harness.state()
        assert (current['owner_uuid'], current['owner_generation']) == identity
        time.sleep(.5)
    assert len([r for r in harness.ledger()['requests'] if r.get('injected_submission_busy')]) == len(attempts)
    harness.mm_command(port, 'stop')
    state = harness.state()
    assert state['desired_state'] == 'stopped' and state['recovery_state']['halt']
    assert int(harness.sql('SELECT count(*) FROM orders WHERE creation_pending')) >= 1
    harness.metrics['submission_uncertainty'] = dict(
        busy_http_posts=len(attempts), accepted=0, fills=0, no_automatic_resubmission=True,
        stable_halted_owner=True, stop_acknowledged=True, pending_intents_preserved=True, simulated=True)
