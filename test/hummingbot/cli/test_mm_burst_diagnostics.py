"""Bounded actual-send evidence, including a failed burst measurement."""
import sys
from test.e2e.offline import exchange as simulator, load_health, load_trace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def test_bucket_history_is_bounded_and_counts_only_successful_sends():
    metrics = simulator.MarketDiagnostics()
    origin = metrics.origin
    for index in range(100):
        metrics.cycle(origin + index, origin + index + .025, 4)
        metrics.send(origin + index + .03, .002, .004, delivered=True)
        metrics.send(origin + index + .04, .001, .003, delivered=False)
    result = metrics.snapshot()
    assert len(result['seconds']) == 64
    assert result['discarded_seconds'] == 36
    assert result['delivered_frames'] == 100
    assert result['failed_frames'] == 100
    last = result['seconds'][-1]
    assert last['delivered_frames'] == last['failed_frames'] == 1
    assert last['max_schedule_lag_ms'] == pytest.approx(25)
    assert last['max_book_build_ms'] == pytest.approx(2)
    assert last['max_send_ms'] == pytest.approx(4)
    assert last['min_subscriptions'] == last['max_subscriptions'] == 4
    last['delivered_frames'] = 999
    assert metrics.snapshot()['seconds'][-1]['delivered_frames'] == 1


@pytest.mark.asyncio
async def test_simulator_diagnostics_are_opt_in_and_resettable():
    exchange = simulator.Exchange(fixtures={})
    request = SimpleNamespace(path='/market-metrics', method='GET')
    import json
    assert 'diagnostics' not in json.loads((await exchange.handle(request)).text)
    await exchange.control({'market_diagnostics': True})
    exchange.market_diagnostics.send(exchange.market_diagnostics.origin, .001, .002, delivered=True)
    assert json.loads((await exchange.handle(request)).text)['diagnostics']['delivered_frames'] == 1
    await exchange.control({'market_diagnostics': True})
    assert exchange.market_diagnostics.snapshot()['delivered_frames'] == 0
    await exchange.control({'market_diagnostics': False})
    assert 'diagnostics' not in json.loads((await exchange.handle(request)).text)


@pytest.mark.asyncio
@pytest.mark.parametrize('updates,elapsed,passes', [(4990, 10, False), (5100, 10, True), (5100, 11, False)])
async def test_burst_retains_actual_measurement_before_asserting(monkeypatch, updates, elapsed, passes):
    with patch.dict(sys.modules, {'load_trace': load_trace, 'load_health': load_health}):
        from test.e2e.offline import load_client
    snapshots = iter([{'market_update_count': 0, 'observed_at_monotonic': 0},
                      {'market_update_count': updates, 'observed_at_monotonic': elapsed,
                       'diagnostics': {'seconds': [{'delivered_frames': updates}]}}])
    controls, retained = [], {}

    async def exchange(path, data=None):
        if path == '/control':
            controls.append(data['target_updates_per_second'])
            return {}
        return next(snapshots)
    monkeypatch.setattr(load_client.asyncio, 'sleep', AsyncMock())
    # A wall-clock discontinuity cannot make a slow producer pass.
    monkeypatch.setattr(load_client.time, 'time', lambda: -100)
    if passes:
        await load_client.measure_market_burst(exchange, 100, on_measurement=retained.update)
    else:
        with pytest.raises(AssertionError, match='Burst feed'):
            await load_client.measure_market_burst(exchange, 100, on_measurement=retained.update)
    assert controls == [500, 100]
    assert retained['burst_updates'] == updates
    assert retained['burst_updates_per_second'] == updates / elapsed
    assert retained['burst_measurement_seconds'] == elapsed
    assert retained['burst_minimum_updates_per_second'] == 500
    assert retained['burst_diagnostics']['seconds'][0]['delivered_frames'] == updates
