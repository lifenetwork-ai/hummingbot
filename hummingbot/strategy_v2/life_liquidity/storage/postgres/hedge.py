"""Native actual-position hedge coordinator; receipts grant no transport."""
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from types import MappingProxyType

from hummingbot.strategy_v2.life_liquidity.hedge_coordinator import HedgeMarket
from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalClaim, _json
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    AtomicScope,
    StorageError,
    encode,
    fingerprint,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import decimal
from hummingbot.strategy_v2.life_liquidity.storage.hedge_application import HedgePlan
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
from hummingbot.strategy_v2.life_liquidity.storage.swap import PrepareSwap


@dataclass(frozen=True)
class UpdateHedgeTarget:
    operation_id: str
    expected_version: int
    target_base: Decimal

    def __post_init__(self):
        identifier(self.operation_id)
        if len(self.operation_id) > 96 or type(self.expected_version) is not int or not 0 <= self.expected_version <= 9223372036854775807:
            raise StorageError('PG_HEDGE_TARGET_INVALID')
        decimal(self.target_base)

    def json(self):
        return dict(operation_id=self.operation_id, expected_version=self.expected_version, target_base=str(self.target_base))

    @classmethod
    def from_json(cls, data):
        try:
            if type(data) is not dict or set(data) != {'operation_id', 'expected_version', 'target_base'} or type(data['target_base']) is not str:
                raise ValueError()
            return cls(data['operation_id'], data['expected_version'], Decimal(data['target_base']))
        except (TypeError, ValueError, KeyError, ArithmeticError):
            raise StorageError('PG_HEDGE_TARGET_INVALID') from None


@dataclass(frozen=True)
class HedgeChild:
    capital_operation_id: str
    carry_operation_id: str
    prepare: PrepareSwap
    claim: CapitalClaim

    def __post_init__(self):
        for v in (self.capital_operation_id, self.carry_operation_id):
            identifier(v)
            if len(v) > 96:
                raise StorageError('PG_HEDGE_CHILD_INVALID')
        if type(self.prepare) is not PrepareSwap or type(self.claim) is not CapitalClaim:
            raise StorageError('PG_HEDGE_CHILD_INVALID')
        if len({self.capital_operation_id, self.carry_operation_id, self.prepare.operation_id}) != 3:
            raise StorageError('PG_HEDGE_CHILD_ID_CONFLICT')

    def json(self):
        return dict(capital_operation_id=self.capital_operation_id, carry_operation_id=self.carry_operation_id,
                    prepare=self.prepare.json(), claim=_json(asdict(self.claim)))

    @classmethod
    def from_json(cls, data):
        try:
            if type(data) is not dict or set(data) != {'capital_operation_id', 'carry_operation_id', 'prepare', 'claim'}:
                raise ValueError()
            claim = dict(data['claim'])
            for key in ('quantity_base', 'price_usdt'):
                if type(claim[key]) is not str:
                    raise ValueError()
                claim[key] = Decimal(claim[key])
            result = cls(data['capital_operation_id'], data['carry_operation_id'], PrepareSwap.from_json(data['prepare']), CapitalClaim(**claim))
            if encode(result.json()) != encode(data):
                raise ValueError()
            return result
        except (TypeError, ValueError, KeyError, ArithmeticError):
            raise StorageError('PG_HEDGE_CHILD_INVALID') from None


@dataclass(frozen=True)
class PlanHedge:
    observation: HedgePlan
    child: HedgeChild | None = None

    def __post_init__(self):
        if type(self.observation) is not HedgePlan or self.child is not None and type(self.child) is not HedgeChild:
            raise StorageError('PG_HEDGE_COMMAND_INVALID')
        if self.child is not None and self.operation_id in (self.child.capital_operation_id, self.child.carry_operation_id, self.child.prepare.operation_id):
            raise StorageError('PG_HEDGE_CHILD_ID_CONFLICT')

    @property
    def operation_id(self):
        return self.observation.operation_id

    @classmethod
    def capture(cls, operation_id, market, *, child=None):
        if type(market) is not HedgeMarket:
            raise StorageError('PG_HEDGE_MARKET_INVALID')
        return cls(HedgePlan.capture(operation_id, market), child)

    def json(self):
        return {**self.observation.json(), 'child': None if self.child is None else self.child.json()}

    @classmethod
    def from_json(cls, data):
        try:
            if type(data) is not dict or set(data) != {'operation_id', 'market', 'child'}:
                raise ValueError()
            return cls(HedgePlan(data['operation_id'], encode(data['market'])),
                       None if data['child'] is None else HedgeChild.from_json(data['child']))
        except (TypeError, ValueError, KeyError, ArithmeticError):
            raise StorageError('PG_HEDGE_COMMAND_INVALID') from None


