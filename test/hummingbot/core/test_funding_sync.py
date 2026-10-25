import copy
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from hummingbot.core.funding_sync import DAY_MS, FundingSync


class Memory:
    def __init__(self):
        self.state, self.rows = {}, {}

    def load(self):
        return copy.deepcopy(self.state)

    def commit(self, owner, generation, rows, checkpoint):
        for row in rows:
            self.rows[row['bill_id']] = row
        self.state = copy.deepcopy(checkpoint)
        return checkpoint


def raw(identity, timestamp, **changes):
    return dict(billId=str(identity), ts=str(timestamp), instId="BTC-USDT-SWAP", type="8", subType="174",
                pnl="0.0000001234567890123456789", ccy="USDT", **changes)


@pytest.mark.asyncio
async def test_backfill_pages_overlap_late_bills_and_closed_position_without_mutations():
    now = 1800000000000
    store = Memory()
    rows = [raw(n, now - 1000) for n in range(102, 2, -1)]
    getter = AsyncMock(side_effect=[{'code': '0', 'data': [{'uid': 'account'}]},
                                    {'code': '0', 'data': rows}, {'code': '0', 'data': [raw(2, now - 1000)]}])
    worker = FundingSync(store, getter, environment="demo")
    await worker.sync("owner", 1, now_ms=now, pairs={'BTC-USDT'}, earliest_ms=now - 2000)
    assert len(store.rows) == 101 and store.state['covered_until_ms'] == now
    assert store.state['pending'] is None
    assert store.rows['2']['amount'] == Decimal('0.0000001234567890123456789')
    assert getter.call_args_list[-1].args[1]['after'] == '3'
    getter.side_effect = None
    getter.return_value = {'code': '0', 'data': [raw(103, now - 1500), raw(2, now - 1000)]}
    await worker.sync("owner", 1, now_ms=now + 120000, pairs={'BTC-USDT'})
    assert len(store.rows) == 102
    assert all(c.args[0] in ('/api/v5/account/config', '/api/v5/account/bills') for c in getter.call_args_list)
    assert getter.call_args.args[1]['begin'] <= now - 1500


@pytest.mark.asyncio
async def test_full_page_checkpoint_survives_restart_and_api_failure():
    now = 1800000000000
    store = Memory()
    getter = AsyncMock(side_effect=[{'code': '0', 'data': [{'uid': 'account'}]},
                                    {'code': '0', 'data': [raw(n, now - 1000) for n in range(101, 1, -1)]}])
    worker = FundingSync(store, getter, environment='demo', max_pages=1)
    await worker.sync('owner', 1, now_ms=now, pairs={'BTC-USDT'}, earliest_ms=now - 2000)
    assert store.state['pending']['after'] == '2' and store.state['covered_until_ms'] == now - 2000
    before = copy.deepcopy(store.state)
    getter = AsyncMock(side_effect=[{'code': '0', 'data': [{'uid': 'account'}]}, RuntimeError('offline')])
    resumed = FundingSync(store, getter, environment='demo')
    with pytest.raises(RuntimeError):
        await resumed.sync('owner', 2, now_ms=now + 120000, pairs={'BTC-USDT'})
    assert store.state == before and len(store.rows) == 100
    getter.side_effect = None
    getter.return_value = {'code': '0', 'data': []}
    await resumed.sync('owner', 2, now_ms=now + 120000, pairs={'BTC-USDT'})
    assert getter.call_args.args[1]['after'] == '2' and store.state['covered_until_ms'] == now


@pytest.mark.asyncio
async def test_old_gap_uses_history_and_exposes_retention_limit():
    now = 1800000000000
    store = Memory()
    getter = AsyncMock(side_effect=[{'code': '0', 'data': [{'uid': 'account'}]}, {'code': '0', 'data': []}])
    worker = FundingSync(store, getter, environment='live', max_pages=1)
    await worker.sync('owner', 1, now_ms=now, pairs={'BTC-USDT'}, earliest_ms=now - 200 * DAY_MS)
    assert getter.call_args.args[0] == '/api/v5/account/bills-archive'
    assert store.state['history_gap_until_ms'] > store.state['history_gap_from_ms']
    assert store.state['covered_until_ms'] < now


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [{'pnl': 'NaN'}, {'ccy': 'BTC'}, {'billId': ''}])
async def test_invalid_funding_never_advances_coverage(changes):
    now = 1800000000000
    store = Memory()
    row = raw(1, now - 1000)
    row.update(changes)
    getter = AsyncMock(side_effect=[{'code': '0', 'data': [{'uid': 'account'}]}, {'code': '0', 'data': [row]}])
    with pytest.raises(ValueError):
        await FundingSync(store, getter, environment='demo').sync('owner', 1, now_ms=now, pairs={'BTC-USDT'}, earliest_ms=now - 2000)
    assert store.state == {} and not store.rows


@pytest.mark.asyncio
async def test_read_only_reader_reuses_get_and_rejects_mutating_endpoints():
    from types import SimpleNamespace

    from hummingbot.core.funding_sync import FundingReader
    connector = SimpleNamespace(_api_get=AsyncMock(return_value={'code': '0', 'data': []}))
    reader = FundingReader(connector)
    await reader('/api/v5/account/bills', {'type': '8'})
    connector._api_get.assert_awaited_once()
    assert connector._api_get.call_args.kwargs['is_auth_required'] is True
    with pytest.raises(ValueError):
        await reader('/api/v5/trade/order', {})
    assert connector._api_get.await_count == 1


@pytest.mark.asyncio
async def test_downtime_beyond_history_limit_marks_gap_and_does_not_loop_expired_cursor():
    now = 1800000000000
    store = Memory()
    store.state = dict(account_id='account', environment='demo', pairs=['BTC-USDT'],
                       covered_from_ms=now - 200 * DAY_MS, covered_until_ms=now - 190 * DAY_MS,
                       pending=dict(begin=now - 190 * DAY_MS, end=now - 189 * DAY_MS, after='3'))
    getter = AsyncMock(side_effect=[{'code': '0', 'data': [{'uid': 'account'}]}, {'code': '0', 'data': []}])
    await FundingSync(store, getter, environment='demo', max_pages=1).sync('owner', 1, now_ms=now, pairs={'BTC-USDT'})
    assert store.state['history_gap_from_ms'] == now - 190 * DAY_MS
    assert getter.call_args.args[1]['begin'] >= now - 89 * DAY_MS
    assert 'after' not in getter.call_args.args[1]
