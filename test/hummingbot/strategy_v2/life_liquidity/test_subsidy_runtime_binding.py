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
from hummingbot.strategy_v2.life_liquidity.spot_quotes import QuoteCosts

NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
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
