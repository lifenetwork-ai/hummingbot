"""Actual repository runner clock entry and owned controller shutdown."""
import asyncio
from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_startup import joint_service
from unittest.mock import PropertyMock, patch

import pytest

from hummingbot.core.clock import Clock, ClockMode


@pytest.mark.asyncio
async def test_service_runner_ready_ticks_use_controller_queue_without_abstract_script_proposals(tmp_path):
    service, _, _, spot, swap = await joint_service(tmp_path)
    try:
        service.runner.market_data_provider.ready = True
        with patch.object(type(spot), 'ready', new_callable=PropertyMock, return_value=True), \
                patch.object(type(swap), 'ready', new_callable=PropertyMock, return_value=True):
            service.runner.tick(1)
            service.runner.tick(2)
            service.runner.tick(3)
        assert service.runner.ready_to_trade
        assert service.runner.create_actions_proposal() == []
        assert service.runner.stop_actions_proposal() == []
        assert not service.runtime._local_ready()
        assert not service.runner.executor_orchestrator.active_executors['life']
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_service_runner_clock_start_never_constructs_ui_and_close_joins_controller_loop(tmp_path):
    service, _, _, _, _ = await joint_service(tmp_path)
    entered, exited = asyncio.Event(), asyncio.Event()

    async def blocked_control():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            exited.set()

    service.controller.control_task = blocked_control
    clock = Clock(ClockMode.BACKTEST, 1, 1, 10)
    clock.add_iterator(service.runner)  # No connector/network iterator in this isolated clock test.
    try:
        with patch('hummingbot.client.hummingbot_application.HummingbotApplication.main_application',
                   side_effect=AssertionError('headless runtime constructed UI')):
            with clock:
                clock.backtest_til(2)
                await asyncio.wait_for(entered.wait(), 1)
                await service.close()
                assert exited.is_set()
                assert service.controller._control_loop_task.done()
                assert service.controller.order_safety_watchdog_task is None or service.controller.order_safety_watchdog_task.done()
                assert service.runner.listen_to_executor_actions_task.done()
                assert service._revoked
    finally:
        await service.close()
