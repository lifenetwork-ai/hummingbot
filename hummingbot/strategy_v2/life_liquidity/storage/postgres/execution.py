"""Stage native execution market inputs; financial admission always remains in SQL."""
from dataclasses import dataclass
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint, identifier
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import sql_error
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository


@dataclass(frozen=True)
class NativeExecutionMarket:
    operation_id: str
    intent_id: str
    value_at_ms: int
    observed_at_ms: int
    source_kind: str
    volume_anchor: str
    best_bid_usdt: Decimal
    best_ask_usdt: Decimal
    public_volume_base: Decimal
    known_own_volume_base: Decimal
    independent_depth_base: Decimal
    expected_impact_quote: Decimal
    fee_rate: Decimal
    independent_value_usdt: Decimal

    def __post_init__(self):
        for value in (self.operation_id, self.intent_id, self.volume_anchor):
            identifier(value)
            if len(value) > 96:
                raise StorageError('PG_EXECUTION_MARKET_COMMAND_INVALID')
        if (self.source_kind != 'independent_market'
                or any(type(v) is not int or not 0 < v <= 253402300799999 for v in (self.value_at_ms, self.observed_at_ms))
                or self.value_at_ms > self.observed_at_ms
                or any(type(getattr(self, k)) is not Decimal or not getattr(self, k).is_finite() or getattr(self, k) < 0 for k in (
                    'best_bid_usdt', 'best_ask_usdt', 'public_volume_base', 'known_own_volume_base', 'independent_depth_base', 'expected_impact_quote', 'fee_rate', 'independent_value_usdt'))
                or not 0 < self.best_bid_usdt < self.best_ask_usdt or self.independent_value_usdt <= 0
                or self.known_own_volume_base > self.public_volume_base):
            raise StorageError('PG_EXECUTION_MARKET_COMMAND_INVALID')

    def json(self):
        return {k: str(v) if type(v) is Decimal else v for k, v in self.__dict__.items()}


class PgExecutionRepository:
    def __init__(self, financial):
        if type(financial) is not PgSpotRepository:
            raise StorageError('PG_EXECUTION_MARKET_CONTEXT_INVALID')
        self.financial, self.runtime, self.binding = financial, financial._runtime, financial.binding
        self._original = (financial, self.runtime, self.binding, self.runtime.config, self.runtime._risk_observer)

    def _context(self):
        if (self._original != (self.financial, self.runtime, self.binding, self.runtime.config, self.runtime._risk_observer)
                or self.financial._runtime is not self.runtime or self.financial.binding != self.binding):
            self._original[1]._healthy = False
            raise StorageError('PG_EXECUTION_MARKET_CONTEXT_CHANGED')

    def _receipt(self, v):
        self._context()
        if (type(v) is not dict or v.get('kind') != 'EXECUTION_OBSERVE' or v.get('trading_permission') is not False
                or type(v.get('replayed')) is not bool or type(v.get('event_version')) is not int
                or v['event_version'] < 1):
            raise StorageError('PG_EXECUTION_MARKET_RECEIPT_INVALID')
        return v

    async def observe(self, command, *, cut, owner):
        self._context()
        if type(command) is not NativeExecutionMarket:
            raise StorageError('PG_EXECUTION_MARKET_COMMAND_INVALID')
        version, digest = self.financial._cut(cut, owner)
        body = command.json()
        request = dict(kind='EXECUTION_OBSERVE', binding=self.binding.json(), owner=owner.json(), version=version, cut=digest, command=body)
        self.runtime._ingress_epoch += 1
        try:
            return await self.runtime._transaction('SELECT life_financial.execution_market_command($1,$2,$3,$4,$5,$6,$7)',
                                                   (self.runtime.config.account_id, self.binding.json(), owner.json(), version, digest, fingerprint(request), body),
                                                   owner, self._receipt, observation=True)
        except BaseException:
            self.runtime._healthy = False
            raise

    async def frame(self):
        import asyncpg
        self._context()

        async def read(c):
            try:
                return await c.fetchval('SELECT life_financial.execution_market_frame($1)', self.runtime.config.account_id)
            except asyncpg.RaiseError as e:
                raise sql_error(e) from None
        value = await self.runtime._admit(read, safety=True)
        if type(value) is not dict or value.get('trading_permission') is not False or type(value.get('decision')) is not dict or type(value['decision'].get('allowed')) is not bool:
            raise StorageError('PG_EXECUTION_MARKET_FRAME_INVALID')
        return value

    async def settle_exit(self, operation_id, intent_id, *, facts, cut, owner):
        from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts
        self._context()
        identifier(operation_id)
        identifier(intent_id)
        if len(operation_id) > 96 or len(intent_id) > 96 or type(facts) is not SpotFinancialRecoveryFacts:
            raise StorageError('PG_EXIT_SETTLEMENT_COMMAND_INVALID')
        self.financial._binding(facts.binding)
        version, digest = self.financial._cut(cut, owner)
        body = dict(operation_id=operation_id, intent_id=intent_id)
        request = dict(kind='SPOT_EXIT_SETTLE', binding=self.binding.json(), owner=owner.json(), command=body)

        def receipt(v):
            self._context()
            if (type(v) is not dict or v.get('kind') != 'SPOT_EXIT_SETTLE' or v.get('trading_permission') is not False or v.get('state') != 'SETTLED'
                    or v.get('operation_id') != operation_id or v.get('intent_id') != intent_id or type(v.get('replayed')) is not bool
                    or any(type(v.get(k)) is not str or not Decimal(v[k]).is_finite() or Decimal(v[k]) < 0 for k in ('actual_usdt', 'held_before_usdt'))):
                raise StorageError('PG_EXIT_SETTLEMENT_RECEIPT_INVALID')
            return v
        self.runtime._ingress_epoch += 1
        try:
            return await self.runtime._transaction('SELECT life_financial.execution_exit_settle($1,$2,$3,$4,$5,$6,$7,$8)',
                                                   (self.runtime.config.account_id, self.binding.json(), owner.json(), version, digest, fingerprint(request), body, facts.json()), owner, receipt, observation=True)
        except BaseException:
            self.runtime._healthy = False
            raise
