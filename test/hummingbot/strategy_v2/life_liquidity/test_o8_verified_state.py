"""Cache hits must retain independent disk, memory and crash-phase fences."""
import asyncio
import os
from contextlib import contextmanager
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.o7_scenarios import send, system
from test.hummingbot.strategy_v2.life_liquidity.o8_history import seed_settled_history

import pytest

from hummingbot.strategy_v2.life_liquidity.policy_state import PolicyState
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL


@pytest.mark.asyncio
async def test_wire_index_reuses_verified_image_and_invalidates_after_commit(tmp_path, monkeypatch):
    async with system(tmp_path) as f:
        seed_settled_history(f, 20)
        wal = f.c._order_safety_wal
        wal.assert_verified()
        assert wal.find_by_client_order_id("HBOTpast000000000000000000000019").intent_id == "past-00019"
        image = wal._wire_image
        for record in wal.all_records():
            assert wal.find_by_client_order_id(record.client_order_id) == record
        assert wal._wire_image is image
        # A map transition replaces the strong referenced image, including
        # changed cancellation state for an otherwise identical wire ID.
        wal._commit(replace(wal.get("past-00019"), cancel_requested=True))
        assert wal.find_by_client_order_id("HBOTpast000000000000000000000019").cancel_requested
        assert wal._wire_image is not image
        with pytest.raises(KeyError):
            wal.find_by_client_order_id("absent-wire")


@pytest.mark.asyncio
async def test_wire_index_does_not_hide_replaced_memory_or_duplicate_wires(tmp_path):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        wal = f.c._order_safety_wal
        wire = wal.get("past-00000").client_order_id
        assert wal.find_by_client_order_id(wire).intent_id == "past-00000"
        wal._records = {"past-00000": replace(wal.get("past-00000"), cancel_requested=True)}
        assert wal.find_by_client_order_id(wire).cancel_requested
        wal._records["past-00001"] = replace(wal.get("past-00000"), intent_id="past-00001")
        with pytest.raises(ValueError, match="WAL_WIRE_ID_DUPLICATE"):
            wal.find_by_client_order_id(wire)
        wal._save(wal._records)
        with pytest.raises(ValueError, match="WAL_WIRE_ID_DUPLICATE"):
            IntentWAL(wal.path)


@pytest.mark.asyncio
async def test_read_only_carry_and_poll_queries_do_not_require_mutable_full_history(tmp_path, monkeypatch):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        assert await f.joint.reconcile()

        def unexpected(*args, **kwargs):
            raise AssertionError("read-only query requested a mutable full-history checkpoint")
        capital_locked = f.v[1].journal.locked

        def shallow_only(*args, **kwargs):
            if kwargs.get("shallow") is not True:
                unexpected()
            return capital_locked(*args, **kwargs)

        monkeypatch.setattr(f.v[1].journal, "locked", shallow_only)
        assert f.c._carry_monitor.check().allowed
        monkeypatch.setattr(f.joint.journal, "locked", unexpected)
        assert not f.joint.reconciliation_due()


def test_read_only_and_shallow_checkpoints_never_alias_mutable_state(tmp_path):
    path = tmp_path / "state.json"
    journal = PolicyState(path, policy={"v": 1}, initial={"nested": {"items": [1]}, "clock": 0}, create=True)
    with journal.inspect() as state:
        with pytest.raises(TypeError):
            state["nested"]["items"].append(2)
        with pytest.raises(ValueError, match="LOCK_REQUIRED"):
            journal.commit({"clock": 2})
    with journal.locked(shallow=True) as state:
        with pytest.raises(TypeError):
            state["nested"]["items"][0] = 2
        state["clock"] = 1
        journal.commit(state)
        state["clock"] = 99
    with journal.inspect() as state:
        assert state == {"nested": {"items": [1]}, "clock": 1}
    other = PolicyState(path, policy={"v": 1}, initial={}, create=False)
    with other.locked() as state:
        state["clock"] = 2
        other.commit(state)
    with pytest.raises(ValueError, match="STALE"):
        with journal.inspect():
            pass


def test_verified_policy_cache_detects_same_stat_and_memory_mutation(tmp_path):
    path = tmp_path / "state.json"
    journal = PolicyState(path, policy={"v": 1}, initial={"count": 1}, create=True)
    with journal.inspect():
        pass
    stat = path.stat()
    path.write_bytes(path.read_bytes().replace(b'"count":1', b'"count":2'))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(ValueError, match="STALE"):
        with journal.inspect():
            pass
    restored = PolicyState(path, policy={"v": 1}, initial={}, create=False)
    with restored.inspect():
        pass
    with pytest.raises(TypeError):
        restored._state["count"] = 3
    restored._state = {"count": 3}
    with pytest.raises(ValueError, match="STALE"):
        with restored.inspect():
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["loss_disk", "attribution_memory", "reservation_memory", "wal_memory", "uncertain"])
async def test_attribution_cache_cannot_hide_financial_changes(tmp_path, fault):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        attribution = f.c._fill_attributor
        assert attribution.ready()
        assert attribution.ready()
        if fault == "loss_disk":
            path = attribution.loss_budget.path
            stat = path.stat()
            raw = path.read_bytes()
            changed = raw.replace(b'"loss_quote": "0"', b'"loss_quote": "1"', 1)
            assert raw != changed and len(raw) == len(changed)
            path.write_bytes(changed)
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        elif fault == "attribution_memory":
            attribution._events["history-00000"]["loss_quote"] = "1"
        elif fault == "reservation_memory":
            attribution.reservations._trades = {**attribution.reservations._trades, "history-00000": ("past-00000", 2, 1)}
        elif fault == "wal_memory":
            attribution.wal._records = {}
        else:
            attribution.reservations._uncertain = True
        assert not attribution.ready()


