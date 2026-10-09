"""Persisted execution-loss status must be fresh at the LIFE wire boundary."""

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_executor_protected_send import _setup as sender_setup
from test.hummingbot.strategy_v2.life_liquidity.test_final_quote_send import _attach_quote_planner
from test.hummingbot.strategy_v2.life_liquidity.test_quote_actions import _setup as planner_setup
from test.hummingbot.strategy_v2.life_liquidity.test_runtime_risk_binding import _install, _observation
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.life_liquidity.action_journal import QuoteActionJournal
from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetLedger

AT = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)


def _queued(tmp_path, *, limit="10"):
    controller, template, connector, wal, reservations, _ = sender_setup(tmp_path)
    risk = {"now": 100, "observation": _observation(100)}
    _install(controller, tmp_path, risk)
    ledger = LossBudgetLedger(
        tmp_path / "loss_budget.json", campaign_id="life", campaign_limit_quote=Decimal(limit),
        day_limit_quote=Decimal(limit), session_limit_quote=Decimal(limit))
    session_id = controller._order_safety_manager.current_session.session_id
    ledger.record("synthetic-opening-anchor", Decimal("0"), session_id=session_id, at_utc=AT)
    controller.install_execution_loss_budget(ledger, utc_clock=lambda: AT)
    _, executor = _attach_quote_planner(
        controller, template, wal, reservations,
        loss_budget_status=ledger.verified_status(session_id=session_id, at_utc=AT))
    executor.place_open_order()
    wire = {"clOrdId": executor._order.order_id, "instId": "LIFE-USDT",
            "side": "buy", "ordType": "post_only", "tdMode": "cash",
            "px": str(executor.config.price), "sz": str(executor.config.amount)}
    return controller, connector.sent[0]["pre_send_check"], wire, ledger, wal, reservations, session_id


def test_exhausted_loss_budget_revokes_queued_send(tmp_path):
    controller, check, wire, ledger, wal, reservations, session_id = _queued(tmp_path, limit="1")
    ledger.record("adverse-fill", Decimal("1"), session_id=session_id, at_utc=AT)

    with pytest.raises(PermissionError, match="SEND_PERMISSION_REVOKED"):
        check(wire)
    assert controller.execution_loss_reason_code == "EXECUTION_LOSS_BUDGET_EXHAUSTED"
    assert wal.get("quote-1").state == "SEND_UNKNOWN"
    assert reservations.has_open_intent("quote-1")


def test_new_loss_below_limit_still_requires_fresh_quote_snapshot(tmp_path):
    controller, check, wire, ledger, _, _, session_id = _queued(tmp_path)
    ledger.record("small-loss", Decimal("1"), session_id=session_id, at_utc=AT)

    assert controller.allow_create_executor_actions()
    with pytest.raises(PermissionError, match="SEND_PERMISSION_REVOKED"):
        check(wire)


def test_missing_loss_journal_revokes_queued_send(tmp_path):
    controller, check, wire, ledger, _, _, _ = _queued(tmp_path)
    ledger.path.unlink()

    with pytest.raises(PermissionError, match="SEND_PERMISSION_REVOKED"):
        check(wire)
    assert controller.execution_loss_reason_code == "EXECUTION_LOSS_BUDGET_UNAVAILABLE"


def test_exhausted_loss_budget_rejects_queued_v2_action(tmp_path):
    controller, planner, _, _, snapshot = planner_setup(tmp_path, max_actions=1)
    _install(controller, tmp_path, {"now": 100, "observation": _observation(100)})
    session_id = controller._order_safety_manager.current_session.session_id
    ledger = LossBudgetLedger(
        tmp_path / "loss_budget.json", campaign_id="life", campaign_limit_quote=Decimal("1"),
        day_limit_quote=Decimal("1"), session_limit_quote=Decimal("1"))
    ledger.record("synthetic-opening-anchor", Decimal("0"), session_id=session_id, at_utc=AT)
    controller.install_execution_loss_budget(ledger, utc_clock=lambda: AT)
    status = ledger.verified_status(session_id=session_id, at_utc=AT)
    planner.snapshot = lambda: replace(snapshot, loss_budget_status=status)
    controller.install_quote_action_planner(planner)
    create = planner.propose()[0]
    ledger.record("adverse-fill", Decimal("1"), session_id=session_id, at_utc=AT)
    runner = SimpleNamespace(controllers={"life": controller}, logger=lambda: MagicMock())

    assert StrategyV2Base._filter_authorized_actions(runner, [create]) == []
    assert QuoteActionJournal(planner.action_journal.path).get(create.executor_config.id).state == "REJECTED"
