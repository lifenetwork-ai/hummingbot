"""Account-atomic original SPOT/SWAP/approved-flow/paid-funding observations.

The final CarryBundle is an independently qualified source, never an invented
intermediate account. These DTOs authenticate no provider or operator facts.
"""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.storage.capital import CashflowSpot
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    AtomicScope,
    StateBinding,
    StorageError,
    fingerprint,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import FillSpot, signed_decimal
from hummingbot.strategy_v2.life_liquidity.storage.postgres.funding import ApplyFunding
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_fills import FillSwap


@dataclass(frozen=True)
class ApplyJointBatch:
    operation_id: str
    spot_fills: tuple[FillSpot, ...]
    swap_fills: tuple[FillSwap, ...]
    cashflows: tuple[CashflowSpot, ...]
    funding: ApplyFunding

    def __post_init__(self):
        identifier(self.operation_id)
        if len(self.operation_id) > 96 or type(self.funding) is not ApplyFunding:
            raise StorageError('PG_JOINT_BATCH_COMMAND_INVALID')
        for name, kind in (('spot_fills', FillSpot), ('swap_fills', FillSwap), ('cashflows', CashflowSpot)):
            rows = getattr(self, name)
            if type(rows) is not tuple or any(type(row) is not kind for row in rows):
                raise StorageError('PG_JOINT_BATCH_COMMAND_INVALID')
        ids = [self.operation_id, self.funding.operation_id, *(r.operation_id for r in (*self.spot_fills, *self.swap_fills, *self.cashflows))]
        if len(ids) != len(set(ids)) or len(ids) > 257:
            raise StorageError('PG_JOINT_BATCH_MEMBER_ID_CONFLICT')

    def json(self):
        return dict(operation_id=self.operation_id, spot_fills=[row.json() for row in self.spot_fills],
                    swap_fills=[row.json() for row in self.swap_fills], cashflows=[row.json() for row in self.cashflows], funding=self.funding.json())

    @classmethod
    def from_json(cls, value):
        try:
            if type(value) is not dict or set(value) != {'operation_id', 'spot_fills', 'swap_fills', 'cashflows', 'funding'}:
                raise StorageError('PG_JOINT_BATCH_COMMAND_INVALID')
            if any(type(value[name]) is not list for name in ('spot_fills', 'swap_fills', 'cashflows')):
                raise StorageError('PG_JOINT_BATCH_COMMAND_INVALID')
            flows = []
            for row in value['cashflows']:
                data = dict(row)
                for name in ('signed_amount', 'independent_price_usdt'):
                    if type(data[name]) is not str:
                        raise StorageError('PG_JOINT_BATCH_COMMAND_INVALID')
                    data[name] = Decimal(data[name])
                data['observed_at'] = datetime.fromisoformat(data['observed_at'])
                flows.append(CashflowSpot(**data))
            return cls(value['operation_id'], tuple(FillSpot.from_json(r) for r in value['spot_fills']),
                       tuple(FillSwap.from_json(r) for r in value['swap_fills']), tuple(flows), ApplyFunding.from_json(value['funding']))
        except (KeyError, TypeError, ValueError, ArithmeticError):
            raise StorageError('PG_JOINT_BATCH_COMMAND_INVALID') from None


@dataclass(frozen=True)
class JointBatchReceipt:
    binding: StateBinding
    operation_id: str
    state: str
    cash_delta_usdt: Decimal
    base_delta_life: Decimal
    replayed: bool
    atomic_scope: AtomicScope = field(default=AtomicScope.ACCOUNT, init=False)
    trading_permission: bool = field(default=False, init=False)


class PgJointBatchRepository:
    atomic_scope = AtomicScope.ACCOUNT

    def __init__(self, swap):
        if type(swap) is not PgSwapRepository:
            raise StorageError('PG_JOINT_BATCH_CONTEXT_INVALID')
        self.swap, self.financial, self.runtime = swap, swap.financial, swap.runtime
        self.binding, self.owner = swap.binding, swap.owner

    def _original(self):
        self.swap._original()
        if (self.swap.financial is not self.financial or self.swap.runtime is not self.runtime
                or self.swap.binding != self.binding or self.swap.owner != self.owner):
            raise StorageError('PG_JOINT_BATCH_CONTEXT_CHANGED')

    def _receipt(self, value):
        self._original()
        if (value.get('kind') != 'JOINT_BATCH' or value.get('trading_permission') is not False
                or value.get('state') not in ('APPLIED', 'RECOVERY_REQUIRED') or type(value.get('replayed')) is not bool):
            raise StorageError('PG_JOINT_BATCH_RECEIPT_INVALID')
        return JointBatchReceipt(self.binding, identifier(value['operation_id']), value['state'], signed_decimal(Decimal(value['cash_delta_usdt'])),
                                 signed_decimal(Decimal(value['base_delta_life'])), value['replayed'])

    async def _read(self, operation_id, binding):
        self._original()
        self.financial._binding(binding)
        return await self.swap.capital._read('SELECT life_financial.joint_batch_read($1,$2,$3,$4)',
                                             (self.runtime.config.account_id, self.swap.capital.max_rows, self.swap.capital.max_bytes, operation_id))

    async def frame(self, binding):
        return await self._read(None, binding)

    async def lookup(self, operation_id, binding):
        identifier(operation_id)
        value = await self._read(operation_id, binding)
        return None if value is None else self._receipt({**value['receipt'], 'replayed': True})

    async def apply(self, command, *, cut, owner):
        self._original()
        if type(command) is not ApplyJointBatch or owner != self.owner:
            raise StorageError('PG_JOINT_BATCH_CONTEXT_INVALID')
        version, digest = self.financial._cut(cut, owner)
        body = command.json()
        request = dict(kind='JOINT_BATCH', binding=self.binding.json(), owner=owner.json(), version=version, cut=digest, command=body)
        self.runtime._ingress_epoch += 1
        try:
            return await self.runtime._transaction('SELECT life_financial.joint_batch_command($1,$2,$3,$4,$5,$6,$7)',
                                                   (self.runtime.config.account_id, self.binding.json(), owner.json(), version, digest, fingerprint(request), body),
                                                   owner, self._receipt, observation=True)
        except StorageError as error:
            if str(error) == 'PG_JOINT_BATCH_BOUND':
                self.runtime._healthy = False
            raise
