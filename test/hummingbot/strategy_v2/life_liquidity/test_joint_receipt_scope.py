"""Advancing joint receipts verify one locked cut and retain revocation fences."""
import json
import os
from test.hummingbot.strategy_v2.life_liquidity.test_joint_recovery import setup_recovery
from unittest.mock import Mock

import pytest

from hummingbot.strategy_v2.life_liquidity.checkpoint_archive import ArchivePolicy
from hummingbot.strategy_v2.life_liquidity.policy_state import PolicyState


def test_callback_fence_requires_a_verified_scope_and_grants_no_write(tmp_path):
    journal = PolicyState(tmp_path / "policy.json", policy={"v": 1}, initial={"clock": 0}, create=True)
    with pytest.raises(ValueError, match="LOCK_REQUIRED"):
        journal.assert_current()
    with journal.inspect():
        journal.assert_current()
        with pytest.raises(ValueError, match="LOCK_REQUIRED"):
            journal.commit({"clock": 1})


@pytest.mark.asyncio
@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "segmented"])
@pytest.mark.parametrize("advance", [False, True], ids=["same-time", "advancing"])
async def test_receipt_uses_one_verified_scope_without_unnecessary_writes(tmp_path, monkeypatch, archived, advance):
    joint, values, _, _ = setup_recovery(tmp_path)
    assert await joint.reconcile(), joint.reason_code
    if archived:
        joint.journal.migrate_archive(ArchivePolicy(
            paths=(("bundle", "orders"),), records_per_segment=500, max_segment_bytes=1024 * 1024,
            max_archive_bytes=64 * 1024 * 1024, max_segments=512, max_active_bytes=256 * 1024))
    if advance:
        values[6].advance(0.001)
    verified = Mock(wraps=joint.journal._verified)
    commit = Mock(wraps=joint.journal.commit)
    monkeypatch.setattr(joint.journal, "_verified", verified)
    monkeypatch.setattr(joint.journal, "commit", commit)
    state = joint._fresh()
    assert verified.call_count == 1
    assert commit.call_count == int(advance)
    assert state["last_ms"] == joint.clock_ms()
    with pytest.raises(TypeError):
        state["last_ms"] = 0
    with pytest.raises(TypeError):
        state["bundle"]["orders"].append({})


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["health", "context", "runner", "stale", "sequence"])
async def test_rejected_receipt_persists_clock_advance_and_never_rearms(tmp_path, fault):
    joint, values, _, _ = setup_recovery(tmp_path)
    assert await joint.reconcile(), joint.reason_code
    values[6].advance(0.001)
    now = joint.clock_ms()
    if fault == "health":
        joint._healthy = False
    elif fault == "context":
        joint._receipt_context = None
    elif fault == "runner":
        values[0]._runner_scope_invalid = True
    elif fault == "stale":
        values[6].advance(2)
        now = joint.clock_ms()
    else:
        from dataclasses import replace
        values[8]["snapshot"] = replace(values[8]["snapshot"], sequence=2)
    with pytest.raises(ValueError):
        joint._fresh()
    assert json.loads(joint.journal.path.read_bytes())["state"]["last_ms"] == now
    assert not joint.allocation_ready()
    assert not joint.journal._locked and not joint.journal._read_only


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["disk", "memory", "uncertain", "clock", "nested_write", "advanced_nested_write", "clock_write"])
async def test_single_receipt_scope_keeps_independent_fences(tmp_path, fault):
    joint, values, _, _ = setup_recovery(tmp_path)
    assert await joint.reconcile(), joint.reason_code
    if fault == "disk":
        stat = joint.journal.path.stat()
        raw = joint.journal.path.read_bytes()
        changed = raw.replace(b'"COMMITTED"', b'"CORRUPTED"')
        assert changed != raw and len(changed) == len(raw)
        joint.journal.path.write_bytes(changed)
        os.utime(joint.journal.path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    elif fault == "memory":
        joint.journal._state = {**joint.journal._state, "phase": "APPLYING"}
    elif fault == "uncertain":
        joint.journal._uncertain = True
    elif fault == "clock":
        values[6].advance(-0.001)
    elif fault == "clock_write":
        original = joint.clock_ms

        def clock():
            joint.journal.commit({**joint.journal._state, "last_ms": 0})
            return original()
        joint.clock_ms = clock
    else:
        if fault == "advanced_nested_write":
            values[6].advance(0.001)
        original = joint.capital.observation

        def observation():
            joint.journal.commit({**joint.journal._state, "last_ms": 0})
            return original()
        joint.capital.observation = observation
    assert not joint.allocation_ready()
    assert not joint.journal._locked and not getattr(joint.journal, "_read_only", False)


@pytest.mark.asyncio
@pytest.mark.parametrize("callback", ["clock", "observation"])
@pytest.mark.parametrize("fault", ["uncertain", "memory", "root", "symlink", "binding"])
async def test_callbacks_cannot_publish_or_authorize_a_changed_checkpoint(tmp_path, callback, fault):
    joint, values, _, _ = setup_recovery(tmp_path)
    assert await joint.reconcile(), joint.reason_code
    values[6].advance(0.001)
    target, name = (joint, "clock_ms") if callback == "clock" else (joint.capital, "observation")
    original = getattr(target, name)

    def changed():
        if fault == "uncertain":
            joint.journal._uncertain = True
        elif fault == "memory":
            joint.journal._state = {**joint.journal._state, "phase": "APPLYING"}
        elif fault == "root":
            raw = joint.journal.path.read_bytes()
            joint.journal.path.write_bytes(raw.replace(b'"COMMITTED"', b'"CORRUPTED"'))
        elif fault == "binding":
            joint.journal._policy_blob = "{}"
        else:
            path = joint.journal.path
            foreign = tmp_path / "foreign-joint.json"
            foreign.write_bytes(path.read_bytes())
            path.unlink()
            path.symlink_to(foreign)
        return original()
    setattr(target, name, changed)
    assert not joint.allocation_ready()
    assert not joint.journal._locked and not getattr(joint.journal, "_read_only", False)


@pytest.mark.asyncio
async def test_advancing_clock_segment_tamper_denies_and_retains_uncertain_cut(tmp_path):
    from test.hummingbot.strategy_v2.life_liquidity.o7_scenarios import system
    from test.hummingbot.strategy_v2.life_liquidity.o8_history import archive_settled_history, seed_settled_history

    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        archive_settled_history(f)
        assert await f.joint.reconcile(), f.joint.reason_code
        f.v[6].advance(0.001)
        journal = f.joint.journal
        manifest = json.loads(journal.path.read_bytes())
        sha = manifest["archives"][0]["segments"][0]["sha256"]
        path = journal.archive.directory / (sha + ".json")
        original = f.joint.clock_ms

        def clock():
            raw, stat = path.read_bytes(), path.stat()
            changed = raw.replace(b"past-", b"evil-", 1)
            assert changed != raw and len(changed) == len(raw)
            path.write_bytes(changed)
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            return original()
        f.joint.clock_ms = clock
        assert not f.joint.allocation_ready()
        # Root publication can precede the resulting cut's segment check.
        # The writer remains fenced; no receipt or claim release follows.
        assert journal._uncertain
        assert not journal._locked and not journal._read_only
        assert not f.joint.allocation_ready()


@pytest.mark.asyncio
async def test_receipt_clock_commit_failure_revokes_then_unlocks(tmp_path, monkeypatch):
    joint, values, _, _ = setup_recovery(tmp_path)
    assert await joint.reconcile(), joint.reason_code
    values[6].advance(0.001)

    def fail(state):
        joint.journal._uncertain = True
        raise OSError("synthetic ambiguous checkpoint")
    monkeypatch.setattr(joint.journal, "commit", fail)
    assert not joint.allocation_ready()
    assert not joint.journal._locked and not getattr(joint.journal, "_read_only", False)
    assert not joint.allocation_ready()
