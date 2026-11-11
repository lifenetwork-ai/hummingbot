"""Only read-only controller initialization may retry in place."""
import asyncio
import time
from test.hummingbot.cli.test_controller_stall_confirmation import running_host
from test.hummingbot.strategy_v2.controllers.test_life_mm import configuration, make_controller
from unittest.mock import AsyncMock

import pytest

from hummingbot.core.data_type.common import PositionMode


@pytest.mark.asyncio
async def test_public_metadata_timeout_retries_same_controller_without_order_side_effects(monkeypatch):
    item, _ = make_controller(configuration(scenario="follow_token", references=[{"pair": "BTC-USDT", "weight": "1"}]))
    source = item.market_data_provider.get_connector_with_fallback.return_value
    source.is_trading_required = False
    source._update_trading_rules = AsyncMock(side_effect=[TimeoutError("secret"), None])
    item.market_data_provider.acquire_order_books = AsyncMock(return_value={"BTC-USDT": True})
    monkeypatch.setattr(item, "_startup_retry_sleep", AsyncMock(), raising=False)
    await item.on_start()
    assert source._update_trading_rules.await_count == 2
    item.market_data_provider.acquire_order_books.assert_awaited_once()
    assert item.actions_queue.empty() and item._startup_retry is None
    source._set_trading_pair_leverage.assert_not_called()


@pytest.mark.asyncio
async def test_account_mode_read_can_retry_but_leverage_mutation_cannot(monkeypatch):
    item, _ = make_controller(configuration(connector_name="okx_perpetual"))
    source = item.market_data_provider.get_connector_with_fallback.return_value
    source._fetch_account_position_mode = AsyncMock(side_effect=[TimeoutError(), PositionMode.HEDGE])
    source._set_trading_pair_leverage = AsyncMock(side_effect=TimeoutError("uncertain mutation"))
    monkeypatch.setattr(item, "_startup_retry_sleep", AsyncMock(), raising=False)
    with pytest.raises(TimeoutError):
        await item.on_start()
    assert source._fetch_account_position_mode.await_count == 2
    source._set_trading_pair_leverage.assert_awaited_once()
    assert not item._account_ready


@pytest.mark.asyncio
async def test_permanent_http_rejection_is_not_retried(monkeypatch):
    item, _ = make_controller(configuration(connector_name="okx_perpetual"))
    source = item.market_data_provider.get_connector_with_fallback.return_value
    source._fetch_account_position_mode = AsyncMock(side_effect=OSError("HTTP status is 401. Error: private"))
    monkeypatch.setattr(item, "_startup_retry_sleep", AsyncMock(), raising=False)
    with pytest.raises(OSError):
        await item.on_start()
    source._fetch_account_position_mode.assert_awaited_once()
    assert item._startup_retry is None


@pytest.mark.asyncio
async def test_stop_interrupts_read_retry_and_releases_reference_subscription():
    item, _ = make_controller(configuration(scenario="follow_token", references=[{"pair": "BTC-USDT", "weight": "1"}]))
    source = item.market_data_provider.get_connector_with_fallback.return_value
    source.is_trading_required = False
    source._update_trading_rules = AsyncMock(side_effect=TimeoutError())
    retrying = asyncio.Event()

    async def sleep(delay):
        retrying.set()
        await asyncio.Event().wait()

    item._startup_retry_sleep = sleep
    item.market_data_provider.release_order_books = AsyncMock()
    item.start()
    await asyncio.wait_for(retrying.wait(), 1)
    assert item._startup_retry["reason_code"] == "controller_startup_read_retry"
    assert item._control_wait_until is not None
    item.stop()
    await asyncio.wait_for(item.wait_closed(), 1)
    source._update_trading_rules.assert_awaited_once()
    item.market_data_provider.release_order_books.assert_awaited_once()
    assert item.actions_queue.empty()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['position_mode_lock', 'position_mode_read', 'position_mode_change',
                                   'position_mode_confirmation', 'leverage_change', 'reference_metadata', 'reference_books'])
