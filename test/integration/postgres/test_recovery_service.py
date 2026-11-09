"""Shipping API, supervisor and engine against disposable PG/local KMS/fake venue."""
import base64
from test.integration.postgres.test_managed_service import call
from test.integration.postgres.test_service import eventually, health, start_service, terminate

from sqlalchemy import text

from hummingbot.model.bot_state_store import BotStateStore


def test_supervised_review_confirm_retry_and_restart_stay_stopped(pg_engine, tmp_path, monkeypatch):
    monkeypatch.setenv('EXCHANGE_CREDENTIALS_SOURCE', 'postgres_kms')
    monkeypatch.setenv('KMS_PROVIDER', 'local')
    monkeypatch.setenv('KMS_LOCAL_SECRET_KEY', base64.b64encode(b'K' * 32).decode())
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    monkeypatch.setenv('OKX_SITE', 'www')
    monkeypatch.setenv('HBOT_OFFLINE_CREDENTIAL_ACCOUNT', 'offline-account')
    monkeypatch.setenv('HBOT_OFFLINE_RECOVERY_EMPTY', 'true')
    processes = []
    state = BotStateStore(pg_engine, 'hummingbot')
    try:
        process, port, log = start_service(tmp_path / 'recovery-first', admin_enabled=True)
        processes.append((process, log))
        eventually(lambda: health(port, '/startup')[0] == 200)
        eventually(lambda: state.read() and state.read()['owner_uuid'])
        code, saved = call(port, '/credentials/okx/okx', key='save', body=dict(expected_version=0,
                                                                               expected_control_version=0, credentials=dict(api_key='RECOVERY-CANARY-API', secret_key='RECOVERY-CANARY-SECRET',
                                                                                                                            passphrase='RECOVERY-CANARY-PASS')))
        assert code == 202, saved
        eventually(lambda: call(port, '/credentials/operations/' + saved['id'])[1].get('status') == 'succeeded')
        eventually(lambda: state.read()['acknowledged_version'] == 1)
        # Inject the incident latch on this disposable account; no runtime or
        # venue effects exist. Preserve all other checkpoint/credential fields.
        with pg_engine.begin() as db:
            db.execute(text("UPDATE bot_state SET recovery_state=recovery_state || CAST(:incident AS jsonb)"),
                       {'incident': '{"halt":true,"reason":"Strategy initialization/reconciliation failed; inspect sanitized logs."}'})
        eventually(lambda: call(port, '/status')[1].get('recovery', {}).get('halted'))
        code, check = call(port, '/recovery/check', key='check', body={'expected_version': 1})
        assert code == 202, check
        check = eventually(lambda: (item if (item := call(port, '/recovery/operations/' + check['id'])[1]).get('status') in ('succeeded', 'failed') else None))
        assert check['can_recover'], check
        assert state.read()['recovery_state']['halt']  # Preview cannot clear HALT.
        payload = dict(expected_version=1, check_id=check['id'], confirmation_token='0' * 32)
        code, rejected = call(port, '/recovery/reset', key='tampered', body=payload)
        assert code == 409 and rejected['code'] == 'recovery_confirmation_invalid'
        payload['confirmation_token'] = check['confirmation_token']
        code, reset = call(port, '/recovery/reset', key='reset', body=payload)
        assert code == 202, reset
        reset = eventually(lambda: (item if (item := call(port, '/recovery/operations/' + reset['id'])[1]).get('status') in ('succeeded', 'failed') else None))
        assert reset['stage'] == 'recovered', reset
        assert call(port, '/recovery/reset', key='reset', body=payload)[1]['id'] == reset['id']
        eventually(lambda: state.read()['acknowledged_version'] == 2)
        assert state.read()['desired_state'] == 'stopped' and not state.read()['status']['running']
        assert not state.read()['recovery_state'].get('halt')
        terminate(process)
        process, port, log = start_service(tmp_path / 'recovery-restart', admin_enabled=True, auto_migrate=False)
        processes.append((process, log))
        eventually(lambda: health(port, '/startup')[0] == 200)
        eventually(lambda: state.read()['owner_uuid'] and state.read()['status'].get('phase') == 'stopped')
        assert state.read()['control_version'] == 2 and state.read()['desired_state'] == 'stopped'
        assert not state.read()['recovery_state'].get('halt') and not state.read()['status']['running']
    finally:
        for process, log in processes:
            terminate(process)
            log.seek(0)
            assert 'RECOVERY-CANARY' not in log.read()
            log.close()
