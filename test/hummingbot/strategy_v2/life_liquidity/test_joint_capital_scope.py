"""One synchronous joint → capital allocation cut, no reusable permission."""
import json
import os
from dataclasses import replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.o7_scenarios import send, system
from test.hummingbot.strategy_v2.life_liquidity.o8_history import (
    archive_financial_journals,
    archive_settled_history,
    seed_settled_history,
)
from test.hummingbot.strategy_v2.life_liquidity.test_joint_recovery import setup_recovery
from unittest.mock import Mock

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("advance", [False, True])
@pytest.mark.parametrize("operation", ["check", "reserve", "authorize"])
async def test_allocation_observes_once_with_one_verified_capital_scope(tmp_path, monkeypatch, advance, operation):
    from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital import claim

    joint, v, _, _ = setup_recovery(tmp_path)
    assert await joint.reconcile(), joint.reason_code
    cap = v[1]
    order = claim(amount="1")
    if operation == "authorize":
        assert cap.reserve(order).allowed
    if advance:
        v[6].advance(0.001)
    verified = Mock(wraps=cap.journal._verified)
    observed = Mock(wraps=cap.observation)
    original = cap.observation

    def observation():
        assert joint.journal._locked and joint.journal._read_only
        assert cap.journal._locked and cap.journal._read_only
        return observed()

    monkeypatch.setattr(cap.journal, "_verified", verified)
    monkeypatch.setattr(cap, "observation", observation)
    decision = cap.check() if operation == "check" else getattr(cap, operation)(order)
    assert decision.allowed, decision.reason_code
    assert verified.call_count == observed.call_count == 1
    assert not cap.journal._locked and not cap.journal._read_only
    assert not joint.journal._locked and not joint.journal._read_only
    monkeypatch.setattr(cap, "observation", original)
    if advance:
        assert json.loads(joint.journal.path.read_bytes())["state"]["last_ms"] == joint.clock_ms()


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["capital", "joint"])
@pytest.mark.parametrize("fault", ["uncertain", "memory", "disk", "policy", "nested_write"])
async def test_observer_cannot_authorize_changed_or_reentrant_checkpoint(tmp_path, owner, fault):
    from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital import claim

    joint, v, _, _ = setup_recovery(tmp_path)
    assert await joint.reconcile(), joint.reason_code
    cap = v[1]
    order = claim(amount="1")
    assert cap.reserve(order).allowed
    journal = cap.journal if owner == "capital" else joint.journal
    original = cap.observation

    def changed():
        if fault == "uncertain":
            journal._uncertain = True
        elif fault == "memory":
            journal._state = {**journal._state, **({"last_checked_ms": 0} if owner == "capital" else {"phase": "APPLYING"})}
        elif fault == "disk":
            journal.path.write_bytes(b"{}")
        elif fault == "policy":
            journal._policy_blob = "{}"
        else:
            journal.commit(dict(journal._state))
        return original()

    cap.observation = changed
    assert not cap.authorize(order).allowed
    assert cap.retained_claim_ids() == (order.intent_id,)
    assert not cap.journal._locked and not cap.journal._read_only
    assert not joint.journal._locked and not joint.journal._read_only


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["armed", "health", "context", "runner", "sequence", "cash", "live_policy"])
async def test_observed_joint_revocation_and_financial_change_deny_without_release(tmp_path, fault):
    from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital import claim

    joint, v, _, _ = setup_recovery(tmp_path)
    assert await joint.reconcile(), joint.reason_code
    cap = v[1]
    order = claim(amount="1")
    assert cap.reserve(order).allowed
    original = cap.observation

    def changed():
        if fault == "armed":
            joint._armed = False
        elif fault == "health":
            joint._healthy = False
        elif fault == "context":
            joint._receipt_context = None
        elif fault == "runner":
            v[0]._runner_scope_invalid = True
        elif fault == "live_policy":
            cap.policy = replace(cap.policy, fee_rate=Decimal("0.002"))
        obs = original()
        if fault == "sequence":
            return replace(obs, sequence=obs.sequence + 1)
        if fault == "cash":
            return replace(obs, sequence=obs.sequence + 1, spot_cash_quote=Decimal("0"))
        return obs

    cap.observation = changed
    assert not cap.authorize(order).allowed
    assert cap.retained_claim_ids() == (order.intent_id,)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["armed", "health", "context", "uncertain"])
async def test_revocation_during_financial_evaluation_cannot_publish_a_new_claim(tmp_path, monkeypatch, fault):
    from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital import claim

    joint, v, _, _ = setup_recovery(tmp_path)
    assert await joint.reconcile(), joint.reason_code
    cap = v[1]
    original = cap._evaluate

    def evaluate(*args):
        decision = original(*args)
        if fault == "armed":
            joint._armed = False
        elif fault == "health":
            joint._healthy = False
        elif fault == "context":
            joint._receipt_context = None
        else:
            joint.journal._uncertain = True
        return decision

    monkeypatch.setattr(cap, "_evaluate", evaluate)
    assert not cap.reserve(claim(amount="1")).allowed
    assert cap.retained_claim_ids() == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "segmented"])
