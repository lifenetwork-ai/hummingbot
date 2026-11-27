"""History fetches at a managed profile handoff must leave the engine responsive."""
import asyncio
import builtins
import threading
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.core.trading_core import TradingCore
from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator
from hummingbot.strategy_v2.models.executors import CloseType
from scripts.v2_with_controllers import V2WithControllers


def setup(monkeypatch, *, bad=False):
    entered, release = threading.Event(), threading.Event()
    waits, threads, closed, constructed = [], [], [], []

    def history(ids):
        assert list(ids) == ['a']
        threads.append(threading.get_ident())
        try:
            entered.set()
            waits.append(release.wait(.5))
            for pnl in ('1e27', '-1e27', '.0001', 'NaN' if bad else '.123'):
                yield SimpleNamespace(controller_id='a', net_pnl_quote=Decimal(pnl),
                                      filled_amount_quote=Decimal('.001'), close_type=CloseType.EARLY_STOP)
        finally:
            closed.append(threading.get_ident())

    recorder = SimpleNamespace(iter_executor_performance=history, has_uncovered_positions=lambda ids: False,
                               get_all_positions=lambda **kw: [], sql_manager=SimpleNamespace(
                                   assert_persistence_healthy=lambda: None))
    monkeypatch.setattr(MarketsRecorder, 'get_instance', lambda: recorder)

    class Strategy(V2WithControllers):
        markets = {}

        def __init__(self, markets, config, *, startup_performance=None):
            constructed.append(threading.get_ident())
            self.config, self.controllers = config, {'a': None}
            kwargs = {} if startup_performance is None else {'startup_performance': startup_performance}
            self.executor_orchestrator = ExecutorOrchestrator(self, **kwargs)

    core = object.__new__(TradingCore)
    core.retained_market_data_provider = None
    core.strategy, core.strategy_name = None, 'v2_with_controllers'
    core.connector_manager = SimpleNamespace(get_all_connectors=lambda: {})
    config = SimpleNamespace(mm_drawdown_scope={'a': object()})
    core.load_v2_class = lambda name: (Strategy, config)
    core.initialize_markets = AsyncMock()
    return core, entered, release, waits, threads, closed, constructed


async def wait_entered(event):
    async with asyncio.timeout(2):
        while not event.is_set():
            await asyncio.sleep(.001)


@pytest.mark.asyncio
async def test_managed_startup_fetch_allows_loop_progress_and_preserves_decimal_history(monkeypatch):
    core, entered, release, waits, threads, closed, constructed = setup(monkeypatch)
    importing, release_import = threading.Event(), threading.Event()
    import_waits = []
    original_import = builtins.__import__

    def slow_orchestrator_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'hummingbot.strategy_v2.executors.executor_orchestrator' and 'ExecutorOrchestrator' in fromlist:
            importing.set()
            import_waits.append(release_import.wait(.5))
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, '__import__', slow_orchestrator_import)

    async def probe():
        await wait_entered(importing)
        release_import.set()
        await wait_entered(entered)
        release.set()

    await asyncio.gather(core._initialize_v2_strategy(), probe())
    assert import_waits == [True], 'Cold executor import blocked the engine loop'
    assert waits == [True], 'Historical fetchmany blocked the engine loop'
    assert threads == closed and threads[0] != threading.get_ident()
    assert constructed == [threading.get_ident()], 'Native strategy construction must stay on its loop'
    report = core.strategy.executor_orchestrator.cached_performance['a']
    assert report.realized_pnl_quote == Decimal('.1231')
    assert report.volume_traded == Decimal('.004')
    assert report.close_type_counts == {CloseType.EARLY_STOP: 4}


@pytest.mark.asyncio
async def test_bad_startup_history_closes_reader_before_strategy_is_constructed(monkeypatch):
    core, _, release, _, _, closed, constructed = setup(monkeypatch, bad=True)
    release.set()
    with pytest.raises(ValueError):
        await core._initialize_v2_strategy()
    assert closed and not constructed and core.strategy is None


@pytest.mark.asyncio
async def test_cancelled_startup_drains_history_worker_without_constructing_strategy(monkeypatch):
    core, entered, release, _, _, closed, constructed = setup(monkeypatch)
    task = asyncio.create_task(core._initialize_v2_strategy())
    await wait_entered(entered)
    task.cancel()
    await asyncio.sleep(.01)
    task.cancel()
    await asyncio.sleep(.01)
    try:
        assert not task.done() and not closed, 'Cancellation detached the history reader'
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled() and closed and not constructed and core.strategy is None


@pytest.mark.asyncio
async def test_replaced_recorder_cannot_publish_history_from_previous_runtime(monkeypatch):
    core, entered, release, _, _, closed, constructed = setup(monkeypatch)

    async def replace():
        await wait_entered(entered)
        monkeypatch.setattr(MarketsRecorder, 'get_instance', lambda: object())
        release.set()

    task = asyncio.create_task(replace())
    try:
        with pytest.raises(RuntimeError, match='recorder'):
            await core._initialize_v2_strategy()
    finally:
        release.set()
        await task
    assert closed and not constructed and core.strategy is None


@pytest.mark.asyncio
@pytest.mark.parametrize('changed', ['scope', 'persistence'])
async def test_startup_rechecks_scope_and_persistence_after_read(monkeypatch, changed):
    core, entered, release, _, _, closed, constructed = setup(monkeypatch)
    _, config = core.load_v2_class(core.strategy_name)

    async def change():
        await wait_entered(entered)
        if changed == 'scope':
            config.mm_drawdown_scope['other'] = object()
        else:
            def unhealthy():
                raise RuntimeError('persistence failed while reading')
            MarketsRecorder.get_instance().sql_manager.assert_persistence_healthy = unhealthy
        release.set()

    task = asyncio.create_task(change())
    try:
        with pytest.raises(ValueError if changed == 'scope' else RuntimeError):
            await core._initialize_v2_strategy()
    finally:
        release.set()
        await task
    assert closed and not constructed and core.strategy is None


