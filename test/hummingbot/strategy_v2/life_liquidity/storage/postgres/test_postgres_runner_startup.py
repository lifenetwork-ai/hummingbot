"""Actual V2 startup over signed PG event/history, without File recorder fallback.

This explicit SPOT fixture is not the configured LIFE service or a send route.
"""
import asyncio
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_event_application import close, setup

import pytest

from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.data_feed.market_data_provider import MarketDataProvider
from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator
from hummingbot.strategy_v2.life_liquidity.storage.postgres.event_application import PgSpotEventApplication


def inputs(s):
    s.controller.actions_queue = asyncio.Queue()
    s.controller.market_data_provider = MarketDataProvider({'okx': s.t.connector})
    return dict(repository_reporting={'controller1': s.reporting}, dispatch_max_pending=2, dispatch_shutdown_timeout_s=1)


async def stop(runner):
    if runner is not None:
        task = runner.listen_to_executor_actions_task
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        dispatch = runner._async_action_dispatcher
        if dispatch is not None:
            await dispatch.close()


@pytest.mark.asyncio
async def test_actual_strategy_starts_only_after_signed_event_history_without_legacy_recorder(database, monkeypatch):
    s = await setup(database)
    runner = None
    try:
        kwargs = inputs(s)
        monkeypatch.setattr(MarketsRecorder, 'get_instance', lambda: (_ for _ in ()).throw(AssertionError('legacy recorder used')))
        await PgSpotEventApplication.install(s.reporting, s.observer)
        before = await s.financial.balance(s.cfg.binding)
        posts = len(s.t.session.sent)
        runner = StrategyV2Base({'okx': s.t.connector}, **kwargs)
        assert type(runner.executor_orchestrator) is ExecutorOrchestrator
        assert runner.controllers == {'controller1': s.controller}
        assert runner.actions_queue is s.controller.actions_queue
        assert runner.market_data_provider is s.controller.market_data_provider
        assert runner.markets == {'okx': {'LIFE-USDT'}}
        assert runner.executor_orchestrator.active_executors == {'controller1': []}
        assert runner.ready_to_trade is False
        # Reporting startup is observation-only. A direct orchestrator call
        # cannot bypass the still-missing native final trading assembly.
        assert await s.reporting.before_dispatch(s.t.create) is False
        with pytest.raises(ValueError, match='PG_RUNNER_TRADING_ASSEMBLY_REQUIRED'):
            runner.executor_orchestrator.create_executor(s.t.create)
        foreign = s.t.create.model_copy(deep=True, update={'controller_id': 'foreign'})
        with pytest.raises(ValueError, match='RECORDER_RUNNER_SCOPE_MISMATCH'):
            runner.executor_orchestrator.create_executor(foreign)
        with pytest.raises(ValueError, match='REPOSITORY_RUNNER_STARTUP_INVALID'):
            StrategyV2Base({'okx': s.t.connector}, **kwargs)
        assert (await s.financial.balance(s.cfg.binding)) == before
        assert len(s.t.session.sent) == posts
    finally:
        await stop(runner)
        await close(s)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['missing_history', 'listener_removed', 'connector', 'extra_connector', 'closed', 'credentials'])
async def test_unqualified_pg_runner_startup_creates_no_tasks_or_orders(database, fault, monkeypatch):
    s = await setup(database)
    try:
        kwargs = inputs(s)
        connectors = {'okx': s.t.connector}
        if fault != 'missing_history':
            events = await PgSpotEventApplication.install(s.reporting, s.observer)
        if fault == 'listener_removed':
            s.t.connector.remove_listener(events.TAGS[0], events._forwarder)
        elif fault == 'connector':
            connectors['okx'] = object()
        elif fault == 'extra_connector':
            connectors['unqualified'] = object()
        elif fault == 'closed':
            await events.close()
        elif fault == 'credentials':
            s.t.connector.okx_api_key = 'changed-synthetic-key'
        posts, tasks = len(s.t.session.sent), set(asyncio.all_tasks())
        with pytest.raises(ValueError, match='REPOSITORY_RUNNER_STARTUP_INVALID'):
            StrategyV2Base(connectors, **kwargs)
        assert not (set(asyncio.all_tasks()) - tasks)
        assert len(s.t.session.sent) == posts
    finally:
        await close(s)
