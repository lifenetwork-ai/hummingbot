"""Operational views use native provenance, never the cached SQLite recorder."""
import asyncio
import json
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_events import event
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_recorder_application import close, setup
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from hummingbot.client.command.export_command import ExportCommand
from hummingbot.client.command.history_command import HistoryCommand
from hummingbot.client.command.status_command import StatusCommand
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.operations import PgRecorderOperations


class CLI(HistoryCommand, ExportCommand, StatusCommand):
    def __init__(self, s):
        self.trading_core = SimpleNamespace(strategy=SimpleNamespace(
            controllers=s.strategy.controllers, executor_orchestrator=s.runner), trade_fill_db=MagicMock())
        self.trading_core.trade_fill_db.get_new_session.side_effect = AssertionError('SQLite fallback')
        self.strategy_file_name, self.init_time = 'life.yml', 0
        self.client_config_map = SimpleNamespace(commands_timeout=SimpleNamespace(other_commands_timeout=3))
        self.messages = []

    def notify(self, message):
        self.messages.append(message)


@pytest.mark.asyncio
async def test_cli_native_exact_trade_fees_and_json_do_not_open_sqlite(database, monkeypatch):
    s = await setup(database, monkeypatch)
    try:
        at = int((await s.financial.observation_cut(s.cfg.binding)).observed_at.timestamp() * 1000)
        fill = event(s.cfg.binding, s.source, at, 'OrderFilled', price='1.123456789012345678', amount='0.123456789012345678',
                     fee_quote='-0.000001234567890123', fee_json=json.dumps({'percent': '0', 'flat_fees': [{'token': 'USDT', 'amount': '-0.000001234567890123'}]}))
        await s.app.enqueue_event(fill)
        await s.app.drain()
        cli = CLI(s)
        data = await cli.async_get_history_trades_json()
        assert data[0]['price'] == '1.123456789012345678' and data[0]['amount'] == '0.123456789012345678'
        assert data[0]['fee_quote'] == '-0.000001234567890123'
        assert data[0]['market'] == 'okx' and data[0]['controller_id'] == 'controller1'
        assert json.loads(data[0]['fee_json'])['flat_fees'][0]['amount'] == '-0.000001234567890123'
        with pytest.raises(StorageError, match='PG_HISTORY_ASYNC_REQUIRED'):
            cli.get_history_trades_json()
        await cli._postgres_history_report(0)
        assert 'PostgreSQL' in cli.messages[-1] and '-0.000001' in cli.messages[-1]
        assert 'reporting only' in cli.messages[-1]
        cli.trading_core.trade_fill_db.get_new_session.assert_not_called()
    finally:
        await close(s)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['pending', 'missing', 'foreign', 'legacy', 'unavailable', 'capacity', 'bytes'])
async def test_cli_reports_incomplete_provenance_without_false_empty_history(database, monkeypatch, fault):
    s = await setup(database, monkeypatch)
    try:
        at = int((await s.financial.observation_cut(s.cfg.binding)).observed_at.timestamp() * 1000)
        fill = event(s.cfg.binding, s.source, at, 'OrderFilled')
        if fault == 'pending':
            await s.app.events[0].stage(fill, cut=(await s.financial.snapshot(s.cfg.binding)).cut, owner=s.t.owner)
        else:
            await s.app.enqueue_event(fill)
            await s.app.drain()
        if fault == 'missing':
            await database[0].execute('DELETE FROM life_recorder."TradeFill"')
        elif fault == 'foreign':
            await database[0].execute('UPDATE life_recorder."TradeFill" SET config_file_path=\'foreign.yml\'')
        elif fault == 'legacy':
            await database[0].execute('''INSERT INTO life_recorder."TradeFill"
                SELECT config_file_path,strategy,market,symbol,base_asset,quote_asset,timestamp,
                  order_id,trade_type,order_type,price,amount,leverage,trade_fee,trade_fee_in_quote,
                  'legacy-unqualified',position FROM life_recorder."TradeFill"''')
        elif fault == 'unavailable':
            await s.financial.close()
        elif fault == 'capacity':
            s.app.repository.max_rows = 1  # Config plus the event exceeds the complete-view bound.
        elif fault == 'bytes':
            s.app.repository.max_read_bytes = 1
        cli = CLI(s)
        await cli._postgres_history_report(0)
        assert 'incomplete' in cli.messages[-1].lower(), cli.messages
        assert 'No past trades' not in cli.messages[-1]
        with pytest.raises(StorageError):
            await cli.async_get_history_trades_json()
        cli.trading_core.trade_fill_db.get_new_session.assert_not_called()
        assert not s.t.session.sent
    finally:
        await close(s)