@pytest.mark.asyncio
async def test_unmanaged_script_keeps_legacy_constructor_without_recorder_reads(monkeypatch):
    calls = []

    class Legacy:
        markets = {}

        def __init__(self, markets, config):
            calls.append((threading.get_ident(), config))

    core = object.__new__(TradingCore)
    core.retained_market_data_provider = None
    core.strategy, core.strategy_name = None, 'legacy'
    core.connector_manager = SimpleNamespace(get_all_connectors=lambda: {})
    core.initialize_markets = AsyncMock()
    config = SimpleNamespace(mm_drawdown_scope=None)
    core.load_v2_class = lambda name: (Legacy, config)
    await core._initialize_v2_strategy()
    assert isinstance(core.strategy, Legacy) and calls == [(threading.get_ident(), config)]
    calls.clear()
    result = await V2WithControllers.create_with_async_history.__func__(Legacy, {}, config)
    assert isinstance(result, Legacy) and calls == [(threading.get_ident(), config)]


@pytest.mark.asyncio
async def test_partial_history_transport_failure_cannot_publish_valid_prefix(monkeypatch):
    core, _, release, _, _, _, constructed = setup(monkeypatch)
    recorder = MarketsRecorder.get_instance()
    original = recorder.iter_executor_performance
    closed = []

    def broken(ids):
        stream = original(ids)
        try:
            yield next(stream)
            raise OSError('synthetic history response failure')
        finally:
            stream.close()
            closed.append(True)

    recorder.iter_executor_performance = broken
    release.set()
    with pytest.raises(OSError):
        await core._initialize_v2_strategy()
    assert closed == [True] and not constructed and core.strategy is None


@pytest.mark.asyncio
async def test_cancellation_closes_current_batch_without_scanning_remaining_history(monkeypatch):
    core, entered, release, _, _, closed, constructed = setup(monkeypatch)
    recorder = MarketsRecorder.get_instance()
    original, consumed = recorder.iter_executor_performance, []

    def count(ids):
        stream = original(ids)
        try:
            for row in stream:
                consumed.append(True)
                yield row
        finally:
            stream.close()

    recorder.iter_executor_performance = count
    task = asyncio.create_task(core._initialize_v2_strategy())
    await wait_entered(entered)
    task.cancel()
    await asyncio.sleep(.01)
    release.set()
    await asyncio.gather(task, return_exceptions=True)
    assert consumed == [True], 'Stop drained the whole historical scan instead of closing its current read'
    assert task.cancelled() and closed and not constructed


@pytest.mark.asyncio
async def test_abandoned_handoff_drains_its_open_reader(monkeypatch):
    from hummingbot.cli.profile_switch import ProfileSwitchPreparation

    _, entered, release, waits, threads, closed, constructed = setup(monkeypatch)
    preparation = ProfileSwitchPreparation(SimpleNamespace(id='session', generation=2), object(), [], history_scope=['a'])
    try:
        preparation.start_history(MarketsRecorder.get_instance())
        await wait_entered(entered)
        closing = asyncio.create_task(preparation.close())
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        await closing
        assert preparation.history.task.cancelled() and closed == threads and not constructed
        assert waits == [True]
    finally:
        release.set()
        await preparation.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('mismatch', ['recorder', 'scope'])
async def test_mismatched_prepared_history_reads_the_current_scope_again(monkeypatch, mismatch):
    core, _, release, _, threads, _, _ = setup(monkeypatch)
    recorder = MarketsRecorder.get_instance()
    _, config = core.load_v2_class(core.strategy_name)
    stale = SimpleNamespace(recorder=object() if mismatch == 'recorder' else recorder,
                            identities=('other',) if mismatch == 'scope' else ('a',))
    release.set()
    result = await V2WithControllers.create_with_async_history.__func__(
        lambda *a, startup_performance: startup_performance, {}, config, prepared_history=stale)
    assert len(threads) == 1 and result.controller_ids == ('a',)


@pytest.mark.asyncio
@pytest.mark.parametrize('ready', [False, True])
async def test_cloud_log_reports_how_long_startup_actually_waited(monkeypatch, caplog, ready):
    import json
    import logging

    from hummingbot.core.startup_history import StartupHistoryRead
    from hummingbot.logger.service_logging import ServiceFormatter

    core, entered, release, _, threads, _, _ = setup(monkeypatch)
    _, config = core.load_v2_class(core.strategy_name)
    read = StartupHistoryRead(MarketsRecorder.get_instance(), tuple(config.mm_drawdown_scope))
    try:
        await wait_entered(entered)
        if ready:
            release.set()
            await read.task
        with caplog.at_level(logging.INFO):
            startup = asyncio.create_task(V2WithControllers.create_with_async_history.__func__(
                lambda *a, **kw: True, {}, config, prepared_history=read))
            await asyncio.sleep(0)
            assert startup.done() is ready
            release.set()
            assert await startup
    finally:
        release.set()
        await asyncio.gather(read.task, return_exceptions=True)
    event = next(r for r in caplog.records if getattr(r, 'event', None) == 'mm_profile_history_handoff')
    payload = json.loads(ServiceFormatter('json', {}).format(event))
    assert payload['history_ready_at_start'] is ready and payload['history_wait_ms'] >= 0
    assert len(threads) == 1
