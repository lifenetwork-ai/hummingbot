"""A stopped required action consumer closes admission and uses existing teardown."""
import asyncio
from test.hummingbot.cli.test_service_availability import host
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.models.executor_actions import StopExecutorAction


async def listener(item, failure):
    strategy = SimpleNamespace(controllers={}, actions_queue=asyncio.Queue(),
                               executor_orchestrator=SimpleNamespace(execute_actions=MagicMock()))
    strategy.logger = MagicMock()
    if failure == 'failed':
        async def broken():
            raise RuntimeError('private-listener-error')
        task = asyncio.create_task(broken())
    else:
        task = asyncio.create_task(StrategyV2Base.listen_to_executor_actions(strategy))
    strategy.listen_to_executor_actions_task = task
    item.hb.trading_core.strategy = strategy
    await asyncio.sleep(0)
    if failure == 'cancelled':
        task.cancel()
    if failure != 'healthy':
        await asyncio.gather(task, return_exceptions=True)
    return strategy, task


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['cancelled', 'failed'])
async def test_stopped_action_consumer_blocks_new_risk_before_observer_tick(failure):
    item = host()
    strategy, task = await listener(item, failure)
    try:
        await strategy.actions_queue.put([StopExecutorAction(controller_id='audit', executor_id='executor', keep_position=True)])
        assert item.runtime_problem() == 'executor_action_listener_stopped'
        assert not item.runtime_healthy()
        assert strategy.actions_queue.qsize() == 1
        strategy.executor_orchestrator.execute_actions.assert_not_called()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['cancelled', 'failed'])
async def test_stopped_action_consumer_enters_existing_recovery_teardown(monkeypatch, failure):
    item = host()
    _, task = await listener(item, failure)
    core = item.hb.trading_core
    core.trade_fill_db = SimpleNamespace(persistence_healthy=True)
    core._strategy_running = True
    item.running, item.applied_version = True, 0
    row = dict(control_version=0, config_type='controller', config_file_path='test.yml', desired_state='running',
               recovery_state={}, mm_session=None)
    item.owner = MagicMock()
    item.owner.try_acquire.return_value = True
    item.owner.check_owner.return_value = row
    item.owner.allow_new_orders = True
    item.stop_core = AsyncMock(return_value=True)
    item.poll_reconciliation = AsyncMock()
    item.poll_funding = MagicMock()
    monkeypatch.setattr('hummingbot.core.order_submission_guard.install_guard', lambda owner: None)
    try:
        await item.observe_once()
        item.owner.require_recovery.assert_called_once()
        item.stop_core.assert_awaited_once()
        assert not item.running and item.phase == 'halted'
        incident = item.availability.snapshot()['incidents'][0]
        assert incident['reason_code'] == 'executor_action_listener_stopped'
        assert incident['operator_action_required']
        assert 'private-listener-error' not in str(item.snapshot())
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_live_consumer_and_intentional_close_do_not_raise_runtime_incident():
    item = host()
    _, task = await listener(item, 'healthy')
    try:
        assert item.runtime_problem() is None and item.runtime_healthy()
        item.owner = MagicMock(owner_uuid='owner', generation=1)
        item.owner.check_owner.return_value = {'control_version': 0}
        item.stop_core = AsyncMock(return_value=True)
        item.snapshot = lambda: {}
        item.stop.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert await item.close()
        item.owner.require_recovery.assert_not_called()
        assert item.availability.snapshot()['incidents'] == []
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
