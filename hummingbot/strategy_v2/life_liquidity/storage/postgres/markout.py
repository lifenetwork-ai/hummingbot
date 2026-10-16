"""Exact accounted-fill horizon storage and bounded diagnostic cohort reads."""
from dataclasses import dataclass, field
from decimal import Decimal, localcontext

from hummingbot.strategy_v2.life_liquidity.markout import MarkoutSummary, MarkoutTracker
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    StorageError,
    StorageUnavailable,
    fingerprint,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.markout import ObserveSpotMarkout
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import sql_error
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository


@dataclass(frozen=True)
class MarkoutReceipt:
    operation_id: str
    trade_id: str
    horizon_ms: int
    markout_usdt: Decimal
    replayed: bool
    trading_permission: bool = field(default=False, init=False)


@dataclass(frozen=True)
class MarkoutDecision:
    reason_code: str
    cohorts_ready: bool
    summaries: tuple[tuple[str, str, int, MarkoutSummary], ...]
    trading_permission: bool = field(default=False, init=False)


class PgSpotMarkoutRepository:
    def __init__(self, financial, *, max_rows, max_bytes):
        if (type(financial) is not PgSpotRepository or type(max_rows) is not int or not 0 < max_rows <= 4096
                or type(max_bytes) is not int or not 0 < max_bytes <= 16777216):
            raise StorageError('PG_MARKOUT_CONTEXT_INVALID')
        self.financial, self.runtime, self.binding = financial, financial._runtime, financial.binding
        self.max_rows, self.max_bytes = max_rows, max_bytes

    @staticmethod
    def _receipt(value):
        if (type(value) is not dict or type(value.get('replayed')) is not bool or value.get('kind') != 'SPOT_MARKOUT' or value.get('state') != 'RECORDED' or value.get('trading_permission') is not False):
            raise StorageUnavailable('PG_MARKOUT_RECEIPT_INVALID')
        return MarkoutReceipt(value['operation_id'], value['trade_id'], value['horizon_ms'], Decimal(value['markout_usdt']), value['replayed'])

    async def observe(self, command, *, cut, owner):
        if type(command) is not ObserveSpotMarkout:
            raise StorageError('PG_MARKOUT_COMMAND_INVALID')
        version, digest = self.financial._cut(cut, owner)
        self.runtime._ingress_epoch += 1
        identity = fingerprint({'kind': 'SPOT_MARKOUT', 'binding': self.binding.json(), 'owner': owner.json(), 'command': command.json()})
        args = (self.runtime.config.account_id, self.binding.json(), owner.json(), version, digest, identity, command.json())
        return await self.runtime._transaction('SELECT life_financial.spot_observe_markout($1,$2,$3,$4,$5,$6,$7)',
                                               args, owner, self._receipt, observation=True)

    async def _read(self, query, args):
        import asyncpg

        async def read(c):
            try:
                return await c.fetchval(query, *args)
            except asyncpg.RaiseError as error:
                raise sql_error(error) from None
        return await self.runtime._admit(read, safety=True)

    async def frame(self, binding):
        self.financial._binding(binding)
        value = await self._read('SELECT life_financial.spot_markout_frame($1,$2,$3)',
                                 (self.runtime.config.account_id, self.max_rows, self.max_bytes))
        if type(value) is not dict or value.get('trading_permission') is not False:
            raise StorageUnavailable('PG_MARKOUT_FRAME_INVALID')
        return value

    @staticmethod
    def _tracker(value):
        policy = value['policy']
        tracker = MarkoutTracker(horizons_ms=tuple(policy['horizons_ms']), min_samples=policy['min_samples'],
                                 size_bucket_edges=(Decimal(policy['size_cutoff_base']),))
        for row in value['fills']:
            tracker.record_fill(row['trade_id'], row['side'], Decimal(row['quantity_base']), Decimal(row['price_usdt']), fill_at_ms=row['fill_at_ms'])
        for row in value['observations']:
            if not tracker.observe(row['trade_id'], row['horizon_ms'], Decimal(row['independent_price_usdt']),
                                   observed_at_ms=row['reference_observed_at_ms'], source_kind=row['source_kind']):
                raise StorageUnavailable('PG_MARKOUT_OBSERVATION_INVALID')
        return tracker

    async def summary(self, binding, *, side, size_bucket, horizon_ms):
        value = await self.frame(binding)
        with localcontext() as precision:
            precision.prec = 160
            tracker = self._tracker(value)
            if (type(horizon_ms) is not int or horizon_ms not in tracker.horizons_ms
                    or side not in ('BUY', 'SELL') or size_bucket not in ('small', 'large')):
                raise StorageError('PG_MARKOUT_COHORT_INVALID')
            return tracker.summary(side, size_bucket, horizon_ms, now_ms=value['now_ms'])

    async def evaluate(self, binding):
        value = await self.frame(binding)
        with localcontext() as precision:
            precision.prec = 160
            tracker = self._tracker(value)
            observed = {(row['trade_id'], row['horizon_ms']) for row in value['observations']}
            missing = pending = False
            for row in value['fills']:
                for horizon in tracker.horizons_ms:
                    if (row['trade_id'], horizon) not in observed:
                        if value['now_ms'] < row['fill_at_ms'] + horizon:
                            pending = True
                        else:
                            missing = True
            if missing or pending:
                return MarkoutDecision('MARKOUT_OBSERVATION_MISSING' if missing else 'MARKOUT_HORIZON_PENDING', False, ())
            summaries = []
            insufficient = False
            minimum = Decimal(value['policy']['min_mean_markout_quote'])
            for side, bucket, horizon in value['policy']['monitored_cohorts']:
                if horizon not in tracker.horizons_ms:
                    raise StorageUnavailable('PG_MARKOUT_POLICY_UNQUALIFIED')
                result = tracker.summary(side, bucket, horizon, now_ms=value['now_ms'])
                summaries.append((side, bucket, horizon, result))
                if result.reason_code != 'MARKOUT_READY':
                    insufficient = True
                elif result.mean_markout_quote < minimum:
                    return MarkoutDecision('MARKOUT_ADVERSE', False, tuple(summaries))
            return MarkoutDecision('MARKOUT_INSUFFICIENT_SAMPLES' if insufficient else 'MARKOUT_READY', not insufficient, tuple(summaries))

    async def lookup(self, operation_id, binding):
        self.financial._binding(binding)
        identifier(operation_id)
        value = await self._read('''SELECT CASE WHEN (s.value->>'unverified')::boolean THEN jsonb_build_object('invalid',true)
            ELSE o.receipt END FROM (SELECT life_financial.spot_state($1) AS value) s
            LEFT JOIN life_financial.operations o ON o.account_id=$1 AND o.operation_id=$2 AND o.command->>'kind'='SPOT_MARKOUT' ''',
                                 (self.runtime.config.account_id, operation_id))
        if value is None:
            return None
        if value.get('invalid'):
            raise StorageUnavailable('PG_MARKOUT_UNVERIFIED')
        return self._receipt({**value, 'replayed': True})
