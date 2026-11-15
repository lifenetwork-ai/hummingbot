"""HTTP read deadlines keep a real unresponsive DB worker and owner isolated."""
import asyncio
from test.integration.postgres.response_loss_proxy import response_loss_proxy

import pytest
from aiohttp import BasicAuth, web
from aiohttp.test_utils import TestClient, TestServer

from hummingbot.cli.admin_api import ADMIN_PREFIX, AdminAPI, admin_middleware
from hummingbot.cli.admin_service import AdminService
from hummingbot.client.config.admin_config import AdminSettings
from hummingbot.client.config.runtime_config import RuntimeSettings
from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.core.engine_diagnostics import EngineDiagnostics
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.connection import create_postgres_engine
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres


@pytest.mark.asyncio
async def test_http_status_deadline_retains_physical_ping_work_without_losing_owner(pg_engine, monkeypatch):
    import hummingbot.core.engine_diagnostics as diagnostic

    await asyncio.to_thread(bootstrap_postgres, pg_engine, auto_migrate=True)
    store = BotStateStore(pg_engine, 'hummingbot')
    await asyncio.to_thread(store.ensure)
    owner = BotOwnership(pg_engine, 'hummingbot')
    assert await asyncio.to_thread(owner.try_acquire)
    identity = owner.owner_uuid, owner.generation, owner.backend_pid
    tracer = EngineDiagnostics(emit=lambda _: None)
    monkeypatch.setattr(diagnostic, '_ACTIVE', tracer)
    monkeypatch.setattr(AdminAPI, 'STATUS_READ_SECONDS', .05)
    try:
        async with response_loss_proxy(pg_engine.url) as proxy:
            settings = RuntimeSettings.from_environment({'DATABASE_URL': proxy.url.render_as_string(hide_password=False)})
            engine = create_postgres_engine(settings)
            api_store = BotStateStore(engine, 'hummingbot')
            try:
                assert await asyncio.to_thread(api_store.read, publication=True)  # Prime the exact pooled reader.
                host = AdminService(settings, engine_port=0, stop=asyncio.Event())
                host.store, host.phase, host.bootstrapped = api_store, 'api_running', True
                admin = AdminSettings(True, 'operator', 'offline-password', basic_auth_enabled=True)
                app = web.Application(middlewares=[admin_middleware(admin)])
                app.router.add_get('/live', host.health)
                api = AdminAPI(host, admin)
                api.register(app)
                async with TestClient(TestServer(app), headers={
                        'Authorization': BasicAuth('operator', 'offline-password').encode()}) as http:
                    proxy.drop_responses = True
                    try:
                        response = await asyncio.wait_for(http.get(ADMIN_PREFIX + '/status'), .8)
                        body = await response.json()
                        assert response.status == 503 and body['code'] == 'unavailable'
                        assert body['service']['stale'] and body['service']['state_source'] == 'process'
                        assert api._database_executor.active == 1
                        assert any(s['operation'] == 'db.ping' for s in tracer.snapshot()['active'])
                        assert (await http.get('/live')).status == 200
                        assert await asyncio.to_thread(owner.check_owner)
                        assert identity == (owner.owner_uuid, owner.generation, owner.backend_pid)
                        assert not owner.lost
                    finally:
                        proxy.drop_responses = False
                    # A dropped reply cannot be repaired by releasing its worker
                    # early. Join the native I/O deadline/reconnect, then fresh work.
                    assert await asyncio.wait_for(api.run_database(lambda: 'joined'), 6) == 'joined'
                    assert api._database_executor.active == api._database_executor.pending == 0
                    assert (await http.get(ADMIN_PREFIX + '/status')).status == 200
                    assert not tracer.snapshot()['active']
            finally:
                engine.dispose()
    finally:
        owner.release()
