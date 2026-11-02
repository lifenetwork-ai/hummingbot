"""Durable status publication must not occupy the engine event loop."""
import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from hummingbot.cli.service import ServiceHost
from hummingbot.client.config.runtime_config import RuntimeSettings


def host(monkeypatch):
    item = ServiceHost(RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test'}),
                       asyncio.Event(), MagicMock())
    item.hb = SimpleNamespace(trading_core=SimpleNamespace(strategy=None, connector_manager=SimpleNamespace(connectors={})))
    item.owner = MagicMock(owner_uuid='owner', generation=1, lost=False)
    item.owner.try_acquire.return_value = True
    item.owner.check_owner.return_value = dict(control_version=0, config_type='controller', config_file_path='test.yml',
                                               desired_state='stopped', recovery_state={}, mm_session=None)
    item.applied_version = 0
    item.bootstrapped = True
    item.observer_progress_at = time.monotonic()
    item.snapshot = MagicMock(return_value={})
    item.poll_reconciliation = AsyncMock()
    item.poll_funding = item.poll_outflow = MagicMock()
    item.stop_core = AsyncMock(return_value=True)
    monkeypatch.setattr('hummingbot.core.order_submission_guard.install_guard', lambda owner: None)
    return item


@pytest.mark.asyncio
async def test_slow_status_write_leaves_engine_health_loop_responsive(monkeypatch):
    item = host(monkeypatch)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    threads = []
    loop_thread = threading.get_ident()

    def write(**fields):
        threads.append(threading.get_ident())
        entered.set()
        release.wait(1)
        finished.set()
        return True

    item.owner.store.publish.side_effect = write
    task = asyncio.create_task(item.observe_once())
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        assert not finished.is_set(), 'Publication occupied the event loop until the DB write completed'
        assert (await item.health(SimpleNamespace(path='/live'))).status == 200
        assert not task.done()
        assert threads == [threads[0]] and threads[0] != loop_thread
        assert item.snapshot.call_count == 1
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_cancelled_observer_cannot_release_owner_before_physical_publication_finishes(monkeypatch):
    item = host(monkeypatch)
    entered, release = threading.Event(), threading.Event()
    order = []

    def write(**fields):
        if not entered.is_set():
            entered.set()
            release.wait(1)
            order.append('first_committed')
        else:
            order.append('final_committed')
        return True

    item.owner.store.publish.side_effect = write
    item.owner.release.side_effect = lambda: order.append('owner_released')
    item.owner.check_owner.return_value = {'control_version': 0}
    observer = asyncio.create_task(item.publish_status(0))
    closing = None
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        observer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await observer
        closing = asyncio.create_task(item.close())
        await asyncio.sleep(.02)
        assert not closing.done()
        item.owner.release.assert_not_called()
        release.set()
        assert await closing
        assert order == ['first_committed', 'final_committed', 'owner_released']
    finally:
        release.set()
        await asyncio.gather(observer, *([closing] if closing else []), return_exceptions=True)


@pytest.mark.asyncio
async def test_status_snapshot_stays_on_loop_and_daily_cadence_is_applied_once(monkeypatch):
    item = host(monkeypatch)
    loop_thread = threading.get_ident()
    calls = []
    item.snapshot.side_effect = lambda: calls.append(threading.get_ident()) or {}
    item.owner.store.publish.side_effect = lambda **fields: True
    await item.publish_status(0)
    await item.publish_status(0)
    assert calls == [loop_thread, loop_thread]
    assert [c.kwargs['save_balances'] for c in item.owner.store.publish.call_args_list] == [True, False]


@pytest.mark.asyncio
async def test_overlapping_publications_never_overlap_physical_writes(monkeypatch):
    item = host(monkeypatch)
    state = dict(active=0, peak=0)
    lock = threading.Lock()

    def write(**fields):
        with lock:
            state['active'] += 1
            state['peak'] = max(state['peak'], state['active'])
        try:
            time.sleep(.03)
            return True
        finally:
            with lock:
                state['active'] -= 1

    item.owner.store.publish.side_effect = write
    await asyncio.gather(*(item.publish_status(version) for version in range(6)))
    assert state == {'active': 0, 'peak': 1}
    assert item.owner.store.publish.call_count == 6


@pytest.mark.asyncio
async def test_cancelled_waiter_still_propagates_physical_failure_to_shutdown(monkeypatch):
    item = host(monkeypatch)
    entered, release = threading.Event(), threading.Event()

    def write(**fields):
        entered.set()
        release.wait(2)
        raise OSError('injected commit failure')

    item.owner.store.publish.side_effect = write
    task = asyncio.create_task(item.publish_status(0))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        assert not await item.close()
        item.owner.release.assert_not_called()
        assert item.owner.store.publish.call_count == 1
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancellation_at_failed_write_completion_cannot_hide_failure_from_shutdown(monkeypatch):
    item = host(monkeypatch)
    writes = 0
    observer = None

    def write(**fields):
        nonlocal writes
        writes += 1
        if writes == 1:
            raise OSError('injected commit failure')
        return True

    def finished(physical_task):
        # Task callbacks run before the shield resumes its waiter. Reproduce
        # cancellation arriving in the same loop turn as the DB failure.
        physical_task.exception()
        observer.cancel()

    item.owner.store.publish.side_effect = write
    monkeypatch.setattr(item, '_publication_finished', finished)
    observer = asyncio.create_task(item.publish_status(0))
    with pytest.raises(asyncio.CancelledError):
        await observer
    assert not await item.close()
    item.owner.release.assert_not_called()
    assert writes == 1


@pytest.mark.asyncio
async def test_worker_receives_detached_snapshot_and_does_not_read_live_state(monkeypatch):
    item = host(monkeypatch)
    entered, release = threading.Event(), threading.Event()
    live = {'balances': {'BTC': '1'}}
    received = []
    item.snapshot.side_effect = lambda: live

    def write(**fields):
        entered.set()
        release.wait(2)
        received.append(fields['status']['balances']['BTC'])
        return False  # A fenced rejection cannot advance the daily cadence.

    item.owner.store.publish.side_effect = write
    task = asyncio.create_task(item.publish_status(0))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        live['balances']['BTC'] = '2'
        release.set()
        await task
        assert received == ['1']
        assert item._daily_next_write_at == 0
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_shutdown_prevents_queued_publication_after_owner_release(monkeypatch):
    item = host(monkeypatch)
    entered, release = threading.Event(), threading.Event()
    writes = []

    def write(**fields):
        writes.append(fields['version'])
        entered.set()
        release.wait(2)
        return True

    item.owner.store.publish.side_effect = write
    item.owner.check_owner.return_value = {'control_version': 2}
    first = asyncio.create_task(item.publish_status(0))
    queued = closing = None
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        queued = asyncio.create_task(item.publish_status(1))
        await asyncio.sleep(0)
        closing = asyncio.create_task(item.close())
        await asyncio.sleep(.02)
        release.set()
        assert await closing
        await asyncio.gather(first, queued)
        assert writes == [0, 2]
        item.owner.release.assert_called_once()
        await item.publish_status(3)
        assert writes == [0, 2]
    finally:
        release.set()
        await asyncio.gather(first, *([queued] if queued else []), *([closing] if closing else []), return_exceptions=True)
