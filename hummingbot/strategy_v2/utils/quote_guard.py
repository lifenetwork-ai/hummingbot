"""Strict quote qualification over bounded existing book/order observations.

This filters known own orders; it cannot establish independent fair value or an
atomic snapshot across public/private streams. Readiness may revoke permission;
only distinct public observations sampled by the control loop can restore it.
"""
import math
from dataclasses import dataclass
from decimal import Decimal
from itertools import chain, islice
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.in_flight_order import OrderState


class SidewaysRange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    lower_price: Decimal = Field(gt=0, allow_inf_nan=False)
    upper_price: Decimal = Field(gt=0, allow_inf_nan=False)
    resume_lower_price: Decimal = Field(gt=0, allow_inf_nan=False)
    resume_upper_price: Decimal = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered(self):
        if not self.lower_price < self.resume_lower_price <= self.resume_upper_price < self.upper_price:
            raise ValueError("Resume range must be inside the strictly ordered sideways range")
        return self


class QuoteGuardConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = False
    mode: Literal["strict"] = "strict"
    max_deviation_bps: Optional[Decimal] = Field(default=None, gt=0, lt=10000, allow_inf_nan=False)
    resume_deviation_bps: Optional[Decimal] = Field(default=None, ge=0, lt=10000, allow_inf_nan=False)
    recovery_delay_seconds: Decimal = Field(default=Decimal("5"), ge=0, le=3600, allow_inf_nan=False)
    max_book_levels: int = Field(default=50, ge=1, le=200, strict=True)
    max_own_orders: int = Field(default=200, ge=1, le=1000, strict=True)

    @model_validator(mode="after")
    def explicit_thresholds(self):
        if self.enabled and (self.max_deviation_bps is None or self.resume_deviation_bps is None
                             or self.resume_deviation_bps >= self.max_deviation_bps):
            raise ValueError("Enabled guards require explicit deviation and tighter resume thresholds")
        return self


@dataclass(frozen=True)
class MarketObservation:
    market_mid: Optional[Decimal]
    book_at: Optional[float]
    observed_at: float
    token: tuple = ()
    detail: Optional[str] = None

    @property
    def quality(self):
        return "known_order_filtered" if self.market_mid is not None else "unavailable"


