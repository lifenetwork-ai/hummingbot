"""Native managed-key lifecycle checks on the shipping image, never real OKX."""
import json
from decimal import Decimal
from test.e2e.offline.exchange import FIXTURE_KEYS

from scripts.mm_managed_environment import (
    LOCAL_KEK,
    connection_status,
    credential_digest,
    financial_digest,
    private_requests,
)
from scripts.test_e2e_offline import docker, http, poll

SCENARIOS = ('managed-close-ack-crash', 'managed-close-fill-crash', 'managed-rotation', 'managed-cleanup-repair')


def initialize(harness):
    harness.env.update(EXCHANGE_CREDENTIALS_SOURCE='postgres_kms', KMS_PROVIDER='local',
                       KMS_LOCAL_SECRET_KEY=LOCAL_KEK, KMS_LOCAL_KEY_ID='local-v1', OKX_TRADING_ENV='demo',
                       OKX_SITE='www', ADMIN_API_ENABLED='true', ADMIN_BASIC_AUTH_ENABLED='true',
                       ADMIN_API_USERNAME='operator', ADMIN_API_PASSWORD='offline-admin', FUNDING_SYNC_INTERVAL_SECONDS='2')
    bot, port = harness.start('managed-lifecycle')
    for connector in ('okx', 'okx_perpetual'):
        save(harness, port, connector, 'a', key='initial-' + connector)
    poll(lambda: connection_status(harness, port, environment='demo', connected=True), description='initial managed connections')
    return bot, port


def operation(harness, port, identity):
    code, row = harness.admin(port, '/credentials/operations/' + identity)
    assert code == 200
    return row


def completed(harness, port, identity):
    def check():
        row = operation(harness, port, identity)
        assert row['status'] != 'failed', (identity, row['error'])
        assert not row['result']['operator_action_required'], (identity, row['result'].get('reason_code'))
        return row if row['status'] == 'succeeded' else None
    return poll(check, timeout=180, description='same native credential operation completes')


def slot(harness, port, connector):
    code, body = harness.admin(port, '/credentials/okx')
    assert code == 200
    return next(row for row in body['slots'] if row['connector'] == connector)


def save(harness, port, connector, generation, *, key):
    poll(lambda: harness.state()['owner_uuid'] and harness.state()['control_version'] == harness.state()['acknowledged_version'],
         description='stopped acknowledged control before Set')
    data = dict(expected_version=slot(harness, port, connector)['version'],
                expected_control_version=harness.state()['control_version'], credentials=FIXTURE_KEYS[generation])
    path = '/credentials/okx/' + connector
    code, receipt = harness.admin(port, path, data, key=key)
    assert code == 202, (code, receipt.get('code'))
    code, replay = harness.admin(port, path, data, key=key)
    assert code == 202 and replay['id'] == receipt['id']
    completed(harness, port, receipt['id'])
    return receipt


def seed_exposure(harness, port):
    harness.mm_command(port, 'start', market_mode='both', selection='fixed', profile_id='sideways', duration_seconds=1800)
    harness.wait_running(port)
    for instrument, side in (('LIFE-USDT', 'buy'), ('LIFE-USDT-SWAP', 'buy'), ('LIFE-USDT-SWAP', 'sell')):
        order = poll(lambda: next((row for row in harness.ledger()['orders']
                                  if row['instId'] == instrument and row['side'] == side and row['state'] == 'live'), None),
                     description='native working order for held exposure')
        harness.control(fill=dict(clOrdId=order['clOrdId'], amount='1', fee='0.00001'), duplicate=True)
    poll(lambda: harness.sql('SELECT count(*) FROM trade_fills') == '3', description='three exact deduplicated fills')
    harness.mm_command(port, 'stop')
    assert harness.sql("SELECT count(*) FROM positions WHERE connector_name='okx_perpetual' AND amount > 0") == '2'


def spot_digest(harness):
    return harness.sql("SELECT md5(COALESCE(jsonb_agg(to_jsonb(t) ORDER BY id),'[]'::jsonb)::text) "
                       "FROM positions t WHERE connector_name='okx'")


