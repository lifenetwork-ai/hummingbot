"""Hedge permission uses verified WAL cuts and exact pending-maker identities."""
import os
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.o7_scenarios import send, system
from test.hummingbot.strategy_v2.life_liquidity.o8_history import (
    archive_financial_journals,
    archive_settled_history,
    seed_settled_history,
)
from test.hummingbot.strategy_v2.life_liquidity.test_carry_monitor import refresh

import pytest

from hummingbot.strategy_v2.life_liquidity.joint_recovery import RecoveryOrder
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL


def migrate(f, archived):
    if archived:
        archive_settled_history(f)
        archive_financial_journals(f)


def no_history(*args, **kwargs):
    raise AssertionError("hedge permission materialized the historical WAL")


async def publish_pending(f, claim):
    refresh(f.v, f.feeds, pending=(claim,))
    obs, previous = f.v[8]["snapshot"], f.history["value"]
    f.history["value"] = replace(
        previous, snapshot_sequence=obs.sequence, observed_at_ms=obs.observed_at_ms,
        orders=previous.orders + (RecoveryOrder(claim, "exchange-" + claim.intent_id, "live", ()),),
        pending_wire_ids=(claim.wire_id,))
    assert await f.joint.reconcile(), f.joint.reason_code


@pytest.mark.asyncio
@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "journals"])
@pytest.mark.parametrize("history", [0, 1000, 10000])
async def test_healthy_hedge_permission_does_not_materialize_history(tmp_path, monkeypatch, archived, history):
    async with system(tmp_path) as f:
        seed_settled_history(f, history)
        migrate(f, archived)
        # Guard the class, including independently restored/sealed WAL images.
        monkeypatch.setattr(IntentWAL, "all_records", no_history)
        assert f.hedge._snapshot().spot_life_base == 10
        assert f.hedge.allows_spot(), f.hedge.reason_code
        assert f.hedge.reason_code == "HEDGE_BALANCED"
        if history:
            assert f.c._order_safety_wal.get(f"past-{history - 1:05}").state == "TERMINAL"


@pytest.mark.asyncio
@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "journals"])
@pytest.mark.parametrize("state", ["SEND_UNKNOWN", "ACKED"])
async def test_pending_maker_uses_only_exact_intent_lookup(tmp_path, monkeypatch, archived, state):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        migrate(f, archived)
        assert await f.joint.reconcile(), f.joint.reason_code
        action = f.c._quote_action_planner.propose()[0]
        claim, _ = send(f, action)
        wal = f.c._order_safety_wal
        await publish_pending(f, claim)
        if state == "SEND_UNKNOWN":
            wal._commit(replace(wal.get(claim.intent_id), state=state))
        lookups = []
        original = wal.get

        def get(key):
            lookups.append(key)
            return original(key)

        monkeypatch.setattr(IntentWAL, "all_records", no_history)
        monkeypatch.setattr(wal, "get", get)
        assert f.hedge.allows_spot(), f.hedge.reason_code
        assert lookups == [claim.intent_id]
        assert f.v[1].claim_ids() == (claim.intent_id,)


@pytest.mark.asyncio
@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "journals"])
@pytest.mark.parametrize("fault", ["same_stat_disk", "wal_memory", "ledger_memory", "uncertain", "missing",
                                   "wire", "terminal", "prepared", "session", "epoch", "claim", "account"])
async def test_warm_hedge_permission_rejects_changed_evidence_and_keeps_claim(tmp_path, archived, fault):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        migrate(f, archived)
        assert await f.joint.reconcile(), f.joint.reason_code
        action = f.c._quote_action_planner.propose()[0]
        claim, _ = send(f, action)
        await publish_pending(f, claim)
        assert f.hedge.allows_spot(), f.hedge.reason_code
        wal = f.c._order_safety_wal
        if fault == "same_stat_disk":
            path = wal.path if not archived else next(wal._archive.archive.directory.glob("*.json"))
            stat, raw = path.stat(), path.read_bytes()
            changed = raw.replace(b'"state":"TERMINAL"', b'"state":"INVALID!"', 1)
            assert changed != raw and len(changed) == len(raw)
            path.write_bytes(changed)
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        elif fault == "wal_memory":
            wal._records = {k: r for k, r in wal._records.items() if k != "past-00000"}
        elif fault == "ledger_memory":
            ledger = f.c._order_safety_reservations
            ledger._trades = {**ledger._trades, "history-00000": ("past-00000", 2, 1)}
        elif fault == "uncertain":
            wal._uncertain = True
        elif fault in ("missing", "wire", "terminal", "prepared", "session", "epoch"):
            rows = dict(wal._records.items())
            if fault == "missing":
                del rows[claim.intent_id]
            else:
                changes = {"wire": {"client_order_id": "different-wire"}, "terminal": {"state": "TERMINAL"},
                           "prepared": {"state": "PREPARED"}, "session": {"session_id": "different-session"},
                           "epoch": {"epoch": claim.epoch + 1}}[fault]
                rows[claim.intent_id] = replace(rows[claim.intent_id], **changes)
            # Coherent disk AND memory: denial must establish claim/row identity,
            # rather than depend on a stale cache or disk/memory mismatch.
            wal._save(rows)
            wal._records = wal.reopen()._records
        elif fault == "claim":
            refresh(f.v, f.feeds, pending=(replace(claim, quantity_base=claim.quantity_base * 2),))
        else:
            f.v[7]["value"] = replace(f.v[7]["value"], account_uid="other-account")
        assert not f.hedge.allows_spot(), fault
        assert f.v[1].claim_ids() == (claim.intent_id,)
