"""Native startup/Random latency qualification on the isolated exchange fixture."""
import json

import yaml


def scenario(harness):
    from scripts.test_e2e_offline import poll

    print('Offline E2E: bounded slow account startup survives Random rollover', flush=True)
    path = harness.assets / 'life_mm_profiles.yaml'
    cfg = yaml.safe_load(path.read_text())
    cfg['random'] = dict(profile_ids=['sideways'], step_duration_seconds=15)
    path.write_text(yaml.safe_dump(cfg))
    harness.env.update(ADMIN_API_ENABLED='true', ADMIN_BASIC_AUTH_ENABLED='true',
                       ADMIN_API_USERNAME='operator', ADMIN_API_PASSWORD='offline-admin')
    for field, value in (('API_KEY', 'offline-key'), ('SECRET_KEY', 'offline-secret'), ('PASSPHRASE', 'offline-pass')):
        harness.env['HBOT_OKX_PERPETUAL_' + field] = value
    # Native strategy/controller setup serialize two account writes. Each HTTP
    # response remains below its real 5s mutation deadline; combined waiting
    # exceeds the ordinary controller watchdog without simulating a lost ACK.
    delay = 4.0
    harness.control(account_mutation_delay_seconds=delay)
    _, port = harness.start('startup-latency')
    harness.mm_command(port, 'start', wait=False, market_mode='perpetual', selection='random', duration_mode='until_stopped')
    first = harness.state()
    owner = first['owner_uuid'], first['owner_generation']

    def quoting(step):
        state = harness.state()
        if state['recovery_state'].get('halt'):
            harness.report.joinpath('startup-latency-halt-private.json').write_text(json.dumps(state, indent=2))
            raise AssertionError('Slow but bounded startup was latched as a HALT')
        assert (state['owner_uuid'], state['owner_generation']) == owner
        session = state.get('mm_session') or {}
        orders = [r for r in harness.ledger()['orders'] if r['state'] in ('live', 'partially_filled')]
        if (session.get('step_index', -1) >= step and session.get('status') == 'running'
                and {r['side'] for r in orders} == {'buy', 'sell'}):
            assert session['expires_at'] is None
            return session
        return None

    initial = poll(lambda: quoting(0), timeout=60, description='initial slow account ACK completes without HALT')
    advanced = poll(lambda: quoting(1), timeout=60, description='Random rollover retains bounded startup waits and restores both quotes')
    assert advanced['id'] == initial['id']
    ledger = harness.ledger()
    delayed = [r for r in ledger['requests'] if r.get('injected_account_delay_s')]
    assert len(delayed) >= 2
    posts = [r for r in ledger['requests'] if r['path'] == '/api/v5/trade/order' and r['method'] == 'POST']
    assert posts
    first_quote_at = min(int(r['at_ms']) for r in posts)
    initial_writes = [r for r in delayed if int(r['at_ms']) < first_quote_at]
    assert len(initial_writes) == 2
    assert first_quote_at >= max(int(r['at_ms']) for r in initial_writes) + int(delay * 1000)
    initial_wait = (first_quote_at - int(initial_writes[0]['at_ms'])) / 1000
    assert initial_wait > 5
    harness.mm_command(port, 'stop')
    final = harness.state()
    assert not final['recovery_state'].get('halt')
    assert not any(r['state'] in ('live', 'partially_filled') for r in harness.ledger()['orders'])
    assert not ledger['fills'] and not ledger['unknown']
    harness.metrics['startup_latency'] = dict(
        account_response_delay_s=delay, observed_initial_account_wait_s=initial_wait,
        ordinary_watchdog_s=5, startup_operation_timeout_s=30,
        random_steps_observed=advanced['step_index'], continuous_session=True,
        owner_preserved=True, quotes_restored=True, orders_waited_for_account_ack=True,
        stopped_without_halt=True, simulated=True)