@pytest.mark.parametrize("history", [0, 1000, 10000])
async def test_actual_sender_preserves_independent_final_joint_admission(tmp_path, archived, history):
    async with system(tmp_path) as f:
        seed_settled_history(f, history)
        if archived:
            archive_settled_history(f)
            archive_financial_journals(f)
        assert await f.joint.reconcile(), f.joint.reason_code
        order, wire = send(f, f.c._quote_action_planner.propose()[0])
        callback = f.v[3].sent[-1]["pre_send_check"]
        callback(wire)
        f.joint._armed = False
        with pytest.raises(PermissionError):
            callback(wire)
        assert f.v[1].retained_claim_ids() == (order.intent_id,)
        assert len(f.v[3].sent) == 1


@pytest.mark.asyncio
async def test_scope_validation_cannot_escape_or_lock_in_reverse_order(tmp_path):
    joint, v, _, _ = setup_recovery(tmp_path)
    assert await joint.reconcile(), joint.reason_code
    cap = v[1]
    obs = cap.qualified_snapshot()
    with joint._capital_allocation(cap) as validate:
        with pytest.raises(ValueError):
            validate(obs, cap.clock_ms())  # No capital lock, no authority.
        with cap.journal.locked(shallow=True):
            validate(obs, cap.clock_ms())
    with cap.journal.locked(shallow=True):
        with pytest.raises(ValueError):
            validate(obs, cap.clock_ms())
        with pytest.raises(ValueError):
            with joint._capital_allocation(cap):
                pass


@pytest.mark.asyncio
async def test_observation_segment_corruption_is_checked_before_unchanged_authorization(tmp_path):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        archive_settled_history(f)
        assert await f.joint.reconcile(), f.joint.reason_code
        cap = f.v[1]
        order, _ = send(f, f.c._quote_action_planner.propose()[0])
        manifest = json.loads(cap.journal.path.read_bytes())
        sha = manifest["archives"][0]["segments"][0]["sha256"]
        path = cap.journal.archive.directory / (sha + ".json")
        original = cap.observation

        def corrupted():
            raw, stat = path.read_bytes(), path.stat()
            changed = raw.replace(b"past-", b"evil-", 1)
            assert changed != raw and len(changed) == len(raw)
            path.write_bytes(changed)
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            return original()

        cap.observation = corrupted
        assert not cap.authorize(order).allowed
        assert cap.retained_claim_ids() == (order.intent_id,)


@pytest.mark.asyncio
async def test_observation_cannot_rebind_the_capital_recovery_authority(tmp_path):
    joint, v, _, _ = setup_recovery(tmp_path)
    assert await joint.reconcile(), joint.reason_code
    cap = v[1]
    original = cap.observation

    def rebound():
        cap._recovery = None
        return original()

    cap.observation = rebound
    assert not cap.check().allowed


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["capital_uncertain", "joint_health", "capital_policy"])
async def test_revocation_after_publication_denies_but_retains_the_published_hold(tmp_path, monkeypatch, fault):
    from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital import claim

    joint, v, _, _ = setup_recovery(tmp_path)
    assert await joint.reconcile(), joint.reason_code
    cap = v[1]
    order = claim(amount="1")
    original = cap.journal.commit

    def commit(state):
        original(state)
        if fault == "capital_uncertain":
            cap.journal._uncertain = True
        elif fault == "joint_health":
            joint._healthy = False
        else:
            cap.policy = replace(cap.policy, fee_rate=Decimal("0.002"))

    monkeypatch.setattr(cap.journal, "commit", commit)
    assert not cap.reserve(order).allowed
    assert cap.retained_claim_ids() == (order.intent_id,)
    assert order.intent_id in json.loads(cap.journal.path.read_bytes())["state"]["claims"]
    assert not cap.journal._locked and not cap.journal._read_only
    assert not joint.journal._locked and not joint.journal._read_only


@pytest.mark.asyncio
async def test_invalid_clock_cannot_run_observation_or_leave_the_capital_lock_held(tmp_path, monkeypatch):
    joint, v, _, _ = setup_recovery(tmp_path)
    assert await joint.reconcile(), joint.reason_code
    cap = v[1]
    observed = Mock(wraps=cap.observation)
    monkeypatch.setattr(cap, "clock_ms", lambda: True)
    monkeypatch.setattr(cap, "observation", observed)
    assert not cap.check().allowed
    observed.assert_not_called()
    assert not cap.journal._locked and not cap.journal._read_only


@pytest.mark.asyncio
async def test_unarmed_joint_refuses_capital_before_observing_account_facts(tmp_path, monkeypatch):
    joint, v, _, _ = setup_recovery(tmp_path)
    assert await joint.reconcile(), joint.reason_code
    joint._armed = False
    cap = v[1]
    observed = Mock(wraps=cap.observation)
    monkeypatch.setattr(cap, "observation", observed)
    decision = cap.check()
    assert not decision.allowed and decision.reason_code == "CAPITAL_JOINT_RECOVERY_REQUIRED"
    observed.assert_not_called()
    assert cap.retained_claim_ids() == ()
