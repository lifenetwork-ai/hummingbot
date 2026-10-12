"""Approved spot transfers and recorded NAV observations; no exchange grants."""
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    AtomicScope,
    OwnerToken,
    StateBinding,
    StorageError,
    identifier,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import (
    FinancialCut,
    SpotFillRepository,
    decimal,
    signed_decimal,
)


def bill_id(value):
    if type(value) is not str or re.fullmatch(r'[1-9][0-9]{0,63}', value) is None:
        raise StorageError('SPOT_CASHFLOW_BILL_INVALID')


def milliseconds(value):
    if type(value) is not int or not 0 < value <= 253402300799999:
        raise StorageError('SPOT_CAPITAL_TIME_INVALID')


@dataclass(frozen=True)
class ApprovedSpotCashflow:
    bill_id: str
    currency: str
    signed_amount: Decimal
    transfer_at_ms: int

    def __post_init__(self):
        bill_id(self.bill_id)
        milliseconds(self.transfer_at_ms)
        object.__setattr__(self, 'signed_amount', signed_decimal(self.signed_amount))
        if self.currency not in ('LIFE', 'USDT') or self.signed_amount == 0:
            raise StorageError('SPOT_CASHFLOW_APPROVAL_INVALID')

    def json(self):
        return {key: str(value) if type(value) is Decimal else value for key, value in self.__dict__.items()}


@dataclass(frozen=True)
class SpotAccountingPolicy:
    opening_base_life: Decimal
    opening_quote_usdt: Decimal
    opening_independent_price_usdt: Decimal
    opened_at: datetime
    anchor_bill_id: str
    approved_cashflows: tuple[ApprovedSpotCashflow, ...]

    def __post_init__(self):
        for key in ('opening_base_life', 'opening_quote_usdt', 'opening_independent_price_usdt'):
            object.__setattr__(self, key, decimal(getattr(self, key), positive=key == 'opening_independent_price_usdt'))
        if self.opening_base_life == 0 and self.opening_quote_usdt == 0:
            raise StorageError('SPOT_OPENING_CAPITAL_INVALID')
        utc(self.opened_at)
        bill_id(self.anchor_bill_id)
        if (type(self.approved_cashflows) is not tuple
                or any(not isinstance(row, ApprovedSpotCashflow) for row in self.approved_cashflows)
                or len({row.bill_id for row in self.approved_cashflows}) != len(self.approved_cashflows)
                or any(int(row.bill_id) <= int(self.anchor_bill_id)
                       or row.transfer_at_ms < int(self.opened_at.timestamp() * 1000) for row in self.approved_cashflows)):
            raise StorageError('SPOT_CASHFLOW_APPROVAL_INVALID')
        object.__setattr__(self, 'approved_cashflows', tuple(sorted(self.approved_cashflows, key=lambda row: int(row.bill_id))))

    def json(self):
        return {key: ([row.json() for row in value] if key == 'approved_cashflows' else
                      str(value) if type(value) is Decimal else value.isoformat() if isinstance(value, datetime) else value)
                for key, value in self.__dict__.items()}


@dataclass(frozen=True)
class CashflowSpot:
    operation_id: str
    bill_id: str
    currency: str
    signed_amount: Decimal
    transfer_at_ms: int
    independent_price_usdt: Decimal
    value_at_ms: int
    reference_observed_at_ms: int
    source_kind: str
    observed_at: datetime

    def __post_init__(self):
        approval = ApprovedSpotCashflow(self.bill_id, self.currency, self.signed_amount, self.transfer_at_ms)
        object.__setattr__(self, 'signed_amount', approval.signed_amount)
        identifier(self.operation_id)
        if len(self.operation_id) > 96:
            raise StorageError('SPOT_CAPITAL_OPERATION_INVALID')
        object.__setattr__(self, 'independent_price_usdt', decimal(self.independent_price_usdt, positive=True))
        for value in (self.value_at_ms, self.reference_observed_at_ms):
            milliseconds(value)
        utc(self.observed_at)
        if (self.reference_observed_at_ms < self.value_at_ms
                or self.currency == 'USDT' and (self.independent_price_usdt != 1 or self.source_kind != 'quote_unit')
                or self.currency == 'LIFE' and self.source_kind != 'independent_market'):
            raise StorageError('SPOT_CASHFLOW_VALUE_INVALID')

    def json(self):
        return {key: str(value) if type(value) is Decimal else value.isoformat() if isinstance(value, datetime) else value
                for key, value in self.__dict__.items()}


