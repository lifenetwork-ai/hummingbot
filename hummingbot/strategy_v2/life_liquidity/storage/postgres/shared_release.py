"""Permanent original-claim retirement, never wallet refund or send permission."""
from dataclasses import dataclass, field

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    AtomicScope,
    StateBinding,
    StorageError,
    fingerprint,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository


@dataclass(frozen=True)
class ReleaseSharedClaim:
    operation_id: str
    intent_id: str
    terminal_operation_id: str

    def __post_init__(self):
        for value in vars(self).values():
            identifier(value)

    def json(self):
        return dict(vars(self))

    @classmethod
    def from_json(cls, value):
        if type(value) is not dict or set(value) != {'operation_id', 'intent_id', 'terminal_operation_id'}:
            raise StorageError('PG_SHARED_RELEASE_COMMAND_INVALID')
        return cls(**value)


@dataclass(frozen=True)
class SharedReleaseReceipt:
    binding: StateBinding
    operation_id: str
    intent_id: str
    replayed: bool
    released: bool = field(default=True, init=False)
    atomic_scope: AtomicScope = field(default=AtomicScope.ACCOUNT, init=False)
    trading_permission: bool = field(default=False, init=False)


class PgSharedReleaseRepository:
    atomic_scope = AtomicScope.ACCOUNT

    def __init__(self, capital):
        if type(capital) is not PgSharedCapitalRepository:
            raise StorageError('PG_SHARED_RELEASE_CONTEXT_INVALID')
        self.capital, self.financial, self.runtime = capital, capital.financial, capital.runtime
        self.binding, self.owner = capital.binding, capital.owner

    def _original(self):
        self.capital._original()
        if (self.capital.financial is not self.financial or self.capital.runtime is not self.runtime
                or self.capital.binding != self.binding or self.capital.owner != self.owner):
            raise StorageError('PG_SHARED_RELEASE_CONTEXT_CHANGED')

    def _receipt(self, value):
        self._original()
        if (value.get('kind') != 'SHARED_RELEASE' or value.get('released') is not True
                or value.get('trading_permission') is not False or type(value.get('replayed')) is not bool):
            raise StorageError('PG_SHARED_RELEASE_RECEIPT_INVALID')
        return SharedReleaseReceipt(self.binding, identifier(value['operation_id']), identifier(value['intent_id']), value['replayed'])

    async def _read(self, operation_id, binding):
        self._original()
        self.financial._binding(binding)
        return await self.capital._read('SELECT life_financial.shared_release_read($1,$2,$3,$4)',
                                        (self.runtime.config.account_id, self.capital.max_rows, self.capital.max_bytes, operation_id))

    async def frame(self, binding):
        return await self._read(None, binding)

    async def lookup(self, operation_id, binding):
        identifier(operation_id)
        value = await self._read(operation_id, binding)
        return None if value is None else self._receipt({**value['receipt'], 'replayed': True})

    async def release(self, command, *, cut, owner):
        self._original()
        if type(command) is not ReleaseSharedClaim or owner != self.owner:
            raise StorageError('PG_SHARED_RELEASE_CONTEXT_INVALID')
        version, digest = self.financial._cut(cut, owner)
        body = command.json()
        request = dict(kind='SHARED_RELEASE', binding=self.binding.json(), owner=owner.json(), version=version, cut=digest, command=body)
        self.runtime._ingress_epoch += 1
        try:
            return await self.runtime._transaction('SELECT life_financial.shared_release_command($1,$2,$3,$4,$5,$6,$7)',
                                                   (self.runtime.config.account_id, self.binding.json(), owner.json(), version, digest, fingerprint(request), body),
                                                   owner, self._receipt, observation=True)
        except StorageError as error:
            if str(error) in ('PG_SHARED_RELEASE_BOUND', 'PG_SHARED_RELEASE_SOURCE_UNQUALIFIED'):
                self.runtime._healthy = False
            raise
