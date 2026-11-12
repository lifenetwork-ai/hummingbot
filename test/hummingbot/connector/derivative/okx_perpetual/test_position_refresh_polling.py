"""A slow history read must not starve native position freshness requests."""
import asyncio
from test.hummingbot.strategy_v2.controllers.test_reduction_controller import maker
from unittest.mock import AsyncMock

import pytest

from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative


@pytest.mark.asyncio
async def test_position_request_is_served_while_the_same_account_poll_waits_for_history():
    controller, _, _ = maker('1', '0')
    connector = OkxPerpetualDerivative(trading_pairs=['BTC-USDT'], trading_required=False)
    controller.market_data_provider.get_connector_with_fallback.return_value = connector
    connector._poll_notifier.set()  # The native poll is already active.
    first, second, history = asyncio.Event(), asyncio.Event(), asyncio.Event()
    reads = 0

    async def positions():
        nonlocal reads
        reads += 1
        connector.position_snapshot_times['BTC-USDT'] = controller.market_data_provider.time()
        (first if reads == 1 else second).set()

    connector._update_positions = AsyncMock(side_effect=positions)
    connector._update_trade_history = AsyncMock(side_effect=history.wait)
    connector._update_order_status = AsyncMock()
    connector._update_balances = AsyncMock()
    task = asyncio.create_task(connector._status_polling_loop_fetch_updates())
    try:
        await asyncio.wait_for(first.wait(), 1)
        controller.market_data_provider.time.return_value = 1011
        controller.request_reduction_position_refresh()
        await asyncio.wait_for(second.wait(), .1)
        assert not task.done() and not history.is_set()
        assert connector.position_snapshot_times['BTC-USDT'] == 1011
        assert connector._update_trade_history.await_count == 1
        assert connector._update_order_status.await_count == 1
        assert connector._update_balances.await_count == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_requests_during_a_position_read_coalesce_into_one_serial_followup():
    connector = OkxPerpetualDerivative(trading_pairs=['BTC-USDT'], trading_required=False)
    entered, release = asyncio.Event(), asyncio.Event()
    reads = active = peak = 0

    async def positions():
        nonlocal reads, active, peak
        reads += 1
        active += 1
        peak = max(peak, active)
        try:
            if reads == 1:
                entered.set()
                await release.wait()
        finally:
            active -= 1

    connector._update_positions = AsyncMock(side_effect=positions)
    for method in ('_update_trade_history', '_update_order_status', '_update_balances'):
        setattr(connector, method, AsyncMock())
    task = asyncio.create_task(connector._status_polling_loop_fetch_updates())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert connector.request_position_refresh()
        assert not any(connector.request_position_refresh() for _ in range(20))
        release.set()
        await asyncio.wait_for(task, 1)
        assert reads == 2 and peak == 1 and active == 0
        assert not connector._position_refresh_notifier.is_set()
        for method in ('_update_trade_history', '_update_order_status', '_update_balances'):
            getattr(connector, method).assert_awaited_once()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['history', 'positions', 'cancel'])
async def test_failed_or_cancelled_poll_drains_all_native_readers(failure):
    connector = OkxPerpetualDerivative(trading_pairs=['BTC-USDT'], trading_required=False)
    entered, release = asyncio.Event(), asyncio.Event()
    active, canceled = set(), set()

    async def read(name):
        active.add(name)
        if len(active) == 4:
            entered.set()
        try:
            await release.wait()
            if name == failure:
                raise OSError('account read unavailable')
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            canceled.add(name)
            raise
        finally:
            active.remove(name)

    for method, name in (('_update_trade_history', 'history'), ('_update_order_status', 'orders'),
                         ('_update_balances', 'balances'), ('_update_positions', 'positions')):
        async def native_read(name=name):
            return await read(name)
        setattr(connector, method, AsyncMock(side_effect=native_read))
    task = asyncio.create_task(connector._status_polling_loop_fetch_updates())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        if failure == 'cancel':
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
        else:
            release.set()
            with pytest.raises(OSError, match='account read unavailable'):
                await asyncio.wait_for(task, 1)
        assert not active
        assert canceled == {'history', 'orders', 'balances', 'positions'} - {failure}
        assert connector.position_snapshot_times == {}
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
