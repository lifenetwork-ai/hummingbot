"""Original post-auth SWAP bytes and account-atomic arm, without a send grant."""
import hashlib
import json
from dataclasses import dataclass, field

from hummingbot.strategy_v2.life_liquidity.storage.contracts import AtomicScope, StorageError, fingerprint, identifier
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
from hummingbot.strategy_v2.life_liquidity.storage.quote_send import _constant, _pairs


@dataclass(frozen=True)
class ArmSwapRequest:
    prepare_operation_id: str
    wire_json: str

    def __post_init__(self):
        identifier(self.prepare_operation_id)
        try:
            if len(self.prepare_operation_id) > 96 or type(self.wire_json) is not str or not 0 < len(self.wire_json.encode()) <= 4096:
                raise ValueError()
            body = json.loads(self.wire_json, object_pairs_hook=_pairs, parse_constant=_constant)
            base = {'instId', 'tdMode', 'ordType', 'clOrdId', 'side', 'sz', 'px', 'posSide'}
            if (type(body) is not dict or set(body) not in (base, base | {'reduceOnly'})
                    or any(type(body[k]) is not str for k in base) or 'reduceOnly' in body and body['reduceOnly'] is not True):
                raise ValueError()
        except (ValueError, TypeError, UnicodeError):
            raise StorageError('PG_SWAP_WIRE_INVALID') from None

    @property
    def operation_id(self):
        return 'swap-boundary:' + hashlib.sha256(self.prepare_operation_id.encode()).hexdigest()

    def json(self):
        return dict(operation_id=self.operation_id, prepare_operation_id=self.prepare_operation_id, wire_json=self.wire_json)

    @classmethod
    def from_json(cls, data):
        if type(data) is not dict or set(data) != {'operation_id', 'prepare_operation_id', 'wire_json'}:
            raise StorageError('PG_SWAP_WIRE_INVALID')
        value = cls(data['prepare_operation_id'], data['wire_json'])
        if data['operation_id'] != value.operation_id:
            raise StorageError('PG_SWAP_WIRE_INVALID')
        return value


@dataclass(frozen=True)
class SwapBoundaryReceipt:
    prepare_operation_id: str
    intent_id: str
    wire_sha256: str
    checked_at_ms: int
    valid_until_ms: int
    replayed: bool
    state: str = field(default='SEND_UNKNOWN', init=False)
    trading_permission: bool = field(default=False, init=False)
    atomic_scope: AtomicScope = field(default=AtomicScope.ACCOUNT, init=False)


class PgSwapBoundary:
    def __init__(self, swap):
        if type(swap) is not PgSwapRepository:
            raise StorageError('PG_SWAP_BOUNDARY_CONTEXT_INVALID')
        self.swap, self.runtime, self.financial = swap, swap.runtime, swap.financial
        self.binding, self.owner = swap.binding, swap.owner

    def _original(self):
        self.swap._original()
        if (self.swap.runtime is not self.runtime or self.swap.financial is not self.financial
                or self.swap.binding != self.binding or self.swap.owner != self.owner):
            raise StorageError('PG_SWAP_BOUNDARY_CONTEXT_CHANGED')

    def _receipt(self, data):
        self._original()
        if (data.get('kind') != 'SWAP_BOUNDARY' or data.get('trading_permission') is not False
                or data.get('state') != 'SEND_UNKNOWN' or type(data.get('replayed')) is not bool):
            raise StorageError('PG_SWAP_BOUNDARY_RECEIPT_INVALID')
        return SwapBoundaryReceipt(data['prepare_operation_id'], data['intent_id'], data['wire_sha256'],
                                   data['checked_at_ms'], data['valid_until_ms'], data['replayed'])

    async def _read(self, binding, operation_id):
        self._original()
        self.financial._binding(binding)
        result = await self.swap.capital._read('SELECT life_financial.swap_boundary_read($1,$2,$3,$4)',
                                               (self.runtime.config.account_id, self.swap.capital.max_rows, self.swap.capital.max_bytes, operation_id))
        self._original()
        return result

    async def policy(self, binding):
        result = await self._read(binding, None)
        return result['policy']

    async def lookup(self, prepare_operation_id, binding):
        identifier(prepare_operation_id)
        result = await self._read(binding, prepare_operation_id)
        return None if result is None else self._receipt({**result['receipt'], 'replayed': True})

    async def arm(self, command, *, cut, owner):
        self._original()
        if type(command) is not ArmSwapRequest or owner != self.owner:
            raise StorageError('PG_SWAP_BOUNDARY_COMMAND_INVALID')
        if await self.lookup(command.prepare_operation_id, self.binding) is not None:
            raise StorageError('PG_SWAP_RECONCILE_BEFORE_RETRY')
        version, digest = self.financial._cut(cut, owner)
        body = command.json()
        request = dict(kind='SWAP_BOUNDARY', binding=self.binding.json(), owner=owner.json(), version=version, cut=digest, command=body)
        return await self.runtime._transaction('SELECT life_financial.swap_boundary_arm($1,$2,$3,$4,$5,$6,$7)',
                                               (self.runtime.config.account_id, self.binding.json(), owner.json(), version, digest, fingerprint(request), body),
                                               owner, self._receipt)
