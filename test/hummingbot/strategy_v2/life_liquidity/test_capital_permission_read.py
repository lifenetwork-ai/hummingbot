"""Ownership and financial admission share one fresh capital journal read."""
from dataclasses import replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.o7_scenarios import send, system
from test.hummingbot.strategy_v2.life_liquidity.o8_history import archive_settled_history, seed_settled_history
from test.hummingbot.strategy_v2.life_liquidity.test_hedge_wal_scope import publish_pending
from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital import claim, setup

import pytest


def test_required_claim_is_checked_with_finances_without_a_separate_read(tmp_path, monkeypatch):
    cap, _ = setup(tmp_path)
    order = claim(amount="1")
    assert cap.reserve(order).allowed
    reads = []
    original = cap.journal._verified

    def verified():
        reads.append(True)
        return original()

    monkeypatch.setattr(cap.journal, "_verified", verified)
    assert cap.check(required_claims=(order,)).allowed
    assert reads == [True]


def test_external_pending_counts_as_risk_but_cannot_supply_owned_claim(tmp_path):
    cap, feed = setup(tmp_path)
    order = claim(amount="1")
    feed["snapshot"] = replace(feed["snapshot"], pending=(order,))
    assert cap.check().allowed
    decision = cap.check(required_claims=(order,))
    assert not decision.allowed
    assert decision.reason_code == "CAPITAL_REQUIRED_CLAIM_MISMATCH"
    assert cap.claim_ids() == ()


@pytest.mark.parametrize("field,value", [
    ("intent_id", "other"), ("wire_id", "other-wire"), ("account_uid", "54321"),
    ("session_id", "other-session"), ("epoch", 2), ("config_version", 2), ("risk_epoch", 2),
    ("product", "SWAP"), ("side", "SELL"), ("quantity_base", Decimal("2")),
    ("price_usdt", Decimal("2")),
])
def test_required_claim_checks_complete_identity_without_releasing_hold(tmp_path, field, value):
    cap, _ = setup(tmp_path)
    order = claim(amount="1")
    assert cap.reserve(order).allowed
    decision = cap.check(required_claims=(replace(order, **{field: value}),))
    assert not decision.allowed and decision.reason_code == "CAPITAL_REQUIRED_CLAIM_MISMATCH"
    assert cap.claims() == {order.intent_id: order}


@pytest.mark.parametrize("changes", [{"leverage": 3}, {"position_action": "CLOSE"}])
def test_required_swap_claim_checks_leverage_and_position_action(tmp_path, changes):
    cap, _ = setup(tmp_path)
    order = claim(product="SWAP", amount="1")
    assert cap.reserve(order).allowed
    decision = cap.check(required_claims=(replace(order, **changes),))
    assert not decision.allowed and decision.reason_code == "CAPITAL_REQUIRED_CLAIM_MISMATCH"
    assert cap.claims() == {order.intent_id: order}


@pytest.mark.parametrize("required", [None, [], {}, (None,)])
def test_invalid_required_claims_fail_closed(tmp_path, required):
    cap, _ = setup(tmp_path)
    decision = cap.check(required_claims=required)
    assert not decision.allowed and decision.reason_code == "CAPITAL_REQUIRED_CLAIM_MISMATCH"


@pytest.mark.parametrize("fault", ["uncertain", "disk", "clock", "sequence", "cash"])
def test_required_claim_never_reuses_prior_financial_permission(tmp_path, fault):
    cap, feed = setup(tmp_path)
    order = claim(amount="1")
    assert cap.reserve(order).allowed
    assert cap.check(required_claims=(order,)).allowed
    if fault == "uncertain":
        cap.journal._uncertain = True
    elif fault == "disk":
        cap.journal.path.write_bytes(b"{}")
    elif fault == "clock":
        feed["now"] -= 1
    else:
        feed["snapshot"] = replace(feed["snapshot"], **(
            {"sequence": 0} if fault == "sequence" else {"sequence": 2, "spot_cash_quote": Decimal("0")}))
    assert not cap.check(required_claims=(order,)).allowed
    assert cap.retained_claim_ids() == (order.intent_id,)


@pytest.mark.asyncio
@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "segmented"])
@pytest.mark.parametrize("pending", [False, True])
async def test_hedge_ownership_does_not_read_capital_claims_separately(tmp_path, monkeypatch, archived, pending):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        if archived:
            archive_settled_history(f)
        assert await f.joint.reconcile(), f.joint.reason_code
        if pending:
            order, _ = send(f, f.c._quote_action_planner.propose()[0])
            await publish_pending(f, order)

        def separate_read(*args, **kwargs):
            raise AssertionError("hedge gate read capital claims separately")

        monkeypatch.setattr(f.v[1], "claims", separate_read)
        assert f.hedge.allows_spot(), f.hedge.reason_code


@pytest.mark.asyncio
@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "segmented"])
async def test_market_callback_cannot_reuse_pre_callback_ownership(tmp_path, monkeypatch, archived):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        if archived:
            archive_settled_history(f)
        assert await f.joint.reconcile(), f.joint.reason_code
        order, _ = send(f, f.c._quote_action_planner.propose()[0])
        await publish_pending(f, order)
        assert f.hedge.allows_spot()
        cap, market = f.v[1], f.hedge.market

        def changed_ownership():
            # Simulate a coherent newer checkpoint after the first snapshot.
            # Pending risk remains financially valid; ownership must still deny.
            with cap.journal.locked(shallow=True) as state:
                state["claims"] = {}
                cap.journal.commit(state)
            return market()

        monkeypatch.setattr(f.hedge, "market", changed_ownership)
        assert not f.hedge.allows_spot()


@pytest.mark.asyncio
@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "segmented"])
async def test_final_send_rechecks_capital_after_healthy_permission(tmp_path, archived):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        if archived:
            archive_settled_history(f)
        assert await f.joint.reconcile(), f.joint.reason_code
        order, wire = send(f, f.c._quote_action_planner.propose()[0])
        check = f.v[3].sent[-1]["pre_send_check"]
        check(wire)
        f.v[1].journal._uncertain = True
        with pytest.raises(PermissionError):
            check(wire)
        assert f.v[1].retained_claim_ids() == (order.intent_id,)
        assert len(f.v[3].sent) == 1