@pytest.mark.asyncio
async def test_wal_cache_independent_images_and_same_stat_conflict(tmp_path):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        wal = f.c._order_safety_wal
        first = IntentWAL(wal.path)
        second = IntentWAL(wal.path)
        with pytest.raises(TypeError):
            del first._records["past-00000"]
        first._records = {}
        assert len(second.all_records()) == len(IntentWAL(wal.path).all_records()) == 2
        stat = wal.path.stat()
        raw = wal.path.read_bytes()
        changed = raw.replace(b'"state":"TERMINAL"', b'"state":"INVALID!"', 1)
        assert raw != changed and len(raw) == len(changed)
        wal.path.write_bytes(changed)
        os.utime(wal.path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        with pytest.raises(ValueError):
            IntentWAL(wal.path)


@pytest.mark.asyncio
async def test_applying_phase_must_replay_cross_journal_terminal_checkpoints(tmp_path, monkeypatch):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        with f.joint.journal.locked() as state:
            state["phase"] = "APPLYING"
            f.joint.journal.commit(state)
        calls = []
        ledger = f.c._order_safety_reservations
        original = ledger.confirm_terminal

        def observe(*args, **kwargs):
            calls.append(args[0])
            return original(*args, **kwargs)
        monkeypatch.setattr(ledger, "confirm_terminal", observe)
        assert await f.joint.reconcile(), f.joint.reason_code
        assert set(calls) == {"past-00000", "past-00001"}


@pytest.mark.asyncio
async def test_cached_reservation_evidence_cannot_be_mutated(tmp_path):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        ledger = f.c._order_safety_reservations
        disk = ledger.verify_memory()
        with pytest.raises(TypeError):
            disk.life_balance = 999
        with pytest.raises(TypeError):
            disk._trades["history-00000"] = ("past-00000", 999, 1)
        assert ledger.verify_memory() is disk
        assert ledger.life_balance == 10


@pytest.mark.asyncio
async def test_cancel_transports_start_before_full_joint_qualification(tmp_path, monkeypatch):
    async with system(tmp_path) as f:
        c, spot = f.c, f.v[3]
        q = c._quote_action_planner
        q.intent_id_factory = lambda: "cancel-before-poll"
        action = q.propose()[0]
        send(f, action)
        original_cancel = spot.cancel_by_client_id
        original_reconcile = f.joint.reconcile
        checks = []

        async def delayed_transport(*args, **kwargs):
            await asyncio.sleep(.01)
            return await original_cancel(*args, **kwargs)

        async def qualify_after_cancel():
            checks.append(bool(spot.cancels))
            assert spot.cancels, "full qualification started ahead of healthy spot cancel transport"
            return await original_reconcile()
        monkeypatch.setattr(spot, "cancel_by_client_id", delayed_transport)
        monkeypatch.setattr(f.joint, "reconcile", qualify_after_cancel)
        c._order_safety_manager.pause("SYNTHETIC_CANCEL_ORDERING")
        await c._cancel_and_reconcile_orders()
        assert checks and all(checks)
        assert "cancel-before-poll" in f.v[1].claim_ids()


@pytest.mark.asyncio
async def test_permission_telemetry_does_not_replay_financial_history(tmp_path, monkeypatch):
    async with system(tmp_path) as f:
        def unexpected(*args, **kwargs):
            raise AssertionError("financial sampling belongs to explicit SNAPSHOT events")
        monkeypatch.setattr("controllers.generic.life_liquidity.collect_spot_metrics", unexpected)
        assert f.c._record_telemetry("PERMISSION", allowed=True)
        assert f.log.records()[-1]["metrics"]["nav_quote"]["value"] is None
        assert not f.c._record_telemetry("SNAPSHOT")
        assert not f.log.healthy


@pytest.mark.asyncio
async def test_attribution_cache_rejects_a_change_during_final_snapshot(tmp_path, monkeypatch):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        attributor = f.c._fill_attributor
        original = attributor._readiness_cut

        def changed(*, detach=False):
            if detach:
                path = attributor.loss_budget.path
                raw = path.read_bytes()
                path.write_bytes(raw.replace(b'"loss_quote": "0"', b'"loss_quote": "1"', 1))
            return original(detach=detach)
        monkeypatch.setattr(attributor, "_readiness_cut", changed)
        assert not attributor.ready()
        assert not attributor.ready()


@pytest.mark.asyncio
async def test_clock_fence_is_rechecked_after_switching_to_write_lock(tmp_path, monkeypatch):
    async with system(tmp_path) as f:
        journal = f.joint.journal
        f.v[6].advance(.001)
        now = f.joint.clock_ms()
        original = journal.locked

        @contextmanager
        def observe_newer_checkpoint(*args, **kwargs):
            # Publish the newer cut immediately before the operation acquires
            # its writer scope; it must fence the current clock at that entry.
            with original(shallow=True) as state:
                state["last_ms"] = now + 1
                journal.commit(state)
            with original(*args, **kwargs) as state:
                yield state
        monkeypatch.setattr(journal, "locked", observe_newer_checkpoint)
        with pytest.raises(ValueError, match="JOINT_CLOCK_ROLLBACK"):
            f.joint._fresh()
        assert journal._state["last_ms"] == now + 1
