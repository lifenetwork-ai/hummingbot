"""Loopback-only Google acceptance page using the real admin identity handlers.

No trading/credential endpoints or trading engine are exposed. Use a separate
local hbot_auth_* PostgreSQL database; this is not the dashboard deployment.
"""
import argparse
import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web
from dotenv import dotenv_values

from hummingbot.cli.admin_api import ADMIN_PREFIX, AdminAPI, admin_middleware
from hummingbot.client.config.admin_config import AdminSettings
from hummingbot.client.config.runtime_config import RuntimeSettings
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.connection import create_postgres_engine
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres

ASSETS = Path(__file__).with_suffix("")
ALLOWED_API = frozenset({
    ("POST", "/access/login"), ("GET", "/access/me"),
    ("GET", "/access/bootstrap"), ("GET", "/access/accounts"),
    ("POST", "/access/accounts"), ("GET", "/audit"),
})


def create_app(settings, state):
    @web.middleware
    async def local_only(request, handler):
        if request.host not in {"localhost:3000", "127.0.0.1:3000"}:
            raise web.HTTPForbidden(text="Use http://localhost:3000")
        if request.path.startswith(ADMIN_PREFIX):
            if (request.method, request.path.removeprefix(ADMIN_PREFIX)) not in ALLOWED_API:
                raise web.HTTPNotFound()
        elif request.method != "GET" or request.path not in {"/", "/app.js", "/config"}:
            raise web.HTTPNotFound()
        response = await handler(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    app = web.Application(middlewares=[local_only, admin_middleware(settings)], client_max_size=16384)
    api = AdminAPI(None, settings, store=SimpleNamespace(store=state))
    api.register(app)

    async def index(request):
        return web.FileResponse(ASSETS / "index.html")

    async def javascript(request):
        return web.FileResponse(ASSETS / "app.js")

    async def config(request):
        return web.json_response({"client_id": settings.google_client_id})

    app.router.add_get("/", index)
    app.router.add_get("/app.js", javascript)
    app.router.add_get("/config", config)
    return app


async def serve(values):
    settings = AdminSettings.from_environment(values)
    runtime = RuntimeSettings.from_environment(values)
    if not settings.enabled or not settings.google_auth_enabled:
        raise ValueError("Enable the admin API and Google auth in the selected env file.")
    if runtime.database_url.host not in {"localhost", "127.0.0.1"} or not runtime.database_url.database.startswith("hbot_auth_"):
        raise ValueError("Use a separate loopback PostgreSQL database named hbot_auth_*.")
    engine = create_postgres_engine(runtime)
    runner = None
    try:
        bootstrap_postgres(engine, auto_migrate=True)
        state = BotStateStore(engine, runtime.bot_id)
        state.ensure()
        runner = web.AppRunner(create_app(settings, state), access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 3000).start()
        print("Google acceptance page: http://localhost:3000 (auth only; no trading engine)", flush=True)
        await asyncio.Event().wait()
    finally:
        if runner is not None:
            await runner.cleanup()
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    args = parser.parse_args()
    if not args.env_file.is_file():
        parser.error("The selected env file does not exist.")
    logging.basicConfig(level=logging.WARNING)
    try:
        asyncio.run(serve(dotenv_values(args.env_file, interpolate=False)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