@pytest.mark.asyncio
async def test_native_history_missing_application_cannot_select_a_legacy_database(database, monkeypatch):
    s = await setup(database, monkeypatch)
    try:
        cli = CLI(s)
        cli.trading_core.strategy.executor_orchestrator._repository_reporting = None
        with pytest.raises(StorageError, match='PG_HISTORY_ASSEMBLY_REQUIRED'):
            await cli.async_get_history_trades_json()
        cli.trading_core.trade_fill_db.get_new_session.assert_not_called()
    finally:
        await close(s)


@pytest.mark.asyncio
@pytest.mark.parametrize('abandon', ['cancel', 'timeout'])
async def test_operational_timeout_backpressure_and_cancel_keep_original_physical_slot(database, monkeypatch, abandon):
    s = await setup(database, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    try:
        original = s.financial._runtime._admit

        async def blocked(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)
        monkeypatch.setattr(s.financial._runtime, '_admit', blocked)
        reader = PgRecorderOperations(s.app)
        task = asyncio.create_task(reader.snapshot(start_ms=0, timeout_s=3 if abandon == 'cancel' else 0.05))
        await asyncio.wait_for(entered.wait(), 3)
        if abandon == 'cancel':
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert (await task).reason == 'PG_HISTORY_TIMEOUT'
        refused = await reader.snapshot(start_ms=0, timeout_s=0.01)
        assert refused.reason == 'PG_HISTORY_BACKPRESSURE'
        release.set()
        await asyncio.shield(s.app._operations_task)
        await asyncio.sleep(0)
        assert (await reader.snapshot(start_ms=0, timeout_s=3)).reporting_complete
        assert not s.t.session.sent
    finally:
        release.set()
        await close(s)


@pytest.mark.asyncio
async def test_actual_status_and_export_use_native_report_and_restore_ui(database, monkeypatch, tmp_path):
    import csv

    s = await setup(database, monkeypatch)
    try:
        at = int((await s.financial.observation_cut(s.cfg.binding)).observed_at.timestamp() * 1000)
        await s.app.enqueue_event(event(s.cfg.binding, s.source, at, 'OrderFilled', fee_quote='-0.000001'))
        await s.app.drain()
        cli = CLI(s)
        cli.app = SimpleNamespace(hide_input=False, change_prompt=MagicMock())
        cli.placeholder_mode = False
        cli.client_config_map.log_file_path = str(tmp_path)
        cli.prompt_new_export_file_name = AsyncMock(return_value='trades.csv')
        assert 'PostgreSQL reporting only' in await cli.strategy_status()
        await cli.export_trades()
        with (tmp_path / 'trades.csv').open() as stream:
            rows = list(csv.DictReader(stream))
        assert rows[0]['fee_quote'] == '-0.000001'
        assert not cli.placeholder_mode and not cli.app.hide_input
        original = (tmp_path / 'trades.csv').read_bytes()
        await cli.export_trades()
        assert (tmp_path / 'trades.csv').read_bytes() == original
        assert 'failed' in cli.messages[-1]
        assert not cli.placeholder_mode and not cli.app.hide_input
        cli.trading_core.trade_fill_db.get_new_session.assert_not_called()
        cli.strategy_file_name = 'foreign.yml'
        with pytest.raises(StorageError, match='PG_HISTORY_SOURCE_SCOPE'):
            await cli.async_get_history_trades_json()
    finally:
        await close(s)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['direct_dml', 'schema_ddl', 'missing_read'])
async def test_native_recorder_principal_rejects_unscoped_writes_and_missing_read_grant(database, monkeypatch, fault):
    s = await setup(database, monkeypatch)
    try:
        role = s.cfg.principal  # Fixed disposable fixture role, never an environment DSN.
        sql = {
            'direct_dml': f'GRANT INSERT ON life_recorder."TradeFill" TO "{role}"',
            'schema_ddl': f'GRANT CREATE ON SCHEMA life_recorder TO "{role}"',
            'missing_read': f'REVOKE EXECUTE ON FUNCTION life_financial.recorder_scope_verified(uuid,text,jsonb) FROM "{role}"',
        }[fault]
        await database[0].execute(sql)
        report = await PgRecorderOperations(s.app).snapshot(start_ms=0, timeout_s=3)
        assert not report.reporting_complete
        assert report.reason == 'PG_RECORDER_ROLE_PROFILE_INVALID'
        assert not s.financial._runtime.healthy
        assert not s.t.session.sent
    finally:
        await close(s)


