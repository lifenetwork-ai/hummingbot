"""Qualified spot inventory settlement ports; no caller-supplied release amount."""
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping, Protocol

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    AtomicScope,
    OwnerToken,
    StateBinding,
    StorageError,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut, decimal
from hummingbot.strategy_v2.life_liquidity.storage.recovery import (
    SpotFinancialRecoveryFacts,
    SpotFinancialRecoveryRepository,
)


@dataclass(frozen=True)
class SpotSettlementPolicy:
    max_fact_age_ms: int

    def __post_init__(self):
        if type(self.max_fact_age_ms) is not int or self.max_fact_age_ms <= 0:
            raise StorageError('SPOT_SETTLEMENT_POLICY_INVALID')


@dataclass(frozen=True)
class SettleSpotInventory:
    operation_id: str
    cycle_id: str
    intent_ids: tuple[str, ...]

    def __post_init__(self):
        identifier(self.operation_id)
        identifier(self.cycle_id)
        if (len(self.operation_id) > 96 or type(self.intent_ids) is not tuple or not self.intent_ids
                or len(set(self.intent_ids)) != len(self.intent_ids)):
            raise StorageError('SPOT_SETTLEMENT_MEMBERS_INVALID')
        for key in self.intent_ids:
            identifier(key)
        object.__setattr__(self, 'intent_ids', tuple(sorted(self.intent_ids)))

    def json(self):
        return {'operation_id': self.operation_id, 'cycle_id': self.cycle_id, 'intent_ids': list(self.intent_ids)}


@dataclass(frozen=True)
class SpotSettlementReceipt:
    binding: StateBinding
    operation_id: str
    cycle_id: str
    actuals_usdt: Mapping[str, Decimal]
    proof_digest: str
    state: str
    replayed: bool
    atomic_scope: AtomicScope

    def __post_init__(self):
        object.__setattr__(self, 'actuals_usdt', MappingProxyType({
            key: decimal(value) for key, value in self.actuals_usdt.items()}))


class SpotSettlementRepository(SpotFinancialRecoveryRepository, Protocol):
    async def settle_inventory(self, command: SettleSpotInventory, *, facts: SpotFinancialRecoveryFacts,
                               cut: FinancialCut, owner: OwnerToken) -> SpotSettlementReceipt:
        ...

    async def resume_inventory_settlement(self, command: SettleSpotInventory, *, facts: SpotFinancialRecoveryFacts,
                                          cut: FinancialCut, owner: OwnerToken) -> SpotSettlementReceipt:
        ...

    async def lookup_inventory_settlement(self, operation_id: str, binding: StateBinding) -> SpotSettlementReceipt | None:
        ...
