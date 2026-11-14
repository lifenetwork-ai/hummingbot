"""A later native cash observation must re-evaluate a covered filled entry."""
import asyncio
from decimal import Decimal
from test.hummingbot.strategy_v2.executors.test_terminal_position_checkpoint import fill, setup_checkpoint
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from controllers.market_making.life_mm import LifeMMController
from hummingbot.client.config.client_config_map import ClientConfigMap
from hummingbot.client.config.config_helpers import ClientConfigAdapter
from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.connector.okx_account_observations import record_balance_details
from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase


@pytest_asyncio.fixture
async def entry_account(monkeypatch):
    orchestrator, executor, order, recorder = setup_checkpoint(monkeypatch)
    old_strategy = executor._strategy
    executor.config = executor.config.model_copy(update={
        'connector_name': 'okx', 'mm_session_id': 'cash-session', 'mm_generation': 1})
    executor.connectors = {'okx': next(iter(executor.connectors.values()))}
    executor.get_in_flight_order = lambda *args: order
    exchange = OkxExchange(ClientConfigAdapter(ClientConfigMap()), 'offline-key', 'offline-secret', 'offline-pass',
                           trading_pairs=['LIFE-USDT'], trading_required=False)
    exchange.account_balance_observations = {
        asset: dict(cash_balance=Decimal('100'), observed_at=1) for asset in ('LIFE', 'USDT')}
    provider = SimpleNamespace(time=lambda: 4, get_connector_with_fallback=lambda name: exchange)
    controller = SimpleNamespace(
        config=SimpleNamespace(mm_session_id='cash-session', mm_generation=1, connector_name='okx',
                               trading_pair='LIFE-USDT', max_data_age_seconds=Decimal('10')),
        market_data_provider=provider, executors_update_event=asyncio.Event(),
        stop=MagicMock(), wait_closed=AsyncMock(), _session_admitted=True, _control_error=None)
    controller._entry_account_covers_fill = MethodType(LifeMMController._entry_account_covers_fill, controller)

    def initialize(strategy):
        strategy.controllers = {'maker': controller}

    with patch.object(StrategyV2Base, 'initialize_controllers', initialize), \
            patch.object(StrategyV2Base, 'initialize_candles'), \
            patch('hummingbot.strategy.strategy_v2_base._get_executor_orchestrator_class',
                  return_value=lambda **kw: orchestrator):
        strategy = StrategyV2Base({'okx': exchange}, StrategyV2ConfigBase())
    strategy.config = old_strategy.config
    strategy.market_data_provider = old_strategy.market_data_provider
    executor._strategy = orchestrator.strategy = strategy
    strategy.update_executors_info = MagicMock(wraps=strategy.update_executors_info)
    monkeypatch.setattr('hummingbot.connector.okx_account_observations.time.time', lambda: 3)

    def observe(asset, value='100'):
        record_balance_details(exchange, dict(ccy=asset, cashBal=value, availBal=value, eq=value, availEq=value))

    try:
        yield SimpleNamespace(strategy=strategy, controller=controller, executor=executor, order=order,
                              recorder=recorder, exchange=exchange, observe=observe)
    finally:
        if strategy._managed_report_handle is not None:
            strategy._managed_report_handle.cancel()
        task = getattr(strategy, '_spot_account_progress_task', None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        strategy.listen_to_executor_actions_task.cancel()
        await asyncio.gather(strategy.listen_to_executor_actions_task, return_exceptions=True)


async def completed_report(state):
    fill(state.order, 'full-fill', '1')
    state.order.current_state = OrderState.FILLED
    state.strategy.notify_executor_entry_filled(state.executor)
    await asyncio.wait_for(state.controller.executors_update_event.wait(), .3)
    state.controller.executors_update_event.clear()
    state.strategy.update_executors_info.reset_mock()


@pytest.mark.asyncio
async def test_account_after_completion_republishes_only_after_both_cash_assets_cover_fill(entry_account):
    state = entry_account
    await completed_report(state)
    state.observe('LIFE')
    await asyncio.sleep(.07)
    assert not state.controller.executors_update_event.is_set()
    state.strategy.update_executors_info.assert_not_called()
    state.observe('USDT')
    await asyncio.wait_for(state.controller.executors_update_event.wait(), .3)
    state.strategy.update_executors_info.assert_called_once()
    assert state.executor.is_active  # A report is not a financial hold/level release.
    assert not state.strategy.executor_orchestrator.positions_held['maker']
    assert not state.executor._held_position_orders
    state.recorder.checkpoint_held_positions.assert_not_called()


@pytest.mark.asyncio
async def test_account_burst_coalesces_and_does_not_publish_before_full_fill(entry_account):
    state = entry_account
    state.observe('LIFE')
    state.observe('USDT')
    await asyncio.sleep(.07)
    state.strategy.update_executors_info.assert_not_called()
    await completed_report(state)
    for _ in range(100):
        state.observe('LIFE')
        state.observe('USDT')
    await asyncio.wait_for(state.controller.executors_update_event.wait(), .3)
    state.strategy.update_executors_info.assert_called_once()


@pytest.mark.asyncio
async def test_repeated_cash_updates_do_not_republish_an_already_covered_fill(entry_account):
    state = entry_account
    await completed_report(state)
    state.observe('LIFE')
    state.observe('USDT')
    await asyncio.wait_for(state.controller.executors_update_event.wait(), .3)
    state.strategy.update_executors_info.reset_mock()
    state.controller.executors_update_event.clear()
    # Separate turns beyond the coalescing window: a steady account stream is
    # not permission to add a periodic report while the same entry is held.
    for _ in range(3):
        state.observe('LIFE')
        state.observe('USDT')
        await asyncio.sleep(.07)
    state.strategy.update_executors_info.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['-1', 'NaN', '', 'old'])
