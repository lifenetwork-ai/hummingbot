"""Foreground host for the existing engine; PostgreSQL is its control plane.

Imports that construct the application are deliberately deferred until signals
and the HTTP server are installed. No daemon, PID file or runtime snapshot exists.
"""
import asyncio
import hashlib
import json
import logging
import os
import signal
import threading
import time
from pathlib import Path

from aiohttp import web


class ShutdownDeadline:
    """Independent process deadline, including synchronous DB/cleanup work."""

    def __init__(self, stop, seconds=9.0):
        self.stop = stop
        self.seconds = seconds
        self.timer = None
        self.previous = {}

    def request(self, *_):
        if self.timer is None:
            self.timer = threading.Timer(self.seconds, os._exit, args=(1,))
            self.timer.daemon = True
            self.timer.start()
        self.stop.set()

    def install(self):
        for sig in (signal.SIGTERM, signal.SIGINT):
            self.previous[sig] = signal.signal(sig, self.request)

    def close(self):
        if self.timer is not None:
            self.timer.cancel()
        for sig, previous in self.previous.items():
            signal.signal(sig, previous)


class ServiceHost:
    def __init__(self, settings, stop, deadline):
        self.settings = settings
        self.stop = stop
        self.deadline = deadline
        self.bootstrapped = False
        self.phase = "bootstrapping"
        self.error = None
        self.engine = self.store = self.owner = self.hb = None
        self.applied_version = None
        self.start_task = None
        self.running = False
        self.halt_requested = False
        self.catalogue = {}
        self.config_hashes = {}
        self.revision = os.environ.get("K_REVISION") or os.environ.get("COMMIT_SHA") or "local"
        self.log = logging.getLogger(__name__)

    def configure_logging(self):
        host = self

        class Context(logging.Filter):
            def filter(self, record):
                record.hbot_id = host.settings.bot_id
                record.hbot_config = getattr(host.owner, "config_file_path", None) or "-"
                record.hbot_owner = getattr(host.owner, "owner_uuid", None) or "-"
                record.hbot_generation = getattr(host.owner, "generation", None) or 0
                record.hbot_revision = host.revision
                return True

        for handler in logging.getLogger().handlers:
            handler.addFilter(Context())
            handler.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s bot=%(hbot_id)s config=%(hbot_config)s "
                "owner=%(hbot_owner)s generation=%(hbot_generation)s revision=%(hbot_revision)s %(message)s"))

    async def health(self, request):
        path = request.path
        healthy = not self.stop.is_set()
        if path == "/startup":
            healthy = healthy and self.bootstrapped
        elif path == "/ready":
            healthy = healthy and self.running
        return web.json_response({"state": self.phase}, status=200 if healthy else 503)

    async def bootstrap(self):
        from hummingbot.cli.strategy_configs import STRATEGY_TYPES, config_path, list_configs
        from hummingbot.client.config.config_helpers import load_client_config_map_from_file
        from hummingbot.client.runner import bootstrap_application
        from hummingbot.core.bot_ownership import BotOwnership
        from hummingbot.model.bot_state_store import BotStateStore
        from hummingbot.model.sql_connection_manager import SQLConnectionManager
        self.engine = SQLConnectionManager.initialize_postgres(self.settings)
        self.store = BotStateStore(self.engine, self.settings.bot_id)
        self.store.ensure()
        self.owner = BotOwnership(self.engine, self.settings.bot_id)
        self.catalogue = {kind: list_configs(kind) for kind in STRATEGY_TYPES}
        self.config_hashes = {kind: {name: hashlib.sha256(config_path(kind, name).read_bytes()).hexdigest()
                                     for name in files} for kind, files in self.catalogue.items()}
        client = load_client_config_map_from_file(read_only=True)
        if client.mqtt_bridge.mqtt_autostart:
            raise ValueError("MQTT autostart is outside the reviewed service scope.")
        self.hb = await bootstrap_application(client, None, headless=True, environment_connectors=[],
                                              read_only_configuration=True)
        self.configure_logging()
        # A strategy-triggered risk stop uses this same bounded service lifecycle.
        # It must not start the interactive stop coroutine behind the host's back.
        self.hb.stop = self.strategy_stop
        await self.validate_selection(self.store.read())
        self.bootstrapped = True
        self.phase = "standby"
        self.log.info("Service bootstrap completed bot=%s revision=%s", self.settings.bot_id, self.revision)

    async def validate_selection(self, row):
        from hummingbot.cli.strategy_configs import config_path, read_yaml
        from hummingbot.client import settings
        from hummingbot.client.config.config_helpers import all_configs_complete, load_strategy_config_map_from_file
        from hummingbot.client.config.environment_credentials import load_connector_credentials
        from hummingbot.client.config.runtime_config import RuntimeConfigurationError
        from hummingbot.client.runner import controller_runner_config
        from hummingbot.core.order_submission_guard import SUPPORTED_CONNECTORS
        from hummingbot.core.trading_core import TradingCore
        name, kind = row["config_file_path"], row["config_type"]
        if name is None and row["desired_state"] == "stopped":
            return
        if (kind not in self.catalogue or name not in self.catalogue[kind] or Path(name).name != name):
            raise RuntimeConfigurationError("Selected YAML configuration is not in this revision's deployed config set.")
        settings.required_exchanges.clear()
        if kind == "v1-strategy":
            config = await load_strategy_config_map_from_file(config_path(kind, name), read_only=True)
            if not all_configs_complete(config, self.hb.client_config_map):
                raise RuntimeConfigurationError("Selected strategy configuration is incomplete.")
            connectors = set(settings.required_exchanges)
        else:
            data = controller_runner_config(name) if kind == "controller" else read_yaml(config_path(kind, name))
            module = data.get("script_file_name", "").removesuffix(".py")
            if not module.isidentifier():
                raise RuntimeConfigurationError("Selected script module identity is invalid.")
            # Reuse configuration/model/market discovery without constructing a
            # strategy, recorder, clock or connector on a standby process.
            validator = TradingCore(self.hb.client_config_map, enable_gateway_monitor=False)
            validator._config_data = data
            strategy_class, _ = validator.load_v2_class(module)
            connectors = set(strategy_class.markets)
        if not connectors or connectors - set(SUPPORTED_CONNECTORS):
            raise RuntimeConfigurationError("The service requires reviewed OKX spot/perpetual connectors.")
        for connector in sorted(connectors):
            load_connector_credentials(connector)

    def strategy_stop(self, *args, **kwargs):
        if self.owner is not None:
            self.owner.require_recovery("The strategy requested a risk stop; operator review is required.")
        self.running = False
        self.halt_requested = True

    async def start_strategy(self, row):
        from hummingbot.client.runner import load_and_start_strategy
        options = {"v1-strategy": "config_file_name", "v2-script": "v2_conf", "controller": "controller_conf"}
        success = await load_and_start_strategy(
            self.hb, headless=True, read_only_configuration=True,
            **{options[row["config_type"]]: row["config_file_path"]})
        if not success:
            self.owner.require_recovery("Strategy initialization/reconciliation failed; inspect sanitized logs.")
        return success

    async def stop_core(self):
        self.running = False
        self.owner.allow_new_orders = False
        if self.start_task is not None and not self.start_task.done():
            self.start_task.cancel()
            done, _ = await asyncio.wait({self.start_task}, timeout=0.5)
            if not done:
                return False
        self.start_task = None
        core = self.hb.trading_core
        if core.strategy is None and not core.connector_manager.connectors and not core._is_running:
            return True
        return await self.owner.shutdown_core(core)

    def snapshot(self):
        core = self.hb.trading_core
        return {"name": self.settings.bot_id, "running": self.running, "phase": self.phase,
                "error": self.error, "available_configs": self.catalogue,
                "configuration_sha256": self.config_hashes, "engine": core.get_status(),
                "balances": {name: {asset: str(value) for asset, value in connector.get_all_balances().items()}
                             for name, connector in core.connector_manager.connectors.items()},
                "format_status": None}

    async def observe_once(self):
        from hummingbot.core.order_submission_guard import install_guard
        if not self.owner.try_acquire():
            self.phase = "standby"
            return
        install_guard(self.owner)
        row = self.owner.check_owner()
        version = row["control_version"]
        if version != self.applied_version:
            self.phase = "stopping"
            if not await self.stop_core():
                raise RuntimeError("Service cleanup did not complete; process exit is required.")
            self.applied_version = version
            self.error = None
            self.halt_requested = False
            try:
                await self.validate_selection(row)
                if row["desired_state"] == "running":
                    self.owner.prepare(version, row["config_file_path"])
                    self.hb.trading_core.before_strategy_start = self.owner.reconcile
                    self.phase = "starting"
                    self.start_task = asyncio.create_task(self.start_strategy(row))
                else:
                    self.phase = "stopped"
            except Exception as error:
                self.phase, self.error = "halted", type(error).__name__
                self.log.error("Configuration bootstrap failed type=%s version=%s", self.error, version)
        if self.start_task is not None and self.start_task.done():
            success = not self.start_task.cancelled() and self.start_task.exception() is None and self.start_task.result()
            self.start_task = None
            if success:
                self.running, self.phase = True, "running"
            else:
                self.running, self.phase, self.error = False, "halted", "StrategyStartFailed"
                if not await self.stop_core():
                    raise RuntimeError("Failed startup cleanup did not complete.")
        core = self.hb.trading_core
        if self.halt_requested:
            self.phase, self.error = "halted", "StrategyRiskStop"
            if not await self.stop_core():
                raise RuntimeError("Risk-stop cleanup did not complete.")
            self.halt_requested = False
        if self.running:
            if (core.trade_fill_db is None or not core.trade_fill_db.persistence_healthy
                    or not self.owner.allow_new_orders or not core._strategy_running):
                self.owner.require_recovery("Trading/recorder health requires reconciliation.")
                self.phase, self.error = "halted", "TradingHealthFailed"
                if not await self.stop_core():
                    raise RuntimeError("Degraded runtime cleanup did not complete.")
        if (self.owner.check_owner()["recovery_state"] or {}).get("halt"):
            self.running, self.phase, self.error = False, "halted", "RecoveryRequired"
        self.owner.store.publish(owner_uuid=self.owner.owner_uuid, owner_generation=self.owner.generation,
                                 version=version, status=json.loads(json.dumps(self.snapshot(), default=str)),
                                 revision=self.revision)

    async def run(self):
        await self.bootstrap()
        while not self.stop.is_set():
            await self.observe_once()
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass

    async def close(self):
        from hummingbot.core.order_submission_guard import install_guard
        self.running, self.phase = False, "stopping"
        if self.owner is not None and self.owner.connection is not None:
            # Any cleanup error keeps the lock until OS process teardown. The
            # deadline must remain armed when this returns False.
            try:
                if not await self.stop_core():
                    return False
                row = self.owner.check_owner()
                self.owner.store.publish(owner_uuid=self.owner.owner_uuid, owner_generation=self.owner.generation,
                                         version=row["control_version"], status=self.snapshot(), revision=self.revision)
                self.owner.release()
            except Exception:
                self.log.error("Service cleanup incomplete; ownership retained until process exit.")
                return False
        install_guard(None)
        if self.engine is not None:
            self.engine.dispose()
        return True


