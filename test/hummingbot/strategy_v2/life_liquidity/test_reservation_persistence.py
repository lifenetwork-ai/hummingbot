"""Persisted reservations and fills survive restart without double application."""

import json
from decimal import Decimal

import pytest

from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, RiskLimits, SpotIntent

LIMITS = RiskLimits(Decimal("0"), Decimal("10"), Decimal("20"), Decimal("10"))


def test_restart_preserves_pending_remainder_and_fill_dedup(tmp_path):
    path = tmp_path / "reservations.json"
    first = ReservationLedger(life_balance=Decimal("1"), usdt_balance=Decimal("5"),
                              limits=LIMITS, path=path)
    intent = SpotIntent("i1", "BUY", Decimal("2"), Decimal("1"), "s1", 1)
    assert first.reserve(intent, reference_price=Decimal("1")).allowed
    assert first.record_fill("i1", "trade-1", Decimal("0.5"), Decimal("1"))
    first.request_cancel("i1")
    recovered = ReservationLedger.restore(path, limits=LIMITS)
    assert recovered.reserved_usdt == Decimal("1.5")
    assert recovered.life_balance == Decimal("1.5")
    assert not recovered.record_fill("i1", "trade-1", Decimal("0.5"), Decimal("1"))
    recovered.confirm_terminal("i1", cumulative_filled=Decimal("0.5"),
                               fills_reconciled=True, exchange_state="CANCELED")
    assert ReservationLedger.restore(path, limits=LIMITS).reserved_usdt == 0


def test_failed_checkpoint_cannot_mutate_in_memory_fill(tmp_path, monkeypatch):
    path = tmp_path / "reservations.json"
    book = ReservationLedger(life_balance=Decimal("1"), usdt_balance=Decimal("5"),
                             limits=LIMITS, path=path)
    intent = SpotIntent("i1", "BUY", Decimal("1"), Decimal("1"), "s1", 1)
    assert book.reserve(intent, reference_price=Decimal("1")).allowed

    def unavailable(*_):
        raise OSError("disk unavailable")

    with monkeypatch.context() as patcher:
        patcher.setattr(book, "_save", unavailable)
        with pytest.raises(OSError, match="disk unavailable"):
            book.record_fill("i1", "trade-1", Decimal("0.5"), Decimal("1"))
    assert book.life_balance == Decimal("1")
    assert book.reserved_usdt == Decimal("1")
    assert book.record_fill("i1", "trade-1", Decimal("0.5"), Decimal("1"))
    assert ReservationLedger.restore(path, limits=LIMITS).life_balance == Decimal("1.5")


def test_corrupt_or_limit_mismatched_journal_refuses_restore(tmp_path):
    path = tmp_path / "reservations.json"
    path.write_text('{"schema_version": 1, "life_balance": "NaN"}')
    with pytest.raises(ValueError):
        ReservationLedger.restore(path, limits=LIMITS)
    path.unlink()
    ReservationLedger(life_balance=Decimal("1"), usdt_balance=Decimal("5"),
                      limits=LIMITS, path=path)
    different = RiskLimits(Decimal("0"), Decimal("20"), Decimal("20"), Decimal("10"))
    with pytest.raises(ValueError, match="RISK_LIMIT_MISMATCH"):
        ReservationLedger.restore(path, limits=different)


def test_missing_trade_history_cannot_restore_filled_exposure(tmp_path):
    path = tmp_path / "reservations.json"
    book = ReservationLedger(life_balance=Decimal("1"), usdt_balance=Decimal("5"),
                             limits=LIMITS, path=path)
    intent = SpotIntent("i1", "BUY", Decimal("1"), Decimal("1"), "s1", 1)
    assert book.reserve(intent, reference_price=Decimal("1")).allowed
    assert book.record_fill("i1", "trade-1", Decimal("0.5"), Decimal("1"))
    data = json.loads(path.read_text())
    data["trades"] = {}
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="RISK_JOURNAL_INVALID"):
        ReservationLedger.restore(path, limits=LIMITS)
