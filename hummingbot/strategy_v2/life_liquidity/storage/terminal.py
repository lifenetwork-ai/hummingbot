"""Terminal storage qualification facts, not exchange authentication or rearm grants."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from hummingbot.strategy_v2.life_liquidity.storage.capital import SpotCapitalRepository
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    AtomicScope,
    OwnerToken,
    StateBinding,
    StorageError,
    identifier,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import AckSpot, FinancialCut, SpotFillObservation, decimal


@dataclass(frozen=True)
class SpotTerminalPolicy:
    max_fact_age_ms: int

    def __post_init__(self):
        if type(self.max_fact_age_ms) is not int or self.max_fact_age_ms <= 0:
            raise StorageError('SPOT_TERMINAL_POLICY_INVALID')


@dataclass(frozen=True)
class SpotTerminalFacts:
    binding: StateBinding
    observed_at: datetime  # Local receipt of the complete qualified observation cut.
    exchange_state: str
    cumulative_base_life: Decimal
    quantity_base_life: Decimal
    limit_price_usdt: Decimal
    fills: tuple[SpotFillObservation, ...]
    account_base_life: Decimal
    account_quote_usdt: Decimal
    liability_base_life: Decimal
    liability_quote_usdt: Decimal
    account_trade_ids: tuple[str, ...]
    account_cashflow_ids: tuple[str, ...]
    pending_wire_ids: tuple[str, ...]
    pending_exchange_order_ids: tuple[str, ...]
    runner_wire_ids: tuple[str, ...]
    order_scope_complete: bool
    bill_scope_complete: bool
    runner_scope_complete: bool

    def __post_init__(self):
        if not isinstance(self.binding, StateBinding) or self.exchange_state not in ('CANCELED', 'FILLED'):
            raise StorageError('SPOT_TERMINAL_FACTS_INVALID')
        utc(self.observed_at)
        for key in ('cumulative_base_life', 'quantity_base_life', 'limit_price_usdt', 'account_base_life',
                    'account_quote_usdt', 'liability_base_life', 'liability_quote_usdt'):
            object.__setattr__(self, key, decimal(getattr(self, key), positive=key in ('quantity_base_life', 'limit_price_usdt')))
        if (type(self.fills) is not tuple or any(not isinstance(row, SpotFillObservation) for row in self.fills)
                or len({row.trade_id for row in self.fills}) != len(self.fills)
                or sum((row.quantity_base_life for row in self.fills), Decimal('0')) != self.cumulative_base_life
                or self.cumulative_base_life > self.quantity_base_life
                or self.exchange_state == 'FILLED' and self.cumulative_base_life != self.quantity_base_life):
            raise StorageError('SPOT_TERMINAL_FILL_SNAPSHOT_INVALID')
        object.__setattr__(self, 'fills', tuple(sorted(self.fills, key=lambda row: (row.fill_at_ms, row.trade_id))))
        for key in ('account_trade_ids', 'account_cashflow_ids', 'pending_wire_ids',
                    'pending_exchange_order_ids', 'runner_wire_ids'):
            values = getattr(self, key)
            if type(values) is not tuple or len(set(values)) != len(values):
                raise StorageError('SPOT_TERMINAL_SCOPE_INVALID')
            for value in values:
                identifier(value)
            object.__setattr__(self, key, tuple(sorted(values)))
        if any(type(getattr(self, key)) is not bool for key in (
                'order_scope_complete', 'bill_scope_complete', 'runner_scope_complete')):
            raise StorageError('SPOT_TERMINAL_SCOPE_INVALID')

    def json(self):
        return {key: (value.json() if isinstance(value, StateBinding) else
                      [row.json() for row in value] if key == 'fills' else list(value) if type(value) is tuple else
                      str(value) if type(value) is Decimal else value.isoformat() if isinstance(value, datetime) else value)
                for key, value in self.__dict__.items()}


@dataclass(frozen=True)
class TerminalSpot:
    operation_id: str
    prepare_operation_id: str
    intent_id: str
    client_order_id: str
    market: str
    side: str
    session_id: str
    epoch: int
    exchange_order_id: str
    facts: SpotTerminalFacts

    def __post_init__(self):
        if not isinstance(self.facts, SpotTerminalFacts):
            raise StorageError('SPOT_TERMINAL_FACTS_INVALID')
        AckSpot(self.operation_id, self.prepare_operation_id, self.intent_id, self.client_order_id,
                self.market, self.side, self.session_id, self.epoch, self.exchange_order_id, self.facts.observed_at)

    def json(self):
        return {key: value.json() if key == 'facts' else value for key, value in self.__dict__.items()}


@dataclass(frozen=True)
class TerminalReceipt:
    binding: StateBinding
    operation_id: str
    intent_id: str
    exchange_order_id: str
    state: str
    subsidy_settled: bool
    replayed: bool
    atomic_scope: AtomicScope


class SpotTerminalRepository(SpotCapitalRepository, Protocol):
    async def confirm_terminal(self, command: TerminalSpot, *, cut: FinancialCut, owner: OwnerToken) -> TerminalReceipt:
        ...

    async def resume_terminal(self, command: TerminalSpot, *, cut: FinancialCut, owner: OwnerToken) -> TerminalReceipt:
        """Requalify and complete one already durable terminal saga; never rearm."""
        ...

    async def lookup_terminal(self, operation_id: str, binding: StateBinding) -> TerminalReceipt | None:
        ...
