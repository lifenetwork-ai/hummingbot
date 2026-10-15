"""Spot application: one repository owns preparation, arming and ACK writes.

This opt-in file assembly keeps the production release gate disabled. The
volatile application checks and final local fence are required in addition to
fresh repository qualification; no stored receipt is a send grant.
"""
import asyncio
import hashlib
import math
import re
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.life_liquidity.executor_send import ProtectedSpotExecutorSender
from hummingbot.strategy_v2.life_liquidity.order_gateway import CancelRequestError, OkxSpotOrderGateway
from hummingbot.strategy_v2.life_liquidity.protected_send import ProtectedSpotGateway
from hummingbot.strategy_v2.life_liquidity.risk import SpotIntent
from hummingbot.strategy_v2.life_liquidity.send_gate import SendPermit
from hummingbot.strategy_v2.life_liquidity.session import OrderReconciliation, SessionManager
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    OwnerMode,
    OwnerToken,
    StorageError,
    fingerprint,
    identifier,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import (
    ControlSnapshot,
    OperatorApproval,
    OwnerTransition,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import AckSpot, PrepareSpot, decimal
from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import LegacySpotStore
from hummingbot.strategy_v2.life_liquidity.storage.recorder_configuration import financial_controller_config
from hummingbot.strategy_v2.life_liquidity.storage.runtime import FileControllerRuntime
from hummingbot.strategy_v2.life_liquidity.storage.session import SessionTick
from hummingbot.strategy_v2.life_liquidity.storage.spot_lifecycle import SpotLifecycleCommand


@dataclass(frozen=True)
class SpotAdmission:
    reference_price_usdt: Decimal
    subsidy_quote_usdt: Decimal
    fee_buffer_quote_usdt: Decimal
    fee_buffer_base_life: Decimal

    def __post_init__(self):
        for key in self.__dataclass_fields__:
            object.__setattr__(self, key, decimal(getattr(self, key), positive=key == 'reference_price_usdt'))


class RepositorySpotOrderGateway(OkxSpotOrderGateway):
    """Keep read/parser capabilities; native reconciliation writers are forbidden."""

    def __init__(self, application):
        self.application = application
        super().__init__(application.connector, application.store._wal, trading_pair='LIFE-USDT',
                         clock=application.store._clock, apply_fills=self._deny_native,
                         confirm_terminal=self._deny_native, on_cancel_requested=self._deny_native,
                         on_unknown=self._deny_native, account_check=self._deny_native)

    @staticmethod
    def _deny_native(*args):
        raise StorageError('SPOT_REPOSITORY_OBSERVATION_REQUIRED')

    async def reconcile(self, session_id, epoch):
        return await self.application.reconcile(session_id, epoch)

    async def request_cancel(self, session_id, epoch, *, max_requests=None):
        return await self.application.cancel_scopes(((session_id, epoch),), max_requests=max_requests)

    async def request_cancel_scopes(self, scopes):
        return await self.application.cancel_scopes(scopes)


class FileSpotApplication:
    def __init__(self, store, controller, connector, owner, admission, authorize_permit, risk_epoch, max_pending,
                 shutdown_timeout_s, ack_timeout_s):
        if (not isinstance(store, LegacySpotStore) or not isinstance(owner, OwnerToken)
                or not callable(admission) or not callable(authorize_permit) or not callable(risk_epoch)
                or type(max_pending) is not int or not 0 < max_pending <= store.limits.max_pending
                or type(shutdown_timeout_s) not in (int, float) or not math.isfinite(shutdown_timeout_s)
                or shutdown_timeout_s <= 0 or type(ack_timeout_s) not in (int, float)
                or not math.isfinite(ack_timeout_s) or ack_timeout_s <= 0
                or type(controller.config.recovery_reconciliation_max_age_ms) is not int
                or controller.config.recovery_reconciliation_max_age_ms <= 0):
            raise StorageError('SPOT_APPLICATION_CONFIG_INVALID')
        if (controller.config.storage_backend != 'file' or controller.config.storage_binding != store.binding
                or controller.config.recovery_account_uid != store.binding.account_uid
                or fingerprint(controller.config.strategy.model_dump(mode='json')) != store.binding.config_hash
                or not controller.config.strategy.spot.enabled):
            raise StorageError('SPOT_APPLICATION_BINDING_MISMATCH')
        self.store, self.controller, self.connector, self.owner = store, controller, connector, owner
        self.binding = store.binding
        self._configuration_hash = fingerprint(financial_controller_config(controller.config))
        self.admission, self.risk_epoch = admission, risk_epoch
        self.authorize_permit = authorize_permit
        self.max_pending, self.shutdown_timeout_s = max_pending, shutdown_timeout_s
        self.ack_timeout_s = ack_timeout_s
        self._pending, self._issued, self._attempted, self._revoked_ids, self._failures = {}, {}, set(), set(), {}
        self._known_intents = set()
        self._revoked = False
        self._observer = None
        self._cancel_policy = controller._configured_cancel_retry_policy()
        self._cancel_lock = asyncio.Lock()
        self._safety_pending = False
        self._reconciliation_age_ms = controller.config.recovery_reconciliation_max_age_ms
        self._control_snapshot = None
        self._process_id = owner.process_id

    @classmethod
    async def install(cls, store, controller, connector, owner, *, admission, authorize_permit, risk_epoch,
                      max_pending, shutdown_timeout_s, ack_timeout_s, defer_runtime=False):
        app = cls(store, controller, connector, owner, admission, authorize_permit, risk_epoch, max_pending,
                  shutdown_timeout_s, ack_timeout_s)
        # Construction of native file objects and their cold verification stay
        # on the same repository worker. No second ledger/WAL cache is created.
        await store._call(app._install_file_context)
        app.runtime = FileControllerRuntime(store, controller, owner)
        app.runtime.bind_spot_application(app)
        if not defer_runtime:
            controller.install_storage_runtime(app.runtime)
        return app

    def _install_file_context(self):
        c = self.controller
        if (c._order_safety_wal is not None or c._protected_spot_sender is not None
                or c._storage_runtime is not None):
            raise StorageError('SPOT_APPLICATION_ALREADY_INSTALLED')
        self.store._snapshot()
        self._known_intents = {command.intent_id for command in self.store._observation_context().commands}
        manager = SessionManager(self.store._sessions, wall_clock=self.store._clock,
                                 max_reconciliation_age_ms=c.config.recovery_reconciliation_max_age_ms)
        gateway = RepositorySpotOrderGateway(self)
        c.install_order_safety(manager, gateway, self.store._wal, reservations=self.store._ledger)
        protected = ProtectedSpotGateway(self.connector, self.store._wal, authorize=lambda permit: False)
        sender = ProtectedSpotExecutorSender(c, protected, self.store._ledger, risk_epoch=self.risk_epoch,
                                             authorize=self.authorize_permit,
                                             reference_price=self._reference_unavailable)
        # The native sender is retained as a file-only validator. Its submit /
        # gateway submit writers are never used by this application route.
        c.install_protected_spot_sender(sender)

    @staticmethod
    def _reference_unavailable():
        raise StorageError('SPOT_APPLICATION_NATIVE_SUBMIT_FORBIDDEN')

    def local_ready(self):
        try:
            c, store = self.controller, self.store
            runtime = c._storage_runtime
            if runtime is not None and runtime._service is not None and runtime._service.admission_paused:
                return False
            if (runtime is not None and runtime._service is not None and runtime._service.risk is not None
                    and not runtime._service.risk.local_ready(reduce_only=runtime._service.exit is not None and runtime._service.exit.active)):
                return False
            current = c._order_safety_manager.current_session
            now = utc(store._clock())
            return (not self._revoked and not self._safety_pending and not store._closed
                    and not store._uncertain and not store._cold_unarmed
                    and store._admission_generation == self.owner.generation
                    and self.owner.valid_from <= now < self.owner.expires_at
                    and current is not None and (current.session_id, current.epoch) == (
                        str(self.owner.session_id), self.owner.epoch)
                    and c._order_safety_manager.state == 'ACTIVE' and now < current.expires_at
                    and c.config.storage_binding == self.binding and c.config.storage_backend == 'file'
                    and fingerprint(financial_controller_config(c.config)) == self._configuration_hash
                    and fingerprint(c.config.strategy.model_dump(mode='json')) == self.binding.config_hash
                    and c.config_update_state.order_permission() is True
                    and not c.config.manual_kill_switch and not c._order_safety_stopped and c.trading_permissions_ready() is True)
        except Exception:
            return False

    def owns_controller(self, controller):
        return controller is self.controller

    @staticmethod
    def _digest(config):
        return hashlib.sha256(config.model_dump_json().encode()).hexdigest()

    def submit(self, config, *, amount, price, order_type):
        c = self.controller
        if (not isinstance(config, OrderExecutorConfig) or config.controller_id != c.config.id
                or config.connector_name != c.config.strategy.spot.connector or config.trading_pair != 'LIFE-USDT'
                or config.side not in (TradeType.BUY, TradeType.SELL)
                or config.execution_strategy != ExecutionStrategy.LIMIT_MAKER or order_type != OrderType.LIMIT_MAKER
                or amount != config.amount or price != config.price or type(config.level_id) is not str
                or re.fullmatch(r'0|[1-9][0-9]*', config.level_id) is None
                or len(config.level_id) > 10 or int(config.level_id) >= len(c.config.strategy.quotes.spreads_bps)):
            raise StorageError('SPOT_APPLICATION_ORDER_INVALID')
        identifier(config.id)
        decimal(amount, positive=True)
        decimal(price, positive=True)
        if config.id in self._attempted:
            raise ValueError('RECONCILE_BEFORE_RETRY')
        if (not self.local_ready() or config.id in self._revoked_ids
                or config.id in c._runner_stop_revoked_ids):
            raise PermissionError('LIFE_TRADING_DISABLED')
        if len(self._pending) >= self.max_pending or len(self._attempted) >= self.store.limits.max_operations:
            raise StorageError('SPOT_APPLICATION_BACKPRESSURE')
        shared = self.store._shared_spot
        if (c._shared_capital_authority is not None and (shared is None or shared.capital is not c._shared_capital_authority)
                or c._inventory_executor is not None
                or c._spot_exit_planner is not None):
            raise StorageError('SPOT_APPLICATION_PRODUCT_ASSEMBLY_REQUIRED')
        sender = c._protected_spot_sender
        wire = sender.gateway.allocate_client_order_id(side=config.side.name, trading_pair=config.trading_pair)
        self._attempted.add(config.id)
        detached = config.model_copy(deep=True)
        task = asyncio.create_task(self._submit(detached, wire, self._digest(detached)))
        self._pending[config.id] = task

        def completed(result):
            self._pending.pop(config.id, None)
            if not result.cancelled():
                failure = result.exception()
                if failure is not None:
                    self._failures[config.id] = (failure.code if isinstance(failure, StorageError)
                                                 else 'SPOT_APPLICATION_ATTEMPT_UNKNOWN')
            else:
                self._failures[config.id] = 'SPOT_APPLICATION_ATTEMPT_CANCELLED'
        task.add_done_callback(completed)
        return wire

    def _qualify_config(self, config, digest):
        if not self.local_ready() or self._digest(config) != digest or config.id in self._revoked_ids:
            raise PermissionError('SPOT_APPLICATION_REVOKED')
        c = self.controller
        service = self.runtime._service
        reducing = service is not None and service.exit is not None and service.exit.handles(config)
        if service is not None and service.risk is not None and not service.risk.ready(reduce_only=reducing):
            raise PermissionError('SPOT_APPLICATION_RISK_UNQUALIFIED')
        from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction
        action = CreateExecutorAction(controller_id=c.config.id, executor_config=config)
        if c.allow_executor_action(action) is not True or c.authorize_runner_create_action(action) is not True:
            raise PermissionError('SPOT_APPLICATION_ACTION_UNQUALIFIED')
        terms = self.admission(config.model_copy(deep=True))
        if not isinstance(terms, SpotAdmission):
            raise StorageError('SPOT_APPLICATION_ADMISSION_INVALID')
        if not self.local_ready() or config.id in self._revoked_ids or self._digest(config) != digest:
            raise PermissionError('SPOT_APPLICATION_REVOKED')
        return terms

    def _qualify_prepared(self, config, digest, permit, original_terms):
        service = self.runtime._service
        if service is not None and service.configuration is not None:
            service.configuration.verify()
        self.store.assert_worker_thread()
        c = self.controller
        service = self.runtime._service
        reducing = service is not None and service.exit is not None and service.exit.handles(config)
        if service is not None and service.risk is not None and not service.risk.ready(reduce_only=reducing):
            raise PermissionError('SPOT_APPLICATION_RISK_UNQUALIFIED')
        if not self.local_ready() or self._digest(config) != digest or config.id in self._revoked_ids:
            raise PermissionError('SPOT_APPLICATION_REVOKED')
        from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction
        action = CreateExecutorAction(controller_id=c.config.id, executor_config=config)
        if c.allow_executor_action(action) is not True:
            raise PermissionError('SPOT_APPLICATION_ACTION_UNQUALIFIED')
        bounded = c.bounded_spot_planner(config)
        if bounded is not None:
            if bounded.authorizes_permit(permit, c._protected_spot_sender._issued_intents[config.id]) is not True:
                raise PermissionError('SPOT_APPLICATION_INVENTORY_CHANGED')
        elif c._quote_action_planner is None:
            if (c.authorize_runner_create_action(action) is not True
                    or self.admission(config.model_copy(deep=True)) != original_terms):
                raise PermissionError('SPOT_APPLICATION_ADMISSION_CHANGED')
        elif c._quote_action_planner.authorizes_permit(permit) is not True:
            raise PermissionError('SPOT_APPLICATION_QUOTE_CHANGED')
        if not self.local_ready() or config.id in self._revoked_ids:
            raise PermissionError('SPOT_APPLICATION_REVOKED')

    async def _uid(self):
        if await self.connector.get_account_uid() != self.binding.account_uid:
            raise PermissionError('SPOT_APPLICATION_UID_MISMATCH')

    async def _submit(self, config, wire, digest):
        await self._uid()
        await self.runtime.await_reporting()
        terms = await self.store._call(self._qualify_config, config, digest)
        risk_epoch = self.risk_epoch()
        if type(risk_epoch) is not int or risk_epoch <= 0:
            raise StorageError('SPOT_APPLICATION_RISK_EPOCH_INVALID')
        current = self.controller._order_safety_manager.current_session
        key = 'prepare:' + hashlib.sha256(config.id.encode()).hexdigest()[:48]
        command = PrepareSpot(key, config.id, wire, 'LIFE-USDT', config.side.name, config.amount, config.price,
                              terms.reference_price_usdt, terms.subsidy_quote_usdt,
                              current.session_id, current.epoch, current.config_version, int(config.level_id),
                              terms.fee_buffer_quote_usdt, terms.fee_buffer_base_life,
                              risk_epoch=risk_epoch if self.store._shared_spot is not None else None,
                              budget_scope='EXIT' if self.runtime._service is not None and self.runtime._service.exit is not None
                              and self.runtime._service.exit.handles(config) else 'QUOTE')
        self._issued[config.id] = command
        await self.runtime.await_reporting()
        await self.store.prepare_current(command, owner=self.owner)
        if not self.local_ready() or config.id in self._revoked_ids:
            if self.store._shared_spot is not None:
                return  # Joint source proof exclusively releases shared claims.
            await self.store.apply_lifecycle(SpotLifecycleCommand('abort:' + key, key, config.id, wire, 'ABORT_PREPARED'),
                                             cut=(await self.store.snapshot(self.binding)).cut, owner=self.owner)
            return
        # Populate the existing file-only validator's volatile issued context;
        # the durable financial writes already belong to the repository.
        sender = self.controller._protected_spot_sender
        sender._issued_intents[config.id] = SpotIntent(config.id, config.side.name, config.amount, config.price,
                                                       current.session_id, current.epoch)
        sender._issued_slots[config.id] = int(config.level_id)
        permit = SendPermit(config.id, wire, config.id, current.session_id, current.epoch,
                            current.config_version, risk_epoch, config.price, config.amount)
        await self.store.arm_current(key, owner=self.owner)
        acknowledged = asyncio.Event()
        request_started = False
        qualified = False

        async def authority(wire_data):
            nonlocal qualified
            if request_started or not self.local_ready() or config.id in self._revoked_ids:
                raise PermissionError('SPOT_APPLICATION_REVOKED')
            await self.runtime.await_reporting()
            await self._uid()
            # Configuration admission is for a not-yet-prepared proposal.
            # After WAL publication the native sender verifies the exact issued
            # config/claim, held principal/subsidy and freshly repriced permit.
            await self.store._call(self._qualify_prepared, config, digest, permit, terms)
            result = await self.store.check_spot_send(command, owner=self.owner)
            if (not result.qualified or not self.local_ready()
                    or await self.store._call(sender._authorized, permit) is not True):
                raise PermissionError('SPOT_APPLICATION_SEND_UNQUALIFIED')
            qualified = True

        def final_check(wire_data):
            nonlocal request_started, qualified
            expected = {'clOrdId': wire, 'instId': 'LIFE-USDT', 'side': config.side.name.lower(),
                        'ordType': 'post_only', 'tdMode': 'cash'}
            try:
                wire_matches = (set(wire_data) == set(expected) | {'px', 'sz'}
                                and all(wire_data[key] == value for key, value in expected.items())
                                and isinstance(wire_data['px'], str) and isinstance(wire_data['sz'], str)
                                and Decimal(wire_data['px']) == config.price
                                and Decimal(wire_data['sz']) == config.amount)
            except (KeyError, TypeError, ValueError, InvalidOperation):
                wire_matches = False
            if (not qualified or request_started or not self.local_ready() or config.id in self._revoked_ids
                    or config.id in self.controller._runner_stop_revoked_ids
                    or self._digest(config) != digest or not wire_matches or self.risk_epoch() != permit.risk_epoch):
                raise PermissionError('SPOT_APPLICATION_FINAL_REVOKED')
            request_started, qualified = True, False

        async def ack(exchange_id):
            # Once transport returns an ACK, account it even after local stop /
            # expiry. The repository independently checks the original writer.
            observation = AckSpot('ack:' + key, key, config.id, wire, 'LIFE-USDT', config.side.name,
                                  current.session_id, current.epoch, exchange_id, utc(self.store._clock()))
            await self.store.acknowledge(observation, cut=(await self.store.snapshot(self.binding)).cut, owner=self.owner)
            acknowledged.set()

        self.connector.submit_protected_order(order_id=wire, trading_pair='LIFE-USDT', amount=config.amount,
                                              trade_type=config.side, order_type=OrderType.LIMIT_MAKER, price=config.price,
                                              pre_send_check=final_check, async_pre_send_check=authority, async_on_ack=ack)
        # The connector owns its transport task. This application owns only the
        # bounded pending ACK wait; a missing reply never implies unsent/retry.
        await asyncio.wait_for(acknowledged.wait(), timeout=self.ack_timeout_s)

    def revoke(self):
        self._revoked = True

    def on_safety_tick(self, timestamp):
        c = self.controller
        if self._revoked or c._order_safety_stopped:
            return
        if c.order_safety_task is not None and not c.order_safety_task.done():
            return
        self._safety_pending = True
        task = asyncio.create_task(self._safety_cycle())
        c.order_safety_task = task
        task.add_done_callback(c._on_order_safety_done)

    def _session_inputs(self):
        c = self.controller
        view = self.store._snapshot()
        if (self._revoked or c._order_safety_stopped or self.store._cold_unarmed
                or fingerprint(financial_controller_config(c.config)) != self._configuration_hash
                or c.config_update_state.order_permission() is not True):
            return view.session, False, False
        carry = c._carry_ready()
        risk = (c._runtime_risk_ready() and c._spot_risk_ready() and c._joint_risk_ready()
                and c._hedge_ready() and carry)
        financial = risk and c._execution_loss_ready() and c._fill_attribution_ready() and c._markout_risk_ready()
        planner = c._quote_action_planner
        qualified = planner.session_snapshot() if financial and planner is not None else None
        stop_pending = c._runner_stops_pending()
        ready = (not stop_pending and financial and qualified is not None and c._spot_quote_gates_ready()
                 and not c._pause_reconciliation_required)
        exit_ready = not stop_pending and c._bounded_pending_ready()
        return view.session, bool(ready or exit_ready), bool(
            exit_ready or ready and qualified.market_reference_ready is True)

    def _tick_safety_session(self, operation_id, max_age_ms):
        # Capture and publication share one worker turn. Session sources may
        # update NAV, so a cut captured by a separate await would be stale.
        self.store.assert_worker_thread()
        session, ready, market_ready = self._session_inputs()
        # Cancellation cannot kill the physical worker. Recheck a stop learned
        # while source qualification waited before starting a new session write.
        if self._revoked or self.controller._order_safety_stopped:
            return session, 'STOPPED', False
        journal = self.store._sessions.load()
        current = journal.successor
        observing = bool(current is not None
                         and (str(self.owner.session_id), self.owner.epoch) == (journal.primary.session_id, journal.primary.epoch))
        tick = SessionTick(operation_id, session.session_id, session.epoch,
                           False if observing else ready, False if observing else ready,
                           False if observing else market_ready, max_age_ms, observe_only_successor=observing)
        self.store._tick_session(tick, self.store._snapshot().cut, self.owner)
        state = self._sync_session()
        if observing and state == 'ACTIVE':
            return session, 'AWAITING_GRANT', False
        return session, state, ready and not observing

    def _sync_session(self):
        self.store._snapshot()
        c, manager = self.controller, self.controller._order_safety_manager
        previous = manager.state
        manager._journal = self.store._sessions.load()
        manager._monotonic_deadline = self.store._monotonic_deadline
        if previous == 'ACTIVE' and manager.state == 'PAUSED':
            c._pause_reconciliation_required = True
        c._observed_session_state = manager.state
        return manager.state

    def _clear_closed_runner_events(self, session_id, epoch):
        self.store._snapshot()
        c = self.controller
        if c.verify_runner_fill_events() is not True:
            raise StorageError('SPOT_APPLICATION_RUNNER_FINANCIAL_PROJECTION_REQUIRED')
        closed = {row.client_order_id for row in self.store._wal.scoped_records(session_id, epoch)
                  if row.state in ('TERMINAL', 'ABORTED_BEFORE_SEND')}
        c._runner_fill_observations = {key: value for key, value in c._runner_fill_observations.items()
                                       if value[1] not in closed}
        c._runner_cancel_observations = {key: value for key, value in c._runner_cancel_observations.items()
                                         if key not in closed}

    def _transition_anchor(self, *, observation_grant_id=None):
        c = self.controller
        service = self.runtime._service
        if (c._order_safety_stopped or service is not None and service._revoked
                or self._revoked and observation_grant_id is None):
            return None
        if service is not None and service.joint is not None:
            current = c._order_safety_manager.current_session
            # Expiry already disabled allocation. Session bookkeeping requires
            # a fresh complete account proof, never the old allocation grant.
            joint_ready = service.joint._recovery.successor_ready(current.session_id, current.epoch)
        else:
            joint_ready = c._joint_recovery_ready()
        if observation_grant_id is not None:
            self.store._session_observation_guard(self.store._snapshot(), self.store._read(), self.owner,
                                                  observation_grant_id)
            runtime_risk_ready = (service is not None and service.risk is not None
                                  and service.risk.observation_ready(observation_grant_id))
        else:
            runtime_risk_ready = c._runtime_risk_ready()
        if (self._revoked and observation_grant_id is None or c._order_safety_stopped or not joint_ready
                or not runtime_risk_ready or not c._joint_risk_ready()
                or not c._hedge_ready() or not c._execution_loss_ready() or not c._fill_attribution_ready()
                or not c._markout_risk_ready() or c.has_unverified_runner_order_events()
                or not c._spot_quote_gates_ready() or c._quote_action_planner is None):
            return None
        qualified = c._quote_action_planner.session_snapshot()
        anchor = qualified.market_anchor_usdt if qualified is not None and qualified.market_reference_ready is True else None
        return anchor if isinstance(anchor, Decimal) and anchor.is_finite() and anchor > 0 else None

    async def resume_session_ticks(self):
        # Complete the original decision before building product/recorder graphs.
        # Its recorded after-image is immutable; cold completion grants nothing.
        for pending in await self.store.pending_session_ticks(self.binding):
            if pending.owner != self.owner:
                raise StorageError('SPOT_RECOVERY_ORIGINAL_OWNER_MISMATCH')
            await self.store.tick_session(pending.command, cut=pending.original_cut, owner=pending.owner)
        await self.store._call(self._sync_session)

    def _complete_successor(self, snapshot, *, observation_grant_id=None):
        self.store.assert_worker_thread()
        c = self.controller
        if ((self._revoked or self.store._cold_unarmed) and observation_grant_id is None
                or c._order_safety_stopped):
            return False
        current = self.store._snapshot()
        if observation_grant_id is not None:
            self.store._session_observation_guard(current, self.store._read(), self.owner, observation_grant_id)
        if current.session.state != 'TRANSITIONING':
            return False
        planner = c._quote_action_planner
        if planner is not None and planner.action_journal.active_records():
            c.order_safety_reason_code = 'SESSION_PROPOSALS_UNRESOLVED'
            return False
        service = self.runtime._service
        if service is not None and service.joint is not None:
            joint = service.joint
            joint._guard()
            if not joint._recovery.successor_ready(current.session.session_id, current.session.epoch):
                c.order_safety_reason_code = 'SESSION_JOINT_SCOPE_UNQUALIFIED'
                return False
            if joint._recovery.capital.claim_ids() or any(
                    r.state not in ('TERMINAL', 'ABORTED_BEFORE_SEND') for r in joint._recovery._records()):
                return False
        anchor = (self._transition_anchor() if observation_grant_id is None else
                  self._transition_anchor(observation_grant_id=observation_grant_id))
        if anchor is None:
            c.order_safety_reason_code = 'SESSION_MARKET_REFERENCE_UNAVAILABLE'
            return False
        if (self._revoked and observation_grant_id is None or c._order_safety_stopped
                or service is not None and service._revoked):
            return False
        # Risk valuation may publish a new cut while qualifying the anchor. A
        # different cut requires fresh collection, never relabelled old facts.
        if snapshot.binding != self.binding:
            raise StorageError('SESSION_SUCCESSOR_BINDING_MISMATCH')
        if snapshot.cut != self.store._snapshot().cut:
            c.order_safety_reason_code = 'SESSION_SUCCESSOR_SOURCE_CUT_STALE'
            return False
        result = OrderReconciliation(current.session.session_id, current.session.epoch,
                                     snapshot.facts.observed_at, True, (), (), (), True)
        max_age_ms = (service.config.joint.max_age_ms if service is not None and service.joint is not None
                      else self._reconciliation_age_ms)
        tick = SessionTick('successor:' + uuid4().hex, current.session.session_id, current.session.epoch,
                           True, True, True, max_age_ms, result, snapshot.facts, anchor,
                           observation_grant_id=observation_grant_id)
        receipt = self.store._tick_session(tick, snapshot.cut, self.owner)
        self._sync_session()
        c._pause_reconciliation_required = False  # This exact source cut proved the old scope quiescent.
        c.order_safety_reason_code = ('SESSION_SUCCESSOR_AWAITING_GRANT' if receipt.state == 'ACTIVE'
                                      else receipt.reason_code)
        return True

    async def _safety_cycle(self):
        c = self.controller
        try:
            # This is the explicit reconciliation freshness budget, also bounding
            # cancel/status transport for this cycle; disk ownership stays with
            # its physical worker even if the awaiting task times out.
            async with asyncio.timeout(self._reconciliation_age_ms / 1000):
                session, state, ready = await self.store._call(
                    self._tick_safety_session, 'watch:' + uuid4().hex, self._reconciliation_age_ms)
                if state == 'ACTIVE' and ready:
                    c.order_safety_reason_code = 'SESSION_ACTIVE'
                    return
                # Native runner stop remains immediate and performs no financial
                # publication through this application. Cancel then uses ports.
                c._runner_halt_ok = c._halt_runner_orders(spot_only=True)
                context = await self.store.observation_context(self.binding)
                scopes = tuple(sorted({(session.session_id, session.epoch)} | {
                    (row.session_id, row.epoch) for row in context.commands}))
                cancel_failed = False
                try:
                    await self.cancel_scopes(scopes)
                except CancelRequestError:
                    cancel_failed = True
                primary = None
                for session_id, epoch in scopes:
                    result = await self.reconcile(session_id, epoch)
                    if (not result.scope_complete or not result.trade_events_reconciled
                            or result.open_order_ids or result.unknown_order_ids or result.pending_cancel_ids):
                        c.order_safety_reason_code = 'CANCEL_REQUEST_FAILED' if cancel_failed else 'OLD_ORDERS_UNRESOLVED'
                        return
                    await self.store._call(self._clear_closed_runner_events, session_id, epoch)
                    if (session_id, epoch) == (session.session_id, session.epoch):
                        primary = result
                if state == 'TRANSITIONING' and primary is not None:
                    facts = self._observer.last_account_facts
                    if facts is not None:
                        snapshot = ControlSnapshot(self.binding, self._observer.last_account_cut, facts)
                        await self.store._call(self._complete_successor, snapshot)
                if state == 'AWAITING_GRANT':
                    c.order_safety_reason_code = 'SESSION_SUCCESSOR_AWAITING_GRANT'
                elif state != 'TRANSITIONING':
                    c.order_safety_reason_code = 'OLD_ORDERS_RECONCILED'
                if state == 'PAUSED':
                    c._pause_reconciliation_required = False
        except asyncio.CancelledError:
            self.revoke()
            raise
        except BaseException:
            self.revoke()
            raise
        finally:
            self._safety_pending = False

    def cancel_local(self, intent_id):
        self._revoked_ids.add(intent_id)
        self.controller._runner_stop_revoked_ids.add(intent_id)
        return intent_id in self._attempted or intent_id in self._known_intents

    async def cancel_scope(self, session_id, epoch):
        return await self.cancel_scopes(((session_id, epoch),))

    async def cancel_scopes(self, scopes, *, max_requests=None):
        policy = self._cancel_policy
        if policy is None:
            raise StorageError('SPOT_APPLICATION_CANCEL_POLICY_REQUIRED')
        if max_requests is not None and (type(max_requests) is not int or max_requests < 0):
            raise StorageError('SPOT_CANCEL_REQUEST_CAP_INVALID')
        if (not isinstance(scopes, tuple) or any(not isinstance(scope, tuple) or len(scope) != 2
                                                 or not isinstance(scope[0], str) or type(scope[1]) is not int
                                                 or scope[1] <= 0 for scope in scopes)):
            raise StorageError('SPOT_CANCEL_SCOPE_INVALID')
        limit = policy.max_requests_per_cycle if max_requests is None else min(policy.max_requests_per_cycle, max_requests)
        if limit == 0:
            return 0
        async with self._cancel_lock:
            if str(await self.connector.get_account_uid()) != self.binding.account_uid:
                raise StorageError('SPOT_APPLICATION_CANCEL_ACCOUNT_MISMATCH')
            # Read durable commands, including recovered orders absent from this
            # process's issued cache. All native writes remain on the root worker.
            context = await self.store.observation_context(self.binding)
            pending = []
            for command in context.commands:
                if (command.session_id, command.epoch) not in scopes:
                    continue
                self.cancel_local(command.intent_id)
                attempt = await self.store.attempt(command.intent_id, self.binding)
                if attempt.state.value == 'PREPARED':
                    service = self.runtime._service
                    if service is not None and service.joint is not None:
                        # Joint source proof releases both claims behind one
                        # accounting barrier; standalone abort cannot do so.
                        continue
                    await self.store.apply_lifecycle(SpotLifecycleCommand('abort:' + command.operation_id,
                                                                          command.operation_id, command.intent_id,
                                                                          command.client_order_id, 'ABORT_PREPARED'),
                                                     cut=(await self.store.snapshot(self.binding)).cut, owner=self.owner)
                elif attempt.state.value not in ('TERMINAL', 'ABORTED_BEFORE_SEND'):
                    pending.append((attempt, command))
            pending.sort(key=lambda item: (item[0].cancel_attempts, item[0].intent_id))
            used, failed = 0, False
            for _, command in pending:
                if used >= limit:
                    break
                attempt = await self.store.attempt(command.intent_id, self.binding)
                if attempt.state.value not in ('SEND_UNKNOWN', 'ACKED'):
                    continue
                now = utc(self.store._clock())
                if attempt.last_cancel_attempt_at is not None:
                    if now < attempt.last_cancel_attempt_at:
                        raise StorageError('SPOT_CANCEL_CLOCK_ROLLBACK')
                    if now < attempt.last_cancel_attempt_at + timedelta(milliseconds=policy.retry_interval_ms):
                        continue
                key = hashlib.sha256((command.operation_id + ':' + str(attempt.cancel_attempts)).encode()).hexdigest()
                request = SpotLifecycleCommand('cancel:' + key, command.operation_id, command.intent_id,
                                               command.client_order_id, 'RECORD_CANCEL_ATTEMPT', now,
                                               attempt.cancel_attempts, policy.retry_interval_ms)
                receipt = await self.store.apply_lifecycle(request, cut=(await self.store.snapshot(self.binding)).cut,
                                                           owner=self.owner)
                # A recovered/replayed receipt cannot prove that REST was never
                # invoked. Consume a new durable attempt only after its deadline.
                if receipt.replayed:
                    continue
                used += 1
                try:
                    acknowledged = (await self.connector.cancel_by_client_id('LIFE-USDT', command.client_order_id)
                                    if attempt.exchange_order_id is None else
                                    await self.connector.cancel_by_exchange_order_id('LIFE-USDT', attempt.exchange_order_id))
                    failed |= acknowledged is not True
                except (TimeoutError, OSError, ValueError, TypeError, KeyError):
                    failed = True
            if failed:
                raise CancelRequestError(used)
            return used

    async def reconcile(self, session_id, epoch):
        if self._observer is None:
            raise StorageError('SPOT_APPLICATION_OBSERVER_REQUIRED')
        return await self._observer.reconcile(session_id, epoch)

    async def recover_pending(self):
        self.revoke()
        if self._observer is None:
            raise StorageError('SPOT_APPLICATION_OBSERVER_REQUIRED')
        await self.resume_session_ticks()
        return await self._observer.recover_pending()

    async def prepare_control_snapshot(self):
        self.runtime.revoke()
        if self._observer is None:
            raise StorageError('SPOT_APPLICATION_OBSERVER_REQUIRED')
        self.controller._runner_halt_ok = self.controller._halt_runner_orders(spot_only=True)
        self._control_snapshot = await self._observer.control_snapshot()
        return self._control_snapshot

    async def apply_owner_transition(self, command, *, approval, snapshot):
        if (not isinstance(command, OwnerTransition) or not isinstance(approval, OperatorApproval)
                or not isinstance(snapshot, ControlSnapshot) or snapshot.binding != self.binding):
            raise StorageError('SPOT_APPLICATION_CONTROL_INVALID')
        previous = await self.store.lookup_operation('control:' + command.operation_id, self.binding)
        if previous is None:
            self.runtime.revoke()
            if (snapshot != self._control_snapshot
                    or not 0 <= (utc(self.store._clock()) - snapshot.facts.observed_at).total_seconds() * 1000
                    <= self._reconciliation_age_ms):
                raise StorageError('SPOT_APPLICATION_CONTROL_SOURCE_UNQUALIFIED')
            await self._uid()
        receipt = await self.store.transition_owner(command, approval=approval, facts=snapshot.facts, cut=snapshot.cut)
        if (not receipt.replayed and command.mode is OwnerMode.ACTIVE and command.token.process_id == self._process_id
                and not self.controller._order_safety_stopped
                and fingerprint(financial_controller_config(self.controller.config)) == self._configuration_hash):
            self.owner = self.runtime.owner = command.token
            await self.store._call(self._sync_session)
            self._revoked = self.runtime._revoked = False
        return receipt

    def install_observer(self, observer):
        from hummingbot.strategy_v2.life_liquidity.storage.spot_observation import OkxSpotRepositoryObserver
        if self._observer is not None or not isinstance(observer, OkxSpotRepositoryObserver) or observer.application is not self:
            raise StorageError('SPOT_APPLICATION_OBSERVER_BINDING_MISMATCH')
        self._observer = observer

    async def drain(self):
        if self._pending:
            await asyncio.gather(*tuple(self._pending.values()))

    def failure_reason(self, intent_id):
        return self._failures.get(intent_id)

    async def close(self):
        self.revoke()
        safety = self.controller.order_safety_task
        tasks = tuple(self._pending.values()) + (() if safety is None or safety.done() else (safety,))
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=self.shutdown_timeout_s)
