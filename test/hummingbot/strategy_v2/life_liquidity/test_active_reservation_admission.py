"""New archived holds preserve verified financial history and durable recovery."""
import hashlib
import json
import os
import stat
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from test.hummingbot.strategy_v2.life_liquidity.o7_scenarios import system
from test.hummingbot.strategy_v2.life_liquidity.o8_history import archive_financial_journals, seed_settled_history
from test.hummingbot.strategy_v2.life_liquidity.test_archived_journals import LIMITS, capacity, reservations

import pytest

from hummingbot.strategy_v2.life_liquidity.lazy_archive import TransformedMap
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, SpotIntent


def intent(f, mode, key="new-hold", quantity="1"):
    session = f.c._order_safety_manager.current_session
    return SpotIntent(key, "BUY" if mode == "buy" else "SELL", D(quantity), D("1"),
                      session.session_id, session.epoch)


def admit(book, value, mode):
    return (book.reserve_risk_reduction(value, target_base=D("0")) if mode == "exit"
            else book.reserve(value, reference_price=D("1")))


def segments(book):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in book._archive.archive.directory.iterdir()}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["buy", "sell", "exit"])
@pytest.mark.parametrize("history", [0, 1000, 10000])
async def test_admission_keeps_settled_maps_without_decode_copy_or_reload(tmp_path, monkeypatch, mode, history):
    async with system(tmp_path) as f:
        seed_settled_history(f, history)
        archive_financial_journals(f)
        book = f.c._order_safety_reservations
        book.verify_memory()
        previous = (book._reservations.settled, book._trades, book._account_events, book._filled_base_total)
        before_segments = segments(book)
        before = json.loads(book.path.read_bytes())
        value = intent(f, mode)

        def historical_work(*args, **kwargs):
            raise AssertionError("new hold materialized or revalidated settled financial rows")

        with monkeypatch.context() as patch:
            patch.setattr(TransformedMap, "items", historical_work)
            patch.setattr(TransformedMap, "values", historical_work)
            patch.setattr(ReservationLedger, "_load_archive_data", historical_work)
            assert admit(book, value, mode).allowed
            book.verify_memory()
            assert book.matches_open_intent(value)
            assert book.preview().filled_base_total == history
        assert book._reservations.settled is previous[0]
        assert book._trades is previous[1] and book._account_events is previous[2]
        assert book._filled_base_total == previous[3]
        after = json.loads(book.path.read_bytes())
        assert after["archives"] == before["archives"]
        assert segments(book) == before_segments
        assert (book.life_balance, book.usdt_balance) == (D("10"), D("10"))
        recovered = book.reopen()
        assert recovered.matches_open_intent(value)
        assert (recovered.reserved_usdt, recovered.reserved_life) == ((D("1"), D("0")) if mode == "buy" else (D("0"), D("1")))
        if history:
            assert recovered.trade_intent_id("history-00000") == "past-00000"
            assert not recovered.record_fill("past-00000", "history-00000", D("1"), D("1"))
            assert not recovered.record_fee("history-00000", "USDT", D("0"))
        assert not admit(recovered, value, mode).allowed


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["buy", "exit"])
@pytest.mark.parametrize("fault", ["uncertain", "stale_writer", "segment", "balance_memory", "trade_memory", "missing", "symlink", "limits", "filled_total"])
async def test_admission_rejects_unverified_cut_before_publication(tmp_path, mode, fault):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        archive_financial_journals(f)
        book = f.c._order_safety_reservations
        book.verify_memory()
        if fault == "uncertain":
            book._uncertain = True
        elif fault == "stale_writer":
            book.reopen().record_cashflow("102", "USDT", D("1"))
        elif fault == "balance_memory":
            book.life_balance += D("1")
        elif fault == "trade_memory":
            book._trades = {**book._trades, "history-00000": ("past-00000", D("0.5"), D("1"))}
        elif fault == "limits":
            book.limits = replace(book.limits, max_gross_quote=book.limits.max_gross_quote + D("1"))
        elif fault == "filled_total":
            book._filled_base_total += D("1")
        else:
            entry = json.loads(book.path.read_bytes())["archives"][0]["segments"][0]
            path = book._archive.archive.directory / (entry["sha256"] + ".json")
            raw, status = path.read_bytes(), path.stat()
            if fault == "missing":
                path.unlink()
            elif fault == "symlink":
                target = tmp_path / "foreign-segment"
                target.write_bytes(raw)
                path.unlink()
                path.symlink_to(target)
            else:
                changed = raw.replace(b'"side":"BUY"', b'"side":"BUX"', 1)
                assert changed != raw and len(changed) == len(raw)
                path.write_bytes(changed)
                os.utime(path, ns=(status.st_atime_ns, status.st_mtime_ns))
        root = book.path.read_bytes()
        with pytest.raises((ValueError, OSError)):
            admit(book, intent(f, mode), mode)
        assert book.path.read_bytes() == root
        assert "new-hold" not in json.loads(root)["state"]["active"]["reservations"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["buy", "exit"])
