"""Pure LIFE spot quote planning; final send must repeat every gate."""

from dataclasses import dataclass
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.config import QuotesConfig
from hummingbot.strategy_v2.life_liquidity.economics import (
    EconomicDecision,
    EconomicInputs,
    EconomicPolicy,
    evaluate_quote,
)
from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetStatus
from hummingbot.strategy_v2.life_liquidity.market_data import InstrumentRules
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, SpotIntent


@dataclass(frozen=True)
class QuoteCosts:
    maker_fee_rate: Decimal
    exit_fee_rate: Decimal
    impact_cost_quote: Decimal
    carry_cost_quote: Decimal
    inventory_risk_quote: Decimal
    uncertainty_bps: Decimal
    exit_value_includes_impact: bool = False


@dataclass(frozen=True)
class AdaptiveQuotePolicy:
    """Explicit offline bounds; no values are suitable as live defaults."""

    max_spread_bps: Decimal
    max_depth_fraction: Decimal
    target_inventory_base: Decimal
    inventory_band_base: Decimal
    max_inventory_widen_bps: Decimal


@dataclass(frozen=True)
class AdaptiveQuoteSignals:
    """Qualified side-specific observations supplied by a future live adapter."""

    volatility_bps: Decimal
    buy_markout_loss_bps: Decimal
    sell_markout_loss_bps: Decimal
    buy_independent_depth_base: Decimal
    sell_independent_depth_base: Decimal


@dataclass(frozen=True)
class SpotQuoteCandidate:
    side: str
    level: int
    price_usdt: Decimal
    quantity_base: Decimal
    economics: EconomicDecision


@dataclass(frozen=True)
class QuoteRejection:
    side: str
    level: int
    reason_code: str


@dataclass(frozen=True)
class SpotQuotePlan:
    candidates: tuple[SpotQuoteCandidate, ...]
    rejections: tuple[QuoteRejection, ...]
    reason_code: str
    bid_depth_base: Decimal
    ask_depth_base: Decimal
    depth_target_met: bool | None
    subsidy_proposed_quote: Decimal


