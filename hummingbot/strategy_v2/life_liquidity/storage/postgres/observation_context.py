"""Bounded one-statement financial inputs for SPOT provider reconciliation.

This read never infers completeness of provider/runner facts or grants trading.
Known financial pending observations need explicit resolution; this reader
rejects them rather than returning a deceptively empty pending scope.
"""
from datetime import datetime
from decimal import Decimal, localcontext

from hummingbot.strategy_v2.life_liquidity.storage.capital import (
    ApprovedSpotCashflow,
    CashflowSpot,
    SpotAccountingPolicy,
)
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken, StorageError, StorageUnavailable
from hummingbot.strategy_v2.life_liquidity.storage.financial import (
    AckSpot,
    PrepareSpot,
    RecordedSpotFill,
    SpotFillObservation,
    SpotFillPolicy,
)
from hummingbot.strategy_v2.life_liquidity.storage.pending import PendingSpotCommand
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import sql_error
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.spot_observation import SpotObservationContext


class PgSpotObservationReader:
    def __init__(self, repository, *, max_rows, max_bytes):
        if (type(repository) is not PgSpotRepository or type(max_rows) is not int or not 0 < max_rows <= 4096
                or type(max_bytes) is not int or not 0 < max_bytes <= 16 * 1024 * 1024):
            raise StorageError('PG_SPOT_CONTEXT_CONFIG_INVALID')
        self.repository, self.runtime = repository, repository._runtime
        self.max_rows, self.max_bytes = max_rows, max_bytes

    async def observation_context(self, binding):
        import asyncpg
        self.repository._binding(binding)

        async def read(c):
            try:
                return await c.fetchval('SELECT life_financial.spot_observation_context($1,$2,$3)',
                                        self.runtime.config.account_id, self.max_rows, self.max_bytes)
            except asyncpg.RaiseError as error:
                raise sql_error(error) from None
        value = await self.runtime._admit(read, safety=True)
        try:
            if any(value['snapshot']['account'][key] != field for key, field in binding.json().items()):
                raise StorageUnavailable('PG_SPOT_CONTEXT_BINDING_CHANGED')
            with localcontext() as precision:
                precision.prec = 120
                view = self.repository._snapshot(value['snapshot'], binding)
            data = dict(value['policy'])
            for key in ('opening_base_life', 'opening_quote_usdt', 'opening_independent_price_usdt'):
                data[key] = Decimal(data[key])
            data['opened_at'] = datetime.fromisoformat(data['opened_at'])
            data['approved_cashflows'] = tuple(ApprovedSpotCashflow(**{**row, 'signed_amount': Decimal(row['signed_amount'])}) for row in value['approvals'])
            cashflows = []
            for row in value['cashflows']:
                row = dict(row)
                for key in ('signed_amount', 'independent_price_usdt'):
                    row[key] = Decimal(row[key])
                row['observed_at'] = datetime.fromisoformat(row['observed_at'])
                cashflows.append(CashflowSpot(**row))
            pending = []
            for row in value['ack_pending']:
                command = dict(row['command'])
                command['observed_at'] = datetime.fromisoformat(command['observed_at'])
                pending.append(PendingSpotCommand('ACK', AckSpot(**command), OwnerToken.from_json(row['owner'])))
            return SpotObservationContext(binding, view.cut, SpotAccountingPolicy(**data), SpotFillPolicy(**value['fill_policy']),
                                          tuple(PrepareSpot.from_json(row) for row in value['commands']),
                                          tuple(RecordedSpotFill(row['prepare_operation_id'], row['intent_id'], row['exchange_order_id'],
                                                                 SpotFillObservation.from_json(row['fill'])) for row in value['fills']),
                                          tuple(cashflows), tuple(pending))
        except (KeyError, TypeError, ValueError):
            raise StorageUnavailable('PG_SPOT_CONTEXT_INVALID') from None
