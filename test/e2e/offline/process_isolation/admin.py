"""Experimental API process: existing handlers/store, no trading owner/keys."""
import asyncio
import os
import signal
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from aiohttp import ClientSession, ClientTimeout, web

sys.path.insert(0, str(Path.cwd()))
from hummingbot.cli.admin_api import AdminAPI, admin_middleware  # noqa: E402
from hummingbot.client.config.admin_config import AdminSettings  # noqa: E402
from hummingbot.client.config.runtime_config import RuntimeSettings  # noqa: E402
from hummingbot.logger.service_logging import configure_service_logging  # noqa: E402
from hummingbot.model.bot_state_store import BotStateStore  # noqa: E402
from hummingbot.model.db_migration.postgres.connection import create_postgres_engine  # noqa: E402
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres  # noqa: E402


async def main():
    configure_service_logging()
    settings = replace(RuntimeSettings.from_environment(), enable_auto_migration=False, database_pool_size=2)
    stop = asyncio.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(signum, stop.set)
    host = SimpleNamespace(store=None, revision=os.environ.get('K_REVISION', 'architecture-experiment'))
    admin = AdminSettings.from_environment()
    app = web.Application(middlewares=[admin_middleware(admin)])
    ready = False
    engine = None
    async with ClientSession(timeout=ClientTimeout(total=.3)) as client:
        async def health(request):
            if not ready:
                return web.json_response({'state': 'api_initializing'}, status=503)
            try:
                async with client.get('http://127.0.0.1:8081' + request.path) as response:
                    return web.json_response(await response.json(), status=response.status)
            except (OSError, TimeoutError):
                return web.json_response({'state': 'engine_unavailable'}, status=503)
        for path in ('/startup', '/live', '/ready'):
            app.router.add_get(path, health)
        AdminAPI(host, admin).register(app)
        runner = web.AppRunner(app, access_log=None, shutdown_timeout=.1)
        await runner.setup()
        try:
            await web.TCPSite(runner, '0.0.0.0', 8080).start()
            async with asyncio.timeout(180):
                while not stop.is_set():
                    try:
                        async with client.get('http://127.0.0.1:8081/startup') as response:
                            if response.status == 200:
                                break
                    except (OSError, TimeoutError):
                        pass
                    await asyncio.sleep(.1)
            engine = create_postgres_engine(settings)
            await asyncio.to_thread(bootstrap_postgres, engine, auto_migrate=False)
            host.store = BotStateStore(engine, settings.bot_id)
            ready = True
            await stop.wait()
        finally:
            await runner.cleanup()
            if engine is not None:
                engine.dispose()


asyncio.run(main())
