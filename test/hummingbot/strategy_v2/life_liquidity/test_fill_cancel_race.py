"""P5.4: cancel ACKs, fill snapshots, fees, and restart are order independent."""

from datetime import datetime, timezone
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_order_gateway import FakeOkx, full_scope, order

import pytest

from hummingbot.strategy_v2.life_liquidity.order_gateway import OkxSpotOrderGateway, SpotFill, SpotReservationReconciler
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, RiskLimits, SpotIntent
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL

LIMITS = RiskLimits(Decimal("0"), Decimal("30"), Decimal("30"), Decimal("30"))
NOW = datetime(2026, 10, 7, tzinfo=timezone.utc)


def _setup(tmp_path):
    wal = IntentWAL(tmp_path / "intents.json")
    wal.prepare("i1", client_order_id="wire-1", session_id="old-session",
                epoch=1, reservation_id="i1")
    ledger = ReservationLedger(life_balance=Decimal("10"), usdt_balance=Decimal("10"),
                               limits=LIMITS, path=tmp_path / "reservations.json")
    assert ledger.reserve(SpotIntent("i1", "BUY", Decimal("1"), Decimal("1"),
                                     "old-session", 1), reference_price=Decimal("1")).allowed
    connector = FakeOkx()
    connector.status["wire-1"] = order("live")
    reconciler = SpotReservationReconciler(wal, ledger, require_fees=True)
    gateway = OkxSpotOrderGateway(
        connector, wal, trading_pair="LIFE-USDT", clock=lambda: NOW,
        apply_fills=reconciler.apply_fills, confirm_terminal=reconciler.confirm_terminal,
        on_cancel_requested=reconciler.request_cancel, scope_check=full_scope)
    return wal, ledger, connector, reconciler, gateway


def _fill(trade_id, quantity, *, fee="-0.01"):
    return {"tradeId": trade_id, "ordId": "exchange-1", "fillSz": quantity,
            "fillPx": "1", "feeCcy": "USDT", "fee": fee}


@pytest.mark.asyncio
async def test_fills_before_and_after_cancel_ack_are_counted_once_across_restart(tmp_path):
    wal, ledger, connector, _, gateway = _setup(tmp_path)
    connector.status["wire-1"] = order("partially_filled", filled="0.2")
    connector.fills["exchange-1"] = [_fill("trade-1", "0.2")]
    first = await gateway.reconcile("old-session", 1)
    assert first.open_order_ids == ("wire-1",)
    assert ledger.life_balance == Decimal("10.2")

    await gateway.request_cancel("old-session", 1)
    connector.status["wire-1"] = order("partially_filled", filled="0.5")
    connector.fills["exchange-1"] = [_fill("trade-2", "0.3"), _fill("trade-1", "0.2")]
    pending = await gateway.reconcile("old-session", 1)
    assert pending.pending_cancel_ids == ("wire-1",)
    assert ledger.life_balance == Decimal("10.5")
    assert ledger.usdt_balance == Decimal("9.48")
    assert ledger.reserved_usdt == Decimal("0.5")

    restarted_wal = IntentWAL(wal.path)
    restarted_ledger = ReservationLedger.restore(ledger.path, limits=LIMITS)
    restarted_reconciler = SpotReservationReconciler(
        restarted_wal, restarted_ledger, require_fees=True)
    restarted = OkxSpotOrderGateway(
        connector, restarted_wal, trading_pair="LIFE-USDT", clock=lambda: NOW,
        apply_fills=restarted_reconciler.apply_fills,
        confirm_terminal=restarted_reconciler.confirm_terminal,
        scope_check=full_scope)
    connector.status["wire-1"] = order("canceled", filled="0.5")
    assert (await restarted.reconcile("old-session", 1)).trade_events_reconciled
    assert (await restarted.reconcile("old-session", 1)).trade_events_reconciled
    assert restarted_ledger.life_balance == Decimal("10.5")
    assert restarted_ledger.usdt_balance == Decimal("9.48")
    assert restarted_ledger.reserved_usdt == 0
    assert restarted_ledger.trade_ids == {"trade-1", "trade-2"}


def test_invalid_later_fee_does_not_partially_checkpoint_first_fill(tmp_path):
    _, ledger, _, reconciler, _ = _setup(tmp_path)
    fills = (SpotFill("trade-1", Decimal("0.2"), Decimal("1"), "USDT", Decimal("-0.01")),
             SpotFill("trade-2", Decimal("0.3"), Decimal("1"), "USDT", Decimal("-20")))
    assert not reconciler.apply_fills("wire-1", fills, Decimal("0.5"))
    restored = ReservationLedger.restore(ledger.path, limits=LIMITS)
    assert restored.trade_ids == set()
    assert restored.life_balance == Decimal("10")
    assert restored.usdt_balance == Decimal("10")
    assert restored.reserved_usdt == Decimal("1")


def test_older_cumulative_snapshot_cannot_erase_or_replay_newer_fills(tmp_path):
    _, ledger, _, reconciler, _ = _setup(tmp_path)
    newer = (SpotFill("trade-2", Decimal("0.3"), Decimal("1"), "USDT", Decimal("-0.01")),
             SpotFill("trade-1", Decimal("0.2"), Decimal("1"), "USDT", Decimal("-0.01")))
    assert reconciler.apply_fills("wire-1", newer, Decimal("0.5"))
    older = (SpotFill("trade-1", Decimal("0.2"), Decimal("1"), "USDT", Decimal("-0.01")),)
    assert not reconciler.apply_fills("wire-1", older, Decimal("0.2"))
    assert ledger.life_balance == Decimal("10.5")
    assert ledger.usdt_balance == Decimal("9.48")
    assert ledger.trade_ids == {"trade-1", "trade-2"}
    assert reconciler.apply_fills("wire-1", newer, Decimal("0.5"))
    assert ledger.usdt_balance == Decimal("9.48")


@pytest.mark.asyncio
async def test_empty_stale_exchange_snapshot_does_not_skip_ledger_check(tmp_path):
    _, ledger, connector, reconciler, gateway = _setup(tmp_path)
    fill = SpotFill("trade-1", Decimal("0.2"), Decimal("1"), "USDT", Decimal("-0.01"))
    assert reconciler.apply_fills("wire-1", (fill,), Decimal("0.2"))
    connector.status["wire-1"] = order("live", filled="0")
    connector.fills["exchange-1"] = []
    result = await gateway.reconcile("old-session", 1)
    assert not result.trade_events_reconciled
    assert ledger.life_balance == Decimal("10.2")
    assert ledger.usdt_balance == Decimal("9.79")


def test_conflicting_duplicate_trade_id_keeps_prior_checkpoint(tmp_path):
    _, ledger, _, reconciler, _ = _setup(tmp_path)
    first = SpotFill("trade-1", Decimal("0.2"), Decimal("1"), "USDT", Decimal("-0.01"))
    assert reconciler.apply_fills("wire-1", (first,), Decimal("0.2"))
    conflicting = SpotFill("trade-1", Decimal("0.3"), Decimal("1"), "USDT", Decimal("-0.01"))
    assert not reconciler.apply_fills("wire-1", (conflicting,), Decimal("0.3"))
    assert ledger.trade_ids == {"trade-1"}
    assert ledger.life_balance == Decimal("10.2")
    assert ledger.usdt_balance == Decimal("9.79")
