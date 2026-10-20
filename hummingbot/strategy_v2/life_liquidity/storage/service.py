"""Configured headless file service. Startup/recovery never grants activation."""
import asyncio
import inspect
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from controllers.generic.life_liquidity import LifeLiquidityController
from hummingbot.strategy_v2.life_liquidity.hedge_coordinator import HedgeCoordinator
from hummingbot.strategy_v2.life_liquidity.joint_recovery import JointRecovery
from hummingbot.strategy_v2.life_liquidity.protected_swap import ProtectedSwapExecutorSender
from hummingbot.strategy_v2.life_liquidity.request_budget import AccountRequestBudget
from hummingbot.strategy_v2.life_liquidity.shared_capital import SharedCapitalAuthority
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.hedge_application import FileHedgeApplication
from hummingbot.strategy_v2.life_liquidity.storage.joint import open_file_joint
from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import LegacySpotFiles, open_legacy_spot
from hummingbot.strategy_v2.life_liquidity.storage.recorder_factory import open_file_recorder
from hummingbot.strategy_v2.life_liquidity.storage.service_config import FileServiceConfig
from hummingbot.strategy_v2.life_liquidity.storage.spot_application import FileSpotApplication
from hummingbot.strategy_v2.life_liquidity.storage.swap import open_file_swap
from hummingbot.strategy_v2.life_liquidity.storage.swap_application import FileSwapApplication


@dataclass(frozen=True)
class ServiceSources:
    # Cache-only synchronous inputs; collection/HTTP callbacks remain async.
    clock: Callable
    risk_epoch: Callable
    authorize_permit: Callable
    spot_admission: Callable
    capital_snapshot: Callable | None = None
    swap_account: Callable | None = None
    joint_collect: Callable | None = None
    cancel_swap: Callable | None = None
    hedge_market: Callable | None = None
    independent_fill_value: Callable | None = None
    independent_cashflow_value: Callable | None = None
    joint_spot_recovery: Callable | None = None
    quote_snapshot: Callable | None = None
    fee_snapshot: Callable | None = None
    monotonic_clock: Callable | None = None
    carry_observation: Callable | None = None
    nav_observation: Callable | None = None
    safety_observation: Callable | None = None
    horizon_observation: Callable | None = None
    control_seed: Callable | None = None
    inventory_observation: Callable | None = None
    exit_observation: Callable | None = None


