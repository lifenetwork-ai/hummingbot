"""File runtime authorization seam. Repository views alone never approve a send.

The complete existing controller/sender checks run on the owning bounded worker;
authenticated UID collection and dispatch remain asynchronous. PostgreSQL is an
explicit future implementation, never a Path-compatible fallback.
"""
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


class FileControllerRuntime:
    backend = Backend.FILE

    def __init__(self, store, controller, owner):
        if not isinstance(store, FileCheckpointStore) or not isinstance(owner, OwnerToken):
            raise StorageError('RUNTIME_FILE_REPOSITORY_REQUIRED')
        self.store, self.controller, self.owner = store, controller, owner
        self.binding = store.binding
        self._action_hashes = {}
        self._revoked = False
        self._assert_config()

    def owns_controller(self, controller):
        return controller is self.controller

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
