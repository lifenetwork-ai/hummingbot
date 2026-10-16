"""Durable quote publication bookkeeping. No receipt authorizes an executor/send.

The captured payload is exact opaque UTF-8 JSON from the issuing planner. Its
financial meaning must still be checked by the future action-to-prepare bridge.
"""
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    AtomicScope,
    OwnerToken,
    StateBinding,
    StorageError,
    identifier,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut


def _reject_constant(value):
    raise ValueError('Nonfinite JSON constant')


@dataclass(frozen=True)
class SpotQuoteAction:
    intent_id: str
    controller_id: str
    session_id: str
    epoch: int
    config_version: int
    market: str
    side: str
    level: int
    payload_json: str

    def __post_init__(self):
        for value in (self.intent_id, self.controller_id, self.session_id):
            identifier(value)
        if (self.market != 'LIFE-USDT' or self.side not in ('BUY', 'SELL')
                or any(type(value) is not int or not 0 < value <= 9223372036854775807 for value in (self.epoch, self.config_version))
                or type(self.level) is not int or not 0 <= self.level <= 2147483647
                or type(self.payload_json) is not str):
            raise StorageError('QUOTE_ACTION_INVALID')
        try:
            if not 0 < len(self.payload_json.encode('utf-8')) <= 65536:
                raise ValueError('Payload size')
            payload = json.loads(self.payload_json, parse_constant=_reject_constant)
        except (TypeError, ValueError, UnicodeError):
            raise StorageError('QUOTE_ACTION_PAYLOAD_INVALID') from None
        if type(payload) is not dict or not payload:
            raise StorageError('QUOTE_ACTION_PAYLOAD_INVALID')

    def json(self):
        return dict(self.__dict__)


@dataclass(frozen=True)
class ClaimQuoteActions:
    operation_id: str
    actions: tuple[SpotQuoteAction, ...]

    def __post_init__(self):
        identifier(self.operation_id)
        if (len(self.operation_id) > 96 or type(self.actions) is not tuple or not 0 < len(self.actions) <= 64
                or any(type(row) is not SpotQuoteAction for row in self.actions)
                or len({row.intent_id for row in self.actions}) != len(self.actions)
                or len({(row.market, row.side, row.level) for row in self.actions}) != len(self.actions)):
            raise StorageError('QUOTE_ACTION_BATCH_INVALID')

    def json(self):
        return {'operation_id': self.operation_id, 'actions': [row.json() for row in self.actions]}


@dataclass(frozen=True)
class RunnerActionFacts:
    binding: StateBinding
    observed_at: datetime
    controller_id: str
    executor_ids: tuple[str, ...]
    scope_complete: bool

    def __post_init__(self):
        identifier(self.controller_id)
        object.__setattr__(self, 'observed_at', utc(self.observed_at))
        if (type(self.binding) is not StateBinding or type(self.scope_complete) is not bool
                or type(self.executor_ids) is not tuple or len(self.executor_ids) > 4096):
            raise StorageError('QUOTE_ACTION_RUNNER_FACTS_INVALID')
        for value in self.executor_ids:
            identifier(value)
        if len(set(self.executor_ids)) != len(self.executor_ids):
            raise StorageError('QUOTE_ACTION_RUNNER_FACTS_INVALID')

    def json(self):
        return {**self.__dict__, 'binding': self.binding.json(), 'observed_at': self.observed_at.isoformat(),
                'executor_ids': list(self.executor_ids)}


@dataclass(frozen=True)
class TransitionQuoteAction:
    operation_id: str
    action: SpotQuoteAction
    expected_state: str
    state: str
    facts: RunnerActionFacts

    def __post_init__(self):
        identifier(self.operation_id)
        if (len(self.operation_id) > 96 or type(self.action) is not SpotQuoteAction
                or type(self.facts) is not RunnerActionFacts or (self.expected_state, self.state) not in (
                    ('PROPOSED', 'DISPATCHED'), ('PROPOSED', 'REJECTED'),
                    ('PROPOSED', 'RECONCILED'), ('DISPATCHED', 'RECONCILED'))):
            raise StorageError('QUOTE_ACTION_TRANSITION_INVALID')

    def json(self):
        return {**self.__dict__, 'action': self.action.json(), 'facts': self.facts.json()}


@dataclass(frozen=True)
class QuoteActionReceipt:
    binding: StateBinding
    operation_id: str
    states: tuple[tuple[str, str], ...]
    replayed: bool
    atomic_scope: AtomicScope = AtomicScope.ACCOUNT


@dataclass(frozen=True)
class StoredQuoteAction:
    action: SpotQuoteAction
    state: str


class QuoteActionRepository(Protocol):
    async def claim_quote_actions(self, command: ClaimQuoteActions, *, cut: FinancialCut, owner: OwnerToken) -> QuoteActionReceipt:
        ...

    async def transition_quote_action(self, command: TransitionQuoteAction, *, cut: FinancialCut, owner: OwnerToken) -> QuoteActionReceipt:
        ...

    async def quote_actions(self, binding: StateBinding) -> tuple[StoredQuoteAction, ...]:
        ...

    async def lookup_quote_action(self, operation_id: str, binding: StateBinding) -> QuoteActionReceipt | None:
        ...
