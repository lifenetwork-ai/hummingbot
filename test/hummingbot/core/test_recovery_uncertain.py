"""An empty detail lookup alone cannot authorize an operator repair."""
from types import SimpleNamespace

import pytest

from hummingbot.core.recovery import RecoveryBlocked
from hummingbot.core.recovery_uncertain import verify_uncertain_order
from hummingbot.model.recovery_uncertain import change_for
from hummingbot.model.runtime_journal import credential_fingerprint


@pytest.mark.asyncio
@pytest.mark.parametrize('fault,code', [(None, None),
                                        ('found_first', 'recovery_uncertain_order_found'), ('found_last', 'recovery_uncertain_order_found'),
                                        ('history_match', 'recovery_uncertain_order_found'), ('busy', 'recovery_exchange_unavailable'),
                                        ('partial', 'recovery_exchange_scope_incomplete'), ('duplicate', 'recovery_exchange_scope_incomplete'),
                                        ('key_changed', 'exchange_account_mismatch')])
async def test_expired_order_requires_two_exact_reads_and_complete_history(monkeypatch, fault, code):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    connector = SimpleNamespace(name='okx', authenticator=SimpleNamespace(api_key='fixture'))
    order = dict(id='order', pair='BTC-USDT', side='BUY', amount='1', price='100', created_ms=100000,
                 deadline_ms=105000, credential_fingerprint=credential_fingerprint(connector))
    if fault == 'key_changed':
        connector.authenticator.api_key = 'different'
    calls = []

    async def get(*, path_url, params, is_auth_required, **kwargs):
        assert is_auth_required
        calls.append((path_url, dict(params)))
        if path_url.endswith('/order'):
            if (len(calls) == 1 and fault == 'found_first') or (len(calls) > 1 and fault == 'found_last'):
                return dict(code='0', data=[{'clOrdId': order['id']}])
            return dict(code='51603', data=[])
        assert path_url.endswith('/orders-history')
        if fault == 'busy':
            return dict(code='50013', data=[])
        if fault == 'history_match':
            return dict(code='0', data=[dict(clOrdId=order['id'], instId=order['pair'])])
        if fault == 'partial':
            return dict(code='0', data=[dict(instId='ETH-USDT')])
        page = [dict(ordId=str(i + 1), instId=order['pair'], cTime='100000') for i in range(100)]
        return dict(code='0', data=page if 'after' not in params or fault == 'duplicate' else [])

    readers = {'okx': SimpleNamespace(connector=connector, _api_get=get)}
    snapshot = dict(uncertain_order=order)
    if code:
        with pytest.raises(RecoveryBlocked, match=code):
            await verify_uncertain_order(snapshot, readers)
    else:
        assert await verify_uncertain_order(snapshot, readers) == change_for(order)
        assert calls[2][1]['after'] == '100'
        assert calls[0] == calls[-1]
