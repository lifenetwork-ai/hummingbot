"""Synthetic service quotes spend a durable subsidy at the V2 send boundary."""

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_executor_protected_send import _setup
from test.hummingbot.strategy_v2.life_liquidity.test_final_quote_send import _attach_quote_planner

import pytest

from hummingbot.core.data_type.common import OrderType
from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
from hummingbot.strategy_v2.life_liquidity.config import SubsidyBudgetConfig
from hummingbot.strategy_v2.life_liquidity.economics import EconomicPolicy, SubsidyBudgetLedger
from hummingbot.strategy_v2.life_liquidity.fill_attribution import IndependentFillObservation, ReconciledFillAttributor
from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetLedger
from hummingbot.strategy_v2.life_liquidity.order_gateway import SpotFill, SpotReservationReconciler
from hummingbot.strategy_v2.life_liquidity.spot_quotes import QuoteCosts
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL

NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
NOW_MS = int(NOW.timestamp() * 1000)
D = Decimal


def _service(tmp_path):
    controller, template, connector, wal, reservations, _ = _setup(tmp_path)
    limits = SubsidyBudgetConfig(campaign=D("1"), day=D("0.1"), session=D("0.05"))
    economics = controller.config.strategy.economics.model_copy(update={
        "objective": "liquidity_service", "subsidy_budget_quote": limits})
    controller.config = controller.config.model_copy(update={
        "strategy": controller.config.strategy.model_copy(update={"economics": economics})})
    path = tmp_path / "subsidy_budget.json"
    book = SubsidyBudgetLedger(path, campaign_id="life-launch",
                               campaign_limit_quote=limits.campaign,
                               day_limit_quote=limits.day,
                               session_limit_quote=limits.session)
    book.initialize_empty()
    state, _ = _attach_quote_planner(
        controller, template, wal, reservations, propose=False,
        subsidy_budget=book, utc_clock=lambda: NOW)
    current = controller._order_safety_manager.current_session
    state["snapshot"] = replace(
        state["snapshot"], policy=EconomicPolicy("liquidity_service", D("0")),
        costs=QuoteCosts(D("0.02"), D("0"), D("0"), D("0"), D("0"), D("0")),
        subsidy_remaining_quote=book.verified_status(
            session_id=current.session_id, at_utc=NOW).available_quote)
    return controller, template, connector, wal, reservations, book, state


def _queue_service(tmp_path):
    controller, template, connector, wal, reservations, book, state = _service(tmp_path)
    actions = controller.determine_executor_actions()
    assert len(actions) == 1
    executor = OrderExecutor(template._strategy, actions[0].executor_config)
    executor.get_order_price = lambda: executor.config.price
    executor.place_open_order()
    wire = {"clOrdId": executor._order.order_id, "instId": "LIFE-USDT",
            "side": "buy", "ordType": "post_only", "tdMode": "cash",
            "px": str(executor.config.price), "sz": str(executor.config.amount)}
    return controller, connector, wal, reservations, book, state, executor, wire


def test_service_quote_reserves_exact_cost_before_send_and_survives_restart(tmp_path):
    controller, connector, wal, reservations, book, state, executor, wire = _queue_service(tmp_path)
    assert book.campaign_committed_quote == D("0.0098")
    assert SubsidyBudgetLedger(
        book.path, campaign_id="life-launch", campaign_limit_quote=D("1"),
        day_limit_quote=D("0.1"), session_limit_quote=D("0.05")
    ).campaign_committed_quote == D("0.0098")
    connector.sent[0]["pre_send_check"](wire)
    assert wal.get(executor.config.id).state == "SEND_UNKNOWN"
    assert reservations.has_open_intent(executor.config.id)
    assert controller._quote_action_planner.subsidy_reason_code == "SUBSIDY_BUDGET_READY"


def test_optimistic_snapshot_and_missing_journal_block_service_quote(tmp_path):
    controller, _, _, _, _, book, state = _service(tmp_path)
    state["snapshot"] = replace(state["snapshot"], subsidy_remaining_quote=D("100"))
    assert controller.determine_executor_actions() == []
    assert controller._quote_action_planner.subsidy_reason_code == "SUBSIDY_SNAPSHOT_MISMATCH"
    state["snapshot"] = replace(state["snapshot"], subsidy_remaining_quote=D("0.05"))
    book.path.unlink()
    assert controller.determine_executor_actions() == []
    assert controller._quote_action_planner.subsidy_reason_code == "SUBSIDY_BUDGET_UNAVAILABLE"


def test_changed_subsidy_journal_revokes_final_send_and_keeps_reservation(tmp_path):
    _, connector, wal, reservations, book, _, executor, wire = _queue_service(tmp_path)
    stale = SubsidyBudgetLedger(
        book.path, campaign_id="life-launch", campaign_limit_quote=D("1"),
        day_limit_quote=D("0.1"), session_limit_quote=D("0.05"))
    current = wal.get(executor.config.id)
    assert stale.reserve("other", D("0.01"), session_id=current.session_id, at_utc=NOW)
    with pytest.raises(PermissionError, match="SEND_PERMISSION_REVOKED"):
        connector.sent[0]["pre_send_check"](wire)
    assert wal.get(executor.config.id).state == "SEND_UNKNOWN"
    assert reservations.has_open_intent(executor.config.id)


