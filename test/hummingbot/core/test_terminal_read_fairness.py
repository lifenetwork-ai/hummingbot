"""A durable terminal SELECT may wait; the engine loop and owner must stay live."""
import asyncio
import threading
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.core import automatic_recovery as module
from hummingbot.core.bot_ownership import OwnershipLost
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState, OrderUpdate, TradeUpdate
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee
from hummingbot.core.order_submission_guard import SubmissionBlocked


def setup(monkeypatch):
    main = threading.get_ident()
    connection_threads, closed = [], []

    @contextmanager
    def connect():
        connection_threads.append(threading.get_ident())
        try:
            yield object()
        finally:
            closed.append(threading.get_ident())

    owner = SimpleNamespace(engine=SimpleNamespace(connect=connect), terminal_tasks=set(), lost=False)

    def check():
        assert threading.get_ident() == main, 'Physical owner access escaped its loop thread'
        if owner.lost:
            raise OwnershipLost('test physical owner lost')

    owner.check_owner = Mock(side_effect=check)
    recorder = SimpleNamespace(sql_manager=SimpleNamespace(engine=owner.engine, assert_persistence_healthy=Mock()))
    monkeypatch.setattr(MarketsRecorder, '_shared_instance', recorder)
    connector = SimpleNamespace(name='okx')
    order = InFlightOrder('read-order', 'BTC-USDT', OrderType.LIMIT_MAKER, TradeType.BUY,
                          Decimal('.1'), 1700000000, price=Decimal('100'), exchange_order_id='venue-read')
    order.current_state = OrderState.OPEN
    raw = dict(clOrdId=order.client_order_id, ordId=order.exchange_order_id, instId='BTC-USDT', side='buy',
               ordType='post_only', px='100', sz='.1', accFillSz='0', state='canceled', uTime='1700000001000')
    update = OrderUpdate(order.trading_pair, 1700000001, OrderState.CANCELED,
                         order.client_order_id, order.exchange_order_id, {'venue_order': raw})
    return module.AutomaticOrderRecovery(owner), owner, connector, order, update, connection_threads, closed


async def entered(event):
    async with asyncio.timeout(2):
        while not event.is_set():
            await asyncio.sleep(.001)


@pytest.mark.asyncio
async def test_terminal_read_services_loop_before_database_returns(monkeypatch):
    recovery, owner, connector, order, update, threads, closed = setup(monkeypatch)
    in_read, release = threading.Event(), threading.Event()
    progressed = []

    def slow_read(*args, **kwargs):
        in_read.set()
        progressed.append(release.wait(.5))
        return True

    monkeypatch.setattr(module, 'durable_fills', slow_read)

    async def probe():
        await entered(in_read)
        release.set()

    await asyncio.gather(recovery.terminal_update(connector, order, update), probe())
    assert progressed == [True], 'Synchronous terminal SQL held the engine loop until the read completed'
    assert all(identity != threading.get_ident() for identity in threads)
    assert closed == threads and not owner.terminal_tasks
    assert order._managed_terminal_proved


@pytest.mark.asyncio
@pytest.mark.parametrize('changed', ['owner', 'fill', 'recorder'])
async def test_terminal_read_rechecks_owner_and_native_fills_after_wait(monkeypatch, changed):
    recovery, owner, connector, order, update, _, closed = setup(monkeypatch)
    in_read, release = threading.Event(), threading.Event()

    def slow_read(*args, **kwargs):
        in_read.set()
        release.wait(.5)
        return True

    monkeypatch.setattr(module, 'durable_fills', slow_read)

    async def mutate():
        await entered(in_read)
        if changed == 'owner':
            owner.lost = True
        elif changed == 'recorder':
            monkeypatch.setattr(MarketsRecorder, '_shared_instance', SimpleNamespace(
                sql_manager=SimpleNamespace(engine=owner.engine)))
        else:
            order.update_with_trade_update(TradeUpdate(
                'new-fill', order.client_order_id, order.exchange_order_id, order.trading_pair,
                1700000001, Decimal('100'), Decimal('.01'), Decimal('1'), AddedToCostTradeFee()))
        release.set()

    task = asyncio.create_task(recovery.terminal_update(connector, order, update))
    mutation = asyncio.create_task(mutate())
    try:
        expected = OwnershipLost if changed == 'owner' else SubmissionBlocked if changed == 'recorder' else IOError
        with pytest.raises(expected):
            await task
    finally:
        release.set()
        await mutation
    assert not getattr(order, '_managed_terminal_proved', False)
    assert closed and not owner.terminal_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel_children', [False, True])
