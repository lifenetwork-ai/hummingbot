"""O.7.4 synthetic qualified streams; no exchange connection or live defaults."""

from dataclasses import replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital_runner import setup_shared

import pytest

from hummingbot.core.data_type.common import PositionMode
from hummingbot.strategy_v2.life_liquidity.carry_monitor import (
    CarryAccount,
    CarryBundle,
    CarryFunding,
    CarryMonitor,
    CarryPolicy,
    CarryPrice,
    CarryTerms,
    FundingPayment,
)

D = Decimal


def setup_carry(tmp_path, *, values=None, collateral="1000", route=None, spot_route=None,
                mode=PositionMode.ONEWAY, **changes):
    values = values or setup_shared(tmp_path, collateral=collateral, route=route, spot_route=spot_route, mode=mode)
    c, cap, sender, spot, swap, wal, clock, account, state, strategy, sc, pc = values
    now = int(clock.wall.timestamp() * 1000)
    policy = CarryPolicy("12345", "LIFE-USDT-SWAP", now - 10000, 1000, 500,
                         4 * 3600000, 16 * 3600000, D("0.002"), D("10"), D("100"))
    policy = replace(policy, **changes)
    feeds = {"bundle": CarryBundle(
        CarryAccount(state["snapshot"], (), True, True),
        CarryPrice("LIFE-USDT-SWAP", 1, now, D("1"), "okx_mark"),
        CarryPrice("LIFE-USDT-SWAP", 1, now, D("1"), "okx_index"),
        CarryFunding("12345", "LIFE-USDT-SWAP", 1, now, D("0.001"), now + 1000, now + 1000 + 4 * 3600000,
                     policy.anchor_ms, now, (), True, "okx_funding"),
        CarryTerms("12345", "LIFE-USDT-SWAP", 1, now, D("0.001"), D("0.002"),
                   cap.policy.tiers, True, "okx_account_terms"))}
    monitor = CarryMonitor(tmp_path / "carry.json", policy=policy, capital=cap,
                           observation=lambda: feeds["bundle"], clock_ms=lambda: int(clock.wall.timestamp() * 1000), create=True)
    c.install_carry_monitor(monitor)
    return monitor, values, feeds


def test_schedule_shortening_increases_reserve_without_eight_hour_assumption(tmp_path):
    m, v, feed = setup_carry(tmp_path)
    before = m.check()
    assert before.allowed and before.funding_events == 2
    b = feed["bundle"]
    feed["bundle"] = replace(b, funding=replace(b.funding, sequence=2, next_settlement_ms=b.funding.settlement_ms + 3600000))
    after = m.check()
    assert after.allowed and after.funding_events == 5
    assert after.funding_reserve_quote > before.funding_reserve_quote


def test_funding_sign_diagnostics_do_not_finance_orders_with_expected_income(tmp_path):
    m, v, feed = setup_carry(tmp_path)
    positive = m.check()
    b = feed["bundle"]
    feed["bundle"] = replace(b, funding=replace(b.funding, sequence=2, rate=D("-0.001")))
    negative = m.check()
    assert positive.expected_payment_quote < 0 < negative.expected_payment_quote
    assert positive.funding_reserve_quote == negative.funding_reserve_quote > 0


def refresh(v, feed, *, payments=(), **snapshot_changes):
    clock, state = v[6], v[8]
    now = int(clock.wall.timestamp() * 1000)
    old = feed["bundle"]
    snap = replace(state["snapshot"], sequence=state["snapshot"].sequence + 1,
                   observed_at_ms=now, **snapshot_changes)
    state["snapshot"] = snap
    v[7]["value"] = replace(v[7]["value"], observed_at_ms=now, snapshot_sequence=snap.sequence)
    feed["bundle"] = replace(
        old, account=CarryAccount(snap, tuple(p.bill_id for p in payments), True, True),
        mark=replace(old.mark, sequence=old.mark.sequence + 1, observed_at_ms=now, price_usdt=snap.mark_price_usdt),
        index=replace(old.index, sequence=old.index.sequence + 1, observed_at_ms=now, price_usdt=snap.index_price_usdt),
        funding=replace(old.funding, sequence=old.funding.sequence + 1, observed_at_ms=now,
                        settlement_ms=now + 1000, next_settlement_ms=now + 1000 + 4 * 3600000,
                        coverage_through_ms=now, payments=payments),
        terms=replace(old.terms, sequence=old.terms.sequence + 1, observed_at_ms=now))


def restore(m):
    return CarryMonitor(m.journal.path, policy=m.policy, capital=m.capital,
                        observation=m.observation, clock_ms=m.clock_ms, create=False)