async def test_pending_startup_operation_has_a_bounded_wait_without_admitting_quotes_or_halting(monkeypatch, stage):
    public = stage.startswith('reference_')
    cfg = (configuration(scenario='follow_token', references=[{'pair': 'BTC-USDT', 'weight': '1'}]) if public
           else configuration(connector_name='okx_perpetual'))
    controller, _ = make_controller(cfg)
    source = controller.market_data_provider.get_connector_with_fallback.return_value
    source._fetch_account_position_mode = AsyncMock(return_value=PositionMode.HEDGE)
    source._trading_pair_position_mode_set = AsyncMock(return_value=(True, ''))
    source._set_trading_pair_leverage = AsyncMock(return_value=(True, ''))
    entered, release = asyncio.Event(), asyncio.Event()

    async def pending(result):
        entered.set()
        await release.wait()
        return result

    if stage == 'position_mode_lock':
        await source._set_position_mode_lock.acquire()
    elif stage == 'position_mode_read':
        async def read_mode():
            return await pending(PositionMode.HEDGE)
        source._fetch_account_position_mode.side_effect = read_mode
    elif public:
        source.is_trading_required = False
        source._update_trading_rules = AsyncMock()
        controller.market_data_provider.acquire_order_books = AsyncMock(return_value={'BTC-USDT': True})
        controller.market_data_provider.release_order_books = AsyncMock()
        if stage == 'reference_metadata':
            async def metadata():
                return await pending(None)
            source._update_trading_rules.side_effect = metadata
        else:
            async def books(*args):
                return await pending({'BTC-USDT': True})
            controller.market_data_provider.acquire_order_books.side_effect = books
    elif stage == 'position_mode_change':
        source._fetch_account_position_mode = AsyncMock(side_effect=[PositionMode.ONEWAY, PositionMode.HEDGE])

        async def change(*args):
            return await pending((True, ''))
        source._trading_pair_position_mode_set.side_effect = change
    elif stage == 'position_mode_confirmation':
        calls = []

        async def mode():
            calls.append(True)
            return PositionMode.ONEWAY if len(calls) == 1 else await pending(PositionMode.HEDGE)
        source._fetch_account_position_mode.side_effect = mode
    else:
        async def leverage(*args):
            return await pending((True, ''))
        source._set_trading_pair_leverage.side_effect = leverage
    host, _ = running_host(monkeypatch)
    host.hb.trading_core.strategy.controllers = {'perpetual': controller}
    controller.start()
    try:
        if stage == 'position_mode_lock':
            for _ in range(4):
                await asyncio.sleep(0)
        else:
            await asyncio.wait_for(entered.wait(), 1)
        # Observe six seconds into this same pending operation. Do not
        # fabricate a wait budget in the previous implementation.
        controller._control_progress_at = time.monotonic() - 6
        if controller._control_wait_until is not None:
            controller._control_wait_until -= 6
        assert host.runtime_problem() is None
        await host.observe_once()
        host.owner.require_recovery.assert_not_called()
        assert (public or not controller._account_ready) and controller.actions_queue.empty()
        assert controller._startup_stage == stage
        assert controller._control_wait_until > time.monotonic()
    finally:
        controller.stop()
        await controller.wait_closed()
        if stage == 'position_mode_lock' and source._set_position_mode_lock.locked():
            source._set_position_mode_lock.release()
        assert not source._set_position_mode_lock.locked()


@pytest.mark.asyncio
async def test_a_completed_slow_read_refreshes_progress_before_the_next_startup_stage():
    controller, _ = make_controller(configuration())
    started = time.monotonic()

    async def slow_read():
        controller._control_progress_at = started - 6
        return 'observed'

    assert await controller._startup_read(slow_read) == 'observed'
    assert controller._control_progress_at >= started
    assert controller._control_wait_until is None


@pytest.mark.asyncio
async def test_pending_leverage_timeout_still_fails_closed_without_replaying_the_write(monkeypatch):
    controller, _ = make_controller(configuration(connector_name='okx_perpetual'))
    source = controller.market_data_provider.get_connector_with_fallback.return_value
    source._fetch_account_position_mode = AsyncMock(return_value=PositionMode.HEDGE)
    source._set_trading_pair_leverage = AsyncMock()

    async def pending(*args):
        await asyncio.Event().wait()
    source._set_trading_pair_leverage.side_effect = pending
    monkeypatch.setattr(controller, 'STARTUP_OPERATION_TIMEOUT', .01)
    controller.start()
    await asyncio.wait_for(controller.wait_closed(), 1)
    assert controller._control_error == 'TimeoutError'
    assert controller._startup_stage == 'leverage_change'
    assert controller._control_wait_until is None and not controller._account_ready
    assert controller.actions_queue.empty()
    source._set_trading_pair_leverage.assert_awaited_once()
    assert not source._set_position_mode_lock.locked()


