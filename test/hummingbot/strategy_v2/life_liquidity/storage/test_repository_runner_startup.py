"""The actual V2 runner accepts fully assembled asynchronous repositories."""
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_application import setup
from unittest.mock import MagicMock

import pytest

from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.life_liquidity.storage.recorder_factory import open_file_recorder


@pytest.mark.asyncio
async def test_actual_v2_constructor_uses_preassembled_controllers_without_global_sql(tmp_path, monkeypatch):
    app, store, controller, _, _, _ = await setup(tmp_path)
    reporting = await open_file_recorder(app.runtime, database_path=str(tmp_path / 'recorder.sqlite'),
                                         config_file_path='life-test', strategy_name='v2',
                                         connectors=(app.connector,), max_pending=2, shutdown_timeout_s=.5)
    monkeypatch.setattr(MarketsRecorder, 'get_instance', MagicMock(side_effect=AssertionError('global SQL forbidden')))
    runner = None
    try:
        runner = StrategyV2Base({'okx': app.connector}, repository_reporting={'life': reporting},
                                dispatch_max_pending=2, dispatch_shutdown_timeout_s=.5)
        assert runner.controllers['life'] is controller
        assert runner.executor_orchestrator.strategy is runner
        assert runner._async_action_dispatcher is not None
        assert runner.executor_orchestrator._repository_reporting['life'] is reporting
        assert runner.executor_orchestrator.active_executors['life'] == []
        await runner.on_stop()
        assert not app.runtime._local_ready()
    finally:
        if runner is not None:
            runner.listen_to_executor_actions_task.cancel()
        await reporting.close()
        await app.close()
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['budget', 'scope', 'connector', 'config'])
async def test_invalid_repository_startup_refuses_before_legacy_recorder_access(tmp_path, monkeypatch, fault):
    app, store, _, _, _, _ = await setup(tmp_path)
    reporting = await open_file_recorder(app.runtime, database_path=str(tmp_path / 'recorder.sqlite'),
                                         config_file_path='life-test', strategy_name='v2',
                                         connectors=(app.connector,), max_pending=2, shutdown_timeout_s=.5)
    monkeypatch.setattr(MarketsRecorder, 'get_instance', MagicMock(side_effect=AssertionError('global SQL forbidden')))
    try:
        with pytest.raises(ValueError, match='REPOSITORY_RUNNER_STARTUP_INVALID'):
            StrategyV2Base({'okx': object()} if fault == 'connector' else {'okx': app.connector},
                           config=object() if fault == 'config' else None,
                           repository_reporting={'foreign' if fault == 'scope' else 'life': reporting},
                           dispatch_max_pending=None if fault == 'budget' else 2, dispatch_shutdown_timeout_s=.5)
    finally:
        await reporting.close()
        await app.close()
        await store.close()