def test_unknown_send_keeps_subsidy_until_durable_no_send_proof(tmp_path):
    _, _, wal, reservations, book, _, executor, _ = _queue_service(tmp_path)
    intent_id = executor.config.id
    with pytest.raises(ValueError, match="SUBSIDY_RELEASE_UNPROVEN"):
        book.release_unsent(intent_id, wal=wal, reservations=reservations)
    record = wal.get(intent_id)
    wal.abort_rejected_at_send_boundary(
        intent_id, client_order_id=record.client_order_id,
        session_id=record.session_id, epoch=record.epoch)
    with pytest.raises(ValueError, match="SUBSIDY_RELEASE_UNPROVEN"):
        book.release_unsent(intent_id, wal=wal, reservations=reservations)
    reservations.abort_unsent(
        intent_id, session_id=record.session_id, epoch=record.epoch, wal=wal)
    assert book.release_unsent(intent_id, wal=wal, reservations=reservations)
    assert book.campaign_committed_quote == D("0")
    assert not book.release_unsent(intent_id, wal=wal, reservations=reservations)


def test_changed_config_budget_revokes_queued_service_order(tmp_path):
    controller, connector, wal, reservations, _, _, executor, wire = _queue_service(tmp_path)
    economics = controller.config.strategy.economics.model_copy(update={
        "subsidy_budget_quote": SubsidyBudgetConfig(
            campaign=D("2"), day=D("0.2"), session=D("0.1"))})
    controller.config = controller.config.model_copy(update={
        "strategy": controller.config.strategy.model_copy(update={"economics": economics})})
    with pytest.raises(PermissionError, match="SEND_PERMISSION_REVOKED"):
        connector.sent[0]["pre_send_check"](wire)
    assert controller._quote_action_planner.subsidy_reason_code == "SUBSIDY_BUDGET_CONFIG_MISMATCH"
    assert wal.get(executor.config.id).state == "SEND_UNKNOWN"
    assert reservations.has_open_intent(executor.config.id)


def test_lost_ack_cannot_retry_and_spend_subsidy_twice(tmp_path):
    controller, _, wal, _, book, _, executor, _ = _queue_service(tmp_path)
    with pytest.raises(ValueError, match="RECONCILE_BEFORE_RETRY"):
        controller._protected_spot_sender.submit(
            executor.config, amount=executor.config.amount,
            price=executor.config.price, order_type=OrderType.LIMIT_MAKER)
    assert wal.get(executor.config.id).state == "SEND_UNKNOWN"
    assert book.campaign_committed_quote == D("0.0098")


def test_protected_service_send_partial_fill_charges_loss_and_subsidy_after_restart(tmp_path):
    controller, _, wal, reservations, subsidy, state, executor, wire = _queue_service(tmp_path)
    session_id = wal.get(executor.config.id).session_id
    loss = LossBudgetLedger(
        tmp_path / "loss_budget.json", campaign_id="life",
        campaign_limit_quote=D("1"), day_limit_quote=D("1"), session_limit_quote=D("1"))
    loss.record("opening", D("0"), session_id=session_id, at_utc=NOW)

    def independent(_):
        return IndependentFillObservation(
            D("0.9"), NOW_MS, NOW_MS + 100, "independent_market")
    attribution = ReconciledFillAttributor(
        tmp_path / "fill_attribution.json", wal=wal, reservations=reservations,
        loss_budget=loss, subsidy_budget=subsidy,
        opening_life=D("10"), opening_usdt=D("10"),
        opening_independent_price_usdt=D("1"), independent_value=independent,
        max_reference_skew_ms=200, create=True)
    controller._order_safety_gateway.apply_fills = SpotReservationReconciler(
        wal, reservations, require_fees=True).apply_fills
    controller.install_execution_loss_budget(loss, utc_clock=lambda: NOW)
    controller.install_fill_attributor(attribution)
    fill = SpotFill("trade-service", D("0.4"), executor.config.price,
                    "USDT", D("-0.01"), NOW_MS)
    assert controller._order_safety_gateway.apply_fills(wire["clOrdId"], (fill,), D("0.4"))
    assert attribution.ready()
    assert subsidy.campaign_committed_quote == D("0.046")
    assert loss.verified_status(session_id=session_id, at_utc=NOW).campaign_loss_quote == D("0.046")
    planner = controller._quote_action_planner
    assert planner._current_snapshot(controller._order_safety_manager.current_session) is None
    assert planner.subsidy_reason_code == "SUBSIDY_SNAPSHOT_MISMATCH"
    restored_reservations = type(reservations).restore(
        reservations.path, limits=reservations.limits)
    restored_subsidy = SubsidyBudgetLedger(
        subsidy.path, campaign_id="life-launch", campaign_limit_quote=D("1"),
        day_limit_quote=D("0.1"), session_limit_quote=D("0.05"))
    restored_attribution = ReconciledFillAttributor(
        attribution.path, wal=IntentWAL(wal.path), reservations=restored_reservations,
        loss_budget=LossBudgetLedger(
            loss.path, campaign_id="life", campaign_limit_quote=D("1"),
            day_limit_quote=D("1"), session_limit_quote=D("1")),
        subsidy_budget=restored_subsidy, opening_life=D("10"),
        opening_usdt=D("10"), opening_independent_price_usdt=D("1"),
        independent_value=independent, max_reference_skew_ms=200, create=False)
    assert restored_attribution.ready()
