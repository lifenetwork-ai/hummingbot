"""Native carry evidence and diagnostics. No feed authentication or send grant."""
import asyncio
import inspect
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from uuid import uuid4

from hummingbot.strategy_v2.life_liquidity.carry_monitor import CarryBundle, CarryDecision
from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalClaim, CapitalDecision, _json
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    AtomicScope,
    StorageError,
    StorageUnavailable,
    encode,
    fingerprint,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import sql_error
from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
from hummingbot.strategy_v2.life_liquidity.storage.products import ProductCut


@dataclass(frozen=True)
class CarryReceipt:
    operation_id: str
    decision: CarryDecision
    checked_at_ms: int
    replayed: bool
    atomic_scope: AtomicScope = field(default=AtomicScope.ACCOUNT, init=False)
    trading_permission: bool = field(default=False, init=False)


class PgCarryRepository:
    def __init__(self, capital, *, observation, max_rows, max_bytes):
        if (type(capital) is not PgSharedCapitalRepository or not callable(observation)
                or type(max_rows) is not int or not 0 < max_rows <= 4096
                or type(max_bytes) is not int or not 0 < max_bytes <= 16777216):
            raise StorageError('PG_CARRY_CONTEXT_INVALID')
        self.capital, self.financial, self.runtime = capital, capital.financial, capital.runtime
        self.binding, self.owner = capital.binding, capital.owner
        self.observation, self.max_rows, self.max_bytes = observation, max_rows, max_bytes

    def _original(self):
        self.capital._original()
        if (self.capital.financial is not self.financial or self.capital.runtime is not self.runtime
                or self.capital.binding != self.binding or self.capital.owner != self.owner):
            raise StorageUnavailable('PG_CARRY_CONTEXT_CHANGED')

    def _receipt(self, value):
        self._original()
        if (type(value) is not dict or value.get('kind') != 'CARRY_CHECK' or value.get('trading_permission') is not False
                or type(value.get('replayed')) is not bool or type(value.get('checked_at_ms')) is not int):
            raise StorageUnavailable('PG_CARRY_RECEIPT_INVALID')
        data = value['decision']
        raw = data['capital']
        capital = None if raw is None else CapitalDecision(**{k: v if k in ('allowed', 'reason_code') else Decimal(v) for k, v in raw.items()})
        decision = CarryDecision(**{k: capital if k == 'capital' else v if k in ('allowed', 'reason_code', 'funding_events') else Decimal(v) for k, v in data.items()})
        return CarryReceipt(value['operation_id'], decision, value['checked_at_ms'], value['replayed'])

    async def cut(self, binding):
        self._original()
        return await self.capital.cut(binding)

    async def _read(self, operation_id, binding):
        import asyncpg
        self._original()
        self.financial._binding(binding)

        async def read(c):
            try:
                return await c.fetchval('SELECT life_financial.carry_read($1,$2,$3,$4)', self.runtime.config.account_id,
                                        self.max_rows, self.max_bytes, operation_id)
            except asyncpg.RaiseError as error:
                raise sql_error(error) from None
        result = await self.runtime._admit(read, safety=True)
        self._original()
        return result

    async def frame(self, binding):
        return await self._read(None, binding)

    async def lookup(self, operation_id, binding):
        identifier(operation_id)
        result = await self._read(operation_id, binding)
        return None if result is None else self._receipt({**result['receipt'], 'replayed': True})

    async def observe(self, operation_id, *, cut, owner, prospective=None, _preflight=None):
        identifier(operation_id)
        self._original()
        if (len(operation_id) > 96 or type(cut) is not ProductCut or owner != self.owner
                or prospective is not None and type(prospective) is not CapitalClaim):
            raise StorageError('PG_CARRY_COMMAND_CONTEXT_INVALID')
        version, digest = self.financial._cut(FinancialCut(cut.checkpoint, cut.components), owner)
        candidate = None if prospective is None else _json(asdict(prospective))
        # An unknown COMMIT is resolved through the original operation, never
        # through another feed read or a refreshed deadline.
        if _preflight is None:
            previous = await self._read(operation_id, self.binding)
        else:
            from hummingbot.strategy_v2.life_liquidity.storage.postgres.operation_preflight import (
                consume_operation_read,
            )
            consume_operation_read(self, operation_id, _preflight)
            previous = None
        if previous is not None:
            old = previous['command']
            if (old['binding'] != self.binding.json() or old['owner'] != owner.json() or old['version'] != version
                    or old['cut'] != digest or old['command']['prospective'] != candidate):
                raise StorageError('PG_OPERATION_CONFLICT')
            return self._receipt({**previous['receipt'], 'replayed': True})
        self.runtime._ingress_epoch += 1
        try:
            bundle = self.observation()
            if inspect.isawaitable(bundle):
                bundle = await bundle
        except asyncio.CancelledError:
            self.runtime._healthy = False
            raise
        except Exception:
            bundle = None
        self._original()
        captured = _json(asdict(bundle)) if type(bundle) is CarryBundle else None
        try:
            encode(captured)
        except StorageError:
            # Invalid JSON-domain types are feed failure, never coerced into
            # an integer stamp. Persist a denial and the primary clock advance.
            captured = None
        body = dict(operation_id=operation_id, bundle=captured, prospective=candidate)
        request = dict(kind='CARRY_CHECK', binding=self.binding.json(), owner=owner.json(), version=version, cut=digest, command=body)
        return await self.runtime._transaction('SELECT life_financial.carry_check($1,$2,$3,$4,$5,$6,$7)',
                                               (self.runtime.config.account_id, self.binding.json(), owner.json(), version, digest, fingerprint(request), body),
                                               owner, self._receipt, observation=True)

    async def check_carry(self, binding, prospective=None):
        self.financial._binding(binding)
        result = await self.observe('carry:' + uuid4().hex, cut=await self.cut(binding), owner=self.owner, prospective=prospective)
        return result.decision
