"""Signed operator commands bound to exact account, request, facts and state cut.

Keys are injected explicitly, never read from trading credentials. This models
local operator approval, not exchange credential fencing or live eligibility.
"""
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Mapping, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from hummingbot.strategy_v2.life_liquidity.storage.capital import ApprovedSpotCashflow
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    OwnerMode,
    OwnerToken,
    StateBinding,
    StorageError,
    digest,
    encode,
    fingerprint,
    identifier,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.import_commands import (
    ImportApprovedCashflows,
    ImportPreparationOrigin,
)
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts

ROLES = frozenset(('ACTIVATE', 'DRAIN', 'RECOVER', 'REVOKE', 'APPROVE_CASHFLOW', 'IMPORT'))


@dataclass(frozen=True)
class ControlSnapshot:
    binding: StateBinding
    cut: FinancialCut
    facts: SpotFinancialRecoveryFacts

    def __post_init__(self):
        if (not isinstance(self.binding, StateBinding) or not isinstance(self.cut, FinancialCut)
                or not isinstance(self.facts, SpotFinancialRecoveryFacts)
                or self.cut.checkpoint.binding != self.binding or self.facts.binding != self.binding):
            raise StorageError('CONTROL_SNAPSHOT_BINDING_INVALID')


@dataclass(frozen=True)
class OperatorApproval:
    operator_id: str
    binding: StateBinding
    role: str
    request_digest: str
    issued_at: datetime
    expires_at: datetime
    signature: str = field(repr=False)

    def __post_init__(self):
        identifier(self.operator_id)
        if not isinstance(self.binding, StateBinding) or self.role not in ROLES:
            raise StorageError('OPERATOR_APPROVAL_SCOPE_INVALID')
        digest(self.request_digest)
        if type(self.signature) is not str or re.fullmatch('[0-9a-f]{128}', self.signature) is None:
            raise StorageError('OPERATOR_SIGNATURE_ENCODING_INVALID')
        utc(self.issued_at)
        utc(self.expires_at)
        if self.issued_at >= self.expires_at:
            raise StorageError('OPERATOR_APPROVAL_TIME_INVALID')

    def body(self):
        return {'operator_id': self.operator_id, 'binding': self.binding.json(), 'role': self.role,
                'request_digest': self.request_digest, 'issued_at': self.issued_at.isoformat(),
                'expires_at': self.expires_at.isoformat()}


@dataclass(frozen=True)
class OperatorVerifier:
    keys: Mapping[str, bytes] = field(repr=False)
    roles: Mapping[str, frozenset[str]]

    def __post_init__(self):
        if (not self.keys or set(self.keys) != set(self.roles)
                or any(type(key) is not bytes or len(key) != 32 for key in self.keys.values())
                or any(type(roles) is not frozenset or not roles or not roles <= ROLES for roles in self.roles.values())):
            raise StorageError('OPERATOR_KEYS_INVALID')
        for key in self.keys:
            identifier(key)
        object.__setattr__(self, 'keys', MappingProxyType(dict(self.keys)))
        object.__setattr__(self, 'roles', MappingProxyType(dict(self.roles)))

    def policy(self):
        return {key: {'key_sha256': hashlib.sha256(secret).hexdigest(), 'roles': sorted(self.roles[key])}
                for key, secret in sorted(self.keys.items())}

    @staticmethod
    def request(command, cut, facts):
        return fingerprint({'command': command, 'cut': cut.json(), 'facts': facts.json()})

    def verify(self, approval, binding, role, command, cut, facts, now):
        if (not isinstance(approval, OperatorApproval) or approval.binding != binding or approval.role != role
                or approval.operator_id not in self.keys or role not in self.roles[approval.operator_id]
                or not approval.issued_at <= utc(now) < approval.expires_at
                or approval.request_digest != self.request(command, cut, facts)):
            raise StorageError('OPERATOR_APPROVAL_UNQUALIFIED')
        self._signature(approval.operator_id, approval.body(), approval.signature)

    def _signature(self, operator, body, signature):
        try:
            Ed25519PublicKey.from_public_bytes(self.keys[operator]).verify(bytes.fromhex(signature), encode(body))
        except (InvalidSignature, ValueError, KeyError, TypeError):
            raise StorageError('OPERATOR_APPROVAL_SIGNATURE_INVALID') from None

    def verify_seed(self, seed, binding):
        operator = seed['operator_id']
        if (operator not in self.keys or seed['body']['purpose'] != 'LIFE_CONTROL_SEED'
                or seed['body']['binding'] != binding.json()):
            raise StorageError('CONTROL_SEED_UNVERIFIED')
        self._signature(operator, seed['body'], seed['signature'])
        return seed['body']['ownership']

    def audit(self, record, binding):
        body = record['approval']
        operator = body['operator_id']
        if (body['binding'] != binding.json() or operator not in self.keys
                or body['role'] not in self.roles[operator]
                or record['cut']['checkpoint']['binding'] != binding.json()
                or record['facts']['binding'] != binding.json()
                or body['request_digest'] != fingerprint({'command': record['payload'],
                                                         'cut': record['cut'], 'facts': record['facts']})):
            raise StorageError('CONTROL_HISTORY_UNVERIFIED')
        self._signature(operator, body, record['signature'])


