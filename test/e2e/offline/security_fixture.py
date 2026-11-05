"""Offline signed identities for test wrappers only; never imported by shipping code."""
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


def create_identity_fixture(directory):
    import jwt
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    directory = Path(directory)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Offline load identity fixture')])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                   .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
                   .not_valid_after(now + timedelta(days=1)).sign(key, hashes.SHA256()))
    certs = {'offline-load': certificate.public_bytes(serialization.Encoding.PEM).decode()}
    client_id = 'offline-load-client'
    tokens = {role: jwt.encode(dict(iss='https://accounts.google.com', sub='load-' + role, aud=client_id,
                                    email=f'load-{role}@gmail.com', email_verified=True, iat=int(time.time()),
                                    exp=int(time.time()) + 6 * 3600), key, algorithm='RS256', headers={'kid': 'offline-load'})
              for role in ('admin', 'write', 'read')}
    fixture = dict(client_id=client_id, tokens=tokens)
    (directory / 'google-certificates.json').write_text(json.dumps(certs))
    (directory / 'google-identities.json').write_text(json.dumps(fixture))
    # The private signing key is never persisted or mounted into any container.
    return fixture


def install_identity_fixture(directory):
    from hummingbot.cli.admin_identity import AdminIdentityService, GoogleTokenVerifier
    directory = Path(directory)
    certs = json.loads((directory / 'google-certificates.json').read_text())
    original = AdminIdentityService.__init__

    def initialize(self, api, **kwargs):
        verifier = GoogleTokenVerifier(api.settings.google_client_id, certificates=certs)
        return original(self, api, verifier=verifier)

    AdminIdentityService.__init__ = initialize


def bootstrap_accounts(harness, port):
    """Use actual account and sign-in handlers, not direct seed/role bypass SQL."""
    fixture = json.loads((harness.assets / 'google-identities.json').read_text())
    from scripts.test_e2e_offline import http
    for role, token in fixture['tokens'].items():
        status, result = harness.admin(port, '/access/accounts', dict(email=f'load-{role}@gmail.com', role=role))
        assert status == 201, (status, result)
        status, result = http(port, '/api/v1/admin/access/login', {}, {'Authorization': 'Bearer ' + token})
        assert status == 200, (status, result)
        assert result['role'] == role
    harness.admin_authorization = 'Bearer ' + fixture['tokens']['admin']
    status, readiness = harness.admin(port, '/access/bootstrap')
    assert status == 200 and readiness['google_admin_ready'], readiness
    return dict(auth='signed_offline_google', actual_google=False, roles=['admin', 'write', 'read'],
                certificate_injection='test_wrapper_only', basic_disabled_for_measurement=True)


def configure_connections(harness, port):
    from scripts.test_e2e_offline import poll
    poll(lambda: (row if (row := harness.state())['owner_uuid'] and row['control_version'] == row['acknowledged_version'] else None),
         description='stopped owner before managed connection setup')
    for connector in ('okx', 'okx_perpetual'):
        status, receipt = harness.admin(port, '/credentials/okx/' + connector,
                                        dict(expected_version=0, expected_control_version=harness.state()['control_version'],
                                             credentials=dict(api_key='offline-key', secret_key='offline-secret', passphrase='offline-pass')), key='setup-' + connector)
        assert status == 202, (status, receipt)
        poll(lambda: harness.admin(port, '/credentials/operations/' + receipt['id'])[1]['status'] == 'succeeded',
             description='managed connection loaded')
        poll(lambda: harness.state()['acknowledged_version'] == receipt['effective_version'], description='managed setup control acknowledgement')


