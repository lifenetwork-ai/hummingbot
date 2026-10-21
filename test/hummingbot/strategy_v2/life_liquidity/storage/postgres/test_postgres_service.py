"""Configured native lifecycle, real V2/SDKs/PG, synthetic account and HTTP."""
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_joint_history import setup

import pytest

from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.data_feed.market_data_provider import MarketDataProvider
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.service import PgLiquidityService, PgServiceLimits


def limits():
    return PgServiceLimits(max_rows=64, max_bytes=4194304, max_reads=128, max_pages=2, max_actions=64,
                           max_pending=4, max_callbacks=16, max_uid_age_ms=1000, max_auth_age_ms=1000,
                           max_clock_skew_ms=100, source_max_age_ms=1000, request_timeout_ms=1000,
                           collection_timeout_ms=4000, cycle_timeout_ms=5000, shutdown_timeout_ms=5000,
                           max_cancels=2, cancel_retry_ms=100, max_levels=64)


async def open_service(s, **changes):
    connectors = {'okx': s.t.connector, 'okx_perpetual': s.t.swap_connector}
    values = dict(runtime_config=s.t.cfg, owner=s.t.owner, connectors=connectors,
                  market_data_provider=MarketDataProvider(connectors), sources=s.registered,
                  limits=limits(), clock=s.t.journal.clock,
                  independent_fill_value=s.intake.fill_value, independent_cashflow_value=s.intake.cashflow_value)
    values.update(changes)
    return await PgLiquidityService.open(s.controller.config, **values)


@pytest.mark.asyncio
async def test_native_service_constructs_both_history_routes_and_actual_runner_without_file_fallback(database, monkeypatch):
    s = await setup(database, apply_finance=False)
    service = None
    try:
        await s.reporting.close()
        await s.t.financial.close()
        monkeypatch.setattr(MarketsRecorder, 'get_instance', lambda: (_ for _ in ()).throw(AssertionError('legacy recorder used')))
        service = await open_service(s)
        assert service.runner.controllers == {'main': service.controller}
        from hummingbot.strategy_v2.controllers.controller_base import ControllerBase
        assert isinstance(service.controller, ControllerBase)
        assert service.controller._runner_orchestrator is service.runner.executor_orchestrator
        assert service.events.connectors == (s.t.connector, s.t.swap_connector)
        assert not service.runner.ready_to_trade
        assert service.controller.determine_executor_actions() == []
        assert not service.controller.local_runner_create_fence(None)
        assert (await service.controller.runner_facts()).scope_complete
        assert (await service.financial.balance(s.t.cfg.binding)).total_quote_usdt == s.t.command.funding.bundle.account.snapshot.spot_cash_quote
        assert service.events._started and not service.events._failed
        # Observation readiness must not authorize even a direct native hedge
        # adapter call. Check its original local fence without any pause/cancel
        # mutation or HTTP; kernel writes cannot turn this profile into trading.
        assert service.hedge._fence() is False
        assert not s.t.session.sent
        from hummingbot.core.clock import Clock, ClockMode
        with Clock(ClockMode.BACKTEST, tick_size=.1, start_time=service.clock().timestamp()) as clock:
            clock.add_iterator(service.runner)
            clock.backtest_til(service.clock().timestamp() + .3)
        assert service._revoked and service.runner._is_stop_triggered
        # Repeated close must join the same cleanup and retain permanent claims.
        await service.close()
        await service.close()
        assert service._close_task.done() and not service.runtime._tasks
    finally:
        if service is not None:
            await service.close()
        await s.t.financial.close()


@pytest.mark.asyncio
async def test_native_service_cold_reopen_preserves_money_and_reinstalls_both_listeners(database):
    s = await setup(database)
    service = None
    try:
        before = await s.t.financial.snapshot(s.t.cfg.binding)
        await s.reporting.close()
        await s.t.financial.close()
        service = await open_service(s)
        first = service
        await first.close()
        assert not first.events._registered
        service = await open_service(s)
        after = await service.financial.snapshot(s.t.cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert service.controller.processed_data['trading_enabled'] is False
        assert service.events._registered and (await service.controller.runner_facts()).executor_ids == ()
        assert not s.t.session.sent
    finally:
        if service is not None:
            await service.close()
        await s.t.financial.close()


@pytest.mark.asyncio
async def test_native_service_rejects_direct_hedge_execution_before_any_cancel_plan_or_post(database):
    s = await setup(database)
    service = None
    try:
        await s.reporting.close()
        await s.t.financial.close()
        service = await open_service(s)
        before = await service.financial.snapshot(s.t.cfg.binding)
        with pytest.raises(StorageError, match='PG_HEDGE_APPLICATION_STOPPED'):
            await service.hedge.execute('unqualified-direct-hedge')
        after = await service.financial.snapshot(s.t.cfg.binding)
        assert before == after
        assert not service.runtime.healthy and not s.t.session.sent
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.hedge_plans') == 0
    finally:
        if service is not None:
            await service.close()
        await s.t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['credentials', 'config', 'provider', 'executor'])
