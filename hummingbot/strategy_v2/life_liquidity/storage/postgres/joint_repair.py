"""Signed source repair for joint/funding pending; raw facts and HALT survive.

Source labels/complete flags are caller assertions until the separately qualified
joint collector is installed. This port authenticates no exchange response.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    AtomicScope,
    StateBinding,
    StorageError,
    fingerprint,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_batch import ApplyJointBatch, PgJointBatchRepository
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts


@dataclass(frozen=True)
class JointRepairFacts:
    account: SpotFinancialRecoveryFacts
    product_trade_ids: tuple[tuple[str, str], ...]
    funding_bill_ids: tuple[str, ...]
    swap_scope_complete: bool
    coverage_from_ms: int
    coverage_through_ms: int

    def __post_init__(self):
        if (type(self.account) is not SpotFinancialRecoveryFacts or type(self.product_trade_ids) is not tuple
                or any(type(v) is not tuple or len(v) != 2 or type(v[0]) is not str or v[0] not in ('SPOT', 'SWAP') or type(v[1]) is not str for v in self.product_trade_ids)
                or len(set(self.product_trade_ids)) != len(self.product_trade_ids)
                or {v[1] for v in self.product_trade_ids} != set(self.account.account_trade_ids)
                or type(self.funding_bill_ids) is not tuple
                or any(type(v) is not str or re.fullmatch('[0-9]{1,64}', v) is None for v in self.funding_bill_ids) or len(set(self.funding_bill_ids)) != len(self.funding_bill_ids)
                or type(self.swap_scope_complete) is not bool):
            raise StorageError('PG_JOINT_REPAIR_FACTS_INVALID')
        for _, trade in self.product_trade_ids:
            identifier(trade)
        if (any(type(v) is not int or v <= 0 for v in (self.coverage_from_ms, self.coverage_through_ms))
                or not self.coverage_from_ms <= self.coverage_through_ms <= int(self.account.observed_at.timestamp() * 1000)):
            raise StorageError('PG_JOINT_REPAIR_COVERAGE_INVALID')
        object.__setattr__(self, 'product_trade_ids', tuple(sorted(self.product_trade_ids)))
        object.__setattr__(self, 'funding_bill_ids', tuple(sorted(self.funding_bill_ids)))

    @property
    def binding(self):
        return self.account.binding

    @property
    def observed_at(self):
        return self.account.observed_at

    def json(self):
        return dict(binding=self.binding.json(), observed_at=self.observed_at.isoformat(), account=self.account.json(),
                    product_trade_ids=[list(v) for v in self.product_trade_ids], funding_bill_ids=list(self.funding_bill_ids),
                    swap_scope_complete=self.swap_scope_complete, coverage_from_ms=self.coverage_from_ms, coverage_through_ms=self.coverage_through_ms)

    @classmethod
    def from_json(cls, value):
        try:
            if type(value) is not dict or set(value) != {'binding', 'observed_at', 'account', 'product_trade_ids', 'funding_bill_ids',
                                                         'swap_scope_complete', 'coverage_from_ms', 'coverage_through_ms'}:
                raise StorageError('PG_JOINT_REPAIR_FACTS_INVALID')
            account = value['account']
            for key in ('account_base_life', 'account_quote_usdt', 'liability_base_life', 'liability_quote_usdt'):
                if type(account[key]) is not str:
                    raise StorageError('PG_JOINT_REPAIR_FACTS_INVALID')
            if (type(value['product_trade_ids']) is not list or any(type(v) is not list for v in value['product_trade_ids'])
                    or type(value['funding_bill_ids']) is not list):
                raise StorageError('PG_JOINT_REPAIR_FACTS_INVALID')
            facts = cls(SpotFinancialRecoveryFacts.from_json(account), tuple(tuple(v) for v in value['product_trade_ids']),
                        tuple(value['funding_bill_ids']), value['swap_scope_complete'], value['coverage_from_ms'], value['coverage_through_ms'])
            if facts.binding.json() != value['binding'] or facts.observed_at != datetime.fromisoformat(value['observed_at']):
                raise StorageError('PG_JOINT_REPAIR_FACTS_INVALID')
            return facts
        except (KeyError, TypeError, ValueError, ArithmeticError):
            raise StorageError('PG_JOINT_REPAIR_FACTS_INVALID') from None


@dataclass(frozen=True)
class ResolveJointSources:
    operation_id: str
    pending_operation_id: str
    replacement: ApplyJointBatch

    def __post_init__(self):
        for value in (self.operation_id, self.pending_operation_id):
            identifier(value)
            if len(value) > 96:
                raise StorageError('PG_JOINT_REPAIR_COMMAND_INVALID')
        if type(self.replacement) is not ApplyJointBatch:
            raise StorageError('PG_JOINT_REPAIR_COMMAND_INVALID')
        ids = [self.operation_id, self.pending_operation_id, self.replacement.operation_id,
               *(r.operation_id for r in (*self.replacement.spot_fills, *self.replacement.swap_fills, *self.replacement.cashflows)), self.replacement.funding.operation_id]
        if len(ids) != len(set(ids)):
            raise StorageError('PG_JOINT_REPAIR_ID_CONFLICT')

    def json(self):
        return dict(operation_id=self.operation_id, pending_operation_id=self.pending_operation_id, replacement=self.replacement.json())

    @classmethod
    def from_json(cls, value):
        if type(value) is not dict or set(value) != {'operation_id', 'pending_operation_id', 'replacement'}:
            raise StorageError('PG_JOINT_REPAIR_COMMAND_INVALID')
        return cls(value['operation_id'], value['pending_operation_id'], ApplyJointBatch.from_json(value['replacement']))


@dataclass(frozen=True)
class JointRepairReceipt:
    binding: StateBinding
    operation_id: str
    pending_operation_id: str
    replacement_operation_id: str
    replayed: bool
    state: str = field(default='REPAIRED', init=False)
    atomic_scope: AtomicScope = field(default=AtomicScope.ACCOUNT, init=False)
    trading_permission: bool = field(default=False, init=False)


class PgJointRepairRepository:
    atomic_scope = AtomicScope.ACCOUNT

    def __init__(self, batch):
        if type(batch) is not PgJointBatchRepository:
            raise StorageError('PG_JOINT_REPAIR_CONTEXT_INVALID')
        self.batch, self.financial, self.runtime = batch, batch.financial, batch.runtime
        self.binding, self.owner = batch.binding, batch.owner

    def _original(self):
        self.batch._original()
        if (self.batch.financial is not self.financial or self.batch.runtime is not self.runtime
                or self.batch.binding != self.binding or self.batch.owner != self.owner):
            raise StorageError('PG_JOINT_REPAIR_CONTEXT_CHANGED')

    def _receipt(self, value):
        self._original()
        if (value.get('kind') != 'JOINT_REPAIR' or value.get('state') != 'REPAIRED' or value.get('trading_permission') is not False
                or type(value.get('replayed')) is not bool):
            raise StorageError('PG_JOINT_REPAIR_RECEIPT_INVALID')
        return JointRepairReceipt(self.binding, identifier(value['operation_id']), identifier(value['pending_operation_id']),
                                  identifier(value['replacement_operation_id']), value['replayed'])

    async def _read(self, operation_id, binding):
        self._original()
        self.financial._binding(binding)
        return await self.batch.swap.capital._read('SELECT life_financial.joint_repair_read($1,$2,$3,$4)',
                                                   (self.runtime.config.account_id, self.batch.swap.capital.max_rows, self.batch.swap.capital.max_bytes, operation_id))

    async def frame(self, binding):
        return await self._read(None, binding)

    async def lookup(self, operation_id, binding):
        identifier(operation_id)
        result = await self._read(operation_id, binding)
        return None if result is None else self._receipt({**result['receipt'], 'replayed': True})

    async def resolve(self, command, *, cut, owner):
        self._original()
        if type(command) is not ResolveJointSources or owner != self.owner:
            raise StorageError('PG_JOINT_REPAIR_CONTEXT_INVALID')
        version, digest = self.financial._cut(cut, owner)
        body = command.json()
        request = dict(kind='JOINT_REPAIR', binding=self.binding.json(), owner=owner.json(), version=version, cut=digest, command=body)
        self.runtime._ingress_epoch += 1
        try:
            return await self.runtime._transaction('SELECT life_financial.joint_repair_command($1,$2,$3,$4,$5,$6,$7)',
                                                   (self.runtime.config.account_id, self.binding.json(), owner.json(), version, digest, fingerprint(request), body),
                                                   owner, self._receipt, observation=True)
        except StorageError as error:
            if str(error) == 'PG_JOINT_REPAIR_BOUND':
                self.runtime._healthy = False
            raise