async def mixed_security_workload(client, directory, base_url, deadline, summary, *, interval=30):
    """Bounded extra security operations; original read/fill rates remain unchanged."""
    import asyncio
    fixture = json.loads((Path(directory) / 'google-identities.json').read_text())
    tokens = fixture['tokens']
    summary.update(requests=0, expected_denials=0, role_cycles=0)

    async def request(role, method, path, expected, body=None):
        authorization = 'Bearer ' + tokens[role] if role in tokens else 'Bearer invalid.offline.signature'
        async with client.request(method, base_url + path, headers={'Authorization': authorization}, json=body) as response:
            value = await response.json()
            summary['requests'] += 1
            if response.status != expected:
                summary['failure'] = dict(path=path, expected_status=expected, status=response.status,
                                          code=value.get('code'), request_id=value.get('request_id'))
                raise RuntimeError('Unexpected security workload HTTP status')
            summary['expected_denials'] += expected in (401, 403)
            return value

    loop = asyncio.get_running_loop()
    while loop.time() < deadline:
        await request('read', 'GET', '/credentials/okx', 200)
        await request('read', 'GET', '/audit?limit=20', 200)
        await request('read', 'POST', '/credentials/okx/okx', 403, {})
        await request('invalid', 'GET', '/access/me', 401)
        accounts = await request('admin', 'GET', '/access/accounts', 200)
        writer = next(row for row in accounts['items'] if row['email'] == 'load-write@gmail.com')
        changed = await request('admin', 'POST', '/access/accounts/' + writer['id'], 200,
                                dict(expected_version=writer['version'], role='read'))
        try:
            await request('write', 'POST', '/reconciliation/check', 403, {'market': 'all'})
            observed = await request('write', 'GET', '/access/me', 200)
            if observed['role'] != 'read':
                raise RuntimeError('Role change was not observed on the existing signed token')
        finally:
            await request('admin', 'POST', '/access/accounts/' + writer['id'], 200,
                          dict(expected_version=changed['version'], role='write'))
        summary['role_cycles'] += 1
        await asyncio.sleep(max(0, min(interval, deadline - loop.time())))


def disconnect_connections(harness, bot, port):
    """Actual native connector/recorder cleanup against the isolated exchange."""
    from decimal import Decimal

    from scripts.mm_load_ledger import ledger_violations
    from scripts.test_e2e_offline import docker, http, poll

    # Ordinary Stop remains a separate hold-preserving operation.
    harness.mm_command(port, 'stop')
    spot = harness.sql("SELECT COALESCE(json_agg(t ORDER BY id),'[]') FROM positions t WHERE connector_name='okx'")
    before_generation = harness.state()['owner_generation']
    harness.control(cleanup_market_closes=True)
    operations = []
    for connector in ('okx', 'okx_perpetual'):
        status, metadata = harness.admin(port, '/credentials/okx')
        assert status == 200
        slot = next(item for item in metadata['slots'] if item['connector'] == connector)
        status, receipt = harness.admin(port, f'/credentials/okx/{connector}/revoke',
                                        dict(expected_version=slot['version'], expected_control_version=harness.state()['control_version']), key='disconnect-' + connector)
        assert status == 202, receipt

        def completed():
            status, observed = harness.admin(port, '/credentials/operations/' + receipt['id'])
            assert status == 200
            assert not observed['result']['operator_action_required'], observed
            assert observed['status'] != 'failed', observed
            return observed if observed['status'] == 'succeeded' else None

        result = poll(completed, timeout=180, description='native automatic cancel/close/removal')
        assert result['result']['stage'] == 'removed'
        operations.append(dict(id=result['id'], connector=connector, status=result['status']))
    ledger = harness.ledger()
    assert not ledger['unknown'] and all(Decimal(row['pos']) == 0 for row in ledger['positions'])
    rows = json.loads(harness.sql("SELECT COALESCE(json_agg(t),'[]') FROM (SELECT market,order_id,exchange_trade_id,amount::text AS amount,price::text AS price,trade_fee,trade_fee_in_quote::text AS trade_fee_in_quote FROM trade_fills WHERE config_file_path <> 'load-history.yaml') t"))
    assert not ledger_violations(ledger, rows)
    assert spot == harness.sql("SELECT COALESCE(json_agg(t ORDER BY id),'[]') FROM positions t WHERE connector_name='okx'")
    assert harness.sql("SELECT count(*) FROM positions WHERE connector_name='okx_perpetual' AND amount <> 0") == '0'
    assert harness.sql("SELECT count(*) FROM exchange_credentials WHERE envelope IS NOT NULL") == '0'
    assert harness.state()['owner_generation'] == before_generation
    harness.terminate(bot)
    docker('start', bot)
    poll(lambda: http(port, '/startup')[0] == 200, timeout=200, description='credential-removal restart')
    poll(lambda: harness.state()['owner_generation'] > before_generation, description='new owner after credential removal')
    assert harness.state()['desired_state'] == 'stopped'
    status, metadata = harness.admin(port, '/credentials/okx')
    assert status == 200 and all(row['status'] == 'disconnected' for row in metadata['slots'])
    assert harness.sql("SELECT count(*) FROM positions WHERE connector_name='okx_perpetual' AND amount <> 0") == '0'
    return dict(operations=operations, recorder_fills=len(rows), fills_fees_deduplicated=True,
                flat_verified=True, spot_checkpoint_preserved=True, restart_removed=True,
                actual_okx_demo=False, exchange='isolated_simulator')
