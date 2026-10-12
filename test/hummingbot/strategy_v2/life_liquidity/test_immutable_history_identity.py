"""Immutable identity avoids logical replay, never independent byte fences."""
from dataclasses import asdict, replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.o7_scenarios import send, system
from test.hummingbot.strategy_v2.life_liquidity.o8_history import (
    archive_financial_journals,
    archive_settled_history,
    seed_settled_history,
)
from test.hummingbot.strategy_v2.life_liquidity.test_capital_snapshot_scope import initialized
from test.hummingbot.strategy_v2.life_liquidity.test_checkpoint_archive import capacity, checkpoint
from unittest.mock import Mock

import pytest

from hummingbot.strategy_v2.life_liquidity.policy_state import _freeze, _FrozenDict
from hummingbot.strategy_v2.life_liquidity.shared_capital import _json


def self_comparisons(monkeypatch):
    calls = []

    def equal(left, right):
        if left is right:
            calls.append(len(left))
        return dict.__eq__(left, right)

    def unequal(left, right):
        if left is right:
            calls.append(len(left))
        return dict.__ne__(left, right)

    monkeypatch.setattr(_FrozenDict, "__eq__", equal)
    monkeypatch.setattr(_FrozenDict, "__ne__", unequal)
    return calls


@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "segmented"])
def test_verified_policy_endpoints_do_not_walk_their_own_immutable_image(tmp_path, monkeypatch, archived):
    journal = checkpoint(tmp_path)
    if archived:
        journal.migrate_archive(capacity())
    with journal.locked(shallow=True) as state:
        state["clock"] = 1
        journal.commit(state)
    comparisons = self_comparisons(monkeypatch)
    verified = Mock(wraps=journal._verified)
    monkeypatch.setattr(journal, "_verified", verified)
    for _ in range(2):
        with journal.inspect():
            journal.assert_current()
    assert verified.call_count == 2
    assert comparisons == []


@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "segmented"])
def test_capital_still_observes_each_call_without_comparing_settled_history_to_itself(tmp_path, monkeypatch, archived):
    capital, feed = initialized(tmp_path, archived)
    assert capital.check().allowed
    comparisons = self_comparisons(monkeypatch)
    observed = Mock(wraps=capital.observation)
    monkeypatch.setattr(capital, "observation", observed)
    for _ in range(2):
        assert capital.check().allowed
        assert capital.qualified_snapshot() is feed["snapshot"]
    assert observed.call_count == 4
    assert comparisons == []


def test_active_checkpoint_publication_reuses_the_identical_immutable_branch(tmp_path, monkeypatch):
    journal = checkpoint(tmp_path)
    journal.migrate_archive(capacity())
    comparisons = self_comparisons(monkeypatch)
    with journal.locked(shallow=True) as state:
        state["clock"] = 1
        journal.commit(state)
    assert comparisons == []


@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "segmented"])
@pytest.mark.parametrize("history", [0, 1000, 10000])
@pytest.mark.asyncio
async def test_actual_send_keeps_fresh_checks_without_immutable_self_comparisons(tmp_path, monkeypatch, archived, history):
    async with system(tmp_path) as f:
        seed_settled_history(f, history)
        if archived:
            archive_settled_history(f)
            archive_financial_journals(f)
        assert await f.joint.reconcile(), f.joint.reason_code
        comparisons = self_comparisons(monkeypatch)
        order, wire = send(f, f.c._quote_action_planner.propose()[0])
        check = f.v[3].sent[-1]["pre_send_check"]
        check(wire)
        assert comparisons == []
        # An independently repeated send fence still revokes a previously good
        # permission; no identity shortcut is a reusable trading permit.
        f.v[1].journal._uncertain = True
        with pytest.raises(PermissionError):
            check(wire)
        assert f.v[1].retained_claim_ids() == (order.intent_id,)
        assert len(f.v[3].sent) == 1


@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "segmented"])
@pytest.mark.parametrize("fault", ["memory", "root", "policy", "uncertain", "segment"])
def test_identity_never_hides_changed_checkpoint(tmp_path, archived, fault):
    capital, _ = initialized(tmp_path, archived)
    assert capital.check().allowed
    if fault == "memory":
        capital.journal._state = _freeze({**capital.journal._state, "last_checked_ms": 999})
    elif fault == "root":
        raw = capital.journal.path.read_bytes()
        changed = raw.replace(b'"last_checked_ms":1000', b'"last_checked_ms":999')
        assert changed != raw
        capital.journal.path.write_bytes(changed)
    elif fault == "policy":
        capital.policy = replace(capital.policy, fee_rate=Decimal("0.002"))
    elif fault == "uncertain":
        capital.journal._uncertain = True
    elif archived:
        path = next(capital.journal.archive.directory.iterdir())
        path.write_bytes(path.read_bytes().replace(b"wire-past", b"wire-fake"))
    else:
        capital.journal.path.write_bytes(capital.journal.path.read_bytes().replace(b'"wire-spot"', b'"wire-fake"'))
    assert not capital.check().allowed
    assert capital.retained_claim_ids() == ("spot",)


def test_different_settled_image_revalidates_full_identity_and_forbids_wire_reuse(tmp_path):
    from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital import claim

    capital, _ = initialized(tmp_path, True)
    assert capital.check().allowed
    past = claim("new-past", amount="1")
    with capital.journal.locked(shallow=True) as state:
        state["settled"] = {**state["settled"], past.intent_id: {"claim": _json(asdict(past)), "proof_id": "1" * 64}}
        capital.journal.commit(state)
    assert not capital.reserve(replace(past, intent_id="new-intent")).allowed
    assert past.intent_id in capital.claims(include_settled=True)
    assert capital.retained_claim_ids() == ("spot",)


@pytest.mark.parametrize("changed", [False, True])
def test_replaced_memory_uses_equality_fallback_and_rejects_mismatch(tmp_path, changed):
    journal = checkpoint(tmp_path)
    with journal.inspect():
        pass
    journal._state = _freeze({**journal._state, "clock": int(changed)})
    if changed:
        with pytest.raises(ValueError, match="STALE"):
            with journal.inspect():
                pass
    else:
        with journal.inspect() as state:
            assert state["clock"] == 0
