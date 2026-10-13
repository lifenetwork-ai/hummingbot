"""Actual recorder callbacks capture on the loop and persist on the worker."""
import asyncio
import threading
from test.hummingbot.strategy_v2.life_liquidity.storage.test_recorder_application import assembly
from test.hummingbot.strategy_v2.life_liquidity.storage.test_recorder_events import created, fill
from unittest.mock import MagicMock

import pytest
from sqlalchemy import event as sql_event

from hummingbot.core.event.events import MarketEvent, OrderCancelledEvent
from hummingbot.model.order_status import OrderStatus
from hummingbot.model.trade_fill import TradeFill
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


@pytest.mark.asyncio
async def test_actual_event_forwarders_use_worker_and_confirmed_cache_updates(tmp_path, monkeypatch):
    app, reporting, repo, runner, action, _, _ = await assembly(tmp_path, monkeypatch)
    connector = app.connector
    connector.add_exchange_order_ids_from_market_recorder = MagicMock()
    connector.add_trade_fills_from_market_recorder = MagicMock()
    main_thread = threading.get_ident()
    engine = repo._recorder.sql_manager.engine

    def no_loop_sql(*args):
        assert threading.get_ident() != main_thread
    sql_event.listen(engine, 'before_cursor_execute', no_loop_sql)
    try:
        assert await reporting.before_dispatch(action)
        # Repository preparation gives the wire an immutable native identity.
        runner.execute_action(action)
        await reporting.drain()
        executor = runner.active_executors['life'][0]
        executor.place_open_order()
        await app.drain()
        wire = executor.recovery_order_ids()[0]
        await reporting.install_event_sources((connector,))
        connector.add_exchange_order_ids_from_market_recorder.reset_mock()
        connector.add_trade_fills_from_market_recorder.reset_mock()
        evt = created()
        evt.order_id = wire
        connector.trigger_event(MarketEvent.BuyOrderCreated, evt)
        assert not connector.add_exchange_order_ids_from_market_recorder.called
        await reporting.drain()
        connector.add_exchange_order_ids_from_market_recorder.assert_called_once_with({'exchange-1': wire})
        trade = fill()._replace(order_id=wire)
        connector.trigger_event(MarketEvent.OrderFilled, trade)
        await reporting.drain()
        connector.trigger_event(MarketEvent.OrderFilled, trade)  # SDK duplicate uses original command/cut.
        await reporting.drain()
        connector.trigger_event(MarketEvent.OrderCancelled, OrderCancelledEvent(4, wire))
        await reporting.drain()
        count = await app.store._call(lambda: _counts(repo._recorder))
        assert count == (1, 3)
        # Raw filled/canceled events never book money or terminalize the WAL.
        assert (await app.store.attempt(executor.config.id, app.binding)).state.value == 'ACKED'
        assert (await app.store.snapshot(app.binding)).balance.reserved_quote_usdt == 2
    finally:
        sql_event.remove(engine, 'before_cursor_execute', no_loop_sql)
        await reporting.close()
        await app.close()
        await app.store.close()


def _counts(recorder):
    with recorder.sql_manager.get_new_session() as session:
        return session.query(TradeFill).count(), session.query(OrderStatus).count()


@pytest.mark.asyncio
async def test_blocked_event_sql_keeps_loop_and_stop_responsive(tmp_path, monkeypatch):
    app, reporting, repo, runner, action, _, _ = await assembly(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    connector = app.connector
    connector.add_exchange_order_ids_from_market_recorder = MagicMock()
    original = repo._recorder.project_market_event

    def blocked(effect):
        entered.set()
        assert release.wait(5)
        return original(effect)
    monkeypatch.setattr(repo._recorder, 'project_market_event', blocked)
    try:
        assert await reporting.before_dispatch(action)
        runner.execute_action(action)
        await reporting.drain()
        executor = runner.active_executors['life'][0]
        executor.place_open_order()
        await app.drain()
        wire = executor.recovery_order_ids()[0]
        await reporting.install_event_sources((connector,))
        connector.add_exchange_order_ids_from_market_recorder.reset_mock()
        evt = created()
        evt.order_id = wire
        connector.trigger_event(MarketEvent.BuyOrderCreated, evt)
        assert await asyncio.to_thread(entered.wait, 3)
        app.controller.stop()
        assert not app.runtime._local_ready()
        assert not connector.add_exchange_order_ids_from_market_recorder.called
        release.set()
        await reporting.drain()
        assert connector.add_exchange_order_ids_from_market_recorder.called
        assert not app.runtime._local_ready()
    finally:
        release.set()
        await reporting.close()
        await app.close()
        await app.store.close()


@pytest.mark.asyncio
async def test_wrong_uid_denies_source_install_without_attaching_listeners(tmp_path, monkeypatch):
    app, reporting, repo, _, _, _, _ = await assembly(tmp_path, monkeypatch)

    async def wrong_uid():
        return '999'
    monkeypatch.setattr(app.connector, 'get_account_uid', wrong_uid)
    try:
        with pytest.raises(StorageError, match='RECORDER_EVENT_UID_MISMATCH'):
            await reporting.install_event_sources((app.connector,))
        assert repo._recorder._repository_event_sink is None
        assert not reporting.ready and not app.runtime._local_ready()
    finally:
        await reporting.close()
        await app.close()
        await app.store.close()