async def run_service():
    from hummingbot.client.config.runtime_config import RuntimeSettings
    stop = asyncio.Event()
    deadline = ShutdownDeadline(stop)
    deadline.install()
    runner = host = None
    clean = False
    started = time.monotonic()
    try:
        settings = RuntimeSettings.from_environment()
        host = ServiceHost(settings, stop, deadline)
        host.configure_logging()
        app = web.Application()
        for path in ("/startup", "/live", "/ready"):
            app.router.add_get(path, host.health)
        runner = web.AppRunner(app, access_log=None, shutdown_timeout=0.1)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", settings.port).start()
        await host.run()
        code = 0
    except Exception as error:
        # Driver/Pydantic exceptions may include secret inputs; never log raw
        # exception text or traceback at this outer process boundary.
        logging.getLogger(__name__).error("Service stopped type=%s", type(error).__name__)
        code = 1
    finally:
        deadline.request()
        if host is not None:
            clean = await host.close()
        else:
            clean = True
        if runner is not None:
            await runner.cleanup()
        logging.getLogger(__name__).info("Service exit clean=%s elapsed_s=%.3f", clean, time.monotonic() - started)
        if clean:
            deadline.close()
    if not clean:
        # asyncio.run would wait indefinitely for arbitrary cancellation-hostile
        # tasks. Preserve the advisory session until immediate OS teardown.
        os._exit(1)
    return code


def serve():
    """Run the foreground PostgreSQL service (the CLI already loaded dotenv)."""
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(asyncio.run(run_service()))


def main():
    from hummingbot.client.config.runtime_config import load_environment
    load_environment()
    serve()


if __name__ == "__main__":
    main()
