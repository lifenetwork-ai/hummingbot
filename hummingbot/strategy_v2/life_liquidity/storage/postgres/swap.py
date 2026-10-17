"""Native protected SWAP intents; explicit exact-request transport is separate.

Preparation requires a separately retained original shared claim and current
carry evidence. All receipts are diagnostic; ARM is irreversible SEND_UNKNOWN.
"""
import hashlib
from dataclasses import dataclass, field

from hummingbot.strategy_v2.life_liquidity.storage.contracts import AtomicScope, StorageError, fingerprint, identifier
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
from hummingbot.strategy_v2.life_liquidity.storage.products import ProductCut
from hummingbot.strategy_v2.life_liquidity.storage.swap import PrepareSwap, SwapReceipt, _permit


@dataclass(frozen=True)
class PgSwapReceipt(SwapReceipt):
    atomic_scope: AtomicScope = field(default=AtomicScope.ACCOUNT, init=False)
    trading_permission: bool = field(default=False, init=False)


class PgSwapRepository:
    atomic_scope = AtomicScope.ACCOUNT

    def __init__(self, capital):
        if type(capital) is not PgSharedCapitalRepository:
            raise StorageError('PG_SWAP_CONTEXT_INVALID')
        self.capital, self.financial, self.runtime = capital, capital.financial, capital.runtime
        self.binding, self.owner = capital.binding, capital.owner

    def _original(self):
        self.capital._original()
        if (self.capital.financial is not self.financial or self.capital.runtime is not self.runtime
                or self.capital.binding != self.binding or self.capital.owner != self.owner):
            raise StorageError('PG_SWAP_CONTEXT_CHANGED')

    def _receipt(self, value):
        self._original()
        if (value.get('kind') not in ('SWAP_PREPARE', 'SWAP_ARM', 'SWAP_ACK')
                or value.get('trading_permission') is not False or type(value.get('replayed')) is not bool):
            raise StorageError('PG_SWAP_RECEIPT_INVALID')
        return PgSwapReceipt(self.binding, value['operation_id'], value['state'], _permit(value['permit']), value['replayed'])

    async def cut(self, binding):
        self._original()
        return await self.capital.cut(binding)

    async def _read(self, operation_id, binding):
        self._original()
        self.financial._binding(binding)
        return await self.capital._read('SELECT life_financial.swap_read($1,$2,$3,$4)',
                                        (self.runtime.config.account_id, self.capital.max_rows, self.capital.max_bytes, operation_id))

    async def frame(self, binding):
        return await self._read(None, binding)

    async def lookup(self, operation_id, binding):
        identifier(operation_id)
        result = await self._read(operation_id, binding)
        return None if result is None else self._receipt({**result['receipt'], 'replayed': True})

    async def _command(self, kind, body, cut, owner):
        self._original()
        if type(cut) is not ProductCut or owner != self.owner:
            raise StorageError('PG_SWAP_COMMAND_CONTEXT_INVALID')
        version, digest = self.financial._cut(FinancialCut(cut.checkpoint, cut.components), owner)
        request = dict(kind=kind, binding=self.binding.json(), owner=owner.json(), version=version, cut=digest, command=body)
        return await self.runtime._transaction('SELECT life_financial.swap_command($1,$2,$3,$4,$5,$6,$7,$8)',
                                               (self.runtime.config.account_id, self.binding.json(), owner.json(), version, digest, fingerprint(request), kind, body),
                                               owner, self._receipt, observation=kind == 'SWAP_ACK')

    async def prepare(self, command, *, cut, owner):
        if type(command) is not PrepareSwap:
            raise StorageError('PG_SWAP_COMMAND_INVALID')
        return await self._command('SWAP_PREPARE', command.json(), cut, owner)

    @staticmethod
    def _step_id(operation_id, kind):
        identifier(operation_id)
        return 'swap-' + kind.lower() + ':' + hashlib.sha256(operation_id.encode()).hexdigest()

    async def arm(self, operation_id, *, cut, owner):
        if await self._read(self._step_id(operation_id, 'arm'), self.binding) is not None:
            raise StorageError('PG_SWAP_RECONCILE_BEFORE_RETRY')
        body = dict(operation_id=self._step_id(operation_id, 'arm'), prepare_operation_id=operation_id, exchange_order_id=None)
        return await self._command('SWAP_ARM', body, cut, owner)

    async def acknowledge(self, operation_id, *, exchange_order_id, owner):
        self._original()
        if owner != self.owner:
            raise StorageError('PG_SWAP_COMMAND_CONTEXT_INVALID')
        key = self._step_id(operation_id, 'ack')
        previous = await self._read(key, self.binding)
        if previous is not None:
            old = previous['command']
            if old['owner'] != owner.json() or old['command']['exchange_order_id'] != exchange_order_id:
                raise StorageError('PG_OPERATION_CONFLICT')
            return self._receipt({**previous['receipt'], 'replayed': True})
        value = await self.financial.observation_cut(self.binding)
        return await self._command('SWAP_ACK', dict(operation_id=key, prepare_operation_id=operation_id, exchange_order_id=exchange_order_id),
                                   ProductCut(value.cut.checkpoint, value.cut.components), owner)

    async def authorize_boundary(self, operation_id, *, wire_payload, owner):
        # This returns diagnostic SQL state, never a reusable HTTP send grant.
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_send import ArmSwapRequest, PgSwapBoundary
        if type(wire_payload) is not bytes or not 0 < len(wire_payload) <= 4096:
            raise StorageError('PG_SWAP_WIRE_INVALID')
        try:
            wire = wire_payload.decode('utf-8')
        except UnicodeError:
            raise StorageError('PG_SWAP_WIRE_INVALID') from None
        return await PgSwapBoundary(self).arm(ArmSwapRequest(operation_id, wire),
                                              cut=(await self.financial.snapshot(self.binding)).cut, owner=owner)
