"""Safety progress must survive a stalled runner, feed, and create queue."""

import asyncio
from test.hummingbot.strategy_v2.life_liquidity.test_controller_order_safety import _install
from test.hummingbot.strategy_v2.life_liquidity.test_order_gateway import FakeOkx
from test.hummingbot.strategy_v2.life_liquidity.test_session import FakeClock
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from controllers.generic.life_liquidity import LifeLiquidityConfig, LifeLiquidityController
from hummingbot.connector.exchange.okx import okx_constants, okx_web_utils
from hummingbot.strategy.strategy_v2_base import StrategyV2Base


async def _until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait(), timeout=2)


@pytest.mark.asyncio
async def test_watchdog_cancels_without_runner_tick_but_does_not_claim_runner_scope(tmp_path):
    clock = FakeClock()
    connector = FakeOkx()
    connector.status["wire-1"] = {"clOrdId": "wire-1", "ordId": "exchange-1",
                                  "state": "canceled", "accFillSz": "0"}
    config = LifeLiquidityConfig.model_construct(
        id="life", safety_watchdog_interval_ms=10,
        cancel_retry_interval_ms=1000, cancel_max_requests_per_cycle=2)
    controller = LifeLiquidityController(config, MagicMock(), asyncio.Queue(maxsize=1))
    active, wal, reservations = _install(controller, tmp_path, clock, connector)
    await controller.actions_queue.put([object()])  # A full create queue cannot hold up safety.
    clock.advance(10)
    try:
        controller.start()
        await _until(lambda: controller.order_safety_reason_code == "RECONCILIATION_INCOMPLETE")
        assert connector.cancels
        assert active.state in ("EXPIRED", "TRANSITIONING")
        assert wal.get("i1").state != "TERMINAL"
        assert reservations.requires_reconciliation("i1")
        assert controller.order_safety_watchdog_task is not None
        assert not controller.order_safety_watchdog_task.done()
    finally:
        controller.stop()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_watchdog_reconciles_after_runner_stops_ticking_with_feed_unready(tmp_path):
    clock = FakeClock()
    connector = FakeOkx()
    connector.status["wire-1"] = {"clOrdId": "wire-1", "ordId": "exchange-1",
                                  "state": "live", "accFillSz": "0"}
    connector.open_pages[None] = [{"clOrdId": "wire-1", "ordId": "exchange-1",
                                   "instId": "LIFE-USDT", "state": "live"}]
    config = LifeLiquidityConfig.model_construct(
        id="life", safety_watchdog_interval_ms=10,
        cancel_retry_interval_ms=1000, cancel_max_requests_per_cycle=2)
    controller = LifeLiquidityController(config, MagicMock(), asyncio.Queue())
    active, wal, _ = _install(controller, tmp_path, clock, connector)
    with patch("hummingbot.strategy.strategy_v2_base._get_executor_orchestrator_class",
               return_value=lambda **kwargs: MagicMock()):
        runner = StrategyV2Base({}, config=None)
    runner.executor_orchestrator.active_executors = {"life": []}
    runner.executor_orchestrator.get_stored_executors_by_controller.return_value = ()
    runner.controllers = {"life": controller}
    runner.connectors = {"okx": SimpleNamespace(ready=False, name="okx")}
    runner.market_data_provider = SimpleNamespace(ready=False)
    try:
        runner.tick(0)  # Attach live and stored executor scope once.
        await controller.order_safety_task
        assert wal.get("i1").state != "TERMINAL"
        controller.start()
        clock.advance(10)
        connector.status["wire-1"]["state"] = "canceled"
        connector.open_pages[None] = []
        await _until(lambda: wal.get("i1").state == "TERMINAL")
        assert active.state == "TRANSITIONING"
        assert runner.ready_to_trade is False
        runner.executor_orchestrator.execute_actions.assert_not_called()
    finally:
        controller.stop()
        runner.listen_to_executor_actions_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner.listen_to_executor_actions_task
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_okx_cancel_capacity_is_independent_of_exhausted_create_capacity():
    throttler = okx_web_utils.create_throttler()
    for _ in range(20):
        async with throttler.execute_task(okx_constants.OKX_PLACE_ORDER_PATH):
            pass
    pending_create = asyncio.create_task(
        throttler.execute_task(okx_constants.OKX_PLACE_ORDER_PATH).__aenter__())
    try:
        await asyncio.sleep(0)
        assert not pending_create.done()
        async with asyncio.timeout(0.5):
            async with throttler.execute_task(okx_constants.OKX_ORDER_CANCEL_PATH):
                pass
        assert not pending_create.done()
    finally:
        pending_create.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending_create


