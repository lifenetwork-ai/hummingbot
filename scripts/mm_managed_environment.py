"""Cold deployment-mode boundaries on the real image and isolated OKX ledger."""
import base64
import time

from scripts.test_e2e_offline import http, poll

LOCAL_KEK = base64.b64encode(b'K' * 32).decode()
FIXTURE_CREDENTIALS = dict(api_key='offline-key', secret_key='offline-secret', passphrase='offline-pass')


def private_requests(ledger):
    return [row for row in ledger['requests'] if row['path'].startswith(('/api/v5/account/', '/api/v5/trade/'))]


def financial_digest(harness):
    # Owner/status/checkpoint timestamps may change on a legitimate cold boot.
    # Filled quantities, fees, held exposure and saved ciphertext must not.
    tables = (('orders', 'id'), ('trade_fills', 'market,order_id,exchange_trade_id'), ('positions', 'id'))
    return {table: harness.sql(
        f"SELECT md5(COALESCE(jsonb_agg(to_jsonb(t) ORDER BY {ordering}),'[]'::jsonb)::text) FROM {table} t")
        for table, ordering in tables}


def credential_digest(harness):
    return harness.sql("SELECT md5(COALESCE(jsonb_agg(to_jsonb(t) ORDER BY connector,environment),'[]'::jsonb)::text) "
                       "FROM exchange_credentials t")


def connection_status(harness, port, *, environment, connected):
    code, body = harness.admin(port, '/credentials/okx')
    assert code == 200 and body['environment'] == environment and body['source'] == 'postgres_kms'
    assert {row['connector'] for row in body['slots']} == {'okx', 'okx_perpetual'}
    return body if all((row['status'] == 'connected') == connected for row in body['slots']) else None


