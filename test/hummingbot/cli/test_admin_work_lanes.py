"""Heavy report/history work leaves one physical slot for operations."""
import asyncio
import json
import threading
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from multidict import MultiDict

from hummingbot.cli.admin_api import AdminAPI
from hummingbot.cli.admin_database import AdminDatabaseExecutor, DatabaseBusy
from hummingbot.client.config.admin_config import AdminSettings
from hummingbot.model.admin_reporting import day_range


def snapshot():
    start, end, *_ = day_range(None, None, 'UTC')
    return dict(start_day=start, end_day=end, reporting_timezone='UTC',
                server_time=datetime.now(timezone.utc), coverage='observed_recorder_events',
                balance_scope='account_last_observed', balances=[], markets=[])


@pytest.mark.asyncio
async def test_heavy_queue_cannot_consume_operational_read_admission():
    executor = AdminDatabaseExecutor()
    started, release = threading.Event(), threading.Event()
    futures = []

    def blocked():
        started.set()
        assert release.wait(5)

    try:
        # Retain the existing 12 non-control slots; reserve two for operations.
        futures = [executor.submit(blocked, heavy=True)
                   for _ in range(executor.MAX_REPORTS - 2)]
        assert await asyncio.to_thread(started.wait, 2)
        with pytest.raises(DatabaseBusy):
            executor.submit(lambda: None, heavy=True)
        assert await asyncio.wait_for(executor.submit(lambda: 'balance available'), 1) == 'balance available'
        release.set()
        await asyncio.gather(*futures)
        assert executor.active == executor.pending == executor.reports == 0
    finally:
        release.set()
        await asyncio.gather(*futures, return_exceptions=True)
        await executor.close()


@pytest.mark.asyncio
async def test_blocked_daily_and_history_leave_balances_available_through_real_handlers():
    daily_started, history_started, release = threading.Event(), threading.Event(), threading.Event()
    storage = MagicMock()

    def daily(**filters):
        daily_started.set()
        assert release.wait(5)
        return snapshot()

    def orders(**filters):
        history_started.set()
        assert release.wait(5)
        return dict(items=[], next_cursor=None)

    storage.daily.side_effect = daily
    storage.orders.side_effect = orders
    storage.observations.return_value = dict(accounts=[], server_time=datetime.now(timezone.utc), owner_available=True)
    api = AdminAPI(None, AdminSettings(reporting_timezone='UTC'), store=storage)
    request = SimpleNamespace(query=MultiDict())
    tasks = []
    try:
        tasks.append(asyncio.create_task(api.daily(request)))
        assert await asyncio.to_thread(daily_started.wait, 2)
        tasks.append(asyncio.create_task(api.orders(SimpleNamespace(query=MultiDict(limit='500')))))
        await asyncio.sleep(.02)
        response = await asyncio.wait_for(api.balances(request), 1)
        assert json.loads(response.text)['owner_available']
        assert not history_started.is_set(), 'A second heavy job must leave operations capacity reserved'
        assert api._database_executor.active == 1
        release.set()
        await asyncio.gather(*tasks)
        assert history_started.is_set()
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await api.close(None)


@pytest.mark.asyncio
async def test_small_history_page_remains_available_while_heavy_daily_read_waits():
    storage = MagicMock()
    started, release = threading.Event(), threading.Event()

    def daily(**filters):
        started.set()
        assert release.wait(5)
        return snapshot()

    storage.daily.side_effect = daily
    storage.orders.return_value = dict(items=[], next_cursor=None)
    api = AdminAPI(None, AdminSettings(reporting_timezone='UTC'), store=storage)
    task = asyncio.create_task(api.daily(SimpleNamespace(query=MultiDict())))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        page = await asyncio.wait_for(api.orders(SimpleNamespace(query=MultiDict(limit='100'))), 1)
        assert json.loads(page.text)['items'] == [] and not task.done()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await api.close(None)


@pytest.mark.asyncio
async def test_operations_and_controls_cannot_starve_fifo_heavy_work():
    executor = AdminDatabaseExecutor()
    op_started, op_release, heavy_started, heavy_release = [threading.Event() for _ in range(4)]
    order, futures = [], []

    def operation():
        op_started.set()
        assert op_release.wait(5)

    def heavy():
        order.append('heavy-first')
        heavy_started.set()
        assert heavy_release.wait(5)

    try:
        futures.append(executor.submit(operation))
        assert await asyncio.to_thread(op_started.wait, 2)
        futures.extend(executor.submit(lambda: order.append('operation')) for _ in range(3))
        futures.extend(executor.submit(lambda: order.append('control'), control=True) for _ in range(5))
        futures.append(executor.submit(heavy, heavy=True))
        futures.append(executor.submit(lambda: order.append('heavy-second'), heavy=True))
        assert await asyncio.to_thread(heavy_started.wait, 1), 'Operations cannot consume the heavy slot'
        assert order == ['heavy-first'] and executor.active == 2
        heavy_release.set()
        await futures[-1]
        assert order == ['heavy-first', 'heavy-second'], 'Queued controls cannot starve independent heavy progress'
        op_release.set()
        await asyncio.gather(*futures)
        assert order[2:7] == ['control'] * 5 and order[7:] == ['operation'] * 3
        assert executor.active == executor.pending == executor.reports == 0
    finally:
        op_release.set()
        heavy_release.set()
        await asyncio.gather(*futures, return_exceptions=True)
        await executor.close()


