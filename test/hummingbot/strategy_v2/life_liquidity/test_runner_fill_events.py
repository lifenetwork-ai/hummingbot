"""P5.4 runner events remain advisory until exchange fills match the ledger."""

from dataclasses import replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_controller_order_safety import _controller, _install
from test.hummingbot.strategy_v2.life_liquidity.test_order_gateway import FakeOkx
from test.hummingbot.strategy_v2.life_liquidity.test_runner_order_scope import _executor
from test.hummingbot.strategy_v2.life_liquidity.test_session import FakeClock
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from controllers.generic.life_liquidity import LifeLiquidityController
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount
from hummingbot.core.event.events import OrderCancelledEvent, OrderFilledEvent
from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.life_liquidity.order_gateway import SpotFill, SpotReservationReconciler
from hummingbot.strategy_v2.models.base import RunnableStatus


def _setup(tmp_path):
    controller: LifeLiquidityController = _controller()
    active, wal, ledger = _install(controller, tmp_path, FakeClock(), FakeOkx())
    wal._commit(replace(wal.get("i1"), slot_market="LIFE-USDT",
                        slot_side="BUY", slot_level=0))
    controller.install_order_safety(active, controller._order_safety_gateway, wal,
                                    reservations=ledger)
    runner = SimpleNamespace(controllers={"life": controller}, logger=lambda: MagicMock())
    return controller, runner, wal, ledger


def _fill(*, trade_id="trade-1", wire_id="wire-1", exchange_id="exchange-1",
          fee=Decimal("0.01")):
    return OrderFilledEvent(
        timestamp=1.0, order_id=wire_id, trading_pair="LIFE-USDT",
        trade_type=TradeType.BUY, order_type=OrderType.LIMIT_MAKER,
        price=Decimal("1"), amount=Decimal("0.4"),
        trade_fee=AddedToCostTradeFee(
            percent_token="USDT",
            flat_fees=[TokenAmount("USDT", fee)]),
        exchange_trade_id=trade_id, exchange_order_id=exchange_id)


def test_runner_fill_duplicate_waits_for_matching_exchange_ledger(tmp_path):
    controller, runner, wal, ledger = _setup(tmp_path)
    event = _fill()
    StrategyV2Base.did_fill_order(runner, event)
    StrategyV2Base.did_fill_order(runner, event)
    assert not controller.verify_runner_fill_events()
    assert ledger.trade_ids == set()
    assert wal.get("i1").state != "TERMINAL"

    reconciler = SpotReservationReconciler(wal, ledger, require_fees=True)
    assert reconciler.apply_fills("wire-1", (
        SpotFill("trade-1", Decimal("0.4"), Decimal("1"), "USDT", Decimal("-0.01")),
    ), Decimal("0.4"))
    wal.acknowledge("i1", "exchange-1")
    assert controller.verify_runner_fill_events()
    assert ledger.life_balance == Decimal("10.4")
    assert ledger.usdt_balance == Decimal("9.59")


def test_fill_after_executor_termination_is_still_observed_by_runner(tmp_path):
    controller, runner, _, ledger = _setup(tmp_path)
    executor = _executor()
    executor._order.order = SimpleNamespace(executed_amount_base=Decimal("0"))
    executor.process_order_canceled_event(
        None, None, OrderCancelledEvent(1.0, "wire-1", "exchange-1"))
    assert executor._order is None
    executor._status = RunnableStatus.TERMINATED
    event = _fill()
    executor.process_order_filled_event(None, None, event)
    StrategyV2Base.did_fill_order(runner, event)
    assert not controller.verify_runner_fill_events()
    assert ledger.trade_ids == set()


def test_cancel_event_never_releases_reservation_or_marks_wal_terminal(tmp_path):
    controller, runner, wal, ledger = _setup(tmp_path)
    StrategyV2Base.did_cancel_order(
        runner, OrderCancelledEvent(1.0, "wire-1", "exchange-1"))
    assert wal.get("i1").state != "TERMINAL"
    assert ledger.has_open_intent("i1")
    assert controller.has_unverified_runner_order_events()


def test_cancel_event_exchange_id_must_match_later_wal_observation(tmp_path):
    controller, runner, wal, _ = _setup(tmp_path)
    StrategyV2Base.did_cancel_order(
        runner, OrderCancelledEvent(1.0, "wire-1", "wrong-exchange-id"))
    wal.acknowledge("i1", "exchange-1")
    assert not controller.verify_runner_fill_events()
    assert controller.has_unverified_runner_order_events()


