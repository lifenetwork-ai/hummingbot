"""API host for durable commands and observations, without trading ownership."""
import asyncio
import logging
import os
import time

from aiohttp import ClientError, ClientSession, ClientTimeout, web


class AdminService:
    def __init__(self, settings, *, engine_port, stop):
        self.settings, self.engine_port, self.stop = settings, engine_port, stop
        self.engine = self.store = self.client = None
        self.bootstrapped = False
        self.closed = False
        self.phase = 'api_initializing'
        self.revision = os.environ.get('K_REVISION') or os.environ.get('COMMIT_SHA') or 'local'

    async def engine_health(self, path):
        try:
            async with self.client.get(f'http://127.0.0.1:{self.engine_port}{path}') as response:
                return response.status, await response.json()
        except (OSError, TimeoutError, ClientError, ValueError):
            return 503, {'state': 'engine_unavailable'}

    async def health(self, request):
        if self.stop.is_set():
            return web.json_response({'state': 'api_stopping'}, status=503)
        if request.path == '/live':
            # The parent supervises the engine independently. DB/engine loss
            # must not terminate an API loop that still serves qualified errors.
            return web.json_response({'state': self.phase})
        if not self.bootstrapped:
            return web.json_response({'state': self.phase}, status=503)
        status, body = await self.engine_health(request.path)
        return web.json_response(body, status=status)

    def service_status(self):
        return dict(observed_at=time.time(), state_source='process', persisted_state_available=False,
                    phase=self.phase, reason_code='reporting_unavailable', stale=True, incidents=[])

    async def run(self):
        from hummingbot.cli.service import create_service_app
        from hummingbot.model.bot_state_store import BotStateStore
        from hummingbot.model.db_migration.postgres.connection import create_postgres_engine
        from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres
        runner = web.AppRunner(create_service_app(self), access_log=None, shutdown_timeout=.1)
        self.client = ClientSession(timeout=ClientTimeout(total=.3))
        try:
            await runner.setup()
            await web.TCPSite(runner, '0.0.0.0', self.settings.port).start()
            while not self.stop.is_set():
                status, _ = await self.engine_health('/startup')
                if status == 200:
                    break
                try:
                    await asyncio.wait_for(self.stop.wait(), timeout=.1)
                except asyncio.TimeoutError:
                    pass
            if not self.stop.is_set():
                self.engine = create_postgres_engine(self.settings)
                # Only the engine migrates/ensures bot state. API startup is
                # read-only schema validation and never acquires ownership.
                await asyncio.to_thread(bootstrap_postgres, self.engine, auto_migrate=False)
                self.store = BotStateStore(self.engine, self.settings.bot_id)
                self.bootstrapped, self.phase = True, 'api_running'
                await self.stop.wait()
        finally:
            self.phase = 'api_stopping'
            await runner.cleanup()
            if self.engine is not None:
                self.engine.dispose()
            await self.client.close()
            self.closed = True


async def run_admin_service(settings, *, engine_port):
    from hummingbot.cli.service import ShutdownDeadline
    from hummingbot.cli.service_processes import failure_exit_code, failure_reason
    from hummingbot.logger.service_logging import configure_service_logging
    stop = asyncio.Event()
    deadline = ShutdownDeadline(stop)
    deadline.install()
    host = None
    try:
        configure_service_logging()
        host = AdminService(settings, engine_port=engine_port, stop=stop)
        await host.run()
        return 0
    except Exception as error:
        logging.getLogger(__name__).error('API service stopped reason=%s type=%s', failure_reason(error), type(error).__name__,
                                          extra={'event': 'api_service_failed', 'reason_code': failure_reason(error),
                                                 'error_type': type(error).__name__})
        return failure_exit_code(error)
    finally:
        deadline.request()
        if host is None or host.closed:
            deadline.close()
        else:
            # Keep a failed cleanup from hanging asyncio's executor shutdown or
            # leaving a child alive with a healthy-looking API process.
            os._exit(1)