@pytest.mark.parametrize("fault", ["file_fsync", "replace_before", "replace_after", "directory_fsync", "post_verify"])
async def test_interrupted_admission_retains_old_or_complete_new_hold(tmp_path, monkeypatch, mode, fault):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        archive_financial_journals(f)
        book = f.c._order_safety_reservations
        value = intent(f, mode)
        original_replace, original_fsync, original_verify = os.replace, os.fsync, book._archive.verify
        replaced = False

        def replace_root(source, target):
            nonlocal replaced
            if Path(target) == book.path and fault == "replace_before":
                raise OSError("synthetic replace-before failure")
            result = original_replace(source, target)
            if Path(target) == book.path:
                replaced = True
                if fault == "replace_after":
                    raise OSError("synthetic replace-after failure")
            return result

        def sync(descriptor):
            if (fault == "file_fsync" and stat.S_ISREG(os.fstat(descriptor).st_mode)
                    or fault == "directory_fsync" and replaced):
                raise OSError("synthetic fsync failure")
            return original_fsync(descriptor)

        def verify():
            result = original_verify()
            if replaced and fault == "post_verify":
                raise OSError("synthetic post-publication verify failure")
            return result

        with monkeypatch.context() as patch:
            patch.setattr(os, "replace", replace_root)
            patch.setattr(os, "fsync", sync)
            patch.setattr(book._archive, "verify", verify)
            with pytest.raises(OSError, match="synthetic"):
                admit(book, value, mode)
        recovered = ReservationLedger.restore(book.path, limits=book.limits, archive=book._archive.archive.policy)
        assert recovered.has_open_intent(value.intent_id) is replaced
        assert not book.has_open_intent(value.intent_id)
        assert (recovered.life_balance, recovered.usdt_balance) == (D("10"), D("10"))
        assert recovered.trade_intent_id("history-00000") == "past-00000"
        if fault == "file_fsync":
            assert not book._uncertain
            assert admit(book, value, mode).allowed
        else:
            assert book._uncertain
            with pytest.raises(ValueError):
                admit(book, intent(f, mode, key="retry"), mode)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["buy", "sell", "exit"])
async def test_duplicate_and_risk_denial_publish_no_new_hold(tmp_path, mode):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        archive_financial_journals(f)
        book = f.c._order_safety_reservations
        root = book.path.read_bytes()
        assert not admit(book, intent(f, mode, key="past-00000"), mode).allowed
        assert not admit(book, intent(f, mode, quantity="1000"), mode).allowed
        assert book.path.read_bytes() == root
        assert admit(book, intent(f, mode, key="first", quantity="6"), mode).allowed
        root = book.path.read_bytes()
        assert not admit(book, intent(f, mode, key="second", quantity="6"), mode).allowed
        assert book.path.read_bytes() == root
        assert book.reopen().has_open_intent("first")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["buy", "exit"])