@pytest.mark.asyncio
async def test_watchdog_restarts_after_immediate_stop_without_losing_polling():
    config = LifeLiquidityConfig.model_construct(
        id="life", safety_watchdog_interval_ms=10,
        cancel_retry_interval_ms=1000, cancel_max_requests_per_cycle=2)
    controller = LifeLiquidityController(config, MagicMock(), asyncio.Queue())
    try:
        controller.start()
        first = controller.order_safety_watchdog_task
        controller.stop()
        controller.start()
        second = controller.order_safety_watchdog_task
        assert second is not first
        await asyncio.sleep(0)
        assert first.cancelled()
        assert not second.done()
    finally:
        controller.stop()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_attached_session_requires_live_watchdog_and_blocks_create_on_expiry(tmp_path):
    clock = FakeClock()
    connector = FakeOkx()
    config = LifeLiquidityConfig.model_construct(
        id="life", safety_watchdog_interval_ms=10,
        cancel_retry_interval_ms=1000, cancel_max_requests_per_cycle=2)
    controller = LifeLiquidityController(config, MagicMock(), asyncio.Queue())
    active, _, _ = _install(controller, tmp_path, clock, connector)
    controller.config_update_state.order_permission = lambda: True
    controller.listing_gate = SimpleNamespace(metadata_ready=True)
    controller.book_ready = True
    controller._live_spot_book_ready = lambda: True
    controller.continuous_gate.ready = True
    controller.snapshot_gate.permit = lambda: True
    controller.continuity_gate.permit = lambda *_: True
    controller.trading_permissions_ready = lambda: True
    try:
        assert not controller.allow_create_executor_actions()
        controller.start()
        assert active.state == "ACTIVE"
        assert controller.allow_create_executor_actions()
        clock.advance(10)
        controller.on_safety_tick(10)
        assert not controller.allow_create_executor_actions()
    finally:
        controller.stop()
        await asyncio.sleep(0)


def test_start_schedules_watchdog_on_controller_loop_before_it_runs():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        config = LifeLiquidityConfig.model_construct(id="life", safety_watchdog_interval_ms=10)
        controller = LifeLiquidityController(config, MagicMock(), asyncio.Queue())
        controller.start()
        assert controller.order_safety_watchdog_task.get_loop() is loop
        controller.stop()
        loop.run_until_complete(asyncio.sleep(0))
        assert controller.order_safety_watchdog_task.cancelled()
    finally:
        loop.close()
        asyncio.set_event_loop(None)


@pytest.mark.asyncio
async def test_recovered_session_rejects_watchdog_without_cancel_policy(tmp_path):
    config = LifeLiquidityConfig.model_construct(id="life", safety_watchdog_interval_ms=10)
    controller = LifeLiquidityController(config, MagicMock(), asyncio.Queue())
    _install(controller, tmp_path, FakeClock(), FakeOkx())
    try:
        controller.start()
        assert controller.order_safety_watchdog_task is None
        assert controller.order_safety_reason_code == "ORDER_SAFETY_CANCEL_POLICY_UNCONFIGURED"
        assert not controller.allow_create_executor_actions()
    finally:
        controller.stop()
        await asyncio.sleep(0)