class FileLiquidityService:
    def update_controller_config(self, config):
        if self.configuration is None:
            raise StorageError('SERVICE_CONFIGURATION_REQUIRED')
        from uuid import uuid4
        return self.reload_config(self.config.model_copy(update={'controller': config}),
                                  operation_id='controller-config:' + uuid4().hex)

    def config_load_failed(self):
        if self.configuration is None:
            raise StorageError('SERVICE_CONFIGURATION_REQUIRED')
        return self.configuration.submit_load_failure()

    async def settle_inventory(self, cycle_id, intent_ids):
        if self.control is None:
            raise StorageError('SERVICE_SETTLEMENT_CONTROL_REQUIRED')
        return await self.control.settle_inventory(cycle_id, intent_ids)

    def check_financial_gate(self, gate):
        self.store.assert_worker_thread()
        if gate == 'risk':
            return self.risk is None or self.risk.ready()
        if gate == 'loss':
            return self.financial_ready(check_loss=True)
        if gate == 'attribution':
            return self.financial_ready()
        if gate == 'markout':
            return self.risk is None or self.risk.markout_permissions_ready()
        if gate == 'mm':
            return (self.exit is None or not self.exit.active) and (self.inventory is None or self.inventory.mm_ready())
        if gate == 'bounded':
            if self.exit is not None and self.exit.active:
                return self.exit.pending_exit_ready()
            return self.inventory is not None and self.inventory.pending_exit_ready()
        if gate == 'reduction':
            return self.exit is not None and self.exit.permissions()
        raise StorageError('SERVICE_FINANCIAL_GATE_INVALID')

    def planner_for(self, config):
        # Selection is cache-only; the returned port's financial methods are
        # used by worker qualification, never as a loop-local send grant.
        for planner in (self.exit, self.inventory):
            if planner is not None and planner.handles(config):
                return planner
        return None

    def on_runner_safety_tick(self, timestamp):
        reducing = self.exit is not None and self.exit.active
        if not self.runtime._local_ready(allow_reduction=reducing):
            self.controller._runner_halt_ok = self.controller._halt_runner_orders()
        self.on_safety_tick(timestamp)

    async def refresh_processed_data(self):
        await self.store._call(self.controller._update_processed_data_sync)

    @classmethod
    async def open(cls, config, *, connectors, sources, market_data_provider, create_binding=False, _startup_owner=None):
        if (type(config) is not FileServiceConfig or type(sources) is not ServiceSources or type(create_binding) is not bool
                or _startup_owner is not None and (type(_startup_owner) is not list or _startup_owner)):
            raise StorageError('SERVICE_ASSEMBLY_INVALID')
        # Detach mutable nested controller settings before the first await.
        config = FileServiceConfig.model_validate_json(config.model_dump_json())
        binding = config.controller.storage_binding
        if create_binding and binding.environment != 'OFFLINE_SIMULATION':
            raise StorageError('SERVICE_BOOTSTRAP_OFFLINE_ONLY')
        routes = {config.controller.strategy.spot.connector}
        if config.controller.strategy.perpetual.enabled:
            routes.add(config.controller.strategy.perpetual.connector)
        connectors = dict(connectors)
        if (set(connectors) != routes or len({id(v) for v in connectors.values()}) != len(routes)
                or any(not callable(getattr(v, 'get_account_uid', None)) or v.name != k for k, v in connectors.items())
                or any(not callable(v) for v in (sources.clock, sources.risk_epoch, sources.authorize_permit, sources.spot_admission))):
            raise StorageError('SERVICE_ROUTE_OR_SOURCE_INVALID')
        for connector in connectors.values():
            if await connector.get_account_uid() != binding.account_uid:
                raise StorageError('SERVICE_UID_MISMATCH')
        if config.joint is not None and any(not callable(v) for v in (
                sources.capital_snapshot, sources.swap_account, sources.joint_collect, sources.cancel_swap)):
            raise StorageError('SERVICE_JOINT_SOURCE_REQUIRED')
        if config.hedge_policy is not None and not callable(sources.hedge_market):
            raise StorageError('SERVICE_HEDGE_SOURCE_REQUIRED')
        if config.quotes is not None and any(not callable(v) for v in (
                sources.quote_snapshot, sources.fee_snapshot, sources.monotonic_clock)):
            raise StorageError('SERVICE_QUOTE_SOURCES_REQUIRED')
        if config.carry_policy is not None and not callable(sources.carry_observation):
            raise StorageError('SERVICE_CARRY_SOURCE_REQUIRED')
        if config.risk is not None and (any(not callable(v) for v in (
                sources.nav_observation, sources.safety_observation, sources.monotonic_clock))
                or config.risk.markout is not None and not callable(sources.horizon_observation)):
            raise StorageError('SERVICE_RISK_SOURCES_REQUIRED')
        if create_binding and config.control is not None and not callable(sources.control_seed):
            raise StorageError('SERVICE_CONTROL_SEED_REQUIRED')
        seed = sources.control_seed(binding, config.owner) if create_binding and config.control is not None else None
        # Signing/seed providers are startup inputs, never retained by runtime.
        sources = replace(sources, control_seed=None)
        if config.inventory is not None and not callable(sources.inventory_observation):
            raise StorageError('SERVICE_INVENTORY_SOURCE_REQUIRED')
        if config.exit is not None and not callable(sources.exit_observation):
            raise StorageError('SERVICE_EXIT_SOURCE_REQUIRED')
        service = cls()
        service.config, service.sources, service.connectors = config, sources, connectors
        service._revoked, service._cycle = False, None
        service._recovering = service._safety_pending = service._control_pending = False
        service._recovery_admitted = 0
        service._pending_events = {}
        service._action_events = {}
        service.quotes = service.products = service.risk = service.control = service.inventory = service.exit = None
        service._hint_generation = service._qualified_hint_generation = 0
        service._close_task = service._halt_task = None
        service.configuration = None
        service._recovery_lock = asyncio.Lock()
        service.store = service.spot = service.swap = service.joint = service.hedge = service.reporting = service.runner = None
        if _startup_owner is not None:
            _startup_owner.append(service)
        root = Path(config.state_dir)
        files = LegacySpotFiles(root / 'binding.json', root / 'session.json', root / 'wal.json',
                                root / 'reservations.json', root / 'subsidy.json', root / 'loss.json',
                                exit_subsidy=None if config.exit is None else root / 'exit_budget.json')
        try:
            service.store = await open_legacy_spot(files, binding=binding, limits=config.file_limits,
                                                   risk_limits=config.risk_limits, subsidy_policy=config.subsidy_policy, loss_policy=config.loss_policy,
                                                   config_version=config.config_version, account_lock_root=config.account_lock_root,
                                                   create_binding=create_binding, owner=config.owner if create_binding else None, clock=sources.clock,
                                                   fill_policy=config.fill_policy, accounting_policy=config.accounting_policy,
                                                   terminal_policy=config.terminal_policy, recovery_policy=config.recovery_policy,
                                                   settlement_policy=config.settlement_policy,
                                                   operator_keys=None if config.control is None else config.control.verifier(),
                                                   control_seed=seed, exit_subsidy_policy=None if config.exit is None else config.exit.budget)
            if (await service.store.configuration_handoff(binding)).active is not None:
                raise StorageError('SERVICE_HANDOFF_SOURCE_FROZEN_MAINTENANCE_REQUIRED')
            # Constructors contain no global SQL/config reads; native financial
            # installation is deferred to the owning store worker below.
            service.controller = LifeLiquidityController(config.controller, market_data_provider, asyncio.Queue())
            service.spot = await FileSpotApplication.install(service.store, service.controller,
                                                             connectors[config.controller.strategy.spot.connector], config.owner,
                                                             admission=sources.spot_admission, authorize_permit=sources.authorize_permit,
                                                             risk_epoch=sources.risk_epoch, max_pending=config.max_pending,
                                                             shutdown_timeout_s=config.shutdown_timeout_s, ack_timeout_s=config.ack_timeout_s, defer_runtime=True)
            service.runtime = service.spot.runtime
            await service.spot.resume_session_ticks()
            if sources.independent_fill_value is not None or sources.independent_cashflow_value is not None:
                from hummingbot.strategy_v2.life_liquidity.storage.spot_observation import OkxSpotRepositoryObserver
                observer = OkxSpotRepositoryObserver(service.spot, independent_fill_value=sources.independent_fill_value,
                                                     independent_cashflow_value=sources.independent_cashflow_value, max_pages=config.observation_max_pages)
                service.spot.install_observer(observer)
            if config.joint is not None:
                await service.store._call(service._install_products, create_binding)
                service.joint = await open_file_joint(service.store, recovery=service.controller._joint_recovery,
                                                      owner=config.owner, limits=config.joint.limits, couple_cash=True, couple_spot=True)
                from hummingbot.strategy_v2.life_liquidity.storage.shared_spot import bind_shared_spot
                await bind_shared_spot(service.joint)
                swap_repo = await open_file_swap(service.joint)
                service.swap = FileSwapApplication(swap_repo, max_pending=config.max_pending,
                                                   ack_timeout_s=config.ack_timeout_s, shutdown_timeout_s=config.shutdown_timeout_s)
                service.runtime.bind_swap_application(service.swap)
                if config.hedge_policy is not None:
                    service.hedge = await FileHedgeApplication.install(service.swap, service.controller._hedge_coordinator)
            if config.carry_policy is not None:
                from hummingbot.strategy_v2.life_liquidity.storage.products import open_file_products
                service.products = await open_file_products(service.store,
                                                            capital=service.controller._shared_capital_authority, carry=service.controller._carry_monitor,
                                                            hedge=service.controller._hedge_coordinator)
                await service.store._call(service.runtime.bind_products, service.products)
            if config.quotes is not None:
                from hummingbot.strategy_v2.life_liquidity.storage.quote_application import FileQuoteApplication
                service.quotes = await FileQuoteApplication.install(service)
            if config.risk is not None:
                from hummingbot.strategy_v2.life_liquidity.storage.risk_application import FileRiskApplication
                service.risk = await FileRiskApplication.install(service, create_binding)
            if config.inventory is not None:
                from hummingbot.strategy_v2.life_liquidity.storage.inventory_application import FileInventoryApplication
                service.inventory = await FileInventoryApplication.install(service, create_binding)
            if config.exit is not None:
                from hummingbot.strategy_v2.life_liquidity.storage.exit_application import FileExitApplication
                service.exit = await FileExitApplication.install(service, create_binding)
            service.controller.install_storage_runtime(service.runtime)
            service.runtime.bind_service(service)
            service.reporting = await open_file_recorder(service.runtime, database_path=config.recorder_path,
                                                         config_file_path=config.recorder_config_name, strategy_name='v2', connectors=tuple(connectors.values()),
                                                         max_pending=config.max_pending, shutdown_timeout_s=config.shutdown_timeout_s)
            from hummingbot.strategy_v2.life_liquidity.storage.configuration_application import (
                FileConfigurationApplication,
            )
            await FileConfigurationApplication.install(service, create_binding)
            from hummingbot.strategy_v2.life_liquidity.storage.service_runner import FileServiceRunner
            service.runner = FileServiceRunner(service)
            service.controller._runner_orchestrator = service.runner.executor_orchestrator
            if config.control is not None:
                from hummingbot.strategy_v2.life_liquidity.storage.control_application import FileControlApplication
                service.control = FileControlApplication(service)
                service.control.configuration_state = await service.store.financial_configuration(service.store.binding)
            return service
        except BaseException:
            await service.close()
            raise

    @classmethod
    async def read_yaml(cls, path):
        def load():
            import json

            import yaml
            source = Path(path)
            if not source.is_absolute() or source.is_symlink():
                raise StorageError('SERVICE_CONFIG_PATH_INVALID')
            with source.open('rb') as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise StorageError('SERVICE_CONFIG_TOO_LARGE')
            # JSON validation allows standard YAML UUID/decimal strings while
            # preserving strict integer/boolean distinction and nested DTOs.
            return FileServiceConfig.model_validate_json(json.dumps(yaml.safe_load(raw)))
        return await asyncio.to_thread(load)

    @classmethod
    async def open_yaml(cls, path, **kwargs):
        return await cls.open(await cls.read_yaml(path), **kwargs)

    def reload_config(self, config, *, operation_id):
        if self.configuration is None:
            raise StorageError('CONFIG_APPLICATION_UNAVAILABLE')
        return self.configuration.submit(config, operation_id=operation_id)

    def reload_yaml(self, path, *, operation_id):
        if self.configuration is None:
            raise StorageError('CONFIG_APPLICATION_UNAVAILABLE')
        return self.configuration.submit_yaml(path, operation_id=operation_id)

    def _install_products(self, create):
        self.store.assert_worker_thread()
        c, cfg, src = self.controller, self.config, self.sources
        root = Path(cfg.state_dir)
        capital = SharedCapitalAuthority(root / 'shared_capital.json', policy=cfg.capital_policy,
                                         observation=src.capital_snapshot, clock_ms=lambda: int(src.clock().timestamp() * 1000), create=create)
        c.install_shared_capital_authority(capital, recover=not create)
        budget = AccountRequestBudget(root / 'requests.json', account_uid=self.store.binding.account_uid,
                                      policy=cfg.request_policy, clock=src.clock)
        if create and not budget.path.exists():
            budget.initialize_empty()
        c._order_safety_gateway.request_budget = budget
        c._protected_spot_sender.gateway.request_budget = budget
        wal = IntentWAL(root / 'swap_wal.json')
        if create:
            wal.initialize_empty()
        sender = ProtectedSwapExecutorSender(c, self.connectors[cfg.controller.strategy.perpetual.connector],
                                             wal, cfg.swap_contract, root / 'swap_actions.json', account_observation=src.swap_account,
                                             authorize_reservation=src.authorize_permit, risk_epoch=src.risk_epoch,
                                             clock_ms=lambda: int(src.clock().timestamp() * 1000), max_age_ms=cfg.capital_policy.max_age_ms,
                                             account_mode=cfg.capital_policy.account_mode, request_budget=budget, create=create,
                                             async_authorize=self.runtime.authorize_swap)
        c.install_protected_swap_sender(sender)
        if cfg.carry_policy is not None:
            from hummingbot.strategy_v2.life_liquidity.carry_monitor import CarryMonitor
            monitor = CarryMonitor(root / 'carry.json', policy=cfg.carry_policy, capital=capital,
                                   observation=src.carry_observation, clock_ms=sender.clock_ms, create=create)
            c.install_carry_monitor(monitor)
        c._execution_loss_budget = self.store._loss
        c._execution_loss_utc_clock = src.clock
        if cfg.hedge_policy is not None:
            native = HedgeCoordinator(c, root / 'hedge_coordinator.json', policy=cfg.hedge_policy,
                                      market=lambda: self._hedge_market, settlement=lambda _: None, create=create)
            c.install_hedge_coordinator(native)
        params = cfg.joint
        recovery = JointRecovery(root / 'joint_recovery.json', controller=c, collect=src.joint_collect,
                                 cancel_swap=src.cancel_swap, clock_ms=sender.clock_ms, max_age_ms=params.max_age_ms,
                                 cancel_retry_ms=params.cancel_retry_ms, max_cancels_per_cycle=params.max_cancels_per_cycle,
                                 poll_interval_ms=params.poll_interval_ms, opening_long_entry=params.opening_long_entry,
                                 opening_short_entry=params.opening_short_entry, create=create)
        c.install_joint_recovery(recovery)

    async def plan(self):
        if self._revoked or getattr(self.store, '_handoff_frozen', False):
            return []
        await self.drain_action_events()
        if self._revoked or self._recovering:
            return []
        reducing = self.exit is not None and self.exit.active
        if self.risk is not None and not await self.store._call(lambda: self.risk.ready(reduce_only=reducing)):
            return []
        if not self.runtime._local_ready(allow_reduction=reducing):
            return []
        if reducing:
            from hummingbot.strategy_v2.life_liquidity.storage.quote_application import FileQuoteApplication
            raw = await self.store._call(self.exit.propose)
            return [FileQuoteApplication.restore(v) for v in raw] if not self._revoked else []
        if self.config.carry_policy is not None:
            def carry_stops():
                c = self.controller
                allowed = c._carry_ready()
                return allowed, tuple(a.executor_id for a in c._carry_stops()) if not allowed else ()
            allowed, stops = await self.store._call(carry_stops)
            if not allowed:
                from hummingbot.strategy_v2.models.executor_actions import StopExecutorAction
                return [StopExecutorAction(controller_id=self.controller.config.id, executor_id=key) for key in stops]
        if self.hedge is not None:
            if self.risk is not None and not await self.store._call(lambda: self.risk.ready(normal_only=True)):
                return []
            market = self.sources.hedge_market()
            if inspect.isawaitable(market):
                market = await market
            self._hedge_market = market
            import uuid
            actions = await self.hedge.plan('cycle:' + uuid.uuid4().hex, market)
            if actions:
                return actions
            if not await self.store._call(self.hedge.native.allows_spot):
                return []
        if self.inventory is not None and not await self.store._call(self.inventory.mm_ready):
            from hummingbot.strategy_v2.life_liquidity.storage.quote_application import FileQuoteApplication
            raw = await self.store._call(self.inventory.propose)
            return [FileQuoteApplication.restore(v) for v in raw] if not self._revoked else []
        # Existing quote planner remains a worker-only adapter. No financial
        # publication occurs on the controller event loop.
        planner = self.controller._quote_action_planner
        if planner is None:
            return []
        from hummingbot.strategy_v2.life_liquidity.storage.quote_application import FileQuoteApplication
        payloads = await self.store._call(lambda: tuple(FileQuoteApplication.capture(a) for a in planner.propose()))
        return [FileQuoteApplication.restore(raw) for raw in payloads] if not self._revoked else []

    def financial_ready(self, *, check_loss=False):
        self.store.assert_worker_thread()
        try:
            view, meta = self.store._snapshot(), self.store._read()
            return (not self.store._incomplete(meta.resources)
                    and dict(view.cut.components) == dict(meta.resources['journal_hashes'])
                    and (not check_loss or not view.budget.loss_exhausted))
        except Exception:
            return False

    async def recover(self):
        from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageUnavailable
        if self._revoked:
            raise StorageError('SERVICE_RECOVERY_REVOKED')
        if self._recovery_admitted >= self.config.max_pending:
            raise StorageUnavailable('SERVICE_RECOVERY_BACKPRESSURE')
        self._recovery_admitted += 1
        try:
            async with asyncio.timeout(self.config.recovery_policy.max_fact_age_ms / 1000):
                await self._recover()
        except BaseException:
            self.revoke()
            raise
        finally:
            self._recovery_admitted -= 1

    async def request_exit(self, operation_id):
        if self.exit is None or self.control is None:
            raise StorageError('SERVICE_EXIT_DISABLED')
        if await self.store._call(self.exit.recorded, operation_id, 'exit_requests'):
            return False  # Readback cannot restore mode or authority.
        await self.drain_action_events()
        snapshot = await self.control.snapshot()
        async with self.control._lock:
            changed = await self.store._call(self.exit.request, operation_id, snapshot)
            self.control._snapshot = None
            self._control_pending = False
            return changed

    async def stop_exit(self, operation_id):
        if self.exit is None or self.control is None:
            raise StorageError('SERVICE_EXIT_DISABLED')
        if await self.store._call(self.exit.recorded, operation_id, 'exit_stops'):
            return False
        await self.drain_action_events()
        snapshot = await self.control.snapshot()
        async with self.control._lock:
            changed = await self.store._call(self.exit.stop, operation_id, snapshot)
            self.control._snapshot = None
            self._control_pending = False
            return changed

    async def _recover(self):
        async with self._recovery_lock:
            self._recovering = True
            try:
                for connector in self.connectors.values():
                    if await connector.get_account_uid() != self.store.binding.account_uid:
                        raise StorageError('SERVICE_RECOVERY_UID_MISMATCH')
                await self.drain_runner_events()
                hint_generation = self._hint_generation
                await self.reporting.recover()
                if self.inventory is not None:
                    await self.store._call(self.inventory.recover)
                if self.exit is not None:
                    await self.store._call(self.exit.recover)
                resources = (await self.store.read(self.store.binding)).resources
                if self.hedge is not None:
                    for key, row in resources.get('hedge_plans', {}).items():
                        if row['state'] != 'PLANNED':
                            await self.hedge.resume(key)
                if self.swap is not None:
                    resources = (await self.store.read(self.store.binding)).resources
                    for key, row in resources.get('swap_operations', {}).items():
                        if row['state'] == 'PREPARING':
                            await self.swap.repository.resume_prepare(key, owner=self.swap.owner)
                        elif row['state'] == 'ARMING':
                            await self.swap.repository.resume_arm(key, owner=self.swap.owner)
                        elif row['state'] == 'ACKING':
                            await self.swap.repository.acknowledge(key, exchange_order_id=row['exchange_order_id'],
                                                                   owner=self.swap.owner)
                if self.joint is not None:
                    fills, facts, cashflows = (), None, ()
                    if self.sources.joint_spot_recovery is not None:
                        result = self.sources.joint_spot_recovery()
                        if inspect.isawaitable(result):
                            result = await result
                        from hummingbot.strategy_v2.life_liquidity.storage.joint import JointSpotRecoveryInputs
                        if type(result) is JointSpotRecoveryInputs:
                            fills, facts, cashflows = result.fills, result.facts, result.cashflows
                        elif type(result) is tuple and len(result) == 2:
                            fills, facts = result
                        else:
                            raise StorageError('SERVICE_JOINT_SPOT_SOURCE_INVALID')
                    if facts is not None:
                        pending = await self.store.pending_spot_commands(self.store.binding)
                        for item in pending:
                            if item.owner != self.spot.owner:
                                raise StorageError('SERVICE_RECOVERY_WRITER_MISMATCH')
                            if item.kind == 'PREPARE':
                                if await self.store.prepared_receipt_ready(item.command, owner=self.spot.owner, observed_at=facts.observed_at):
                                    continue  # Qualified joint batch completes this original receipt.
                                await self.joint.restore_original_spot_holds(item.command, facts)
                                # No prepared receipt, arm, transport or release
                                # is inferred from conservative hold restoration.
                            elif item.kind == 'ARM':
                                await self.joint.restore_original_arm_bookkeeping(item.command.operation_id, facts)
                    if not await self.joint.reconcile(self.store.binding, self.joint.owner,
                                                      spot_fills=fills, spot_facts=facts, spot_cashflows=cashflows):
                        raise StorageError(self.joint._recovery.reason_code)
                    self._qualified_hint_generation = hint_generation
                    settled = await self.store._call(lambda: tuple(r.intent_id for r in self.swap.sender.wal.all_records()
                                                                   if r.state in ('TERMINAL', 'ABORTED_BEFORE_SEND')))
                    for key in settled:
                        self.swap._unknown_attempts.discard(key)
                else:
                    if self.spot._observer is None:
                        raise StorageError('SERVICE_RECOVERY_OBSERVER_REQUIRED')
                    for item in await self.store.pending_session_ticks(self.store.binding):
                        if item.owner != self.spot.owner:
                            raise StorageError('SERVICE_RECOVERY_WRITER_MISMATCH')
                        await self.store.tick_session(item.command, cut=item.original_cut, owner=item.owner)
                    await self.spot._observer.recover_pending()
            except BaseException:
                self.revoke()
                raise
            finally:
                # Never clear revoke/cold/restore latches after a receipt.
                self._recovering = False

    def recognizes_config(self, config):
        c = self.controller
        if config.controller_id != c.config.id:
            return False
        if config.connector_name == c.config.strategy.spot.connector:
            return config.trading_pair == c.config.strategy.spot.pair
        if self.swap is None:
            return False
        from hummingbot.strategy_v2.life_liquidity.storage.contracts import encode
        from hummingbot.strategy_v2.life_liquidity.storage.swap import _config_data
        return self.swap.repository._known_configs.get(config.id) == encode(_config_data(config))

    def status_lines(self):
        reducing = self.exit is not None and self.exit.active
        state = ('STOPPED' if self._revoked else 'UNARMED' if not self.runtime._local_ready(allow_reduction=reducing)
                 else 'REDUCING' if reducing else 'READY')
        lines = [f'LIFE file service: {state}; account {self.store.binding.account_uid}; '
                 f'spot/SWAP share one storage worker']
        app = self.configuration
        if app is not None and app.state is not None:
            head = app.state  # Local display only; never synchronous SQL/history.
            reason = 'CONFIG_REPAIR_REQUIRED' if app._poisoned else head.reason_code
            lines.append(f'Config revision {head.active_revision}: {reason}; pending {len(app._pending)}; '
                         f'rejection latched {head.rejection_latched}; kill latched {head.kill_latched}; '
                         f'last rejection {head.rejection_reason_code or "none"}')
        if self.control is not None and self.control.configuration_state is not None:
            head = self.control.configuration_state
            if head.pending is not None:
                lines.append(f'Financial config {head.pending.operation_id}: STAGED; policy handoff required; '
                             f'active version {head.active_config_version}')
        return lines

    def on_safety_tick(self, timestamp):
        if self._revoked or self._cycle is not None and not self._cycle.done():
            return
        if self.joint is None:
            self.spot.on_safety_tick(timestamp)
            return
        self._safety_pending = True
        self._cycle = asyncio.create_task(self._joint_safety_cycle())
        self.controller.order_safety_task = self._cycle
        self._cycle.add_done_callback(self.controller._on_order_safety_done)

    async def _joint_safety_cycle(self):
        from uuid import uuid4

        try:
            async with asyncio.timeout(self.config.joint.max_age_ms / 1000):
                await self.recover()
                # Session/cancel bookkeeping shares the same admission barrier
                # as recovery. It cannot race a proposal or a second watchdog.
                async with self._recovery_lock:
                    self._recovering = True
                    session, state, ready = await self.store._call(
                        self.spot._tick_safety_session, 'jointwatch:' + uuid4().hex, self.config.joint.max_age_ms)
                    if state != 'ACTIVE' or not ready:
                        self.controller._runner_halt_ok = self.controller._halt_runner_orders()
                        context = await self.store.observation_context(self.store.binding)
                        scopes = tuple(sorted({(session.session_id, session.epoch)} | {
                            (row.session_id, row.epoch) for row in context.commands}))
                        await self.spot.cancel_scopes(scopes)
                        await self.joint.cancel_working_swaps(self.store.binding, self.joint.owner)
                    self.controller.order_safety_reason_code = (
                        'SESSION_SUCCESSOR_AWAITING_GRANT' if state == 'AWAITING_GRANT' else 'JOINT_ORDERS_RECONCILED')
                    transitioning = state == 'TRANSITIONING'
                if transitioning and self.control is not None:
                    # Cancel replies are hints. A new complete joint proof must
                    # observe the terminal effects before the successor decision.
                    await self.recover()
                    values = self.sources.joint_spot_recovery() if self.sources.joint_spot_recovery is not None else None
                    if inspect.isawaitable(values):
                        values = await values
                    if values is not None:
                        _, facts = values
                        snapshot = await self.store._call(self.control._joint_snapshot, facts)
                        await self.store._call(self.spot._complete_successor, snapshot)
                    else:
                        self.controller.order_safety_reason_code = 'SESSION_ACCOUNT_FACTS_UNAVAILABLE'
                elif transitioning:
                    self.controller.order_safety_reason_code = 'SESSION_CONTROL_SERVICE_REQUIRED'
        except BaseException:
            self.revoke()
            raise
        finally:
            self._recovering = self._safety_pending = False

    def dispatched(self, action):
        # Async storage qualification already precedes runner creation. This
        # loop callback consumes only the one-use in-memory dispatch fence.
        allowed = self.runtime.local_fence(action)
        self.runtime.forget_action(action.executor_config.id)
        if self.quotes is not None and not self.controller._is_swap_config(action.executor_config):
            self._enqueue_action_event(action, 'DISPATCHED')
        return allowed

    def _enqueue_action_event(self, action, state):
        # Capture before yielding; no file I/O on this runner callback.
        key = (state, action.executor_config.id)
        if key in self._action_events:
            return
        if self._revoked or len(self._action_events) >= self.config.max_pending:
            self.revoke()
            return
        raw = self.quotes.capture(action)
        bounded = self.controller.bounded_spot_planner(action.executor_config)
        transition = bounded.transition if bounded is not None else self.quotes.transition
        task = asyncio.create_task(self.store._call(transition, raw, state))
        self._action_events[key] = task

        def completed(result):
            self._action_events.pop(key, None)
            if result.cancelled() or result.exception() is not None or result.result() is not True:
                self.revoke()
        task.add_done_callback(completed)

    async def drain_action_events(self):
        tasks = tuple(self._action_events.values())
        if tasks:
            await asyncio.gather(*tasks)
        if self._revoked:
            raise StorageError('SERVICE_REVOKED')

    @property
    def admission_paused(self):
        return bool(self._recovering or self._safety_pending or self._control_pending or self._pending_events
                    or self.configuration is not None and not self.configuration.ready
                    or self.control is not None and self.control._admitted > 0
                    or self._hint_generation != self._qualified_hint_generation)

    def enqueue_runner_event(self, kind, event):
        from hummingbot.strategy_v2.life_liquidity.storage.runner_events import RunnerHint
        try:
            captured = RunnerHint.capture(kind, event)
            key = (captured.kind, captured.payload)
            if key in self._pending_events:
                return
            if self.store._closed or len(self._pending_events) >= self.config.max_pending:
                raise StorageError('SERVICE_RUNNER_EVENT_BACKPRESSURE')
            self._hint_generation += 1
            task = asyncio.create_task(self.store._call(self._apply_runner_hint, captured))
            self._pending_events[key] = task

            def completed(result):
                self._pending_events.pop(key, None)
                if result.cancelled() or result.exception() is not None or self.controller._runner_scope_invalid:
                    self.controller._runner_scope_invalid = True
                    self.revoke()
            task.add_done_callback(completed)
        except Exception:
            self.controller._runner_scope_invalid = True
            self.revoke()

    def _apply_runner_hint(self, captured):
        self.store.assert_worker_thread()
        callback = (self.controller._on_runner_order_filled_sync if captured.kind == 'fill'
                    else self.controller._on_runner_order_canceled_sync)
        callback(captured.event())
        if self.controller._runner_scope_invalid:
            raise StorageError('SERVICE_RUNNER_HINT_UNQUALIFIED')

    async def drain_runner_events(self):
        tasks = tuple(self._pending_events.values())
        if tasks:
            await asyncio.gather(*tasks)
        if self.controller._runner_scope_invalid:
            raise StorageError('SERVICE_RUNNER_HINT_UNQUALIFIED')

    def stop_local(self, action):
        if action.controller_id != self.controller.config.id:
            return False
        self.controller._runner_stop_revoked_ids.add(action.executor_id)
        self.spot.cancel_local(action.executor_id)
        if self.swap is not None:
            self.swap.revoke_intent(action.executor_id)
        # Source qualification owns durable cancel/terminal work. This callback
        # must work even when the worker is blocked or native files are damaged.
        return True

    def rejected(self, action):
        if self.quotes is not None and not self.controller._is_swap_config(action.executor_config):
            self._enqueue_action_event(action, 'REJECTED')
        self.runtime.forget_action(action.executor_config.id)
        self.controller._runner_stop_revoked_ids.add(action.executor_config.id)
        if self.swap is not None and action.executor_config.connector_name == self.swap.connector.name:
            self.swap.revoke_intent(action.executor_config.id)
        else:
            self.spot.cancel_local(action.executor_config.id)
        return False

    def request_halt(self):
        if self.risk is None:
            raise StorageError('SERVICE_RISK_ROUTE_REQUIRED')
        # Immediate loop-local revocation precedes worker admission. Keep the
        # observational cancel port available until this one coalesced task ends.
        self._revoked = True
        self.runtime.revoke()
        self.controller._runner_halt_ok = self.controller._halt_runner_orders()
        if self._halt_task is not None:
            return
        self._halt_task = asyncio.create_task(self._persist_halt())

        def completed(task):
            if task.cancelled() or task.exception() is not None:
                self.controller.order_safety_reason_code = 'SERVICE_HALT_CHECKPOINT_OR_CANCEL_FAILED'
        self._halt_task.add_done_callback(completed)

    async def _persist_halt(self):
        from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageUnavailable
        try:
            async with asyncio.timeout(self.config.recovery_policy.max_fact_age_ms / 1000):
                while True:
                    try:
                        await self.store._call(self.risk.gate.halt, 'MANUAL_KILL_SWITCH')
                        break
                    except StorageUnavailable as error:
                        pending = tuple(self.store._pending)
                        if error.code != 'STORAGE_BACKPRESSURE' or not pending:
                            raise
                        # One logical halt waits for physical admission capacity;
                        # it neither queues another job nor frees a running one.
                        await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                context = await self.store.observation_context(self.store.binding)
                scopes = tuple(sorted({(row.session_id, row.epoch) for row in context.commands}))
                if scopes:
                    await self.spot.cancel_scopes(scopes)
                if self.joint is not None:
                    await self.joint.cancel_working_swaps(self.store.binding, self.joint.owner)
        finally:
            if self.joint is not None:
                self.joint.revoke()

    def revoke(self):
        self._revoked = True
        runtime = getattr(self, 'runtime', None)
        if runtime is not None:
            runtime.revoke()
        if self.swap is not None:
            self.swap.revoke()
        if self.joint is not None:
            self.joint.revoke()

    async def close(self):
        self.revoke()
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    async def _close(self):
        failure = None
        if self._halt_task is not None:
            try:
                await asyncio.shield(self._halt_task)
            except BaseException as error:
                failure = error
        if self._cycle is not None and not self._cycle.done():
            self._cycle.cancel()
            await asyncio.gather(self._cycle, return_exceptions=True)
        hints = tuple(self._pending_events.values()) + tuple(self._action_events.values())
        for task in hints:
            task.cancel()
        if hints:
            await asyncio.gather(*hints, return_exceptions=True)
        resources = (self.configuration, self.runner, self.reporting, self.swap, self.spot, self.store)
        for resource in resources:
            if resource is None:
                continue
            try:
                if resource is self.runner:
                    # The file service retains exchange uncertainty in WAL;
                    # shutdown records exposure immediately instead of waiting
                    # for a market loop that may already have stopped.
                    resource.max_executors_close_attempts = 0
                    if not getattr(self.store, '_handoff_frozen', False):
                        await resource.on_stop()
                    resource.listen_to_executor_actions_task.cancel()
                    await asyncio.gather(resource.listen_to_executor_actions_task, return_exceptions=True)
                else:
                    await resource.close()
            except BaseException as error:
                if failure is None:
                    failure = error
        if failure is not None:
            raise failure
