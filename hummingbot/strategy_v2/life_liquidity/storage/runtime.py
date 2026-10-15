"""File runtime authorization seam. Repository views alone never approve a send.

The complete existing controller/sender checks run on the owning bounded worker;
authenticated UID collection and dispatch remain asynchronous. PostgreSQL is an
explicit future implementation, never a Path-compatible fallback.
"""
import asyncio
import hashlib
from datetime import datetime, timezone
from typing import Protocol

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    Backend,
    OwnerMode,
    OwnerToken,
    StateBinding,
    StorageError,
    fingerprint,
)
from hummingbot.strategy_v2.life_liquidity.storage.file import FileCheckpointStore


class RuntimeAuthorizationRepository(Protocol):
    binding: StateBinding
    backend: Backend

    def owns_controller(self, controller) -> bool:
        ...

    def validate_binding(self, binding: StateBinding, config_hash: str) -> None:
        ...

    async def authorize_action(self, action) -> bool:
        ...

    async def before_dispatch(self, action) -> bool:
        ...

    async def authorize_spot(self, permit) -> bool:
        ...

    async def authorize_swap(self, permit) -> bool:
        ...

    def local_fence(self, action) -> bool:
        ...

    def revoke(self) -> None:
        ...

    def bind_final_authority(self) -> None:
        ...

    def preflight(self, action) -> bool:
        ...

    def forget_action(self, intent_id: str) -> None:
        ...

    @property
    def handles_financial_spot(self) -> bool:
        ...

    def submit_spot(self, config, **kwargs) -> str:
        ...

    def cancel_spot_local(self, intent_id: str) -> bool:
        ...

    def on_spot_safety_tick(self, timestamp: float) -> None:
        ...


