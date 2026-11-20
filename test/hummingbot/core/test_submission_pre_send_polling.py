import asyncio
from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio

from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core import order_submission_guard as gates
from hummingbot.core.data_type.common import OrderType, PositionAction, TradeType

PAIR = 'BTC-USDT'
ORDER_ID = 'local-order'


@pytest_asyncio.fixture(params=['okx', 'okx_perpetual'])
async def connector(request, monkeypatch):
    if request.param == 'okx':
        market = OkxExchange('', '', '', trading_pairs=[PAIR], trading_required=False)
    else:
        market = OkxPerpetualDerivative(trading_pairs=[PAIR], trading_required=False)
    market._trading_rules[PAIR] = TradingRule(
        PAIR, min_price_increment=Decimal('.01'), min_base_amount_increment=Decimal('.0001'))
    market._set_current_timestamp(1700000000)
    monkeypatch.setattr(market, '_request_order_update', AsyncMock(return_value={'code': '51603', 'data': []}))
    return market


@pytest.fixture
def owner(monkeypatch):
    @asynccontextmanager
    async def order_write_scope():
        yield

    guard = SimpleNamespace(admit_order=AsyncMock(), order_failed=Mock(return_value=False),
                            order_write_scope=order_write_scope, sent_intents=set())
    monkeypatch.setattr(gates, 'postgres_requested', lambda: True)
    monkeypatch.setattr(gates, '_guard', guard)
    return guard


def create_order(connector):
    position = {'position_action': PositionAction.CLOSE} if connector.name == 'okx_perpetual' else {}
    return connector._create_order(
        TradeType.SELL, ORDER_ID, PAIR, Decimal('.0002'), OrderType.LIMIT_MAKER, Decimal('72938.67'), **position)


async def poll_past_lost_order_limit(connector):
    for _ in range(connector._order_tracker.lost_order_count_limit + 1):
        await connector._update_orders()


@pytest.mark.asyncio
async def test_waiting_for_admission_never_polls_or_loses_the_local_order(connector, owner, monkeypatch):
    waiting, reject = asyncio.Event(), asyncio.Event()

    async def wait_for_admission(connector, order):
        waiting.set()
        await reject.wait()
        raise gates.MarketReadinessBlocked('reduction_market_not_ready')

    owner.admit_order.side_effect = wait_for_admission
    place = AsyncMock()
    monkeypatch.setattr(connector, '_place_order', place)
    task = asyncio.create_task(create_order(connector))
    try:
        await asyncio.wait_for(waiting.wait(), 2)
        order = connector.in_flight_orders[ORDER_ID]
        await poll_past_lost_order_limit(connector)
        connector._request_order_update.assert_not_awaited()
        assert order.is_pending_create and ORDER_ID not in connector._order_tracker.lost_orders
        reject.set()
        await task
        await asyncio.sleep(0)
        place.assert_not_awaited()
        assert order.is_failure and ORDER_ID not in connector.in_flight_orders
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_polling_resumes_after_send_is_authorized_before_ack(connector, owner, monkeypatch):
    waiting, authorize, sent, acknowledge = (asyncio.Event() for _ in range(4))

    async def place_order(**kwargs):
        waiting.set()
        await authorize.wait()
        owner.sent_intents.add(ORDER_ID)
        sent.set()
        await acknowledge.wait()
        return 'exchange-id', connector.current_timestamp

    monkeypatch.setattr(connector, '_place_order', place_order)
    task = asyncio.create_task(create_order(connector))
    try:
        await asyncio.wait_for(waiting.wait(), 2)
        order = connector.in_flight_orders[ORDER_ID]
        await connector._update_orders()
        connector._request_order_update.assert_not_awaited()
        authorize.set()
        await asyncio.wait_for(sent.wait(), 2)
        await poll_past_lost_order_limit(connector)
        assert connector._request_order_update.await_count == connector._order_tracker.lost_order_count_limit + 1
        assert order.is_pending_create and ORDER_ID not in connector._order_tracker.lost_orders
        acknowledge.set()
        await task
        await asyncio.sleep(0)
        assert order.exchange_order_id == 'exchange-id'
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_restored_pending_order_can_still_be_polled_without_exchange_id(connector, owner):
    connector.start_tracking_order(
        ORDER_ID, None, PAIR, TradeType.SELL, Decimal('72938.67'), Decimal('.0002'), OrderType.LIMIT_MAKER)
    snapshot = connector.tracking_states
    connector.stop_tracking_order(ORDER_ID)
    connector.restore_tracking_states(snapshot)

    await connector._update_orders()

    connector._request_order_update.assert_awaited_once_with(order=connector.in_flight_orders[ORDER_ID])


@pytest.mark.asyncio
async def test_file_storage_still_polls_while_waiting_for_ack(connector, monkeypatch):
    waiting, acknowledge = asyncio.Event(), asyncio.Event()

    async def place_order(**kwargs):
        waiting.set()
        await acknowledge.wait()
        return 'exchange-id', connector.current_timestamp

    monkeypatch.setattr(gates, 'postgres_requested', lambda: False)
    monkeypatch.setattr(connector, '_place_order', place_order)
    task = asyncio.create_task(create_order(connector))
    try:
        await asyncio.wait_for(waiting.wait(), 2)
        await connector._update_orders()
        connector._request_order_update.assert_awaited_once_with(order=connector.in_flight_orders[ORDER_ID])
        acknowledge.set()
        await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
