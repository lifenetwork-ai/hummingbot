"""Captured-action financial admission. Caller facts do not authenticate feeds."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from hummingbot.strategy_v2.life_liquidity.storage.actions import SpotQuoteAction
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken, StateBinding, StorageError, utc
from hummingbot.strategy_v2.life_liquidity.storage.financial import (
    FinancialCut,
    PrepareReceipt,
    PrepareSpot,
    decimal,
    signed_decimal,
)


@dataclass(frozen=True)
class QuoteAdmissionPolicy:
    binding: StateBinding
    objective: str
    min_net_edge_bps: Decimal
    uncertainty_bps: Decimal
    tick_size: Decimal
    lot_size: Decimal
    min_size: Decimal
    max_fact_age_ms: int
    max_reference_age_ms: int
    max_fee_age_ms: int
    max_capture_age_ms: int

    def __post_init__(self):
        if type(self.binding) is not StateBinding or self.objective not in ('profit_mm', 'liquidity_service'):
            raise StorageError('QUOTE_ADMISSION_POLICY_INVALID')
        for key in ('min_net_edge_bps', 'uncertainty_bps', 'tick_size', 'lot_size', 'min_size'):
            object.__setattr__(self, key, decimal(getattr(self, key), positive=key in ('tick_size', 'lot_size', 'min_size')))
        for key in ('max_fact_age_ms', 'max_reference_age_ms', 'max_fee_age_ms', 'max_capture_age_ms'):
            if type(getattr(self, key)) is not int or not 0 < getattr(self, key) <= 2147483647:
                raise StorageError('QUOTE_ADMISSION_POLICY_INVALID')


@dataclass(frozen=True)
class QuotePolicySnapshot:
    policy: QuoteAdmissionPolicy
    cut: FinancialCut
    observed_at: datetime

    def __post_init__(self):
        if (type(self.policy) is not QuoteAdmissionPolicy or type(self.cut) is not FinancialCut
                or self.cut.checkpoint.binding != self.policy.binding):
            raise StorageError('QUOTE_POLICY_SNAPSHOT_INVALID')
        utc(self.observed_at)


@dataclass(frozen=True)
class QuoteAdmissionFacts:
    binding: StateBinding
    observed_at: datetime
    reference_at: datetime
    exit_value_at: datetime
    fee_at: datetime
    reference_price_usdt: Decimal
    exit_value_usdt: Decimal
    best_bid_usdt: Decimal
    best_ask_usdt: Decimal
    maker_fee_rate: Decimal
    exit_fee_rate: Decimal
    impact_cost_usdt: Decimal
    carry_cost_usdt: Decimal
    inventory_risk_usdt: Decimal
    exit_value_includes_impact: bool
    exit_source_kind: str

    def __post_init__(self):
        if (type(self.binding) is not StateBinding or type(self.exit_value_includes_impact) is not bool
                or self.exit_source_kind != 'independent_market'):
            raise StorageError('QUOTE_ADMISSION_FACTS_INVALID')
        for key in ('observed_at', 'reference_at', 'exit_value_at', 'fee_at'):
            utc(getattr(self, key))
        if any(getattr(self, key) > self.observed_at for key in ('reference_at', 'exit_value_at', 'fee_at')):
            raise StorageError('QUOTE_ADMISSION_TIME_INVALID')
        for key in ('reference_price_usdt', 'exit_value_usdt', 'best_bid_usdt', 'best_ask_usdt',
                    'impact_cost_usdt', 'carry_cost_usdt', 'inventory_risk_usdt'):
            object.__setattr__(self, key, decimal(getattr(self, key), positive=key in (
                'reference_price_usdt', 'exit_value_usdt', 'best_bid_usdt', 'best_ask_usdt')))
        for key in ('maker_fee_rate', 'exit_fee_rate'):
            object.__setattr__(self, key, signed_decimal(getattr(self, key)))
        if self.best_bid_usdt >= self.best_ask_usdt:
            raise StorageError('QUOTE_ADMISSION_BOOK_INVALID')

    def json(self):
        return {key: (value.json() if key == 'binding' else value.isoformat() if isinstance(value, datetime)
                      else str(value) if type(value) is Decimal else value) for key, value in self.__dict__.items()}

    @classmethod
    def from_json(cls, value):
        data = dict(value)
        data['binding'] = StateBinding.from_json(data['binding'])
        for key in ('observed_at', 'reference_at', 'exit_value_at', 'fee_at'):
            data[key] = datetime.fromisoformat(data[key])
        for key in ('reference_price_usdt', 'exit_value_usdt', 'best_bid_usdt', 'best_ask_usdt',
                    'maker_fee_rate', 'exit_fee_rate', 'impact_cost_usdt', 'carry_cost_usdt', 'inventory_risk_usdt'):
            data[key] = Decimal(data[key])
        return cls(**data)


@dataclass(frozen=True)
class PrepareQuoteAction:
    action: SpotQuoteAction
    prepare: PrepareSpot
    admission: QuoteAdmissionFacts

    def __post_init__(self):
        if (type(self.action) is not SpotQuoteAction or type(self.prepare) is not PrepareSpot
                or type(self.admission) is not QuoteAdmissionFacts
                or self.prepare.budget_scope != 'QUOTE'
                or any(getattr(self.action, key) != getattr(self.prepare, key) for key in (
                    'intent_id', 'session_id', 'epoch', 'config_version', 'market', 'side'))
                or self.action.level != self.prepare.quote_level
                or self.admission.reference_price_usdt != self.prepare.reference_price_usdt):
            raise StorageError('QUOTE_PREPARATION_SCOPE_INVALID')
        # A shared account must retain its explicit risk epoch. PrepareSpot
        # validates its units; the native account/claim and COMMIT gates verify
        # the original current epoch. This DTO does not grant risk permission.

    def json(self):
        return {'action': self.action.json(), 'prepare': self.prepare.json(), 'admission': self.admission.json()}

    @classmethod
    def from_json(cls, value):
        return cls(SpotQuoteAction(**value['action']), PrepareSpot.from_json(value['prepare']),
                   QuoteAdmissionFacts.from_json(value['admission']))


class ActionPreparationRepository(Protocol):
    async def prepare_quote_action(self, command: PrepareQuoteAction, *, cut: FinancialCut, owner: OwnerToken) -> PrepareReceipt:
        ...
