"""Original-order terminal observation; shared capital is deliberately held.

Source strings are labels. Configured authenticated joint collection/release
requires its separate implementation and qualification.
"""
from dataclasses import dataclass, field
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    AtomicScope,
    StateBinding,
    StorageError,
    fingerprint,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import decimal
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository


@dataclass(frozen=True)
class ConfirmSwapTerminal:
    operation_id: str
    prepare_operation_id: str
    exchange_order_id: str
    terminal_state: str
    cumulative_base_life: Decimal
    trade_ids: tuple[str, ...]
    observed_at_ms: int
    coverage_from_ms: int
    source: str

    def __post_init__(self):
        identifier(self.operation_id)
        identifier(self.prepare_operation_id)
        decimal(self.cumulative_base_life)
        if (type(self.trade_ids) is not tuple or len(self.trade_ids) > 4096
                or any(type(v) is not str for v in (self.exchange_order_id, self.terminal_state, self.source))
                or any(type(v) is not int or not 0 < v <= 253402300799999 for v in (self.observed_at_ms, self.coverage_from_ms))):
            raise StorageError('PG_SWAP_TERMINAL_COMMAND_INVALID')
        for value in self.trade_ids:
            identifier(value)

    def json(self):
        return dict(operation_id=self.operation_id, prepare_operation_id=self.prepare_operation_id,
                    exchange_order_id=self.exchange_order_id, terminal_state=self.terminal_state,
                    cumulative_base_life=str(self.cumulative_base_life), trade_ids=list(self.trade_ids),
                    observed_at_ms=self.observed_at_ms, coverage_from_ms=self.coverage_from_ms, source=self.source, product='SWAP')

    @classmethod
    def from_json(cls, value):
        expected_fields = {
            'operation_id', 'prepare_operation_id', 'exchange_order_id', 'terminal_state',
            'cumulative_base_life', 'trade_ids', 'observed_at_ms', 'coverage_from_ms', 'source', 'product',
        }
        try:
            if (type(value) is not dict or set(value) != expected_fields
                    or value['product'] != 'SWAP' or type(value['cumulative_base_life']) is not str or type(value['trade_ids']) is not list):
                raise StorageError('PG_SWAP_TERMINAL_COMMAND_INVALID')
            return cls(**{**{k: v for k, v in value.items() if k != 'product'}, 'cumulative_base_life': Decimal(value['cumulative_base_life']),
                          'trade_ids': tuple(value['trade_ids'])})
        except (ArithmeticError, TypeError, KeyError, ValueError):
            raise StorageError('PG_SWAP_TERMINAL_COMMAND_INVALID') from None


@dataclass(frozen=True)
class SwapTerminalReceipt:
    binding: StateBinding
    operation_id: str
    intent_id: str
    state: str
    replayed: bool
    claim_released: bool = field(default=False, init=False)
    atomic_scope: AtomicScope = field(default=AtomicScope.ACCOUNT, init=False)
    trading_permission: bool = field(default=False, init=False)


class PgSwapTerminalRepository:
    atomic_scope = AtomicScope.ACCOUNT

    def __init__(self, swap):
        if type(swap) is not PgSwapRepository:
            raise StorageError('PG_SWAP_TERMINAL_CONTEXT_INVALID')
        self.swap, self.financial, self.runtime = swap, swap.financial, swap.runtime
        self.binding, self.owner = swap.binding, swap.owner

    def _original(self):
        self.swap._original()
        if (self.swap.financial is not self.financial or self.swap.runtime is not self.runtime
                or self.swap.binding != self.binding or self.swap.owner != self.owner):
            raise StorageError('PG_SWAP_TERMINAL_CONTEXT_CHANGED')

    def _receipt(self, value):
        self._original()
        if (value.get('kind') != 'SWAP_TERMINAL' or value.get('trading_permission') is not False
                or value.get('claim_released') is not False or value.get('state') not in ('TERMINAL', 'RECOVERY_REQUIRED')
                or type(value.get('replayed')) is not bool):
            raise StorageError('PG_SWAP_TERMINAL_RECEIPT_INVALID')
        return SwapTerminalReceipt(self.binding, identifier(value['operation_id']), identifier(value['intent_id']), value['state'], value['replayed'])

    async def _read(self, operation_id, binding):
        self._original()
        self.financial._binding(binding)
        return await self.swap.capital._read('SELECT life_financial.swap_terminal_read($1,$2,$3,$4)',
                                             (self.runtime.config.account_id, self.swap.capital.max_rows, self.swap.capital.max_bytes, operation_id))

    async def frame(self, binding):
        return await self._read(None, binding)

    async def lookup(self, operation_id, binding):
        identifier(operation_id)
        value = await self._read(operation_id, binding)
        return None if value is None else self._receipt({**value['receipt'], 'replayed': True})

    async def confirm(self, command, *, cut, owner, _source_fence=None):
        self._original()
        if type(command) is not ConfirmSwapTerminal or owner != self.owner:
            raise StorageError('PG_SWAP_TERMINAL_CONTEXT_INVALID')
        version, digest = self.financial._cut(cut, owner)
        body = command.json()
        request = dict(kind='SWAP_TERMINAL', binding=self.binding.json(), owner=owner.json(), version=version, cut=digest, command=body)
        self.runtime._ingress_epoch += 1
        try:
            return await self.runtime._transaction('SELECT life_financial.swap_terminal_command($1,$2,$3,$4,$5,$6,$7)',
                                                   (self.runtime.config.account_id, self.binding.json(), owner.json(), version, digest, fingerprint(request), body),
                                                   owner, self._receipt, observation=True, source_fence=_source_fence)
        except StorageError as error:
            if str(error) in ('PG_SWAP_TERMINAL_OBSERVATION_BOUND', 'PG_SWAP_TERMINAL_SOURCE_STALE_AT_COMMIT'):
                self.runtime._healthy = False
            raise