@dataclass(frozen=True)
class OperatorKeys:
    """Operator-side signing keys; runtime stores only verifier() public keys."""
    keys: Mapping[str, bytes] = field(repr=False)
    roles: Mapping[str, frozenset[str]]

    def __post_init__(self):
        # Reuse shape/ACL validation; bytes here are private 32-byte seeds.
        checked = OperatorVerifier(self.keys, self.roles)
        object.__setattr__(self, 'keys', checked.keys)
        object.__setattr__(self, 'roles', checked.roles)

    def verifier(self):
        public = {key: Ed25519PrivateKey.from_private_bytes(value).public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw) for key, value in self.keys.items()}
        return OperatorVerifier(public, self.roles)

    def sign(self, operator_id, binding, role, command, cut, facts, *, issued_at, expires_at):
        if operator_id not in self.keys or role not in self.roles[operator_id]:
            raise StorageError('OPERATOR_ROLE_DENIED')
        value = OperatorApproval(operator_id, binding, role, OperatorVerifier.request(command, cut, facts),
                                 issued_at, expires_at, '0' * 128)
        signature = Ed25519PrivateKey.from_private_bytes(self.keys[operator_id]).sign(encode(value.body())).hex()
        return OperatorApproval(operator_id, binding, role, value.request_digest, issued_at, expires_at, signature)

    def seed(self, binding, ownership):
        operator = sorted(self.keys)[0]
        body = {'purpose': 'LIFE_CONTROL_SEED', 'binding': binding.json(), 'ownership': ownership}
        return {'operator_id': operator, 'body': body,
                'signature': Ed25519PrivateKey.from_private_bytes(self.keys[operator]).sign(encode(body)).hex()}


@dataclass(frozen=True)
class ApproveSpotCashflow:
    operation_id: str
    bill: ApprovedSpotCashflow

    def __post_init__(self):
        identifier(self.operation_id)
        if len(self.operation_id) > 96 or not isinstance(self.bill, ApprovedSpotCashflow):
            raise StorageError('CASHFLOW_APPROVAL_COMMAND_INVALID')

    def json(self):
        return {'operation_id': self.operation_id, 'kind': 'APPROVE_CASHFLOW', 'bill': self.bill.json()}


@dataclass(frozen=True)
class OwnerTransition:
    operation_id: str
    mode: OwnerMode
    token: OwnerToken | None

    def __post_init__(self):
        identifier(self.operation_id)
        if (len(self.operation_id) > 96 or not isinstance(self.mode, OwnerMode)
                or self.token is not None and not isinstance(self.token, OwnerToken)
                or self.mode is not OwnerMode.UNARMED and self.token is None):
            raise StorageError('OWNER_TRANSITION_INVALID')

    @property
    def role(self):
        return {OwnerMode.ACTIVE: 'ACTIVATE', OwnerMode.DRAINING: 'DRAIN',
                OwnerMode.RECOVERY_ONLY: 'RECOVER', OwnerMode.UNARMED: 'REVOKE'}[self.mode]

    def json(self):
        return {'operation_id': self.operation_id, 'mode': self.mode.value,
                'token': self.token.json() if self.token is not None else None}


@dataclass(frozen=True)
class ControlReceipt:
    binding: StateBinding
    operation_id: str
    mode: OwnerMode
    token: OwnerToken | None
    replayed: bool


class ControlPlaneRepository(Protocol):
    async def import_legacy(self, command: ImportApprovedCashflows | ImportPreparationOrigin, *,
                            approval: OperatorApproval, facts: SpotFinancialRecoveryFacts,
                            cut: FinancialCut) -> ControlReceipt:
        ...

    async def approve_cashflow(self, command: ApproveSpotCashflow, *, approval: OperatorApproval,
                               facts: SpotFinancialRecoveryFacts, cut: FinancialCut) -> ControlReceipt:
        ...

    async def transition_owner(self, command: OwnerTransition, *, approval: OperatorApproval,
                               facts: SpotFinancialRecoveryFacts, cut: FinancialCut) -> ControlReceipt:
        ...