@pytest.mark.asyncio
async def test_cancelled_running_heavy_job_keeps_its_slot_until_physical_completion():
    executor = AdminDatabaseExecutor()
    started, release, replacement_started = threading.Event(), threading.Event(), threading.Event()
    futures = []

    def blocked():
        started.set()
        assert release.wait(5)

    try:
        first = executor.submit(blocked, heavy=True)
        futures.append(first)
        assert await asyncio.to_thread(started.wait, 2)
        first.cancel()
        replacement = executor.submit(replacement_started.set, heavy=True)
        futures.append(replacement)
        assert await executor.submit(lambda: 'operations available') == 'operations available'
        assert not replacement_started.is_set() and executor.active == 1
        release.set()
        await replacement
        assert replacement_started.is_set() and executor.pending == 0
    finally:
        release.set()
        await asyncio.gather(*futures, return_exceptions=True)
        await executor.close()


@pytest.mark.asyncio
async def test_close_drains_both_lanes_and_cancels_queued_work_without_execution():
    executor = AdminDatabaseExecutor()
    started = [threading.Event(), threading.Event()]
    release = threading.Event()
    futures, closing = [], None

    def blocked(index):
        started[index].set()
        assert release.wait(5)

    try:
        futures = [executor.submit(lambda: blocked(0)), executor.submit(lambda: blocked(1), heavy=True)]
        assert all([await asyncio.to_thread(event.wait, 2) for event in started])
        queued = [executor.submit(lambda: pytest.fail('Queued job ran during close'), **kind)
                  for kind in ({}, {'heavy': True}, {'control': True})]
        futures.extend(queued)
        closing = asyncio.create_task(executor.close())
        await asyncio.sleep(.02)
        assert all(future.cancelled() for future in queued) and not closing.done()
        assert executor.active == 2
        release.set()
        await closing
        assert executor.active == executor.pending == executor.reports == 0
    finally:
        release.set()
        await asyncio.gather(*futures, return_exceptions=True)
        if closing is not None:
            await closing
        else:
            await executor.close()


@pytest.mark.parametrize('heavy', [False, True])
@pytest.mark.asyncio
async def test_exception_and_cancelled_queue_entry_do_not_leak_lane_capacity(heavy):
    executor = AdminDatabaseExecutor()
    started, release = threading.Event(), threading.Event()
    futures = []

    def blocked():
        started.set()
        assert release.wait(5)

    def fail():
        raise ValueError('synthetic lane failure')

    try:
        futures.append(executor.submit(blocked, heavy=heavy))
        assert await asyncio.to_thread(started.wait, 2)
        cancelled = executor.submit(lambda: pytest.fail('Cancelled queue entry executed'), heavy=heavy)
        futures.append(cancelled)
        cancelled.cancel()
        failed = executor.submit(fail, heavy=heavy)
        futures.append(failed)
        release.set()
        with pytest.raises(ValueError, match='synthetic lane failure'):
            await failed
        assert await executor.submit(lambda: 'still available', heavy=heavy) == 'still available'
        assert executor.active == executor.pending == executor.reports == 0
    finally:
        release.set()
        await asyncio.gather(*futures, return_exceptions=True)
        await executor.close()


@pytest.mark.parametrize('handler,limit,heavy', [
    *[(handler, limit, limit > 100) for handler in (
        'commands_history', 'sessions', 'orders', 'order', 'fills', 'session_events') for limit in (100, 500)],
    *[(handler, None, False) for handler in (
        'balances', 'positions', 'performance', 'reconciliation', 'reconciliation_by_id', 'liquidity', 'session_configuration')],
])
@pytest.mark.asyncio
async def test_handlers_explicitly_route_history_and_current_observations(handler, limit, heavy):
    api = AdminAPI(None, AdminSettings(), store=MagicMock())
    api.run_database = AsyncMock(side_effect=RuntimeError('captured dispatch'))
    query = MultiDict({('timeline_limit' if handler == 'order' else 'limit'): str(limit)}) if limit else MultiDict()
    request = SimpleNamespace(query=query, match_info=dict(
        order_id='order', session_id='session', check_id='check'))
    try:
        with pytest.raises(RuntimeError, match='captured dispatch'):
            await getattr(api, handler)(request)
        assert api.run_database.await_count == 1
        assert api.run_database.await_args.kwargs.get('heavy', False) is heavy
    finally:
        await api.close(None)


@pytest.mark.parametrize('heavy', [False, True])
@pytest.mark.asyncio
async def test_physical_submission_failure_returns_every_admission_slot(monkeypatch, heavy):
    executor = AdminDatabaseExecutor()
    original = executor.executor.submit
    try:
        with monkeypatch.context() as patch:
            patch.setattr(executor.executor, 'submit', MagicMock(side_effect=RuntimeError('submit failed')))
            with pytest.raises(RuntimeError, match='submit failed'):
                await executor.submit(lambda: None, heavy=heavy)
        assert executor.active == executor.pending == executor.reports == executor.heavy_pending == 0
        assert executor.active_heavy == 0
        assert executor.executor.submit == original
        assert await executor.submit(lambda: 'recovered', heavy=heavy) == 'recovered'
    finally:
        await executor.close()


@pytest.mark.asyncio
async def test_invalid_control_classification_does_not_consume_admission():
    executor = AdminDatabaseExecutor()
    try:
        with pytest.raises(ValueError, match='operations lane'):
            executor.submit(lambda: None, control=True, heavy=True)
        assert executor.pending == executor.reports == executor.heavy_pending == 0
    finally:
        await executor.close()