@dataclass(frozen=True)
class ObserveSpotNAV:
    operation_id: str
    independent_price_usdt: Decimal
    value_at_ms: int
    reference_observed_at_ms: int
    source_kind: str
    observed_at: datetime

    def __post_init__(self):
        identifier(self.operation_id)
        if len(self.operation_id) > 96:
            raise StorageError('SPOT_CAPITAL_OPERATION_INVALID')
        object.__setattr__(self, 'independent_price_usdt', decimal(self.independent_price_usdt, positive=True))
        milliseconds(self.value_at_ms)
        milliseconds(self.reference_observed_at_ms)
        utc(self.observed_at)
        if self.source_kind != 'independent_market' or self.reference_observed_at_ms < self.value_at_ms:
            raise StorageError('SPOT_NAV_VALUE_INVALID')

    def json(self):
        return {key: str(value) if type(value) is Decimal else value.isoformat() if isinstance(value, datetime) else value
                for key, value in self.__dict__.items()}


@dataclass(frozen=True)
class SpotCapitalView:
    binding: StateBinding
    cut: FinancialCut
    nav_usdt: Decimal
    adjusted_nav_usdt: Decimal
    highwater_usdt: Decimal
    drawdown_bps: Decimal
    execution_loss_usdt: Decimal
    starting_inventory_pnl_usdt: Decimal
    net_cashflows_usdt: Decimal
    independent_price_usdt: Decimal
    value_at_ms: int
    observed_at: datetime
    current: bool  # Financial fact set matches; this does not assert live price freshness.

    def __post_init__(self):
        if not isinstance(self.cut, FinancialCut) or self.cut.checkpoint.binding != self.binding or type(self.current) is not bool:
            raise StorageError('SPOT_CAPITAL_VIEW_INVALID')
        for key in ('nav_usdt', 'highwater_usdt', 'drawdown_bps', 'execution_loss_usdt', 'independent_price_usdt'):
            decimal(getattr(self, key), positive=key in ('highwater_usdt', 'independent_price_usdt'))
        for key in ('adjusted_nav_usdt', 'starting_inventory_pnl_usdt', 'net_cashflows_usdt'):
            signed_decimal(getattr(self, key))
        milliseconds(self.value_at_ms)
        utc(self.observed_at)


@dataclass(frozen=True)
class CashflowReceipt:
    binding: StateBinding
    operation_id: str
    bill_id: str
    state: str
    replayed: bool
    atomic_scope: AtomicScope


@dataclass(frozen=True)
class NavReceipt:
    binding: StateBinding
    operation_id: str
    capital: SpotCapitalView
    replayed: bool
    atomic_scope: AtomicScope


class SpotCapitalRepository(SpotFillRepository, Protocol):
    async def apply_cashflow(self, command: CashflowSpot, *, cut: FinancialCut, owner: OwnerToken) -> CashflowReceipt:
        ...

    async def lookup_cashflow(self, operation_id: str, binding: StateBinding) -> CashflowReceipt | None:
        ...

    async def observe_nav(self, command: ObserveSpotNAV, *, cut: FinancialCut, owner: OwnerToken) -> NavReceipt:
        ...

    async def capital(self, binding: StateBinding) -> SpotCapitalView | None:
        ...