@pytest.mark.asyncio
async def test_pending_lock_timeout_does_not_unlock_someone_else_or_send_account_writes(monkeypatch):
    controller, _ = make_controller(configuration(connector_name='okx_perpetual'))
    source = controller.market_data_provider.get_connector_with_fallback.return_value
    await source._set_position_mode_lock.acquire()
    monkeypatch.setattr(controller, 'STARTUP_OPERATION_TIMEOUT', .01)
    controller.start()
    try:
        await asyncio.wait_for(controller.wait_closed(), 1)
        assert controller._control_error == 'TimeoutError'
        assert controller._startup_stage == 'position_mode_lock'
        assert source._set_position_mode_lock.locked()
        source._fetch_account_position_mode.assert_not_awaited()
        source._set_trading_pair_leverage.assert_not_called()
    finally:
        source._set_position_mode_lock.release()


@pytest.mark.asyncio
async def test_position_mode_confirmation_read_can_retry_without_repeating_the_acknowledged_write(monkeypatch):
    controller, _ = make_controller(configuration(connector_name='okx_perpetual'))
    source = controller.market_data_provider.get_connector_with_fallback.return_value
    source._fetch_account_position_mode = AsyncMock(side_effect=[PositionMode.ONEWAY, TimeoutError(), PositionMode.HEDGE])
    source._trading_pair_position_mode_set = AsyncMock(return_value=(True, ''))
    source._set_trading_pair_leverage = AsyncMock(return_value=(True, ''))
    monkeypatch.setattr(controller, '_startup_retry_sleep', AsyncMock())
    await controller.on_start()
    assert controller._startup_stage == 'ready' and controller._account_ready
    assert source._fetch_account_position_mode.await_count == 3
    source._trading_pair_position_mode_set.assert_awaited_once()
    source._set_trading_pair_leverage.assert_awaited_once()


@pytest.mark.asyncio
async def test_script_and_life_controller_have_one_acknowledged_account_setup_owner():
    from types import SimpleNamespace

    from scripts.v2_with_controllers import V2WithControllers

    item, _ = make_controller(configuration(connector_name='okx_perpetual'))
    source = item.market_data_provider.get_connector_with_fallback.return_value
    source._fetch_account_position_mode = AsyncMock(return_value=PositionMode.HEDGE)
    source._set_trading_pair_leverage = AsyncMock(return_value=(True, ''))
    background = []

    def native_background_leverage(trading_pair, leverage):
        background.append(asyncio.create_task(source._set_trading_pair_leverage(trading_pair, leverage)))

    source.set_leverage.side_effect = native_background_leverage
    strategy = SimpleNamespace(controllers={'perp': item}, connectors={'okx_perpetual': source},
                               max_pnl_by_controller={}, is_perpetual=lambda name: name == 'okx_perpetual')
    try:
        V2WithControllers.apply_initial_setting(strategy)
        await asyncio.sleep(0)
        await item.on_start()
        if background:
            await asyncio.gather(*background)
        source._set_trading_pair_leverage.assert_awaited_once_with(item.config.trading_pair, item.config.leverage)
        source.set_leverage.assert_not_called()
        source.set_position_mode.assert_not_called()
        assert item._account_ready and item._startup_stage == 'ready'
    finally:
        await asyncio.gather(*background, return_exceptions=True)


def test_generic_controller_keeps_native_script_account_setup():
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from scripts.v2_with_controllers import V2WithControllers

    config = configuration(connector_name='okx_perpetual')
    generic = SimpleNamespace(config=config)
    source = MagicMock()
    strategy = SimpleNamespace(controllers={'generic': generic}, connectors={'okx_perpetual': source},
                               max_pnl_by_controller={}, is_perpetual=lambda name: name == 'okx_perpetual')
    V2WithControllers.apply_initial_setting(strategy)
    source.set_leverage.assert_called_once_with(leverage=config.leverage, trading_pair=config.trading_pair)
    source.set_position_mode.assert_called_once_with(PositionMode.HEDGE)
