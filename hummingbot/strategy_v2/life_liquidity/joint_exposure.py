"""Offline joint LIFE spot/linear-perpetual risk over one collateral pool."""

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal


def _nonnegative(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value >= 0


def _positive(value: Decimal) -> bool:
    return _nonnegative(value) and value > 0


@dataclass(frozen=True)
class LinearLifeContractSpec:
    ct_val_base: Decimal
    lot_contracts: Decimal

    def __post_init__(self):
        if not _positive(self.ct_val_base) or not _positive(self.lot_contracts):
            raise ValueError("LINEAR_CONTRACT_SPEC_INVALID")

    def base_from_contracts(self, contracts_signed: Decimal) -> Decimal:
        if not isinstance(contracts_signed, Decimal) or not contracts_signed.is_finite():
            raise ValueError("CONTRACT_QUANTITY_INVALID")
        return contracts_signed * self.ct_val_base

    def contracts_for_base(self, base_signed: Decimal) -> Decimal:
        if not isinstance(base_signed, Decimal) or not base_signed.is_finite():
            raise ValueError("BASE_QUANTITY_INVALID")
        magnitude = (abs(base_signed) / self.ct_val_base / self.lot_contracts).to_integral_value(
            rounding=ROUND_FLOOR) * self.lot_contracts
        return -magnitude if base_signed < 0 else magnitude


@dataclass(frozen=True)
class JointExposureObservation:
    spot_life_base: Decimal
    spot_buy_pending_base: Decimal
    spot_sell_pending_base: Decimal
    perp_contracts_signed: Decimal
    perp_buy_pending_contracts: Decimal
    perp_sell_pending_contracts: Decimal
    mark_price_usdt: Decimal
    index_price_usdt: Decimal
    mark_source: str
    index_source: str
    account_collateral_quote: Decimal
    maintenance_margin_quote: Decimal
    funding_due_quote: Decimal


@dataclass(frozen=True)
class JointRiskLimits:
    max_abs_delta_base: Decimal
    max_gross_base: Decimal
    min_margin_buffer_quote: Decimal
    max_basis_loss_quote: Decimal
    max_funding_quote: Decimal
    adverse_basis_bps: Decimal

    def __post_init__(self):
        if not all(_nonnegative(value) for value in (
                self.max_abs_delta_base, self.max_gross_base,
                self.min_margin_buffer_quote, self.max_basis_loss_quote,
                self.max_funding_quote, self.adverse_basis_bps)):
            raise ValueError("JOINT_RISK_LIMIT_INVALID")


@dataclass(frozen=True)
class JointExposureDecision:
    allowed: bool
    reason_code: str
    net_delta_worst_base: Decimal
    gross_worst_base: Decimal
    basis_loss_worst_quote: Decimal
    margin_buffer_worst_quote: Decimal


def evaluate_joint_exposure(contract: LinearLifeContractSpec,
                            observation: JointExposureObservation,
                            limits: JointRiskLimits) -> JointExposureDecision:
    zero = Decimal("0")

    def reject(reason: str, delta=zero, gross=zero, basis=zero, margin=zero):
        return JointExposureDecision(False, reason, delta, gross, basis, margin)

    if (not isinstance(contract, LinearLifeContractSpec)
            or not isinstance(observation, JointExposureObservation)
            or not isinstance(limits, JointRiskLimits)
            or not all(_nonnegative(value) for value in (
                observation.spot_life_base, observation.spot_buy_pending_base,
                observation.spot_sell_pending_base,
                observation.perp_buy_pending_contracts,
                observation.perp_sell_pending_contracts,
                observation.account_collateral_quote,
                observation.maintenance_margin_quote,
                observation.funding_due_quote))
            or observation.spot_sell_pending_base > observation.spot_life_base
            or not isinstance(observation.perp_contracts_signed, Decimal)
            or not observation.perp_contracts_signed.is_finite()
            or not _positive(observation.mark_price_usdt)
            or not _positive(observation.index_price_usdt)):
        return reject("JOINT_EXPOSURE_INPUT_INVALID")
    if observation.mark_source != "okx_mark":
        return reject("JOINT_MARK_UNQUALIFIED")
    if observation.index_source != "okx_index":
        return reject("JOINT_INDEX_UNQUALIFIED")
    if observation.funding_due_quote > limits.max_funding_quote:
        return reject("JOINT_FUNDING_LIMIT")

    spot_endpoints = (observation.spot_life_base - observation.spot_sell_pending_base,
                      observation.spot_life_base + observation.spot_buy_pending_base)
    perp_endpoints = (
        contract.base_from_contracts(
            observation.perp_contracts_signed - observation.perp_sell_pending_contracts),
        contract.base_from_contracts(
            observation.perp_contracts_signed + observation.perp_buy_pending_contracts))
    scenarios = tuple((spot, perp) for spot in spot_endpoints for perp in perp_endpoints)
    delta = max(abs(spot + perp) for spot, perp in scenarios)
    gross = max(spot + abs(perp) for spot, perp in scenarios)
    basis = (max(abs(perp) for perp in perp_endpoints)
             * observation.mark_price_usdt * limits.adverse_basis_bps / Decimal("10000"))
    margin = (observation.account_collateral_quote - observation.maintenance_margin_quote
              - basis - observation.funding_due_quote)
    if delta > limits.max_abs_delta_base:
        return reject("JOINT_DELTA_LIMIT", delta, gross, basis, margin)
    if gross > limits.max_gross_base:
        return reject("JOINT_GROSS_LIMIT", delta, gross, basis, margin)
    if basis > limits.max_basis_loss_quote:
        return reject("BASIS_STRESS_LIMIT", delta, gross, basis, margin)
    if margin < limits.min_margin_buffer_quote:
        return reject("JOINT_MARGIN_BUFFER_LOW", delta, gross, basis, margin)
    return JointExposureDecision(True, "JOINT_EXPOSURE_READY", delta, gross, basis, margin)
