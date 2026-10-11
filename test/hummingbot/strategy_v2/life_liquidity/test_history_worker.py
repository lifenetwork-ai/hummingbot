"""Read-only worker isolation, bounded admission and stale-result rejection."""
import asyncio
import os
import threading
import time
from dataclasses import replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.o7_scenarios import send, system
from test.hummingbot.strategy_v2.life_liquidity.o8_history import seed_settled_history

import pytest

from hummingbot.strategy_v2.life_liquidity import history_worker as module
from hummingbot.strategy_v2.life_liquidity.history_worker import HistoryWorker, HistoryWorkerPolicy
from hummingbot.strategy_v2.life_liquidity.joint_recovery import JointRecovery, digest, encode_order
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger


def worker(**updates):
    # Deliberately synthetic; no controller or live defaults.
    return HistoryWorker(HistoryWorkerPolicy(**{
        "max_orders": 100, "max_trades": 500, "max_snapshot_bytes": 1024 * 1024,
        "timeout_ms": 500, **updates}))


async def entered(event):
    async with asyncio.timeout(2):
        while not event.is_set():
            await asyncio.sleep(.001)


def block(monkeypatch):
    start, release = threading.Event(), threading.Event()
    original = module._encode

    def slow(snapshot):
        start.set()
        assert release.wait(3), "test did not release history worker"
        return original(snapshot)

    monkeypatch.setattr(module, "_encode", slow)
    return start, release


@pytest.mark.asyncio
async def test_worker_encodes_detached_immutable_history_off_event_loop(tmp_path, monkeypatch):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        w = worker()
        start, release = block(monkeypatch)
        task = asyncio.create_task(w.prepare(f.history["value"]))
        try:
            await entered(start)
            # Reaching here proves the event loop runs while the worker waits.
            original = f.history["value"]
            f.history["value"] = replace(original, orders=())
            release.set()
            result = await task
            assert result.bundle == original
            assert result.encoded["orders"] == [encode_order(o) for o in original.orders]
            assert result.proof_id == digest(result.encoded)
            with pytest.raises(TypeError):
                result.encoded["orders"][0]["trades"].clear()
        finally:
            release.set()
            w.close()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupt", ["timeout", "cancel", "close"])
async def test_worker_keeps_one_slot_until_thread_finishes_and_discards_late_results(tmp_path, monkeypatch, interrupt):
    async with system(tmp_path) as f:
        w = worker()
        start, release = block(monkeypatch)
        task = asyncio.create_task(w.prepare(f.history["value"], timeout_ms=30))
        try:
            await entered(start)
            if interrupt == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            elif interrupt == "close":
                w.close()
            else:
                with pytest.raises(ValueError, match="WORKER_TIMEOUT"):
                    await task
            with pytest.raises(ValueError, match="WORKER_(BUSY|CLOSED)"):
                await w.prepare(f.history["value"])
            release.set()
            if interrupt == "close":
                with pytest.raises(ValueError, match="WORKER_CLOSED"):
                    await task
            async with asyncio.timeout(2):
                while w.busy:
                    await asyncio.sleep(.001)
            if interrupt != "close":
                assert (await w.prepare(f.history["value"])).bundle == f.history["value"]
        finally:
            release.set()
            w.close()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["orders", "trades", "bytes", "mutable", "failed"])
async def test_capacity_and_worker_failure_never_authorize_or_mutate_bot(tmp_path, monkeypatch, fault):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        options = {"orders": {"max_orders": 1}, "trades": {"max_trades": 1},
                   "bytes": {"max_snapshot_bytes": 100}}.get(fault, {})
        w = worker(**options)
        f.joint.history_worker = w
        if fault == "mutable":
            f.history["value"] = replace(f.history["value"], orders=list(f.history["value"].orders))
        if fault == "failed":
            def failed(_):
                raise RuntimeError("synthetic worker failure")
            monkeypatch.setattr(module, "_encode", failed)
        before = f.c._order_safety_reservations.path.read_bytes()
        try:
            assert not await f.joint.reconcile()
            assert f.joint.reason_code.startswith("JOINT_HISTORY_")
            assert not f.joint.allocation_ready()
            assert f.c._order_safety_reservations.path.read_bytes() == before
            assert not f.v[3].sent and not f.v[4].sent
        finally:
            w.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["context", "account", "journal", "same_stat", "clock", "stale",
                                   "wal", "reservation", "runner", "stop"])