def _decimal(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Non-finite market observation")
    return result


def _orders(connector, pair, maximum):
    tracker = getattr(connector, "_order_tracker", None)
    sources = (connector.in_flight_orders, getattr(tracker, "cached_orders", {}), getattr(tracker, "lost_orders", {}))
    values, seen = [], set()
    for order in chain.from_iterable(source.values() for source in sources):
        if order.client_order_id in seen:
            continue
        seen.add(order.client_order_id)
        if len(seen) > maximum:
            raise ValueError("own_order_limit")
        if order.trading_pair != pair:
            continue
        values.append((order.client_order_id, order.exchange_order_id, order.trade_type, order.current_state,
                       _decimal(order.price), _decimal(order.amount), _decimal(order.executed_amount_base),
                       float(_decimal(order.last_update_timestamp))))
    return tuple(values)


def observe_market(connector, book, pair, *, now, max_age_seconds, max_levels, max_orders):
    """Read at most max_levels per side and max_orders known identities.

    Stable acknowledged, unfilled orders contribute their remaining base quantity.
    Pending, partially filled and recently terminal orders contribute their full
    original amount as an upper bound, never their smaller private remainder.
    Insufficient ambiguous depth is masked, rather than treated as independent.
    The connector's existing 30s terminal cache supplies that bounded history.
    """
    book_at = None
    try:
        ids = (book.snapshot_uid, book.last_diff_uid)
        book_at = max(ids) / (1000 if connector.name == "okx_perpetual" else 1)
        if book_at <= 0 or not -1 <= now - book_at <= float(max_age_seconds):
            raise ValueError("stale_book")
        orders = _orders(connector, pair, max_orders)
        own, ambiguous = ({}, {}), (set(), set())
        for _, exchange_id, side, state, price, amount, filled, updated in orders:
            if price <= 0 or amount <= 0 or updated <= 0 or side not in (TradeType.BUY, TradeType.SELL) or not 0 <= filled <= amount:
                raise ValueError("invalid_own_order")
            index = 0 if side == TradeType.BUY else 1
            # Books use doubles upstream. A non-representable own price is
            # ambiguous at that projected price, never independent liquidity.
            projected = _decimal(float(price))
            if (state != OrderState.OPEN or not exchange_id or filled > 0 or updated >= book_at or projected != price):
                ambiguous[index].add(projected)
            own[index][projected] = own[index].get(projected, Decimal("0")) + amount
        rows = []
        best = []
        rules = getattr(connector, "trading_rules", {})
        rule = rules.get(pair)
        minimum = max(_decimal(getattr(rule, "min_order_size", 0)),
                      _decimal(getattr(rule, "min_base_amount_increment", 0)))
        for index, entries in enumerate((book.bid_entries(), book.ask_entries())):
            levels = tuple((_decimal(row.price), _decimal(row.amount)) for row in islice(entries, max_levels))
            rows.append(levels)
            candidate, seen = None, set()
            previous = None
            for price, quantity in levels:
                if price <= 0 or quantity <= 0 or (previous is not None and (price >= previous if index == 0 else price <= previous)):
                    raise ValueError("invalid_book")
                previous = price
                if candidate is not None:
                    continue
                seen.add(price)
                remaining = quantity - own[index].get(price, Decimal("0"))
                if remaining < 0:
                    if price not in ambiguous[index]:
                        raise ValueError("inconsistent_own_depth")
                    continue
                # Discard residual that could be the upstream double's rounding
                # error, including very large quantities with tiny tick sizes.
                noise = Decimal(str(math.ulp(float(quantity)))) * 2
                if remaining > noise and remaining >= minimum:
                    candidate = price
            if candidate is None:
                raise ValueError("depth_limit" if len(levels) == max_levels else "empty_or_filtered_depth")
            if any(price not in seen and price not in ambiguous[index]
                   and (price >= candidate if index == 0 else price <= candidate) for price in own[index]):
                raise ValueError("inconsistent_own_depth")
            best.append(candidate)
        if rows[0][0][0] >= rows[1][0][0] or best[0] >= best[1]:
            raise ValueError("crossed_book")
        if ids != (book.snapshot_uid, book.last_diff_uid) or orders != _orders(connector, pair, max_orders):
            raise ValueError("inconsistent_observation")
        return MarketObservation((best[0] + best[1]) / 2, book_at, now, (ids, tuple(rows)),
                                 "conservative_own_quantity_bound" if any(ambiguous) else None)
    except (ValueError, ArithmeticError, TypeError, AttributeError, OverflowError) as error:
        known = {"stale_book", "own_order_limit", "invalid_own_order", "invalid_book", "inconsistent_own_depth",
                 "empty_or_filtered_depth", "depth_limit", "crossed_book", "inconsistent_observation"}
        detail = str(error) if str(error) in known else "invalid_observation"
        return MarketObservation(None, book_at, now, detail=detail)


class QuoteGuard:
    def __init__(self, config, sideways_range=None):
        self.config, self.sideways_range = config, sideways_range
        self.blocked = True
        self.good_since = self.good_since_epoch = self.last_token = None
        self.valid_samples = 0

    def invalidate(self):
        self.blocked = True
        self.good_since = self.good_since_epoch = self.last_token = None
        self.valid_samples = 0

    def evaluate(self, observation, reference, monotonic_now, *, sample=False):
        reason, deviation = None, None
        inner = True
        if observation.market_mid is None:
            reason = "market_price_unavailable"
        else:
            reference = _decimal(reference)
            if reference <= 0:
                raise ValueError("Invalid reference price")
            deviation = abs(reference / observation.market_mid - 1) * 10000
            if deviation > self.config.max_deviation_bps:
                reason = "reference_deviation"
            inner = deviation <= self.config.resume_deviation_bps
            limits = self.sideways_range
            if limits is not None:
                if not limits.lower_price <= observation.market_mid <= limits.upper_price:
                    reason = "sideways_breakout"
                inner = inner and limits.resume_lower_price <= observation.market_mid <= limits.resume_upper_price
        if reason is not None or (self.blocked and not inner):
            self.invalidate()
        elif self.blocked and sample and observation.token != self.last_token:
            if self.good_since is None or monotonic_now < self.good_since:
                self.good_since, self.good_since_epoch = monotonic_now, observation.observed_at
                self.valid_samples = 0
            self.last_token = observation.token
            self.valid_samples = min(2, self.valid_samples + 1)
            if self.valid_samples >= 2 and monotonic_now - self.good_since >= float(self.config.recovery_delay_seconds):
                self.blocked = False
        return dict(enabled=True, mode="strict", ready=not self.blocked,
                    reason_code=reason or ("guard_recovering" if self.blocked else None),
                    data_quality=observation.quality, detail=observation.detail,
                    market_mid=observation.market_mid, deviation_bps=deviation,
                    book_observed_at=observation.book_at, evaluated_at=observation.observed_at,
                    recovery_started_at=self.good_since_epoch if self.blocked else None,
                    recovery_valid_samples=self.valid_samples)
