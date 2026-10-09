"""Persisted reservations and fills survive restart without double application."""

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from hummingbot.strategy_v2.life_liquidity import risk
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

    def unavailable(_descriptor):
        raise OSError("disk unavailable before replacement")

    with monkeypatch.context() as patcher:
        patcher.setattr(risk.os, "fsync", unavailable)
        with pytest.raises(OSError, match="disk unavailable before replacement"):
            book.record_fill("i1", "trade-1", Decimal("0.5"), Decimal("1"))
    assert book.life_balance == Decimal("1")
    assert book.reserved_usdt == Decimal("1")
    assert ReservationLedger.restore(path, limits=LIMITS).trade_ids == set()
    assert book.record_fill("i1", "trade-1", Decimal("0.5"), Decimal("1"))
    assert ReservationLedger.restore(path, limits=LIMITS).life_balance == Decimal("1.5")


def test_directory_fsync_failure_cannot_overwrite_committed_fill_with_cashflow(tmp_path, monkeypatch):
    path = tmp_path / "reservations.json"
    book = ReservationLedger(life_balance=Decimal("1"), usdt_balance=Decimal("5"),
                             limits=LIMITS, path=path)
    intent = SpotIntent("i1", "BUY", Decimal("1"), Decimal("1"), "s1", 1)
    assert book.reserve(intent, reference_price=Decimal("1")).allowed

    original_fsync = risk.os.fsync
    calls = 0

    def fail_directory_fsync(descriptor):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("crash after file replacement")
        return original_fsync(descriptor)

    with monkeypatch.context() as patcher:
        patcher.setattr(risk.os, "fsync", fail_directory_fsync)
        with pytest.raises(OSError, match="crash after file replacement"):
            book.apply_fills_snapshot(
                "i1", (("trade-1", Decimal("0.5"), Decimal("1"), "USDT", Decimal("-0.01")),),
                Decimal("0.5"), require_fees=True)

    assert calls == 2
    assert book.life_balance == Decimal("1")
    assert ReservationLedger.restore(path, limits=LIMITS).trade_ids == {"trade-1"}
    with pytest.raises(ValueError, match="RISK_JOURNAL_UNCERTAIN"):
        book.record_cashflow("101", "USDT", Decimal("1"))
    with pytest.raises(ValueError, match="RISK_JOURNAL_UNCERTAIN"):
        book.preview()
    with pytest.raises(ValueError, match="RISK_JOURNAL_UNCERTAIN"):
        book.matches_open_intent(intent)
    with pytest.raises(ValueError, match="RISK_JOURNAL_UNCERTAIN"):
        book.reserve(SpotIntent("i2", "BUY", Decimal("0.1"), Decimal("1"), "s1", 1),
                     reference_price=Decimal("1"))

    recovered = ReservationLedger.restore(path, limits=LIMITS)
    assert recovered.trade_ids == {"trade-1"}
    assert recovered.fee_for_trade("trade-1") == ("USDT", Decimal("-0.01"))
    assert recovered.record_cashflow("101", "USDT", Decimal("1"))
    assert ReservationLedger.restore(path, limits=LIMITS).trade_ids == {"trade-1"}


def test_killed_process_after_cashflow_replace_replays_once(tmp_path):
    path = tmp_path / "reservations.json"
    ReservationLedger(life_balance=Decimal("1"), usdt_balance=Decimal("5"),
                      limits=LIMITS, path=path)
    child = """
import os
import sys
from decimal import Decimal
from pathlib import Path
from hummingbot.strategy_v2.life_liquidity import risk
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, RiskLimits

path = Path(sys.argv[1])
ledger = ReservationLedger.restore(
    path, limits=RiskLimits(Decimal("0"), Decimal("10"), Decimal("20"), Decimal("10")))
original_replace = risk.os.replace

def replace_then_die(source, destination):
    original_replace(source, destination)
    if Path(destination) == path:
        os._exit(25)

risk.os.replace = replace_then_die
ledger.record_cashflow("101", "USDT", Decimal("2"))
raise AssertionError("process should have exited during the cashflow checkpoint")
"""
    process = subprocess.run(
        [sys.executable, "-c", child, str(path)],
        cwd=Path(__file__).resolve().parents[4], capture_output=True, text=True, timeout=15)
    assert process.returncode == 25, process.stderr
    restarted = ReservationLedger.restore(path, limits=LIMITS)
    assert restarted.usdt_balance == Decimal("7")
    assert not restarted.record_cashflow("101", "USDT", Decimal("2"))
    assert ReservationLedger.restore(path, limits=LIMITS).usdt_balance == Decimal("7")


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