@pytest.mark.parametrize("stream", ["mark", "index", "funding", "terms"])
@pytest.mark.parametrize("fault", ["stale", "future", "source", "instrument", "sequence", "mutation"])
def test_independent_streams_cannot_be_substituted_or_replayed(tmp_path, stream, fault):
    m, v, feed = setup_carry(tmp_path)
    assert m.check().allowed
    b = feed["bundle"]
    item = getattr(b, stream)
    updates = {"stale": {"observed_at_ms": item.observed_at_ms - 1001},
               "future": {"observed_at_ms": item.observed_at_ms + 1},
               "source": {"source": "benchmark"}, "instrument": {"instrument": "BTC-USDT-SWAP"},
               "sequence": {"sequence": 0}, "mutation": {"observed_at_ms": item.observed_at_ms - 1}}
    feed["bundle"] = replace(b, **{stream: replace(item, **updates[fault])})
    assert not m.check().allowed
    assert not restore(m).check().allowed


@pytest.mark.parametrize("stream", ["funding", "terms"])
@pytest.mark.parametrize("fault", ["uid", "scope", "nan", "negative_fee"])
def test_account_cost_streams_require_complete_finite_evidence(tmp_path, stream, fault):
    m, _, feed = setup_carry(tmp_path)
    b = feed["bundle"]
    item = getattr(b, stream)
    updates = {"uid": {"account_uid": "999"}, "scope": {"scope_complete": False},
               "nan": {"rate" if stream == "funding" else "maker_fee_rate": D("NaN")},
               "negative_fee": {"rate": D("2")} if stream == "funding" else {"taker_fee_rate": D("-0.01")}}
    feed["bundle"] = replace(b, **{stream: replace(item, **updates[fault])})
    assert m.check().reason_code == "CARRY_FUNDING_OR_TERMS_UNQUALIFIED"


@pytest.mark.parametrize("interval", [1, 2, 4, 8])
def test_observed_cadence_sets_conservative_horizon_cost(tmp_path, interval):
    m, _, feed = setup_carry(tmp_path)
    b = feed["bundle"]
    feed["bundle"] = replace(b, funding=replace(b.funding, next_settlement_ms=b.funding.settlement_ms + interval * 3600000))
    result = m.check()
    assert result.allowed
    assert result.funding_events == 1 + 4 // interval
    assert result.funding_reserve_quote == D("0.0022") * result.funding_events


def test_shorter_schedule_can_exhaust_budget_and_cannot_roll_back_to_cheap_evidence(tmp_path):
    m, _, feed = setup_carry(tmp_path, funding_budget_quote=D("0.006"))
    assert m.check().allowed
    old = feed["bundle"]
    feed["bundle"] = replace(old, funding=replace(old.funding, sequence=2,
                                                  next_settlement_ms=old.funding.settlement_ms + 3600000))
    assert m.check().reason_code == "CARRY_FUNDING_BUDGET"
    feed["bundle"] = old
    assert m.check().reason_code == "CARRY_STREAM_REPLAY_OR_MUTATION"


def test_cash_settlements_are_idempotent_and_do_not_double_deduct_collateral(tmp_path):
    m, v, feed = setup_carry(tmp_path)
    assert m.check().allowed
    v[6].advance(1)
    now = int(v[6].wall.timestamp() * 1000)
    payments = (FundingPayment("debit", now, D("-2")), FundingPayment("credit", now, D("3")))
    refresh(v, feed, payments=payments, collateral_quote=D("998"))
    result = m.check()
    assert result.allowed and result.settled_payment_quote == 1 and result.funding_debits_quote == 2
    assert result == m.check() == restore(m).check()
    # The qualified account already includes all settled bills; only future liability is held again.
    from copy import copy
    from dataclasses import replace as changed
    virtual = copy(v[1])
    virtual.policy = changed(virtual.policy, fee_rate=result.fee_rate)
    expected = virtual._evaluate(changed(v[8]["snapshot"], funding_liability_quote=result.funding_reserve_quote), {})
    assert result.capital == expected


@pytest.mark.parametrize("fault", ["missing", "changed", "duplicate", "account", "currency_contract", "gap"])
def test_settled_funding_history_cannot_disappear_or_mutate(tmp_path, fault):
    m, v, feed = setup_carry(tmp_path)
    assert m.check().allowed
    v[6].advance(1)
    now = int(v[6].wall.timestamp() * 1000)
    payment = FundingPayment("bill", now, D("-2"))
    refresh(v, feed, payments=(payment,))
    assert m.check().allowed
    b = feed["bundle"]
    if fault == "account":
        feed["bundle"] = replace(b, account=replace(b.account, included_funding_bill_ids=()))
    elif fault == "currency_contract":
        feed["bundle"] = replace(b, account=replace(b.account, settled_funding_included=False))
    else:
        payments = {"missing": (), "changed": (replace(payment, signed_payment_quote=D("-1")),),
                    "duplicate": (payment, payment), "gap": (payment,)}[fault]
        feed["bundle"] = replace(b, funding=replace(b.funding, sequence=3, payments=payments,
                                                    coverage_through_ms=now - 1 if fault == "gap" else now))
    assert not m.check().allowed


