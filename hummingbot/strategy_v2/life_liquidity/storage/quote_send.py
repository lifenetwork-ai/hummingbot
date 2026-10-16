"""Exact captured SPOT request boundary; no receipt grants transport authority."""
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Protocol

from hummingbot.strategy_v2.life_liquidity.storage.action_preparation import QuoteAdmissionFacts
from hummingbot.strategy_v2.life_liquidity.storage.actions import SpotQuoteAction
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    AtomicScope,
    OwnerToken,
    StateBinding,
    StorageError,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def _constant(value):
    raise ValueError('Nonfinite JSON constant')


@dataclass(frozen=True)
class ArmQuoteAction:
    prepare_operation_id: str
    action: SpotQuoteAction
    admission: QuoteAdmissionFacts
    wire_json: str

    def __post_init__(self):
        identifier(self.prepare_operation_id)
        if (len(self.prepare_operation_id) > 96 or type(self.action) is not SpotQuoteAction
                or type(self.admission) is not QuoteAdmissionFacts):
            raise StorageError('QUOTE_ARM_COMMAND_INVALID')
        try:
            if type(self.wire_json) is not str or not 0 < len(self.wire_json.encode('utf-8')) <= 4096:
                raise ValueError('Invalid size')
            wire = json.loads(self.wire_json, object_pairs_hook=_pairs, parse_constant=_constant)
            if (type(wire) is not dict or set(wire) != {'clOrdId', 'instId', 'side', 'ordType', 'tdMode', 'px', 'sz'}
                    or any(type(value) is not str for value in wire.values())):
                raise ValueError('Invalid fields')
        except (ValueError, TypeError, UnicodeError):
            raise StorageError('QUOTE_WIRE_INVALID') from None

    @property
    def wire_sha256(self):
        return hashlib.sha256(self.wire_json.encode('utf-8')).hexdigest()

    def json(self):
        return {'prepare_operation_id': self.prepare_operation_id, 'action': self.action.json(),
                'admission': self.admission.json(), 'wire_json': self.wire_json}

    @classmethod
    def from_json(cls, data):
        if type(data) is not dict or set(data) != {'prepare_operation_id', 'action', 'admission', 'wire_json'}:
            raise StorageError('QUOTE_ARM_COMMAND_INVALID')
        return cls(data['prepare_operation_id'], SpotQuoteAction(**data['action']),
                   QuoteAdmissionFacts.from_json(data['admission']), data['wire_json'])


@dataclass(frozen=True)
class QuoteArmReceipt:
    binding: StateBinding
    operation_id: str
    intent_id: str
    wire_sha256: str
    state: str
    replayed: bool
    trading_permission: bool = field(default=False, init=False)
    atomic_scope: AtomicScope = field(default=AtomicScope.ACCOUNT, init=False)

    def __post_init__(self):
        identifier(self.operation_id)
        identifier(self.intent_id)
        if (type(self.binding) is not StateBinding or type(self.wire_sha256) is not str
                or re.fullmatch(r'[0-9a-f]{64}', self.wire_sha256) is None
                or self.state != 'SEND_UNKNOWN' or type(self.replayed) is not bool):
            raise StorageError('QUOTE_ARM_RECEIPT_INVALID')


class QuoteSendRepository(Protocol):
    async def arm_quote_action(self, command: ArmQuoteAction, *, cut: FinancialCut, owner: OwnerToken) -> QuoteArmReceipt:
        ...

    async def lookup_quote_arm(self, prepare_operation_id: str, binding: StateBinding) -> QuoteArmReceipt | None:
        ...
