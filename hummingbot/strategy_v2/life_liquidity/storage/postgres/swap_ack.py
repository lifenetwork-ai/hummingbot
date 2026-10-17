"""Untrusted SWAP ACK intake before financial delivery; cold repair never sends."""
from dataclasses import dataclass, field

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    OwnerToken,
    StateBinding,
    StorageError,
    digest,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_send import PgSwapBoundary


@dataclass(frozen=True)
class ReceivedSwapAck:
    prepare_operation_id: str
    exchange_order_id: str
    wire_sha256: str
    binding: StateBinding
    owner: OwnerToken
    replayed: bool = field(compare=False)
    trading_permission: bool = field(default=False, init=False)


class PgSwapAckIngress:
    def __init__(self, boundary):
        if type(boundary) is not PgSwapBoundary:
            raise StorageError('PG_SWAP_ACK_CONTEXT_INVALID')
        self.boundary, self.runtime = boundary, boundary.runtime

    def _receipt(self, data):
        self.boundary._original()
        b = data['body']
        if (data.get('kind') not in ('SWAP_ACK_CAPTURE', 'SWAP_ACK_DELIVERY') or data.get('trading_permission') is not False
                or type(data.get('replayed')) is not bool or b['binding'] != self.boundary.binding.json()
                or b['owner'] != self.boundary.owner.json()):
            raise StorageError('PG_SWAP_ACK_RECEIPT_INVALID')
        return ReceivedSwapAck(b['prepare_operation_id'], b['exchange_order_id'], b['wire_sha256'],
                               self.boundary.binding, self.boundary.owner, data['replayed'])

    async def capture(self, prepare_operation_id, exchange_order_id, *, wire_sha256):
        self.boundary._original()
        identifier(prepare_operation_id)
        digest(wire_sha256)
        if (not self.runtime.config.write or type(exchange_order_id) is not str or not 0 < len(exchange_order_id) <= 64
                or not exchange_order_id.isascii() or not exchange_order_id.isdecimal()):
            raise StorageError('PG_SWAP_ACK_CAPTURE_INVALID')
        body = dict(prepare_operation_id=prepare_operation_id, exchange_order_id=exchange_order_id, wire_sha256=wire_sha256,
                    binding=self.boundary.binding.json(), owner=self.boundary.owner.json())
        self.runtime._ingress_epoch += 1
        try:
            return await self.runtime._transaction('SELECT life_financial.swap_capture_ack($1,$2)',
                                                   (self.runtime.config.account_id, body), self.boundary.owner, self._receipt,
                                                   observation=True, untrusted_ingress=True)
        except BaseException:
            self.runtime._healthy = False
            raise

    async def _read(self, prepare_operation_id):
        self.boundary._original()
        result = await self.boundary.swap.capital._read('SELECT life_financial.swap_read_ack($1,$2,$3,$4)',
                                                        (self.runtime.config.account_id, self.boundary.swap.capital.max_rows,
                                                         self.boundary.swap.capital.max_bytes, prepare_operation_id))
        return tuple(self._receipt(row) for row in result)

    async def pending(self):
        return await self._read(None)

    async def deliver(self, received):
        self.boundary._original()
        if (type(received) is not ReceivedSwapAck or received.binding != self.boundary.binding or received.owner != self.boundary.owner
                or received not in await self._read(received.prepare_operation_id)):
            raise StorageError('PG_SWAP_ACK_ORIGINAL_REQUIRED')
        receipt = await self.boundary.swap.acknowledge(received.prepare_operation_id, exchange_order_id=received.exchange_order_id,
                                                       owner=received.owner)
        await self.runtime._transaction('SELECT life_financial.swap_mark_ack($1,$2)',
                                        (self.runtime.config.account_id, received.prepare_operation_id), received.owner, self._receipt,
                                        observation=True, untrusted_ingress=True)
        return receipt
