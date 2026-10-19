"""Original FILL/CASHFLOW command intake before monetary application.

This journal is untrusted diagnostics. Provider authentication/collection and
configured service composition must qualify the source separately. Received
commands/replay never grant trading permission, rewrite values or release funds.
"""
import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, localcontext

from hummingbot.strategy_v2.life_liquidity.storage.capital import CashflowSpot
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    OwnerToken,
    StateBinding,
    StorageError,
    StorageUnavailable,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import FillSpot
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import sql_error
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository


@dataclass(frozen=True)
class ReceivedFinancial:
    ingress_id: str
    command: FillSpot | CashflowSpot
    binding: StateBinding
    owner: OwnerToken
    captured_at: datetime
    capture_sequence: int
    replayed: bool
    applied_operation_id: str | None = None
    trading_permission: bool = field(default=False, init=False)


class PgFinancialIngress:
    def __init__(self, financial):
        if type(financial) is not PgSpotRepository:
            raise StorageError('PG_FINANCIAL_INGRESS_CONFIG_INVALID')
        self.financial, self.runtime = financial, financial._runtime
        if not hasattr(self.runtime, '_financial_ingress_mutex'):
            self.runtime._financial_ingress_mutex = asyncio.Lock()
        self._mutex = self.runtime._financial_ingress_mutex

    @staticmethod
    def _receipt(value):
        if value.get('kind') not in ('FINANCIAL_INGRESS_CAPTURE', 'FINANCIAL_INGRESS_APPLIED') or value.get('trading_permission') is not False:
            raise StorageUnavailable('PG_FINANCIAL_INGRESS_RECEIPT_INVALID')
        body = value['body']
        with localcontext() as precision:
            precision.prec = 160
            if body['kind'] == 'SPOT_FILL':
                command = FillSpot.from_json(body['command'])
            elif body['kind'] == 'SPOT_CASHFLOW':
                data = dict(body['command'])
                for key in ('signed_amount', 'independent_price_usdt'):
                    data[key] = Decimal(data[key])
                data['observed_at'] = datetime.fromisoformat(data['observed_at'])
                command = CashflowSpot(**data)
            else:
                raise StorageUnavailable('PG_FINANCIAL_INGRESS_RECEIPT_INVALID')
        return ReceivedFinancial(value['ingress_id'], command, StateBinding.from_json(body['binding']), OwnerToken.from_json(body['owner']),
                                 datetime.fromisoformat(value['captured_at']).astimezone(timezone.utc), value['capture_sequence'], value['replayed'], value.get('applied_operation_id'))

    async def capture(self, command, *, owner):
        if not self.runtime.config.write:
            raise StorageError('PG_FINANCIAL_INGRESS_WRITE_REQUIRED')
        self.runtime._ingress_epoch += 1
        try:
            if type(command) not in (FillSpot, CashflowSpot) or type(owner) is not OwnerToken:
                raise StorageError('PG_FINANCIAL_INGRESS_COMMAND_INVALID')
            kind = 'SPOT_FILL' if type(command) is FillSpot else 'SPOT_CASHFLOW'
            body = {'kind': kind, 'binding': self.financial.binding.json(), 'owner': owner.json(), 'command': command.json()}
            if len(json.dumps(body, allow_nan=False).encode('utf-8')) > 1048576:
                raise StorageUnavailable('PG_FINANCIAL_INGRESS_CAPTURE_BOUND')
            return await self.runtime._transaction('SELECT life_financial.spot_capture_financial_ingress($1,$2)',
                                                   (self.runtime.config.account_id, body), owner, self._receipt,
                                                   observation=True, untrusted_ingress=True)
        except BaseException:
            self.runtime._healthy = False
            raise

    async def mark_applied(self, received, *, financial_operation_id, owner):
        if not self.runtime.config.write:
            raise StorageError('PG_FINANCIAL_INGRESS_WRITE_REQUIRED')
        if type(received) is not ReceivedFinancial or type(owner) is not OwnerToken:
            raise StorageError('PG_FINANCIAL_INGRESS_COMMAND_INVALID')
        identifier(financial_operation_id)
        return await self.runtime._transaction('SELECT life_financial.spot_mark_financial_ingress($1,$2,$3)',
                                               (self.runtime.config.account_id, received.ingress_id, financial_operation_id), owner, self._receipt,
                                               observation=True, untrusted_ingress=True)

    async def _read(self, query, args):
        import asyncpg

        async def read(c):
            try:
                return await c.fetchval(query, *args)
            except asyncpg.RaiseError as error:
                raise sql_error(error) from None
        return await self.runtime._admit(read, safety=True)

    async def lookup(self, ingress_id):
        identifier(ingress_id)
        value = await self._read('SELECT life_financial.spot_read_financial_ingress($1,$2)', (self.runtime.config.account_id, ingress_id))
        return None if value is None else self._receipt(value)

    async def pending(self, binding, *, max_rows, max_bytes):
        self.financial._binding(binding)
        if (type(max_rows) is not int or not 0 < max_rows <= 4096
                or type(max_bytes) is not int or not 0 < max_bytes <= 16 * 1024 * 1024):
            raise StorageError('PG_FINANCIAL_INGRESS_READ_BOUND_INVALID')
        values = await self._read('SELECT life_financial.spot_pending_financial_ingress($1,$2,$3)',
                                  (self.runtime.config.account_id, max_rows, max_bytes))
        return tuple(self._receipt(value) for value in values)

    async def capture_and_apply(self, command, *, owner, max_rows, max_bytes):
        async with self._mutex:
            try:
                received = await self.capture(command, owner=owner)
                lookup = self.financial.lookup_fill if type(command) is FillSpot else self.financial.lookup_cashflow
                if received.applied_operation_id is not None:
                    result = await lookup(received.applied_operation_id, self.financial.binding)
                    if result is None:
                        raise StorageUnavailable('PG_FINANCIAL_INGRESS_MISSING_APPLICATION')
                    return result
                pending = await self.pending(self.financial.binding, max_rows=max_rows, max_bytes=max_bytes)
                self._original_values(received, pending)
                result = await self.financial.apply_received(received.command, owner=received.owner)
                if result.state == 'APPLIED':
                    await self.mark_applied(received, financial_operation_id=received.command.operation_id, owner=received.owner)
                else:
                    self.runtime._healthy = False
                return result
            except BaseException:
                self.runtime._healthy = False
                raise

    @staticmethod
    def _original_values(received, pending):
        command = received.command
        first = {}
        for row in sorted(pending, key=lambda item: item.capture_sequence):
            if type(row.command) is FillSpot:
                for fill in row.command.fills:
                    first.setdefault(('fill', fill.trade_id), (row, fill))
            else:
                first.setdefault(('cashflow', row.command.bill_id), (row, row.command))
        values = ([(('fill', fill.trade_id), fill) for fill in command.fills] if type(command) is FillSpot
                  else [(('cashflow', command.bill_id), command)])
        for key, value in values:
            original, chosen = first[key]
            if original.owner != received.owner or original.binding != received.binding:
                raise StorageError('PG_FINANCIAL_INGRESS_ORIGINAL_SCOPE_CONFLICT')
            if type(command) is FillSpot:
                a, b = chosen.json(), value.json()
                if original.command.intent_id != command.intent_id or original.command.exchange_order_id != command.exchange_order_id:
                    raise StorageError('PG_FINANCIAL_INGRESS_ORIGINAL_SCOPE_CONFLICT')
            else:
                a, b = chosen.json(), value.json()
                for key_field in ('operation_id', 'observed_at'):
                    a.pop(key_field)
                    b.pop(key_field)
            if a != b:
                raise StorageError('PG_FINANCIAL_INGRESS_VALUE_CONFLICT')
