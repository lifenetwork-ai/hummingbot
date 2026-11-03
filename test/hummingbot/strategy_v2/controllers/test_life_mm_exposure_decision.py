"""One synchronous decision snapshot, with fresh public gates and await boundaries."""
import asyncio
from decimal import Decimal
from test.hummingbot.strategy_v2.controllers.test_life_mm import configuration, make_controller
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, StopExecutorAction


async def working_controller(name):
    controller, _ = make_controller(configuration(
        connector_name=name, buy_spreads=[.004, .009, .018], sell_spreads=[.004, .009, .018],
        max_base_inventory='1700', max_long_base='1700', max_short_base='1700', max_gross_base='2500'))
    controller._account_ready = True
    if name == 'okx_perpetual':
        controller.market_data_provider.get_order_book.side_effect = lambda *args: SimpleNamespace(
            snapshot_uid=1000000, last_diff_uid=1000000)
    await controller.update_processed_data()
    for side in ('buy', 'sell'):
        for index in range(3):
            level = f'{side}_{index}'
            config = controller.get_executor_config(level, Decimal('.099' if side == 'buy' else '.101'), Decimal('10'))
            controller.executors_info.append(SimpleNamespace(
                id=config.id, config=config, timestamp=1000, is_active=True, is_trading=False,
                status=RunnableStatus.RUNNING, custom_info={'level_id': level}, close_type=None, close_timestamp=None))
    return controller


@pytest.mark.asyncio
@pytest.mark.parametrize('name', ['okx', 'okx_perpetual'])
@pytest.mark.parametrize('report_ready', [True, False])
async def test_healthy_cycle_reads_exposure_once_without_creating_duplicate_quotes(name, report_ready):
    controller = await working_controller(name)
    if report_ready:
        controller.executors_update_event.set()
    else:
        controller.executors_update_event.clear()
    connector = controller.market_data_provider.get_connector_with_fallback.return_value
    connector.get_balance.reset_mock()
    with patch.object(controller, '_exposure', wraps=controller._exposure) as reads:
        await controller.control_task()
    assert controller.actions_queue.empty()
    assert reads.call_count == 1
    if name == 'okx':
        assert connector.get_balance.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('name', ['okx', 'okx_perpetual'])
async def test_decisions_and_public_risk_checks_do_not_reuse_a_previous_snapshot(name):
    controller = await working_controller(name)
    connector = controller.market_data_provider.get_connector_with_fallback.return_value
    with patch.object(controller, '_exposure', wraps=controller._exposure) as reads:
        assert controller.determine_executor_actions() == []
        if name == 'okx':
            connector.get_balance.side_effect = lambda asset: Decimal('1800')
        else:
            from hummingbot.core.data_type.common import PositionSide
            connector.account_positions = {'long': SimpleNamespace(
                trading_pair='LIFE-USDT', position_side=PositionSide.LONG, amount=Decimal('1800'))}
        assert not controller.exposure_within_caps()
        stops = controller.determine_executor_actions()
        assert len(stops) == 6 and all(isinstance(action, StopExecutorAction) and action.keep_position for action in stops)
        assert reads.call_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', ['NaN', 'Infinity', '-1'])
@pytest.mark.parametrize('report_ready', [True, False])
async def test_invalid_exposure_still_cancels_even_when_executor_report_is_not_ready(bad, report_ready):
    controller = await working_controller('okx')
    connector = controller.market_data_provider.get_connector_with_fallback.return_value
    connector.get_balance.side_effect = lambda asset: Decimal(bad)
    controller.executors_update_event.set() if report_ready else controller.executors_update_event.clear()
    await controller.control_task()
    stops = controller.actions_queue.get_nowait()
    assert len(stops) == 6 and all(isinstance(action, StopExecutorAction) and action.keep_position for action in stops)
    assert controller.quote_diagnostics['reasons']['BUY'] == ['inventory_limit']


@pytest.mark.asyncio
async def test_queue_await_does_not_keep_a_stale_snapshot_for_a_later_public_check(monkeypatch):
    controller = await working_controller('okx')
    controller.executors_info = []
    controller.executors_update_event.set()
    entered, release = asyncio.Event(), asyncio.Event()

    async def queue(actions):
        assert actions and all(isinstance(action, CreateExecutorAction) for action in actions)
        entered.set()
        await release.wait()

    monkeypatch.setattr(controller, 'send_actions', queue)
    task = asyncio.create_task(controller.control_task())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        connector = controller.market_data_provider.get_connector_with_fallback.return_value
        connector.get_balance.side_effect = lambda asset: Decimal('1800')
        assert not controller.exposure_within_caps()
        assert controller.create_actions_proposal() == []
    finally:
        release.set()
        await asyncio.wait_for(task, 1)