async def test_native_service_context_changes_revoke_scope_without_new_risk(database, fault):
    from types import SimpleNamespace
    s = await setup(database)
    service = None
    money = None
    claims = None
    try:
        await s.reporting.close()
        await s.t.financial.close()
        service = await open_service(s)
        money = await database[0].fetch('SELECT asset,total,reserved FROM life_financial.balances WHERE account_id=$1 ORDER BY asset', s.t.cfg.account_id)
        claims = await database[0].fetch('SELECT * FROM life_financial.shared_capital_claims WHERE account_id=$1 ORDER BY intent_id', s.t.cfg.account_id)
        if fault == 'credentials':
            s.t.connector.okx_api_key = 'changed-synthetic-key'
            with pytest.raises(StorageError):
                service.events.runner_sources()
        else:
            if fault == 'config':
                service.config = service.config.model_copy(update={'id': 'other'})
            elif fault == 'provider':
                service.controller.market_data_provider = object()
            else:
                service.runner.executor_orchestrator.active_executors['main'].append(SimpleNamespace(config=SimpleNamespace(id='foreign')))
            with pytest.raises(StorageError):
                await service.controller.runner_facts()
        assert not s.t.session.sent
    finally:
        if service is not None:
            await service.close()
            assert service._close_task.done() and service.runtime._closing and not service.runtime._tasks
            assert not service.events._registered and not s.t.session.sent
            assert await database[0].fetch('SELECT asset,total,reserved FROM life_financial.balances WHERE account_id=$1 ORDER BY asset', s.t.cfg.account_id) == money
            assert await database[0].fetch('SELECT * FROM life_financial.shared_capital_claims WHERE account_id=$1 ORDER BY intent_id', s.t.cfg.account_id) == claims
            assert 'foreign' not in service._owned_executors
        await s.t.financial.close()


@pytest.mark.asyncio
async def test_native_service_cancelled_close_keeps_the_owned_cleanup_until_physical_pool_close(database, monkeypatch):
    import asyncio
    s = await setup(database)
    service = task = None
    entered, release = asyncio.Event(), asyncio.Event()
    try:
        await s.reporting.close()
        await s.t.financial.close()
        service = await open_service(s)
        original = service.financial.close

        async def delayed_close():
            entered.set()
            await release.wait()
            await original()
        monkeypatch.setattr(service.financial, 'close', delayed_close)
        task = asyncio.create_task(service.close())
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service._revoked and not service._close_task.done()
        release.set()
        await service.close()
        assert service._close_task.done() and service.runtime._closing
        assert not service.events._registered and not service.runtime._tasks
    finally:
        release.set()
        if service is not None:
            await service.close()
        await s.t.financial.close()


@pytest.mark.asyncio
async def test_timed_out_service_close_remains_joinable_and_retains_late_failure(database, monkeypatch):
    import asyncio
    s = await setup(database)
    service = closing = None
    entered, release = asyncio.Event(), asyncio.Event()
    late = StorageError('SYNTHETIC_LATE_CLOSE_FAILURE')
    try:
        await s.reporting.close()
        await s.t.financial.close()
        service = await open_service(s, limits=replace(limits(), shutdown_timeout_ms=100))
        original = service.financial.close

        async def delayed_close():
            entered.set()
            await release.wait()
            await original()
            raise late
        monkeypatch.setattr(service.financial, 'close', delayed_close)
        closing = asyncio.create_task(service.close())
        with pytest.raises(TimeoutError):
            await closing
        await asyncio.wait_for(entered.wait(), 3)
        assert not service._close_task.done() and not service.runtime._closing
        waiter = asyncio.create_task(service.wait_closed())
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not service._close_task.cancelled() and not service.runtime._closing
        release.set()
        with pytest.raises(StorageError) as result:
            await service.wait_closed()
        assert result.value is late
        with pytest.raises(StorageError) as repeated:
            await service.wait_closed()
        assert repeated.value is late
        assert service._close_task.done() and not service.runtime._tasks
        assert all(pool._closed for pool in service.runtime._pools.values())
    finally:
        release.set()
        if service is not None and service._close_task is not None:
            await asyncio.gather(service._close_task, return_exceptions=True)
        await s.t.financial.close()


@pytest.mark.asyncio
async def test_cancelled_native_startup_closes_opened_pools_before_returning(database, monkeypatch):
    import asyncio

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.recorder_application import PgRecorderApplication
    s = await setup(database)
    entered = asyncio.Event()
    captured = []
    starting = []
    task = None

    async def blocked(reporting, observer):
        captured.append(reporting)
        entered.set()
        await asyncio.Event().wait()
    try:
        await s.reporting.close()
        await s.t.financial.close()
        monkeypatch.setattr(PgRecorderApplication, 'install_joint_events', blocked)
        task = asyncio.create_task(open_service(s, _startup_owner=starting))
        await asyncio.wait_for(entered.wait(), 4)
        assert len(starting) == 1 and starting[0]._opening
        assert starting[0].financial is captured[0].financial
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert captured[0]._closed and captured[0].financial._runtime._closing
        assert starting[0]._close_task.done()
        assert not captured[0].financial._runtime._tasks and not s.t.session.sent
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await s.t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['connector', 'limits', 'owner', 'sources', 'mode'])
async def test_invalid_native_service_profile_never_starts_a_runner_or_places_orders(database, fault):
    import asyncio
    s = await setup(database)
    try:
        changes = {}
        if fault == 'connector':
            changes['connectors'] = {'okx': s.t.connector}
        elif fault == 'limits':
            changes['limits'] = replace(limits(), max_pending=64)
        elif fault == 'owner':
            changes['owner'] = None
        elif fault == 'sources':
            changes['sources'] = tuple(reversed(s.registered))
        else:
            s.controller.config = s.controller.config.model_copy(update={
                'strategy': s.controller.config.strategy.model_copy(update={
                    'perpetual': s.controller.config.strategy.perpetual.model_copy(update={'enabled': False})})})
        tasks = set(asyncio.all_tasks())
        with pytest.raises(StorageError, match='PG_SERVICE_ASSEMBLY_INVALID'):
            await open_service(s, **changes)
        assert not (set(asyncio.all_tasks()) - tasks) and not s.t.session.sent
    finally:
        await s.reporting.close()
        await s.t.financial.close()
