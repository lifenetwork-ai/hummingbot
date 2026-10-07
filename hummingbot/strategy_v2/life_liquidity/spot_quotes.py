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


def plan_spot_quotes(*, session_id: str, epoch: int,
                     qualified_reference_usdt: Decimal,
                     qualified_exit_value_usdt: Decimal,
                     best_bid_usdt: Decimal, best_ask_usdt: Decimal,
                     quotes: QuotesConfig, rules: InstrumentRules,
                     costs: QuoteCosts, policy: EconomicPolicy,
                     reservations: ReservationLedger,
                     subsidy_remaining_quote: Decimal | None = None,
                     min_depth_base_per_side: Decimal | None = None,
                     sides: tuple[str, ...] = ("BUY", "SELL")) -> SpotQuotePlan:
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
    preview = reservations.preview()
    remaining_subsidy = subsidy_remaining_quote
    candidates = []
    rejections = []
    for level, (spread_bps, size_base) in enumerate(zip(quotes.spreads_bps, quotes.sizes_base)):
        for side in sides:
            direction = Decimal("-1") if side == "BUY" else Decimal("1")
            raw_price = qualified_reference_usdt * (
                Decimal("1") + direction * spread_bps / Decimal("10000"))
            decision = evaluate_quote(EconomicInputs(
                side=side, price_usdt=raw_price, quantity_base=size_base,
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
