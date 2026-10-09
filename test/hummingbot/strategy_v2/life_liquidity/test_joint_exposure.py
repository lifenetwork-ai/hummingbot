"""Synthetic LIFE spot and linear perpetual risk share one account capital pool."""

from dataclasses import replace
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.joint_exposure import (
    JointExposureObservation,
    JointRiskLimits,
    LinearLifeContractSpec,
    evaluate_joint_exposure,
)

D = Decimal


def _observation(**changes):
    observed = JointExposureObservation(
        spot_life_base=D("10"), spot_buy_pending_base=D("0"),
        spot_sell_pending_base=D("0"), perp_contracts_signed=D("-20"),
        perp_buy_pending_contracts=D("0"), perp_sell_pending_contracts=D("0"),
        mark_price_usdt=D("1"), index_price_usdt=D("1"),
        mark_source="okx_mark", index_source="okx_index",
        account_collateral_quote=D("30"), maintenance_margin_quote=D("5"),
        funding_due_quote=D("0.1"))
    return replace(observed, **changes)


def _limits(**changes):
    limits = JointRiskLimits(
        max_abs_delta_base=D("5"), max_gross_base=D("25"),
        min_margin_buffer_quote=D("10"), max_basis_loss_quote=D("2"),
        max_funding_quote=D("1"), adverse_basis_bps=D("100"))
    return replace(limits, **changes)


def test_contract_base_is_fixed_across_mark_and_entry_price_changes():
    contract = LinearLifeContractSpec(ct_val_base=D("0.5"), lot_contracts=D("0.1"))
    assert contract.base_from_contracts(D("-20")) == D("-10")
    assert contract.contracts_for_base(D("1.27")) == D("2.5")
    assert evaluate_joint_exposure(contract, _observation(
        mark_price_usdt=D("2"), index_price_usdt=D("1.9")),
        _limits()).net_delta_worst_base == D("0")


def test_near_zero_delta_still_fails_basis_or_account_margin():
    contract = LinearLifeContractSpec(D("0.5"), D("0.1"))
    basis = evaluate_joint_exposure(contract, _observation(),
                                    _limits(max_basis_loss_quote=D("0.05")))
    assert not basis.allowed and basis.reason_code == "BASIS_STRESS_LIMIT"
    margin = evaluate_joint_exposure(contract, _observation(
        account_collateral_quote=D("15")), _limits())
    assert not margin.allowed and margin.reason_code == "JOINT_MARGIN_BUFFER_LOW"


def test_all_one_sided_pending_fills_count_toward_delta_and_gross():
    contract = LinearLifeContractSpec(D("0.5"), D("0.1"))
    pending = evaluate_joint_exposure(contract, _observation(
        spot_buy_pending_base=D("10"), perp_sell_pending_contracts=D("8")),
        _limits())
    assert not pending.allowed and pending.reason_code == "JOINT_DELTA_LIMIT"
    assert pending.net_delta_worst_base == D("10")
    gross = evaluate_joint_exposure(contract, _observation(
        spot_buy_pending_base=D("6")), _limits(max_abs_delta_base=D("10")))
    assert not gross.allowed and gross.reason_code == "JOINT_GROSS_LIMIT"


def test_mark_index_identity_and_funding_are_independent_gates():
    contract = LinearLifeContractSpec(D("0.5"), D("0.1"))
    assert evaluate_joint_exposure(contract, _observation(
        mark_source="benchmark_model"), _limits()).reason_code == "JOINT_MARK_UNQUALIFIED"
    assert evaluate_joint_exposure(contract, _observation(
        index_source="benchmark_model"), _limits()).reason_code == "JOINT_INDEX_UNQUALIFIED"
    assert evaluate_joint_exposure(contract, _observation(
        funding_due_quote=D("2")), _limits()).reason_code == "JOINT_FUNDING_LIMIT"
