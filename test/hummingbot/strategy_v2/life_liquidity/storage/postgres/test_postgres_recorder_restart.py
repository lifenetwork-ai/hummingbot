"""PG5 real orchestrator/reporting cold restart, with no SQLite authority."""
import json
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_positions import held
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_recorder_application import close, setup
from unittest.mock import PropertyMock, patch

import pytest

from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.operations import PgRecorderOperations
from hummingbot.strategy_v2.life_liquidity.storage.postgres.recorder_application import PgRecorderApplication
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import StoreExecutorAction


@pytest.mark.asyncio
@pytest.mark.parametrize('current_wire', [None, 'wire1'])
async def test_real_orchestrator_cold_primary_preserves_exact_config_retry_wire_and_position_history(database, monkeypatch, current_wire):
    s = await setup(database, monkeypatch)
    financial = app = None
    try:
        before = await s.financial.balance(s.cfg.binding)
        assert await s.app.before_dispatch(s.t.create)
        s.runner.execute_action(s.t.create)
        child, = s.runner.active_executors['controller1']
        await s.app.drain()
        initial = child.executor_info
        child._status = RunnableStatus.TERMINATED
        precision = D('0.123456789012345678')
        info = initial.model_copy(deep=True, update={
            'status': RunnableStatus.TERMINATED, 'is_active': False, 'is_trading': False,
            'cum_fees_quote': precision, 'net_pnl_quote': -precision, 'filled_amount_quote': D('2.123456789012345678'),
            'custom_info': {**initial.custom_info, 'recovery_order_ids': ['wire1'],
                            'order_id': current_wire, 'current_retries': 2,
                            'authenticated_account_uid': None, 'opaque': {'exact': str(precision)}}})
        with patch.object(type(child), 'executor_info', new_callable=PropertyMock, return_value=info):
            s.runner.store_executor(StoreExecutorAction(controller_id='controller1', executor_id=child.config.id))
            await s.runner.drain_repository_reporting()
        position = held().restore()
        s.runner.positions_held['controller1'] = [position]
        s.runner.store_all_positions()
        await s.runner.drain_repository_reporting()
        assert s.runner.active_executors['controller1'] == []
        stored = await s.runner.async_get_stored_executors_by_controller('controller1')
        await s.app.close()
        await s.financial.close()
        financial = PgSpotRepository(await PgRuntime.open(s.cfg))
        app = await PgRecorderApplication.install(financial, **s.kwargs)
        with patch('hummingbot.strategy_v2.executors.executor_orchestrator.MarketsRecorder.get_instance',
                   side_effect=AssertionError('legacy recorder fallback')):
            cold = ExecutorOrchestrator(s.strategy, repository_reporting={'controller1': app})
            assert cold.active_executors['controller1'] == []
            restored, = await cold.async_get_stored_executors_by_controller('controller1')
            assert restored.model_dump_json() == stored[0].model_dump_json()
            assert restored.config == initial.config and restored.cum_fees_quote == precision
            assert restored.custom_info['recovery_order_ids'] == ['wire1']
            assert restored.custom_info['current_retries'] == 2
            assert restored.custom_info['order_id'] == current_wire
            assert restored.custom_info['authenticated_account_uid'] is None
            assert restored.custom_info['recovery_account_uid'] == s.cfg.binding.account_uid
            assert restored.custom_info['life_storage_binding'] == s.cfg.binding.json()
            assert cold.positions_held['controller1'][0].net_amount_base == position.net_amount_base
            assert await financial.balance(s.cfg.binding) == before
            report = await PgRecorderOperations(app).snapshot(start_ms=0, timeout_s=3)
            assert report.reporting_complete
            assert json.loads(report.executor_json[0])['cum_fees_quote'] == str(precision)
        s.strategy.buy.assert_not_called()
        s.strategy.sell.assert_not_called()
        assert not s.t.session.sent
    finally:
        if app is not None:
            await app.close()
        if financial is not None:
            await financial.close()
        await close(s)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['missing', 'foreign', 'legacy', 'read_failure'])
async def test_missing_foreign_legacy_or_unreadable_executor_never_becomes_empty_clean_restart(database, monkeypatch, fault):
    s = await setup(database, monkeypatch)
    try:
        assert await s.app.before_dispatch(s.t.create)
        s.runner.execute_action(s.t.create)
        await s.app.drain()
        before = await s.financial.balance(s.cfg.binding)
        c = database[0]
        if fault == 'missing':
            await c.execute('DELETE FROM life_recorder."Executors"')
        elif fault == 'foreign':
            await c.execute('UPDATE life_recorder."Executors" SET controller_id=\'foreign\'')
        elif fault == 'legacy':
            await c.execute('UPDATE life_recorder."Executors" SET custom_info=\'{}\'')
        else:
            async def broken(*args, **kwargs):
                raise StorageError('PG_SYNTHETIC_RECORDER_READ_FAILURE')
            monkeypatch.setattr(s.app.repository, 'read', broken)
        with pytest.raises(StorageError):
            await s.app.recover()
        assert not s.app.ready
        assert s.runner.active_executors['controller1']
        assert await s.financial.balance(s.cfg.binding) == before
        s.strategy.buy.assert_not_called()
        s.strategy.sell.assert_not_called()
        assert not s.t.session.sent
    finally:
        await close(s)
