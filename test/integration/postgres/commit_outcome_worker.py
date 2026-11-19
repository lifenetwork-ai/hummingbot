"""Real recorder transaction; parent can lose COMMIT or only its response."""
import asyncio
import json
import os
import sys
import time
from decimal import Decimal
from test.integration.postgres.test_ownership import assistant, intent, send
from test.integration.postgres.test_recorder import create, fill
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy import event
from sqlalchemy.exc import DBAPIError

from hummingbot.client.config.client_config_map import MarketDataCollectionConfigMap
from hummingbot.client.config.runtime_config import RuntimeSettings
from hummingbot.connector.client_order_tracker import ClientOrderTracker
from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.core.data_type.common import OrderType, PositionAction, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState, OrderUpdate, TradeUpdate
from hummingbot.core.event.events import MarketEvent
from hummingbot.core.order_submission_guard import install_guard
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.connection import create_postgres_engine
from hummingbot.model.sql_connection_manager import SQLConnectionManager, SQLConnectionType


def main():
    mode = sys.argv[1]
    url = os.environ['HBOT_IO_TEST_URL']
    engine = create_postgres_engine(RuntimeSettings.from_environment({'DATABASE_URL': url}))
    SQLConnectionManager._postgres_engine = engine
    manager = SQLConnectionManager(None, SQLConnectionType.TRADE_FILLS)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    market = MagicMock()
    market.name = market.display_name = 'okx_perpetual' if mode == 'cleanup' else 'okx'
    market.in_flight_orders = {}
    market.tracking_states = {'order-1': {'state': 'pending'}}
    recorder = MarketsRecorder(manager, [market], 'test.yml', 'pure_market_making',
                               MarketDataCollectionConfigMap(market_data_collection_enabled=False))
    owner = None
    if mode in ('intent', 'async-intent', 'hold', 'terminal', 'async-fill', 'async-ack'):
        os.environ['DATABASE_URL'] = url
        store = BotStateStore(engine, 'hummingbot')
        store.ensure()
        store.control(expected_version=0, desired_state='running', config_file_path='test.yml', config_type='v1-strategy')
        owner = BotOwnership(engine, 'hummingbot')
        assert owner.try_acquire()
        owner.prepare(1, 'test.yml')
        owner.allow_new_orders, owner.reconciling = True, False
        owner.connectors = {'okx': market}
        install_guard(owner)
    if mode in ('terminal', 'async-fill', 'async-ack'):
        terminal_order = intent(SimpleNamespace(market=market, owner=owner))
        if mode != 'async-ack':
            terminal_order.update_exchange_order_id('exchange-1')
            terminal_order.current_state = OrderState.OPEN
            market.tracking_states[terminal_order.client_order_id] = terminal_order.to_json()
            recorder._did_create_order(MarketEvent.BuyOrderCreated.value, market, create())
        tracker = ClientOrderTracker(market)
        tracker.start_tracking_order(terminal_order)
        market.current_timestamp = 1700000001
        state = OrderState.OPEN if mode == 'async-ack' else OrderState.CANCELED
        update = OrderUpdate('BTC-USDT', 1700000001, state, 'order-1', 'exchange-1')
    if mode == 'async-intent':
        new_order = InFlightOrder('order-1', 'BTC-USDT', OrderType.LIMIT, TradeType.BUY,
                                  Decimal('.1'), 1700000000, Decimal('100'))
        market.in_flight_orders['order-1'] = new_order
        market.tracking_states['order-1'] = new_order.to_json()
    target, raw, armed = [None], [None], [True]

    def flushed(session, context):
        target[0] = session.connection()
        raw[0] = target[0].connection.driver_connection

    def committing(connection):
        if connection is target[0] and armed[0]:
            armed[0] = False
            print('COMMIT_READY', flush=True)
            input()  # Parent now cuts traffic; no SQL executes inside this hook.

    event.listen(manager._session_cls, 'after_flush', flushed)
    event.listen(engine, 'commit', committing)
    if mode == 'cleanup':
        evt = fill(trade_type=TradeType.SELL, order_type=OrderType.MARKET,
                   amount=Decimal('.1'), price=Decimal('100'), position=PositionAction.CLOSE.value)
    else:
        evt = fill()

    def operation():
        if mode == 'hold':
            from test.integration.postgres.test_automatic_recovery import executor_info
            from test.integration.postgres.test_position_checkpoint_batch import snapshot
            return recorder.checkpoint_held_positions([snapshot('committed-hold', 'BUY', '.1')], [executor_info('held')])
        if mode in ('terminal', 'async-ack'):
            return loop.run_until_complete(tracker._process_order_update(update))
        if mode == 'async-fill':
            trade = TradeUpdate(evt.exchange_trade_id, evt.order_id, 'exchange-1', evt.trading_pair, evt.timestamp,
                                evt.price, evt.amount, evt.price * evt.amount, evt.trade_fee)
            return loop.run_until_complete(tracker.apply_trade_update(trade))
        if mode == 'intent':
            return intent(SimpleNamespace(market=market, owner=owner))
        if mode == 'async-intent':
            return loop.run_until_complete(owner.admit_order(market, new_order))
        return recorder._did_fill_order(MarketEvent.OrderFilled.value, market, evt)

    try:
        with patch('hummingbot.core.order_submission_guard.fill_committed') as acknowledged:
            started = time.monotonic()
            try:
                operation()
            except DBAPIError as error:
                print(json.dumps(dict(stage='uncertain', error_type=type(error).__name__,
                                      persistence_healthy=manager.persistence_healthy,
                                      acknowledged=acknowledged.called or market.trigger_event.called, closed=bool(raw[0].closed),
                                      elapsed_seconds=time.monotonic() - started)), flush=True)
            else:
                raise AssertionError('Cut COMMIT unexpectedly returned success')
            input()  # Restore the network before proving the local failure latch.
            transport_calls = 0
            try:
                if mode in ('intent', 'async-intent', 'terminal', 'async-fill', 'async-ack'):
                    rest, transport = assistant()
                    try:
                        loop.run_until_complete(send(rest))
                    finally:
                        transport_calls = transport.call.await_count
                else:
                    operation()
            except RuntimeError as error:
                blocked = 'Persistence failed' in str(error)
            else:
                blocked = False
            print(json.dumps(dict(stage='blocked', blocked=blocked, transport_calls=transport_calls,
                                  acknowledged=acknowledged.called or market.trigger_event.called)), flush=True)
    finally:
        event.remove(manager._session_cls, 'after_flush', flushed)
        event.remove(engine, 'commit', committing)
        if owner is not None:
            owner.release()
        install_guard(None)
        engine.dispose()
        loop.close()


if __name__ == '__main__':
    main()
