"""Durable received ACK before financial application; no grant or replay send."""
from dataclasses import dataclass, field
from datetime import datetime, timezone

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    OwnerToken,
    StateBinding,
    StorageError,
    StorageUnavailable,
    digest,
    fingerprint,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import AckSpot, PrepareSpot
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import sql_error
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository


@dataclass(frozen=True)
class ReceivedAck:
    ingress_id: str
    command: AckSpot
    binding: StateBinding
    owner: OwnerToken
    replayed: bool
    trading_permission: bool = field(default=False, init=False)


class PgAckIngress:
    def __init__(self, financial):
        if type(financial) is not PgSpotRepository:
            raise StorageError('PG_ACK_INGRESS_CONFIG_INVALID')
        self.financial, self.runtime = financial, financial._runtime

    @staticmethod
    def _receipt(value):
        if value.get('kind') not in ('ACK_INGRESS_CAPTURE', 'ACK_INGRESS_APPLIED') or value.get('trading_permission') is not False:
            raise StorageUnavailable('PG_ACK_INGRESS_RECEIPT_INVALID')
        command = dict(value['body']['command'])
        command['observed_at'] = datetime.fromisoformat(command['observed_at']).astimezone(timezone.utc)
        return ReceivedAck(value['ingress_id'], AckSpot(**command), StateBinding.from_json(value['body']['binding']),
                           OwnerToken.from_json(value['body']['owner']), value['replayed'])

    async def capture(self, prepare, exchange_order_id, *, owner, request_sha256):
        if not self.runtime.config.write:
            raise StorageError('PG_ACK_INGRESS_WRITE_REQUIRED')
        if type(prepare) is not PrepareSpot or type(owner) is not OwnerToken:
            raise StorageError('PG_ACK_INGRESS_COMMAND_INVALID')
        digest(request_sha256)
        # Only a structural timestamp placeholder, omitted from the capture.
        # The primary supplies the immutable accounting observation timestamp.
        command = AckSpot('ack:' + fingerprint(prepare.json()), prepare.operation_id, prepare.intent_id,
                          prepare.client_order_id, prepare.market, prepare.side, prepare.session_id, prepare.epoch,
                          exchange_order_id, datetime(2000, 1, 1, tzinfo=timezone.utc)).json()
        command.pop('observed_at')
        body = {'binding': self.financial.binding.json(), 'owner': owner.json(), 'command': command, 'request_sha256': request_sha256}
        self.runtime._ingress_epoch += 1  # Invalidate other local pre-send captures before the first await.
        try:
            return await self.runtime._transaction('SELECT life_financial.spot_capture_ack_ingress($1,$2)',
                                                   (self.runtime.config.account_id, body), owner, self._receipt,
                                                   observation=True, untrusted_ingress=True)
        except BaseException:
            self.runtime._healthy = False
            raise

    async def mark_applied(self, received, *, owner):
        if not self.runtime.config.write:
            raise StorageError('PG_ACK_INGRESS_WRITE_REQUIRED')
        if type(received) is not ReceivedAck or type(owner) is not OwnerToken:
            raise StorageError('PG_ACK_INGRESS_COMMAND_INVALID')
        return await self.runtime._transaction('SELECT life_financial.spot_mark_ack_ingress($1,$2)',
                                               (self.runtime.config.account_id, received.ingress_id), owner, self._receipt,
                                               observation=True, untrusted_ingress=True)

    async def lookup(self, ingress_id):
        identifier(ingress_id)

        async def read(c):
            return await self._read(c, ingress_id)
        value = await self.runtime._admit(read, safety=True)
        return None if value is None else self._receipt(value)

    async def _read(self, connection, ingress_id):
        import asyncpg
        try:
            return await connection.fetchval('SELECT life_financial.spot_read_ack_ingress($1,$2)',
                                             self.runtime.config.account_id, ingress_id)
        except asyncpg.RaiseError as error:
            raise sql_error(error) from None

    async def pending(self, binding, *, max_rows, max_bytes):
        self.financial._binding(binding)
        if (type(max_rows) is not int or not 0 < max_rows <= 4096
                or type(max_bytes) is not int or not 0 < max_bytes <= 16 * 1024 * 1024):
            raise StorageError('PG_ACK_INGRESS_READ_BOUND_INVALID')

        async def read(c):
            rows = await c.fetch("""SELECT r.ingress_id,octet_length(r.body::text)+512 AS bytes
                FROM life_financial.spot_ack_ingress r WHERE r.account_id=$1 AND NOT EXISTS(
                    SELECT 1 FROM life_financial.spot_ack_ingress_receipts d WHERE d.account_id=$1 AND d.ingress_id=r.ingress_id)
                ORDER BY r.ingress_id LIMIT $2""", self.runtime.config.account_id, max_rows + 1)
            if len(rows) > max_rows or sum(row['bytes'] for row in rows) > max_bytes:
                raise StorageUnavailable('PG_ACK_INGRESS_READ_BOUND')
            result = []
            for row in rows:
                value = await self._read(c, row['ingress_id'])
                if value is None:
                    raise StorageUnavailable('PG_ACK_INGRESS_MISSING_ORIGINAL')
                result.append(self._receipt(value))
            return tuple(result)
        return await self.runtime._admit(read, safety=True)
