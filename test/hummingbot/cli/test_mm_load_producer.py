"""Exercise the real load client across the generation/drain boundary."""
import asyncio
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from test.e2e.offline import load_health, load_trace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts.mm_load_budgets import violations

with patch.dict(sys.modules, {'load_health': load_health, 'load_trace': load_trace}):
    from test.e2e.offline import load_client


class Response:
    status = 200

    def __init__(self, body):
        self.body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def raise_for_status(self):
        pass

    async def json(self):
        return await self.body()


class ExchangeSession:
    def __init__(self, mode):
        self.mode = mode
        self.window_ended = asyncio.Event()
        self.posts = []
        self.cancelled_request = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def request(self, method, url, json=None, **kwargs):
        async def body():
            if url.endswith('/ledger'):
                now = time.monotonic()
                return dict(market_update_count=int(now * 100), observed_at_monotonic=now, requests=[])
            if url.endswith('/working-orders'):
                if self.mode == 'empty':
                    return {'orders': []}
                if self.mode == 'late_order':
                    await self.window_ended.wait()
                return {'orders': [dict(instId='LIFE-USDT', side='buy', state='live',
                                        clOrdId='one', accFillSz='0', sz='1')]}
            if json and 'fill' in json:
                self.posts.append(self.window_ended.is_set())
                if self.mode in ('inflight', 'stuck'):
                    try:
                        await (self.window_ended if self.mode == 'inflight' else asyncio.Event()).wait()
                    except asyncio.CancelledError:
                        self.cancelled_request = True
                        raise
                return {}
            return dict(running=True, session=dict(id='test', step_expires_at=(
                datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()))
        return Response(body)

    def get(self, url, **kwargs):
        return self.request('GET', url, **kwargs)

    def post(self, url, **kwargs):
        return self.request('POST', url, **kwargs)


async def run_client(monkeypatch, tmp_path, mode, *, duration=1):
    session = ExchangeSession(mode)
    monkeypatch.setattr(load_client.aiohttp, 'ClientSession', lambda **kwargs: session)
    original_wait = asyncio.wait_for
    waits = 0

    async def bounded_drain(awaitable, timeout):
        nonlocal waits
        # First wait is queue.join(), after the real client closes generation.
        if waits == 0:
            session.window_ended.set()
        waits += 1
        assert timeout == 10  # Production deadline must remain unchanged.
        return await original_wait(awaitable, timeout=.4)

    monkeypatch.setattr(load_client.asyncio, 'wait_for', bounded_drain)
    output = tmp_path / 'result.json'
    await load_client.main(SimpleNamespace(duration=duration, read_rate=20, market_rate=100,
                                           output=str(output), fills=True, controls=False,
                                           operations=False, burst=False, random_soak=True))
    return session, json.loads(output.read_text())


@pytest.mark.asyncio
async def test_empty_book_at_random_handover_is_not_an_inflight_backlog(monkeypatch, tmp_path):
    session, result = await run_client(monkeypatch, tmp_path, 'empty', duration=2)
    assert session.posts == []
    assert result['valid_fills'] == 0
    assert result['errors'] == []
    assert result['fill_target_shortfall'] == 2
    assert result['fill_drain_policy'] == 'await_dispatched_requests_only'
    # Ending production cleanly must not make an underloaded fixed run pass.
    assert 'fill_rate' in violations(result)
    assert result['completed_reads'] == result['requested_reads'] == 40


@pytest.mark.asyncio
async def test_order_discovered_after_generation_closes_cannot_start_a_new_fill(monkeypatch, tmp_path):
    session, result = await run_client(monkeypatch, tmp_path, 'late_order')
    assert session.posts == []
    assert result['valid_fills'] == 0
    assert result['errors'] == []


@pytest.mark.asyncio
async def test_already_dispatched_fill_is_awaited_and_counted_during_drain(monkeypatch, tmp_path):
    session, result = await run_client(monkeypatch, tmp_path, 'inflight')
    assert session.posts == [False]
    assert not session.cancelled_request
    assert result['valid_fills'] == 1
    assert result['errors'] == []


@pytest.mark.asyncio
async def test_genuinely_stuck_fill_request_still_fails_drain(monkeypatch, tmp_path):
    session, result = await run_client(monkeypatch, tmp_path, 'stuck')
    assert session.posts == [False]
    assert session.cancelled_request
    assert result['valid_fills'] == 0
    assert result['errors'] == [{'path': 'fill_generator', 'error': 'backlog_not_drained'}]


@pytest.mark.asyncio
async def test_missing_next_family_does_not_block_other_executable_quotes(monkeypatch, tmp_path):
    # The simulator continuously offers one real working order. The old strict
    # family cursor stops after one fill while waiting for an absent perpetual
    # BUY, even though the measured workload can still execute a spot fill.
    session, result = await run_client(monkeypatch, tmp_path, 'available', duration=2)
    assert result['valid_fills'] == 2
    assert session.posts == [False, False]
    assert result['fill_target_shortfall'] == 0
    assert result['errors'] == []


def quote(identity, market='LIFE-USDT', side='buy', **values):
    return dict(clOrdId=identity, instId=market, side=side, state='live', accFillSz='0', sz='1', **values)


def test_family_rotation_recovers_a_missing_family_without_starving_others():
    quotes = [quote('spot-buy'), quote('spot-sell', side='sell'),
              quote('perp-sell', market='LIFE-USDT-SWAP', side='sell')]
    cursor, selected = 1, []
    for _ in range(3):
        order, amount, cursor = load_client.fill_candidate(quotes, cursor)
        selected.append(order['clOrdId'])
    assert selected == ['spot-sell', 'perp-sell', 'spot-buy']
    quotes.append(quote('perp-buy', market='LIFE-USDT-SWAP'))
    order, amount, cursor = load_client.fill_candidate(quotes, cursor)
    assert order['clOrdId'] == 'perp-buy'
    assert amount == load_client.Decimal('.1')


def test_exhausted_first_order_does_not_hide_an_executable_order_at_another_level():
    first, second = quote('exhausted'), quote('available')
    first['accFillSz'] = '.9999'
    order, amount, _ = load_client.fill_candidate([first, second], 0)
    assert order['clOrdId'] == 'available'
    assert amount == load_client.Decimal('.001')


def test_absent_and_terminal_orders_cannot_generate_synthetic_fills():
    canceled, filled = quote('canceled'), quote('filled')
    canceled['state'], filled['state'] = 'canceled', 'filled'
    assert load_client.fill_candidate([], 0) is None
    assert load_client.fill_candidate([canceled, filled], 0) is None