def test_missing_or_conflicting_fill_identity_fails_closed(tmp_path):
    controller, runner, _, ledger = _setup(tmp_path)
    StrategyV2Base.did_fill_order(runner, _fill(trade_id=""))
    StrategyV2Base.did_fill_order(runner, _fill(trade_id="trade-1"))
    assert not controller.verify_runner_fill_events()
    assert ledger.trade_ids == set()


def test_other_market_event_is_ignored_but_unknown_life_wire_is_latched(tmp_path):
    controller, runner, _, ledger = _setup(tmp_path)
    StrategyV2Base.did_fill_order(
        runner, _fill(wire_id="other")._replace(trading_pair="BTC-USDT"))
    assert not controller.has_unverified_runner_order_events()
    StrategyV2Base.did_fill_order(runner, _fill(wire_id="other"))
    assert controller.has_unverified_runner_order_events()
    assert ledger.trade_ids == set()


def test_conflicting_duplicate_fill_cannot_replace_first_observation(tmp_path):
    controller, runner, wal, ledger = _setup(tmp_path)
    StrategyV2Base.did_fill_order(runner, _fill())
    StrategyV2Base.did_fill_order(runner, _fill(fee=Decimal("0.02")))
    reconciler = SpotReservationReconciler(wal, ledger, require_fees=True)
    assert reconciler.apply_fills("wire-1", (
        SpotFill("trade-1", Decimal("0.4"), Decimal("1"), "USDT", Decimal("-0.01")),
    ), Decimal("0.4"))
    wal.acknowledge("i1", "exchange-1")
    assert not controller.verify_runner_fill_events()


@pytest.mark.asyncio
async def test_matching_runner_fill_is_cleared_only_after_exchange_reconciliation(tmp_path):
    controller, runner, wal, ledger = _setup(tmp_path)
    connector = controller._order_safety_gateway.connector
    active = controller._order_safety_manager
    connector.status["wire-1"] = {
        "clOrdId": "wire-1", "ordId": "exchange-1", "state": "canceled",
        "accFillSz": "0.4"}
    connector.fills["exchange-1"] = [{
        "tradeId": "trade-1", "ordId": "exchange-1", "fillSz": "0.4",
        "fillPx": "1", "feeCcy": "USDT", "fee": "-0.01"}]
    connector.cash_balances = {"LIFE": "10.4", "USDT": "9.59"}
    executor = _executor()
    executor._status = RunnableStatus.SHUTTING_DOWN
    controller._runner_orchestrator = SimpleNamespace(
        active_executors={"life": [executor]},
        get_stored_executors_by_controller=lambda _: ())
    controller._runner_halt_ok = True
    controller._order_safety_gateway.runner_scope_check = controller._runner_executor_scope_complete
    StrategyV2Base.did_fill_order(runner, _fill())
    StrategyV2Base.did_cancel_order(
        runner, OrderCancelledEvent(1.0, "wire-1", "exchange-1"))
    assert controller.has_unverified_runner_order_events()

    await controller._cancel_and_reconcile_orders()

    assert not controller.has_unverified_runner_order_events()
    assert wal.get("i1").state == "TERMINAL"
    assert ledger.life_balance == Decimal("10.4")
    assert ledger.usdt_balance == Decimal("9.59")
    assert active.current_session is not None


@pytest.mark.asyncio
async def test_disagreeing_runner_trade_blocks_terminal_release(tmp_path):
    controller, runner, wal, ledger = _setup(tmp_path)
    connector = controller._order_safety_gateway.connector
    connector.status["wire-1"] = {
        "clOrdId": "wire-1", "ordId": "exchange-1", "state": "canceled",
        "accFillSz": "0.4"}
    connector.fills["exchange-1"] = [{
        "tradeId": "different-trade", "ordId": "exchange-1", "fillSz": "0.4",
        "fillPx": "1", "feeCcy": "USDT", "fee": "-0.01"}]
    connector.cash_balances = {"LIFE": "10.4", "USDT": "9.59"}
    executor = _executor()
    executor._status = RunnableStatus.SHUTTING_DOWN
    controller._runner_orchestrator = SimpleNamespace(
        active_executors={"life": [executor]},
        get_stored_executors_by_controller=lambda _: ())
    controller._runner_halt_ok = True
    controller._order_safety_gateway.runner_scope_check = controller._runner_executor_scope_complete
    StrategyV2Base.did_fill_order(runner, _fill())

    await controller._cancel_and_reconcile_orders()

    assert controller.has_unverified_runner_order_events()
    assert wal.get("i1").state != "TERMINAL"
    assert ledger.requires_reconciliation("i1")
    assert ledger.reserved_usdt == Decimal("0.6")
