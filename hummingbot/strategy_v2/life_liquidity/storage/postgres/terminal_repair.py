"""Signed reconciliation of retained SWAP terminal observations.

This resolves diagnostics only. Native terminal confirmation, claim retirement
and activation remain separate guarded operations. No exchange authentication
is implied by a caller's complete-source assertions.
"""
from dataclasses import dataclass, field

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    AtomicScope,
    StateBinding,
    StorageError,
    fingerprint,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_terminal import (
    ConfirmSwapTerminal,
    PgSwapTerminalRepository,
)


@dataclass(frozen=True)
class ResolveSwapTerminals:
    operation_id: str
    pending_operation_id: str
    terminals: tuple[ConfirmSwapTerminal, ...]
    additional_pending_operation_ids: tuple[str, ...] = ()

    def __post_init__(self):
        if (type(self.terminals) is not tuple or not 0 < len(self.terminals) <= 256
                or any(type(t) is not ConfirmSwapTerminal for t in self.terminals)
                or type(self.additional_pending_operation_ids) is not tuple or len(self.additional_pending_operation_ids) > 255):
            raise StorageError('PG_TERMINAL_REPAIR_COMMAND_INVALID')
        ids = (self.operation_id, self.pending_operation_id, *self.additional_pending_operation_ids, *(t.operation_id for t in self.terminals))
        for value in ids:
            identifier(value)
            if len(value) > 96:
                raise StorageError('PG_TERMINAL_REPAIR_COMMAND_INVALID')
        if len(ids) != len(set(ids)) or len({t.prepare_operation_id for t in self.terminals}) != len(self.terminals):
            raise StorageError('PG_TERMINAL_REPAIR_ID_CONFLICT')
        object.__setattr__(self, 'additional_pending_operation_ids', tuple(sorted(self.additional_pending_operation_ids)))
        object.__setattr__(self, 'terminals', tuple(sorted(self.terminals, key=lambda t: t.prepare_operation_id)))

    def json(self):
        return dict(operation_id=self.operation_id, pending_operation_id=self.pending_operation_id,
                    additional_pending_operation_ids=list(self.additional_pending_operation_ids), terminals=[t.json() for t in self.terminals])

    @classmethod
    def from_json(cls, value):
        if (type(value) is not dict or set(value) != {'operation_id', 'pending_operation_id', 'additional_pending_operation_ids', 'terminals'}
                or type(value['additional_pending_operation_ids']) is not list or type(value['terminals']) is not list):
            raise StorageError('PG_TERMINAL_REPAIR_COMMAND_INVALID')
        return cls(value['operation_id'], value['pending_operation_id'], tuple(ConfirmSwapTerminal.from_json(v) for v in value['terminals']),
                   tuple(value['additional_pending_operation_ids']))


@dataclass(frozen=True)
class TerminalRepairReceipt:
    binding: StateBinding
    operation_id: str
    pending_operation_id: str
    additional_pending_operation_ids: tuple[str, ...]
    replayed: bool
    state: str = field(default='RECONCILED', init=False)
    claim_released: bool = field(default=False, init=False)
    atomic_scope: AtomicScope = field(default=AtomicScope.ACCOUNT, init=False)
    trading_permission: bool = field(default=False, init=False)


class PgTerminalRepairRepository:
    atomic_scope = AtomicScope.ACCOUNT

    def __init__(self, terminals):
        if type(terminals) is not PgSwapTerminalRepository:
            raise StorageError('PG_TERMINAL_REPAIR_CONTEXT_INVALID')
        self.terminals, self.financial, self.runtime = terminals, terminals.financial, terminals.runtime
        self.binding, self.owner = terminals.binding, terminals.owner

    def _original(self):
        self.terminals._original()
        if (self.terminals.financial is not self.financial or self.terminals.runtime is not self.runtime
                or self.terminals.binding != self.binding or self.terminals.owner != self.owner):
            raise StorageError('PG_TERMINAL_REPAIR_CONTEXT_CHANGED')

    def _receipt(self, value):
        self._original()
        if (value.get('kind') != 'TERMINAL_REPAIR' or value.get('state') != 'RECONCILED'
                or value.get('trading_permission') is not False or value.get('claim_released') is not False
                or type(value.get('replayed')) is not bool):
            raise StorageError('PG_TERMINAL_REPAIR_RECEIPT_INVALID')
        additional = value.get('additional_pending_operation_ids')
        if (type(additional) is not list or len(additional) > 255 or any(type(v) is not str for v in additional)
                or len(set(additional)) != len(additional)):
            raise StorageError('PG_TERMINAL_REPAIR_RECEIPT_INVALID')
        return TerminalRepairReceipt(self.binding, identifier(value['operation_id']), identifier(value['pending_operation_id']),
                                     tuple(identifier(v) for v in additional), value['replayed'])

    async def _read(self, operation_id, binding):
        self._original()
        self.financial._binding(binding)
        capital = self.terminals.swap.capital
        return await capital._read('SELECT life_financial.terminal_repair_read($1,$2,$3,$4)',
                                   (self.runtime.config.account_id, capital.max_rows, capital.max_bytes, operation_id))

    async def frame(self, binding):
        return await self._read(None, binding)

    async def lookup(self, operation_id, binding):
        identifier(operation_id)
        result = await self._read(operation_id, binding)
        return None if result is None else self._receipt({**result['receipt'], 'replayed': True})

    async def resolve(self, command, *, cut, owner):
        self._original()
        if type(command) is not ResolveSwapTerminals or owner != self.owner:
            raise StorageError('PG_TERMINAL_REPAIR_CONTEXT_INVALID')
        version, digest = self.financial._cut(cut, owner)
        body = command.json()
        request = dict(kind='TERMINAL_REPAIR', binding=self.binding.json(), owner=owner.json(), version=version, cut=digest, command=body)
        self.runtime._ingress_epoch += 1
        try:
            return await self.runtime._transaction('SELECT life_financial.terminal_repair_command($1,$2,$3,$4,$5,$6,$7)',
                                                   (self.runtime.config.account_id, self.binding.json(), owner.json(), version, digest, fingerprint(request), body),
                                                   owner, self._receipt, observation=True)
        except StorageError as error:
            if str(error) in ('PG_TERMINAL_REPAIR_BOUND', 'PG_TERMINAL_REPAIR_SOURCE_STALE_AT_COMMIT'):
                self.runtime._healthy = False
            raise
