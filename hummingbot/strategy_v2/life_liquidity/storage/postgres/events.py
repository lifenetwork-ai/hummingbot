"""Atomic PostgreSQL event reporting; never an accounting or SDK restore grant."""
import json
from dataclasses import dataclass, field

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    OwnerToken,
    ResourceVersion,
    StateCut,
    StorageError,
    StorageUnavailable,
    fingerprint,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.postgres.recorder import PgRecorderRepository
from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import ConnectorHistory, EventProjection, EventSource


@dataclass(frozen=True)
class EventDeliveryReceipt:
    projection_id: str
    event_id: str
    state: str
    replayed: bool
    trading_permission: bool = field(default=False, init=False)


@dataclass(frozen=True)
class PendingEventDelivery:
    command: EventProjection
    original_cut: FinancialCut
    owner: OwnerToken


class PgEventRepository(PgRecorderRepository):
    _controller_sql = "o.body#>>'{command,source,controller_id}'"
    _all_history = True
    _subtypes = ('EVENT',)

    def __init__(self, financial, *, source, max_rows, max_read_bytes):
        if type(source) is not EventSource:
            raise StorageError('PG_EVENT_SOURCE_INVALID')
        super().__init__(financial, controller_id=source.controller_id, max_rows=max_rows, max_read_bytes=max_read_bytes)
        self.source = source

    @staticmethod
    def _receipt(value):
        if value.get('trading_permission') is not False or value.get('kind') not in ('RECORDER_EVENT_STAGE', 'RECORDER_EVENT_APPLY'):
            raise StorageUnavailable('PG_EVENT_RECEIPT_INVALID')
        return EventDeliveryReceipt(value['projection_id'], value['event_id'], value['state'], value['replayed'])

    async def stage(self, command, *, cut, owner):
        if type(command) is not EventProjection or command.binding != self.binding or command.source != self.source:
            raise StorageError('PG_EVENT_COMMAND_INVALID')
        version, digest = self.financial._cut(cut, owner)
        identity = fingerprint({'command': command.json(), 'cut': cut.json(), 'owner': owner.json()})
        args = (self._runtime.config.account_id, self.binding.json(), owner.json(), version, digest, identity,
                command.operation_id, command.json())
        return await self._runtime._transaction(
            'SELECT life_financial.recorder_event_stage($1,$2,$3,$4,$5,$6,$7,$8)',
            args, owner, self._receipt, observation=True)

    async def apply(self, projection_id, *, cut, owner):
        identifier(projection_id)
        version, digest = self.financial._cut(cut, owner)
        args = (self._runtime.config.account_id, self.binding.json(), owner.json(), version, digest, projection_id)
        return await self._runtime._transaction(
            'SELECT life_financial.recorder_event_apply($1,$2,$3,$4,$5,$6)', args, owner, self._receipt, observation=True)

    async def _events(self, binding, pending):
        rows = await self._read(binding, pending)
        result = []
        for row in sorted(rows, key=lambda r: r['body']['version']):
            command = EventProjection.from_json(row['body']['command'])
            if command.operation_id != row['projection_id']:
                raise StorageUnavailable('PG_EVENT_IDENTITY_UNVERIFIED')
            if command.source == self.source:
                result.append((row, command))
        return tuple(result)

    async def pending(self, binding):
        result = []
        for row, command in await self._events(binding, True):
            body, version = row['body'], row['body']['version']
            checkpoint = StateCut(binding, version, version, (ResourceVersion('account', version),), body['cut'])
            result.append(PendingEventDelivery(command, FinancialCut(checkpoint, (('postgres_account', body['cut']),)),
                                               OwnerToken.from_json(body['owner'])))
        return tuple(result)

    async def connector_history(self, source, binding):
        if source != self.source:
            raise StorageError('PG_EVENT_SOURCE_INVALID')
        rows = await self._events(binding, False)
        trades, exchanges = set(), set()
        tracking = '{}'
        for _, command in rows:
            body = json.loads(command.payload_json)
            if body['kind'] == 'OrderFilled':
                trades.add((source.market, body['trade_id'], body['pair']))
            if body['kind'] in ('BuyOrderCreated', 'SellOrderCreated') and body.get('exchange_id') is not None:
                exchanges.add((body['exchange_id'], body['wire']))
            tracking = command.tracking_json
        return ConnectorHistory(source, tuple(sorted(trades)), tuple(sorted(exchanges)), tracking)

    async def read(self, binding):
        return await self.connector_history(self.source, binding)
