"""P4 economic attribution begins only after exchange fill reconciliation."""

from datetime import datetime, timezone
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_runner_fill_events import _setup
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hummingbot.strategy_v2.life_liquidity.fill_attribution import IndependentFillObservation, ReconciledFillAttributor
from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetLedger
from hummingbot.strategy_v2.life_liquidity.order_gateway import OkxSpotOrderGateway, SpotFill, SpotReservationReconciler
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL

AT = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
AT_MS = int(AT.timestamp() * 1000)


def _attributor(tmp_path, wal, reservations, *, restore=False, loss_limit=Decimal("1")):
    session_id = wal.get("i1").session_id
    loss = LossBudgetLedger(
        tmp_path / "loss_budget.json", campaign_id="life", campaign_limit_quote=loss_limit,
        day_limit_quote=loss_limit, session_limit_quote=loss_limit)
    if not restore:
        loss.record("synthetic-opening-anchor", Decimal("0"), session_id=session_id,
                    at_utc=AT)
    attributor = ReconciledFillAttributor(
        tmp_path / "fill_attribution.json", wal=wal, reservations=reservations,
        loss_budget=loss, opening_life=Decimal("10"), opening_usdt=Decimal("10"),
        opening_independent_price_usdt=Decimal("1"),
        independent_value=lambda _: IndependentFillObservation(
            Decimal("0.9"), AT_MS, AT_MS + 100, "independent_market"),
        max_reference_skew_ms=200, create=not restore)
    return attributor, loss


def test_reconciled_partial_fill_attributed_once_and_restored(tmp_path):
    _, _, wal, reservations = _setup(tmp_path)
    attribution, loss = _attributor(tmp_path, wal, reservations)
    fill = SpotFill("trade-1", Decimal("0.4"), Decimal("1"), "USDT", Decimal("-0.01"), AT_MS)
    assert not attribution.apply("wire-1", (fill,))
    assert SpotReservationReconciler(wal, reservations, require_fees=True).apply_fills(
        "wire-1", (fill,), Decimal("0.4"))
    assert attribution.apply("wire-1", (fill,))
    assert attribution.ready()
    assert attribution.apply("wire-1", (fill,))
    assert attribution.capital().life_balance == reservations.life_balance == Decimal("10.4")
    assert attribution.capital().usdt_balance == reservations.usdt_balance == Decimal("9.59")
    status = loss.verified_status(session_id=wal.get("i1").session_id, at_utc=AT)
    assert status.session_loss_quote == Decimal("0.05")
    assert status.day_loss_quote == Decimal("0.05")
    assert status.campaign_loss_quote == Decimal("0.05")
    restored_reservations = type(reservations).restore(reservations.path, limits=reservations.limits)
    restored, _ = _attributor(tmp_path, wal, restored_reservations, restore=True)
    assert restored.ready()
    assert restored.apply("wire-1", (fill,))


def test_missing_fee_or_independent_value_keeps_fill_unattributed(tmp_path):
    _, _, wal, reservations = _setup(tmp_path)
    attribution, _ = _attributor(tmp_path, wal, reservations)
    fill = SpotFill("trade-1", Decimal("0.4"), Decimal("1"), "LIFE", Decimal("-0.01"), AT_MS)
    assert SpotReservationReconciler(wal, reservations, require_fees=True).apply_fills(
        "wire-1", (fill,), Decimal("0.4"))
    assert not attribution.apply("wire-1", (fill,))
    assert not attribution.ready()


def test_missing_independent_value_after_reconciliation_keeps_risk_blocked(tmp_path):
    _, _, wal, reservations = _setup(tmp_path)
    attribution, _ = _attributor(tmp_path, wal, reservations)
    fill = SpotFill("trade-1", Decimal("0.4"), Decimal("1"), "USDT", Decimal("-0.01"), AT_MS)
    assert SpotReservationReconciler(wal, reservations, require_fees=True).apply_fills(
        "wire-1", (fill,), Decimal("0.4"))
    attribution.independent_value = lambda _: None

    assert not attribution.apply("wire-1", (fill,))
    assert not attribution.ready()


