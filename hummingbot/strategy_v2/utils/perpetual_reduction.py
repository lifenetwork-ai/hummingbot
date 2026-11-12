"""Pure close-first decisions; these objects do not grant transport authority.

The caller supplies fresh, scoped observations and committed owned cost basis.
No wallet total, pending opening fill, or proposed close is treated as owned
capacity. Persistence, session fencing and terminal proof belong to the native
runtime; never rebuild history in a quote/send check.
"""
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext
from fractions import Fraction
from typing import Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, model_validator

from hummingbot.core.data_type.common import PositionAction, TradeType

ZERO = Decimal('0')


class InventoryReductionConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    mode: Literal['disabled', 'close_first'] = 'disabled'
    price_mode: Optional[Literal['breakeven']] = None

    @model_validator(mode='after')
    def explicit_pricing(self):
        if (self.mode == 'close_first') != (self.price_mode == 'breakeven'):
            raise ValueError('Close-first requires explicit breakeven pricing; disabled policy must omit pricing')
        return self


def _nonnegative(value):
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError('Reduction evidence must contain finite nonnegative Decimal values')
    if len(value.as_tuple().digits) > 256 or abs(value.as_tuple().exponent) > 256 or abs(value.adjusted()) > 100:
        raise ValueError('Reduction evidence exceeds the bounded numeric precision')


def _side(value):
    if value not in (TradeType.BUY, TradeType.SELL):
        raise ValueError('A hedge leg requires an explicit directional side')


@dataclass(frozen=True)
class ClosingFeeBound:
    """Conservative quote-currency fee bound for this exact closing order.

    Rate includes no rebate credit. A fee in an unknown asset cannot be converted
    to this object by assuming zero. Flat fees apply per order, not per hedge leg.
    """
    rate: Decimal
    flat_quote: Decimal

    def __post_init__(self):
        _nonnegative(self.rate)
        _nonnegative(self.flat_quote)
        if self.rate >= 1:
            raise ValueError('Closing fee rate must be below one')


