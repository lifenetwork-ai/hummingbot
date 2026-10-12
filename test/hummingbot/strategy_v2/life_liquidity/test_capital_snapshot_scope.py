"""Qualified capital observations use one fresh, serialized checkpoint scope."""
import json
import os
from dataclasses import asdict, replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_checkpoint_archive import capacity
from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital import claim, setup
from unittest.mock import Mock

import pytest

from hummingbot.strategy_v2.life_liquidity import policy_state
from hummingbot.strategy_v2.life_liquidity.shared_capital import _json


def initialized(tmp_path, archived):
    authority, feed = setup(tmp_path)
    assert authority.reserve(claim(amount="1")).allowed
    if archived:
        # Retained synthetic tombstone ensures segment bytes are independently
        # exercised as well as the active manifest on every qualification.
        past = claim("past", amount="1")
        with authority.journal.locked() as state:
            state["settled"][past.intent_id] = {"claim": _json(asdict(past)), "proof_id": "0" * 64}
            authority.journal.commit(state)
        authority.journal.migrate_archive(capacity())
    return authority, feed


@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "segmented"])
@pytest.mark.parametrize("change", ["clock", "snapshot", "both"])
def test_advancing_facts_are_observed_and_verified_once_under_one_lock(tmp_path, monkeypatch, archived, change):
    authority, feed = initialized(tmp_path, archived)
    if change in ("clock", "both"):
        feed["now"] += 1
    if change in ("snapshot", "both"):
        feed["snapshot"] = replace(feed["snapshot"], sequence=2, collateral_quote=Decimal("11"))
    verified = Mock(wraps=authority.journal._verified)
    observed = Mock(wraps=authority.observation)
    clock = Mock(wraps=authority.clock_ms)
    written = Mock(wraps=authority.journal._write)
    monkeypatch.setattr(authority.journal, "_verified", verified)
    monkeypatch.setattr(authority, "observation", observed)
    monkeypatch.setattr(authority, "clock_ms", clock)
    monkeypatch.setattr(authority.journal, "_write", written)
    assert authority.qualified_snapshot() is feed["snapshot"]
    assert verified.call_count == observed.call_count == clock.call_count == 1
    assert written.call_count == 1
    assert authority.journal._state["last_checked_ms"] == feed["now"]
    assert authority.journal._state["snapshot"] == _json(asdict(feed["snapshot"]))
    assert authority.retained_claim_ids() == ("spot",)


@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "segmented"])
def test_unchanged_snapshot_still_observes_and_verifies_but_never_writes(tmp_path, monkeypatch, archived):
    authority, feed = initialized(tmp_path, archived)
    verified = Mock(wraps=authority.journal._verified)
    observed = Mock(wraps=authority.observation)
    written = Mock(wraps=authority.journal._write)
    monkeypatch.setattr(authority.journal, "_verified", verified)
    monkeypatch.setattr(authority, "observation", observed)
    monkeypatch.setattr(authority.journal, "_write", written)
    raw = authority.journal.path.read_bytes()
    for _ in range(2):
        assert authority.qualified_snapshot() is feed["snapshot"]
    assert verified.call_count == observed.call_count == 2
    written.assert_not_called()
    assert authority.journal.path.read_bytes() == raw


@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "segmented"])
@pytest.mark.parametrize("fault", ["same_stat", "memory", "symlink", "missing", "uncertain", "policy"])
def test_previous_snapshot_cannot_hide_changed_storage_or_policy(tmp_path, archived, fault):
    authority, feed = initialized(tmp_path, archived)
    assert authority.qualified_snapshot() is feed["snapshot"]
    if fault == "same_stat":
        path = authority.journal.path
        if archived:
            manifest = json.loads(path.read_bytes())
            sha = manifest["archives"][0]["segments"][0]["sha256"]
            path = authority.journal.archive.directory / (sha + ".json")
        raw, stat = path.read_bytes(), path.stat()
        changed = raw.replace(b'"wire-past"', b'"wire-fake"') if archived else raw.replace(
            b'"collateral_quote":"10"', b'"collateral_quote":"11"')
        assert changed != raw and len(changed) == len(raw)
        path.write_bytes(changed)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    elif fault == "memory":
        authority.journal._state = {**authority.journal._state, "last_checked_ms": 999}
    elif fault == "symlink":
        path = authority.journal.path
        foreign = tmp_path / "foreign-capital.json"
        foreign.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(foreign)
    elif fault == "missing":
        authority.journal.path.unlink()
    elif fault == "uncertain":
        authority.journal._uncertain = True
    else:
        authority.policy = replace(authority.policy, fee_rate=Decimal("0.002"))
    with pytest.raises(ValueError):
        authority.qualified_snapshot()
    assert authority.retained_claim_ids() == ("spot",)


@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "segmented"])
@pytest.mark.parametrize("fault", ["sequence", "mutation", "observation_clock", "stale", "future", "scope", "clock", "bool_clock"])
def test_invalid_observations_never_publish_or_replace_the_previous_snapshot(tmp_path, archived, fault):
    authority, feed = initialized(tmp_path, archived)
    feed["snapshot"] = replace(feed["snapshot"], sequence=2)
    assert authority.qualified_snapshot() is feed["snapshot"]
    raw = authority.journal.path.read_bytes()
    if fault == "sequence":
        feed["snapshot"] = replace(feed["snapshot"], sequence=1)
    elif fault == "mutation":
        feed["snapshot"] = replace(feed["snapshot"], collateral_quote=Decimal("11"))
    elif fault == "observation_clock":
        feed["snapshot"] = replace(feed["snapshot"], sequence=3, observed_at_ms=999)
    elif fault == "stale":
        feed["now"] = 2001
    elif fault == "future":
        feed["snapshot"] = replace(feed["snapshot"], sequence=3, observed_at_ms=1001)
    elif fault == "scope":
        feed["snapshot"] = replace(feed["snapshot"], sequence=3, scope_complete=False)
    else:
        feed["now"] = 999 if fault == "clock" else True
    with pytest.raises(ValueError):
        authority.qualified_snapshot()
    assert authority.journal.path.read_bytes() == raw
    assert authority.retained_claim_ids() == ("spot",)