def position_rows(harness):
    return json.loads(harness.sql("SELECT COALESCE(json_agg(t ORDER BY id),'[]') FROM positions t"))


def safe_logs(harness, bot):
    result = docker('logs', bot)
    text = result.stdout + result.stderr
    for values in FIXTURE_KEYS.values():
        assert all(value not in text for value in values.values()), 'Credential canary leaked into native service logs'


def disconnect(harness, port):
    data = dict(expected_version=slot(harness, port, 'okx_perpetual')['version'],
                expected_control_version=harness.state()['control_version'])
    path = '/credentials/okx/okx_perpetual/revoke'
    code, root = harness.admin(port, path, data, key='disconnect-perpetual')
    assert code == 202, (code, root.get('code'))
    code, replay = harness.admin(port, path, data, key='disconnect-perpetual')
    assert code == 202 and replay['id'] == root['id']
    return root


def audit(harness, port, root, *, children=()):
    code, body = harness.admin(port, '/audit?operation_id=' + root + '&limit=100')
    assert code == 200
    rows = body['items']
    assert rows and all(row['actor_id'] == 'basic:operator' and row['request_id'] for row in rows)
    for outcome in ('accepted', 'succeeded'):
        assert sum(row['action'] == 'credentials_disconnect' and row['outcome'] == outcome for row in rows) == 1
    for child, action in children:
        for outcome in ('accepted', 'succeeded'):
            assert sum(row['action'] == action and row['outcome'] == outcome
                       and row['target_id'] == child for row in rows) == 1
    text = json.dumps(body)
    for values in FIXTURE_KEYS.values():
        assert all(value not in text for value in values.values())
    return dict(events=len(rows), actor='basic:operator', root=root, children=list(children))


def verify_cleanup(harness, port, root, spot):
    receipt = completed(harness, port, root['id'])
    assert receipt['result']['stage'] == 'removed'
    assert spot_digest(harness) == spot
    assert harness.sql("SELECT count(*) FROM positions WHERE connector_name='okx_perpetual' AND amount <> 0") == '0'
    assert harness.sql("SELECT count(*) FROM exchange_credentials WHERE connector='okx_perpetual' AND envelope IS NULL AND state='revoked'") == '1'
    assert harness.sql("SELECT count(*) FROM exchange_credentials WHERE connector='okx' AND envelope IS NOT NULL AND state='active'") == '1'
    closes = [row for row in harness.ledger()['accepted'] if row['ordType'] == 'market']
    assert len(closes) == 2 and {row['posSide'] for row in closes} == {'long', 'short'}
    sends = [row['order'] for row in harness.ledger()['requests']
             if row.get('order', {}).get('ordType') == 'market']
    assert len(sends) == 2 and {row['clOrdId'] for row in sends} == {row['clOrdId'] for row in closes}
    assert {(row['posSide'], row['side'], Decimal(row['sz'])) for row in sends} == {('long', 'sell', Decimal('1')), ('short', 'buy', Decimal('1'))}
    assert all(Decimal(row['pos']) == 0 for row in harness.ledger()['positions'])
    assert not [row for row in harness.ledger()['orders'] if row['state'] in ('live', 'partially_filled')]
    assert harness.sql('SELECT count(*) FROM trade_fills') == '5'
    assert not harness.ledger()['unknown']
    assert harness.state()['desired_state'] == 'stopped'
    # The selected connector is intentionally gone; do not demand a new private
    # API observation from erased credentials. Compare venue truth and SQL directly.
    from scripts.mm_load_ledger import ledger_violations
    rows = json.loads(harness.sql("SELECT COALESCE(json_agg(t),'[]') FROM (SELECT market,order_id,exchange_trade_id,"
                                  "amount::text AS amount,price::text AS price,trade_fee,trade_fee_in_quote::text AS trade_fee_in_quote "
                                  "FROM trade_fills) t"))
    assert not ledger_violations(harness.ledger(), rows)
    return dict(unique_exchange_fills=len(harness.ledger()['fills']), unique_recorder_fills=len(rows),
                fees_matched=True, positions_flat=True, physical_close_attempts=len(sends))


