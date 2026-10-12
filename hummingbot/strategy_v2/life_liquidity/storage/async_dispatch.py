"""Bounded executor-create tasks; stop dispatch and local fences stay synchronous.

This scheduler grants no order permission. Protected senders independently await
fresh storage authority and run their local fence at every REST attempt.
"""
import asyncio
import hashlib
import math

from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, StopExecutorAction


class AsyncActionDispatcher:
    def __init__(self, runner, *, max_pending, shutdown_timeout_s):
        if type(max_pending) is not int or max_pending <= 0:
            raise ValueError('ASYNC_DISPATCH_CAPACITY_REQUIRED')
        if (type(shutdown_timeout_s) not in (int, float) or not math.isfinite(shutdown_timeout_s)
                or shutdown_timeout_s <= 0):
            raise ValueError('ASYNC_DISPATCH_SHUTDOWN_BUDGET_REQUIRED')
        self.runner, self.max_pending = runner, max_pending
        self.shutdown_timeout_s = shutdown_timeout_s
        self._pending, self._generation = {}, {}
        self._closed = False

    @property
    def pending_count(self):
        return len(self._pending)

    @staticmethod
    def _payload(action):
        return hashlib.sha256(action.model_dump_json().encode()).hexdigest()

    def _reject(self, action):
        controller = self.runner.controllers.get(action.controller_id)
        rejected = getattr(controller, 'on_runner_create_action_rejected', None)
        if callable(rejected):
            try:
                rejected(action)
            except Exception:
                self.runner.logger().error('Async create rejection checkpoint failed', exc_info=True)

    def _valid(self, action, controller, generation, digest):
        return (not self._closed and not getattr(self.runner, '_is_stop_triggered', False)
                and self.runner.controllers.get(action.controller_id) is controller
                and self._generation.get(action.controller_id, 0) == generation
                and self._payload(action) == digest
                and controller.local_runner_create_fence(action) is True)

    def _preflight(self, action, controller, generation, digest):
        check = getattr(controller, 'local_runner_create_preflight', controller.local_runner_create_fence)
        return (not self._closed and not getattr(self.runner, '_is_stop_triggered', False)
                and self.runner.controllers.get(action.controller_id) is controller
                and self._generation.get(action.controller_id, 0) == generation
                and self._payload(action) == digest and check(action) is True)

    async def _dispatch(self, action, controller, generation, digest):
        try:
            if (not self._preflight(action, controller, generation, digest)
                    or await controller.async_authorize_runner_create_action(action) is not True
                    or not self._valid(action, controller, generation, digest)):
                self._reject(action)
                return
            before = getattr(controller, 'async_before_runner_dispatch', None)
            if callable(before) and await before(action) is not True:
                self._reject(action)
                return
            if not self._valid(action, controller, generation, digest):
                self._reject(action)
                return
            self.runner.executor_orchestrator.execute_action(action)
            dispatched = getattr(controller, 'on_runner_create_action_dispatched', None)
            if callable(dispatched):
                dispatched(action)
        except asyncio.CancelledError:
            self._reject(action)
            raise
        except Exception:
            self._reject(action)
            self.runner.logger().error('Async create qualification failed', exc_info=True)

    def submit(self, actions):
        if not isinstance(actions, (list, tuple)):
            raise ValueError('ASYNC_DISPATCH_BATCH_INVALID')
        stopped = {action.controller_id for action in actions if isinstance(action, StopExecutorAction)}
        for controller_id in stopped:
            self._generation[controller_id] = self._generation.get(controller_id, 0) + 1
        # Revocation/filter and stop execution are never queued behind a create.
        immediate = [action for action in actions if not isinstance(action, CreateExecutorAction)]
        for action in self.runner._filter_authorized_actions(immediate):
            self.runner.executor_orchestrator.execute_action(action)
        for action in actions:
            if not isinstance(action, CreateExecutorAction):
                continue
            controller = self.runner.controllers.get(action.controller_id)
            if self._closed or action.controller_id in stopped or getattr(self.runner, '_is_stop_triggered', False):
                self._reject(action)
                continue
            if getattr(controller, 'async_dispatch_enabled', False) is not True:
                for allowed in self.runner._filter_authorized_actions([action]):
                    self.runner.executor_orchestrator.execute_action(allowed)
                    dispatched = getattr(controller, 'on_runner_create_action_dispatched', None)
                    if callable(dispatched):
                        dispatched(allowed)
                continue
            key = (action.controller_id, action.executor_config.id)
            digest = self._payload(action)
            previous = self._pending.get(key)
            if previous is not None:
                if previous[1] != digest:
                    self._generation[action.controller_id] = self._generation.get(action.controller_id, 0) + 1
                    self._reject(action)
                continue
            if len(self._pending) >= self.max_pending:
                self._reject(action)
                continue
            # Detach mutable Pydantic configs before the first storage await.
            detached = action.model_copy(deep=True)
            generation = self._generation.get(action.controller_id, 0)
            task = asyncio.create_task(self._dispatch(detached, controller, generation, digest))
            self._pending[key] = (task, digest)

            def done(result, pending_key=key):
                if self._pending.get(pending_key, (None,))[0] is result:
                    self._pending.pop(pending_key)
                if not result.cancelled():
                    result.exception()
            task.add_done_callback(done)

    async def drain(self):
        if self._pending:
            await asyncio.gather(*(value[0] for value in tuple(self._pending.values())), return_exceptions=True)

    async def close(self):
        self._closed = True
        tasks = tuple(value[0] for value in self._pending.values())
        for task in tasks:
            task.cancel()
        if tasks:
            # A backend may physically finish after cancellation. Closed/local
            # generation fences still deny dispatch when its late reply arrives.
            await asyncio.wait(tasks, timeout=self.shutdown_timeout_s)
