"""Actual REST gate + PG intent + native OKX tracker across a busy response."""
import asyncio
import json
from decimal import Decimal
from test.integration.postgres.test_ownership import assistant, owned as owned_fixture
from unittest.mock import AsyncMock, patch

import pytest
from bidict import bidict

from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.connector.okx_order_status import OKXOrderNotFoundError
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import OrderState, OrderUpdate, TradeUpdate
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount
from hummingbot.core.order_submission_guard import SubmissionBlocked
from hummingbot.model.order import Order
from hummingbot.model.trade_fill import TradeFill

owned = owned_fixture


@pytest.fixture
def native(owned):
    exchange = OkxExchange(okx_api_key='offline', okx_secret_key='offline', okx_passphrase='offline',
                           trading_pairs=['BTC-USDT'])
    exchange._set_current_timestamp(1700000000)
    exchange._set_trading_pair_symbol_map(bidict({'BTC-USDT': 'BTC-USDT'}))
    exchange._trading_rules['BTC-USDT'] = TradingRule(
        'BTC-USDT', min_order_size=Decimal('.00001'), min_price_increment=Decimal('.1'),
        min_base_amount_increment=Decimal('.00001'), min_notional_size=Decimal('1'))
    recorder = MarketsRecorder(owned.manager, [exchange], 'test.yml', 'pure_market_making',
                               owned.recorder._market_data_collection_config)
    recorder.start()
    owned.owner.connectors = {'okx': exchange}
    rest, transport = assistant()
    response = transport.call.return_value
    response.status = 429
    response.text = AsyncMock(return_value=json.dumps({'code': '50013', 'msg': 'System is busy, please try again later.'}))
    response.json = AsyncMock(return_value={'code': '50013', 'msg': 'System is busy, please try again later.'})
    with patch.object(exchange._web_assistants_factory, 'get_rest_assistant', AsyncMock(return_value=rest)):
        owned.loop.run_until_complete(exchange._create_order(
            TradeType.SELL, 'busy-order', 'BTC-USDT', Decimal('.1'), OrderType.LIMIT_MAKER, Decimal('100')))
        # Let native tracker events run: the Red implementation marks FAILED here.
        owned.loop.run_until_complete(asyncio.sleep(0))
        yield owned, exchange, recorder, transport
    recorder.stop()
    owned.loop.run_until_complete(exchange._web_assistants_factory.close())


def test_50013_preserves_uncertain_order_in_tracker_and_durable_halt(native):
    context, exchange, recorder, transport = native
    assert transport.call.await_count == 1
    order = exchange._order_tracker.fetch_order('busy-order')
    assert order.current_state == OrderState.PENDING_CREATE
    assert 'busy-order' in exchange.in_flight_orders
    with context.manager.get_new_session() as db:
        assert db.get(Order, 'busy-order').creation_pending
        assert db.query(TradeFill).count() == 0
    assert context.store.read()['recovery_state']['halt']
    assert not context.owner.allow_new_orders
    with pytest.raises(SubmissionBlocked):
        context.owner.check_owner(new_risk=True)
    assert transport.call.await_count == 1  # No automatic POST retry.


def test_51603_after_50013_is_not_terminal_or_permission_to_resubmit(native):
    context, exchange, recorder, transport = native
    order = exchange._order_tracker.fetch_order('busy-order')
    with patch.object(exchange, '_request_order_status', AsyncMock(side_effect=OKXOrderNotFoundError('51603'))):
        with pytest.raises(OKXOrderNotFoundError):
            context.loop.run_until_complete(BotOwnership._refresh_orders(exchange, [order]))
    with context.manager.get_new_session() as db:
        assert db.get(Order, 'busy-order').creation_pending
    assert context.store.read()['recovery_state']['halt']
    assert order.current_state == OrderState.PENDING_CREATE
    assert transport.call.await_count == 1


@pytest.mark.parametrize('state', [OrderState.OPEN, OrderState.PARTIALLY_FILLED, OrderState.FILLED])
def test_late_native_acceptance_or_fill_is_recorded_without_second_post(native, state):
    context, exchange, recorder, transport = native
    order = exchange._order_tracker.fetch_order('busy-order')
    filled = state != OrderState.OPEN
    amount = Decimal('.04') if state == OrderState.PARTIALLY_FILLED else Decimal('.1')
    status = OrderUpdate(trading_pair='BTC-USDT', update_timestamp=1700000001,
                         new_state=state,
                         client_order_id='busy-order', exchange_order_id='venue-busy')
    trade = TradeUpdate(trade_id='trade-busy', client_order_id='busy-order', exchange_order_id='venue-busy',
                        trading_pair='BTC-USDT', fill_timestamp=1700000001, fill_price=Decimal('100'),
                        fill_base_amount=amount, fill_quote_amount=amount * Decimal('100'),
                        fee=AddedToCostTradeFee(flat_fees=[TokenAmount('USDT', Decimal('.01'))]))
    with patch.object(exchange, '_request_order_status', AsyncMock(return_value=status)), \
            patch.object(exchange, '_all_trade_updates_for_order', AsyncMock(return_value=[trade] if filled else [])):
        context.loop.run_until_complete(BotOwnership._refresh_orders(exchange, [order]))
        # Repeated REST/WS observation must not duplicate this physical fill.
        context.loop.run_until_complete(BotOwnership._refresh_orders(exchange, [order]))
    assert order.current_state == status.new_state
    with context.manager.get_new_session() as db:
        saved = db.get(Order, 'busy-order')
        assert not saved.creation_pending and saved.exchange_order_id == 'venue-busy'
        assert db.query(TradeFill).count() == int(filled)
        if filled:
            fill = db.query(TradeFill).one()
            assert fill.amount == amount and fill.price == Decimal('100')
            assert fill.trade_fee['flat_fees'] == [{'token': 'USDT', 'amount': '0.01'}]
    assert context.store.read()['recovery_state']['halt']  # Positive readback is not an operator reset.
    assert transport.call.await_count == 1
