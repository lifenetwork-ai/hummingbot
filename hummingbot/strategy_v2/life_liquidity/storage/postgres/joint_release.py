"""Explicit authenticated joint terminal/release application.

Uses fresh full provider evidence for each effect. Existing low-level financial
repositories remain bookkeeping kernels; configured backend installation and
runner topology qualification retain their separate acceptance gates.
"""
import asyncio
import json
from decimal import Decimal as D

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, identifier
from hummingbot.strategy_v2.life_liquidity.storage.financial import SpotFillObservation
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_source import PgJointAccountReconciler
from hummingbot.strategy_v2.life_liquidity.storage.postgres.portfolio import PgPortfolioRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_release import (
    PgSharedReleaseRepository,
    ReleaseSharedClaim,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.source_fence import JointSourceFence
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_terminal import (
    ConfirmSwapTerminal,
    PgSwapTerminalRepository,
)
from hummingbot.strategy_v2.life_liquidity.storage.terminal import SpotTerminalFacts, TerminalSpot


class PgJointReleaseApplication:
    def __init__(self, collector):
        if type(collector) is not PgJointAccountReconciler:
            raise StorageError('PG_JOINT_RELEASE_CONTEXT_INVALID')
        self.collector = collector
        self.financial, self.runtime = collector.financial, collector.runtime
        self.binding, self.owner = collector.binding, collector.swap.owner
        self.swap = PgSwapTerminalRepository(collector.swap)
        self.release = PgSharedReleaseRepository(collector.swap.capital)
        self.portfolio = PgPortfolioRepository(collector.swap)
        self._lock = asyncio.Lock()
        self._context = (collector, self.financial, self.runtime, self.binding, self.owner, self.swap, self.release, self.portfolio)

    def _original(self):
        if self._context != (self.collector, self.financial, self.runtime, self.binding, self.owner, self.swap, self.release, self.portfolio):
            raise StorageError('PG_JOINT_RELEASE_CONTEXT_CHANGED')
        self.collector._original()
        if self.collector.financial is not self.financial or self.collector.runtime is not self.runtime or self.collector.binding != self.binding:
            raise StorageError('PG_JOINT_RELEASE_CONTEXT_CHANGED')

    def _order(self, report, intent_id):
        for evidence in report.orders:
            native = json.loads(evidence.native_json)
            if native['intent_id'] == intent_id:
                if evidence.exchange_json is None or json.loads(evidence.exchange_json)['state'] not in ('filled', 'canceled'):
                    raise StorageError('PG_JOINT_RELEASE_TERMINAL_UNQUALIFIED')
                return native, json.loads(evidence.exchange_json), evidence
        raise StorageError('PG_JOINT_RELEASE_ORDER_UNKNOWN')

    async def _existing(self, operation_id, intent_id, terminal_operation_id):
        if terminal_operation_id is not None:
            original = await self.release._read(operation_id, self.binding)
            if original is None:
                return None
            if original['command']['command'] != ReleaseSharedClaim(operation_id, intent_id, terminal_operation_id).json():
                raise StorageError('PG_OPERATION_CONFLICT')
            return self.release._receipt({**original['receipt'], 'replayed': True})
        for read in (self.financial.lookup_terminal, self.swap.lookup):
            receipt = await read(operation_id, self.binding)
            if receipt is not None:
                if receipt.intent_id != intent_id:
                    raise StorageError('PG_OPERATION_CONFLICT')
                return receipt
        return None

    async def confirm_terminal(self, operation_id, intent_id):
        return await self._run(operation_id, intent_id, terminal_operation_id=None)

    async def retire_claim(self, operation_id, intent_id, terminal_operation_id):
        identifier(terminal_operation_id)
        return await self._run(operation_id, intent_id, terminal_operation_id=terminal_operation_id)

    async def _run(self, operation_id, intent_id, *, terminal_operation_id):
        identifier(operation_id)
        identifier(intent_id)
        self._original()
        if self._lock.locked():
            raise StorageError('PG_JOINT_RELEASE_BACKPRESSURE')
        async with self._lock:
            # A receipt read has no new release effect and recreates no source
            # authority. Unknown COMMIT uses the exact original operation ID.
            existing = await self._existing(operation_id, intent_id, terminal_operation_id)
            if existing is not None:
                return existing
            self.runtime._observation_inflight += 1
            report = None
            try:
                report = await self.collector.collect()
                self._original()
                native, exchange, evidence = self._order(report, intent_id)
                fence = JointSourceFence(self.collector, report, operation_id=operation_id, intent_id=intent_id)
                if terminal_operation_id is not None:
                    return await self.release.release(ReleaseSharedClaim(operation_id, intent_id, terminal_operation_id),
                                                      cut=report.cut, owner=self.owner, _source_fence=fence)
                if native['product'] == 'SWAP':
                    nav = await self.portfolio.frame(self.binding)
                    if nav.get('current') is not True:
                        raise StorageError('PG_JOINT_RELEASE_NAV_REQUIRED')
                    # Preserve the independently qualified original NAV time;
                    # current signed collection time must never re-date it.
                    observed = nav['basis']['observed_at_ms']
                    command = ConfirmSwapTerminal(operation_id, native['prepare_operation_id'], exchange['ordId'], exchange['state'],
                                                  sum((D(v['quantity_base_life']) for v in json.loads(evidence.fills_json)), D('0')),
                                                  evidence.trade_ids, observed, report.coverage_from_ms, 'okx_joint_reconciled')
                    return await self.swap.confirm(command, cut=report.cut, owner=self.owner, _source_fence=fence)
                fills = tuple(SpotFillObservation.from_json(v) for v in json.loads(evidence.fills_json))
                pending = [json.loads(v.exchange_json) for v in report.orders if v.exchange_json is not None
                           and json.loads(v.native_json)['product'] == 'SPOT' and json.loads(v.exchange_json)['state'] in ('live', 'partially_filled')]
                facts = SpotTerminalFacts(self.binding, report.observed_at, exchange['state'].upper(),
                                          sum((v.quantity_base_life for v in fills), D('0')), D(native['quantity']), D(native['price']), fills,
                                          report.account.base_life, report.account.cash_usdt, D('0'), D('0'),
                                          tuple(sorted({v for _, v in report.product_trade_ids})), report.cashflow_ids,
                                          tuple(v['clOrdId'] for v in pending), tuple(v['ordId'] for v in pending), (), True, True, True)
                command = TerminalSpot(operation_id, native['prepare_operation_id'], intent_id, native['wire_id'], 'LIFE-USDT',
                                       native['side'], native['session_id'], native['epoch'], exchange['ordId'], facts)
                return await self.financial.confirm_terminal(command, cut=report.cut, owner=self.owner, _source_fence=fence)
            except BaseException:
                self.runtime._healthy = False
                raise
            finally:
                if report is not None and self.collector._report is report:
                    self.collector._report = None
                self.runtime._observation_inflight -= 1
