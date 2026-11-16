"""A failed real financial transaction cannot publish stale action readiness."""
import asyncio
import logging
from test.integration.postgres.test_held_checkpoint_batch import group
from test.integration.postgres.test_ownership import owned as owned_fixture
from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import event, text

from hummingbot.model.executors import Executors
from hummingbot.model.order import Order
from hummingbot.model.position import Position
from hummingbot.model.runtime_journal import RuntimeCheckpoint, RuntimeJournal, financial_hash
from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator
from hummingbot.strategy_v2.models.executor_actions import StopExecutorAction
from hummingbot.strategy_v2.models.executors_info import PerformanceReport

owned = owned_fixture


@pytest.mark.parametrize('failure_at', ['after_flush_postexec', 'before_commit'])
def test_real_checkpoint_failure_keeps_report_readiness_closed_and_financial_rows_rolled_back(owned, failure_at):
    infos, _ = group(owned, count=1)
    controller = SimpleNamespace(config=SimpleNamespace(id='maker'), executors_update_event=asyncio.Event(),
                                 executors_info=[], positions_held=[], performance_report=None)
    strategy = SimpleNamespace(
        controllers={'maker': controller}, config=SimpleNamespace(mm_drawdown_scope=None),
        markets={'okx': {'BTC-USDT'}}, current_timestamp=1700000002, market_data_provider=MagicMock(),
        _managed_report_handle=None, _managed_report_pending={}, controller_reports_error=None,
        controller_reports={'maker': dict(executors=[], positions=[], performance=PerformanceReport())},
        actions_queue=asyncio.Queue(), logger=lambda: logging.getLogger(__name__))
    strategy.market_data_provider.time.return_value = 1700000002
    strategy.market_data_provider.get_price_by_type.return_value = 100
    for name in ('update_executors_info', 'get_executors_by_controller', 'listen_to_executor_actions'):
        setattr(strategy, name, MethodType(getattr(StrategyV2Base, name), strategy))
    orchestrator = strategy.executor_orchestrator = ExecutorOrchestrator(strategy)
    resident = SimpleNamespace(config=infos[0].config, executor_info=infos[0], is_closed=True)
    orchestrator.active_executors['maker'] = [resident]
    orchestrator.positions_held['maker'] = []
    orchestrator.cached_performance['maker'] = PerformanceReport()
    entered = asyncio.Event()

    def fail_transaction(session, *args):
        if any(isinstance(row, Position) for row in session.identity_map.values()):
            entered.set()
            session.execute(text('SELECT 1/0'))  # Actual PostgreSQL error after native financial work.

    async def run():
        controller.executors_update_event.clear()
        listener = asyncio.create_task(strategy.listen_to_executor_actions())
        try:
            await strategy.actions_queue.put([StopExecutorAction(
                controller_id='maker', executor_id=infos[0].id, keep_position=True)])
            await asyncio.wait_for(entered.wait(), 2)
            assert strategy.controller_reports_error is not None
            assert not controller.executors_update_event.is_set()
            assert not owned.manager.persistence_healthy
            assert not orchestrator.positions_held['maker']
            assert infos[0].id not in orchestrator.executors_ids_position_held
            assert not listener.done()
            strategy.update_executors_info()
            assert not controller.executors_update_event.is_set()  # A failed writer needs real recovery, not a latch reset.
        finally:
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)

    event.listen(owned.manager._session_cls, failure_at, fail_transaction)
    try:
        owned.loop.run_until_complete(run())
    finally:
        event.remove(owned.manager._session_cls, failure_at, fail_transaction)
    with owned.manager.get_new_session() as db:
        assert db.query(Position).count() == db.query(Executors).count() == 0
        assert db.query(Order).count() == 1 and not db.query(Order).filter_by(position_applied=True).count()
        assert db.query(RuntimeJournal).filter_by(kind='fill', applied=False).count() == 1
        assert not db.query(RuntimeJournal).filter(RuntimeJournal.kind.in_(('hold', 'applied_order'))).count()
        checkpoint = db.get(RuntimeCheckpoint, 'test.yml')
        assert checkpoint.financial_hash == financial_hash(db)
