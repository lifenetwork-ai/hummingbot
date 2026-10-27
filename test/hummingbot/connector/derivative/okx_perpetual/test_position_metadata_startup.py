import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from bidict import bidict

from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative


@pytest_asyncio.fixture
async def exchange():
    connector = OkxPerpetualDerivative(trading_pairs=['LIFE-USDT'], trading_required=False)
    connector._set_trading_pair_symbol_map(None)
    return connector


def instruments(size='.1'):
    return {'code': '0', 'data': [dict(instId='LIFE-USDT-SWAP', instType='SWAP', ctType='linear', state='live',
                                       ctValCcy='LIFE', settleCcy='USDT', ctVal=size,
                                       minSz='1', lotSz='1', tickSz='.001')]}


def position(amount='6'):
    return dict(instId='LIFE-USDT-SWAP', posSide='long', pos=amount, avgPx='100', lever='2', upl='0')


@pytest.mark.asyncio
async def test_cold_position_uses_contract_metadata_from_its_lazy_symbol_request(exchange):
    exchange._api_get = AsyncMock(return_value=instruments())
    await exchange._process_account_position_event(position())
    assert next(iter(exchange.account_positions.values())).amount == Decimal('.6')
    exchange._api_get.assert_awaited_once()
    # Symbol metadata does not bypass full trading-rule/account readiness.
    assert exchange.trading_rules == {}


@pytest.mark.asyncio
async def test_warm_reconnect_processes_latest_snapshot_without_another_metadata_request(exchange):
    exchange._api_get = AsyncMock(return_value=instruments())
    await exchange._process_account_position_event(position())
    await exchange.stop_network()
    await exchange._process_account_position_event(position('2'))
    assert next(iter(exchange.account_positions.values())).amount == Decimal('.2')
    exchange._api_get.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_during_cold_metadata_fetch_does_not_publish_partial_state_or_lose_retry(exchange):
    entered, released = asyncio.Event(), asyncio.Event()

    async def delayed(**kwargs):
        entered.set()
        await released.wait()
        return instruments()
    exchange._api_get = AsyncMock(side_effect=delayed)
    task = asyncio.create_task(exchange._process_account_position_event(position()))
    exchange._user_stream_event_listener_task = task
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        assert not exchange.account_positions and not exchange._contract_sizes
        await exchange.stop_network()
        await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled()
        assert not exchange.trading_pair_symbol_map_ready() and not exchange._contract_sizes
    finally:
        released.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    await exchange._process_account_position_event(position('3'))
    assert next(iter(exchange.account_positions.values())).amount == Decimal('.3')


@pytest.mark.asyncio
async def test_rest_snapshot_started_before_cold_ws_cannot_erase_its_new_position(exchange):
    rest_started, allow_rest = asyncio.Event(), asyncio.Event()
    exchange._set_trading_pair_symbol_map(bidict({'LIFE-USDT-SWAP': 'LIFE-USDT'}))

    async def request(**kwargs):
        if kwargs.get('params') == {'instType': 'SWAP'}:
            return instruments()
        rest_started.set()
        await allow_rest.wait()
        return {'code': '0', 'data': []}
    exchange._api_get = AsyncMock(side_effect=request)
    task = asyncio.create_task(exchange._update_positions())
    await rest_started.wait()
    # A reconnect may initialize the lazy symbol path before rule polling finishes.
    exchange._set_trading_pair_symbol_map(None)
    try:
        await exchange._process_account_position_event(position())
    finally:
        allow_rest.set()
        await task
    assert next(iter(exchange.account_positions.values())).amount == Decimal('.6')


@pytest.mark.asyncio
@pytest.mark.parametrize('size', ['0', '-1', 'NaN', 'Infinity', None, '', 'bad'])
async def test_invalid_contract_size_cannot_publish_a_new_symbol_mapping(exchange, size):
    exchange._api_get = AsyncMock(return_value=instruments(size))
    with pytest.raises(ValueError, match='contract size'):
        await exchange._process_account_position_event(position())
    assert not exchange.trading_pair_symbol_map_ready()
    assert not exchange._contract_sizes


@pytest.mark.asyncio
async def test_listener_applies_early_position_then_processes_following_account_frame(exchange):
    async def events():
        yield {'arg': {'channel': 'positions'}, 'data': [position()]}
        yield {'arg': {'channel': 'account'}, 'data': [{'details': [dict(ccy='USDT', eq='12', availEq='10',
                                                                         cashBal='12', availBal='10', frozenBal='2')]}]}
    exchange._iter_user_event_queue = events
    exchange._api_get = AsyncMock(return_value=instruments())
    exchange._sleep = AsyncMock()
    await exchange._user_stream_event_listener()
    assert next(iter(exchange.account_positions.values())).amount == Decimal('.6')
    assert exchange.get_balance('USDT') == 12
    exchange._sleep.assert_not_awaited()
