"""Native SWAP fill accounting ports; callbacks/DTOs do not authenticate facts.

Complete cumulative fills publish money atomically or retain a durable pending
command. No ACK, fill receipt or readback authorizes release or an external send.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Context, Decimal, localcontext

from hummingbot.strategy_v2.life_liquidity.storage.contracts import AtomicScope, StorageError, fingerprint, identifier
from hummingbot.strategy_v2.life_liquidity.storage.financial import (
    AckSpot,
    FillReceipt,
    SpotFillObservation,
    decimal,
    signed_decimal,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository


@dataclass(frozen=True)
class SwapFillObservation(SpotFillObservation):
    realized_pnl_usdt: Decimal
    sequence: int | None

    def __post_init__(self):
        super().__post_init__()
        object.__setattr__(self, 'realized_pnl_usdt', signed_decimal(self.realized_pnl_usdt))
        if (self.fee_currency != 'USDT' or re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', self.trade_id) is None
                or self.sequence is not None and (type(self.sequence) is not int or not 0 <= self.sequence <= 9223372036854775807)):
            raise StorageError('PG_SWAP_FILL_INVALID')

    @classmethod
    def from_json(cls, data):
        values = dict(data)
        for key in ('quantity_base_life', 'price_usdt', 'signed_fee', 'independent_price_usdt', 'realized_pnl_usdt'):
            values[key] = Decimal(values[key])
        return cls(**values)


@dataclass(frozen=True)
class FillSwap:
    operation_id: str
    prepare_operation_id: str
    intent_id: str
    client_order_id: str
    market: str
    side: str
    session_id: str
    epoch: int
    exchange_order_id: str
    cumulative_base_life: Decimal
    fills: tuple[SwapFillObservation, ...]
    observed_at: datetime

    def __post_init__(self):
        AckSpot(self.operation_id, self.prepare_operation_id, self.intent_id, self.client_order_id,
                self.market, self.side, self.session_id, self.epoch, self.exchange_order_id, self.observed_at)
        object.__setattr__(self, 'cumulative_base_life', decimal(self.cumulative_base_life, positive=True))
        with localcontext(Context(prec=160)):
            if (type(self.fills) is not tuple or not self.fills or any(type(v) is not SwapFillObservation for v in self.fills)
                    or len({v.trade_id for v in self.fills}) != len(self.fills)
                    or sum((v.quantity_base_life for v in self.fills), Decimal('0')) != self.cumulative_base_life):
                raise StorageError('PG_SWAP_FILL_SNAPSHOT_INVALID')
        object.__setattr__(self, 'fills', tuple(sorted(self.fills, key=lambda v: (v.fill_at_ms, v.trade_id))))

    def json(self):
        return {k: [v.json() for v in value] if k == 'fills' else str(value) if type(value) is Decimal
                else value.isoformat() if k == 'observed_at' else value for k, value in vars(self).items()}

    @classmethod
    def from_json(cls, data):
        values = dict(data)
        values['cumulative_base_life'] = Decimal(values['cumulative_base_life'])
        values['observed_at'] = datetime.fromisoformat(values['observed_at'])
        values['fills'] = tuple(SwapFillObservation.from_json(v) for v in values['fills'])
        return cls(**values)


@dataclass(frozen=True)
class PgSwapFillReceipt(FillReceipt):
    atomic_scope: AtomicScope = field(default=AtomicScope.ACCOUNT, init=False)
    trading_permission: bool = field(default=False, init=False)


class PgSwapFillRepository:
    atomic_scope = AtomicScope.ACCOUNT

    def __init__(self, swap):
        if type(swap) is not PgSwapRepository:
            raise StorageError('PG_SWAP_FILL_CONTEXT_INVALID')
        self.swap, self.financial, self.runtime = swap, swap.financial, swap.runtime
        self.binding, self.owner = swap.binding, swap.owner

    def _original(self):
        self.swap._original()
        if (self.swap.financial is not self.financial or self.swap.runtime is not self.runtime
                or self.swap.binding != self.binding or self.swap.owner != self.owner):
            raise StorageError('PG_SWAP_FILL_CONTEXT_CHANGED')

    def _receipt(self, value):
        if (value.get('kind') != 'SWAP_FILL' or value.get('trading_permission') is not False
                or value.get('state') not in ('APPLIED', 'RECOVERY_REQUIRED') or type(value.get('replayed')) is not bool):
            raise StorageError('PG_SWAP_FILL_RECEIPT_INVALID')
        return PgSwapFillReceipt(self.binding, value['operation_id'], value['intent_id'], Decimal(value['cumulative_base_life']),
                                 Decimal(value['execution_loss_usdt']), value['state'], value['replayed'])

    async def _read(self, operation_id, binding):
        self._original()
        self.financial._binding(binding)
        return await self.swap.capital._read('SELECT life_financial.swap_fill_read($1,$2,$3,$4)',
                                             (self.runtime.config.account_id, self.swap.capital.max_rows, self.swap.capital.max_bytes, operation_id))

    async def frame(self, binding):
        return await self._read(None, binding)

    async def lookup(self, operation_id, binding):
        identifier(operation_id)
        value = await self._read(operation_id, binding)
        return None if value is None else self._receipt({**value['receipt'], 'replayed': True})

    async def apply_fills(self, command, *, cut, owner):
        self._original()
        if type(command) is not FillSwap or owner != self.owner:
            raise StorageError('PG_SWAP_FILL_CONTEXT_INVALID')
        version, digest = self.financial._cut(cut, owner)
        body = command.json()
        request = dict(kind='SWAP_FILL', binding=self.binding.json(), owner=owner.json(), version=version, cut=digest, command=body)
        self.runtime._ingress_epoch += 1
        try:
            return await self.runtime._transaction('SELECT life_financial.swap_fill_command($1,$2,$3,$4,$5,$6,$7)',
                                                   (self.runtime.config.account_id, self.binding.json(), owner.json(), version, digest, fingerprint(request), body),
                                                   owner, self._receipt, observation=True)
        except StorageError as error:
            if str(error) == 'PG_SWAP_FILL_OBSERVATION_BOUND':
                self.runtime._healthy = False
            raise
