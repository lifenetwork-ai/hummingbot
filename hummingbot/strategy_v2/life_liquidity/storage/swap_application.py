"""Asynchronous protected SWAP application over the owning repository worker."""
import asyncio
import math
import uuid

from hummingbot.core.data_type.common import OrderType
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, encode
from hummingbot.strategy_v2.life_liquidity.storage.recorder_configuration import financial_controller_config
from hummingbot.strategy_v2.life_liquidity.storage.swap import FileSwapRepository, PrepareSwap, wire_matches
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction


class FileSwapApplication:
    def __init__(self, repository, *, max_pending, ack_timeout_s, shutdown_timeout_s):
        if (type(repository) is not FileSwapRepository or repository.application is not None
                or type(max_pending) is not int or max_pending <= 0 or max_pending > repository.store.limits.max_pending
                or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0
                       for v in (ack_timeout_s, shutdown_timeout_s))):
            raise StorageError('SWAP_APPLICATION_CONFIG_INVALID')
        self.repository, self.store, self.sender = repository, repository.store, repository.sender
        self.controller, self.connector, self.owner = self.sender.controller, self.sender.connector, repository.joint.owner
        self.max_pending, self.ack_timeout_s, self.shutdown_timeout_s = max_pending, ack_timeout_s, shutdown_timeout_s
        self._issued, self._seen, self._pending, self._failures = {}, set(), {}, {}
        self._unknown_attempts, self._acknowledged_ids = set(), set()
        self._revoked_ids, self._revoked, self._proposals = set(), False, 0
        self._configuration = encode(financial_controller_config(self.controller.config))
        repository.application = self

    def local_ready(self):
        try:
            c = self.controller
            runtime = c._storage_runtime
            if runtime is not None and runtime._service is not None and runtime._service.admission_paused:
                return False
            if (runtime is not None and runtime._service is not None and runtime._service.risk is not None
                    and not runtime._service.risk.local_ready(normal_only=True)):
                return False
            return (not self._revoked and not self.store._closed and not self.store._uncertain
                    and not self.repository.joint._revoked and self.sender._runtime_enabled
                    and self.repository.joint._recovery._armed and self.repository.joint._recovery._healthy
                    and self.owner.valid_from <= self.store._clock() < self.owner.expires_at
                    and not getattr(self.store, '_cold_unarmed', False)
                    and not c.config.manual_kill_switch and not c._order_safety_stopped and not c._safety_rearm_in_progress
                    and not c._runner_scope_invalid and c.config_update_state.order_permission() is True
                    and c.trading_permissions_ready() is True and encode(financial_controller_config(c.config)) == self._configuration)
        except Exception:
            return False

    async def _uid(self):
        if await self.connector.get_account_uid() != self.repository.binding.account_uid:
            raise StorageError('SWAP_APPLICATION_UID_MISMATCH')

    async def propose(self, config):
        command = PrepareSwap.from_config('swap:' + uuid.uuid4().hex, 'HBOT' + uuid.uuid4().hex[:28], config)
        if (not self.local_ready() or config.id in self._seen or config.id in self._revoked_ids
                or config.id in self.controller._runner_stop_revoked_ids):
            raise StorageError('SWAP_APPLICATION_REVOKED_OR_REPLAY')
        if self._proposals + len(self._pending) + len(self._unknown_attempts) >= self.max_pending or len(self._seen) >= self.store.limits.max_operations:
            raise StorageError('SWAP_APPLICATION_BACKPRESSURE')
        self._seen.add(config.id)
        self._proposals += 1
        try:
            await self._uid()
            result = await self.repository.prepare(command, cut=await self.repository.cut(self.repository.binding), owner=self.owner)
            if result.state != 'PREPARED' or not self.local_ready() or config.id in self._revoked_ids:
                raise StorageError('SWAP_APPLICATION_REVOKED_OR_REPLAY')
            self._issued[config.id] = (command, result, False)
            return CreateExecutorAction(controller_id=self.controller.config.id, executor_config=command.config())
        finally:
            self._proposals -= 1

    def adopt_prepared(self, command, receipt):
        # This local cache never replaces repository qualification at arm/send.
        if (type(command) is not PrepareSwap or receipt is None or receipt.binding != self.repository.binding
                or receipt.operation_id != command.operation_id or receipt.state != 'PREPARED'
                or receipt.permit.intent_id != command.config().id or receipt.permit.client_order_id != command.client_order_id
                or not self.local_ready() or command.config().id in self._seen
                or len(self._seen) >= self.store.limits.max_operations):
            raise StorageError('SWAP_PREPARED_ADOPTION_DENIED')
        self._seen.add(command.config().id)
        self._issued[command.config().id] = (command, receipt, False)

    def authorizes_config(self, config):
        try:
            row = self._issued.get(config.id)
            return (row is not None and not row[2] and self.local_ready()
                    and config.id not in self._revoked_ids and config.id not in self.controller._runner_stop_revoked_ids
                    and PrepareSwap.from_config(row[0].operation_id, row[0].client_order_id, config) == row[0])
        except Exception:
            return False

    def submit(self, config, *, amount, price, order_type):
        if (amount != config.amount or price != config.price or order_type != OrderType.LIMIT_MAKER
                or not self.authorizes_config(config)):
            raise StorageError('SWAP_APPLICATION_ACTION_UNAUTHORIZED')
        if len(self._pending) + len(self._unknown_attempts) >= self.max_pending:
            raise StorageError('SWAP_APPLICATION_BACKPRESSURE')
        command, receipt, _ = self._issued[config.id]
        self._issued[config.id] = (command, receipt, True)
        task = asyncio.create_task(self._submit(command, receipt))
        self._pending[config.id] = task

        def done(result):
            self._pending.pop(config.id, None)
            if result.cancelled():
                self._failures[config.id] = 'SWAP_APPLICATION_CANCELLED'
            else:
                error = result.exception()
                if error is not None:
                    self._failures[config.id] = error.code if isinstance(error, StorageError) else 'SWAP_APPLICATION_ATTEMPT_UNKNOWN'
            if config.id in self._failures and config.id not in self._acknowledged_ids:
                # Timeout/cancel does not prove the connector's scheduled RPC
                # ended. Retain admission until qualified original ACK/recovery.
                self._unknown_attempts.add(config.id)
        task.add_done_callback(done)
        return command.client_order_id

    def _local_fence(self, command, receipt, wire):
        config, permit = command.config(), receipt.permit
        if (not self.local_ready() or config.id in self._revoked_ids
                or config.id in self.controller._runner_stop_revoked_ids or not wire_matches(wire, config, permit)
                or self.sender.risk_epoch() != permit.risk_epoch
                or self.connector.position_mode != permit.position_mode
                or self.connector.get_leverage(config.trading_pair) != permit.leverage
                or self.connector._contract_sizes.get(config.trading_pair) != permit.contract_value_life):
            raise StorageError('SWAP_APPLICATION_FINAL_REVOKED')

    async def _submit(self, command, receipt):
        config, permit = command.config(), receipt.permit
        runtime = self.controller._storage_runtime
        if runtime is not None:
            await runtime.await_reporting()
        await self._uid()
        if not self.local_ready() or config.id in self._revoked_ids:
            raise StorageError('SWAP_APPLICATION_FINAL_REVOKED')
        await self.repository.arm_prepared(command.operation_id, owner=self.owner)
        acknowledged = asyncio.Event()
        boundary_attempted, qualified, request_started = False, False, False
        checked_at_ms = valid_until_ms = None

        async def authority(wire):
            nonlocal boundary_attempted, qualified, checked_at_ms, valid_until_ms
            if boundary_attempted:
                raise StorageError('SWAP_RECONCILE_BEFORE_RETRY')
            boundary_attempted = True
            self._local_fence(command, receipt, wire)
            await self._uid()
            if runtime is not None:
                await runtime.await_reporting()
            if self.sender._async_authorize is not None and await self.sender._async_authorize(permit) is not True:
                raise StorageError('SWAP_ASYNC_AUTHORITY_DENIED')
            boundary = await self.repository.authorize_boundary(command.operation_id, wire_payload=encode(wire), owner=self.owner)
            self._local_fence(command, receipt, wire)
            checked_at_ms, valid_until_ms = boundary.boundary_checked_at_ms, boundary.boundary_valid_until_ms
            qualified = boundary.state == 'SEND_UNKNOWN' and not boundary.replayed

        def final_check(wire):
            nonlocal qualified, request_started
            now_ms = self.sender.clock_ms()  # Injected clock/epoch callbacks must be cache-only.
            if (not qualified or request_started or type(now_ms) is not int
                    or type(checked_at_ms) is not int or type(valid_until_ms) is not int
                    or not checked_at_ms <= now_ms <= valid_until_ms):
                raise StorageError('SWAP_RECONCILE_BEFORE_RETRY')
            self._local_fence(command, receipt, wire)
            qualified, request_started = False, True

        async def ack(exchange_id):
            if not request_started:
                raise StorageError('SWAP_ACK_WITHOUT_BOUNDARY')
            await self.repository.acknowledge(command.operation_id, exchange_order_id=exchange_id, owner=self.owner)
            self._acknowledged_ids.add(config.id)
            self._unknown_attempts.discard(config.id)
            acknowledged.set()

        self.connector.submit_protected_order(order_id=permit.client_order_id, trading_pair=config.trading_pair,
                                              amount=config.amount, price=config.price, trade_type=config.side,
                                              order_type=OrderType.LIMIT_MAKER, position_action=config.position_action,
                                              pre_send_check=final_check, async_pre_send_check=authority, async_on_ack=ack)
        await asyncio.wait_for(acknowledged.wait(), self.ack_timeout_s)

    def revoke_intent(self, intent_id):
        # Immediate local stop never queues behind a financial worker.
        self._revoked_ids.add(intent_id)

    def revoke(self):
        self._revoked = True

    def unresolved_order_ids(self):
        # Conservatively retain issued wires until joint source qualification;
        # this local diagnostic never reads a native journal on the event loop.
        return tuple(sorted(set(self.repository._known_wires)
                            | {row[0].client_order_id for row in self._issued.values()}))

    async def drain(self):
        if self._pending:
            await asyncio.gather(*tuple(self._pending.values()), return_exceptions=True)

    async def close(self):
        self.revoke()
        tasks = tuple(self._pending.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=self.shutdown_timeout_s)

    def failure_reason(self, intent_id):
        return self._failures.get(intent_id)
