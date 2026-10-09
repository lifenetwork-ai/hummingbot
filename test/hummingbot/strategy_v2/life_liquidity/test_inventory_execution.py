"""Offline inventory execution respects fixed targets and independent market limits."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from hummingbot.strategy_v2.life_liquidity.inventory_execution import (
    InventoryExecutionLedger,
    InventoryExecutionPolicy,
    InventoryExecutionSnapshot,
    plan_inventory_child,
)

D = Decimal
NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


def _policy(**changes):
    policy = InventoryExecutionPolicy(
        side="SELL", target_base=D("10"), benchmark_price_usdt=D("1"),
        deadline_utc=NOW + timedelta(hours=1), max_child_base=D("3"),
        max_total_notional_quote=D("10"), max_slippage_bps=D("100"),
        max_impact_quote=D("0.1"), max_exit_loss_quote=D("0.2"),
        participation_fraction=D("0.1"), min_independent_volume_base=D("10"),
        tick_size=D("0.01"), lot_size=D("0.1"), min_size_base=D("0.1"))
    return replace(policy, **changes)


def _snapshot(**changes):
    snapshot = InventoryExecutionSnapshot(
        observed_utc=NOW, best_bid_usdt=D("0.99"), best_ask_usdt=D("1.01"),
        independent_volume_base=D("100"), known_own_volume_base=D("0"),
        independent_depth_base=D("5"), expected_impact_quote=D("0.01"),
        life_balance=D("10"), usdt_balance=D("10"), filled_base=D("0"),
        pending_base=D("0"), filled_notional_quote=D("0"),
        pending_notional_quote=D("0"), exit_loss_used_quote=D("0"),
        mm_orders_reconciled=True, account_scope_ready=True)
    return replace(snapshot, **changes)


def test_child_is_post_only_and_never_exceeds_target_balances_or_notional():
    decision = plan_inventory_child(_policy(max_total_notional_quote=D("11")), _snapshot(
        filled_base=D("7"), pending_base=D("2"),
        filled_notional_quote=D("7"), pending_notional_quote=D("2")))
    assert decision.allowed
    assert decision.quantity_base == D("1")
    assert decision.price_usdt == D("1.01")
    assert decision.order_semantics == "LIMIT_MAKER"
    assert decision.remaining_base == D("1")


def test_own_or_inflated_volume_never_expands_hard_target_or_participation():
    policy = _policy(max_child_base=D("100"), max_total_notional_quote=D("1000"))
    thin = plan_inventory_child(policy, _snapshot(
        independent_volume_base=D("100000"), known_own_volume_base=D("99990")))
    assert thin.allowed and thin.quantity_base == D("1")
    capped = plan_inventory_child(policy, _snapshot(
        independent_volume_base=D("100000"), filled_base=D("9")))
    assert capped.allowed and capped.quantity_base == D("1")


def test_deadline_and_unreconciled_mm_never_force_market_sweep():
    expired = plan_inventory_child(_policy(), _snapshot(observed_utc=NOW + timedelta(hours=1)))
    assert not expired.allowed and expired.reason_code == "INVENTORY_DEADLINE_EXPIRED"
    assert expired.remaining_base == D("10")
    blocked = plan_inventory_child(_policy(), _snapshot(mm_orders_reconciled=False))
    assert not blocked.allowed and blocked.reason_code == "MM_RECONCILIATION_REQUIRED"


def test_thin_depth_slippage_impact_and_exit_budget_pause_child():
    assert plan_inventory_child(_policy(), _snapshot(
        independent_volume_base=D("5"))).reason_code == "INDEPENDENT_VOLUME_INSUFFICIENT"
    assert plan_inventory_child(_policy(), _snapshot(
        independent_depth_base=D("0"))).reason_code == "INDEPENDENT_DEPTH_UNAVAILABLE"
    assert plan_inventory_child(_policy(), _snapshot(
        best_bid_usdt=D("0.96"), best_ask_usdt=D("0.98"))).reason_code == "PRICE_LIMIT_BREACHED"
    assert plan_inventory_child(_policy(), _snapshot(
        expected_impact_quote=D("0.2"))).reason_code == "IMPACT_LIMIT_BREACHED"
    assert plan_inventory_child(_policy(), _snapshot(
        exit_loss_used_quote=D("0.2"))).reason_code == "EXIT_BUDGET_EXHAUSTED"


def test_no_capacity_or_account_scope_blocks_without_child():
    assert plan_inventory_child(_policy(), _snapshot(
        filled_base=D("8"), pending_base=D("2"))).reason_code == "INVENTORY_TARGET_FILLED_OR_PENDING"
    assert plan_inventory_child(_policy(), _snapshot(
        account_scope_ready=False)).reason_code == "ACCOUNT_SCOPE_UNAVAILABLE"
    assert plan_inventory_child(_policy(), _snapshot(
        life_balance=D("0"))).reason_code == "INVENTORY_BALANCE_UNAVAILABLE"


def test_durable_inventory_session_caps_all_pending_and_filled_children(tmp_path):
    path = tmp_path / "inventory_execution.json"
    book = InventoryExecutionLedger(
        path, session_id="inventory-1",
        policy=_policy(target_base=D("4"), max_total_notional_quote=D("100")),
        create=True)
    first = book.reserve_child("child-1", _snapshot())
    assert first.allowed and first.quantity_base == D("3")
    restored = InventoryExecutionLedger(path, session_id="inventory-1",
                                        policy=book.policy, create=False)
    second = restored.reserve_child("child-2", _snapshot())
    assert second.allowed and second.quantity_base == D("1")
    third = restored.reserve_child("child-3", _snapshot())
    assert third.reason_code == "INVENTORY_TARGET_FILLED_OR_PENDING"
    assert restored.pending_base == D("4")
    with pytest.raises(ValueError, match="INVENTORY_JOURNAL_UNAVAILABLE"):
        book.reserve_child("stale-child", _snapshot())


def test_inventory_fill_deduplicates_and_deadline_reports_residual(tmp_path):
    book = InventoryExecutionLedger(
        tmp_path / "inventory_execution.json", session_id="inventory-1",
        policy=_policy(target_base=D("4"), max_total_notional_quote=D("100")),
        create=True)
    assert book.reserve_child("child-1", _snapshot()).allowed
    assert book.record_fill("child-1", "trade-1", D("2"), D("1.01"))
    assert not book.record_fill("child-1", "trade-1", D("2"), D("1.01"))
    assert book.filled_base == D("2") and book.pending_base == D("1")
    report = book.report(NOW + timedelta(hours=1))
    assert report.state == "EXPIRED_PARTIAL"
    assert report.remaining_base == D("2")
    assert report.pending_base == D("1")
    assert not book.reserve_child("child-2", _snapshot(
        observed_utc=NOW + timedelta(hours=1))).allowed


def test_inventory_clock_rollback_and_changed_policy_fail_closed(tmp_path):
    path = tmp_path / "inventory_execution.json"
    book = InventoryExecutionLedger(path, session_id="inventory-1",
                                    policy=_policy(), create=True)
    assert book.reserve_child("child-1", _snapshot(
        observed_utc=NOW + timedelta(minutes=10))).allowed
    assert book.reserve_child("child-2", _snapshot()).reason_code == "INVENTORY_CLOCK_ROLLBACK"
    with pytest.raises(ValueError, match="INVENTORY_JOURNAL_UNAVAILABLE"):
        InventoryExecutionLedger(path, session_id="inventory-1",
                                 policy=_policy(target_base=D("20")), create=False)
    path.unlink()
    with pytest.raises(ValueError, match="INVENTORY_JOURNAL_UNAVAILABLE"):
        book.reserve_child("child-3", _snapshot())


def test_inventory_ambiguous_commit_requires_restore_and_fill_ids_are_unique(tmp_path):
    path = tmp_path / "inventory_execution.json"
    book = InventoryExecutionLedger(path, session_id="inventory-1",
                                    policy=_policy(), create=True)
    real_save = book._save

    def write_then_fail(entries, observed):
        real_save(entries, observed)
        raise OSError("ambiguous directory sync")

    with patch.object(book, "_save", side_effect=write_then_fail):
        with pytest.raises(OSError):
            book.reserve_child("child-1", _snapshot())
    with pytest.raises(ValueError, match="INVENTORY_JOURNAL_UNAVAILABLE"):
        book.reserve_child("child-2", _snapshot())
    restored = InventoryExecutionLedger(path, session_id="inventory-1",
                                        policy=book.policy, create=False)
    assert restored.pending_base == D("3")
    assert restored.record_fill("child-1", "trade-1", D("1"), D("1.01"))
    assert not restored.record_fill("child-1", "trade-1", D("1"), D("1.01"))
    with pytest.raises(ValueError, match="INVENTORY_FILL_CONFLICT"):
        restored.record_fill("child-1", "trade-1", D("1"), D("1.02"))


def test_one_exchange_trade_id_cannot_count_for_two_children(tmp_path):
    book = InventoryExecutionLedger(
        tmp_path / "inventory_execution.json", session_id="inventory-1",
        policy=_policy(max_total_notional_quote=D("100")), create=True)
    assert book.reserve_child("child-1", _snapshot()).allowed
    assert book.reserve_child("child-2", _snapshot()).allowed
    assert book.record_fill("child-1", "trade-1", D("1"), D("1.01"))
    with pytest.raises(ValueError, match="INVENTORY_FILL_CONFLICT"):
        book.record_fill("child-2", "trade-1", D("1"), D("1.01"))
