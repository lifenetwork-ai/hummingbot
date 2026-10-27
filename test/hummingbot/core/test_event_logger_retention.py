import asyncio
import gc
import weakref
from decimal import Decimal

import pytest

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee
from hummingbot.core.event.event_logger import EventLogger
from hummingbot.core.event.events import MarketEvent, OrderFilledEvent


def fill(index):
    return OrderFilledEvent(index, f'order-{index}', 'LIFE-USDT', TradeType.BUY,
                            OrderType.LIMIT, Decimal('0.1'), Decimal('1'), AddedToCostTradeFee())


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['timeout', 'cancel', 'notified_cancel'])
async def test_abandoned_waiter_does_not_retain_notifier_or_result(monkeypatch, outcome):
    logger = EventLogger()
    original = asyncio.Event
    references = []

    def event():
        value = original()
        references.append(weakref.ref(value))
        return value
    monkeypatch.setattr(asyncio, 'Event', event)
    task = asyncio.create_task(logger.wait_for(OrderFilledEvent, timeout_seconds=.001 if outcome == 'timeout' else 60))
    await asyncio.sleep(0)
    if outcome == 'notified_cancel':
        logger(fill(1))
    if outcome != 'timeout':
        task.cancel()
    with pytest.raises((asyncio.CancelledError, asyncio.TimeoutError)):
        await task
    del task
    await asyncio.sleep(0)
    gc.collect()
    assert references and all(reference() is None for reference in references)


@pytest.mark.asyncio
async def test_successful_waiter_still_returns_exact_event():
    logger = EventLogger()
    task = asyncio.create_task(logger.wait_for(OrderFilledEvent))
    await asyncio.sleep(0)
    value = fill(1)
    logger(value)
    assert await task is value


def test_explicit_recent_fill_limit_does_not_change_legacy_default():
    bounded, legacy = EventLogger(), EventLogger()
    bounded.set_fill_event_limit(1000)
    for index in range(10000):
        value = fill(index)
        bounded(value)
        legacy(value)
    assert len(bounded.event_log) == 1000
    assert bounded.event_log[0].order_id == 'order-9000'
    assert len(legacy.event_log) == 10000
    bounded.set_fill_event_limit(10)
    assert len(bounded.event_log) == 10
    bounded.clear()
    assert bounded.event_log == []


@pytest.mark.parametrize('snapshot,limits', [(False, {}), (True, {'ConnectorBase': {'LIFE': Decimal('10')}})])
def test_connector_refuses_truncation_when_balance_math_requires_full_history(snapshot, limits):
    connector = ConnectorBase(balance_asset_limit=limits)
    connector._real_time_balance_update = snapshot
    with pytest.raises(ValueError):
        connector.set_event_log_fill_limit(1000)


def test_connector_bounds_only_recent_diagnostics_when_history_is_not_a_balance_input():
    connector = ConnectorBase()
    connector.set_event_log_fill_limit(1000)
    for index in range(1500):
        connector.trigger_event(MarketEvent.OrderFilled, fill(index))
    assert len(connector.event_logs) == 1000
    assert connector.event_logs[0].order_id == 'order-500'