@dataclass(frozen=True)
class SettleHedge:
    operation_id: str
    plan_operation_id: str

    def __post_init__(self):
        for v in (self.operation_id, self.plan_operation_id):
            identifier(v)
            if len(v) > 96:
                raise StorageError('PG_HEDGE_SETTLEMENT_INVALID')
        if self.operation_id == self.plan_operation_id:
            raise StorageError('PG_HEDGE_CHILD_ID_CONFLICT')

    def json(self):
        return dict(operation_id=self.operation_id, plan_operation_id=self.plan_operation_id)

    @classmethod
    def from_json(cls, data):
        try:
            if type(data) is not dict or set(data) != {'operation_id', 'plan_operation_id'}:
                raise ValueError()
            return cls(**data)
        except (TypeError, ValueError, KeyError):
            raise StorageError('PG_HEDGE_SETTLEMENT_INVALID') from None


@dataclass(frozen=True)
class HedgeReceipt:
    operation_id: str
    kind: str
    state: str
    decision: object
    child_operation_id: str | None
    child_state: str | None
    replayed: bool
    atomic_scope: AtomicScope = field(default=AtomicScope.ACCOUNT, init=False)
    trading_permission: bool = field(default=False, init=False)


class PgHedgeRepository:
    atomic_scope = AtomicScope.ACCOUNT

    def __init__(self, swap):
        if type(swap) is not PgSwapRepository:
            raise StorageError('PG_HEDGE_CONTEXT_INVALID')
        self.swap, self.financial, self.runtime = swap, swap.financial, swap.runtime
        self.binding, self.owner = swap.binding, swap.owner

    def _original(self):
        self.swap._original()
        if (self.swap.financial is not self.financial or self.swap.runtime is not self.runtime
                or self.swap.binding != self.binding or self.swap.owner != self.owner):
            raise StorageError('PG_HEDGE_CONTEXT_CHANGED')

    def _receipt(self, value):
        self._original()
        if (type(value) is not dict or value.get('kind') not in ('HEDGE_TARGET', 'HEDGE_PLAN', 'HEDGE_SETTLE')
                or value.get('trading_permission') is not False or type(value.get('replayed')) is not bool):
            raise StorageError('PG_HEDGE_RECEIPT_INVALID')
        decision = value.get('decision')
        return HedgeReceipt(identifier(value['operation_id']), value['kind'], value['state'],
                            None if decision is None else MappingProxyType(dict(decision)), value.get('child_operation_id'),
                            value.get('child_state'), value['replayed'])

    async def _read(self, binding, operation_id=None):
        self._original()
        self.financial._binding(binding)
        return await self.swap.capital._read('SELECT life_financial.hedge_read($1,$2,$3,$4)',
                                             (self.runtime.config.account_id, self.swap.capital.max_rows, self.swap.capital.max_bytes, operation_id))

    async def frame(self, binding):
        return await self._read(binding)

    async def lookup(self, operation_id, binding):
        identifier(operation_id)
        result = await self._read(binding, operation_id)
        return None if result is None else self._receipt({**result['receipt'], 'replayed': True})

    async def _command(self, kind, body, *, cut, owner):
        self._original()
        if owner != self.owner:
            raise StorageError('PG_HEDGE_CONTEXT_INVALID')
        version, digest = self.financial._cut(cut, owner)
        request = dict(kind=kind, binding=self.binding.json(), owner=owner.json(), version=version, cut=digest, command=body)
        self.runtime._ingress_epoch += 1
        try:
            return await self.runtime._transaction('SELECT life_financial.hedge_command($1,$2,$3,$4,$5,$6,$7,$8)',
                                                   (self.runtime.config.account_id, self.binding.json(), owner.json(), version, digest, fingerprint(request), kind, body),
                                                   owner, self._receipt, observation=kind == 'HEDGE_SETTLE' or kind == 'HEDGE_PLAN' and body['child'] is None)
        except StorageError as error:
            if str(error) == 'PG_HEDGE_BOUND':
                self.runtime._healthy = False
            raise

    async def update_target(self, command, *, cut, owner):
        if type(command) is not UpdateHedgeTarget:
            raise StorageError('PG_HEDGE_TARGET_INVALID')
        return await self._command('HEDGE_TARGET', command.json(), cut=cut, owner=owner)

    async def plan(self, command, *, cut, owner):
        if type(command) is not PlanHedge:
            raise StorageError('PG_HEDGE_COMMAND_INVALID')
        return await self._command('HEDGE_PLAN', command.json(), cut=cut, owner=owner)

    async def settle(self, command, *, cut, owner):
        if type(command) is not SettleHedge:
            raise StorageError('PG_HEDGE_SETTLEMENT_INVALID')
        return await self._command('HEDGE_SETTLE', command.json(), cut=cut, owner=owner)