async def test_bad_cash_cannot_release_handoff_from_an_observation_signal(entry_account, monkeypatch, invalid):
    state = entry_account
    await completed_report(state)
    if invalid == 'old':
        monkeypatch.setattr('hummingbot.connector.okx_account_observations.time.time', lambda: 1)
    state.observe('LIFE', '100' if invalid == 'old' else invalid)
    state.observe('USDT')
    await asyncio.sleep(.07)
    assert not state.controller.executors_update_event.is_set()
    state.strategy.update_executors_info.assert_not_called()
    assert state.executor.is_active and not state.executor._held_position_orders


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['superseded', 'removed', 'unmanaged', 'stopped', 'connector', 'revoked', 'control_error'])
async def test_observation_cannot_wake_retired_or_foreign_entry_scope(entry_account, change):
    state = entry_account
    await completed_report(state)
    if change == 'superseded':
        state.controller.config.mm_generation = 2
    elif change == 'removed':
        state.strategy.executor_orchestrator.active_executors['maker'] = []
    elif change == 'unmanaged':
        state.executor.config = state.executor.config.model_copy(update={'mm_session_id': None})
    elif change == 'connector':
        state.strategy.connectors = {}
    elif change == 'revoked':
        state.controller._session_admitted = False
    elif change == 'control_error':
        state.controller._control_error = 'OSError'
    else:
        state.strategy._is_stop_triggered = True
    state.observe('LIFE')
    state.observe('USDT')
    await asyncio.sleep(.07)
    state.strategy.update_executors_info.assert_not_called()


@pytest.mark.asyncio
async def test_stop_drains_progress_listener_and_ignores_late_account_update(entry_account):
    state = entry_account
    await completed_report(state)
    state.strategy.executor_orchestrator.stop = AsyncMock()
    state.strategy.executor_orchestrator.store_all_executors = MagicMock()
    state.strategy.market_data_provider.wait_closed = AsyncMock()
    await state.strategy.on_stop()
    state.observe('LIFE')
    state.observe('USDT')
    await asyncio.sleep(.07)
    state.strategy.update_executors_info.assert_not_called()
    assert state.strategy._spot_account_progress_task is None


@pytest.mark.asyncio
async def test_failed_report_keeps_normal_clock_retry_despite_account_burst(entry_account):
    state = entry_account
    await completed_report(state)
    state.strategy.executor_orchestrator.get_all_reports = MagicMock(side_effect=OSError('synthetic report failure'))
    state.observe('LIFE')
    state.observe('USDT')
    await asyncio.sleep(.07)
    assert state.strategy.controller_reports_error == 'OSError'
    assert not state.controller.executors_update_event.is_set()
    state.strategy.update_executors_info.reset_mock()
    for _ in range(10):
        state.observe('LIFE')
        state.observe('USDT')
        await asyncio.sleep(.01)
    state.strategy.update_executors_info.assert_not_called()
    assert state.executor.is_active and not state.executor._held_position_orders
