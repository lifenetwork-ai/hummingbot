"""A timed-out TLS loader must retain its physical slot and join on close."""
import asyncio
from test.hummingbot.strategy_v2.life_liquidity.storage.test_postgres_runtime_config import configuration
from threading import Event

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.postgres import monitor as module


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [False, True])
async def test_original_tls_thread_retains_one_slot_and_cannot_open_a_late_pool(monkeypatch, cancel):
    release, entered = Event(), asyncio.Event()
    loop = asyncio.get_running_loop()
    calls = []

    def blocked(*args):
        calls.append('tls')
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return None

    async def no_late_pool(*args, **kwargs):
        calls.append('pool')
        raise AssertionError('A closing monitor must not start a new pool')

    monkeypatch.setattr(module, '_ssl_context', blocked)
    import asyncpg
    monkeypatch.setattr(asyncpg, 'create_pool', no_late_pool)
    monitor = module.PgFinancialMonitor(configuration(), max_rows=8, timeout_ms=1000 if cancel else 10)
    task = asyncio.create_task(monitor.sample())
    closing = None
    try:
        await asyncio.wait_for(entered.wait(), 1)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await asyncio.wait_for(task, .5)
        assert monitor.status()['state'] == 'UNAVAILABLE'
        for _ in range(4):
            await asyncio.wait_for(monitor.sample(), .1)
        assert calls == ['tls']
        closing = asyncio.create_task(monitor.close())
        await asyncio.sleep(.02)
        assert not closing.done()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        if closing is not None:
            await closing
        await monitor.close()
    assert calls == ['tls'] and monitor._pool is None
    assert monitor.status()['state'] == 'CLOSED'


@pytest.mark.asyncio
async def test_late_read_result_never_becomes_a_fresh_snapshot(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    monitor = module.PgFinancialMonitor(configuration(), max_rows=8, timeout_ms=10)

    async def read():
        calls.append('read')
        entered.set()
        await release.wait()
        return {'old_result': True}

    monkeypatch.setattr(monitor, '_collect', read)
    try:
        await monitor.sample()
        assert entered.is_set() and monitor.status()['state'] == 'UNAVAILABLE'
        await monitor.sample()
        assert calls == ['read']
        release.set()
        await asyncio.sleep(.02)
        assert 'old_result' not in monitor.status() and monitor._sampled_at is None
        monkeypatch.setattr(monitor, '_collect', lambda: asyncio.sleep(0, result={'new_result': True}))
        await monitor.sample()
        assert monitor.status()['new_result'] is True and 'old_result' not in monitor.status()
    finally:
        release.set()
        await monitor.close()


@pytest.mark.asyncio
async def test_driver_self_cancellation_is_unavailable_without_cancelling_sampler(monkeypatch):
    monitor = module.PgFinancialMonitor(configuration(), max_rows=8, timeout_ms=100)

    async def cancelled():
        raise asyncio.CancelledError()

    monkeypatch.setattr(monitor, '_collect', cancelled)
    await monitor.sample()
    assert monitor.status()['state'] == 'UNAVAILABLE'
    assert asyncio.current_task().cancelling() == 0
    await monitor.close()


@pytest.mark.asyncio
async def test_closing_before_original_collection_starts_opens_no_tls_or_pool(monkeypatch):
    monitor = module.PgFinancialMonitor(configuration(), max_rows=8, timeout_ms=1000)
    original = monitor._collect
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed():
        entered.set()
        await release.wait()
        return await original()

    def no_tls(*args):
        pytest.fail('Closing before collection must not start new I/O')

    monkeypatch.setattr(module, '_ssl_context', no_tls)
    monkeypatch.setattr(monitor, '_collect', delayed)
    sampling = asyncio.create_task(monitor.sample())
    await entered.wait()
    closing = asyncio.create_task(monitor.close())
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(sampling, closing)
    assert monitor.status()['state'] == 'CLOSED' and monitor._pool is None