def crash_scenario(harness, boundary):
    print('Offline E2E: native cleanup crash at ' + boundary, flush=True)
    bot, port = initialize(harness)
    seed_exposure(harness, port)
    spot = spot_digest(harness)
    harness.control(cleanup_market_closes=True, cleanup_barrier=boundary)
    before_fills = len(harness.ledger()['fills'])
    generation = harness.state()['owner_generation']
    root = disconnect(harness, port)
    poll(lambda: harness.ledger()['cleanup_barrier_hits'], description='independent exchange reaches cleanup barrier')
    observed = harness.ledger()
    closes = [row for row in observed['accepted'] if row['ordType'] == 'market']
    assert len(closes) == 1
    if boundary == 'accepted':
        assert len(observed['fills']) == before_fills and observed['cleanup_barrier_hits'][-1]['stage'] == 'accepted'
    else:
        poll(lambda: harness.sql('SELECT count(*) FROM trade_fills') == '4', description='close fill committed before cleanup verification')
        assert len(observed['fills']) == before_fills + 1
    assert harness.sql("SELECT count(*) FROM exchange_credentials WHERE connector='okx_perpetual' AND envelope IS NOT NULL AND state='revoking'") == '1'
    assert operation(harness, port, root['id'])['status'] != 'succeeded'
    snapshot = dict(boundary=boundary, operation_id=root['id'], generation_before=generation,
                    close_id=closes[0]['clOrdId'], exchange_fills_before_kill=len(observed['fills']), ciphertext_retained=True)
    (harness.report / 'crash-boundary.json').write_text(json.dumps(snapshot, indent=2))
    docker('kill', '--signal', 'KILL', bot)
    assert json.loads(docker('inspect', bot).stdout)[0]['State']['ExitCode'] == 137
    # The independent venue can execute an already accepted order while the bot
    # is dead. Recovery must read it back instead of creating another close.
    harness.control(cleanup_barrier='none')
    poll(lambda: len(harness.ledger()['fills']) == 4, description='one accepted close fills independently of crashed bot')
    docker('start', bot)
    poll(lambda: http(port, '/startup')[0] == 200, timeout=200, description='crashed native service restarts')
    poll(lambda: harness.state()['owner_generation'] > generation, description='fresh cleanup owner')
    ledger = verify_cleanup(harness, port, root, spot)
    recovery_halt = bool(harness.state()['recovery_state'].get('halt'))
    safe_logs(harness, bot)
    history = financial_digest(harness)
    harness.terminate(bot)
    resumed, port = harness.start('removed-restart', auto_migrate=False)
    assert financial_digest(harness) == history
    assert slot(harness, port, 'okx_perpetual')['status'] == 'disconnected'
    assert len([row for row in harness.ledger()['accepted'] if row['ordType'] == 'market']) == 2
    evidence = audit(harness, port, root['id'])
    harness.terminate(resumed)
    harness.metrics['managed_cleanup_crash'] = dict(**snapshot, generation_after=harness.state()['owner_generation'],
                                                    no_duplicate_close=True, no_reversal=True, spot_preserved=True,
                                                    recovery_halt_retained=recovery_halt,
                                                    ledger=ledger, audit=evidence, actual_okx=False, local_kms=True)


