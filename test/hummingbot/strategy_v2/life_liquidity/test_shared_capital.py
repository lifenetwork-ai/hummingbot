"""O.7.2 one account capital allocation; all observations/limits are synthetic."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from threading import Barrier

import pytest

from hummingbot.strategy_v2.life_liquidity.joint_exposure import JointRiskLimits
from hummingbot.strategy_v2.life_liquidity.shared_capital import (
    CapitalClaim,
    CapitalPolicy,
    CapitalSnapshot,
    MarginTier,
    SharedCapitalAuthority,
)

D = Decimal


def policy(**updates):
    values = dict(account_uid="12345", account_mode="2", collateral_currency="USDT", position_mode="HEDGE", leverage=2,
                  contract_value_life=D("0.25"), lot_contracts=D("0.1"), max_age_ms=1000,
                  fee_rate=D("0.001"), price_stress_bps=D("1000"), max_stress_loss_quote=D("100"),
                  limits=JointRiskLimits(D("100"), D("200"), D("1"), D("100"), D("10"), D("500")),
                  tiers=(MarginTier(D("10"), D("0.5"), D("0.1")),
                         MarginTier(D("1000"), D("0.6"), D("0.2"))))
    values.update(updates)
    return CapitalPolicy(**values)


def snapshot(**updates):
    values = dict(account_uid="12345", account_mode="2", collateral_currency="USDT", position_mode="HEDGE", margin_mode="cross", leverage=2,
                  observed_at_ms=1000, sequence=1, scope_complete=True, account_source="okx_account",
                  spot_life_base=D("0"), spot_cash_quote=D("10"), long_base=D("0"), short_base=D("0"),
                  collateral_quote=D("10"), initial_margin_quote=D("0"), maintenance_margin_quote=D("0"),
                  funding_liability_quote=D("0"), mark_price_usdt=D("1"), index_price_usdt=D("1"),
                  mark_source="okx_mark", index_source="okx_index", pending=())
    values.update(updates)
    return CapitalSnapshot(**values)


def claim(key="spot", *, product="SPOT", side="BUY", amount="6", action="OPEN", price="1"):
    return CapitalClaim(key, "wire-" + key, "12345", "session", 1, 1, 1, product, side,
                        action, D(amount), D(price), 1 if product == "SPOT" else 2)


def setup(tmp_path, *, configured=None, observed=None, create=True):
    feed = {"snapshot": observed or snapshot(), "now": 1000}
    authority = SharedCapitalAuthority(tmp_path / "capital.json", policy=configured or policy(),
                                       observation=lambda: feed["snapshot"], clock_ms=lambda: feed["now"], create=create)
    return authority, feed


def test_spot_and_swap_cannot_spend_the_same_cash_or_collateral(tmp_path):
    authority, feed = setup(tmp_path)
    assert authority.reserve(claim()).allowed
    denied = authority.reserve(claim("hedge", product="SWAP", side="SELL", amount="6"))
    assert not denied.allowed and denied.reason_code == "CAPITAL_INITIAL_MARGIN_LOW"
    assert authority.claim_ids() == ("spot",)
    restored, _ = setup(tmp_path, create=False)
    assert restored.claim_ids() == ("spot",)
    assert not restored.reserve(claim("hedge", product="SWAP", side="SELL", amount="6")).allowed


def test_concurrent_individually_valid_approvals_are_serialized(tmp_path):
    authority, _ = setup(tmp_path)
    barrier = Barrier(2)

    def reserve(order):
        barrier.wait(timeout=3)
        return authority.reserve(order)

    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(reserve, (claim(amount="7"), claim("swap", product="SWAP", side="SELL", amount="7"))))
    assert sum(result.allowed for result in results) == 1
    assert len(authority.claim_ids()) == 1


def test_pending_hedge_does_not_net_away_one_sided_spot_fill(tmp_path):
    configured = policy(limits=JointRiskLimits(D("5"), D("200"), D("1"), D("100"), D("10"), D("0")))
    authority, _ = setup(tmp_path, configured=configured, observed=snapshot(collateral_quote=D("100")))
    assert authority.reserve(claim("hedge", product="SWAP", side="SELL", amount="4")).allowed
    denied = authority.reserve(claim(amount="6"))
    assert not denied.allowed and denied.reason_code == "CAPITAL_DELTA_LIMIT"


def test_hedge_mode_long_and_short_count_in_gross_and_tiers_despite_zero_delta(tmp_path):
    observed = snapshot(long_base=D("6"), short_base=D("6"), collateral_quote=D("8"))
    authority, _ = setup(tmp_path, observed=observed)
    decision = authority.check()
    assert not decision.allowed and decision.reason_code == "CAPITAL_INITIAL_MARGIN_LOW"
    assert decision.gross_worst_base == D("12") and decision.net_delta_worst_base == 0
    assert decision.initial_margin_quote == D("7.92")  # 12 * stressed 1.1 * tier 0.6


def test_margin_counts_account_initial_requirement_once_and_preserves_other_positions(tmp_path):
    authority, _ = setup(
        tmp_path, observed=snapshot(long_base=D("2"), initial_margin_quote=D("4"),
                                    maintenance_margin_quote=D("1"), collateral_quote=D("100")))
    before = authority.check()
    assert before.initial_margin_quote == D("4.1")  # Account 4 + LIFE mark stress increment .1.
    after = authority.reserve(claim("swap", product="SWAP", amount="2"))
    assert after.allowed and after.initial_margin_quote == D("5.2")


def test_basis_and_directional_stress_remain_when_delta_is_flat(tmp_path):
    configured = policy(limits=JointRiskLimits(D("100"), D("200"), D("1"), D("0.4"), D("10"), D("500")))
    authority, _ = setup(tmp_path, configured=configured,
                         observed=snapshot(spot_life_base=D("10"), short_base=D("10"), collateral_quote=D("100")))
    result = authority.check()
    assert result.net_delta_worst_base == 0 and result.basis_loss_quote == D("0.55")
    assert not result.allowed and result.reason_code == "CAPITAL_BASIS_LIMIT"


def test_identical_pending_identity_is_counted_once_and_mutations_are_rejected(tmp_path):
    authority, feed = setup(tmp_path)
    order = claim(amount="4")
    assert authority.reserve(order).allowed
    feed["snapshot"] = replace(feed["snapshot"], sequence=2, pending=(order,))
    assert authority.authorize(order).allowed
    assert authority.check().spot_buy_hold_quote == D("4")
    feed["snapshot"] = replace(feed["snapshot"], sequence=3, pending=(replace(order, quantity_base=D("3")),))
    assert not authority.authorize(order).allowed


@pytest.mark.parametrize("fault", ["uid", "stale", "future", "scope", "source", "mode", "leverage", "nan", "funding", "tier", "rollback", "sequence", "journal"])
def test_unqualified_or_changed_account_evidence_revokes_existing_claim_without_release(tmp_path, fault):
    authority, feed = setup(tmp_path)
    order = claim(amount="1")
    assert authority.reserve(order).allowed
    changes = {"uid": {"account_uid": "99999"}, "future": {"observed_at_ms": 1001},
               "scope": {"scope_complete": False}, "source": {"mark_source": "internal_quote"},
               "mode": {"margin_mode": "isolated"}, "leverage": {"leverage": 3},
               "nan": {"collateral_quote": D("NaN")}, "funding": {"funding_liability_quote": D("11")},
               "tier": {"long_base": D("1001")}, "sequence": {"sequence": 0}}
    if fault == "stale":
        feed["now"] = 2001
    elif fault == "rollback":
        feed["now"] = 999
    elif fault == "journal":
        authority.journal.path.unlink()
    else:
        feed["snapshot"] = replace(feed["snapshot"], **changes[fault])
    assert not authority.authorize(order).allowed
    # No release method is inferred from stale/unknown evidence or an ACK.
    assert authority.retained_claim_ids() == ("spot",)


def test_changed_intent_cannot_reuse_an_existing_capital_approval(tmp_path):
    authority, _ = setup(tmp_path)
    order = claim(amount="1")
    assert authority.reserve(order).allowed
    assert authority.reserve(order).allowed
    assert not authority.reserve(replace(order, side="SELL")).allowed
    assert not authority.authorize(replace(order, wire_id="other")).allowed
    assert not authority.authorize(claim("unreserved", amount="1")).allowed


def test_two_writer_instances_cannot_overwrite_each_others_claims(tmp_path):
    first, _ = setup(tmp_path)
    second, _ = setup(tmp_path, create=False)
    assert first.reserve(claim(amount="7")).allowed
    assert not second.reserve(claim("swap", product="SWAP", side="SELL", amount="7")).allowed
    restored, _ = setup(tmp_path, create=False)
    assert restored.claim_ids() == ("spot",)


def test_maintenance_observation_can_block_independently_of_model_initial_margin(tmp_path):
    authority, _ = setup(tmp_path, observed=snapshot(maintenance_margin_quote=D("10")))
    decision = authority.check()
    assert not decision.allowed and decision.reason_code == "CAPITAL_MAINTENANCE_MARGIN_LOW"


def test_unowned_pending_orders_consume_capacity_without_proceeds_credit(tmp_path):
    pending = claim("external", product="SWAP", side="SELL", amount="6")
    authority, _ = setup(tmp_path, observed=snapshot(pending=(pending,)))
    assert authority.check().allowed
    assert not authority.reserve(claim()).allowed


def test_close_and_sale_reservations_cannot_spend_same_actual_holdings(tmp_path):
    authority, _ = setup(tmp_path, observed=snapshot(spot_life_base=D("3"), long_base=D("3"), collateral_quote=D("100")))
    assert authority.reserve(claim("close", product="SWAP", side="SELL", action="CLOSE", amount="2")).allowed
    assert not authority.reserve(claim("second", product="SWAP", side="SELL", action="CLOSE", amount="2")).allowed
    assert authority.reserve(claim("sale", side="SELL", amount="2")).allowed
    assert not authority.reserve(claim("second-sale", side="SELL", amount="2")).allowed


def test_new_session_cannot_ignore_old_capital_claims(tmp_path):
    authority, _ = setup(tmp_path)
    assert authority.reserve(claim(amount="1")).allowed
    assert not authority.reserve(replace(claim("successor", amount="1"), session_id="next", epoch=2)).allowed
    assert authority.claim_ids() == ("spot",)


def test_failed_directory_fsync_keeps_possible_durable_claim_and_fences_writer(tmp_path, monkeypatch):
    import os

    from hummingbot.strategy_v2.life_liquidity import policy_state

    authority, feed = setup(tmp_path)
    fsync = os.fsync
    calls = []

    def fail_directory(fd):
        calls.append(fd)
        if len(calls) == 2:
            raise OSError("synthetic directory fsync failure")
        fsync(fd)

    monkeypatch.setattr(policy_state.os, "fsync", fail_directory)
    assert not authority.reserve(claim(amount="1")).allowed
    assert authority.retained_claim_ids() == ("spot",)
    assert not authority.reserve(claim("second", amount="1")).allowed
    monkeypatch.setattr(policy_state.os, "fsync", fsync)
    restored, _ = setup(tmp_path, create=False)
    assert restored.claim_ids() == ("spot",)


@pytest.mark.parametrize("fault", ["sequence_pair", "claim_units", "claim_identity", "snapshot", "policy"])
def test_corrupt_or_changed_checkpoint_cannot_be_restored(tmp_path, fault):
    import json

    authority, _ = setup(tmp_path)
    assert authority.reserve(claim(amount="1")).allowed
    raw = json.loads(authority.journal.path.read_text())
    if fault == "sequence_pair":
        raw["state"]["snapshot_sequence"] = None
    elif fault == "claim_units":
        raw["state"]["claims"]["spot"]["quantity_base"] = 1.0
    elif fault == "claim_identity":
        raw["state"]["claims"]["spot"]["intent_id"] = "other"
    elif fault == "snapshot":
        raw["state"]["snapshot"]["collateral_quote"] = "NaN"
    else:
        raw["policy"]["leverage"] = 100
    authority.journal.path.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        setup(tmp_path, create=False)


def test_fees_are_reserved_and_rebates_or_sale_proceeds_do_not_finance_orders(tmp_path):
    configured = policy(fee_rate=D("0.1"), price_stress_bps=D("0"))
    authority, _ = setup(tmp_path, configured=configured, observed=snapshot(spot_cash_quote=D("6"), spot_life_base=D("10")))
    assert not authority.reserve(claim(amount="6")).allowed
    assert authority.reserve(claim("sale", side="SELL", amount="6")).allowed
    assert not authority.reserve(claim(amount="6")).allowed


def test_duplicate_pending_rows_and_changed_same_sequence_cannot_authorize(tmp_path):
    order = claim(amount="1")
    authority, feed = setup(tmp_path)
    assert authority.reserve(order).allowed
    feed["snapshot"] = replace(feed["snapshot"], sequence=2, pending=(order, order))
    assert not authority.authorize(order).allowed
    feed["snapshot"] = replace(snapshot(), collateral_quote=D("100"))
    assert not authority.authorize(order).allowed


@pytest.mark.parametrize("updates", [
    {"fee_rate": D("-1")}, {"price_stress_bps": D("10000")}, {"max_age_ms": True},
    {"leverage": True}, {"position_mode": "UNKNOWN"},
    {"tiers": (MarginTier(D("10"), D("0.6"), D("0.2")), MarginTier(D("20"), D("0.5"), D("0.1")))},
])
def test_capital_policy_requires_explicit_consistent_units_and_conservative_tiers(updates):
    with pytest.raises(ValueError):
        policy(**updates)


def _process_reserve(path, order, barrier, results):
    authority = SharedCapitalAuthority(path, policy=policy(), observation=snapshot, clock_ms=lambda: 1000, create=False)
    barrier.wait(timeout=10)
    decision = authority.reserve(order)
    results.put((decision.allowed, decision.reason_code))


def test_process_contenders_cannot_overwrite_the_same_account_pool(tmp_path):
    import multiprocessing

    authority, _ = setup(tmp_path)
    context = multiprocessing.get_context("spawn")
    barrier, results = context.Barrier(2), context.Queue()
    children = [context.Process(target=_process_reserve, args=(authority.journal.path, order, barrier, results))
                for order in (claim(amount="7"), claim("swap", product="SWAP", side="SELL", amount="7"))]
    try:
        for child in children:
            child.start()
        decisions = [results.get(timeout=20) for _ in children]
        assert sum(allowed for allowed, _ in decisions) == 1
        restored, _ = setup(tmp_path, create=False)
        assert len(restored.claim_ids()) == 1
    finally:
        for child in children:
            child.join(timeout=3)
            if child.is_alive():
                child.terminate()
                child.join(timeout=3)
        results.close()
        results.join_thread()


def test_combined_stress_budget_applies_even_with_abundant_collateral(tmp_path):
    authority, _ = setup(tmp_path, configured=policy(max_stress_loss_quote=D("0.05")),
                         observed=snapshot(collateral_quote=D("1000")))
    decision = authority.reserve(claim(amount="1"))
    assert not decision.allowed and decision.reason_code == "CAPITAL_STRESS_LOSS_LIMIT"
    assert decision.stress_loss_quote == D("0.101")


@pytest.mark.parametrize("changes", [{"account_mode": "3"}, {"account_mode": "4"}, {"collateral_currency": "USD"}])
def test_unmodeled_portfolio_margin_or_currency_conversion_is_blocked(changes):
    with pytest.raises(ValueError):
        policy(**changes)
