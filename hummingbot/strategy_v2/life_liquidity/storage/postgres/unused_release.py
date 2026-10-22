"""Unused preallocation recovery; no terminal, refund, activation or sender."""
from dataclasses import dataclass, field
from decimal import Decimal as D

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    AtomicScope,
    StateBinding,
    StorageError,
    fingerprint,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_release import PgSharedReleaseRepository
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts

QUERY = 'SELECT life_financial.unused_release_command($1,$2,$3,$4,$5,$6,$7)'


def recovery_facts(collector, report, *, epoch=None):
    """Only the original collector's complete, compared signed report is used."""
    collector.check_report(report, epoch=collector._epoch if epoch is None else epoch)
    if report.runner_executor_ids:
        raise StorageError('PG_UNUSED_RELEASE_RUNNER_NOT_QUIESCENT')
    return SpotFinancialRecoveryFacts(
        collector.binding, report.observed_at, report.account.base_life, report.account.cash_usdt, D('0'), D('0'),
        tuple(sorted({trade for _, trade in report.product_trade_ids})), report.cashflow_ids, (), (), True, True, True).json()


@dataclass(frozen=True)
class UnusedReleaseReceipt:
    binding: StateBinding
    operation_id: str
    intent_id: str
    ticket_id: str
    replayed: bool
    released: bool = field(default=True, init=False)
    atomic_scope: AtomicScope = field(default=AtomicScope.ACCOUNT, init=False)
    trading_permission: bool = field(default=False, init=False)


class PgUnusedReleaseRepository:
    def __init__(self, capital):
        self.original = PgSharedReleaseRepository(capital)
        self._context = self.original

    def _original(self):
        if self.original is not self._context:
            raise StorageError('PG_UNUSED_RELEASE_CONTEXT_CHANGED')
        self.original._original()

    def _receipt(self, value):
        self._original()
        if (value.get('kind') != 'SHARED_UNUSED_RELEASE' or value.get('released') is not True
                or value.get('trading_permission') is not False or type(value.get('replayed')) is not bool):
            raise StorageError('PG_UNUSED_RELEASE_RECEIPT_INVALID')
        return UnusedReleaseReceipt(self.original.binding, identifier(value['operation_id']), identifier(value['intent_id']),
                                    identifier(value['ticket_id']), value['replayed'])

    async def read(self, operation_id):
        self._original()
        identifier(operation_id)
        capital = self.original.capital
        return await capital._read('SELECT life_financial.unused_release_read($1,$2,$3,$4)',
                                   (capital.runtime.config.account_id, operation_id, capital.max_rows, capital.max_bytes))

    async def release(self, command, *, report, fence):
        self._original()
        repo = self.original
        version, digest = repo.financial._cut(report.cut, repo.owner)
        request = dict(kind='SHARED_UNUSED_RELEASE', binding=repo.binding.json(), owner=repo.owner.json(),
                       version=version, cut=digest, command=command)
        repo.runtime._ingress_epoch += 1
        return await repo.runtime._transaction(
            QUERY, (repo.runtime.config.account_id, repo.binding.json(), repo.owner.json(),
                    version, digest, fingerprint(request), command),
            repo.owner, self._receipt, observation=True, source_fence=fence)