async def test_refresh_rejects_changes_while_worker_runs_before_any_settlement(tmp_path, monkeypatch, fault):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        w = worker()
        f.joint.history_worker = w
        start, release = block(monkeypatch)
        task = asyncio.create_task(f.joint.reconcile())
        try:
            await entered(start)
            assert not f.joint.journal._locked  # No file lock is held over await.
            if fault == "context":
                monkeypatch.setattr(f.joint, "_context", lambda: ("new-session", 2, 2, 2))
            elif fault == "account":
                feed = f.v[8]
                feed["snapshot"] = replace(feed["snapshot"], sequence=feed["snapshot"].sequence + 1)
            elif fault == "journal":
                with f.joint.journal.locked(shallow=True) as state:
                    state["runner_cancels"] = {"unknown": "exchange-id"}
                    f.joint.journal.commit(state)
            elif fault == "same_stat":
                path = f.joint.journal.path
                stat = path.stat()
                raw = path.read_bytes()
                changed = raw.replace(b'"phase":"COMMITTED"', b'"phase":"CORRUPTED"')
                assert raw != changed and len(raw) == len(changed)
                path.write_bytes(changed)
                os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            elif fault == "clock":
                previous = f.joint.clock_ms()
                monkeypatch.setattr(f.joint, "clock_ms", lambda: previous - 1)
            elif fault == "stale":
                f.v[6].advance(2)
            elif fault == "wal":
                f.c._order_safety_wal._records = {}
            elif fault == "runner":
                f.c._runner_scope_invalid = True
            elif fault == "stop":
                f.c.stop()
            else:
                f.c._order_safety_reservations._uncertain = True
            before = f.c._order_safety_reservations.path.read_bytes()
            release.set()
            assert not await task
            assert not f.joint.allocation_ready()
            assert f.c._order_safety_reservations.path.read_bytes() == before
        finally:
            release.set()
            w.close()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_slow_history_worker_does_not_block_owned_spot_cancel_or_release_claim(tmp_path, monkeypatch):
    async with system(tmp_path) as f:
        action = f.c._quote_action_planner.propose()[0]
        claim, _ = send(f, action)
        w = worker()
        f.joint.history_worker = w
        start, release = block(monkeypatch)
        task = asyncio.create_task(f.joint.reconcile())
        try:
            await entered(start)
            f.c._order_safety_manager.pause("SYNTHETIC_WORKER_CANCEL")
            watchdog = asyncio.create_task(f.c._order_safety_watchdog(.001))
            try:
                async with asyncio.timeout(.25):
                    while not f.v[3].cancels:
                        await asyncio.sleep(.001)
            finally:
                watchdog.cancel()
                await asyncio.gather(watchdog, return_exceptions=True)
            assert ("LIFE-USDT", claim.wire_id) in f.v[3].cancels
            assert claim.intent_id in f.v[1].claim_ids()
            assert f.c._order_safety_wal.get(claim.intent_id).state != "TERMINAL"
        finally:
            release.set()
            w.close()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_worker_refresh_allows_actual_affordable_send_but_stop_closes_admission(tmp_path):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        w = worker()
        f.joint.history_worker = w
        try:
            assert await f.joint.reconcile(), f.joint.reason_code
            claim, _ = send(f, f.c._quote_action_planner.propose()[0])
            assert claim.intent_id in f.v[1].claim_ids()
            f.c.stop()
            with pytest.raises(ValueError, match="WORKER_CLOSED"):
                await w.prepare(f.history["value"])
        finally:
            w.close()