def test_late_independent_observation_keeps_fill_unattributed(tmp_path):
    _, _, wal, reservations = _setup(tmp_path)
    attribution, _ = _attributor(tmp_path, wal, reservations)
    fill = SpotFill("trade-1", Decimal("0.4"), Decimal("1"), "USDT", Decimal("-0.01"), AT_MS)
    assert SpotReservationReconciler(wal, reservations, require_fees=True).apply_fills(
        "wire-1", (fill,), Decimal("0.4"))
    attribution.independent_value = lambda _: IndependentFillObservation(
        Decimal("0.9"), AT_MS, AT_MS + 201, "independent_market")

    assert not attribution.apply("wire-1", (fill,))
    assert not attribution.ready()


def test_benchmark_model_value_cannot_attribute_execution_loss(tmp_path):
    _, _, wal, reservations = _setup(tmp_path)
    attribution, _ = _attributor(tmp_path, wal, reservations)
    fill = SpotFill("trade-1", Decimal("0.4"), Decimal("1"), "USDT", Decimal("-0.01"), AT_MS)
    assert SpotReservationReconciler(wal, reservations, require_fees=True).apply_fills(
        "wire-1", (fill,), Decimal("0.4"))
    attribution.independent_value = lambda _: IndependentFillObservation(
        Decimal("0.9"), AT_MS, AT_MS + 100, "benchmark_model")

    assert not attribution.apply("wire-1", (fill,))
    assert not attribution.ready()


def test_missing_fee_never_enters_reconciled_attribution(tmp_path):
    _, _, wal, reservations = _setup(tmp_path)
    attribution, _ = _attributor(tmp_path, wal, reservations)
    fill = SpotFill("trade-1", Decimal("0.4"), Decimal("1"), None, None, AT_MS)

    assert not SpotReservationReconciler(wal, reservations, require_fees=True).apply_fills(
        "wire-1", (fill,), Decimal("0.4"))
    assert not attribution.apply("wire-1", (fill,))


def test_controller_gateway_requires_fill_attribution_after_reservation_reconciliation(tmp_path):
    controller, _, wal, reservations = _setup(tmp_path)
    attribution, loss = _attributor(tmp_path, wal, reservations)
    controller.install_execution_loss_budget(
        loss, utc_clock=lambda: AT)
    controller.install_fill_attributor(attribution)
    fill = SpotFill("trade-1", Decimal("0.4"), Decimal("1"), "USDT", Decimal("-0.01"), AT_MS)

    assert controller._order_safety_gateway.apply_fills("wire-1", (fill,), Decimal("0.4"))
    assert controller._fill_attribution_ready()
    assert attribution.capital().execution_loss_quote == Decimal("0.05")


def test_crash_between_attribution_and_loss_write_is_replayable(tmp_path):
    _, _, wal, reservations = _setup(tmp_path)
    attribution, loss = _attributor(tmp_path, wal, reservations)
    fill = SpotFill("trade-1", Decimal("0.4"), Decimal("1"), "USDT", Decimal("-0.01"), AT_MS)
    assert SpotReservationReconciler(wal, reservations, require_fees=True).apply_fills(
        "wire-1", (fill,), Decimal("0.4"))
    with patch.object(loss, "record", side_effect=OSError("crash before loss commit")):
        assert not attribution.apply("wire-1", (fill,))
    assert not attribution.ready()

    restored_reservations = type(reservations).restore(reservations.path, limits=reservations.limits)
    restored, restored_loss = _attributor(tmp_path, wal, restored_reservations, restore=True)
    assert not restored.ready()
    assert restored.recover()
    assert restored.recover()
    assert restored_loss.verified_status(
        session_id=wal.get("i1").session_id,
        at_utc=AT).session_loss_quote == Decimal("0.05")


