"""Stage native independent stress inputs; inventory always comes from SQL facts."""
from dataclasses import dataclass
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.spot_risk import SpotStressObservation
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint, identifier
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import sql_error
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository


@dataclass(frozen=True)
class ObserveNativeStress:
    operation_id: str
    source: SpotStressObservation

    def __post_init__(self):
        identifier(self.operation_id)
        s = self.source
        if (len(self.operation_id) > 96 or type(s) is not SpotStressObservation or s.source_kind != 'independent_market'
                or any(type(v) is not int or not 0 < v <= 253402300799999 for v in (s.value_at_ms, s.observed_at_ms))
                or s.observed_at_ms < s.value_at_ms
                or any(type(v) is not Decimal or not v.is_finite() or v <= 0 for v in (s.reference_usdt, s.stressed_exit_usdt, s.stressed_high_usdt))
                or s.stressed_exit_usdt > s.reference_usdt or s.stressed_high_usdt < s.reference_usdt
                or any(type(v) is not Decimal or not v.is_finite() or v < 0 for v in (s.exit_depth_base, s.collateral_quote, s.maker_fee_rate, s.exit_fee_rate))):
            raise StorageError('PG_STRESS_COMMAND_INVALID')

    def json(self):
        return dict(operation_id=self.operation_id, **{k: str(v) if type(v) is Decimal else v for k, v in self.source.__dict__.items()})


class PgStressRepository:
    def __init__(self, financial):
        if type(financial) is not PgSpotRepository:
            raise StorageError('PG_STRESS_CONTEXT_INVALID')
        self.financial, self.runtime, self.binding = financial, financial._runtime, financial.binding
        self._original = (financial, self.runtime, self.binding, self.runtime.config, self.runtime._risk_observer)

    def _context(self):
        if (self._original != (self.financial, self.runtime, self.binding, self.runtime.config, self.runtime._risk_observer)
                or self.financial._runtime is not self.runtime or self.financial.binding != self.binding):
            self._original[1]._healthy = False
            raise StorageError('PG_STRESS_CONTEXT_CHANGED')

    def _receipt(self, v):
        self._context()
        if (type(v) is not dict or v.get('kind') != 'STRESS_OBSERVE' or v.get('trading_permission') is not False
                or type(v.get('replayed')) is not bool or type(v.get('decision')) is not dict
                or type(v['decision'].get('allowed')) is not bool or type(v.get('event_version')) is not int
                or v['event_version'] < 0 or type(v['decision'].get('reason_code')) is not str):
            raise StorageError('PG_STRESS_RECEIPT_INVALID')
        return v

    async def observe(self, command, *, cut, owner):
        self._context()
        if type(command) is not ObserveNativeStress:
            raise StorageError('PG_STRESS_COMMAND_INVALID')
        version, digest = self.financial._cut(cut, owner)
        body = command.json()
        request = dict(kind='STRESS_OBSERVE', binding=self.binding.json(), owner=owner.json(), version=version, cut=digest, command=body)
        self.runtime._ingress_epoch += 1
        try:
            return await self.runtime._transaction('SELECT life_financial.risk_stress_command($1,$2,$3,$4,$5,$6,$7)',
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
                return await c.fetchval('SELECT life_financial.risk_stress_frame($1)', self.runtime.config.account_id)
            except asyncpg.RaiseError as e:
                raise sql_error(e) from None
        return self._receipt(await self.runtime._admit(read, safety=True))
