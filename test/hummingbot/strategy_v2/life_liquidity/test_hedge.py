"""LIFE hedge intents are sized from actual exposure, cost, and retry state."""

from dataclasses import replace
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.hedge import HedgeObservation, HedgePolicy, plan_life_hedge
from hummingbot.strategy_v2.life_liquidity.joint_exposure import LinearLifeContractSpec

D = Decimal


def _policy(**changes):
    policy = HedgePolicy(
        life_swap_instrument="LIFE-USDT-SWAP", deadband_base=D("0.5"),
        batch_base=D("0.1"), max_child_base=D("5"),
        max_unhedged_base=D("3"), max_unhedged_ms=1000,
        max_retry_attempts=2, max_hedge_cost_quote=D("0.1"),
        max_basis_bps=D("100"), maker_fee_rate=D("0.001"))
    return replace(policy, **changes)


def _observation(**changes):
    observed = HedgeObservation(
        spot_life_base=D("10"), target_inventory_base=D("0"),
        perp_contracts_signed=D("-16"), pending_hedge_contracts_signed=D("0"),
        unresolved_spot_buy_base=D("0"), unresolved_spot_sell_base=D("0"),
        swap_instrument="LIFE-USDT-SWAP", connector_ready=True,
        mark_price_usdt=D("1"), index_price_usdt=D("1"),
        executable_price_usdt=D("1"), mark_source="okx_mark",
        index_source="okx_index",
        executable_depth_base=D("5"), estimated_impact_quote=D("0.01"),
        unhedged_since_ms=100, now_ms=200, retry_attempts=0)
    return replace(observed, **changes)


def test_matching_life_hedge_uses_fixed_contract_value_and_bounded_size():
    decision = plan_life_hedge(_policy(), _observation(),
                               LinearLifeContractSpec(D("0.5"), D("0.1")))
    assert decision.reason_code == "HEDGE_INTENT_READY"
    assert decision.side == "SELL"
    assert decision.contracts == D("4")
    assert decision.base_quantity == D("2")
    assert not decision.allow_spot_risk_increase


def test_pending_hedge_or_small_residual_does_not_duplicate_orders():
    contract = LinearLifeContractSpec(D("0.5"), D("0.1"))
    pending = plan_life_hedge(_policy(), _observation(
        pending_hedge_contracts_signed=D("-4")), contract)
    assert pending.reason_code == "HEDGE_IN_FLIGHT" and pending.contracts == 0
    small = plan_life_hedge(_policy(), _observation(
        perp_contracts_signed=D("-19.4")), contract)
    assert small.reason_code == "HEDGE_DEADBAND" and small.contracts == 0


def test_disconnect_timeout_retry_and_cost_block_new_spot_risk():
    contract = LinearLifeContractSpec(D("0.5"), D("0.1"))
    assert plan_life_hedge(_policy(), _observation(
        connector_ready=False), contract).reason_code == "HEDGE_CONNECTOR_UNAVAILABLE"
    assert plan_life_hedge(_policy(), _observation(
        retry_attempts=2), contract).reason_code == "HEDGE_RETRY_CAP"
    assert plan_life_hedge(_policy(), _observation(
        executable_depth_base=D("0.01")), contract).reason_code == "HEDGE_DEPTH_INSUFFICIENT"
    assert plan_life_hedge(_policy(), _observation(
        estimated_impact_quote=D("0.2")), contract).reason_code == "HEDGE_COST_LIMIT"
    urgent = plan_life_hedge(_policy(), _observation(now_ms=1200), contract)
    assert urgent.reason_code == "HEDGE_URGENT" and urgent.contracts == D("4")


def test_unresolved_spot_intents_and_cross_asset_symbol_fail_closed():
    contract = LinearLifeContractSpec(D("0.5"), D("0.1"))
    assert plan_life_hedge(_policy(), _observation(
        unresolved_spot_buy_base=D("1")), contract).reason_code == "SPOT_INTENT_UNRESOLVED"
    assert plan_life_hedge(_policy(), _observation(
        swap_instrument="ETH-USDT-SWAP"), contract).reason_code == "HEDGE_INSTRUMENT_MISMATCH"