def managed_environment_scenario(harness):
    """No Google/GCP resources or external network; use the shipping entrypoint."""
    print('Offline E2E: managed local KEK, demo/live cold restart and scope isolation', flush=True)
    harness.env.update(EXCHANGE_CREDENTIALS_SOURCE='postgres_kms', KMS_PROVIDER='local',
                       KMS_LOCAL_SECRET_KEY=LOCAL_KEK, KMS_LOCAL_KEY_ID='local-v1', OKX_SITE='www',
                       ADMIN_API_ENABLED='true', ADMIN_BASIC_AUTH_ENABLED='true',
                       ADMIN_API_USERNAME='operator', ADMIN_API_PASSWORD='offline-admin',
                       FUNDING_SYNC_INTERVAL_SECONDS='3600')
    # Valid dummy env keys deliberately remain present in both modes. A broken
    # managed-source fallback would reach the simulator and be detected below.
    for prefix in ('HBOT_OKX_', 'HBOT_OKX_PERPETUAL_', 'HBOT_OKX_DEMO_', 'HBOT_OKX_PERPETUAL_DEMO_'):
        for name, value in (('API_KEY', 'offline-key'), ('SECRET_KEY', 'offline-secret'), ('PASSPHRASE', 'offline-pass')):
            harness.env[prefix + name] = value
    revisions = []

    def start(name, environment, *, valid_key=True, migrate=False):
        print(f'Offline managed environment: cold start {name} ({environment})', flush=True)
        harness.env['OKX_TRADING_ENV'] = environment
        harness.env['KMS_LOCAL_SECRET_KEY'] = LOCAL_KEK if valid_key else base64.b64encode(b'X' * 32).decode()
        bot, port = harness.start(name, auto_migrate=migrate)
        state = poll(lambda: (row if (row := harness.state())['owner_uuid'] and
                              row['acknowledged_version'] == row['control_version'] else None),
                     description='cold owner and control acknowledgement')
        revisions.append(dict(revision=name, environment=environment, generation=state['owner_generation']))
        assert http(port, '/live')[0] == 200
        return bot, port

    def stopped_identity():
        state = harness.state()
        assert state['desired_state'] == 'stopped' and not state['status']['running']

    demo, port = start('managed-demo-initial', 'demo', migrate=True)
    for connector in ('okx', 'okx_perpetual'):
        code, receipt = harness.admin(port, '/credentials/okx/' + connector, dict(
            expected_version=0, expected_control_version=harness.state()['control_version'],
            credentials=FIXTURE_CREDENTIALS), key='save-demo-' + connector)
        assert code == 202, {'status': code, 'code': receipt.get('code')}
        poll(lambda: harness.admin(port, '/credentials/operations/' + receipt['id'])[1]['status'] == 'succeeded',
             description='Demo bundle loaded and acknowledged')
    poll(lambda: connection_status(harness, port, environment='demo', connected=True), description='both Demo slots connected')
    stopped_identity()
    encrypted = credential_digest(harness)
    harness.terminate(demo)

    before = len(private_requests(harness.ledger()))
    live, port = start('managed-live-stopped', 'live')
    poll(lambda: harness.state()['status']['phase'] == 'halted', description='missing Live bundle blocks runtime')
    body = connection_status(harness, port, environment='live', connected=False)
    assert body and all(row['status'] == 'not_configured' and not row['configured'] for row in body['slots'])
    stopped_identity()
    assert len(private_requests(harness.ledger())) == before, 'Managed Live must not fall back to Demo/env keys'
    assert not harness.ledger()['accepted'] and credential_digest(harness) == encrypted
    # Even a valid read-only candidate cannot silently adopt the existing Demo
    # account binding. No manual SQL/reset/rebind is part of this test.
    code, rejected = harness.admin(port, '/credentials/okx/okx', dict(
        expected_version=0, expected_control_version=harness.state()['control_version'],
        credentials=FIXTURE_CREDENTIALS), key='reject-live-binding')
    assert code == 409 and rejected['code'] == 'exchange_account_mismatch', {'status': code, 'code': rejected.get('code')}
    assert credential_digest(harness) == encrypted
    candidate_reads = private_requests(harness.ledger())[before:]
    assert candidate_reads and all(row['method'] == 'GET' and row['path'] == '/api/v5/account/config'
                                   and row['simulated'] is None for row in candidate_reads)
    harness.terminate(live)

    demo, port = start('managed-demo-restored', 'demo')
    poll(lambda: connection_status(harness, port, environment='demo', connected=True), description='Demo binding restored')
    stopped_identity()
    assert credential_digest(harness) == encrypted
    harness.mm_command(port, 'start', market_mode='both', selection='fixed', profile_id='sideways', duration_seconds=1800)
    harness.wait_running(port)
    for instrument in ('LIFE-USDT', 'LIFE-USDT-SWAP'):
        order = poll(lambda: next((row for row in harness.ledger()['orders']
                                  if row['instId'] == instrument and row['side'] == 'buy' and row['state'] == 'live'), None),
                     description='working Demo BUY ' + instrument)
        harness.control(fill=dict(clOrdId=order['clOrdId'], amount='1', fee='0.00001'), duplicate=True)
    poll(lambda: harness.sql("SELECT count(*) FROM trade_fills") == '2', description='two deduplicated native fills recorded')
    harness.terminate(demo)  # Clean revision shutdown preserves desired running.
    financial = financial_digest(harness)
    accepted = len(harness.ledger()['accepted'])
    reads = len(private_requests(harness.ledger()))
    assert harness.state()['desired_state'] == 'running'

    live, port = start('managed-live-running-intent', 'live')
    poll(lambda: harness.state()['status']['phase'] == 'halted', description='running intent cannot cross into Live')
    assert not harness.state()['status']['running'] and harness.state()['desired_state'] == 'running'
    assert connection_status(harness, port, environment='live', connected=False)
    assert len(harness.ledger()['accepted']) == accepted and len(private_requests(harness.ledger())) == reads
    assert financial_digest(harness) == financial and credential_digest(harness) == encrypted
    assert not harness.ledger()['unknown']
    harness.terminate(live)

    demo, port = start('managed-demo-running-return', 'demo')
    harness.wait_running(port)
    poll(lambda: len(harness.ledger()['accepted']) > accepted, description='only original Demo intent resumes quotes')
    new_private = private_requests(harness.ledger())[reads:]
    assert new_private and all(row['simulated'] == '1' for row in new_private)
    assert credential_digest(harness) == encrypted
    harness.mm_command(port, 'stop')
    harness.terminate(demo)
    financial = financial_digest(harness)
    reads = len(private_requests(harness.ledger()))

    bad, port = start('managed-demo-wrong-kek', 'demo', valid_key=False)
    poll(lambda: harness.state()['status']['phase'] == 'halted', description='wrong local KEK refuses cold load')
    assert connection_status(harness, port, environment='demo', connected=False)
    generation = harness.state()['owner_generation']
    time.sleep(2)
    assert http(port, '/live')[0] == 200 and harness.state()['owner_generation'] == generation
    stopped_identity()
    assert len(private_requests(harness.ledger())) == reads and financial_digest(harness) == financial
    assert credential_digest(harness) == encrypted
    harness.terminate(bad)

    demo, port = start('managed-demo-correct-kek', 'demo')
    poll(lambda: connection_status(harness, port, environment='demo', connected=True), description='original local KEK restores loaded state')
    stopped_identity()
    assert financial_digest(harness) == financial and credential_digest(harness) == encrypted
    assert not harness.ledger()['unknown']
    from scripts.test_mm_load import verify_ledger
    ledger = verify_ledger(harness, port, require_running=False)
    harness.terminate(demo)
    harness.metrics['managed_environment'] = dict(
        revisions=revisions, local_kms=True, actual_google=False, actual_gcp=False, actual_okx=False,
        stopped_roundtrip=True, running_intent_blocked_in_live=True, env_fallback_blocked=True,
        live_candidate_rejected=True, ciphertext_preserved=True, financial_records_preserved=True,
        wrong_kek_blocked=True, original_kek_restored=True, ledger=ledger)
