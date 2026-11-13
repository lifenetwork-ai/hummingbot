"""Missing terminal WS evidence must not leave a managed cancel on a120s poll."""
import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(params=[OkxExchange, OkxPerpetualDerivative])
async def connector(request):
    prefix = 'okx' if request.param is OkxExchange else 'okx_perpetual'
    keys = {prefix + '_' + field: 'offline' for field in ('api_key', 'secret_key', 'passphrase')}
    exchange = request.param(trading_pairs=['BTC-USDT'], trading_required=False, **keys)
    exchange._user_stream_tracker = SimpleNamespace(last_recv_time=125)
    exchange._set_current_timestamp(121)
    exchange.tick(121)
    exchange._poll_notifier.clear()  # A normal account poll just finished.
    yield exchange
    await exchange._web_assistants_factory.close()


def track(exchange, *, managed=True, state=OrderState.PENDING_CANCEL, proof_pending=False):
    order = InFlightOrder('cancel', 'BTC-USDT', OrderType.LIMIT_MAKER, TradeType.BUY,
                          Decimal('.1'), 120, price=Decimal('100'), exchange_order_id='venue-cancel')
    order.current_state = state
    order._managed_recovery_order = managed
    order._terminal_proof_pending = proof_pending
    exchange._order_tracker.start_tracking_order(order)
    return order


@pytest.mark.parametrize('proof_pending', [False, True])
async def test_live_stream_with_missing_terminal_evidence_polls_within_five_seconds(connector, proof_pending):
    # An unrelated WS message is fresh; the particular order event never arrives.
    order = track(connector, state=OrderState.OPEN if proof_pending else OrderState.PENDING_CANCEL,
                  proof_pending=proof_pending)
    assert connector._get_poll_interval(125) == 5
    for stamp in range(122, 126):
        connector.tick(stamp)
    assert connector._poll_notifier.is_set()
    assert not order.is_done and 'cancel' in connector.in_flight_orders


@pytest.mark.parametrize('managed,state', [(False, OrderState.PENDING_CANCEL), (True, OrderState.OPEN),
                                           (True, OrderState.CANCELED)])
async def test_ordinary_or_finished_orders_keep_the_native_healthy_stream_interval(connector, managed, state):
    track(connector, managed=managed, state=state)
    assert connector._get_poll_interval(125) == 120
    connector.tick(125)
    assert not connector._poll_notifier.is_set()


@pytest.mark.asyncio
async def test_cancel_ack_keeps_native_order_pending_until_later_proof(connector, monkeypatch):
    from hummingbot.core import order_submission_guard as guard
    order = track(connector, state=OrderState.OPEN)
    monkeypatch.setattr(guard, 'managed_terminal_order', lambda item: item is order)
    connector._place_cancel = AsyncMock(return_value=True)
    assert await connector._execute_order_cancel_and_process_update(order)
    await asyncio.sleep(0)
    assert order.current_state == OrderState.PENDING_CANCEL and not order.is_done
    assert connector._get_poll_interval(125) == 5
    assert order.executed_amount_base == 0 and not order.order_fills


@pytest.mark.asyncio
async def test_poll_request_arriving_during_a_read_is_not_discarded(connector):
    entered, release, followed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
        else:
            followed.set()

    connector._update_time_synchronizer = AsyncMock()
    connector._status_polling_loop_fetch_updates = fetch
    connector._poll_notifier.set()
    task = asyncio.create_task(connector._status_polling_loop())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        # Several requests coalesce while the sole native reader is occupied.
        for _ in range(20):
            connector._poll_notifier.set()
        release.set()
        await asyncio.wait_for(followed.wait(), .1)
        await asyncio.sleep(0)
        assert calls == 2
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_failed_poll_keeps_the_native_retry_without_waiting_for_another_tick(connector):
    followed = asyncio.Event()
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError('transient account read')
        followed.set()

    connector._update_time_synchronizer = AsyncMock()
    connector._status_polling_loop_fetch_updates = fetch
    connector._sleep = AsyncMock()  # The existing backoff, not a wall-clock test delay.
    connector._poll_notifier.set()
    task = asyncio.create_task(connector._status_polling_loop())
    try:
        await asyncio.wait_for(followed.wait(), .1)
        await asyncio.sleep(0)
        assert calls == 2 and connector._sleep.await_count == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
