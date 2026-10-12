"""Fresh account qualification for exact pending financial operations only."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from hummingbot.strategy_v2.life_liquidity.storage.capital import CashflowReceipt, CashflowSpot
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    OwnerToken,
    StateBinding,
    StorageError,
    identifier,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import (
    AckReceipt,
    AckSpot,
    FillReceipt,
    FillSpot,
    FinancialCut,
    PrepareReceipt,
    PrepareSpot,
    decimal,
)
from hummingbot.strategy_v2.life_liquidity.storage.terminal import SpotTerminalRepository


@dataclass(frozen=True)
class SpotFinancialRecoveryPolicy:
    max_fact_age_ms: int

    def __post_init__(self):
        if type(self.max_fact_age_ms) is not int or self.max_fact_age_ms <= 0:
            raise StorageError('SPOT_FINANCIAL_RECOVERY_POLICY_INVALID')


@dataclass(frozen=True)
class SpotFinancialRecoveryFacts:
    binding: StateBinding
    observed_at: datetime
    account_base_life: Decimal  # Actual final balances after the declared complete facts.
    account_quote_usdt: Decimal
    liability_base_life: Decimal
    liability_quote_usdt: Decimal
    account_trade_ids: tuple[str, ...]
    account_cashflow_ids: tuple[str, ...]
    accepted_wire_ids: tuple[str, ...]
    runner_wire_ids: tuple[str, ...]
    order_scope_complete: bool
    bill_scope_complete: bool
    runner_scope_complete: bool

    def __post_init__(self):
        if not isinstance(self.binding, StateBinding):
            raise StorageError('SPOT_FINANCIAL_RECOVERY_FACTS_INVALID')
        utc(self.observed_at)
        for key in ('account_base_life', 'account_quote_usdt', 'liability_base_life', 'liability_quote_usdt'):
            object.__setattr__(self, key, decimal(getattr(self, key)))
        for key in ('account_trade_ids', 'account_cashflow_ids', 'accepted_wire_ids', 'runner_wire_ids'):
            values = getattr(self, key)
            if type(values) is not tuple or len(set(values)) != len(values):
                raise StorageError('SPOT_FINANCIAL_RECOVERY_SCOPE_INVALID')
            for value in values:
                identifier(value)
            object.__setattr__(self, key, tuple(sorted(values)))
        if any(type(getattr(self, key)) is not bool for key in (
                'order_scope_complete', 'bill_scope_complete', 'runner_scope_complete')):
            raise StorageError('SPOT_FINANCIAL_RECOVERY_SCOPE_INVALID')

    def json(self):
        return {key: value.json() if isinstance(value, StateBinding) else
                str(value) if type(value) is Decimal else value.isoformat() if isinstance(value, datetime) else
                list(value) if type(value) is tuple else value for key, value in self.__dict__.items()}


@dataclass(frozen=True)
class SpotAckRecoveryFacts:
    account: SpotFinancialRecoveryFacts
    client_order_id: str
    exchange_order_id: str
    market: str
    side: str
    quantity_base_life: Decimal
    limit_price_usdt: Decimal
    cumulative_base_life: Decimal
    exchange_state: str

    def __post_init__(self):
        if not isinstance(self.account, SpotFinancialRecoveryFacts):
            raise StorageError('SPOT_ACK_RECOVERY_FACTS_INVALID')
        AckSpot('validate', 'validate', 'validate', self.client_order_id, self.market, self.side,
                'validate', 1, self.exchange_order_id, self.account.observed_at)
        for key in ('quantity_base_life', 'limit_price_usdt', 'cumulative_base_life'):
            object.__setattr__(self, key, decimal(getattr(self, key)))
        quantity, cumulative = self.quantity_base_life, self.cumulative_base_life
        if (quantity <= 0 or self.limit_price_usdt <= 0 or not 0 <= cumulative <= quantity
                or self.exchange_state not in ('LIVE', 'PARTIALLY_FILLED', 'FILLED', 'CANCELED')
                or self.exchange_state == 'LIVE' and cumulative != 0
                or self.exchange_state == 'PARTIALLY_FILLED' and not 0 < cumulative < quantity
                or self.exchange_state == 'FILLED' and cumulative != quantity):
            raise StorageError('SPOT_ACK_RECOVERY_FACTS_INVALID')

    def json(self):
        return {key: value.json() if isinstance(value, SpotFinancialRecoveryFacts) else
                str(value) if type(value) is Decimal else value for key, value in self.__dict__.items()}


class SpotFinancialRecoveryRepository(SpotTerminalRepository, Protocol):
    async def resume_prepare(self, command: PrepareSpot, *, facts: SpotFinancialRecoveryFacts,
                             cut: FinancialCut, owner: OwnerToken) -> PrepareReceipt:
        ...

    async def resume_arm(self, operation_id: str, *, facts: SpotFinancialRecoveryFacts,
                         cut: FinancialCut, owner: OwnerToken) -> PrepareReceipt:
        ...

    async def resume_ack(self, command: AckSpot, *, facts: SpotAckRecoveryFacts,
                         cut: FinancialCut, owner: OwnerToken) -> AckReceipt:
        ...

    async def resume_fill(self, command: FillSpot, *, facts: SpotFinancialRecoveryFacts,
                          cut: FinancialCut, owner: OwnerToken) -> FillReceipt:
        ...

    async def resume_cashflow(self, command: CashflowSpot, *, facts: SpotFinancialRecoveryFacts,
                              cut: FinancialCut, owner: OwnerToken) -> CashflowReceipt:
        ...
