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
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from aiohttp import web

from hummingbot.core.mm_readiness import MM_READINESS_REASONS


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
        from hummingbot.cli.service_availability import ServiceAvailability
        self.availability = ServiceAvailability()
        self.observer_progress_at = None
        self.observer_wait_until = None
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
        self.mm_session = None
        self.mm_liquidity_state = None
        self.liquidity_observer = None
        self.mm_readiness_reason = None
        self.outflow_task = None
        self.next_outflow_poll = 0
        self.applied_mm_identity = None
        self.catalogue = {}
        self.config_hashes = {}
        self._daily_signature = None
        self._daily_next_write_at = 0
        self._daily_retry_at = 0
        self.reconciliation_task = None
        self.next_check_poll = 0
        from hummingbot.cli.funding_observer import FundingObserver
        from hummingbot.cli.service_observations import ServiceObservations
        self.log = logging.getLogger(__name__)
        self.observations = ServiceObservations()
        self.funding = FundingObserver(settings.bot_id, self.availability, self.log)
        self.revision = os.environ.get("K_REVISION") or os.environ.get("COMMIT_SHA") or "local"

    def mm_effective_config(self, session):
        from controllers.market_making.life_mm import LifeMMConfig
        configs = session.catalogue.controller_configs(session.profile_id, session.request.market_mode, None,
                                                       references=session.request.references)
        overrides = []
        for config in configs:
            anchors = session.anchors.get(config.id, {})
            effective = LifeMMConfig.model_validate({
                **config.model_dump(), "mm_session_id": session.id, "mm_generation": session.generation,
                "saved_life_anchor": anchors.get("life"), "saved_source_anchors": anchors.get("sources", {}),
            })
            overrides.append(effective.model_dump(mode="json"))
        result = {"script_file_name": "v2_with_controllers.py", "controller_config_overrides": overrides}
        if session.catalogue.outflow_policy() is not None:
            result["mm_spot_outflow_policy"] = session.catalogue.outflow_policy()
        if session.catalogue.bootstrap_policies() is not None:
            result["mm_bootstrap_policies"] = session.catalogue.bootstrap_policies()
        policy = session.catalogue.drawdown_policy()
        if policy is not None:
            result.update(max_global_drawdown_quote=policy["max_global_drawdown_quote"],
                          max_controller_drawdown_quote=policy["max_controller_drawdown_quote"],
                          mm_drawdown_scope=policy["scope"],
                          mm_drawdown_max_data_age_seconds=str(min(config.max_data_age_seconds for config in session.catalogue.markets.values())))
        return result

    def _mm_controllers(self):
        if self.mm_session is None or self.hb is None or self.hb.trading_core.strategy is None:
            return []
        configs = self.mm_session.catalogue.controller_configs(
            self.mm_session.profile_id, self.mm_session.request.market_mode, None,
            references=self.mm_session.request.references)
        current = self.hb.trading_core.strategy.controllers
        if set(current) != {config.id for config in configs}:
            return []
        return [current[config.id] for config in configs]

    def mm_startup_pending(self):
        """Do not abort read/account initialization just to rotate a random step.

        Expired step authority still blocks every new order at the send gates.
        Explicit stop/switch and overall timed expiry retain their normal cleanup.
        """
        if self.start_task is not None and not self.start_task.done():
            return True
        return any(getattr(controller, '_startup_stage', None) not in (None, 'ready')
                   for controller in self._mm_controllers())

    def _mm_available_quote(self, controllers):
        available = [Decimal(str(controller.market_data_provider.get_connector_with_fallback(
            controller.config.connector_name).get_available_balance("USDT"))) for controller in controllers]
        if any(not value.is_finite() or value < 0 for value in available):
            raise ValueError("Account collateral is unavailable")
        # Both adapters may expose the same collateral. Never add them together.
        return min(available)

    def mm_quote_valid(self, connector, order):
        from hummingbot.core.order_submission_guard import SubmissionBlocked
        controller = next((value for value in self._mm_controllers()
                           if (value.config.connector_name, value.config.trading_pair) == (connector.name, order.trading_pair)), None)
        try:
            if controller is not None and controller.config.spread_policy.volatility.defense is not None:
                from hummingbot.core.order_submission_guard import current_quote_level
                valid = controller.spread_quote_valid(order.trade_type, order.price, order.amount, level_id=current_quote_level())
            else:
                valid = controller is not None and controller.spread_quote_valid(order.trade_type, order.price, order.amount)
        except ValueError:
            valid = False
        if not valid:
            raise SubmissionBlocked("spread_policy_quote_rejected")

    def mm_runtime_ready(self, *, allow_bootstrap_start=False):
        self.mm_readiness_reason = None

        def waiting(reason):
            self.mm_readiness_reason = reason
            return False

        try:
            defense = getattr(self.owner, 'volatility_checkpoints', None)
            if defense is not None and not defense.attach():
                return waiting('volatility_checkpoint_pending')
            if self.mm_session.catalogue.drawdown_policy() is not None:
                strategy = self.hb.trading_core.strategy
                if strategy is None or not strategy.mm_drawdown_ready():
                    status = strategy.mm_drawdown_status() if strategy is not None else {}
                    return waiting(status.get("reason_code") or "drawdown_report_unavailable")
            controllers = self._mm_controllers()
            if not controllers:
                return waiting("controllers_unavailable")
            for controller in controllers:
                if ((controller.config.mm_session_id, controller.config.mm_generation)
                        != (self.mm_session.id, self.mm_session.generation)):
                    return waiting("session_identity_mismatch")
                controller.update_reference()
                if not (controller.processed_data.get("ready") or (allow_bootstrap_start
                        and (controller.processed_data.get("quote_guard") or {}).get("can_admit"))):
                    reason = controller.processed_data.get("reason", "").split(":", 1)[0]
                    return waiting(reason if reason in MM_READINESS_REASONS else "market_data_unavailable")
                if not controller.market_data_provider.ready:
                    return waiting("market_data_unavailable")
                if not controller.exposure_within_caps():
                    return waiting("exposure_limit")
            pending_quote = Decimal("0")
            for controller in controllers:
                for executor in controller.executors_info:
                    if executor.is_active and not executor.is_trading:
                        config = executor.config
                        if config.connector_name != "okx" or config.side.name == "BUY":
                            pending_quote += config.amount * config.entry_price / config.leverage
            return True if pending_quote <= self._mm_available_quote(controllers) else waiting("insufficient_collateral")
        except Exception:
            return waiting("readiness_check_unavailable")

    def runtime_problem(self):
        """Cheap send-gate check; never fetch data, restart tasks or query history."""
        core = self.hb.trading_core
        task = getattr(core, "_clock_task", None)
        if isinstance(task, asyncio.Task) and task.done():
            return "clock_task_stopped"
        for connector in core.connector_manager.connectors.values():
            for name in ("_status_polling_task", "_user_stream_tracker_task", "_user_stream_event_listener_task",
                         "_trading_rules_polling_task"):
                task = getattr(connector, name, None)
                if isinstance(task, asyncio.Task) and task.done():
                    return "connector_task_stopped"
        strategy = core.strategy
        if strategy is None:
            return None
        for identity, controller in getattr(strategy, "controllers", {}).items():
            if (controller.config.manual_kill_switch
                    or identity in getattr(strategy, "drawdown_exited_controllers", [])):
                continue
            task = getattr(controller, "_control_loop_task", None)
            if isinstance(task, asyncio.Task) and task.done():
                stage = getattr(controller, "_startup_stage", None)
                if stage in {"position_mode_read", "position_mode_change", "position_mode_confirmation",
                             "leverage_change", "reference_metadata", "reference_books"}:
                    return f"controller_{stage}_failed"
                return "controller_task_stopped"
            if getattr(controller, "_control_error", None):
                return "controller_task_failed"
            if controller.processed_data.get("internal_error"):
                return "controller_data_failed"
            progress = getattr(controller, "_control_progress_at", None)
            if isinstance(progress, (float, int)):
                deadline = max(progress + max(5, 3 * controller.update_interval),
                               controller._control_wait_until or 0)
                if time.monotonic() > deadline:
                    return "controller_task_stalled"
        return None

    def runtime_healthy(self):
        # Ownership already checks reconciled permission/intent before calling
        # this gate. The observer's UI acknowledgement can lag a healthy first
        # clock tick, especially on standby handover; it is not send authority.
        return self.runtime_problem() is None

    async def admit_mm_session(self):
        if self.mm_session is None or self.mm_session.status not in ("preparing", "running"):
            return False
        bootstrap = getattr(self.owner, "listing_bootstrap", None)
        if bootstrap is not None and not bootstrap.sync_qualification():
            return False
        if not self.mm_runtime_ready(allow_bootstrap_start=True):
            return False
        controllers = self._mm_controllers()
        anchors = None
        if self.mm_session.status == "preparing":
            supported = {}
            for controller in controllers:
                for name in (controller.config.connector_name, controller.config.reference_connector):
                    connector = controller.market_data_provider.get_connector_with_fallback(name)
                    supported[name] = set(connector.trading_rules)
            try:
                self.mm_session.catalogue.controller_configs(
                    self.mm_session.profile_id, self.mm_session.request.market_mode, supported,
                    references=self.mm_session.request.references, available_quote=self._mm_available_quote(controllers))
            except ValueError as error:
                self.mm_readiness_reason = "insufficient_collateral" if "collateral" in str(error) else "invalid_market_configuration"
                raise
            # All markets are ready: capture this step together, with one benchmark
            # basket, before committing its start/deadline and allowing any quote.
            sources = None
            for controller in controllers:
                sources = controller.prepare_session_anchors(sources)
                if sources is None:
                    return False
            anchors = {controller.config.id: controller.reference_state() for controller in controllers}
        if anchors is not None or (bootstrap is not None and bootstrap.needs_start()):
            self.mm_session = self.store.update_mm_session(
                owner_uuid=self.owner.owner_uuid, owner_generation=self.owner.generation, version=self.applied_version,
                session_id=self.mm_session.id, generation=self.mm_session.generation,
                change=lambda session, now: session.ready(now=now, anchors=anchors) if anchors is not None else None,
                **({"risk_change": bootstrap.start_admission} if bootstrap is not None else {}))
            if bootstrap is not None:
                bootstrap.refresh()
        if not self.mm_runtime_ready():
            return False
        for controller in controllers:
            controller.admit_session(self.mm_session.id, self.mm_session.generation)
        return True

    def configure_logging(self):
        from hummingbot.logger.service_logging import configure_service_logging
        configure_service_logging(self)

    async def health(self, request):
        path = request.path
        healthy = not self.stop.is_set()
        if path == "/startup":
            healthy = healthy and self.bootstrapped
        elif path == "/ready":
            healthy = healthy and self.running and self.phase == "running"
        if self.observer_progress_at is not None:
            healthy = healthy and (time.monotonic() <= max(
                self.observer_progress_at + 5, self.observer_wait_until or 0))
        return web.json_response({"state": self.phase}, status=200 if healthy else 503)

    def service_status(self, *, persisted=False):
        status = self.availability.snapshot()
        strategy = self.hb.trading_core.strategy if self.hb is not None else None
        for controller in getattr(strategy, "controllers", {}).values():
            retry = getattr(controller, "_startup_retry", None)
            if isinstance(retry, dict) and len(status["incidents"]) < 16:
                status["incidents"].append(dict(retry))
        reason = {"stopped": "operator_stopped", "standby": "owner_standby",
                  "waiting_for_mm_readiness": "market_data_unavailable", "halted": "recovery_required",
                  "starting": "strategy_initializing", "stopping": "strategy_stopping"}.get(self.phase)
        if self.phase == "waiting_for_mm_readiness":
            reason = self.mm_readiness_reason or reason
        if self.mm_session is not None and self.mm_session.status == "expired":
            reason = "session_expired"
        return {**status, "observed_at": time.time(), "reason_code": reason, "state_source": "postgres" if persisted else "process",
                "persisted_state_available": persisted, "phase": self.phase,
                "observer_age_seconds": (max(0, time.monotonic() - self.observer_progress_at)
                                         if self.observer_progress_at is not None else None)}

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
        self.observations.reporting = ((self.store.read() or {}).get("status") or {}).get("reporting") or {}
        self.owner = BotOwnership(self.engine, self.settings.bot_id)
        self.catalogue = {kind: list_configs(kind) for kind in STRATEGY_TYPES}
        self.config_hashes = {kind: {name: hashlib.sha256(config_path(kind, name).read_bytes()).hexdigest()
                                     for name in files} for kind, files in self.catalogue.items()}
        self.catalogue["mm-session"] = ["life_mm_profiles.yaml"]
        client = load_client_config_map_from_file(read_only=True)
        if client.mqtt_bridge.mqtt_autostart:
            raise ValueError("MQTT autostart is outside the reviewed service scope.")
        self.hb = await bootstrap_application(client, None, headless=True, environment_connectors=[],
                                              read_only_configuration=True)
        # API reporting/PnL uses PostgreSQL and V2 aggregates. Keep only recent
        # connector diagnostics; the connector refuses unsafe budget truncation.
        self.hb.trading_core.connector_event_log_fill_limit = 1000
        self.configure_logging()
        # A strategy-triggered risk stop uses this same bounded service lifecycle.
        # It must not start the interactive stop coroutine behind the host's back.
        self.hb.stop = self.strategy_stop
        await self.validate_selection(self.store.read())
        self.bootstrapped = True
        self.phase = "standby"
        self.log.info("Service bootstrap completed bot=%s revision=%s", self.settings.bot_id, self.revision)

    async def validate_selection(self, row):
        # A stopped service must remain available for control even when its
        # previous revision's YAML or connector credentials were removed.
        if row["desired_state"] == "stopped":
            return
        from hummingbot.cli.strategy_configs import config_path, read_yaml
        from hummingbot.client import settings
        from hummingbot.client.config.config_helpers import all_configs_complete, load_strategy_config_map_from_file
        from hummingbot.client.config.environment_credentials import load_connector_credentials
        from hummingbot.client.config.runtime_config import RuntimeConfigurationError
        from hummingbot.client.runner import controller_runner_config
        from hummingbot.core.order_submission_guard import SUPPORTED_CONNECTORS
        from hummingbot.core.trading_core import TradingCore
        name, kind = row["config_file_path"], row["config_type"]
        if (kind not in self.catalogue or name not in self.catalogue[kind] or Path(name).name != name):
            raise RuntimeConfigurationError("Selected YAML configuration is not in this revision's deployed config set.")
        settings.required_exchanges.clear()
        if kind == "v1-strategy":
            config = await load_strategy_config_map_from_file(config_path(kind, name), read_only=True)
            if not all_configs_complete(config, self.hb.client_config_map):
                raise RuntimeConfigurationError("Selected strategy configuration is incomplete.")
            connectors = set(settings.required_exchanges)
        else:
            if kind == "mm-session":
                from hummingbot.core.mm_session import MMSession
                data = self.mm_effective_config(MMSession.model_validate(row["mm_session"]))
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
        reason = kwargs.get("mm_drawdown_reason")
        if reason in {"drawdown_global_limit", "drawdown_controller_limit", "drawdown_checkpoint_unavailable"}:
            self.availability.fail("drawdown", reason, operator=True)
        if self.owner is not None:
            self.owner.require_recovery(reason or "The strategy requested a risk stop; operator review is required.")
        self.running = False
        self.halt_requested = True

    async def start_strategy(self, row):
        from hummingbot.client.runner import load_and_start_strategy
        options = {"v1-strategy": "config_file_name", "v2-script": "v2_conf", "controller": "controller_conf"}
        if row["config_type"] == "mm-session":
            from hummingbot.core.mm_session import MMSession
            self.hb.strategy_file_name = row["config_file_path"]
            self.hb.trading_core.strategy_name = "v2_with_controllers"
            success = await self.hb.trading_core.start_strategy(
                "v2_with_controllers", self.mm_effective_config(MMSession.model_validate(row["mm_session"])),
                row["config_file_path"])
        else:
            success = await load_and_start_strategy(
                self.hb, headless=True, read_only_configuration=True,
                **{options[row["config_type"]]: row["config_file_path"]})
        if not success:
            self.owner.require_recovery("Strategy initialization/reconciliation failed; inspect sanitized logs.")
        return success

    async def stop_core(self):
        self.running = False
        self.owner.allow_new_orders = False
        if self.outflow_task is not None and not self.outflow_task.done():
            self.outflow_task.cancel()
            await asyncio.gather(self.outflow_task, return_exceptions=True)
        strategy = self.hb.trading_core.strategy
        if strategy is not None and isinstance(getattr(getattr(strategy, "config", None), "mm_drawdown_scope", None), dict):
            self.observations.drawdown = strategy.mm_drawdown_status()
        if self.start_task is not None and not self.start_task.done():
            self.start_task.cancel()
            done, _ = await asyncio.wait({self.start_task}, timeout=0.5)
            if not done:
                return False
        self.start_task = None
        core = self.hb.trading_core
        if core.strategy is None and not core.connector_manager.connectors and not core._is_running:
            return True
        self.observer_wait_until = time.monotonic() + 9
        try:
            return await self.owner.shutdown_core(core)
        finally:
            self.observer_wait_until = None
            self.observer_progress_at = time.monotonic()

    def snapshot(self):
        from hummingbot.cli.admin_operations import process_identity
        observations = self.observations.capture(self.hb.trading_core, self.owner, self.availability)
        return {"name": self.settings.bot_id, "running": self.running, "phase": self.phase,
                "identity": process_identity(self),
                "error": self.error, "available_configs": self.catalogue,
                "configuration_sha256": self.config_hashes, "engine": observations["engine"],
                "balances": observations["balances"], "reporting_unavailable": observations["reporting_unavailable"],
                "availability": self.service_status(persisted=True),
                "mm_drawdown": observations["mm_drawdown"],
                "spot_outflow": observations["spot_outflow"],
                "mm_session": self.mm_session.model_dump(mode="json", exclude={"catalogue"}) if self.mm_session else None,
                "mm_liquidity": self.mm_liquidity_state,
                "mm_markets": [{"market": "spot" if controller.config.connector_name == "okx" else "perpetual",
                                "connector": controller.config.connector_name, "pair": controller.config.trading_pair,
                                "ready": bool(controller.processed_data.get("ready")), "admitted": controller.session_admitted,
                                "reference_price": str(controller.processed_data["reference_price"]) if "reference_price" in controller.processed_data else None,
                                "reason": controller.processed_data.get("reason"),
                                "quote_guard": controller.processed_data.get("quote_guard"),
                                "inventory_skew": controller.processed_data.get("inventory_skew"),
                                "spread_policy": controller.processed_data.get("spread_policy")} for controller in self._mm_controllers()],
                "reporting": observations["reporting"],
                "funding_sync": self.funding.status(now_ms=int(time.time() * 1000)),
                "reporting_timezone": self.admin_settings.reporting_timezone if hasattr(self, "admin_settings") else "Asia/Ho_Chi_Minh",
                "format_status": None}

    async def observe_once(self):
        from hummingbot.core.order_submission_guard import install_guard
        from hummingbot.model.bot_state_store import ControlConflict
        if not self.owner.try_acquire():
            self.phase = "standby"
            return
        install_guard(self.owner)
        self.owner.runtime_ready_check = self.runtime_healthy
        row = self.owner.check_owner()
        guard = getattr(self.owner, "spot_outflow", None)
        if guard is None:
            from hummingbot.core.spot_outflow_guard import checkpoint_outflow_status
            self.observations.outflow = checkpoint_outflow_status((row["recovery_state"] or {}).get("spot_outflow"))
        if hasattr(guard, "projection") and guard.projection is not None:
            from hummingbot.core.spot_outflow_guard import SpotOutflowGuard
            if isinstance(guard, SpotOutflowGuard):
                guard.projection.advance(int(row["database_now"] * 1000))
        from hummingbot.core.mm_drawdown import checkpoint_drawdown_status
        checkpoint = (row["recovery_state"] or {}).get("strategy_checkpoints", {}).get("life_mm_profiles.yaml", {})
        if getattr(self.hb.trading_core, "strategy", None) is None:
            self.observations.drawdown = checkpoint_drawdown_status(checkpoint)
        if row["config_type"] == "mm-session" and row.get("mm_session"):
            from random import SystemRandom

            from hummingbot.core.mm_session import MMSession
            self.mm_session = MMSession.model_validate(row["mm_session"])
            session_expired = (self.mm_session.expires_at is not None
                               and float(row['database_now']) >= self.mm_session.expires_at)
            if (row["desired_state"] == "running"
                    and self.mm_session.status in ("preparing", "running")
                    and self.mm_session.step_expires_at is not None
                    and float(row["database_now"]) >= self.mm_session.step_expires_at
                    and (not (row["recovery_state"] or {}).get("halt") or session_expired)
                    and (session_expired or not self.mm_startup_pending())):
                try:
                    self.mm_session = self.store.update_mm_session(
                        owner_uuid=self.owner.owner_uuid, owner_generation=self.owner.generation, version=row["control_version"],
                        session_id=self.mm_session.id, generation=self.mm_session.generation,
                        change=lambda session, now: session.advance(now=now, rng=SystemRandom()))
                except ControlConflict:
                    return
                row = self.owner.check_owner()
        else:
            self.mm_session = None
        mm_identity = (self.mm_session.id, self.mm_session.generation) if self.mm_session else None
        self.owner.mm_ready_check = self.mm_runtime_ready if self.mm_session else None
        self.owner.mm_quote_check = (self.mm_quote_valid if self.mm_session and any(
            config.spread_policy.enabled for config in self.mm_session.catalogue.markets.values()) else None)
        version = row["control_version"]
        if version != self.applied_version or mm_identity != self.applied_mm_identity:
            self.phase = "stopping"
            if not await self.stop_core():
                raise RuntimeError("Service cleanup did not complete; process exit is required.")
            latest = self.owner.check_owner()
            if latest["control_version"] != version or latest.get("mm_session") != row.get("mm_session"):
                return
            row = latest
            self.applied_version = version
            self.applied_mm_identity = mm_identity
            self.error = None
            self.halt_requested = False
            try:
                await self.validate_selection(row)
                self.availability.clear("configuration")
                # Cleanup and configuration loading can both yield to a newer
                # stop/switch. Never prepare an engine from the old intent.
                latest = self.owner.check_owner()
                if latest["control_version"] != version or latest.get("mm_session") != row.get("mm_session"):
                    return
                row = latest
                if row["desired_state"] == "running":
                    self.owner.prepare(version, row["config_file_path"])
                    self.hb.trading_core.before_strategy_start = self.owner.reconcile
                    self.phase = "starting"
                    self.start_task = asyncio.create_task(self.start_strategy(row))
                else:
                    self.phase = "stopped"
                    if self.mm_session is not None and self.mm_session.status == "stopping":
                        self.mm_session = self.store.update_mm_session(
                            owner_uuid=self.owner.owner_uuid, owner_generation=self.owner.generation, version=version,
                            session_id=self.mm_session.id, generation=self.mm_session.generation,
                            change=lambda session, now: setattr(session, "status", "stopped"))
            except Exception as error:
                self.availability.fail("configuration", "configuration_unavailable", error, operator=True)
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
            problem = self.runtime_problem()
            if problem is not None:
                self.availability.fail("runtime", problem, operator=True)
                self.owner.require_recovery("A required runtime task failed; inspect service availability status.")
                self.running, self.phase, self.error = False, "halted", problem
                if not await self.stop_core():
                    raise RuntimeError("Failed runtime-task cleanup did not complete.")
        if self.running:
            if self.mm_session is not None:
                try:
                    admitted = not (row["recovery_state"] or {}).get("halt") and await self.admit_mm_session()
                except (ValueError, ControlConflict):
                    admitted = False
                self.phase = "running" if admitted else "waiting_for_mm_readiness"
                if not admitted:
                    for controller in self._mm_controllers():
                        controller.revoke_session()
                        await controller.send_actions(controller.stop_quote_actions())
            if (core.trade_fill_db is None or not core.trade_fill_db.persistence_healthy
                    or not self.owner.allow_new_orders or not core._strategy_running):
                self.owner.require_recovery("Trading/recorder health requires reconciliation.")
                self.phase, self.error = "halted", "TradingHealthFailed"
                if not await self.stop_core():
                    raise RuntimeError("Degraded runtime cleanup did not complete.")
        if (self.owner.check_owner()["recovery_state"] or {}).get("halt"):
            self.running, self.phase, self.error = False, "halted", "RecoveryRequired"
        from hummingbot.core.mm_liquidity import capture_liquidity
        try:
            self.mm_liquidity_state = capture_liquidity(self)
            self.availability.clear("liquidity")
        except Exception as error:
            self.mm_liquidity_state = self.liquidity_observer = None
            self.availability.fail("liquidity", "liquidity_observation_unavailable", error)
        self.publish_status(version)
        await self.poll_reconciliation()
        self.poll_funding()
        self.poll_outflow()

    def poll_outflow(self):
        from hummingbot.core.spot_outflow_guard import SpotOutflowGuard
        guard = getattr(self.owner, "spot_outflow", None)
        if (not isinstance(guard, SpotOutflowGuard) or guard.initializing or (self.outflow_task is not None and not self.outflow_task.done())
                or time.monotonic() < self.next_outflow_poll):
            return
        self.next_outflow_poll = time.monotonic() + 1

        async def observe():
            try:
                if guard.projection is None:
                    await guard.initialize()
                await guard.reconcile_terminal()
                if guard.reason_code == "spot_outflow_observation_unavailable":
                    guard.reason_code = None
                if guard.terminal_pending:
                    self.next_outflow_poll = time.monotonic() + self.availability.fail(
                        "spot_outflow", "spot_outflow_terminal_unavailable", retry=True)
                else:
                    self.availability.clear("spot_outflow")
            except Exception as error:
                self.owner.check_owner()
                self.hb.trading_core.trade_fill_db.assert_persistence_healthy()
                guard.reason_code = "spot_outflow_observation_unavailable"
                self.next_outflow_poll = time.monotonic() + self.availability.fail(
                    "spot_outflow", "spot_outflow_observation_unavailable", error, retry=True)
        self.outflow_task = asyncio.create_task(observe())

    def publish_status(self, version):
        """Publish control every tick; optional daily checkpoints at most once/minute."""
        status = json.loads(json.dumps(self.snapshot(), default=str))
        now = time.monotonic()
        try:
            zone_name = status.get("reporting_timezone", "Asia/Ho_Chi_Minh")
            zone = ZoneInfo(zone_name)
            signature = frozenset((account["account_id"], account["environment"], balance["currency"], zone_name,
                                   datetime.fromtimestamp(float(balance["observed_at"]), timezone.utc)
                                   .astimezone(zone).date())
                                  for account in (status.get("reporting") or {}).get("accounts", [])
                                  if account.get("identity_verified")
                                  for balance in account.get("balances", [])
                                  if balance.get("present_in_latest_snapshot", True))
        except (ValueError, TypeError, KeyError, OverflowError, OSError):
            # Leave malformed samples to the isolated writer; do not bypass its
            # retry deadline or turn optional projection into an owner failure.
            signature = None
        due = (not status.get("reporting_unavailable") and now >= self._daily_retry_at
               and (now >= self._daily_next_write_at or signature != self._daily_signature))
        published = self.owner.store.publish(
            owner_uuid=self.owner.owner_uuid, owner_generation=self.owner.generation,
            version=version, status=status, revision=self.revision, save_balances=due)
        if published and due:
            if status.get("daily_snapshot_error"):
                delay = self.availability.fail("daily_balances", "daily_snapshot_unavailable", retry=True)
                self._daily_retry_at = now + delay
            else:
                self.availability.clear("daily_balances")
                self._daily_signature = signature
                self._daily_next_write_at, self._daily_retry_at = now + 60, 0

    def poll_funding(self):
        self.funding.poll(self.engine, self.owner, self.hb.trading_core)

    async def collect_funding(self):
        await self.funding.collect(self.engine, self.owner, self.hb.trading_core)

    async def poll_reconciliation(self):
        if self.reconciliation_task is not None and not self.reconciliation_task.done():
            return
        if self.reconciliation_task is not None and not self.reconciliation_task.cancelled():
            error = self.reconciliation_task.exception()
            if error is not None:
                raise error  # Dedicated ownership failures must reach the host.
        if time.monotonic() < self.next_check_poll:
            return
        self.next_check_poll = time.monotonic() + 1
        self.reconciliation_task = asyncio.create_task(self.claim_reconciliation())

    async def claim_reconciliation(self):
        from hummingbot.model.admin_store import AdminStore
        storage = AdminStore(self.store)
        try:
            row = await asyncio.to_thread(storage.claim_check, self.owner.owner_uuid, self.owner.generation)
        except Exception as error:
            # A pool/read error must not hide loss of the dedicated owner lease.
            self.owner.check_owner()
            self.next_check_poll = time.monotonic() + self.availability.fail(
                "reconciliation", "reconciliation_unavailable", error, retry=True)
            return
        self.availability.clear("reconciliation")
        if row:
            await self.run_reconciliation(storage, row)

    async def run_reconciliation(self, storage, row):
        from hummingbot.core.admin_reconciliation import ReconciliationRequest, perform_check
        owner_uuid, generation = self.owner.owner_uuid, self.owner.generation
        try:
            report = await perform_check(storage, self.hb.trading_core, ReconciliationRequest.model_validate(row["payload"]))
            self.availability.clear("reconciliation_read")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.availability.fail("reconciliation_read", "reconciliation_read_unavailable", error)
            report = dict(state="unavailable", markets=[], started_at=None, finished_at=None)
            self.log.warning("Observational check failed type=%s", type(error).__name__)
        try:
            await asyncio.to_thread(storage.finish_check, row["id"], owner_uuid, generation,
                                    json.loads(json.dumps(report, default=str)))
        except Exception as error:
            # The durable applying receipt is reclaimable; do not emit driver
            # details or lose the task exception to asyncio's default logger.
            self.log.warning("Observational report publication failed type=%s", type(error).__name__)

    async def run(self):
        await self.bootstrap()
        while not self.stop.is_set():
            self.observer_progress_at = time.monotonic()
            await self.observe_once()
            self.observer_progress_at = time.monotonic()
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass

    async def close(self):
        from hummingbot.core.order_submission_guard import install_guard
        self.running, self.phase = False, "stopping"
        await self.funding.close()
        if self.reconciliation_task is not None and not self.reconciliation_task.done():
            self.reconciliation_task.cancel()
            await asyncio.gather(self.reconciliation_task, return_exceptions=True)
        if self.owner is not None and self.owner.connection is not None:
            # Any cleanup error keeps the lock until OS process teardown. The
            # deadline must remain armed when this returns False.
            try:
                if not await self.stop_core():
                    return False
                row = self.owner.check_owner()
                self.owner.store.publish(owner_uuid=self.owner.owner_uuid, owner_generation=self.owner.generation,
                                         version=row["control_version"], status=json.loads(json.dumps(self.snapshot(), default=str)), revision=self.revision)
                self.owner.release()
            except Exception:
                self.log.error("Service cleanup incomplete; ownership retained until process exit.")
                return False
        install_guard(None)
        if self.engine is not None:
            self.engine.dispose()
        from hummingbot.core.web_assistant.connections.connections_factory import ConnectionsFactory
        await ConnectionsFactory().close()
        return True


