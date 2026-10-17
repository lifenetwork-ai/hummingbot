"""Native account-atomic shared capital allocations; no source or send grant.

The explicit observation callback must be qualified by the owning application.
SQL checks units, freshness, identity, current native cash and every risk term.
No mutable file checkpoint is imported or retained by this adapter.
"""
import inspect
from dataclasses import asdict, dataclass, field
from decimal import Decimal, localcontext
from uuid import uuid4

from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalClaim, CapitalDecision, CapitalSnapshot, _json
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    AtomicScope,
    OwnerToken,
    StorageError,
    StorageUnavailable,
    fingerprint,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import sql_error
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.products import ProductCut, ProductReceipt, ReserveCapital


@dataclass(frozen=True)
class SharedCapitalReceipt(ProductReceipt):
    atomic_scope: AtomicScope = field(default=AtomicScope.ACCOUNT, init=False)
    trading_permission: bool = field(default=False, init=False)


class PgSharedCapitalRepository:
    atomic_scope = AtomicScope.ACCOUNT

    def __init__(self, financial, *, owner, observation, max_rows, max_bytes):
        if (type(financial) is not PgSpotRepository or type(owner) is not OwnerToken or not callable(observation)
                or type(max_rows) is not int or not 0 < max_rows <= 4096
                or type(max_bytes) is not int or not 0 < max_bytes <= 16777216):
            raise StorageError('PG_CAPITAL_CONTEXT_INVALID')
        self.financial, self.runtime, self.binding = financial, financial._runtime, financial.binding
        self.owner, self.observation = owner, observation
        self.max_rows, self.max_bytes = max_rows, max_bytes

    def _original(self):
        if (self.financial._runtime is not self.runtime or self.financial.binding != self.binding
                or self.runtime.config.binding != self.binding or self.runtime._closing):
            raise StorageUnavailable('PG_CAPITAL_CONTEXT_CHANGED')

    def _receipt(self, value):
        if (type(value) is not dict or value.get('kind') != 'SHARED_CAPITAL' or value.get('trading_permission') is not False
                or type(value.get('changed')) is not bool or type(value.get('replayed')) is not bool):
            raise StorageUnavailable('PG_CAPITAL_RECEIPT_INVALID')
        data = value['decision']
        decision = CapitalDecision(**{k: v if k in ('allowed', 'reason_code') else Decimal(v) for k, v in data.items()})
        return SharedCapitalReceipt(self.binding, value['operation_id'], decision, value['changed'], value['replayed'])

    async def cut(self, binding):
        self._original()
        current = (await self.financial.snapshot(binding)).cut
        return ProductCut(current.checkpoint, current.components)

    async def _read(self, query, args):
        import asyncpg
        self._original()

        async def read(c):
            try:
                return await c.fetchval(query, *args)
            except asyncpg.RaiseError as error:
                raise sql_error(error) from None
        value = await self.runtime._admit(read, safety=True)
        self._original()
        return value

    async def frame(self, binding):
        self.financial._binding(binding)
        return await self._read('SELECT life_financial.shared_capital_frame($1,$2,$3)',
                                (self.runtime.config.account_id, self.max_rows, self.max_bytes))

    async def claims(self, binding):
        value = await self.frame(binding)
        with localcontext() as precision:
            precision.prec = 160
            return tuple(CapitalClaim(**{**c, 'quantity_base': Decimal(c['quantity_base']), 'price_usdt': Decimal(c['price_usdt'])}) for c in value['claims'])

    async def _command(self, operation_id, claim, cut, owner):
        identifier(operation_id)
        self._original()
        if type(cut) is not ProductCut or owner != self.owner or type(owner) is not OwnerToken:
            raise StorageError('PG_CAPITAL_COMMAND_CONTEXT_INVALID')
        version, digest = self.financial._cut(FinancialCut(cut.checkpoint, cut.components), owner)
        captured = None if claim is None else _json(asdict(claim))
        existing = await self._operation(operation_id, self.binding)
        if existing is not None:
            request = existing['command']
            if (request['owner'] != owner.json() or request['binding'] != self.binding.json()
                    or request['version'] != version or request['cut'] != digest
                    or request['command']['claim'] != captured
                    or request['command']['kind'] != ('OBSERVE' if claim is None else 'RESERVE')):
                raise StorageError('PG_OPERATION_CONFLICT')
            return self._receipt({**existing['receipt'], 'replayed': True})
        self.runtime._ingress_epoch += 1
        try:
            snapshot = self.observation()
            if inspect.isawaitable(snapshot):
                snapshot = await snapshot
            self._original()
            if type(snapshot) is not CapitalSnapshot or type(snapshot.leverage) is not int:
                raise StorageError('PG_CAPITAL_SNAPSHOT_UNQUALIFIED')
            body = dict(operation_id=operation_id, kind='OBSERVE' if claim is None else 'RESERVE', snapshot=_json(asdict(snapshot)), claim=captured)
            request = dict(kind='SHARED_CAPITAL', binding=self.binding.json(), owner=owner.json(), version=version, cut=digest, command=body)
            identity = fingerprint(request)
        except BaseException:
            # A cancelled/failed refresh must not leave the previous account
            # decision usable for another new-risk command in this process.
            self.runtime._healthy = False
            raise
        try:
            return await self.runtime._transaction('SELECT life_financial.shared_capital_command($1,$2,$3,$4,$5,$6,$7)',
                                                   (self.runtime.config.account_id, self.binding.json(), owner.json(), version, digest, identity, body),
                                                   owner, self._receipt, observation=claim is None and not self.runtime._healthy)
        except StorageError as error:
            if str(error) in ('PG_CAPITAL_SNAPSHOT_UNQUALIFIED', 'PG_CAPITAL_SPOT_BALANCE_MISMATCH',
                              'PG_CAPITAL_SNAPSHOT_REPLAY_OR_MUTATION', 'PG_CAPITAL_NATIVE_ORDER_SCOPE_INCOMPLETE'):
                self.runtime._healthy = False
            raise

    async def reserve(self, command, cut, owner):
        if type(command) is not ReserveCapital or type(command.claim) is not CapitalClaim:
            raise StorageError('PG_CAPITAL_COMMAND_INVALID')
        return await self._command(command.operation_id, command.claim, cut, owner)

    async def check_capital(self, binding):
        result = await self._command('capital-check:' + uuid4().hex, None, await self.cut(binding), self.owner)
        return result.decision

    async def _operation(self, operation_id, binding):
        identifier(operation_id)
        self.financial._binding(binding)
        return await self._read('SELECT life_financial.shared_capital_read_operation($1,$2,$3,$4)',
                                (self.runtime.config.account_id, operation_id, self.max_rows, self.max_bytes))

    async def lookup(self, operation_id, binding):
        value = await self._operation(operation_id, binding)
        return None if value is None else self._receipt({**value['receipt'], 'replayed': True})
