"""Synthetic capacities: versioned, bounded checkpoints retain every tombstone."""
import json
import os
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.o7_scenarios import send, system
from test.hummingbot.strategy_v2.life_liquidity.o8_history import seed_settled_history

import pytest

from hummingbot.strategy_v2.life_liquidity.checkpoint_archive import ArchivePolicy
from hummingbot.strategy_v2.life_liquidity.policy_state import PolicyState


def capacity(paths=(("settled",),), **changes):
    # Test-only limits, explicitly supplied at migration/restore.
    return replace(ArchivePolicy(paths=paths, records_per_segment=2, max_segment_bytes=4096,
                                 max_archive_bytes=65536, max_segments=16, max_active_bytes=8192), **changes)


def checkpoint(tmp_path):
    return PolicyState(tmp_path / "state.json", policy={"account": "synthetic"},
                       initial={"settled": {str(i): {"wire": "wire-" + str(i)} for i in range(5)},
                                "claims": {}, "clock": 0}, create=True)


def restore(journal, policy):
    return PolicyState(journal.path, policy=journal.policy, initial={}, create=False, archive=policy)


def test_migration_restore_and_clock_writes_reuse_immutable_segments(tmp_path):
    journal = checkpoint(tmp_path)
    old = journal.path.read_bytes()
    policy = capacity()
    journal.migrate_archive(policy)
    manifest = json.loads(journal.path.read_bytes())
    assert manifest["schema_version"] == 2 and b"wire-" not in journal.path.read_bytes()
    files = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in journal.archive.directory.iterdir()}
    assert len(files) == 3
    assert restore(journal, policy)._state == journal._state
    with journal.locked(shallow=True) as state:
        state["clock"] = 1
        journal.commit(state)
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in journal.archive.directory.iterdir()} == files
    assert len(journal.path.read_bytes()) < len(old) + 1500
    with pytest.raises(ValueError, match="ARCHIVE_POLICY"):
        restore(journal, replace(policy, max_segments=20))
    with pytest.raises(ValueError, match="ARCHIVE_POLICY"):
        PolicyState(journal.path, policy=journal.policy, initial={}, create=False)
    with restore(journal, policy).inspect() as state:
        assert state["clock"] == 1 and len(state["settled"]) == 5
        with pytest.raises(TypeError):
            state["settled"]["0"]["wire"] = "reused"


@pytest.mark.parametrize("fault", ["missing", "same_stat_corruption", "symlink", "wrong_coverage", "duplicate_segment"])
def test_segment_faults_deny_warm_and_cold_reads(tmp_path, fault):
    journal = checkpoint(tmp_path)
    policy = capacity()
    journal.migrate_archive(policy)
    with journal.inspect():
        pass
    manifest = json.loads(journal.path.read_bytes())
    entry = manifest["archives"][0]
    path = journal.archive.directory / (entry["segments"][0]["sha256"] + ".json")
    if fault == "missing":
        path.unlink()
    elif fault == "same_stat_corruption":
        stat = path.stat()
        path.write_bytes(path.read_bytes().replace(b"wire-0", b"wire-x"))
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    elif fault == "symlink":
        target = tmp_path / "copied.json"
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target)
    else:
        if fault == "wrong_coverage":
            entry["count"] += 1
        else:
            entry["segments"].insert(0, entry["segments"][0])
            entry["count"] += entry["segments"][0]["count"]
        journal.path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        with journal.inspect():
            pass
    with pytest.raises(ValueError):
        restore(journal, policy)


def test_stale_writer_cannot_publish_or_migrate(tmp_path):
    journal = checkpoint(tmp_path)
    stale = PolicyState(journal.path, policy=journal.policy, initial={}, create=False)
    policy = capacity()
    journal.migrate_archive(policy)
    with pytest.raises(ValueError, match="STALE"):
        stale.migrate_archive(policy)
    stale = restore(journal, policy)
    with journal.locked(shallow=True) as state:
        state["clock"] = 1
        journal.commit(state)
    with pytest.raises(ValueError, match="STALE"):
        with stale.locked(shallow=True) as state:
            state["clock"] = 2
            stale.commit(state)


@pytest.mark.parametrize("bound", ["max_archive_bytes", "max_segments", "max_active_bytes", "max_segment_bytes"])
def test_capacity_exhaustion_preserves_previous_checkpoint(tmp_path, bound):
    journal = checkpoint(tmp_path)
    original = journal.path.read_bytes()
    policy = capacity(**{bound: 1})
    with pytest.raises(ValueError, match="ARCHIVE_CAPACITY"):
        journal.migrate_archive(policy)
    assert journal.path.read_bytes() == original
    assert journal.archive is None
    with journal.inspect() as state:
        assert len(state["settled"]) == 5


