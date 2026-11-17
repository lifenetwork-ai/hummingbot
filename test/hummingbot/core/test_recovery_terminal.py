"""Terminal metadata needs positive status/fills/status proof for every ID."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from hummingbot.core.recovery import RecoveryBlocked
from hummingbot.core.recovery_terminal import validate_changes, verify_terminal_orders
from hummingbot.model.runtime_journal import credential_fingerprint


def evidence(monkeypatch, connector='okx', *, side='BUY', position='NIL'):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    monkeypatch.setenv('OKX_SITE', 'www')
    native = SimpleNamespace(name=connector, authenticator=SimpleNamespace(api_key='offline'))
    row = dict(id='order', connector=connector, pair='BTC-USDT', side=side, position=position,
               exchange_order_id='venue-order', amount='.02', price='100', creation_timestamp=1000,
               last_update_timestamp=1500, last_status='BuyOrderCreated' if side == 'BUY' else 'SellOrderCreated',
               credential_fingerprint=credential_fingerprint(native))
    snapshot = dict(state=dict(database_now=datetime.fromtimestamp(2, timezone.utc)), terminal_orders=[row])
    actual = dict(clOrdId='order', ordId='venue-order', instId='BTC-USDT' + ('-SWAP' if connector != 'okx' else ''),
                  side=side.lower(), ordType='post_only', state='canceled', accFillSz='0',
                  sz='.02' if connector == 'okx' else '2', px='100', uTime='1800',
                  tdMode='cash' if connector == 'okx' else 'cross',
                  posSide='long' if (side == 'BUY') == (position == 'OPEN') else 'short')
    calls, faults = [], {}

    async def get(*, path_url, **kwargs):
        calls.append((path_url, kwargs))
        if path_url.endswith('/fills'):
            return dict(code='0', data=faults.get('fills', []))
        if faults.get('not_found'):
            return dict(code='51603', data=[])
        if faults.get('busy'):
            return dict(code='50013', data=[])
        current = deepcopy(actual)
        if faults.get('second_status') and sum(p.endswith('/order') for p, _ in calls) > 1:
            current['uTime'] = '1900'
        return dict(code='0', data=[current])

    reader = SimpleNamespace(connector=native, _api_get=get,
                             _format_size_to_amount=lambda pair, size: size * Decimal('.01'))
    return snapshot, {connector: reader}, actual, calls, faults


@pytest.mark.asyncio
@pytest.mark.parametrize('connector,side,position', [('okx', 'BUY', 'NIL'), ('okx', 'SELL', 'NIL'),
                                                     ('okx_perpetual', 'SELL', 'CLOSE'), ('okx_perpetual', 'BUY', 'CLOSE'),
                                                     ('okx_perpetual', 'BUY', 'OPEN'), ('okx_perpetual', 'SELL', 'OPEN')])
async def test_positive_native_cancellation_converts_contracts_and_preserves_side(monkeypatch, connector, side, position):
    snapshot, readers, _, calls, _ = evidence(monkeypatch, connector, side=side, position=position)
    changes = await verify_terminal_orders(snapshot, readers)
    validate_changes(snapshot, changes)
    assert changes[0]['after'] == dict(status='OrderCancelled', attempt_state='terminal', tracked=False)
    assert changes[0]['side'] == side and changes[0]['position'] == position
    assert len(calls) == 3 and [path.rsplit('/', 1)[-1] for path, _ in calls] == ['order', 'fills', 'order']
    assert all(kwargs['is_auth_required'] for _, kwargs in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('clOrdId', 'foreign'), ('ordId', 'other'), ('instId', 'ETH-USDT'),
                         ('side', 'sell'), ('ordType', 'limit'), ('state', 'live'), ('state', 'filled'),
                         ('accFillSz', '.01'), ('sz', '.03'), ('px', '99'), ('uTime', '999'),
                         ('uTime', '2100'), ('uTime', None), ('tdMode', 'cross')])
async def test_mismatched_or_nonterminal_evidence_cannot_authorize_metadata(monkeypatch, field, value):
    snapshot, readers, actual, _, _ = evidence(monkeypatch)
    actual[field] = value
    with pytest.raises(RecoveryBlocked):
        await verify_terminal_orders(snapshot, readers)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['not_found', 'fills', 'second_status', 'credentials', 'hedge_leg'])
async def test_missing_fills_or_changed_proof_never_becomes_order_absence(monkeypatch, fault):
    market = 'okx_perpetual' if fault == 'hedge_leg' else 'okx'
    snapshot, readers, actual, _, faults = evidence(monkeypatch, market, position='CLOSE' if market != 'okx' else 'NIL')
    if fault == 'credentials':
        readers[market].connector.authenticator.api_key = 'changed'
    elif fault == 'hedge_leg':
        actual['posSide'] = 'long'
    else:
        faults[fault] = [dict(tradeId='unexpected')] if fault == 'fills' else True
    with pytest.raises(RecoveryBlocked):
        await verify_terminal_orders(snapshot, readers)


@pytest.mark.asyncio
async def test_proof_rejects_extra_missing_or_edited_preview_rows(monkeypatch):
    snapshot, readers, _, _, _ = evidence(monkeypatch)
    changes = await verify_terminal_orders(snapshot, readers)
    for invalid in ([], changes * 2, [{**changes[0], 'amount': '100'}], [{**changes[0], 'terminal_timestamp': True}]):
        with pytest.raises(RecoveryBlocked):
            validate_changes(snapshot, invalid)


@pytest.mark.asyncio
async def test_busy_read_is_retryable_without_inventing_terminal_evidence(monkeypatch):
    snapshot, readers, _, _, faults = evidence(monkeypatch)
    faults['busy'] = True
    with pytest.raises(RecoveryBlocked, match='recovery_exchange_unavailable'):
        await verify_terminal_orders(snapshot, readers)
