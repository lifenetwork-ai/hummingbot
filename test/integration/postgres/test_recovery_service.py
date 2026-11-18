"""Shipping API, supervisor and engine against disposable PG/local KMS/fake venue."""
import base64
import json
from test.integration.postgres.test_managed_service import call
from test.integration.postgres.test_service import eventually, health, start_service, terminate

import pytest
from sqlalchemy import text

from hummingbot.model.bot_state_store import BotStateStore


@pytest.mark.parametrize('checkpoint_history,terminal_metadata,uncertain', [
    (False, False, False), (True, False, False), (True, True, False), (True, False, True)])
def test_supervised_review_confirm_retry_and_restart_stay_stopped(pg_engine, tmp_path, monkeypatch, checkpoint_history, terminal_metadata, uncertain):
    monkeypatch.setenv('EXCHANGE_CREDENTIALS_SOURCE', 'postgres_kms')
    monkeypatch.setenv('KMS_PROVIDER', 'local')
    monkeypatch.setenv('KMS_LOCAL_SECRET_KEY', base64.b64encode(b'K' * 32).decode())
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    monkeypatch.setenv('OKX_SITE', 'www')
    monkeypatch.setenv('HBOT_OFFLINE_CREDENTIAL_ACCOUNT', 'offline-account')
    monkeypatch.setenv('HBOT_OFFLINE_RECOVERY_EMPTY', 'true')
    if terminal_metadata or uncertain:
        orders_path = tmp_path / 'native-terminal-orders.json'
        orders_path.write_text('{}')
        monkeypatch.setenv('HBOT_OFFLINE_RECOVERY_ORDERS', str(orders_path))
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
        if checkpoint_history:
            from test.integration.postgres.test_checkpoint_recovery import seed_checkpoint

            from hummingbot.model.recovery_store import RecoveryStore
            if uncertain:
                from test.integration.postgres.test_recovery_uncertain import seed_uncertain
                identity = seed_uncertain(RecoveryStore(state), existing_binding=True, api_key='RECOVERY-CANARY-API')
            elif terminal_metadata:
                from test.integration.postgres.test_recovery_terminal_metadata import seed_terminal_work
                store = RecoveryStore(state)
                seed_terminal_work(store, existing_binding=True, api_key='RECOVERY-CANARY-API')
                candidates = store.snapshot()['terminal_orders']
                orders_path.write_text(json.dumps({r['id']: dict(clOrdId=r['id'], ordId=r['exchange_order_id'],
                                                                 instId=r['pair'], side=r['side'].lower(), ordType='post_only', state='canceled', tdMode='cash',
                                                                 sz=r['amount'], px=r['price'], accFillSz='0', uTime=str(r['last_update_timestamp'] + 1)) for r in candidates}))
            else:
                seed_checkpoint(RecoveryStore(state), fills=0, existing_binding=True)
        expected = {'expected_version': 1}
        if uncertain:
            path = '/recovery?uncertain_order_id=' + identity
            assert not call(port, '/recovery')[1]['check_available']
            assert call(port, path)[1]['check_available']
            expected['uncertain_order_id'] = identity
        else:
            eventually(lambda: call(port, '/status')[1].get('recovery', {}).get('halted'))
        code, check = call(port, '/recovery/check', key='check', body=expected)
        assert code == 202, check
        check = eventually(lambda: (item if (item := call(port, '/recovery/operations/' + check['id'])[1]).get('status') in ('succeeded', 'failed') else None))
        assert check['can_recover'], check
        if checkpoint_history:
            assert check['verification']['scope'] == 'verified_checkpoint'
            assert check['proposed_changes'] == []
            assert len(check['proposed_order_changes']) == (5 if terminal_metadata else 0)
        assert bool(state.read()['recovery_state'].get('halt')) is not uncertain
        if uncertain:
            assert check['proposed_uncertain_order']['id'] == identity
            assert check['proposed_uncertain_order']['before'] == 'PendingBuy'
            assert call(port, path)[1]['reset_available']
            assert call(port, '/orders/' + identity)[1]['order']['creation_pending']
        payload = dict(**expected, check_id=check['id'], confirmation_token='0' * 32)
        code, rejected = call(port, '/recovery/reset', key='tampered', body=payload)
        assert code == 409 and rejected['code'] == 'recovery_confirmation_invalid'
        payload['confirmation_token'] = check['confirmation_token']
        if terminal_metadata or uncertain:
            code, rejected = call(port, '/recovery/reset', key='missing-order-review', body=payload)
            assert code == 409 and rejected['code'] == 'recovery_order_review_required'
            payload['confirm_order_changes'] = True
        if uncertain:
            code, rejected = call(port, '/recovery/reset', key='different-order',
                                  body={**payload, 'uncertain_order_id': 'different'})
            assert code == 409 and rejected['code'] == 'recovery_state_changed'
            orders_path.write_text(json.dumps({identity: {'clOrdId': identity, 'ordId': '1', 'state': 'live'}}))
            code, late = call(port, '/recovery/reset', key='late-ack', body=payload)
            assert code == 202, late
            late = eventually(lambda: (item if (item := call(port, '/recovery/operations/' + late['id'])[1]).get('status') == 'failed' else None))
            assert late['blockers'][0]['code'] == 'recovery_uncertain_order_found'
            assert call(port, '/orders/' + identity)[1]['order']['creation_pending']
            orders_path.write_text('{}')
        code, reset = call(port, '/recovery/reset', key='reset', body=payload)
        assert code == 202, reset
        reset = eventually(lambda: (item if (item := call(port, '/recovery/operations/' + reset['id'])[1]).get('status') in ('succeeded', 'failed') else None))
        assert reset['stage'] == 'recovered', reset
        assert len(reset['repaired_orders']) == (5 if terminal_metadata else 0)
        if uncertain:
            assert reset['repaired_uncertain_order'] == check['proposed_uncertain_order']
            result = call(port, '/orders/' + identity)[1]['order']
            assert not result['creation_pending'] and result['status'] == 'OrderFailure'
            assert not result['exchange_order_id'] and result['recorded_filled_amount'] == '0'
            assert not call(port, '/recovery?uncertain_order_id=' + identity)[1]['check_available']
        replay_code, replay = call(port, '/recovery/reset', key='reset', body=payload)
        assert replay_code == 202 and replay['id'] == reset['id'], replay
        if uncertain:
            audit_code, audit = call(port, '/audit?operation_id=' + reset['id'])
            assert audit_code == 200, audit
            repairs = [event for event in audit['items'] if event['action'] == 'recovery_order_repaired']
            assert len(repairs) == 1 and repairs[0]['target_id'] == identity and repairs[0]['actor_role'] == 'admin'
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