def test_interrupted_migration_leaves_legacy_state_and_reuses_orphans(tmp_path, monkeypatch):
    journal = checkpoint(tmp_path)
    old = journal.path.read_bytes()
    policy = capacity()
    original = os.replace

    def interrupt(source, target):
        if target == journal.path:
            raise OSError("synthetic publication failure")
        return original(source, target)
    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", interrupt)
        with pytest.raises(OSError):
            journal.migrate_archive(policy)
    assert journal.path.read_bytes() == old
    # An ambiguous manifest replacement locks this instance. Cold restore of
    # the still-valid legacy cut may explicitly retry the migration.
    cold = PolicyState(journal.path, policy=journal.policy, initial={}, create=False)
    cold.migrate_archive(policy)
    assert len(list(cold.archive.directory.iterdir())) == 3
    assert len(restore(cold, policy)._state["settled"]) == 5


@pytest.mark.parametrize("cut", ["segment", "before_manifest", "after_manifest"])
def test_killed_process_restores_complete_old_or_new_cut(tmp_path, cut):
    journal = checkpoint(tmp_path)
    script = '''
import os, sys
from pathlib import Path
from hummingbot.strategy_v2.life_liquidity.checkpoint_archive import ArchivePolicy
from hummingbot.strategy_v2.life_liquidity.policy_state import PolicyState
path, cut = Path(sys.argv[1]), sys.argv[2]
journal = PolicyState(path, policy={"account": "synthetic"}, initial={}, create=False)
replace = os.replace
def crash(source, target):
    if target == path and cut == "before_manifest":
        os._exit(71)
    replace(source, target)
    if (target == path and cut == "after_manifest"
            or Path(target).parent.name.endswith(".segments") and cut == "segment"):
        os._exit(71)
os.replace = crash
journal.migrate_archive(ArchivePolicy(paths=(("settled",),), records_per_segment=2,
    max_segment_bytes=4096, max_archive_bytes=65536, max_segments=16, max_active_bytes=8192))
'''
    result = subprocess.run([sys.executable, "-c", script, str(journal.path), cut], timeout=30,
                            capture_output=True, text=True)
    assert result.returncode == 71, result.stderr
    schema = json.loads(journal.path.read_bytes())["schema_version"]
    assert schema == (2 if cut == "after_manifest" else 1)
    cold = restore(journal, capacity()) if schema == 2 else PolicyState(
        journal.path, policy=journal.policy, initial={}, create=False)
    if schema == 1:
        cold.migrate_archive(capacity())
    assert restore(cold, capacity())._state["settled"] == journal._state["settled"]


def test_segment_directory_sync_precedes_manifest_publication(tmp_path, monkeypatch):
    journal = checkpoint(tmp_path)
    events, fsync, replace = [], os.fsync, os.replace
    directory = journal.path.with_name(journal.path.name + ".segments")

    def sync(fd):
        if directory.exists() and os.fstat(fd).st_ino == directory.stat().st_ino:
            events.append("segment_directory_sync")
        return fsync(fd)

    def publish(source, target):
        if target == journal.path:
            events.append("manifest_replace")
        else:
            events.append("segment_replace")
        return replace(source, target)
    monkeypatch.setattr(os, "fsync", sync)
    monkeypatch.setattr(os, "replace", publish)
    journal.migrate_archive(capacity())
    assert events[-2:] == ["segment_directory_sync", "manifest_replace"]
    assert events.count("segment_replace") == 3


def test_growth_limit_retains_current_claims_and_all_previous_segments(tmp_path):
    journal = checkpoint(tmp_path)
    policy = capacity(max_segments=3)
    journal.migrate_archive(policy)
    files = {p.name: p.read_bytes() for p in journal.archive.directory.iterdir()}
    old = journal.path.read_bytes()
    with pytest.raises(ValueError, match="ARCHIVE_CAPACITY"):
        with journal.locked() as state:
            state["settled"]["6"] = {"wire": "new-wire"}
            journal.commit(state)
    assert journal.path.read_bytes() == old
    assert {p.name: p.read_bytes() for p in journal.archive.directory.iterdir()} == files
    with journal.locked(shallow=True) as state:
        state["claims"] = {"pending": "unreleased"}
        journal.commit(state)
    cold = restore(journal, policy)
    assert cold._state["claims"] == {"pending": "unreleased"}
    assert cold._state["settled"] == journal._state["settled"]