@pytest.mark.asyncio
async def test_unrelated_legacy_source_is_excluded_but_registered_legacy_source_blocks_startup(database, monkeypatch):
    s = await setup(database, monkeypatch)
    try:
        at = int((await s.financial.observation_cut(s.cfg.binding)).observed_at.timestamp() * 1000)
        await s.app.enqueue_event(event(s.cfg.binding, s.source, at, 'OrderFilled'))
        await s.app.drain()
        await database[0].execute('''INSERT INTO life_recorder."TradeFill"
            SELECT 'unrelated.yml',strategy,market,symbol,base_asset,quote_asset,timestamp,
              order_id,trade_type,order_type,price,amount,leverage,trade_fee,trade_fee_in_quote,
              'legacy-unqualified',position FROM life_recorder."TradeFill"''')
        report = await PgRecorderOperations(s.app).snapshot(start_ms=0, timeout_s=3)
        assert report.reporting_complete and len(report.trades) == 1
        await s.app.recover()
        before = await s.financial.balance(s.cfg.binding)
        await database[0].execute('''UPDATE life_recorder."TradeFill" SET config_file_path='life.yml'
            WHERE exchange_trade_id='legacy-unqualified' ''')
        with pytest.raises(StorageError, match='PG_RECORDER_IMPORT_REQUIRED'):
            await s.app.recover()
        assert not s.app.ready and await s.financial.balance(s.cfg.binding) == before
        assert not s.t.session.sent
    finally:
        await close(s)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['no_controllers', 'empty_controllers', 'backend_toggle', 'lost_runtime', 'detached'])
async def test_lost_native_context_never_turns_history_into_a_legacy_database_query(database, monkeypatch, fault):
    s = await setup(database, monkeypatch)
    try:
        cli = CLI(s)
        strategy = cli.trading_core.strategy
        cli._postgres_history_reader()  # Remember only that this source is native.
        if fault == 'no_controllers':
            strategy.controllers = None
        elif fault in ('empty_controllers', 'lost_runtime'):
            strategy.controllers = {}
        elif fault == 'backend_toggle':
            monkeypatch.setattr(s.controller.config, 'storage_backend', 'file')
        elif fault == 'detached':
            cli.trading_core.strategy = None
        if fault == 'lost_runtime':
            strategy.executor_orchestrator._repository_reporting = None
            strategy.service = SimpleNamespace(config=s.controller.config)
        with pytest.raises(StorageError, match='PG_HISTORY_ASSEMBLY_REQUIRED'):
            await cli.async_get_history_trades_json()
        cli.history()
        cli.list_trades(0)
        assert len(cli.messages) == 2
        assert all('incomplete' in message for message in cli.messages)
        cli.trading_core.trade_fill_db.get_new_session.assert_not_called()
    finally:
        await close(s)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['account', 'controller', 'sources'])
async def test_scoped_definer_read_rejects_forged_namespace_without_reading_financial_history(database, monkeypatch, fault):
    from dataclasses import asdict
    from uuid import uuid4

    s = await setup(database, monkeypatch)
    try:
        before = await s.financial.balance(s.cfg.binding)
        args = [s.cfg.account_id, 'controller1', [asdict(s.source)]]
        if fault == 'account':
            args[0] = uuid4()
        elif fault == 'controller':
            args[1] = 'foreign'
        else:
            args[2] = [{**args[2][0], 'config_file_path': 'foreign.yml'}]

        async def read(c):
            return await c.fetchval('SELECT life_financial.recorder_scope_verified($1,$2,$3)', *args)
        assert await s.financial._runtime._admit(read, safety=True) is False
        assert await s.financial.balance(s.cfg.binding) == before
        assert not s.t.session.sent
    finally:
        await close(s)


@pytest.mark.asyncio
async def test_status_keeps_configured_uid_separate_from_authentication_and_risk(database, monkeypatch):
    s = await setup(database, monkeypatch)
    try:
        report = await PgRecorderOperations(s.app).snapshot(start_ms=0, timeout_s=3)
        assert report.configured_account_uid == s.cfg.binding.account_uid
        assert report.authenticated_account_uid is None
        assert report.trading_permission is False
        assert 'configured UID' in report.format_status() and 'authentication not established by reporting' in report.format_status()
        assert 'recovery complete' not in report.format_status().lower()
        bad_config = replace(s.cfg.binding, account_uid='999')
        monkeypatch.setattr(s.controller.config, 'storage_binding', bad_config)
        with pytest.raises(StorageError, match='PG_HISTORY_ASSEMBLY_REQUIRED'):
            await CLI(s).async_get_history_trades_json()
    finally:
        await close(s)