async def test_cancelled_terminal_read_drains_connection_before_releasing_slot(monkeypatch, cancel_children):
    recovery, owner, connector, order, update, _, closed = setup(monkeypatch)
    in_read, release = threading.Event(), threading.Event()

    def slow_read(*args, **kwargs):
        in_read.set()
        release.wait(.5)
        return True

    monkeypatch.setattr(module, 'durable_fills', slow_read)
    baseline = set(asyncio.all_tasks())
    task = asyncio.create_task(recovery.terminal_update(connector, order, update))
    await entered(in_read)
    if cancel_children:
        for child in set(asyncio.all_tasks()) - baseline - {task}:
            child.cancel()  # Loop teardown may also cancel internal child Tasks.
    task.cancel()
    await asyncio.sleep(.01)
    task.cancel()  # A second shutdown request cannot detach the same worker.
    await asyncio.sleep(.01)
    try:
        assert not task.done(), 'Canceled caller released a still-running database read'
        assert task in owner.terminal_tasks and not closed
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        async with asyncio.timeout(1):
            while not closed:
                await asyncio.sleep(.001)
    assert task.cancelled() and closed and not owner.terminal_tasks
    assert not getattr(order, '_managed_terminal_proved', False)


@pytest.mark.asyncio
async def test_terminal_read_uses_detached_fee_snapshot_then_rejects_changed_native_fee(monkeypatch):
    recovery, owner, connector, order, update, _, closed = setup(monkeypatch)
    trade = TradeUpdate('one-fill', order.client_order_id, order.exchange_order_id, order.trading_pair,
                        1700000001, Decimal('100'), Decimal('.1'), Decimal('10'), AddedToCostTradeFee())
    order.update_with_trade_update(trade)
    update.misc_updates['venue_order']['accFillSz'] = '.1'
    in_read, release = threading.Event(), threading.Event()
    fees = []

    def read(connection, identity, trades, **kwargs):
        in_read.set()
        release.wait(.5)
        fees.append(trades[0].fee.percent)
        return True

    monkeypatch.setattr(module, 'durable_fills', read)
    task = asyncio.create_task(recovery.terminal_update(connector, order, update))
    await entered(in_read)
    trade.fee.percent = Decimal('.01')
    release.set()
    with pytest.raises(SubmissionBlocked, match='fill_conflict'):
        await task
    assert fees == [Decimal(0)]
    assert not getattr(order, '_managed_terminal_proved', False)
    assert closed and not owner.terminal_tasks


@pytest.mark.asyncio
async def test_failed_database_read_closes_connection_without_terminal_authority(monkeypatch):
    recovery, owner, connector, order, update, threads, closed = setup(monkeypatch)

    def fail(*args, **kwargs):
        raise IOError('test read unavailable')

    monkeypatch.setattr(module, 'durable_fills', fail)
    with pytest.raises(IOError, match='read unavailable'):
        await recovery.terminal_update(connector, order, update)
    assert threads == closed and not owner.terminal_tasks
    assert not getattr(order, '_managed_terminal_proved', False)


@pytest.mark.asyncio
async def test_terminal_private_reads_retain_one_live_connection(monkeypatch):
    recovery, owner, connector, order, update, _, closed = setup(monkeypatch)
    release = threading.Event()
    lock = threading.Lock()
    active = peak = 0

    def read(*args, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(active, peak)
        try:
            release.wait(.5)
            return True
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(module, 'durable_fills', read)
    tasks = [asyncio.create_task(recovery.terminal_update(connector, order, update)) for _ in range(8)]
    try:
        async with asyncio.timeout(2):
            while active < 1 and not all(task.done() for task in tasks):
                await asyncio.sleep(.001)
        assert active == peak == 1
    finally:
        release.set()
        await asyncio.gather(*tasks)
    assert len(closed) == 8 and not owner.terminal_tasks
