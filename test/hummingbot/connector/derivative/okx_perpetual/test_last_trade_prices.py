"""A missing ticker must not discard other markets or fabricate freshness."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
from hummingbot.core.data_type.order_book import OrderBook


@pytest_asyncio.fixture
async def exchange():
    connector = OkxPerpetualDerivative(trading_pairs=['BTC-USDT'], trading_required=False)
    try:
        yield connector
    finally:
        await connector._web_assistants_factory.close()


def response(*tickers):
    return {'code': '0', 'data': list(tickers)}


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['', None, ' ', 'not-a-price', 'NaN', 'Infinity', '-Infinity', '0', '-1', True, [], {}, '1e999'])
async def test_bad_ticker_does_not_discard_valid_market_prices(exchange, invalid):
    exchange._api_get = AsyncMock(return_value=response(
        {'instId': 'BTC-USDT-SWAP', 'last': '70000.25'},
        {'instId': 'NEW-USDT-SWAP', 'last': invalid},
        {'instId': 'ETH-USDT-SWAP', 'last': '2000'},
    ))
    assert await exchange.get_last_traded_prices() == {'BTC-USDT': 70000.25, 'ETH-USDT': 2000.0}


@pytest.mark.asyncio
async def test_bulk_prices_filter_requested_markets_and_ignore_malformed_rows(exchange):
    exchange._api_get = AsyncMock(return_value=response(
        None, {}, {'last': '10'}, {'instId': None, 'last': '10'}, {'instId': 'BTC-USDT-SWAP'},
        {'instId': 'BTC-USDT', 'last': '1'},
        {'instId': 'ETH-USDT-SWAP', 'last': '2000'},
        {'instId': 'BTC-USDT-SWAP', 'last': '70000'},
    ))
    assert await exchange.get_last_traded_prices(['BTC-USDT']) == {'BTC-USDT': 70000.0}


@pytest.mark.asyncio
async def test_no_usable_requested_price_is_unavailable_even_if_other_markets_have_prices(exchange):
    exchange._api_get = AsyncMock(return_value=response(
        {'instId': 'BTC-USDT-SWAP', 'last': ''},
        {'instId': 'ETH-USDT-SWAP', 'last': '2000'},
    ))
    with pytest.raises(IOError, match='No usable'):
        await exchange.get_last_traded_prices(['BTC-USDT'])


@pytest.mark.asyncio
@pytest.mark.parametrize('data', [[], None, {}])
async def test_missing_or_invalid_batch_is_unavailable(exchange, data):
    exchange._api_get = AsyncMock(return_value={'code': '0', 'data': data})
    with pytest.raises(IOError):
        await exchange.get_last_traded_prices()


@pytest.mark.asyncio
async def test_partial_requested_results_do_not_invent_a_missing_price(exchange):
    exchange._api_get = AsyncMock(return_value=response(
        {'instId': 'BTC-USDT-SWAP', 'last': '70000'},
        {'instId': 'ETH-USDT-SWAP', 'last': ''},
    ))
    assert await exchange.get_last_traded_prices(['BTC-USDT', 'ETH-USDT']) == {'BTC-USDT': 70000.0}


@pytest.mark.asyncio
async def test_single_price_selects_requested_instrument_instead_of_first_bulk_ticker(exchange):
    exchange._api_get = AsyncMock(return_value=response(
        {'instId': 'ETH-USDT-SWAP', 'last': '2000'},
        {'instId': 'BTC-USDT-SWAP', 'last': '70000'},
    ))
    assert await exchange._get_last_traded_price('BTC-USDT') == 70000.0


@pytest.mark.asyncio
async def test_empty_request_does_not_fetch_any_tickers(exchange):
    exchange._api_get = AsyncMock()
    assert await exchange.get_last_traded_prices([]) == {}
    exchange._api_get.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [IOError('transport unavailable'), asyncio.CancelledError()])
async def test_transport_failure_and_cancellation_are_not_converted_to_prices(exchange, error):
    exchange._api_get = AsyncMock(side_effect=error)
    with pytest.raises(type(error)):
        await exchange.get_last_traded_prices(['BTC-USDT'])


@pytest.mark.asyncio
@pytest.mark.parametrize('requested_last', ['70000', ''])
async def test_native_tracker_updates_valid_price_or_preserves_old_freshness_with_backoff(exchange, requested_last):
    exchange._api_get = AsyncMock(return_value=response(
        {'instId': 'BTC-USDT-SWAP', 'last': requested_last},
        {'instId': 'NEW-USDT-SWAP', 'last': ''},
        {'instId': 'ETH-USDT-SWAP', 'last': '2000'},
    ))
    tracker = exchange.order_book_tracker
    book = OrderBook()
    book.last_trade_price = 100
    book.last_trade_price_rest_updated = 10
    tracker._order_books['BTC-USDT'] = book
    tracker._order_books_initialized.set()
    logger = MagicMock()
    with patch.object(tracker, 'logger', return_value=logger), \
            patch('hummingbot.core.data_type.order_book_tracker.time.perf_counter', return_value=1000), \
            patch('hummingbot.core.data_type.order_book_tracker.asyncio.sleep', AsyncMock(side_effect=asyncio.CancelledError)) as sleep:
        with pytest.raises(asyncio.CancelledError):
            await tracker._update_last_trade_prices_loop()
    if requested_last:
        assert book.last_trade_price == 70000.0
        assert book.last_trade_price_rest_updated == 1000
        sleep.assert_awaited_once_with(1)
        logger.network.assert_not_called()
    else:
        assert book.last_trade_price == 100
        assert book.last_trade_price_rest_updated == 10
        sleep.assert_awaited_once_with(30)
        logger.network.assert_called_once()
    exchange._api_get.assert_awaited_once()