def rotation_scenario(harness):
    print('Offline E2E: native same-account rotation A to B and cold restart', flush=True)
    bot, port = initialize(harness)
    seed_exposure(harness, port)
    history = financial_digest(harness)
    positions_before = position_rows(harness)
    harness.control(credential_generations=['a', 'b'])
    receipts = [save(harness, port, connector, 'b', key='rotate-' + connector) for connector in ('okx', 'okx_perpetual')]
    assert financial_digest(harness) == history
    ciphertext = credential_digest(harness)
    harness.control(credential_generations=['b'], drop_ws=True)
    # The old key remains a correctly shaped candidate but is expired at OKX.
    data = dict(expected_version=2, expected_control_version=harness.state()['control_version'], credentials=FIXTURE_KEYS['a'])
    code, rejected = harness.admin(port, '/credentials/okx/okx', data, key='reject-expired-rotation')
    assert code == 422 and rejected['code'] == 'exchange_credentials_rejected'
    assert credential_digest(harness) == ciphertext and financial_digest(harness) == history
    harness.terminate(bot)
    reads = len(harness.ledger()['requests'])
    subscriptions = len(harness.ledger()['subscriptions'])
    bot, port = harness.start('rotated-cold-restart', auto_migrate=False)
    poll(lambda: connection_status(harness, port, environment='demo', connected=True), description='both B bundles cold load')
    assert financial_digest(harness) == history and credential_digest(harness) == ciphertext
    assert harness.state()['desired_state'] == 'stopped'
    harness.mm_command(port, 'start', market_mode='both', selection='fixed', profile_id='sideways', duration_seconds=1800)
    harness.wait_running(port)
    poll(lambda: sum(row['state'] == 'live' for row in harness.ledger()['orders']) == 8
         and harness.sql('SELECT count(*) FROM orders WHERE creation_pending') == '0',
         description='B runtime and orders settle before observational reconciliation')
    code, check = harness.admin(port, '/reconciliation/check', {'market': 'all'}, key='rotated-reconciliation')
    assert code == 202

    def reconciled():
        code, row = harness.admin(port, '/reconciliation/' + check['id'])
        assert code == 200 and row['status'] != 'failed', row.get('error')
        return row if row['status'] == 'succeeded' else None
    report = poll(reconciled, timeout=60, description='same-account B reconciliation finishes')
    assert report['report']['state'] == 'matched', report['report']

    def new_reads():
        return private_requests(dict(requests=harness.ledger()['requests'][reads:]))
    poll(lambda: any(row['path'] in ('/api/v5/account/bills', '/api/v5/account/bills-archive') for row in new_reads()),
         timeout=40, description='funding uses rotated B key')
    poll(lambda: {row['channel'] for row in harness.ledger()['subscriptions'][subscriptions:]
                  if row.get('credential_generation') == 'b'} >= {'orders', 'positions', 'account'},
         description='native private WS reconnects with B')
    observed = new_reads()
    assert observed and all(row['credential_generation'] == 'b' and row['authorized'] for row in observed)
    assert any(row['method'] == 'POST' and row['path'] == '/api/v5/trade/order' for row in observed)
    harness.mm_command(port, 'stop')
    # Existing order statuses can change on Start/Stop; held exposure and original
    # fills/fees cannot. No new fill stimulus is injected after rotation.
    assert financial_digest(harness)['trade_fills'] == history['trade_fills']
    positions_after = position_rows(harness)
    (harness.report / 'rotation-positions.json').write_text(json.dumps(dict(before=positions_before, after=positions_after), indent=2))
    # The existing Stop checkpoint refreshes observation timestamp. Every other
    # field, including row identity, amount, entry, PnL, volume and fees, must agree.
    assert [{key: value for key, value in row.items() if key != 'timestamp'} for row in positions_after] == [
        {key: value for key, value in row.items() if key != 'timestamp'} for row in positions_before]
    audit_events = []
    for receipt in receipts:
        code, body = harness.admin(port, '/audit?operation_id=' + receipt['id'] + '&limit=100')
        assert code == 200
        rows = [row for row in body['items'] if row['action'] == 'credentials_set']
        assert sorted(row['outcome'] for row in rows) == ['accepted', 'succeeded']
        assert all(row['actor_id'] == 'basic:operator' and row['request_id'] for row in rows)
        audit_events.append(len(rows))
    from scripts.test_mm_load import verify_ledger
    ledger = verify_ledger(harness, port, require_running=False)
    safe_logs(harness, bot)
    harness.terminate(bot)
    harness.metrics['managed_rotation'] = dict(same_account=True, generations=['a', 'b'], cold_restart=True,
                                               old_candidate_rejected=True, original_history_preserved=True,
                                               private_rest_b=len(observed), private_ws_b=True, funding_b=True,
                                               reconciliation=report['report']['state'], audit_events=audit_events,
                                               ledger=ledger, actual_okx=False, local_kms=True)


