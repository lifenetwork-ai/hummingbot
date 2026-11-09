"""Venue evidence must be complete and GET-only before any recovery write."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from hummingbot.connector.okx_environment import OKXEnvironment
from hummingbot.core.recovery import RecoveryBlocked
from hummingbot.core.recovery_exchange import verify_exchange


class Reader:
    def __init__(self, name='okx'):
        self.name = name
        self.calls = []
        self.order = dict(clOrdId='a', ordId='10', instId='BTC-USDT', state='filled',
                          accFillSz='1', sz='1', side='buy', px='100', ordType='post_only')
        self.fills = [dict(ordId='10', tradeId='1', ts='2000', fillSz='1', fillPx='100', feeCcy='USDT', fee='-.1',
                           instId='BTC-USDT', side='buy', billId='1')]
        self.pending = []
        self.positions = []
        self._initialize_trading_pair_symbol_map = AsyncMock()

    def _format_size_to_amount(self, pair, size):
        return Decimal(size)

    async def _api_get(self, *, path_url, **kwargs):
        self.calls.append((path_url, deepcopy(kwargs.get('params', {}))))
        if path_url.endswith('/config'):
            data = [dict(uid='42', posMode='long_short_mode', acctLv='2')]
        elif path_url.endswith('/orders-pending'):
            data = self.pending
        elif path_url.endswith('/order'):
            data = [self.order]
        elif path_url.endswith('/fills'):
            data = self.fills
        elif path_url.endswith('/positions'):
            data = self.positions
        else:
            pytest.fail('Unexpected exchange endpoint')
        return dict(code='0', data=deepcopy(data))


def snapshot():
    return dict(state={'database_now': datetime.fromtimestamp(3, timezone.utc)},
                orders=[dict(id='a', market='okx', symbol='BTC-USDT', creation_pending=False,
                             creation_timestamp=1000, exchange_order_id='10', last_status='BuyOrderCompleted',
                             amount='1', price='100', order_type='LIMIT_MAKER', side='BUY')],
                fills=[dict(order_id='a', exchange_trade_id='1', market='okx', symbol='BTC-USDT', trade_type='BUY',
                            position='NIL', price='100', amount='1', timestamp=2000,
                            trade_fee={'percent': '0', 'flat_fees': [{'token': 'USDT', 'amount': '.1'}]})],
                positions=[], bindings=[dict(exchange='okx', connector='okx', state='active', environment=OKXEnvironment.load().mode,
                                             account_id=OKXEnvironment.load().site + ':42')])


@pytest.mark.asyncio
async def test_complete_order_and_original_fee_proof_uses_reads_only():
    reader = Reader()
    result = await verify_exchange(snapshot(), [], {'okx': reader})
    assert result['verified'] and result['fills_checked'] == 1
    assert sum(path.endswith('/fills') for path, _ in reader.calls) == 2
    assert all(path in {'/api/v5/account/config', '/api/v5/trade/orders-pending', '/api/v5/trade/order',
                        '/api/v5/trade/fills', '/api/v5/account/positions'} for path, _ in reader.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation,code', [
    ('quantity', 'recovery_fill_mismatch'), ('fee', 'recovery_original_fee_mismatch'),
    ('missing', 'recovery_fill_coverage_incomplete'), ('extra', 'recovery_unrecorded_or_duplicate_venue_fill'),
    ('duplicate', 'recovery_unrecorded_or_duplicate_venue_fill'), ('identity', 'recovery_order_identity_unproven'),
    ('pending', 'recovery_open_orders_present'), ('position', 'recovery_position_mismatch'),
    ('account', 'exchange_account_mismatch'), ('old', 'recovery_history_window_exceeded'),
    ('order_pending', 'recovery_pending_intents'),
])
async def test_partial_or_inconsistent_evidence_blocks_recovery(mutation, code):
    reader, data = Reader(), snapshot()
    if mutation == 'quantity':
        reader.fills[0]['fillSz'] = '2'
    elif mutation == 'fee':
        reader.fills[0]['fee'] = '-.2'
    elif mutation == 'missing':
        reader.fills = []
    elif mutation == 'extra':
        reader.fills.append({**reader.fills[0], 'tradeId': 'other'})
    elif mutation == 'duplicate':
        reader.fills.append(deepcopy(reader.fills[0]))
    elif mutation == 'identity':
        reader.order['ordId'] = 'different'
    elif mutation == 'pending':
        reader.pending = [{'ordId': 'unexpected'}]
    elif mutation == 'position':
        reader.positions = [{'pos': '1'}]
    elif mutation == 'account':
        data['bindings'][0]['account_id'] = 'global:wrong'
    elif mutation == 'old':
        data['state']['database_now'] = datetime.fromtimestamp(4 * 86400, timezone.utc)
    elif mutation == 'order_pending':
        data['orders'][0]['creation_pending'] = True
    with pytest.raises(RecoveryBlocked, match=code):
        await verify_exchange(data, [], {'okx': reader})


@pytest.mark.asyncio
async def test_perpetual_contract_conversion_and_final_exposure_are_verified():
    reader, data = Reader('okx_perpetual'), snapshot()
    data['orders'][0]['market'] = data['fills'][0]['market'] = 'okx_perpetual'
    data['fills'][0]['position'] = 'OPEN'
    data['bindings'][0]['connector'] = 'okx_perpetual'
    data['positions'] = [dict(id='p', connector_name='okx_perpetual', trading_pair='BTC-USDT', side='BUY', amount='1')]
    reader.order.update(instId='BTC-USDT-SWAP', posSide='long', tdMode='cross')
    reader.fills[0].update(instId='BTC-USDT-SWAP', posSide='long')
    reader.positions = [dict(instId='BTC-USDT-SWAP', pos='1', posSide='long', mgnMode='cross')]
    result = await verify_exchange(data, [], {reader.name: reader})
    assert result['position_legs_checked'] == 1
    reader._initialize_trading_pair_symbol_map.assert_awaited_once()
    reader.positions[0]['pos'] = '2'
    with pytest.raises(RecoveryBlocked, match='recovery_position_mismatch'):
        await verify_exchange(data, [], {reader.name: reader})


@pytest.mark.asyncio
async def test_final_check_rejects_exchange_change_during_proof():
    reader = Reader()
    original = reader._api_get
    reads = 0

    async def changing(**kwargs):
        nonlocal reads
        if kwargs['path_url'].endswith('/fills'):
            reads += 1
            if reads == 2:
                reader.fills[0]['fee'] = '-.2'
        return await original(**kwargs)

    reader._api_get = changing
    with pytest.raises(RecoveryBlocked, match='recovery_original_fee_mismatch'):
        await verify_exchange(snapshot(), [], {'okx': reader})


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('environment', 'other-environment'), ('account_id', 'other-site:42')])
async def test_same_uid_cannot_reuse_proof_across_environment_or_site(field, value):
    reader, data = Reader(), snapshot()
    data['bindings'][0][field] = value
    with pytest.raises(RecoveryBlocked, match='exchange_account_mismatch'):
        await verify_exchange(data, [], {'okx': reader})
    assert reader.calls == []


@pytest.mark.asyncio
async def test_page_budget_cannot_turn_a_full_page_into_complete_evidence(monkeypatch):
    from hummingbot.core import recovery_exchange
    monkeypatch.setattr(recovery_exchange, 'PAGE_LIMIT', 1)
    monkeypatch.setattr(recovery_exchange, 'MAX_PAGES', 1)
    with pytest.raises(RecoveryBlocked, match='recovery_exchange_scope_incomplete'):
        await verify_exchange(snapshot(), [], {'okx': Reader()})
