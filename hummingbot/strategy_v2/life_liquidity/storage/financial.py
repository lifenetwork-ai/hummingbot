"""Typed spot storage ports. Preparation/arming receipts are never send permits."""
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Protocol

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    AtomicScope,
    OwnerToken,
    StateBinding,
    StateCut,
    StorageError,
    identifier,
    utc,
)


def decimal(value, *, positive=False):
    if (type(value) is not Decimal or not value.is_finite()
            or (value <= 0 if positive else value < 0)):
        raise StorageError('SPOT_DECIMAL_INVALID')
    # Canonicalize representation without normalize()/quantize() context rounding.
    if value == 0:
        return Decimal('0')
    parts = value.as_tuple()
    if len(parts.digits) > 64 or not -64 <= parts.exponent <= 64:
        raise StorageError('SPOT_DECIMAL_ENCODING_RANGE')
    text = format(value, 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return Decimal('0' if value == 0 else text)


def signed_decimal(value):
    if type(value) is not Decimal or not value.is_finite():
        raise StorageError('SPOT_DECIMAL_INVALID')
    magnitude = decimal(value.copy_abs())
    return magnitude.copy_negate() if value < 0 else magnitude


@dataclass(frozen=True)
class SpotFillPolicy:
    max_reference_skew_ms: int

    def __post_init__(self):
        if type(self.max_reference_skew_ms) is not int or self.max_reference_skew_ms < 0:
            raise StorageError('SPOT_FILL_POLICY_INVALID')


@dataclass(frozen=True)
class SpotFillObservation:
    trade_id: str
    quantity_base_life: Decimal
    price_usdt: Decimal
    fee_currency: str
    signed_fee: Decimal  # Negative debit, positive actual rebate, in fee_currency.
    fill_at_ms: int
    independent_price_usdt: Decimal
    independent_value_at_ms: int
    independent_observed_at_ms: int
    source_kind: str

    def __post_init__(self):
        identifier(self.trade_id)
        for key in ('quantity_base_life', 'price_usdt', 'independent_price_usdt'):
            object.__setattr__(self, key, decimal(getattr(self, key), positive=True))
        object.__setattr__(self, 'signed_fee', signed_decimal(self.signed_fee))
        if self.fee_currency not in ('LIFE', 'USDT') or self.source_kind != 'independent_market':
            raise StorageError('SPOT_FILL_FEE_OR_VALUE_INVALID')
        for key in ('fill_at_ms', 'independent_value_at_ms', 'independent_observed_at_ms'):
            value = getattr(self, key)
            if type(value) is not int or not 0 < value <= 253402300799999:
                raise StorageError('SPOT_FILL_TIME_INVALID')
        if self.independent_observed_at_ms < self.independent_value_at_ms:
            raise StorageError('SPOT_FILL_VALUE_TIME_INVALID')

    def json(self):
        return {key: str(value) if type(value) is Decimal else value for key, value in self.__dict__.items()}

    @classmethod
    def from_json(cls, data):
        values = dict(data)
        for key in ('quantity_base_life', 'price_usdt', 'signed_fee', 'independent_price_usdt'):
            values[key] = Decimal(values[key])
        return cls(**values)

    def loss(self, side):
        fee_cost = -self.signed_fee * (self.independent_price_usdt if self.fee_currency == 'LIFE' else Decimal('1'))
        sign = Decimal('1') if side == 'BUY' else Decimal('-1')
        return max(Decimal('0'), fee_cost - sign * (self.independent_price_usdt - self.price_usdt) * self.quantity_base_life)


@dataclass(frozen=True)
class RecordedSpotFill:
    prepare_operation_id: str
    intent_id: str
    exchange_order_id: str
    fill: SpotFillObservation


@dataclass(frozen=True)
class FillSpot:
    operation_id: str
    prepare_operation_id: str
    intent_id: str
    client_order_id: str
    market: str
    side: str
    session_id: str
    epoch: int
    exchange_order_id: str
    cumulative_base_life: Decimal
    fills: tuple[SpotFillObservation, ...]  # Complete cumulative snapshot, including known fills/fees.
    observed_at: datetime

    def __post_init__(self):
        # Same exact source identity and local receipt-time encoding as ACK.
        AckSpot(self.operation_id, self.prepare_operation_id, self.intent_id, self.client_order_id,
                self.market, self.side, self.session_id, self.epoch, self.exchange_order_id, self.observed_at)
        object.__setattr__(self, 'cumulative_base_life', decimal(self.cumulative_base_life, positive=True))
        if (type(self.fills) is not tuple or not self.fills or any(not isinstance(row, SpotFillObservation) for row in self.fills)
                or len({row.trade_id for row in self.fills}) != len(self.fills)
                or sum((row.quantity_base_life for row in self.fills), Decimal('0')) != self.cumulative_base_life):
            raise StorageError('SPOT_FILL_SNAPSHOT_INVALID')
        object.__setattr__(self, 'fills', tuple(sorted(self.fills, key=lambda row: (row.fill_at_ms, row.trade_id))))

    def json(self):
        return {key: ([row.json() for row in value] if key == 'fills' else
                      str(value) if type(value) is Decimal else value.isoformat() if isinstance(value, datetime) else value)
                for key, value in self.__dict__.items()}


@dataclass(frozen=True)
class FillReceipt:
    binding: StateBinding
    operation_id: str
    intent_id: str
    cumulative_base_life: Decimal
    execution_loss_usdt: Decimal
    state: str
    replayed: bool
    atomic_scope: AtomicScope


@dataclass(frozen=True)
class BudgetPolicy:
    campaign_id: str
    campaign_limit_quote: Decimal
    day_limit_quote: Decimal
    session_limit_quote: Decimal

    def __post_init__(self):
        identifier(self.campaign_id)
        for field in ('campaign_limit_quote', 'day_limit_quote', 'session_limit_quote'):
            object.__setattr__(self, field, decimal(getattr(self, field), positive=True))
        if not self.session_limit_quote <= self.day_limit_quote <= self.campaign_limit_quote:
            raise StorageError('SPOT_BUDGET_POLICY_INVALID')

    def kwargs(self):
        return {field: getattr(self, field) for field in self.__dataclass_fields__}

    def json(self):
        return {key: str(value) for key, value in self.kwargs().items()}


@dataclass(frozen=True)
class PrepareSpot:
    operation_id: str
    intent_id: str
    client_order_id: str
    market: str
    side: str
    quantity_base: Decimal  # LIFE
    limit_price_usdt: Decimal  # USDT / LIFE
    reference_price_usdt: Decimal  # USDT / LIFE
    subsidy_quote_usdt: Decimal  # USDT, conservative claim determined by caller's economics
    session_id: str
    epoch: int
    config_version: int
    quote_level: int
    fee_buffer_quote_usdt: Decimal
    fee_buffer_base_life: Decimal

    def __post_init__(self):
        for value in (self.operation_id, self.intent_id, self.session_id):
            identifier(value)
        if (len(self.operation_id) > 96 or type(self.client_order_id) is not str
                or re.fullmatch(r'[A-Za-z0-9]{1,32}', self.client_order_id) is None):
            raise StorageError('SPOT_WIRE_ID_INVALID')
        if self.market != 'LIFE-USDT' or self.side not in ('BUY', 'SELL'):
            raise StorageError('SPOT_MARKET_OR_SIDE_INVALID')
        for field in ('quantity_base', 'limit_price_usdt', 'reference_price_usdt', 'subsidy_quote_usdt',
                      'fee_buffer_quote_usdt', 'fee_buffer_base_life'):
            object.__setattr__(self, field, decimal(getattr(self, field), positive=field in (
                'quantity_base', 'limit_price_usdt', 'reference_price_usdt')))
        if type(self.quote_level) is not int or self.quote_level < 0:
            raise StorageError('SPOT_SLOT_INVALID')
        if any(type(v) is not int or v < 1 for v in (self.epoch, self.config_version)):
            raise StorageError('SPOT_VERSION_INVALID')

    def json(self):
        return {key: str(value) if type(value) is Decimal else value
                for key, value in self.__dict__.items()}

    @classmethod
    def from_json(cls, data):
        values = dict(data)
        for field in ('quantity_base', 'limit_price_usdt', 'reference_price_usdt', 'subsidy_quote_usdt',
                      'fee_buffer_quote_usdt', 'fee_buffer_base_life'):
            values[field] = Decimal(values[field])
        return cls(**values)


class AttemptState(str, Enum):
    PREPARED = 'PREPARED'
    SEND_UNKNOWN = 'SEND_UNKNOWN'
    ACKED = 'ACKED'
    TERMINAL = 'TERMINAL'
    ABORTED_BEFORE_SEND = 'ABORTED_BEFORE_SEND'


@dataclass(frozen=True)
class AckSpot:
    """Account-bound observation supplied by the authenticated connector bridge.

    This DTO does not authenticate exchange facts. The bridge remains pending.
    observed_at is local UTC receipt time, not an exchange ordering timestamp.
    """
    operation_id: str
    prepare_operation_id: str
    intent_id: str
    client_order_id: str
    market: str
    side: str
    session_id: str
    epoch: int
    exchange_order_id: str
    observed_at: datetime

    def __post_init__(self):
        for field in ('operation_id', 'prepare_operation_id', 'intent_id', 'session_id'):
            identifier(getattr(self, field))
        if len(self.operation_id) > 96 or len(self.prepare_operation_id) > 96:
            raise StorageError('SPOT_ACK_OPERATION_INVALID')
        if (type(self.client_order_id) is not str
                or re.fullmatch(r'[A-Za-z0-9]{1,32}', self.client_order_id) is None
                or type(self.exchange_order_id) is not str
                or re.fullmatch(r'[0-9]{1,128}', self.exchange_order_id) is None):
            raise StorageError('SPOT_ACK_IDENTITY_INVALID')
        if self.market != 'LIFE-USDT' or self.side not in ('BUY', 'SELL'):
            raise StorageError('SPOT_MARKET_OR_SIDE_INVALID')
        if type(self.epoch) is not int or self.epoch < 1:
            raise StorageError('SPOT_VERSION_INVALID')
        utc(self.observed_at)

    def json(self):
        return {key: value.isoformat() if isinstance(value, datetime) else value
                for key, value in self.__dict__.items()}


@dataclass(frozen=True)
class AckReceipt:
    binding: StateBinding
    operation_id: str
    intent_id: str
    exchange_order_id: str
    state: str
    replayed: bool
    atomic_scope: AtomicScope


@dataclass(frozen=True)
class FinancialCut:
    checkpoint: StateCut
    components: tuple[tuple[str, str], ...]  # Opaque adapter version tokens, never Paths.

    def __post_init__(self):
        if (not isinstance(self.checkpoint, StateCut) or type(self.components) is not tuple
                or not self.components or any(type(v) is not tuple or len(v) != 2 for v in self.components)
                or len({v[0] for v in self.components}) != len(self.components)):
            raise StorageError('SPOT_CUT_INVALID')
        for name, token in self.components:
            identifier(name)
            identifier(token)

    def json(self):
        return {'checkpoint': self.checkpoint.json(), 'components': [list(v) for v in self.components]}


@dataclass(frozen=True)
class SpotSession:
    session_id: str
    epoch: int
    config_version: int
    started_at: datetime
    expires_at: datetime
    state: str
    last_seen_at: datetime
    rollback_latched: bool
    anchors_usdt: Mapping[str, Decimal]

    def __post_init__(self):
        for value in (self.started_at, self.expires_at, self.last_seen_at):
            utc(value)
        object.__setattr__(self, 'anchors_usdt', MappingProxyType(dict(self.anchors_usdt)))


@dataclass(frozen=True)
class SpotBalance:
    total_base_life: Decimal
    total_quote_usdt: Decimal
    reserved_base_life: Decimal
    reserved_quote_usdt: Decimal
    available_base_life: Decimal
    available_quote_usdt: Decimal
    fee_reserved_base_life: Decimal
    fee_reserved_quote_usdt: Decimal

    def __post_init__(self):
        for field in self.__dataclass_fields__:
            decimal(getattr(self, field))


@dataclass(frozen=True)
class SpotBudget:
    session_subsidy_committed_usdt: Decimal
    day_subsidy_committed_usdt: Decimal
    campaign_subsidy_committed_usdt: Decimal
    subsidy_available_usdt: Decimal
    session_execution_loss_usdt: Decimal
    day_execution_loss_usdt: Decimal
    campaign_execution_loss_usdt: Decimal
    loss_exhausted: bool

    def __post_init__(self):
        for key in self.__dataclass_fields__:
            if key != 'loss_exhausted':
                decimal(getattr(self, key))
        if type(self.loss_exhausted) is not bool:
            raise StorageError('SPOT_BUDGET_STATE_INVALID')


@dataclass(frozen=True)
class SpotSnapshot:
    cut: FinancialCut
    session: SpotSession
    balance: SpotBalance
    budget: SpotBudget
    recovery_required: bool


@dataclass(frozen=True)
class SpotReservation:
    intent_id: str
    side: str
    session_id: str
    epoch: int
    quantity_base_life: Decimal
    remaining_base_life: Decimal
    filled_base_life: Decimal
    limit_price_usdt: Decimal
    state: str


@dataclass(frozen=True)
class SpotAttempt:
    intent_id: str
    client_order_id: str
    reservation_id: str
    session_id: str
    epoch: int
    state: AttemptState
    exchange_order_id: str | None
    cancel_requested: bool
    market: str | None
    side: str | None
    quote_level: int | None
    cancel_attempts: int = 0
    last_cancel_attempt_at: datetime | None = None


@dataclass(frozen=True)
class PrepareReceipt:
    binding: StateBinding
    operation_id: str
    intent_id: str
    state: str
    replayed: bool
    atomic_scope: AtomicScope  # File is recoverable saga, PG target is account transaction.


class SpotSessionRepository(Protocol):
    async def session(self, binding: StateBinding) -> SpotSession:
        ...


class SpotBalanceRepository(Protocol):
    async def balance(self, binding: StateBinding) -> SpotBalance:
        ...


class SpotBudgetRepository(Protocol):
    async def budget(self, binding: StateBinding) -> SpotBudget:
        ...


class SpotReadRepository(SpotSessionRepository, SpotBalanceRepository, SpotBudgetRepository, Protocol):
    async def snapshot(self, binding: StateBinding) -> SpotSnapshot:
        ...

    async def attempt(self, intent_id: str, binding: StateBinding) -> SpotAttempt | None:
        ...

    async def reservation(self, intent_id: str, binding: StateBinding) -> SpotReservation | None:
        ...

    async def lookup_prepare(self, operation_id: str, binding: StateBinding) -> PrepareReceipt | None:
        ...


class SpotPreparationRepository(SpotReadRepository, Protocol):
    atomic_scope: AtomicScope

    async def prepare(self, command: PrepareSpot, *, cut: FinancialCut, owner: OwnerToken) -> PrepareReceipt:
        ...

    async def arm(self, operation_id: str, *, cut: FinancialCut, owner: OwnerToken) -> PrepareReceipt:
        ...

    async def close(self) -> None:
        ...


class SpotAcknowledgementRepository(SpotPreparationRepository, Protocol):
    async def acknowledge(self, command: AckSpot, *, cut: FinancialCut, owner: OwnerToken) -> AckReceipt:
        ...

    async def lookup_ack(self, operation_id: str, binding: StateBinding) -> AckReceipt | None:
        ...


class SpotFillRepository(SpotAcknowledgementRepository, Protocol):
    async def apply_fills(self, command: FillSpot, *, cut: FinancialCut, owner: OwnerToken) -> FillReceipt:
        ...

    async def lookup_fill(self, operation_id: str, binding: StateBinding) -> FillReceipt | None:
        ...
