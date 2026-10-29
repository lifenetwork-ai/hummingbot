"""Correlate slow HTTP work with its physical worker and SQL without locals."""
import inspect
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession, web
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool

from hummingbot.cli.admin_api import AdminAPI
from hummingbot.cli.admin_database import AdminDatabaseExecutor
from hummingbot.cli.admin_models import APIModel
from hummingbot.client.config.admin_config import AdminSettings


@pytest.mark.asyncio
async def test_request_probe_correlates_real_worker_pool_sql_and_restores_hooks(tmp_path, monkeypatch):
    offline = Path(__file__).resolve().parents[2] / 'e2e/offline'
    monkeypatch.syspath_prepend(str(offline))
    monkeypatch.syspath_prepend(str(offline / 'process_isolation'))
    from test.e2e.offline.process_isolation import request_probe

    monkeypatch.setenv('HBOT_LOAD_TRACE_DIRECTORY', str(tmp_path))
    original_submit = AdminDatabaseExecutor.submit
    original_connect = QueuePool.connect
    original_validate = inspect.getattr_static(APIModel, 'model_validate')
    database = create_engine('sqlite://', poolclass=QueuePool, connect_args={'check_same_thread': False})
    api = AdminAPI(SimpleNamespace(store=None), AdminSettings.from_environment())
    app = web.Application()
    api.register(app)
    request_probe.attach(app)

    def query(fail):
        with database.connect() as connection:
            time.sleep(.12)
            return connection.execute(text('SELECT :private_value' if not fail else 'SELECT missing_table_column'),
                                      {'private_value': 'never-copy-this-parameter'}).scalar()

    async def handler(request):
        await api.run_database(query, request.path == '/fail')
        return web.json_response({'ok': True})

    app.router.add_get('/ok', handler)
    app.router.add_get('/fail', handler)
    runner = web.AppRunner(app)
    try:
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        async with ClientSession() as client:
            for path, identity, status in (('/ok', 'valid-id', 200), ('/fail', 'failure-id', 500)):
                async with client.get(f'http://127.0.0.1:{port}{path}', headers={'X-Load-Request-ID': identity}) as response:
                    await response.read()
                    assert response.status == status
    finally:
        await runner.cleanup()
        database.dispose()
    events = [json.loads(line) for line in next(tmp_path.glob('api-requests-*.jsonl')).read_text().splitlines()]
    traces = {row['request_id']: row for row in events if row.get('operation') == 'http.request'}
    assert set(traces) == {'valid-id', 'failure-id'}
    for trace in traces.values():
        spans = {row['operation']: row for row in trace['spans']}
        assert {'http.request', 'api_database_worker_start', 'pool_checkout_ms', 'sql'} <= spans.keys()
        assert spans['api_database_worker_start']['queue_wait_ms'] >= 0
        assert spans['sql']['trace_id'] == trace['trace_id']
    assert next(row for row in traces['failure-id']['spans'] if row['operation'] == 'sql')['error']
    assert 'never-copy-this-parameter' not in json.dumps(events)
    assert AdminDatabaseExecutor.submit is original_submit and QueuePool.connect is original_connect
    assert inspect.getattr_static(APIModel, 'model_validate') is original_validate
    summary = json.loads(next(tmp_path.glob('api-requests-*-summary.json')).read_text())
    assert summary['active_spans'] == 0 and summary['sink']['writer_alive'] is False
    assert summary['sink']['dropped_records'] == 0