def test_receipts_never_refund_the_gross_funding_spending_budget(tmp_path):
    m, v, feed = setup_carry(tmp_path, funding_budget_quote=D("2"))
    now = int(v[6].wall.timestamp() * 1000)
    refresh(v, feed, payments=(FundingPayment("debit", now, D("-2")), FundingPayment("credit", now, D("100"))))
    result = m.check()
    assert result.settled_payment_quote == 98 and result.funding_debits_quote == 2
    assert result.reason_code == "CARRY_FUNDING_BUDGET"


def test_rollover_requires_coverage_of_the_previously_announced_settlement(tmp_path):
    m, v, feed = setup_carry(tmp_path)
    assert m.check().allowed
    due = feed["bundle"].funding.settlement_ms
    v[6].advance(1)
    refresh(v, feed)
    b = feed["bundle"]
    feed["bundle"] = replace(b, funding=replace(b.funding, coverage_through_ms=due - 1))
    assert m.check().reason_code == "CARRY_SETTLEMENT_COVERAGE_GAP"
    feed["bundle"] = b
    assert m.check().allowed  # Explicit complete coverage with no bill is permitted.


def test_pending_open_exposure_counts_but_pending_closes_never_refund_funding(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalClaim
    m, v, _ = setup_carry(tmp_path, funding_budget_quote=D("0.02"))
    baseline = m.check()
    claim = CapitalClaim("open", "wire", "12345", "session", 1, 1, 1, "SWAP", "SELL", "OPEN", D("6"), D("1"), 1)
    denied = m.check(claim)
    assert denied.reason_code == "CARRY_FUNDING_BUDGET"
    close = replace(claim, side="SELL", position_action="CLOSE", quantity_base=D("1"))
    assert m.check(close).funding_reserve_quote == baseline.funding_reserve_quote
    assert v[1].claim_ids() == ()  # Monitoring never grants or releases a capital claim.


def test_distinct_price_skew_basis_and_dynamic_whole_notional_tiers(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.shared_capital import MarginTier
    m, v, feed = setup_carry(tmp_path)
    refresh(v, feed, mark_price_usdt=D("1.02"))
    assert m.check().reason_code == "CARRY_BASIS_LIMIT"
    refresh(v, feed, mark_price_usdt=D("1"))
    b = feed["bundle"]
    feed["bundle"] = replace(b, mark=replace(b.mark, observed_at_ms=b.mark.observed_at_ms - 501))
    assert m.check().reason_code == "CARRY_STREAM_SKEW"
    feed["bundle"] = replace(b, terms=replace(b.terms, tiers=(MarginTier(D("0.5"), D("1"), D("0.9")),)))
    assert not m.check().allowed


@pytest.mark.parametrize("fault", ["clock", "missing", "corrupt", "writer", "checkpoint", "provider"])
def test_durable_state_and_provider_failures_revoke_permission(tmp_path, monkeypatch, fault):
    m, v, feed = setup_carry(tmp_path)
    assert m.check().allowed
    if fault == "clock":
        v[6].advance(-1)
    elif fault == "missing":
        m.journal.path.unlink()
    elif fault == "corrupt":
        m.journal.path.write_text("{")
    elif fault == "writer":
        other = restore(m)
        b = feed["bundle"]
        feed["bundle"] = replace(b, terms=replace(b.terms, sequence=2, taker_fee_rate=D("0.003")))
        assert other.check().allowed
    elif fault == "checkpoint":
        b = feed["bundle"]
        feed["bundle"] = replace(b, funding=replace(b.funding, sequence=2, rate=D("0.002")))
        monkeypatch.setattr(m.journal, "_write", lambda _: (_ for _ in ()).throw(OSError("disk")))
    else:
        m.observation = lambda: (_ for _ in ()).throw(RuntimeError("unavailable"))
    assert not m.check().allowed
    assert v[1].claim_ids() == ()


@pytest.mark.parametrize("field,value", [
    ("account_uid", "x"), ("instrument", "BTC-USDT-SWAP"), ("anchor_ms", -1), ("max_age_ms", True),
    ("max_skew_ms", 0), ("horizon_ms", 0), ("max_schedule_gap_ms", -1),
    ("absolute_rate_stress", D("2")), ("funding_budget_quote", D("NaN")), ("max_basis_bps", D("-1")),
])
def test_policy_has_explicit_finite_units_and_no_implicit_live_defaults(tmp_path, field, value):
    with pytest.raises(ValueError, match="CARRY_POLICY_INVALID"):
        setup_carry(tmp_path, **{field: value})


@pytest.mark.parametrize("fault", ["empty", "sequence", "payments", "anchor", "policy"])
def test_corrupt_cold_restore_cannot_grant_authority(tmp_path, fault):
    import json
    m, _, _ = setup_carry(tmp_path)
    assert m.check().allowed
    raw = json.loads(m.journal.path.read_text())
    if fault == "empty":
        raw["state"]["streams"] = {"mark": {}}
    elif fault == "sequence":
        raw["state"]["streams"]["mark"]["sequence"] = 0
    elif fault == "payments":
        raw["state"]["payments"] = {"x": {"bill_id": "x", "settled_at_ms": 0, "signed_payment_quote": "NaN"}}
    elif fault == "anchor":
        raw["state"]["last_ms"] = -1
    else:
        raw["policy"]["policy"]["funding_budget_quote"] = "10000"
    m.journal.path.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        restore(m)


def test_zero_net_delta_does_not_hide_gross_funding_basis_or_observed_margin(tmp_path):
    m, v, feed = setup_carry(tmp_path)
    refresh(v, feed, long_base=D("0"), short_base=D("10"))
    result = m.check()
    assert result.allowed and result.capital.net_delta_worst_base == 0 and result.funding_reserve_quote > 0
    refresh(v, feed, mark_price_usdt=D("1.02"))
    assert m.check().reason_code == "CARRY_BASIS_LIMIT"
    refresh(v, feed, mark_price_usdt=D("1"), initial_margin_quote=D("1000"), maintenance_margin_quote=D("999"))
    assert m.check().reason_code == "CAPITAL_INITIAL_MARGIN_LOW"


def test_current_claims_are_deduplicated_and_changed_identities_are_refused(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalClaim
    m, v, feed = setup_carry(tmp_path)
    claim = CapitalClaim("open", "wire", "12345", "session", 1, 1, 1, "SWAP", "SELL", "OPEN", D("1"), D("1"), 1)
    assert v[1].reserve(claim).allowed
    a = m.check(claim)
    refresh(v, feed, pending=(claim,))
    assert m.check(claim).funding_reserve_quote == a.funding_reserve_quote
    assert m.check(replace(claim, quantity_base=D("2"))).reason_code == "CARRY_CLAIM_CHANGED"
    refresh(v, feed, pending=(replace(claim, quantity_base=D("2")),))
    assert m.check().reason_code == "CARRY_PENDING_CONFLICT"


@pytest.mark.parametrize("field,value", [
    ("settlement_ms", 0), ("next_settlement_ms", 0), ("coverage_from_ms", 0),
    ("coverage_through_ms", 0), ("next_settlement_ms", 10**16), ("payments", []),
])
def test_invalid_or_uncovered_schedule_never_assumes_a_default_interval(tmp_path, field, value):
    m, _, feed = setup_carry(tmp_path)
    b = feed["bundle"]
    feed["bundle"] = replace(b, funding=replace(b.funding, **{field: value}))
    assert not m.check().allowed


def test_unpaid_liability_and_future_forecast_are_both_held_without_settled_double_charge(tmp_path):
    m, v, feed = setup_carry(tmp_path, funding_budget_quote=D("0.006"))
    assert m.check().allowed  # Forecast 0.0044 fits alone.
    refresh(v, feed, funding_liability_quote=D("0.003"))
    result = m.check()
    assert result.reason_code == "CARRY_FUNDING_BUDGET"  # Sum 0.0074; max() would incorrectly permit it.
    assert result.capital.stress_loss_quote >= D("0.0074")
    b = feed["bundle"]
    feed["bundle"] = replace(b, account=replace(b.account, unsettled_funding_only=False))
    assert not m.check().allowed  # Unknown liability semantics cannot silently become a live default.


def test_failed_provider_observation_still_fences_later_clock_rollback(tmp_path):
    m, v, feed = setup_carry(tmp_path)
    assert m.check().allowed
    original = m.observation
    v[6].advance(2)
    m.observation = lambda: (_ for _ in ()).throw(OSError("feed gap"))
    assert not m.check().allowed
    v[6].advance(-0.5)
    refresh(v, feed)
    m.observation = original
    assert m.check().reason_code == "CARRY_CLOCK_ROLLBACK"
