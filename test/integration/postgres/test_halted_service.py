"""Shipping supervisor/API/engine remain usable around a durable unknown send."""
import asyncio
import time
from test.hummingbot.client.config.test_mm_profiles import CATALOGUE
from test.integration.postgres.test_managed_service import call
from test.integration.postgres.test_service import eventually, health, start_service, terminate

from sqlalchemy import update

from hummingbot.client.config.mm_profiles import MMProfilesConfig
from hummingbot.core.mm_session import MMSessionRequest
from hummingbot.model.bot_state import BotState
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres
from hummingbot.model.order import Order


def test_cold_unknown_send_keeps_process_owner_stop_and_recovery_api_available(pg_engine, tmp_path):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    store = BotStateStore(pg_engine, 'hummingbot')
    store.ensure()
    catalogue = asyncio.run(MMProfilesConfig.load(CATALOGUE))
    requested = store.request_mm_session(action='start', expected_version=0, catalogue=catalogue,
                                         request=MMSessionRequest(market_mode='spot', profile_id='follow_market', duration_seconds=600))
    with pg_engine.begin() as db:
        db.execute(update(BotState).values(recovery_state={'halt': True, 'runtime_active': True,
                   'shutdown_incomplete': True, 'reason': 'An order submission did not finish with a durable acknowledgment.'}))
        db.execute(Order.__table__.insert().values(id='unknown-busy', config_file_path='life_mm_profiles.yaml', strategy='test',
                   market='okx', symbol='BTC-USDT', base_asset='BTC', quote_asset='USDT',
                   creation_timestamp=int(time.time() * 1000), order_type='LIMIT_MAKER', amount='.1', price='100',
                   last_status='OrderFailure', last_update_timestamp=int(time.time() * 1000), creation_pending=True))
    process, port, log = start_service(tmp_path / 'halted', admin_enabled=True)
    try:
        eventually(lambda: health(port, '/startup')[0] == 200)
        state = eventually(lambda: (row if (row := store.read())['acknowledged_version'] == 1
                           and row['status'].get('phase') == 'halted' else None))
        identity = state['owner_uuid'], state['owner_generation']
        assert identity[0] and process.poll() is None
        for _ in range(3):
            assert health(port, '/live')[0] == 200
            assert health(port, '/ready')[0] == 503
            assert call(port, '/status')[1]['recovery']['halted']
            assert (store.read()['owner_uuid'], store.read()['owner_generation']) == identity
            time.sleep(.4)
        code, stopped = call(port, '/commands', key='stop-halted', body={
            'action': 'stop', 'expected_version': 1, 'session_id': requested['session_id']})
        assert code == 202, stopped
        eventually(lambda: store.read()['acknowledged_version'] == 2)
        code, check = call(port, '/recovery/check', key='check-halted', body={'expected_version': 2})
        assert code == 202, check
        result = eventually(lambda: (view if (view := call(port, '/recovery/operations/' + check['id'])[1])['status']
                                     in ('failed', 'succeeded') else None))
        assert [b['code'] for b in result['blockers']] == ['recovery_pending_intents'], result
        with pg_engine.connect() as db:
            assert db.execute(Order.__table__.select()).mappings().one()['creation_pending']
        assert store.read()['recovery_state']['halt'] and process.poll() is None
        assert (store.read()['owner_uuid'], store.read()['owner_generation']) == identity
    finally:
        terminate(process)
        log.seek(0)
        output = log.read()
        assert 'child_restart_scheduled' not in output
        assert 'StrategyStartFailed' not in output
        assert 'Configuration bootstrap failed' not in output
        log.close()
