"""Venue proof uses native Spot/contract conversions and exact original fees."""
from copy import deepcopy
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.core.automatic_recovery import proof_covers_local, read_order_proof
from hummingbot.core.data_type.common import OrderType, PositionAction, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState, OrderUpdate
from hummingbot.core.order_submission_guard import SubmissionBlocked


def venue(perpetual):
    connector = OkxPerpetualDerivative(trading_pairs=['BTC-USDT'], trading_required=False) if perpetual else OkxExchange(
        okx_api_key='offline', okx_secret_key='offline', okx_passphrase='offline', trading_pairs=['BTC-USDT'], trading_required=False)
    if perpetual:
        connector._contract_sizes['BTC-USDT'] = Decimal('.01')
    order = InFlightOrder('client', 'BTC-USDT', OrderType.LIMIT_MAKER, TradeType.BUY, Decimal('.1'),
                          1700000000, Decimal(100), position=PositionAction.OPEN if perpetual else PositionAction.NIL)
    status = dict(clOrdId='client', ordId='venue', instId='BTC-USDT-SWAP' if perpetual else 'BTC-USDT',
                  side='buy', posSide='long' if perpetual else 'net', ordType='post_only', px='100',
                  sz='10' if perpetual else '.1', accFillSz='10' if perpetual else '.1',
                  state='filled', uTime='1700000001000')
    fill = dict(tradeId='trade', billId='bill', ordId='venue', instId=status['instId'], side='buy',
                posSide=status['posSide'], fillPx='100', fillSz=status['sz'],
                fee='-.0001234567890123456789', feeCcy='USDT', ts='1700000001000')
    connector._request_order_update = AsyncMock(side_effect=lambda *a, **k: {'code': '0', 'data': [deepcopy(status)]})
    connector._request_order_fills = AsyncMock(side_effect=lambda *a, **k: {'code': '0', 'data': [deepcopy(fill)]})
    return connector, order, status, fill


@pytest.mark.asyncio
@pytest.mark.parametrize('perpetual', [False, True])
async def test_full_proof_keeps_contract_quantity_and_original_fee_exact(perpetual):
    connector, order, _, _ = venue(perpetual)
    status, trades = await read_order_proof(connector, order)
    assert status.new_state == OrderState.FILLED and status.exchange_order_id == 'venue'
    assert len(trades) == 1 and trades[0].fill_base_amount == Decimal('.1')
    assert trades[0].fee.flat_fees[0].amount == Decimal('.0001234567890123456789')
    assert not order.order_fills  # A read proof never applies its own financial effects.


@pytest.mark.asyncio
@pytest.mark.parametrize('perpetual', [False, True])
@pytest.mark.parametrize('field,value', [('clOrdId', 'other'), ('ordId', ''), ('instId', 'ETH-USDT'),
                                         ('side', 'sell'), ('px', '101'), ('sz', '999'), ('ordType', 'market')])
async def test_status_identity_or_economic_mismatch_is_not_recovery_authority(perpetual, field, value):
    connector, order, status, _ = venue(perpetual)
    status[field] = value
    with pytest.raises(SubmissionBlocked):
        await read_order_proof(connector, order)
    assert not order.order_fills


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('ordId', 'wrong'), ('instId', 'ETH-USDT-SWAP'),
                                         ('posSide', 'short'), ('side', 'sell'), ('fee', 'NaN')])
async def test_fill_scope_or_invalid_fee_never_enters_native_tracker(field, value):
    connector, order, _, fill = venue(True)
    fill[field] = value
    with pytest.raises(SubmissionBlocked):
        await read_order_proof(connector, order)
    assert not order.order_fills


@pytest.mark.asyncio
async def test_lagging_fill_visibility_is_retryable_not_absence():
    connector, order, _, fill = venue(False)
    fill['fillSz'] = '.04'
    with pytest.raises(IOError, match='coverage'):
        await read_order_proof(connector, order)
    assert not order.order_fills


@pytest.mark.asyncio
async def test_full_fill_page_is_paginated_before_completeness_is_accepted():
    connector, order, _, fill = venue(False)
    page = [dict(fill, tradeId=str(n), billId=str(n), fillSz='.001') for n in range(100)]
    connector._request_order_fills = AsyncMock(side_effect=[{'code': '0', 'data': page}, {'code': '0', 'data': []}])
    _, trades = await read_order_proof(connector, order)
    assert len(trades) == 100 and connector._request_order_fills.await_args_list[1].kwargs['after'] == '99'


@pytest.mark.asyncio
async def test_older_rest_snapshot_cannot_regress_a_newer_native_terminal_state():
    connector, order, _, _ = venue(False)
    status, trades = await read_order_proof(connector, order)
    order.current_state = OrderState.CANCELED
    with pytest.raises(IOError, match='older'):
        proof_covers_local(order, status, trades)
    order.current_state = OrderState.PARTIALLY_FILLED
    order.order_fills['not-visible-yet'] = trades[0]
    with pytest.raises(IOError, match='Newer'):
        proof_covers_local(order, OrderUpdate('BTC-USDT', 1700000001, OrderState.OPEN, 'client', 'venue'), trades)