def test_recovery_does_not_write_loss_from_changed_durable_wal(tmp_path):
    _, _, wal, reservations = _setup(tmp_path)
    attribution, loss = _attributor(tmp_path, wal, reservations)
    fill = SpotFill("trade-1", Decimal("0.4"), Decimal("1"), "USDT", Decimal("-0.01"), AT_MS)
    assert SpotReservationReconciler(wal, reservations, require_fees=True).apply_fills(
        "wire-1", (fill,), Decimal("0.4"))
    with patch.object(loss, "record", side_effect=OSError("crash before loss commit")):
        assert not attribution.apply("wire-1", (fill,))
    assert IntentWAL(wal.path).mark_cancel_requested("i1")

    assert not attribution.recover()
    assert loss.verified_status(session_id=wal.get("i1").session_id, at_utc=AT).session_loss_quote == 0


def test_changed_durable_reservation_journal_revokes_attribution_readiness(tmp_path):
    _, _, wal, reservations = _setup(tmp_path)
    attribution, _ = _attributor(tmp_path, wal, reservations)
    fill = SpotFill("trade-1", Decimal("0.4"), Decimal("1"), "USDT", Decimal("-0.01"), AT_MS)
    assert SpotReservationReconciler(wal, reservations, require_fees=True).apply_fills(
        "wire-1", (fill,), Decimal("0.4"))
    assert attribution.apply("wire-1", (fill,))
    other = type(reservations).restore(reservations.path, limits=reservations.limits)
    other.record_cashflow("123", "USDT", Decimal("1"))

    assert not attribution.ready()


def test_stale_attribution_writer_cannot_overwrite_newer_fill(tmp_path):
    _, _, wal, reservations = _setup(tmp_path)
    attribution, _ = _attributor(tmp_path, wal, reservations)
    stale, _ = _attributor(tmp_path, wal, reservations, restore=True)
    fill = SpotFill("trade-1", Decimal("0.4"), Decimal("1"), "USDT", Decimal("-0.01"), AT_MS)
    assert SpotReservationReconciler(wal, reservations, require_fees=True).apply_fills(
        "wire-1", (fill,), Decimal("0.4"))
    assert attribution.apply("wire-1", (fill,))

    assert not stale.apply("wire-1", (fill,))
    assert not stale.ready()
    assert attribution.ready()


def test_changed_durable_wal_revokes_attribution_readiness(tmp_path):
    _, _, wal, reservations = _setup(tmp_path)
    attribution, _ = _attributor(tmp_path, wal, reservations)
    fill = SpotFill("trade-1", Decimal("0.4"), Decimal("1"), "USDT", Decimal("-0.01"), AT_MS)
    assert SpotReservationReconciler(wal, reservations, require_fees=True).apply_fills(
        "wire-1", (fill,), Decimal("0.4"))
    assert attribution.apply("wire-1", (fill,))
    assert IntentWAL(wal.path).mark_cancel_requested("i1")

    assert not attribution.ready()


def test_unattributed_reservation_fill_blocks_controller_create(tmp_path):
    controller, _, wal, reservations = _setup(tmp_path)
    attribution, loss = _attributor(tmp_path, wal, reservations)
    controller.install_execution_loss_budget(
        loss, utc_clock=lambda: AT)
    controller.install_fill_attributor(attribution)
    controller._spot_quote_gates_ready = lambda: True
    controller.order_safety_watchdog_task = SimpleNamespace(done=lambda: False)
    assert controller.allow_create_executor_actions()
    fill = SpotFill("trade-1", Decimal("0.4"), Decimal("1"), "USDT", Decimal("-0.01"), AT_MS)
    assert SpotReservationReconciler(wal, reservations, require_fees=True).apply_fills(
        "wire-1", (fill,), Decimal("0.4"))
    assert not controller.allow_create_executor_actions()
    assert controller.fill_attribution_reason_code == "FILL_ATTRIBUTION_UNRESOLVED"