def repair_scenario(harness):
    print('Offline E2E: expired native cleanup key, Retry and companion/selected repair', flush=True)
    bot, port = initialize(harness)
    seed_exposure(harness, port)
    spot = spot_digest(harness)
    ciphertext = credential_digest(harness)
    harness.control(cleanup_market_closes=True, credential_generations=['b'], drop_ws=True)
    root = disconnect(harness, port)

    def blocked():
        row = operation(harness, port, root['id'])
        return row if row['result']['stage'] == 'action_required' else None
    row = poll(blocked, timeout=60, description='expired key surfaces Action required')
    assert row['result']['reason_code'] == 'exchange_credentials_rejected'
    assert credential_digest(harness) != ciphertext  # Revoking metadata changed, not erasure.
    assert harness.sql('SELECT count(*) FROM exchange_credentials WHERE envelope IS NOT NULL') == '2'
    assert not [item for item in harness.ledger()['accepted'] if item['ordType'] == 'market']
    fence = harness.state()['control_version']
    children = []
    request = dict(expected_version=1, expected_control_version=fence, expected_operation_version=row['result']['operation_version'])
    code, retry = harness.admin(port, '/credentials/operations/' + root['id'] + '/retry', request, key='retry-expired-key')
    assert code == 202
    children.append((retry['id'], 'credentials_cleanup_retry'))
    code, replay = harness.admin(port, '/credentials/operations/' + root['id'] + '/retry', request, key='retry-expired-key')
    assert code == 202 and replay['id'] == retry['id']
    poll(blocked, timeout=60, description='Retry without repairing key stays blocked')
    for connector in ('okx', 'okx_perpetual'):
        row = poll(blocked, timeout=60, description='cleanup blocked before same-account repair')
        before = credential_digest(harness)
        data = dict(operation_id=root['id'], expected_version=1, expected_control_version=fence,
                    expected_operation_version=row['result']['operation_version'], credentials=FIXTURE_KEYS['b'])
        path = '/credentials/okx/' + connector + '/cleanup-credentials'
        code, rejected = harness.admin(port, path, {**data, 'expected_operation_version': 0}, key='stale-repair-' + connector)
        assert code == 409 and rejected['code'] == 'operation_version_changed'
        assert credential_digest(harness) == before
        code, child = harness.admin(port, path, data, key='repair-' + connector)
        assert code == 202, (code, child.get('code'))
        children.append((child['id'], 'credentials_cleanup_key'))
        code, replay = harness.admin(port, path, data, key='repair-' + connector)
        assert code == 202 and replay['id'] == child['id']
        assert child['result']['cleanup_operation_id'] == root['id']
        assert harness.state()['control_version'] == fence and harness.state()['desired_state'] == 'stopped'
        if connector == 'okx':
            poll(blocked, timeout=60, description='companion repaired; expired perpetual still blocks cleanup')
            assert harness.sql('SELECT count(*) FROM exchange_credentials WHERE envelope IS NOT NULL') == '2'
            assert not [item for item in harness.ledger()['accepted'] if item['ordType'] == 'market']
    ledger = verify_cleanup(harness, port, root, spot)
    for identity, _ in children:
        receipt = completed(harness, port, identity)
        assert receipt['result']['cleanup_operation_id'] == root['id'] and receipt['result']['stage'] == 'cleanup_completed'
    evidence = audit(harness, port, root['id'], children=children)
    assert slot(harness, port, 'okx')['version'] == 2 and slot(harness, port, 'okx_perpetual')['version'] == 2
    safe_logs(harness, bot)
    harness.terminate(bot)
    history = financial_digest(harness)
    bot, port = harness.start('repaired-cleanup-restart', auto_migrate=False)
    assert financial_digest(harness) == history and slot(harness, port, 'okx_perpetual')['status'] == 'disconnected'
    assert harness.state()['desired_state'] == 'stopped'
    harness.terminate(bot)
    harness.metrics['managed_cleanup_repair'] = dict(expired_key_blocked=True, retry_preserves_root=True,
                                                     companion_and_selected_repaired=True, stale_version_rejected=True,
                                                     no_duplicate_close=True, spot_preserved=True, restart_removed=True,
                                                     audit=evidence, ledger=ledger, actual_okx=False, local_kms=True)
