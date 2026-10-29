"""Test-only loop stall/process-death injection; existing foreground engine."""
import asyncio
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

from aiohttp import web

sys.path.insert(0, str(Path.cwd()))
from hummingbot.cli import service  # noqa: E402
from hummingbot.client.config.runtime_config import RuntimeSettings  # noqa: E402

original_settings = RuntimeSettings.from_environment
if os.environ.get('ARCH_EXPERIMENT_SPLIT') == '1':
    RuntimeSettings.from_environment = classmethod(lambda cls, environ=None:
                                                   replace(original_settings(environ), database_pool_size=3))
original_app = service.create_service_app


def create_app(host, admin_settings=None):
    app = original_app(host, admin_settings)

    async def stall(request):
        asyncio.get_running_loop().call_later(.15, time.sleep, 2.5)
        return web.json_response({'scheduled_stall_seconds': 2.5})

    async def crash(request):
        asyncio.get_running_loop().call_later(.15, os._exit, 77)
        return web.json_response({'scheduled_engine_exit': 77})

    app.router.add_post('/diagnostic/stall', stall)
    app.router.add_post('/diagnostic/crash', crash)
    return app


service.create_service_app = create_app
service.main()