class FileControllerRuntime:
    backend = Backend.FILE

    def __init__(self, store, controller, owner, *, products=None):
        if not isinstance(store, FileCheckpointStore) or not isinstance(owner, OwnerToken):
            raise StorageError('RUNTIME_FILE_REPOSITORY_REQUIRED')
        self.store, self.controller, self.owner = store, controller, owner
        self.binding = store.binding
        self._products = products
        self._spot_application = None
        self._swap_application = None
        self._service = None
        self._recorder_application = None
        self._recorder_startup_pending = False
        self._action_hashes = {}
        self._revoked = False
        self._assert_config()
        if products is not None:
            if products.store is not store or products.binding != self.binding:
                raise StorageError('RUNTIME_PRODUCT_REPOSITORY_MISMATCH')
            products.validate_controller(controller)

    def owns_controller(self, controller):
        return controller is self.controller

    def bind_products(self, products):
        self.store.assert_worker_thread()
        if self._products is not None or products.store is not self.store or products.binding != self.binding:
            raise StorageError('RUNTIME_PRODUCT_REPOSITORY_MISMATCH')
        products.validate_controller(self.controller)
        self._products = products

    def bind_service(self, service):
        if self._service is not None or service.runtime is not self or service.store is not self.store:
            raise StorageError('RUNTIME_SERVICE_MISMATCH')
        self._service = service

    def bind_swap_application(self, application):
        if (self._swap_application is not None or application.store is not self.store
                or application.owner != self.owner or application.controller is not self.controller):
            raise StorageError('RUNTIME_SWAP_APPLICATION_MISMATCH')
        self._swap_application = application

    def bind_recorder_application(self, application):
        if (self._recorder_application is not None or application.store is not self.store
                or application.runtime is not self or application.binding != self.binding):
            raise StorageError('RUNTIME_RECORDER_APPLICATION_MISMATCH')
        self._recorder_application = application

    async def await_reporting(self):
        if self._service is not None:
            await self._service.drain_action_events()
        if self._revoked:
            raise StorageError('RUNTIME_REVOKED')
        if self._recorder_application is not None:
            age_ms = self.controller.config.recovery_reconciliation_max_age_ms
            async with asyncio.timeout(age_ms / 1000):
                await self._recorder_application.drain()
        if self._revoked:
            raise StorageError('RUNTIME_REVOKED')

    async def before_dispatch(self, action):
        if self._recorder_application is None:
            return True
        return await self._recorder_application.before_dispatch(action)

    def bind_spot_application(self, application):
        if (self._spot_application is not None or application.store is not self.store
                or application.owner != self.owner or application.owns_controller(self.controller) is not True):
            raise StorageError('RUNTIME_SPOT_APPLICATION_MISMATCH')
        self._spot_application = application

    @property
    def handles_financial_spot(self):
        return self._spot_application is not None

    def submit_spot(self, config, **kwargs):
        if self._spot_application is None:
            raise StorageError('RUNTIME_SPOT_APPLICATION_REQUIRED')
        return self._spot_application.submit(config, **kwargs)

    def cancel_spot_local(self, intent_id):
        return self._spot_application is not None and self._spot_application.cancel_local(intent_id)

    def on_spot_safety_tick(self, timestamp):
        if self._spot_application is None:
            raise StorageError('RUNTIME_SPOT_APPLICATION_REQUIRED')
        self._spot_application.on_safety_tick(timestamp)

    def validate_binding(self, binding, config_hash):
        if binding != self.binding or config_hash != self.binding.config_hash:
            raise StorageError('RUNTIME_BINDING_OR_CONFIG_MISMATCH')
        self._assert_config()

    def bind_final_authority(self):
        spot = self.controller._protected_spot_sender
        swap = self.controller._protected_swap_sender
        if self.controller.config.strategy.spot.enabled and spot is None:
            raise StorageError('RUNTIME_SPOT_SENDER_REQUIRED')
        if self.controller.config.strategy.perpetual.enabled and (
                swap is None or swap._async_authorize != self.authorize_swap):
            raise StorageError('RUNTIME_SWAP_ASYNC_AUTHORITY_REQUIRED_AT_CREATION')
        if spot is not None:
            if spot.gateway._async_authorize is not None and spot.gateway._async_authorize != self.authorize_spot:
                raise StorageError('RUNTIME_SPOT_AUTHORITY_ALREADY_BOUND')
            spot.gateway._async_authorize = self.authorize_spot

    def _assert_config(self):
        if (self.controller.config.storage_backend != 'file'
                or self.controller.config.storage_binding != self.binding
                or self.controller.config.recovery_account_uid != self.binding.account_uid
                or fingerprint(self.controller.config.strategy.model_dump(mode='json')) != self.binding.config_hash):
            raise StorageError('RUNTIME_BINDING_OR_CONFIG_MISMATCH')

    @staticmethod
    def _action_digest(action):
        return hashlib.sha256(action.model_dump_json().encode()).hexdigest()

    def _local_ready(self):
        try:
            if self._recorder_startup_pending or self._service is not None and self._service.admission_paused:
                return False
            if self._recorder_application is not None and not self._recorder_application.ready:
                return False
            if self._spot_application is not None and not self._spot_application.local_ready():
                return False
            self._assert_config()
            now = datetime.now(timezone.utc) if self.store._clock is None else self.store._clock()
            return (not self._revoked and not self.store._closed and not self.store._uncertain
                    and not getattr(self.store, '_cold_unarmed', False)
                    and getattr(self.store, '_admission_generation', self.owner.generation) == self.owner.generation
                    and self.owner.valid_from <= now < self.owner.expires_at
                    and not self.controller._order_safety_stopped
                    and self.controller.config_update_state.order_permission() is True
                    and self.controller.trading_permissions_ready() is True)
        except Exception:
            return False

    def local_fence(self, action):
        return (self._local_ready() and action.controller_id == self.controller.config.id
                and action.executor_config.id not in self.controller._runner_stop_revoked_ids
                and self._action_hashes.get(action.executor_config.id) == self._action_digest(action))

    def preflight(self, action):
        return (self._local_ready() and action.controller_id == self.controller.config.id
                and action.executor_config.id not in self.controller._runner_stop_revoked_ids)

    def forget_action(self, intent_id):
        self._action_hashes.pop(intent_id, None)

    async def _uid(self, product):
        if product == 'SWAP':
            sender = self.controller._protected_swap_sender
            connector = sender.connector if sender is not None else None
        else:
            gateway = self.controller._order_safety_gateway
            connector = gateway.connector if gateway is not None else None
        fetch = getattr(connector, 'get_account_uid', None)
        if not callable(fetch) or await fetch() != self.binding.account_uid:
            raise StorageError('RUNTIME_AUTHENTICATED_UID_MISMATCH')

    def _worker_check(self, kind, value):
        if not self._local_ready():
            return False
        meta = self.store._read()
        if meta.ownership.mode is not OwnerMode.ACTIVE or meta.ownership.token != self.owner:
            return False
        if any(row['state'] != 'APPLIED' for row in meta.resources.get('product_operations', {}).values()):
            return False
        if any(row['state'] != 'APPLIED' for row in meta.resources.get('recorder_outbox', {}).values()):
            return False
        if any(row['state'] != 'APPLIED' for row in meta.resources.get('recorder_progress', {}).values()):
            return False
        if any(row['state'] != 'APPLIED' for row in meta.resources.get('recorder_positions', {}).values()):
            return False
        if any(row['state'] != 'APPLIED' for row in meta.resources.get('recorder_events', {}).values()):
            return False
        if self._products is not None:
            self._products.validate_controller(self.controller)
            self._products._native_cuts()
        if hasattr(self.store, '_snapshot'):
            view = self.store._snapshot()
            # A durable SEND_UNKNOWN is expected for final authorization;
            # it is not an activation grant and cannot authorize a retry.
            if (self.store._incomplete(meta.resources)
                    or dict(view.cut.components) != dict(meta.resources['journal_hashes'])):
                return False
        current = self.controller._order_safety_manager.current_session
        if current is None or (current.session_id, current.epoch) != (str(self.owner.session_id), self.owner.epoch):
            return False
        if kind == 'ACTION':
            result = (self.controller.allow_executor_action(value) is True
                      and self.controller.authorize_runner_create_action(value) is True)
        elif kind == 'SPOT':
            sender = self.controller._protected_spot_sender
            result = sender is not None and sender._authorized(value) is True
        else:
            sender = self.controller._protected_swap_sender
            config = sender._configs.get(value.intent_id) if sender is not None else None
            if config is None:
                return False
            from hummingbot.strategy_v2.executors.order_executor.data_types import OrderExecutorConfig
            sender._check(OrderExecutorConfig.model_validate(config), value)
            result = True
        return result and self._local_ready()

    async def authorize_action(self, action):
        detached = action.model_copy(deep=True)
        self.forget_action(detached.executor_config.id)
        if len(self._action_hashes) >= self.store.limits.max_pending:
            return False
        digest = self._action_digest(detached)
        product = 'SWAP' if self.controller._is_swap_config(detached.executor_config) else 'SPOT'
        try:
            if not self._local_ready():
                return False
            await self._uid(product)
            result = await self.store._call(self._worker_check, 'ACTION', detached)
            if result is True and self._local_ready() and self._action_digest(detached) == digest:
                self._action_hashes[detached.executor_config.id] = digest
                return True
        except Exception:
            return False
        return False

    async def _authorize_permit(self, kind, permit):
        if not self._local_ready():
            return False
        try:
            await self._uid(kind)
            return await self.store._call(self._worker_check, kind, permit) is True and self._local_ready()
        except Exception:
            return False

    async def authorize_spot(self, permit):
        return await self._authorize_permit('SPOT', permit)

    async def authorize_swap(self, permit):
        return await self._authorize_permit('SWAP', permit)

    def revoke(self):
        self._revoked = True
        self._action_hashes.clear()
        if self._spot_application is not None:
            self._spot_application.revoke()
        if self._swap_application is not None:
            self._swap_application.revoke()
