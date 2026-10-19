"""Native safety decisions from qualified accounted NAV; reads grant nothing."""
from dataclasses import dataclass, field
from datetime import datetime

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    StorageError,
    StorageUnavailable,
    fingerprint,
    identifier,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import sql_error
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository


@dataclass(frozen=True)
class ObserveNativeRisk:
    operation_id: str
    observed_at: datetime
    market_data_fresh: bool
    latency_ok: bool
    account_ready: bool
    model_ready: bool

    def __post_init__(self):
        identifier(self.operation_id)
        utc(self.observed_at)
        if len(self.operation_id) > 96 or any(type(getattr(self, k)) is not bool for k in ('market_data_fresh', 'latency_ok', 'account_ready', 'model_ready')):
            raise StorageError('PG_RISK_COMMAND_INVALID')

    def json(self):
        return {**self.__dict__, 'observed_at': self.observed_at.isoformat()}


@dataclass(frozen=True)
class NativeRiskReceipt:
    operation_id: str | None
    state: str
    reason_code: str
    risk_epoch: int | None
    replayed: bool
    trading_permission: bool = field(default=False, init=False)


class PgRiskRepository:
    def __init__(self, financial):
        if type(financial) is not PgSpotRepository:
            raise StorageError('PG_RISK_CONTEXT_INVALID')
        self.financial, self.runtime, self.binding = financial, financial._runtime, financial.binding
        self._context = (financial, self.runtime, self.binding, self.runtime.config, self.runtime._risk_observer)

    def _original(self):
        if (self._context != (self.financial, self.runtime, self.binding, self.runtime.config, self.runtime._risk_observer)
                or self.financial._runtime is not self.runtime or self.financial.binding != self.binding):
            self._context[1]._healthy = False
            raise StorageError('PG_RISK_CONTEXT_CHANGED')

    def _receipt(self, value):
        self._original()
        if type(value) is not dict or value.get('kind') != 'RISK_OBSERVE' or value.get('trading_permission') is not False or type(value.get('replayed')) is not bool:
            raise StorageUnavailable('PG_RISK_RECEIPT_INVALID')
        d = value.get('decision')
        if d is None and value.get('risk_epoch') is None:
            return NativeRiskReceipt(None, 'PAUSED', 'STARTUP_REVALIDATION', None, True)
        if type(d) is not dict or d.get('state') not in ('PAUSED', 'DEGRADED', 'NORMAL', 'HALTED') or type(value.get('risk_epoch')) is not int:
            raise StorageUnavailable('PG_RISK_RECEIPT_INVALID')
        return NativeRiskReceipt(identifier(value['operation_id']), d['state'], identifier(d['reason_code']), value['risk_epoch'], value['replayed'])

    async def frame(self):
        import asyncpg
        self._original()

        async def read(c):
            try:
                return await c.fetchval('SELECT life_financial.risk_frame($1)', self.runtime.config.account_id)
            except asyncpg.RaiseError as e:
                raise sql_error(e) from None
        return self._receipt(await self.runtime._admit(read, safety=True))

    async def observe(self, command, *, cut, owner):
        self._original()
        if type(command) is not ObserveNativeRisk:
            raise StorageError('PG_RISK_COMMAND_INVALID')
        version, digest = self.financial._cut(cut, owner)
        request = dict(kind='RISK_OBSERVE', binding=self.binding.json(), owner=owner.json(), version=version, cut=digest, command=command.json())
        self.runtime._ingress_epoch += 1
        try:
            return await self.runtime._transaction('SELECT life_financial.risk_command($1,$2,$3,$4,$5,$6,$7)',
                                                   (self.runtime.config.account_id, self.binding.json(), owner.json(), version, digest, fingerprint(request), command.json()),
                                                   owner, self._receipt, observation=True)
        except BaseException:
            self.runtime._healthy = False
            raise