def test_orphan_capacity_overflow_retains_verified_reads_and_clock_checkpoints(tmp_path):
    journal = checkpoint(tmp_path)
    policy = capacity(max_segments=3)
    journal.migrate_archive(policy)
    (journal.archive.directory / "interrupted-orphan").write_bytes(b"{}")
    with pytest.raises(ValueError, match="ARCHIVE_CAPACITY"):
        journal.archive.assert_capacity()
    cold = restore(journal, policy)
    with cold.locked(shallow=True) as state:
        state["clock"] = 1
        cold.commit(state)
    assert restore(cold, policy)._state["clock"] == 1
    assert len(restore(cold, policy)._state["settled"]) == 5


@pytest.mark.parametrize("fault", ["disabled", "replaced_policy", "directory"])
def test_warm_archive_binding_cannot_be_changed_to_bypass_verification(tmp_path, fault):
    journal = checkpoint(tmp_path)
    journal.migrate_archive(capacity())
    with journal.inspect():
        pass
    if fault == "disabled":
        journal.archive = None
    elif fault == "replaced_policy":
        journal.archive.policy = replace(journal.archive.policy, max_segments=50)
    else:
        journal.archive.directory = tmp_path / "other-directory"
    with pytest.raises(ValueError, match="STALE"):
        with journal.inspect():
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["capital", "joint"])
async def test_orphan_capacity_denies_risk_but_cancels_owned_orders(tmp_path, owner):
    async with system(tmp_path) as f:
        seed_settled_history(f, 4)
        q = f.c._quote_action_planner
        q.intent_id_factory = lambda: "pending-at-capacity"
        send(f, q.propose()[0])
        journal = f.v[1].journal if owner == "capital" else f.joint.journal
        paths = (("settled",),) if owner == "capital" else (("bundle", "orders"),)
        journal.migrate_archive(capacity(paths=paths, max_segments=2, max_segment_bytes=16384))
        (journal.archive.directory / "interrupted-orphan").write_bytes(b"{}")
        assert not f.c.allow_create_executor_actions()
        assert "pending-at-capacity" in f.v[1].claim_ids()
        f.v[6].advance(.001)
        f.c._order_safety_manager.pause("SYNTHETIC_ARCHIVE_CAPACITY")
        await f.c._cancel_and_reconcile_orders()
        assert f.v[3].cancels
        assert "pending-at-capacity" in f.v[1].claim_ids()


@pytest.mark.asyncio
async def test_capital_and_joint_checkpoint_migration_preserves_protected_send(tmp_path):
    async with system(tmp_path) as f:
        seed_settled_history(f, 4)
        capital, joint = f.v[1].journal, f.joint.journal
        capital.migrate_archive(capacity(max_segment_bytes=16384))
        joint.migrate_archive(capacity(paths=(("bundle", "orders"),), max_segment_bytes=16384))
        assert await f.joint.reconcile(), f.joint.reason_code
        assert f.joint.measure().nav_quote == 20
        q = f.c._quote_action_planner
        q.intent_id_factory = lambda: "after-archive"
        actions = q.propose()
        assert actions, q.reason_code
        send(f, actions[0])
        assert "after-archive" in f.v[1].claim_ids()
        assert len(f.v[1].claims(include_settled=True)) == 5
        f.c._order_safety_manager.pause("SYNTHETIC_ARCHIVE_CANCEL")
        await f.c._cancel_and_reconcile_orders()
        assert f.v[3].cancels
        assert "after-archive" in f.v[1].claim_ids()
        pool = f.v[1]
        cold = type(pool)(capital.path, policy=pool.policy, observation=pool.observation,
                          clock_ms=pool.clock_ms, create=False, checkpoint_archive=capital.archive.policy)
        assert cold.claims(include_settled=True) == pool.claims(include_settled=True)
        cold_joint = type(f.joint)(
            joint.path, controller=f.c, collect=f.joint.collect,
            cancel_swap=f.joint.cancel_swap, clock_ms=f.joint.clock_ms,
            max_age_ms=f.joint.max_age_ms, cancel_retry_ms=f.joint.cancel_retry_ms,
            max_cancels_per_cycle=f.joint.max_cancels_per_cycle,
            poll_interval_ms=f.joint.poll_interval_ms,
            opening_long_entry=Decimal(f.joint._binding["opening_long_entry"]),
            opening_short_entry=Decimal(f.joint._binding["opening_short_entry"]), create=False,
            checkpoint_archive=joint.archive.policy)
        assert cold_joint.journal._state == f.joint.journal._state
        assert not cold_joint.allocation_ready()  # Restore never grants automatic rearm.