def test_attributed_fill_exhausts_queue_loss_budget(tmp_path):
    controller, _, wal, reservations = _setup(tmp_path)
    attribution, loss = _attributor(tmp_path, wal, reservations, loss_limit=Decimal("0.05"))
    controller.install_execution_loss_budget(loss, utc_clock=lambda: AT)
    controller.install_fill_attributor(attribution)
    controller._spot_quote_gates_ready = lambda: True
    controller.order_safety_watchdog_task = SimpleNamespace(done=lambda: False)
    assert controller.allow_create_executor_actions()
    fill = SpotFill("trade-1", Decimal("0.4"), Decimal("1"), "USDT", Decimal("-0.01"), AT_MS)
    assert controller._order_safety_gateway.apply_fills("wire-1", (fill,), Decimal("0.4"))

    assert attribution.ready()
    assert not controller.allow_create_executor_actions()
    assert controller.execution_loss_reason_code == "EXECUTION_LOSS_BUDGET_EXHAUSTED"


def test_okx_fill_time_is_carried_into_authenticated_fill_snapshot():
    fills = OkxSpotOrderGateway._fills({"code": "0", "data": [{
        "tradeId": "t1", "ordId": "exchange-1", "fillSz": "0.4", "fillPx": "1",
        "feeCcy": "USDT", "fee": "-0.01", "fillTime": str(AT_MS)}]},
        "exchange-1", Decimal("0.4"))
    assert fills == (SpotFill("t1", Decimal("0.4"), Decimal("1"),
                              "USDT", Decimal("-0.01"), AT_MS),)
    with pytest.raises(ValueError, match="ORDER_FILL_TIME_UNTRUSTED"):
        OkxSpotOrderGateway._fills({"code": "0", "data": [{
            "tradeId": "t1", "ordId": "exchange-1", "fillSz": "0.4", "fillPx": "1",
            "feeCcy": "USDT", "fee": "-0.01", "fillTime": True}]},
            "exchange-1", Decimal("0.4"))


@pytest.mark.asyncio
async def test_exchange_reconcile_routes_partial_fill_through_attribution(tmp_path):
    controller, _, wal, reservations = _setup(tmp_path)
    attribution, loss = _attributor(tmp_path, wal, reservations)
    controller.install_execution_loss_budget(
        loss, utc_clock=lambda: AT)
    controller.install_fill_attributor(attribution)
    connector = controller._order_safety_gateway.connector
    connector.status["wire-1"] = {
        "clOrdId": "wire-1", "ordId": "exchange-1", "state": "partially_filled",
        "accFillSz": "0.4"}
    connector.fills["exchange-1"] = [{
        "tradeId": "trade-1", "ordId": "exchange-1", "fillSz": "0.4",
        "fillPx": "1", "feeCcy": "USDT", "fee": "-0.01", "fillTime": str(AT_MS)}]
    connector.cash_balances = {"LIFE": "10.4", "USDT": "9.59"}
    session = controller._order_safety_manager.current_session

    result = await controller._order_safety_gateway.reconcile(session.session_id, session.epoch)

    assert result.trade_events_reconciled
    assert attribution.ready()
    assert loss.verified_status(
        session_id=session.session_id,
        at_utc=AT).session_loss_quote == Decimal("0.05")


@pytest.mark.asyncio
async def test_missing_independent_value_prevents_terminal_release(tmp_path):
    controller, _, wal, reservations = _setup(tmp_path)
    attribution, loss = _attributor(tmp_path, wal, reservations)
    attribution.independent_value = lambda _: None
    controller.install_execution_loss_budget(
        loss, utc_clock=lambda: AT)
    controller.install_fill_attributor(attribution)
    connector = controller._order_safety_gateway.connector
    connector.status["wire-1"] = {
        "clOrdId": "wire-1", "ordId": "exchange-1", "state": "canceled",
        "accFillSz": "0.4"}
    connector.fills["exchange-1"] = [{
        "tradeId": "trade-1", "ordId": "exchange-1", "fillSz": "0.4",
        "fillPx": "1", "feeCcy": "USDT", "fee": "-0.01", "fillTime": str(AT_MS)}]
    connector.cash_balances = {"LIFE": "10.4", "USDT": "9.59"}
    session = controller._order_safety_manager.current_session

    result = await controller._order_safety_gateway.reconcile(session.session_id, session.epoch)

    assert not result.trade_events_reconciled
    assert wal.get("i1").state != "TERMINAL"
    assert reservations.has_open_intent("i1")
    assert not attribution.ready()
