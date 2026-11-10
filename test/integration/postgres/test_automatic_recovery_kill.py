"""SIGKILL before/after fill and native transfer, then independent reconstruction."""
import asyncio
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from test.integration.postgres.test_automatic_recovery import orchestrator
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from bidict import bidict

from hummingbot.client.config.client_config_map import MarketDataCollectionConfigMap
from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.core.order_submission_guard import install_guard
from hummingbot.model.runtime_journal import RuntimeJournal
from hummingbot.model.sql_connection_manager import SQLConnectionManager, SQLConnectionType
from hummingbot.model.trade_fill import TradeFill


@pytest.mark.parametrize('stage', ['send', 'fill', 'hold'])
def test_sigkill_recovers_financial_boundary_once_without_a_new_post(pg_engine, monkeypatch, stage):
    async def kill():
        process = subprocess.Popen([sys.executable, '-m', 'test.integration.postgres.automatic_recovery_worker', stage],
                                   cwd=Path(__file__).resolve().parents[3], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env={**os.environ, 'HBOT_IO_TEST_URL': pg_engine.url.render_as_string(hide_password=False)})
        try:
            line = await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 30)
            if not line:
                error = (await asyncio.to_thread(process.stderr.read)).decode(errors='replace')
                pytest.fail(error.replace(str(pg_engine.url.password), '[redacted]')[-2000:])
            assert json.loads(line) == {'ready': stage, 'posts': 1}
            process.kill()
            assert await asyncio.to_thread(process.wait, timeout=5) != 0
        finally:
            if process.poll() is None:
                process.kill()
            await asyncio.to_thread(process.wait, timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
    asyncio.run(kill())
    monkeypatch.setenv('DATABASE_URL', 'postgresql://test-unused/db')
    monkeypatch.setenv('K_SERVICE', 'offline-test')
    with patch.object(SQLConnectionManager, '_postgres_engine', pg_engine):
        manager = SQLConnectionManager(None, SQLConnectionType.TRADE_FILLS)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    previous = MarketsRecorder._shared_instance
    exchange = OkxExchange(okx_api_key='offline', okx_secret_key='offline', okx_passphrase='offline', trading_pairs=['BTC-USDT'])
    exchange._set_trading_pair_symbol_map(bidict({'BTC-USDT': 'BTC-USDT'}))
    recorder = MarketsRecorder(manager, [exchange], 'test.yml', 'pure_market_making',
                               MarketDataCollectionConfigMap(market_data_collection_enabled=False))
    recorder.start()
    owner = BotOwnership(pg_engine, 'hummingbot')
    assert owner.try_acquire() and not owner.check_owner()['recovery_state'].get('halt')
    try:
        install_guard(owner)
        owner.prepare(1, 'test.yml')
        owner.connectors = {'okx': exchange}
        runtime = orchestrator()
        core = SimpleNamespace(markets_recorder=recorder, trade_fill_db=manager,
                               strategy=SimpleNamespace(executor_orchestrator=runtime))
        status = dict(clOrdId='auto-order', ordId='venue-auto', instId='BTC-USDT', side='buy', ordType='post_only',
                      px='100', sz='.1', accFillSz='.1', uTime='1700000001000', state='filled')
        trade = dict(tradeId='fill-auto', ordId='venue-auto', instId='BTC-USDT', side='buy',
                     fillSz='.1', fillPx='100', fee='-.01', feeCcy='USDT', ts='1700000001000')
        with patch.object(exchange, '_request_order_update', AsyncMock(return_value={'code': '0', 'data': [status]})), \
                patch.object(exchange, '_request_order_fills', AsyncMock(return_value={'code': '0', 'data': [trade]})), \
                patch.object(exchange, '_place_order', AsyncMock(side_effect=AssertionError('Recovery must not POST'))):
            loop.run_until_complete(owner.automatic_recovery.restore(core))
            loop.run_until_complete(owner.automatic_recovery.project(core))
            loop.run_until_complete(owner.automatic_recovery.project(core))
        positions = recorder.get_all_positions()
        assert len(positions) == 1 and positions[0].amount == Decimal('.1') and positions[0].cum_fees_quote == Decimal('.01')
        with manager.get_new_session() as session:
            assert session.query(TradeFill).count() == 1
            assert session.query(RuntimeJournal).filter_by(kind='hold').count() == 1
            assert session.query(RuntimeJournal).filter_by(kind='fill', applied=False).count() == 0
    finally:
        owner.release()
        install_guard(None)
        recorder.stop()
        loop.run_until_complete(exchange._web_assistants_factory.close())
        MarketsRecorder._shared_instance = previous
        loop.close()
        asyncio.set_event_loop(None)
