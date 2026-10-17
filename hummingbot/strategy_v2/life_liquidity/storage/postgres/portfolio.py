"""Portfolio measurements bind durable native money and qualified source cuts.

No wallet/collateral credit, terminal proof, release or dispatch permission.
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
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository


@dataclass(frozen=True)
class MeasurePortfolio:
    operation_id: str

    def __post_init__(self):
        identifier(self.operation_id)

    def json(self):
        return dict(operation_id=self.operation_id)

    @classmethod
    def from_json(cls, value):
        if type(value) is not dict or set(value) != {'operation_id'}:
            raise StorageError('PG_PORTFOLIO_COMMAND_INVALID')
        return cls(value['operation_id'])


@dataclass(frozen=True)
class PortfolioReceipt:
    binding: StateBinding
    operation_id: str
    measurement: dict
    halted: bool
    replayed: bool
    atomic_scope: AtomicScope = field(default=AtomicScope.ACCOUNT, init=False)
    trading_permission: bool = field(default=False, init=False)


class PgPortfolioRepository:
    atomic_scope = AtomicScope.ACCOUNT

    def __init__(self, swap):
        if type(swap) is not PgSwapRepository:
            raise StorageError('PG_PORTFOLIO_CONTEXT_INVALID')
        self.swap, self.financial, self.runtime = swap, swap.financial, swap.runtime
        self.binding, self.owner = swap.binding, swap.owner

    def _original(self):
        self.swap._original()
        if (self.swap.financial is not self.financial or self.swap.runtime is not self.runtime
                or self.swap.binding != self.binding or self.swap.owner != self.owner):
            raise StorageError('PG_PORTFOLIO_CONTEXT_CHANGED')

    def _receipt(self, value):
        self._original()
        fields = {'nav_quote', 'adjusted_nav_quote', 'highwater_quote', 'drawdown_bps', 'unrealized_quote', 'external_flows_quote'}
        raw = value.get('measurement', {})
        if (value.get('kind') != 'PORTFOLIO_NAV' or value.get('trading_permission') is not False
                or type(value.get('halted')) is not bool or type(value.get('replayed')) is not bool
                or type(raw) is not dict or set(raw) != fields | {'trading_permission'} or raw.get('trading_permission') is not False):
            raise StorageError('PG_PORTFOLIO_RECEIPT_INVALID')
        parsed = {}
        for name in fields:
            if type(raw[name]) is not str:
                raise StorageError('PG_PORTFOLIO_RECEIPT_INVALID')
            try:
                parsed[name] = Decimal(raw[name])
            except ArithmeticError:
                raise StorageError('PG_PORTFOLIO_RECEIPT_INVALID') from None
            if not parsed[name].is_finite() or parsed[name].copy_abs() >= Decimal('1e200'):
                raise StorageError('PG_PORTFOLIO_RECEIPT_INVALID')
        if parsed['highwater_quote'] <= 0 or parsed['drawdown_bps'] < 0:
            raise StorageError('PG_PORTFOLIO_RECEIPT_INVALID')
        return PortfolioReceipt(self.binding, identifier(value['operation_id']), parsed, value['halted'], value['replayed'])

    async def _read(self, operation_id, binding):
        self._original()
        self.financial._binding(binding)
        return await self.swap.capital._read('SELECT life_financial.portfolio_read($1,$2,$3,$4)',
                                             (self.runtime.config.account_id, self.swap.capital.max_rows, self.swap.capital.max_bytes, operation_id))

    async def frame(self, binding):
        return await self._read(None, binding)

    async def lookup(self, operation_id, binding):
        identifier(operation_id)
        value = await self._read(operation_id, binding)
        return None if value is None else self._receipt({**value['receipt'], 'replayed': True})

    async def measure(self, command, *, cut, owner):
        self._original()
        if type(command) is not MeasurePortfolio or owner != self.owner:
            raise StorageError('PG_PORTFOLIO_CONTEXT_INVALID')
        version, digest = self.financial._cut(cut, owner)
        body = command.json()
        request = dict(kind='PORTFOLIO_NAV', binding=self.binding.json(), owner=owner.json(), version=version, cut=digest, command=body)
        self.runtime._ingress_epoch += 1
        try:
            return await self.runtime._transaction('SELECT life_financial.portfolio_command($1,$2,$3,$4,$5,$6,$7)',
                                                   (self.runtime.config.account_id, self.binding.json(), owner.json(), version, digest, fingerprint(request), body),
                                                   owner, self._receipt, observation=True)
        except StorageError as error:
            if str(error) in ('PG_PORTFOLIO_SOURCE_UNQUALIFIED', 'PG_PORTFOLIO_COLLATERAL_UNQUALIFIED',
                              'PG_PORTFOLIO_OBSERVATION_BOUND', 'PG_PORTFOLIO_SOURCE_STALE_AT_COMMIT'):
                self.runtime._healthy = False
            raise