async def test_killed_process_after_root_replace_restores_new_hold_once(tmp_path, mode):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        archive_financial_journals(f)
        book = f.c._order_safety_reservations
        value = intent(f, mode)
        script = """
import json, os, sys
from decimal import Decimal as D
from pathlib import Path
from hummingbot.strategy_v2.life_liquidity.checkpoint_archive import ArchivePolicy
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, RiskLimits, SpotIntent
path = Path(sys.argv[1])
manifest = json.loads(path.read_bytes())
policy = manifest['archive_policy']
policy['paths'] = tuple(tuple(p) for p in policy['paths'])
book = ReservationLedger.restore(path, limits=RiskLimits(**{k:D(v) for k,v in manifest['policy']['limits'].items()}), archive=ArchivePolicy(**policy))
value = SpotIntent('new-hold', sys.argv[2], D('1'), D('1'), sys.argv[3], int(sys.argv[4]))
original = os.replace
def replace_then_die(source, target):
    original(source, target)
    if Path(target) == path:
        os._exit(37)
os.replace = replace_then_die
book.reserve_risk_reduction(value, target_base=D('0')) if sys.argv[5] == 'exit' else book.reserve(value, reference_price=D('1'))
raise AssertionError('child did not reach publication')
"""
        result = subprocess.run([sys.executable, "-c", script, str(book.path), value.side, value.session_id, str(value.epoch), mode],
                                cwd=Path(__file__).resolve().parents[4], capture_output=True, text=True, timeout=15)
        assert result.returncode == 37, result.stderr
        recovered = book.reopen()
        assert recovered.matches_open_intent(value)
        assert not admit(recovered, value, mode).allowed
        assert recovered.trade_intent_id("history-00000") == "past-00000"
        with pytest.raises(ValueError):
            admit(book, intent(f, mode, key="stale"), mode)


@pytest.mark.parametrize("fault", ["active_bytes", "orphan_allocation"])
def test_capacity_denial_preserves_existing_hold_and_cancel_path(tmp_path, fault):
    policy = capacity("risk", max_active_bytes=3000)
    book = ReservationLedger(life_balance=D("10"), usdt_balance=D("10"), limits=LIMITS, path=tmp_path / "risk.json")
    book.migrate_archive(policy)
    first = SpotIntent("first", "BUY", D("1"), D("1"), "session", 1)
    assert book.reserve(first, reference_price=D("1")).allowed
    if fault == "orphan_allocation":
        (book._archive.archive.directory / "orphan.bin").write_bytes(b"x" * (policy.max_segment_bytes + 1))
    root = book.path.read_bytes()
    second = replace(first, intent_id="x" * 5000 if fault == "active_bytes" else "second")
    with pytest.raises(ValueError, match="ARCHIVE_CAPACITY"):
        book.reserve(second, reference_price=D("1"))
    assert book.path.read_bytes() == root
    assert book.has_open_intent("first") and not book.has_open_intent(second.intent_id)
    book.request_cancel("first")
    recovered = ReservationLedger.restore(book.path, limits=LIMITS, archive=policy)
    assert recovered.reserved_usdt == 1 and recovered.requires_reconciliation("first")


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_new_hold_keeps_partial_active_fills_fees_and_cancel_state(tmp_path, side):
    book = reservations(tmp_path)
    book.migrate_archive(capacity("risk"))
    book.verify_memory()
    trades, events = book._trades, book._account_events
    before = json.loads(book.path.read_bytes())["state"]["active"]
    balances = book.life_balance, book.usdt_balance
    value = SpotIntent("another", side, D("1"), D("1"), "s", 1)
    assert book.reserve(value, reference_price=D("1")).allowed
    after = json.loads(book.path.read_bytes())["state"]["active"]
    assert after["trades"] == before["trades"] and after["account_events"] == before["account_events"]
    assert book._trades is trades and book._account_events is events
    recovered = book.reopen()
    assert (recovered.life_balance, recovered.usdt_balance) == balances
    assert recovered.fee_for_trade("pending-trade") == ("USDT", D("-0.01"))
    assert recovered.reservation_state("pending").filled_base == D("0.5")
    assert recovered.requires_reconciliation("pending")
    assert recovered.matches_open_intent(value) and recovered.preview().filled_base_total == D("2.5")