def _positive(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


def _nonnegative(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value >= 0


def _adaptive_inputs_valid(policy: AdaptiveQuotePolicy | None,
                           signals: AdaptiveQuoteSignals | None,
                           costs: QuoteCosts) -> bool:
    if policy is None and signals is None:
        return True
    return (isinstance(policy, AdaptiveQuotePolicy)
            and isinstance(signals, AdaptiveQuoteSignals)
            and _positive(policy.max_spread_bps)
            and _positive(policy.max_depth_fraction)
            and policy.max_depth_fraction <= 1
            and _nonnegative(policy.target_inventory_base)
            and _positive(policy.inventory_band_base)
            and _nonnegative(policy.max_inventory_widen_bps)
            and _nonnegative(signals.volatility_bps)
            and _nonnegative(signals.buy_markout_loss_bps)
            and _nonnegative(signals.sell_markout_loss_bps)
            and _nonnegative(signals.buy_independent_depth_base)
            and _nonnegative(signals.sell_independent_depth_base)
            and isinstance(costs, QuoteCosts)
            and isinstance(costs.maker_fee_rate, Decimal)
            and costs.maker_fee_rate.is_finite()
            and isinstance(costs.exit_fee_rate, Decimal)
            and costs.exit_fee_rate.is_finite())


def plan_spot_quotes(*, session_id: str, epoch: int,
                     qualified_reference_usdt: Decimal,
                     qualified_exit_value_usdt: Decimal,
                     best_bid_usdt: Decimal, best_ask_usdt: Decimal,
                     quotes: QuotesConfig, rules: InstrumentRules,
                     costs: QuoteCosts, policy: EconomicPolicy,
                     reservations: ReservationLedger,
                     subsidy_remaining_quote: Decimal | None = None,
                     min_depth_base_per_side: Decimal | None = None,
                     adaptive_policy: AdaptiveQuotePolicy | None = None,
                     adaptive_signals: AdaptiveQuoteSignals | None = None,
                     max_campaign_filled_base: Decimal | None = None,
                     loss_budget_status: LossBudgetStatus | None = None,
                     sides: tuple[str, ...] = ("BUY", "SELL"),
                     exclude_open_intent: SpotIntent | None = None) -> SpotQuotePlan:
    """Return tentative levels; no WAL, ledger, connector, or exchange state is changed."""
    empty = SpotQuotePlan((), (), "QUOTE_MARKET_INPUT_UNAVAILABLE",
                          Decimal("0"), Decimal("0"), None, Decimal("0"))
    if (not isinstance(session_id, str) or not session_id
            or not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 1
            or not all(_positive(value) for value in (
                qualified_reference_usdt, qualified_exit_value_usdt,
                best_bid_usdt, best_ask_usdt))
            or best_bid_usdt >= best_ask_usdt
            or not isinstance(quotes, QuotesConfig)
            or not isinstance(rules, InstrumentRules)
            or not all(_positive(value) for value in (
                rules.tick_size, rules.lot_size, rules.min_size))
            or not isinstance(costs, QuoteCosts)
            or not isinstance(policy, EconomicPolicy)
            or not isinstance(reservations, ReservationLedger)
            or not isinstance(sides, tuple) or not sides
            or any(not isinstance(side, str) for side in sides)
            or len(set(sides)) != len(sides)
            or any(side not in ("BUY", "SELL") for side in sides)
            or (min_depth_base_per_side is not None
                and not _positive(min_depth_base_per_side))):
        return empty
    if not _adaptive_inputs_valid(adaptive_policy, adaptive_signals, costs):
        return SpotQuotePlan((), (), "QUOTE_ADAPTIVE_INPUT_UNAVAILABLE",
                             Decimal("0"), Decimal("0"),
                             False if min_depth_base_per_side is not None else None,
                             Decimal("0"))
    if (max_campaign_filled_base is not None and not _positive(max_campaign_filled_base)
            or loss_budget_status is not None
            and (not isinstance(loss_budget_status, LossBudgetStatus)
                 or not all(_nonnegative(value) for value in (
                     loss_budget_status.session_loss_quote,
                     loss_budget_status.day_loss_quote,
                     loss_budget_status.campaign_loss_quote))
                 or not isinstance(loss_budget_status.exhausted, bool))):
        return SpotQuotePlan((), (), "QUOTE_CAPACITY_INPUT_UNAVAILABLE",
                             Decimal("0"), Decimal("0"),
                             False if min_depth_base_per_side is not None else None,
                             Decimal("0"))
    if loss_budget_status is not None and loss_budget_status.exhausted:
        return SpotQuotePlan((), (), "EXECUTION_LOSS_BUDGET_EXHAUSTED",
                             Decimal("0"), Decimal("0"),
                             False if min_depth_base_per_side is not None else None,
                             Decimal("0"))
    preview = reservations.preview(exclude_open_intent=exclude_open_intent)
    remaining_subsidy = subsidy_remaining_quote
    remaining_fill_capacity = None
    if max_campaign_filled_base is not None:
        remaining_fill_capacity = (max_campaign_filled_base - preview.filled_base_total
                                   - preview.unresolved_quantity_base("BUY")
                                   - preview.unresolved_quantity_base("SELL"))
    depth_remaining = {}
    if adaptive_policy is not None:
        depth_remaining = {
            "BUY": (adaptive_signals.buy_independent_depth_base * adaptive_policy.max_depth_fraction
                    - preview.unresolved_quantity_base("BUY")),
            "SELL": (adaptive_signals.sell_independent_depth_base * adaptive_policy.max_depth_fraction
                     - preview.unresolved_quantity_base("SELL")),
        }
    candidates = []
    rejections = []
    for level, (spread_bps, size_base) in enumerate(zip(quotes.spreads_bps, quotes.sizes_base)):
        for side in sides:
            proposed_size = size_base
            if remaining_fill_capacity is not None:
                if remaining_fill_capacity <= 0:
                    rejections.append(QuoteRejection(side, level,
                                                     "CAMPAIGN_FILL_CAPACITY_EXHAUSTED"))
                    continue
                proposed_size = min(proposed_size, remaining_fill_capacity)
            adjusted_spread = spread_bps
            if adaptive_policy is not None:
                if side == "BUY":
                    inventory_excess = max(
                        Decimal("0"), preview.projected_inventory_after_buys
                        - adaptive_policy.target_inventory_base)
                    markout_loss_bps = adaptive_signals.buy_markout_loss_bps
                else:
                    inventory_excess = max(
                        Decimal("0"), adaptive_policy.target_inventory_base
                        - preview.projected_inventory_after_sells)
                    markout_loss_bps = adaptive_signals.sell_markout_loss_bps
                inventory_pressure = min(
                    Decimal("1"), inventory_excess / adaptive_policy.inventory_band_base)
                fee_bps = (max(Decimal("0"), costs.maker_fee_rate)
                           + max(Decimal("0"), costs.exit_fee_rate)) * Decimal("10000")
                required_spread = fee_bps + adaptive_signals.volatility_bps + markout_loss_bps
                adjusted_spread = (max(spread_bps, required_spread)
                                   + inventory_pressure * adaptive_policy.max_inventory_widen_bps)
                if adjusted_spread > adaptive_policy.max_spread_bps:
                    rejections.append(QuoteRejection(side, level, "ADAPTIVE_SPREAD_LIMIT"))
                    continue
                proposed_size = min(
                    proposed_size * (Decimal("1") - inventory_pressure / Decimal("2")),
                    depth_remaining[side])
                if proposed_size <= 0:
                    rejections.append(QuoteRejection(side, level, "INDEPENDENT_DEPTH_EXHAUSTED"))
                    continue
            if side == "BUY" and quotes.buy_block_base is not None:
                projected = preview.projected_inventory_after_buys
                if projected >= quotes.buy_block_base:
                    rejections.append(QuoteRejection(side, level, "INVENTORY_BUY_BLOCKED"))
                    continue
                if projected > quotes.buy_taper_start_base:
                    proposed_size *= (quotes.buy_block_base - projected) / (
                        quotes.buy_block_base - quotes.buy_taper_start_base)
                proposed_size = min(proposed_size, quotes.buy_block_base - projected)
            direction = Decimal("-1") if side == "BUY" else Decimal("1")
            raw_price = qualified_reference_usdt * (
                Decimal("1") + direction * adjusted_spread / Decimal("10000"))
            decision = evaluate_quote(EconomicInputs(
                side=side, price_usdt=raw_price, quantity_base=proposed_size,
                value_usdt=qualified_exit_value_usdt,
                tick_size=rules.tick_size, lot_size=rules.lot_size,
                min_size_base=rules.min_size,
                maker_fee_rate=costs.maker_fee_rate,
                exit_fee_rate=costs.exit_fee_rate,
                impact_cost_quote=costs.impact_cost_quote,
                carry_cost_quote=costs.carry_cost_quote,
                inventory_risk_quote=costs.inventory_risk_quote,
                uncertainty_bps=costs.uncertainty_bps,
                exit_value_includes_impact=costs.exit_value_includes_impact),
                policy, subsidy_remaining_quote=remaining_subsidy)
            if decision.final_price_usdt <= 0 or decision.final_quantity_base <= 0:
                reason = decision.reason_code
            elif (adaptive_policy is not None
                  and direction * (decision.final_price_usdt / qualified_reference_usdt
                                   - Decimal("1")) * Decimal("10000")
                  > adaptive_policy.max_spread_bps):
                reason = "ADAPTIVE_SPREAD_LIMIT"
            elif ((side == "BUY" and decision.final_price_usdt >= best_ask_usdt)
                  or (side == "SELL" and decision.final_price_usdt <= best_bid_usdt)):
                reason = "QUOTE_WOULD_CROSS_BOOK"
            elif not decision.allowed:
                reason = decision.reason_code
            else:
                intent = SpotIntent(
                    f"preview:{session_id}:{epoch}:{side}:{level}", side,
                    decision.final_quantity_base, decision.final_price_usdt,
                    session_id, epoch)
                risk = preview.check_and_hold(intent, reference_price=qualified_reference_usdt)
                reason = None if risk.allowed else risk.reason_code
            if reason is not None:
                rejections.append(QuoteRejection(side, level, reason))
                continue
            candidates.append(SpotQuoteCandidate(
                side, level, decision.final_price_usdt,
                decision.final_quantity_base, decision))
            if adaptive_policy is not None:
                depth_remaining[side] -= decision.final_quantity_base
            if remaining_fill_capacity is not None:
                remaining_fill_capacity -= decision.final_quantity_base
            if remaining_subsidy is not None:
                remaining_subsidy -= decision.subsidy_reserved_quote
    bid_depth = sum((item.quantity_base for item in candidates if item.side == "BUY"), Decimal("0"))
    ask_depth = sum((item.quantity_base for item in candidates if item.side == "SELL"), Decimal("0"))
    depth_met = (None if min_depth_base_per_side is None else
                 bid_depth >= min_depth_base_per_side and ask_depth >= min_depth_base_per_side)
    return SpotQuotePlan(
        tuple(candidates), tuple(rejections),
        "QUOTE_PLAN_READY" if candidates else "NO_PERMITTED_QUOTES",
        bid_depth, ask_depth, depth_met,
        sum((item.economics.subsidy_reserved_quote for item in candidates), Decimal("0")))
