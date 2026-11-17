"""Large native-history proof must resume without partial recovery authority."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.core.data_type.common import TradeType
from hummingbot.core.recovery import RecoveryBlocked


class Evidence:
    def __init__(self):
        self.progress = None
        self.identities = set()
        self.bills = set()

    async def save(self, before, after, entries):
        assert before == self.progress
        for market, order, trade, bill in entries:
            key = after['scan'], market, order, trade
            bill_key = after['scan'], market, bill
            if key in self.identities or bill_key in self.bills:
                raise RecoveryBlocked('recovery_unrecorded_or_duplicate_venue_fill')
            self.identities.add(key)
            self.bills.add(bill_key)
        self.progress = deepcopy(after)


class Venue:
    name = 'okx'

    def __init__(self, size):
        self.connector = OkxExchange(okx_api_key='offline', okx_secret_key='offline', okx_passphrase='offline', trading_required=False)
        self.size, self.calls, self.extra = size, [], None

    def fill(self, identity):
        return dict(ordId=str(identity), clOrdId='o' + str(identity), tradeId=str(identity), billId=str(identity),
                    ts='2000', fillTime='2000', fillSz='1', fillPx='100', feeCcy='USDT', fee='-.1',
                    instId='BTC-USDT', side='buy')

    async def _api_get(self, *, path_url, params=None, **kwargs):
        params = params or {}
        self.calls.append((path_url, deepcopy(params)))
        if path_url.endswith('/config'):
            data = [dict(uid='42')]
        elif path_url.endswith(('/orders-pending', '/positions')):
            data = []
        elif path_url.endswith(('/fills', '/fills-history')):
            highest = min(self.size, int(params.get('after', self.size + 1)) - 1)
            data = [self.fill(i) for i in range(highest, max(0, highest - int(params.get('limit', 100))), -1)]
            if self.extra is not None and 'end' not in params:
                data = [self.extra]
        else:
            raise AssertionError(path_url)
        return {'code': '0', 'data': data}

    async def lookup(self, keys, watermark):
        result = []
        for name, order_id, trade_id in keys:
            raw = self.fill(int(trade_id))
            tracked = SimpleNamespace(client_order_id=raw['clOrdId'], trading_pair='BTC-USDT', trade_type=TradeType.BUY)
            fee = self.connector._trade_update_from_fill(tracked, raw).fee.to_json()
            fill = dict(market=name, order_id=raw['clOrdId'], exchange_trade_id=trade_id, symbol='BTC-USDT',
                        trade_type='BUY', position='NIL', amount=Decimal(1), price=Decimal(100), timestamp=2000,
                        trade_fee=fee, trade_fee_in_quote=Decimal('.1'))
            result.append(dict(fill=fill, order=dict(id=raw['clOrdId'], exchange_order_id=order_id, creation_pending=False), journal=deepcopy(fill)))
        return result


def snapshot(size):
    return dict(mode='verified_checkpoint', state=dict(database_now=datetime.fromtimestamp(3, timezone.utc),
                desired_state='stopped', control_version=1, acknowledged_version=1, owner_uuid='owner', owner_generation=1),
                positions=[dict(connector_name='okx', trading_pair='BTC-USDT', side='BUY', amount=str(size), controller_id='spot')],
                orders=[], fills=[], ledger_hash='fixed-ledger',
                bindings=[dict(exchange='okx', connector='okx', state='active', version=1, environment='demo', account_id='www:42')],
                checkpoint=dict(expected_fills=size, anchor=dict(verified_at_ms=1000, journal_watermark=0, venue_heads=None)))


@pytest.mark.asyncio
async def test_10001_fills_resume_across_jobs_and_two_scans_without_partial_permission(monkeypatch):
    from hummingbot.core.segmented_recovery import verify_segment
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    monkeypatch.setenv('OKX_SITE', 'www')
    venue, evidence, data = Venue(10001), Evidence(), snapshot(10001)
    result = None
    jobs = 0
    while result is None and jobs < 5:
        before = sum('end' in params for _, params in venue.calls)
        result = await verify_segment(data, {'okx': venue}, venue.lookup, evidence.progress, evidence.save)
        assert sum('end' in params for _, params in venue.calls) - before <= 100
        jobs += 1
        if result is None:
            assert evidence.progress and evidence.progress['verified'] is False
            assert 'confirmation_token' not in evidence.progress
            # A recreated coordinator resumes solely from durable progress.
            data = deepcopy(data)
            data['state']['database_now'] = datetime.fromtimestamp(4, timezone.utc)
    assert jobs >= 3 and result['verified'] is True
    assert result['fills_checked'] == 10001 and result['orders_checked'] == 0
    assert len(evidence.identities) == 20002


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['ledger', 'control', 'owner', 'account', 'late_fill'])
async def test_changed_scope_or_venue_head_cannot_resume(monkeypatch, change):
    from hummingbot.core.segmented_recovery import verify_segment
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    monkeypatch.setenv('OKX_SITE', 'www')
    venue, evidence, data = Venue(101), Evidence(), snapshot(101)
    assert await verify_segment(data, {'okx': venue}, venue.lookup, None, evidence.save, page_budget=1) is None
    if change == 'ledger':
        data['ledger_hash'] = 'edited'
    elif change == 'control':
        data['state']['control_version'] = 2
    elif change == 'owner':
        data['state']['owner_generation'] = 2
    elif change == 'account':
        data['bindings'][0]['account_id'] = 'www:other'
    else:
        venue.extra = dict(venue.fill(102), ts='4000')
    with pytest.raises(RecoveryBlocked):
        await verify_segment(data, {'okx': venue}, venue.lookup, evidence.progress, evidence.save)


@pytest.mark.asyncio
@pytest.mark.parametrize('scan', [0, 1, 2])
async def test_restart_after_committed_last_page_or_pass_transition(monkeypatch, scan):
    from hummingbot.core.segmented_recovery import verify_segment
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    venue, evidence, data = Venue(3), Evidence(), snapshot(3)

    async def interrupted(before, after, entries):
        await evidence.save(before, after, entries)
        if (scan < 2 and after['scan'] == scan and after['connector_index'] == 1
                or scan == 2 and after['scan'] == 2):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await verify_segment(data, {'okx': venue}, venue.lookup, None, interrupted)
    result = await verify_segment(data, {'okx': venue}, venue.lookup, evidence.progress, evidence.save)
    assert result['verified'] and len(evidence.identities) == 6


@pytest.mark.asyncio
async def test_timeout_resumes_last_page_without_duplicate_evidence(monkeypatch):
    from hummingbot.core.segmented_recovery import verify_segment
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    venue, evidence, data = Venue(101), Evidence(), snapshot(101)
    original = venue._api_get

    async def timeout(**kwargs):
        if kwargs.get('params', {}).get('after'):
            raise TimeoutError()
        return await original(**kwargs)

    monkeypatch.setattr(venue, '_api_get', timeout)
    with pytest.raises(TimeoutError):
        await verify_segment(data, {'okx': venue}, venue.lookup, None, evidence.save)
    assert evidence.progress['count'] == 100
    monkeypatch.setattr(venue, '_api_get', original)
    result = await verify_segment(data, {'okx': venue}, venue.lookup, evidence.progress, evidence.save)
    assert result['verified'] and len(evidence.identities) == 202


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['duplicate', 'bill_duplicate', 'economics', 'expired', 'second_scan'])
async def test_incomplete_or_mutated_evidence_never_verifies(monkeypatch, fault):
    from hummingbot.core.segmented_recovery import verify_segment
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    venue, evidence, data = Venue(101), Evidence(), snapshot(101)
    await verify_segment(data, {'okx': venue}, venue.lookup, None, evidence.save, page_budget=1)
    original = venue._api_get

    async def changed(**kwargs):
        result = await original(**kwargs)
        params = kwargs.get('params', {})
        if 'end' in params and result['data']:
            if fault == 'duplicate' and params.get('after'):
                result['data'][0] = venue.fill(101)
            elif fault == 'bill_duplicate' and params.get('after'):
                result['data'][0]['billId'] = '101'
            elif fault == 'economics':
                result['data'][0]['fillPx'] = '999'
            elif fault == 'second_scan' and evidence.progress['scan'] == 1:
                result['data'][0]['fee'] = '-.2'
        return result

    monkeypatch.setattr(venue, '_api_get', changed)
    if fault == 'expired':
        data['state']['database_now'] = datetime.fromtimestamp(2000, timezone.utc)
    with pytest.raises(RecoveryBlocked):
        await verify_segment(data, {'okx': venue}, venue.lookup, evidence.progress, evidence.save)


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('connector_index', -1), ('connector_index', 99), ('scan', True),
                                         ('count', 999), ('cursor', 'bad'), ('digest', 'bad'), ('now_ms', 'bad')])
async def test_corrupted_saved_progress_is_rejected(monkeypatch, field, value):
    from hummingbot.core.segmented_recovery import verify_segment
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    venue, evidence, data = Venue(101), Evidence(), snapshot(101)
    await verify_segment(data, {'okx': venue}, venue.lookup, None, evidence.save, page_budget=1)
    evidence.progress[field] = value
    with pytest.raises(RecoveryBlocked):
        await verify_segment(data, {'okx': venue}, venue.lookup, evidence.progress, evidence.save)


@pytest.mark.asyncio
async def test_time_budget_yields_without_promoting_partial_evidence(monkeypatch):
    from hummingbot.core import segmented_recovery as module
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    venue, evidence, data = Venue(101), Evidence(), snapshot(101)
    await module.verify_segment(data, {'okx': venue}, venue.lookup, None, evidence.save, page_budget=1)
    before = deepcopy(evidence.progress)
    monkeypatch.setattr(module, 'SEGMENT_SECONDS', 0)
    assert await module.verify_segment(data, {'okx': venue}, venue.lookup, before, evidence.save) is None
    assert evidence.progress == before


@pytest.mark.asyncio
async def test_completed_passes_still_recheck_venue_after_worker_restart(monkeypatch):
    from hummingbot.core.segmented_recovery import verify_segment
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    venue, evidence, data = Venue(3), Evidence(), snapshot(3)

    async def interrupted(before, after, entries):
        await evidence.save(before, after, entries)
        if after['scan'] == 2:
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await verify_segment(data, {'okx': venue}, venue.lookup, None, interrupted)
    original = venue._api_get

    async def open_order(**kwargs):
        result = await original(**kwargs)
        if kwargs['path_url'].endswith('/orders-pending'):
            result['data'] = [dict(ordId='99')]
        return result

    monkeypatch.setattr(venue, '_api_get', open_order)
    with pytest.raises(RecoveryBlocked, match='recovery_open_orders_present'):
        await verify_segment(data, {'okx': venue}, venue.lookup, evidence.progress, evidence.save)