def create_service_app(host, admin_settings=None):
    from hummingbot.cli.admin_api import AdminAPI, admin_middleware
    from hummingbot.client.config.admin_config import AdminSettings
    admin_settings = admin_settings or AdminSettings.from_environment()
    host.admin_settings = admin_settings
    app = web.Application(middlewares=[admin_middleware(admin_settings)])
    for path in ("/startup", "/live", "/ready"):
        app.router.add_get(path, host.health)
    AdminAPI(host, admin_settings).register(app)
    return app


async def run_service():
    from hummingbot.client.config.runtime_config import RuntimeConfigurationError, RuntimeSettings, postgres_requested
    stop = asyncio.Event()
    deadline = ShutdownDeadline(stop)
    deadline.install()
    runner = host = None
    clean = False
    started = time.monotonic()
    try:
        from hummingbot.logger.service_logging import configure_service_logging
        configure_service_logging()
        if not postgres_requested():
            raise RuntimeConfigurationError("The foreground service requires STORAGE_BACKEND=postgres; use hbot start for file mode.")
        settings = RuntimeSettings.from_environment()
        host = ServiceHost(settings, stop, deadline)
        host.configure_logging()
        host.log.info("Service starting", extra={"event": "service_starting"})
        app = create_service_app(host)
        runner = web.AppRunner(app, access_log=None, shutdown_timeout=0.1)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", settings.port).start()
        await host.run()
        code = 0
    except Exception as error:
        # Driver/Pydantic exceptions may include secret inputs; never log raw
        # exception text or traceback at this outer process boundary.
        from sqlalchemy.exc import SQLAlchemyError

        from hummingbot.core.bot_ownership import OwnershipLost
        reason = ("ownership_lost" if isinstance(error, OwnershipLost) else
                  "critical_persistence_failure" if isinstance(error, SQLAlchemyError) else "service_internal_failure")
        logging.getLogger(__name__).error("Service stopped reason=%s type=%s", reason, type(error).__name__,
                                          extra={"event": "service_failed", "reason_code": reason,
                                                 "error_type": type(error).__name__})
        code = 1
    finally:
        deadline.request()
        if host is not None:
            clean = await host.close()
        else:
            clean = True
        if runner is not None:
            await runner.cleanup()
        logging.getLogger(__name__).info("Service exit clean=%s elapsed_s=%.3f", clean, time.monotonic() - started,
                                         extra={"event": "service_exit"})
        if clean:
            deadline.close()
    if not clean:
        # asyncio.run would wait indefinitely for arbitrary cancellation-hostile
        # tasks. Preserve the advisory session until immediate OS teardown.
        os._exit(1)
    return code


def serve():
    """Run the foreground PostgreSQL service (the CLI already loaded dotenv)."""
    from hummingbot.logger.service_logging import configure_service_logging

    # Safe defaults also cover rejected configuration before host construction.
    configure_service_logging(environ={}, reset=True)
    raise SystemExit(asyncio.run(run_service()))


def main():
    from hummingbot.client.config.runtime_config import load_environment
    load_environment()
    serve()


if __name__ == "__main__":
    main()