@pytest.mark.parametrize("callback", ["observation", "clock_ms"])
def test_callbacks_cannot_commit_during_read_only_qualification(tmp_path, monkeypatch, callback):
    authority, _ = initialized(tmp_path, False)
    raw = authority.journal.path.read_bytes()
    original = getattr(authority, callback)

    def commit_from_callback():
        state = {**authority.journal._state, "last_checked_ms": 1001}
        authority.journal.commit(state)
        return original()

    monkeypatch.setattr(authority, callback, commit_from_callback)
    with pytest.raises(ValueError, match="LOCK_REQUIRED"):
        authority.qualified_snapshot()
    assert authority.journal.path.read_bytes() == raw
    assert not authority.journal._locked and not authority.journal._read_only


@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "segmented"])
def test_replacement_uncertainty_never_returns_a_qualified_snapshot(tmp_path, monkeypatch, archived):
    authority, feed = initialized(tmp_path, archived)
    feed["now"] = 1001
    fsync = policy_state.os.fsync
    replace_file = policy_state.os.replace
    published = []

    def publish(source, destination):
        replace_file(source, destination)
        if destination == authority.journal.path:
            published.append(True)

    def fail_directory(descriptor):
        import stat
        if published and stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("synthetic directory fsync failure")
        return fsync(descriptor)

    monkeypatch.setattr(policy_state.os, "replace", publish)
    monkeypatch.setattr(policy_state.os, "fsync", fail_directory)
    with pytest.raises(OSError, match="synthetic directory fsync failure"):
        authority.qualified_snapshot()
    assert authority.journal._uncertain
    assert authority.retained_claim_ids() == ("spot",)
    monkeypatch.setattr(policy_state.os, "fsync", fsync)
    with pytest.raises(ValueError, match="UNCERTAIN"):
        authority.qualified_snapshot()


@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "segmented"])
@pytest.mark.parametrize("advancing", [False, True], ids=["stable", "advancing"])
@pytest.mark.parametrize("callback", ["observation", "clock_ms"])
@pytest.mark.parametrize("fault", ["uncertain", "root", "memory", "policy", "journal_policy"])
def test_callback_cannot_publish_from_changed_checkpoint_or_policy(tmp_path, monkeypatch, archived, advancing, callback, fault):
    authority, feed = initialized(tmp_path, archived)
    if advancing:
        feed["now"] += 1
    original = getattr(authority, callback)
    raw = authority.journal.path.read_bytes()
    written = Mock(wraps=authority.journal._write)

    def changed_during_callback():
        if fault == "uncertain":
            authority.journal._uncertain = True
        elif fault == "root":
            path = authority.journal.path
            stat = path.stat()
            changed = raw.replace(b'"collateral_quote":"10"', b'"collateral_quote":"11"')
            assert changed != raw and len(changed) == len(raw)
            path.write_bytes(changed)
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        elif fault == "memory":
            authority.journal._state = {**authority.journal._state, "last_checked_ms": 999}
        elif fault == "policy":
            authority.policy = replace(authority.policy, fee_rate=Decimal("0.002"))
        else:
            authority.journal._policy_blob = authority.journal._policy_blob.replace('"0.001"', '"0.002"')
        return original()

    monkeypatch.setattr(authority, callback, changed_during_callback)
    monkeypatch.setattr(authority.journal, "_write", written)
    with pytest.raises(ValueError):
        authority.qualified_snapshot()
    written.assert_not_called()
    assert authority.retained_claim_ids() == ("spot",)
    if fault != "root":
        assert authority.journal.path.read_bytes() == raw


@pytest.mark.parametrize("callback", ["observation", "clock_ms"])
def test_advancing_callback_segment_tamper_denies_publication(tmp_path, monkeypatch, callback):
    authority, feed = initialized(tmp_path, True)
    feed["now"] += 1
    root = authority.journal.path.read_bytes()
    manifest = json.loads(root)
    sha = manifest["archives"][0]["segments"][0]["sha256"]
    path = authority.journal.archive.directory / (sha + ".json")
    raw, stat = path.read_bytes(), path.stat()
    original = getattr(authority, callback)

    def changed_segment():
        changed = raw.replace(b'"wire-past"', b'"wire-fake"')
        assert changed != raw and len(changed) == len(raw)
        path.write_bytes(changed)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        return original()

    monkeypatch.setattr(authority, callback, changed_segment)
    with pytest.raises(ValueError):
        authority.qualified_snapshot()
    # Publication independently rereads the referenced history before granting
    # a qualified return. A post-replace failure fences the writer; conservative
    # clock bookkeeping may be durable, while every financial hold is retained.
    assert authority.journal._uncertain
    assert authority.retained_claim_ids() == ("spot",)
    with pytest.raises(ValueError, match="UNCERTAIN"):
        authority.qualified_snapshot()