@dataclass(frozen=True)
class HedgeCostBasis:
    """Remaining weighted opening cost, distinct from lifetime fee/PnL totals.

    Apply only financially covered orders in native application order. A partial
    close consumes the same fraction of principal and opening fees. Closing fees
    stay in native realized accounting, never in this remaining opening basis.
    Negative opening fees (rebates) receive no credit in the closing floor.
    """
    leg: TradeType
    amount: Decimal
    principal_quote: Decimal
    opening_fees_quote: Decimal

    def __post_init__(self):
        _side(self.leg)
        for value in (self.amount, self.principal_quote, self.opening_fees_quote):
            _nonnegative(value)
        if (self.amount == 0 and (self.principal_quote or self.opening_fees_quote)
                or self.amount > 0 and self.principal_quote <= 0):
            raise ValueError('Remaining quantity and opening cost are inconsistent')

    def apply(self, effect, amount, quote, fee_quote):
        _nonnegative(amount)
        _nonnegative(quote)
        if (amount <= 0 or quote <= 0 or not isinstance(fee_quote, Decimal) or not fee_quote.is_finite()
                or effect not in (PositionAction.OPEN, PositionAction.CLOSE)):
            raise ValueError('A covered order requires positive size/quote and known fees/effect')
        with localcontext() as context:
            context.prec = 80
            context.rounding = ROUND_CEILING if self.leg == TradeType.BUY else ROUND_FLOOR
            if effect == PositionAction.OPEN:
                principal = self.principal_quote + quote
                context.rounding = ROUND_CEILING
                return HedgeCostBasis(self.leg, self.amount + amount, principal,
                                      self.opening_fees_quote + max(ZERO, fee_quote))
            if amount > self.amount:
                raise ValueError('A reduction cannot reverse its owned hedge leg')
            remaining = self.amount - amount
            if not remaining:
                return HedgeCostBasis(self.leg, ZERO, ZERO, ZERO)
            principal = self.principal_quote * remaining / self.amount
            context.rounding = ROUND_CEILING
            return HedgeCostBasis(self.leg, remaining, principal, self.opening_fees_quote * remaining / self.amount)

    def _quantity(self, amount):
        _nonnegative(amount)
        if not 0 < amount <= self.amount:
            raise ValueError('Closing quantity must be covered by remaining owned quantity')

    def closing_price(self, amount: Decimal, fee: ClosingFeeBound, price_tick: Decimal):
        self._quantity(amount)
        _nonnegative(price_tick)
        if price_tick == 0 or not isinstance(fee, ClosingFeeBound):
            raise ValueError('Closing prices require a positive tick')
        # Exact ratios avoid rounding a repeating division onto the wrong side
        # of an exchange tick. Numeric input sizes above bound this CPU-only work.
        principal, fees, size = map(Fraction, (self.principal_quote, self.opening_fees_quote, self.amount))
        rate, flat, quantity = map(Fraction, (fee.rate, fee.flat_quote, amount))
        if self.leg == TradeType.BUY:
            boundary = ((principal + fees) / size + flat / quantity) / (1 - rate)
        else:
            boundary = ((principal - fees) / size - flat / quantity) / (1 + rate)
        ticks = boundary / Fraction(price_tick)
        integral = (-(-ticks.numerator // ticks.denominator) if self.leg == TradeType.BUY
                    else ticks.numerator // ticks.denominator)
        if integral <= 0:
            raise ValueError('No positive fee-inclusive closing price is available')
        with localcontext() as context:
            context.prec = max(80, len(str(integral)) + len(price_tick.as_tuple().digits) + 2)
            price = Decimal(integral) * price_tick
        if not self.covers(price, amount, fee):
            raise ValueError('Rounded price does not cover the closing cost')
        return price

    def covers(self, price: Decimal, amount: Decimal, fee: ClosingFeeBound):
        self._quantity(amount)
        _nonnegative(price)
        if not isinstance(fee, ClosingFeeBound):
            raise ValueError('Closing fee evidence is unavailable')
        if not price:
            return False
        quantity, price, size, principal, fees, rate, flat = map(Fraction, (
            amount, price, self.amount, self.principal_quote, self.opening_fees_quote, fee.rate, fee.flat_quote))
        gross = quantity * (price * size - principal)
        if self.leg == TradeType.SELL:
            gross = -gross
        costs = quantity * fees + (quantity * price * rate + flat) * size
        return gross >= costs

    def snapshot(self):
        return dict(version=1, leg=self.leg.name, amount=str(self.amount),
                    principal_quote=str(self.principal_quote), opening_fees_quote=str(self.opening_fees_quote))

    @classmethod
    def restore(cls, value):
        fields = {'version', 'leg', 'amount', 'principal_quote', 'opening_fees_quote'}
        if not isinstance(value, dict) or set(value) != fields or type(value['version']) is not int or value['version'] != 1:
            raise ValueError('Unsupported remaining cost basis snapshot')
        try:
            if any(not isinstance(value[key], str) for key in fields - {'version'}):
                raise ValueError('Basis values must retain their exact decimal representation')
            return cls(TradeType[value['leg']], *(Decimal(value[key]) for key in (
                'amount', 'principal_quote', 'opening_fees_quote')))
        except (KeyError, ArithmeticError, TypeError):
            raise ValueError('Invalid remaining cost basis snapshot') from None


@dataclass(frozen=True)
class CloseReservation:
    identity: str
    leg: TradeType
    amount: Decimal
    terminal: bool = False
    financially_applied: bool = False

    def __post_init__(self):
        _side(self.leg)
        _nonnegative(self.amount)
        if (not isinstance(self.identity, str) or not self.identity or self.amount <= 0
                or type(self.terminal) is not bool or type(self.financially_applied) is not bool):
            raise ValueError('Closing reservations require a positive quantity and proven lifecycle flags')

    @property
    def released(self):
        return self.terminal and self.financially_applied


@dataclass(frozen=True)
class ReductionDecision:
    effect: Optional[PositionAction]
    leg: TradeType
    available: Decimal = ZERO
    reason: Optional[str] = None


@dataclass(frozen=True)
class ReductionView:
    long: Optional[HedgeCostBasis]
    short: Optional[HedgeCostBasis]
    venue_long: Decimal
    venue_short: Decimal
    reservations: Tuple[CloseReservation, ...] = ()

    def __post_init__(self):
        for value in (self.venue_long, self.venue_short):
            _nonnegative(value)
        for basis, leg in ((self.long, TradeType.BUY), (self.short, TradeType.SELL)):
            if basis is not None and (not isinstance(basis, HedgeCostBasis) or basis.leg != leg):
                raise ValueError('Owned basis belongs to the wrong hedge leg')
        if not isinstance(self.reservations, tuple) or len(self.reservations) > 100:
            raise ValueError('Reduction reservations exceed the bounded quote scope')
        if any(not isinstance(row, CloseReservation) for row in self.reservations):
            raise ValueError('Unknown reduction reservation')
        if len({row.identity for row in self.reservations}) != len(self.reservations):
            raise ValueError('Duplicate closing reservation identity')

    def decide(self, side, *, reservation_id=None):
        _side(side)
        basis, venue = (self.short, self.venue_short) if side == TradeType.BUY else (self.long, self.venue_long)
        leg = TradeType.SELL if side == TradeType.BUY else TradeType.BUY
        if basis is None:
            return ReductionDecision(None, leg, reason='reduction_basis_unavailable')
        pending = [row for row in self.reservations if row.leg == leg and not row.released]
        if not basis.amount and not venue and not pending:
            return ReductionDecision(PositionAction.OPEN, side)
        with localcontext() as context:
            context.prec = 80
            reserved = sum((row.amount for row in pending if row.identity != reservation_id), ZERO)
            covered = min(basis.amount, venue)
            free = max(ZERO, covered - reserved)
        reason = None if free else ('reduction_reserved' if covered else 'reduction_ownership_unavailable')
        return ReductionDecision(PositionAction.CLOSE, leg, free, reason)


def basis_checkpoint(financial_hash, values):
    """Bounded sidecar committed with the native financial checkpoint."""
    if not isinstance(financial_hash, str) or len(financial_hash) != 64 or any(
            char not in '0123456789abcdef' for char in financial_hash):
        raise ValueError('Remaining costs require a financial checkpoint hash')
    if not isinstance(values, dict) or len(values) > 100:
        raise ValueError('Remaining costs exceed the bounded position scope')
    rows = []
    for key, basis in values.items():
        if (not isinstance(key, tuple) or len(key) != 4 or any(not isinstance(value, str) or not value for value in key)
                or key[1] != 'okx_perpetual' or not isinstance(basis, HedgeCostBasis) or key[3] != basis.leg.name):
            raise ValueError('Remaining cost scope does not match its hedge leg')
        rows.append(dict(controller_id=key[0], connector_name=key[1], trading_pair=key[2], side=key[3],
                         basis=basis.snapshot()))
    rows.sort(key=lambda row: tuple(row[field] for field in ('controller_id', 'connector_name', 'trading_pair', 'side')))
    return dict(version=1, financial_hash=financial_hash, positions=rows)


def restore_basis_checkpoint(value, financial_hash):
    if (not isinstance(value, dict) or set(value) != {'version', 'financial_hash', 'positions'}
            or type(value['version']) is not int or value['version'] != 1 or value['financial_hash'] != financial_hash
            or not isinstance(value['positions'], list) or len(value['positions']) > 100):
        raise ValueError('Remaining costs do not match the current financial checkpoint')
    result = {}
    for row in value['positions']:
        if not isinstance(row, dict) or set(row) != {'controller_id', 'connector_name', 'trading_pair', 'side', 'basis'}:
            raise ValueError('Invalid remaining cost checkpoint row')
        key = tuple(row[field] for field in ('controller_id', 'connector_name', 'trading_pair', 'side'))
        if any(not isinstance(item, str) for item in key) or key in result:
            raise ValueError('Duplicate or invalid remaining cost scope')
        result[key] = HedgeCostBasis.restore(row['basis'])
    # Use the same scope/version validation in both directions.
    basis_checkpoint(financial_hash, result)
    return result
