"""The diagnostic must wait for a running observation before injecting a fault."""
import pytest

from scripts import test_mm_reporting_isolation as isolation


def test_running_metrics_waits_through_missing_and_startup_snapshots(monkeypatch):
    running = {'runtime_running': True, 'process_id': 42}
    values = iter([None, {'runtime_running': False, 'runtime_phase': 'starting'}, running])
    seen = []

    def read(harness, bot):
        value = next(values)
        seen.append(value)
        return value

    monkeypatch.setattr(isolation, 'read_metrics', read)
    assert isolation.wait_running_metrics(object(), 'bot') is running
    assert len(seen) == 3


def test_overview_fault_keeps_source_rows_and_uses_a_real_materialized_wait():
    import runpy
    from pathlib import Path

    namespace = runpy.run_path(str(Path('test/e2e/offline/slow_reporting_injection.py')), init_globals={'process_role': 'test'})
    from hummingbot.model.overview_fee_projection import SOURCE_BATCH

    statement = SOURCE_BATCH.text
    injected = namespace['slow_report_statement'](statement, target='overview', mode='wait')
    assert injected.startswith('WITH injected_wait AS MATERIALIZED (SELECT pg_sleep(2.1))')
    assert 'FROM injected_wait CROSS JOIN trade_fills f' in injected
    assert 'ANY(CAST(:orders AS TEXT[]))' in injected
    assert injected.count('LIMIT :batch_limit') == 1


def test_isolation_restores_the_baseline_external_book_before_injecting_sql():
    from unittest.mock import Mock

    harness = Mock()
    isolation.restore_market_fixture(harness, {'external_book_levels': 2})
    harness.control.assert_called_once_with(book_overrides={
        instrument: {
            'bids': [['.099', '100000'], ['.098999', '100000']],
            'asks': [['.101', '100000'], ['.101001', '100000']],
        } for instrument in ('LIFE-USDT', 'LIFE-USDT-SWAP')})


def test_isolation_preserves_explicit_thin_book_baselines():
    from unittest.mock import Mock

    for baseline in ({'external_book_levels': 1}, {}):
        harness = Mock()
        isolation.restore_market_fixture(harness, baseline)
        harness.control.assert_not_called()


def test_durable_fill_witness_reuses_a_backend_warmed_before_the_fault(monkeypatch):
    import subprocess
    import sys
    from unittest.mock import Mock

    popen = subprocess.Popen
    program = """
import json,sys,time
first=True
for line in sys.stdin:
    if first:
        time.sleep(.2)
        print(json.dumps({'ready': True}),flush=True)
        first=False
    else:
        print(json.dumps({'fills': 1, 'observed_at': 123}),flush=True)
"""
    factory = Mock(side_effect=lambda *args, **kwargs: popen([sys.executable, '-u', '-c', program], **kwargs))
    monkeypatch.setattr(isolation.subprocess, 'Popen', factory)
    reader = isolation.DurableFillReader('offline-fixture-pg')
    try:
        for _ in range(3):
            assert reader.read('SELECT independent_fill') == {'fills': 1, 'observed_at': 123}
        assert factory.call_count == 1
        assert reader.process.poll() is None
    finally:
        reader.close()
    assert reader.process.poll() is not None
    assert reader.process.stdin.closed and reader.process.stdout.closed


def test_durable_fill_witness_reaps_an_unresponsive_startup(monkeypatch):
    import subprocess
    import sys
    from unittest.mock import Mock

    import pytest

    popen = subprocess.Popen
    created = []

    def start(*args, **kwargs):
        process = popen([sys.executable, '-c', 'import time; time.sleep(20)'], **kwargs)
        created.append(process)
        return process

    factory = Mock(side_effect=start)
    monkeypatch.setattr(isolation.subprocess, 'Popen', factory)
    monkeypatch.setattr(isolation.select, 'select', lambda *args: ([], [], []))
    with pytest.raises(AssertionError, match='observer backend timed out'):
        isolation.DurableFillReader('offline-fixture-pg')
    assert factory.call_count == 1
    assert created[0].poll() is not None
    assert created[0].stdin.closed and created[0].stdout.closed


@pytest.mark.asyncio
@pytest.mark.parametrize('base_quantity,expected_depth,expected_quality', [
    (None, 1000, 'unavailable'), ('100000', 100000, 'known_order_filtered')])
async def test_external_depth_uses_native_contract_conversion_and_keeps_thin_history(
        tmp_path, base_quantity, expected_depth, expected_quality):
    import asyncio
    import json
    from decimal import Decimal
    from test.e2e.offline.build_fixtures import write_fixtures
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_api_order_book_data_source import (
        OkxPerpetualAPIOrderBookDataSource,
    )
    from hummingbot.core.data_type.common import TradeType
    from hummingbot.core.data_type.in_flight_order import OrderState
    from hummingbot.core.data_type.order_book import OrderBook
    from hummingbot.strategy_v2.utils.quote_guard import observe_market

    write_fixtures(tmp_path / 'fixtures.json')
    fixture = json.loads((tmp_path / 'fixtures.json').read_text())
    instrument = next(row for row in fixture['perpetual_instruments']['data'] if row['instId'] == 'LIFE-USDT-SWAP')
    baseline = dict(external_book_levels=2)
    if base_quantity is not None:
        baseline['external_book_base_quantity'] = base_quantity
    harness = Mock(assets=tmp_path)
    isolation.restore_market_fixture(harness, baseline)
    levels = harness.control.call_args.kwargs['book_overrides']['LIFE-USDT-SWAP']
    source = OkxPerpetualAPIOrderBookDataSource.__new__(OkxPerpetualAPIOrderBookDataSource)
    source._connector = SimpleNamespace(trading_pair_associated_to_exchange_symbol=AsyncMock(return_value='LIFE-USDT'))
    source._trading_rules = {'LIFE-USDT-SWAP': float(instrument['ctVal'])}
    source._set_trading_rules = AsyncMock()
    queue = asyncio.Queue()
    await source._parse_order_book_snapshot_message(
        dict(arg=dict(instId='LIFE-USDT-SWAP'), data=[dict(ts='1000000', **levels)]), queue)
    message = queue.get_nowait()
    assert [row.amount for row in message.bids] == [expected_depth, expected_depth]
    assert [row.amount for row in message.asks] == [expected_depth, expected_depth]
    book = OrderBook()
    book.apply_snapshot(message.bids, message.asks, message.update_id)
    cached = {}
    for side, prices in ((TradeType.BUY, ['.099', '.098999']), (TradeType.SELL, ['.101', '.101001'])):
        for price in prices:
            identity = side.name + price
            cached[identity] = SimpleNamespace(client_order_id=identity, exchange_order_id=identity,
                                               trading_pair='LIFE-USDT', trade_type=side, current_state=OrderState.CANCELED,
                                               price=Decimal(price), amount=Decimal('1000'), executed_amount_base=Decimal('0'), last_update_timestamp=999)
    connector = SimpleNamespace(name='okx_perpetual', in_flight_orders={},
                                _order_tracker=SimpleNamespace(cached_orders=cached, lost_orders={}), trading_rules={})
    view = observe_market(connector, book, 'LIFE-USDT', now=1000, max_age_seconds=5, max_levels=10, max_orders=20)
    assert view.quality == expected_quality
