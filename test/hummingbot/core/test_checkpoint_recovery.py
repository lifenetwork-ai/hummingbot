"""Checkpoint proof verifies the full venue suffix without per-old-order REST."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.core.checkpoint_recovery import verify_checkpoint_exchange
from hummingbot.core.data_type.common import TradeType
from hummingbot.core.recovery import RecoveryBlocked


class Reader:
    name = 'okx'

    def __init__(self):
        self.connector = OkxExchange(okx_api_key='test', okx_secret_key='test', okx_passphrase='test', trading_required=False)
        self.fills = [dict(ordId='10', clOrdId='a', tradeId='1', ts='2000', fillTime='2000',
                           fillSz='1', fillPx='100', feeCcy='USDT', fee='-.1', instId='BTC-USDT', side='buy', billId='2')]
        self.pending = []
        self.calls = []
        self.uid = '42'

    async def _api_get(self, *, path_url, **kwargs):
        params = kwargs.get('params', {})
        self.calls.append((path_url, deepcopy(params)))
        if path_url.endswith('/config'):
            data = [dict(uid=self.uid)]
        elif path_url.endswith('/orders-pending'):
            data = self.pending
        elif path_url.endswith('/positions'):
            data = []
        elif path_url.endswith(('/fills', '/fills-history')):
            data = self.fills
        else:
            pytest.fail('Unexpected or per-order exchange request')
        return dict(code='0', data=deepcopy(data))


@pytest_asyncio.fixture
async def context(monkeypatch):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo'); monkeypatch.setenv('OKX_SITE', 'www')
    reader = Reader()
    tracked = SimpleNamespace(client_order_id='a', trading_pair='BTC-USDT', trade_type=TradeType.BUY)
    fee = reader.connector._trade_update_from_fill(tracked, reader.fills[0]).fee.to_json()
    fill = dict(market='okx', order_id='a', exchange_trade_id='1', symbol='BTC-USDT', trade_type='BUY', position='NIL',
                amount=Decimal(1), price=Decimal(100), timestamp=0, trade_fee=fee, trade_fee_in_quote=Decimal('.1'))
    row = dict(fill=fill, order=dict(id='a', exchange_order_id='10', creation_pending=False), journal=deepcopy(fill))
    data = dict(mode='verified_checkpoint', state=dict(database_now=datetime.fromtimestamp(3, timezone.utc)),
                positions=[dict(connector_name='okx', trading_pair='BTC-USDT', side='BUY', amount='1', controller_id='spot')],
                bindings=[dict(exchange='okx', connector='okx', state='active', environment='demo', account_id='www:42')],
                checkpoint=dict(expected_fills=1, anchor=dict(verified_at_ms=1000, journal_watermark=0, venue_heads=None)))
    lookup = AsyncMock(return_value=[row])
    return reader, data, lookup, row


@pytest.mark.asyncio
async def test_native_identity_and_economics_verify_event_clock_fill_without_replaying_old_orders(context):
    reader, data, lookup, _ = context
    result = await verify_checkpoint_exchange(data, {'okx': reader}, lookup)
    assert result['verified'] and result['scope'] == 'verified_checkpoint'
    assert result['fills_checked'] == 1 and result['orders_checked'] == 0 and result['venue_heads'] == {'okx': '2'}
    assert lookup.await_count == 2
    assert all(not path.endswith('/order') for path, _ in reader.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation,code', [
    ('missing', 'recovery_fill_coverage_incomplete'), ('unknown', 'recovery_unrecorded_or_duplicate_venue_fill'),
    ('duplicate', 'recovery_unrecorded_or_duplicate_venue_fill'), ('quantity', 'recovery_fill_mismatch'),
    ('fee', 'recovery_original_fee_mismatch'), ('journal', 'recovery_financial_data_invalid'),
    ('account', 'exchange_account_mismatch'), ('pending', 'recovery_open_orders_present'),
    ('client_id', 'recovery_order_identity_unproven'), ('conversion', 'recovery_original_fee_mismatch'),
    ('old', 'recovery_history_window_exceeded'), ('position', 'recovery_position_scope_ambiguous'),
    ('null_bill', 'recovery_exchange_scope_incomplete'), ('null_order', 'recovery_exchange_scope_incomplete'),
])
async def test_incomplete_or_conflicting_suffix_stays_blocked(context, mutation, code):
    reader, data, lookup, row = context
    if mutation == 'missing':
        reader.fills = []
    elif mutation == 'unknown':
        lookup.return_value = []
    elif mutation == 'duplicate':
        reader.fills *= 2
    elif mutation == 'quantity':
        reader.fills[0]['fillSz'] = '2'
    elif mutation == 'fee':
        reader.fills[0]['fee'] = '-.2'
    elif mutation == 'journal':
        row['journal']['price'] = '99'
    elif mutation == 'account':
        reader.uid = 'other'
    elif mutation == 'pending':
        reader.pending = [{'clOrdId': 'foreign'}]
    elif mutation == 'client_id':
        reader.fills[0]['clOrdId'] = 'other'
    elif mutation == 'conversion':
        row['fill']['trade_fee_in_quote'] = row['journal']['trade_fee_in_quote'] = Decimal('.2')
    elif mutation == 'old':
        data['state']['database_now'] = datetime.fromtimestamp(90 * 86400, timezone.utc)
    elif mutation == 'position':
        data['positions'].append(deepcopy(data['positions'][0]))
    elif mutation == 'null_bill':
        reader.fills[0]['billId'] = None
    elif mutation == 'null_order':
        reader.fills[0]['ordId'] = row['order']['exchange_order_id'] = None
    with pytest.raises(RecoveryBlocked, match=code):
        await verify_checkpoint_exchange(data, {'okx': reader}, lookup)


@pytest.mark.asyncio
async def test_cursor_requires_positive_anchor_and_can_use_historical_fills_without_client_id(context, monkeypatch):
    from hummingbot.core import checkpoint_recovery as module
    reader, data, lookup, _ = context
    monkeypatch.setattr(module, 'PAGE_SIZE', 1)
    data['checkpoint']['anchor']['venue_heads'] = {'okx': '1'}
    data['state']['database_now'] = datetime.fromtimestamp(4 * 86400, timezone.utc)
    reader.fills[0]['clOrdId'] = ''
    original = reader._api_get

    async def pages(**kwargs):
        if kwargs['path_url'].endswith('/fills-history') and kwargs['params'].get('after') == '2':
            return dict(code='0', data=[dict(reader.fills[0], tradeId='old', billId='1')])
        return await original(**kwargs)

    reader._api_get = pages
    result = await verify_checkpoint_exchange(data, {'okx': reader}, lookup)
    assert result['verified'] and result['fills_checked'] == 1
    assert any(path.endswith('/fills-history') for path, _ in reader.calls)
    reader._api_get = original
    with pytest.raises(RecoveryBlocked):
        await verify_checkpoint_exchange(data, {'okx': reader}, lookup)


@pytest.mark.asyncio
async def test_fully_filled_page_does_not_prove_coverage_when_budget_is_exhausted(context, monkeypatch):
    from hummingbot.core import checkpoint_recovery as module
    monkeypatch.setattr(module, 'PAGE_SIZE', 1); monkeypatch.setattr(module, 'MAX_PAGES', 1)
    reader, data, lookup, _ = context
    with pytest.raises(RecoveryBlocked, match='recovery_exchange_scope_incomplete'):
        await verify_checkpoint_exchange(data, {'okx': reader}, lookup)


@pytest.mark.asyncio
async def test_changed_second_scan_never_revalidates_a_preview(context):
    reader, data, lookup, _ = context
    original = reader._api_get
    scans = 0

    async def changed(**kwargs):
        nonlocal scans
        if kwargs['path_url'].endswith('/fills'):
            scans += 1
            if scans == 2:
                reader.fills[0]['billId'] = '3'
        return await original(**kwargs)

    reader._api_get = changed
    with pytest.raises(RecoveryBlocked, match='recovery_exchange_changed'):
        await verify_checkpoint_exchange(data, {'okx': reader}, lookup)


@pytest.mark.asyncio
@pytest.mark.parametrize('action,side,leg', [('OPEN', 'BUY', 'long'), ('OPEN', 'SELL', 'short'),
                                             ('CLOSE', 'SELL', 'long'), ('CLOSE', 'BUY', 'short')])
async def test_perpetual_native_contract_conversion_and_both_hedge_legs(context, action, side, leg):
    reader, data, lookup, row = context
    reader.name = 'okx_perpetual'
    reader.connector = OkxPerpetualDerivative(okx_perpetual_api_key='test', okx_perpetual_secret_key='test',
                                              okx_perpetual_passphrase='test', trading_required=False)
    reader.connector._contract_sizes['BTC-USDT'] = Decimal('.01')
    reader._initialize_trading_pair_symbol_map = AsyncMock()
    reader._format_size_to_amount = reader.connector._format_size_to_amount
    data['bindings'][0]['connector'] = 'okx_perpetual'
    data['positions'][0].update(connector_name='okx_perpetual', amount='.02', side='BUY' if leg == 'long' else 'SELL')
    reader.fills[0].update(instId='BTC-USDT-SWAP', fillSz='2', side=side.lower(), posSide=leg)
    row['fill'].update(market='okx_perpetual', position=action, trade_type=side, amount=Decimal('.02'))
    tracked = SimpleNamespace(client_order_id='a', trading_pair='BTC-USDT', trade_type=TradeType[side])
    row['fill']['trade_fee'] = reader.connector._parse_trade_update(reader.fills[0], tracked).fee.to_json()
    row['journal'] = deepcopy(row['fill'])
    original = reader._api_get

    async def responses(**kwargs):
        if kwargs['path_url'].endswith(('/config', '/fills')):
            limit = reader.connector._throttler._id_to_limit_map[kwargs['limit_id']]
            assert limit.limit_id == 'GET-/api/v5/account/config' and limit.limit == 5 and limit.time_interval == 2
        if kwargs['path_url'].endswith('/config'):
            return dict(code='0', data=[dict(uid='42', posMode='long_short_mode', acctLv='2')])
        if kwargs['path_url'].endswith('/positions'):
            return dict(code='0', data=[dict(instId='BTC-USDT-SWAP', pos='2', posSide=leg, mgnMode='cross')])
        return await original(**kwargs)

    reader._api_get = responses
    proof = await verify_checkpoint_exchange(data, {'okx_perpetual': reader}, lookup)
    assert proof['verified'] and proof['position_legs_checked'] == 1
    data['positions'][0]['amount'] = '.03'
    with pytest.raises(RecoveryBlocked, match='recovery_position_mismatch'):
        await verify_checkpoint_exchange(data, {'okx_perpetual': reader}, lookup)


@pytest.mark.asyncio
async def test_controller_reattribution_is_not_a_verified_financial_checkpoint(context):
    reader, data, lookup, _ = context
    data['state']['recovery_state'] = dict(strategy_checkpoints=dict(mm=dict(mm_drawdown_policy=dict(
        scope=dict(other=dict(connector_name='okx', trading_pair='BTC-USDT'))))))
    with pytest.raises(RecoveryBlocked, match='recovery_position_scope_ambiguous'):
        await verify_checkpoint_exchange(data, {'okx': reader}, lookup)


@pytest.mark.asyncio
async def test_base_asset_fee_uses_verified_execution_price(context):
    reader, data, lookup, row = context
    reader.fills[0].update(feeCcy='BTC', fee='-.001')
    tracked = SimpleNamespace(client_order_id='a', trading_pair='BTC-USDT', trade_type=TradeType.BUY)
    row['fill']['trade_fee'] = reader.connector._trade_update_from_fill(tracked, reader.fills[0]).fee.to_json()
    row['journal'] = deepcopy(row['fill'])
    assert (await verify_checkpoint_exchange(data, {'okx': reader}, lookup))['verified']


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['duplicate', 'exchange', 'environment', 'accounts'])
async def test_checkpoint_proof_requires_one_exact_dedicated_account_scope(context, mutation):
    reader, data, lookup, _ = context
    if mutation == 'duplicate':
        data['bindings'].append(deepcopy(data['bindings'][0]))
    elif mutation == 'exchange':
        data['bindings'][0]['exchange'] = 'other'
    elif mutation == 'environment':
        data['bindings'][0]['environment'] = 'live'
    else:
        data['bindings'].append(dict(data['bindings'][0], connector='okx_perpetual', account_id='www:43'))
    with pytest.raises(RecoveryBlocked, match='exchange_account_mismatch'):
        await verify_checkpoint_exchange(data, {'okx': reader, 'okx_perpetual': reader}, lookup)