@pytest.mark.asyncio
async def test_reservation_decode_runs_off_loop_and_is_bound_to_exact_bytes(tmp_path, monkeypatch):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        ledger = f.c._order_safety_reservations
        start, release = threading.Event(), threading.Event()
        original = module._decode_reservations

        def slow(snapshot):
            start.set()
            assert release.wait(3)
            return original(snapshot)

        monkeypatch.setattr(module, "_decode_reservations", slow)
        w = worker()
        f.joint.history_worker = w
        task = asyncio.create_task(f.joint.reconcile())
        try:
            await entered(start)
            assert not f.joint.journal._locked
            release.set()
            assert await task, f.joint.reason_code
            candidate = ledger.verified_disk()
            assert candidate._sealed_evidence
            with pytest.raises(TypeError):
                candidate.life_balance = 999
            # A cache hit on the live path still reads exact file bytes.
            raw = ledger.path.read_bytes()
            ledger.path.write_bytes(raw.replace(b'"life_balance":"10"', b'"life_balance":"11"'))
            with pytest.raises(ValueError, match="UNVERIFIED"):
                ledger.verify_memory()
        finally:
            release.set()
            w.close()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["bytes", "memory", "uncertain", "context", "clock"])
async def test_reservation_predecode_cannot_publish_a_stale_or_uncertain_cut(tmp_path, monkeypatch, fault):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        ledger = f.c._order_safety_reservations
        start, release = threading.Event(), threading.Event()
        original = module._decode_reservations

        def slow(snapshot):
            start.set()
            assert release.wait(3)
            return original(snapshot)

        monkeypatch.setattr(module, "_decode_reservations", slow)
        w = worker()
        f.joint.history_worker = w
        task = asyncio.create_task(f.joint.reconcile())
        try:
            await entered(start)
            if fault == "bytes":
                stat = ledger.path.stat()
                ledger.path.write_bytes(ledger.path.read_bytes().replace(
                    b'"life_balance":"10"', b'"life_balance":"11"'))
                os.utime(ledger.path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            elif fault == "memory":
                ledger.life_balance += 1
            elif fault == "uncertain":
                ledger._uncertain = True
            elif fault == "context":
                monkeypatch.setattr(f.joint, "_context", lambda: ("new", 2, 2, 2))
            else:
                previous = f.joint.clock_ms()
                monkeypatch.setattr(f.joint, "clock_ms", lambda: previous - 1)
            before = ledger.path.read_bytes()
            release.set()
            assert not await task
            assert not f.joint.allocation_ready()
            assert ledger.path.read_bytes() == before
        finally:
            release.set()
            w.close()
            await asyncio.gather(task, return_exceptions=True)


def test_snapshot_decoder_does_not_reopen_paths_or_write(tmp_path):
    path = tmp_path / "reservations.json"
    from decimal import Decimal as D

    from hummingbot.strategy_v2.life_liquidity.risk import RiskLimits
    limits = RiskLimits(D("0"), D("10"), D("100"), D("10"))
    ledger = ReservationLedger(life_balance=D("1"), usdt_balance=D("1"), limits=limits, path=path)
    raw = path.read_bytes()
    path.unlink()
    restored = ReservationLedger._restore_snapshot(raw, path=path, limits=limits)
    assert restored.life_balance == ledger.life_balance
    assert not path.exists()


@pytest.mark.parametrize("value", [0, -1, True, 1.5, None])
def test_worker_requires_explicit_positive_integer_bounds(value):
    with pytest.raises(ValueError, match="POLICY_INVALID"):
        HistoryWorkerPolicy(value, 10, 10000, 100)


@pytest.mark.asyncio
async def test_candidate_bytes_cannot_be_rebound_to_another_checkpoint(tmp_path):
    async with system(tmp_path) as f:
        ledger = f.c._order_safety_reservations
        w = worker()
        try:
            raw = ledger.path.read_bytes()
            evidence = await w.decode_reservations(raw, path=str(ledger.path), limits=ledger.limits)
            other = raw + b" "  # Valid equivalent JSON, different publication.
            ledger.path.write_bytes(other)
            with pytest.raises(ValueError, match="CHANGED_DURING_VERIFY"):
                ledger._accept_verified_snapshot(other, evidence)
        finally:
            w.close()


@pytest.mark.asyncio
async def test_cold_restore_with_explicit_worker_still_requires_operator_rearm(tmp_path):
    async with system(tmp_path) as f:
        w = worker()
        old = f.joint
        try:
            restored = JointRecovery(
                old.journal.path, controller=f.c, collect=old.collect, cancel_swap=old.cancel_swap,
                clock_ms=old.clock_ms, max_age_ms=old.max_age_ms, cancel_retry_ms=old.cancel_retry_ms,
                max_cancels_per_cycle=old.max_cancels_per_cycle, poll_interval_ms=old.poll_interval_ms,
                opening_long_entry=Decimal(old._binding["opening_long_entry"]),
                opening_short_entry=Decimal(old._binding["opening_short_entry"]),
                create=False, history_worker=w)
            f.c._joint_recovery = restored
            f.v[1]._recovery = restored
            assert await restored.reconcile(), restored.reason_code
            assert restored.reason_code == "JOINT_OPERATOR_REARM_REQUIRED"
            assert not restored.allocation_ready()
            assert not f.c._quote_action_planner.propose()
            assert not f.v[3].sent and not f.v[4].sent
        finally:
            w.close()


@pytest.mark.asyncio
async def test_spawn_process_startup_immutable_transfer_and_actual_send(tmp_path):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        w = worker(backend="process", timeout_ms=15000)
        try:
            with pytest.raises(ValueError, match="NOT_STARTED"):
                await w.prepare(f.history["value"])
            await w.start(timeout_ms=15000)
            result = await w.prepare(f.history["value"], timeout_ms=1000)
            with pytest.raises(TypeError):
                result.encoded["orders"][0]["claim"]["quantity_base"] = "999"
            f.joint.history_worker = w
            assert await f.joint.reconcile(), f.joint.reason_code
            claim, _ = send(f, f.c._quote_action_planner.propose()[0])
            assert claim.intent_id in f.v[1].claim_ids()
            children = tuple(w._executor._processes.values())
            with pytest.raises(ValueError, match="WORKER_TIMEOUT"):
                await w._run(time.sleep, lambda: .1, 10)
            with pytest.raises(ValueError, match="WORKER_BUSY"):
                await w.prepare(f.history["value"])
            assert claim.intent_id in f.v[1].claim_ids()
            async with asyncio.timeout(2):
                while w.busy:
                    await asyncio.sleep(.001)
        finally:
            w.close()
        async with asyncio.timeout(5):
            while any(child.is_alive() for child in children):
                await asyncio.sleep(.01)


@pytest.mark.asyncio
async def test_killed_process_worker_denies_refresh_without_releasing_existing_claim(tmp_path):
    async with system(tmp_path) as f:
        claim, _ = send(f, f.c._quote_action_planner.propose()[0])
        w = worker(backend="process", timeout_ms=15000)
        try:
            await w.start(timeout_ms=15000)
            children = tuple(w._executor._processes.values())
            for child in children:
                child.terminate()
            async with asyncio.timeout(5):
                while any(child.is_alive() for child in children):
                    await asyncio.sleep(.01)
            f.joint.history_worker = w
            assert not await f.joint.reconcile()
            assert f.joint.reason_code == "JOINT_HISTORY_WORKER_FAILED"
            assert not f.joint.allocation_ready()
            assert claim.intent_id in f.v[1].claim_ids()
            assert f.c._order_safety_wal.get(claim.intent_id).state != "TERMINAL"
        finally:
            w.close()
