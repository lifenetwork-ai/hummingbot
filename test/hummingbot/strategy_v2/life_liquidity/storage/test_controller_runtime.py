"""Actual LIFE controller accepts typed runtime binding; production stays disabled."""
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import CAPACITY, create_fixture
from test.hummingbot.strategy_v2.life_liquidity.test_executor_protected_send import _setup
from test.hummingbot.strategy_v2.life_liquidity.test_session import FakeClock
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from controllers.generic.life_liquidity import LifeLiquidityConfig, LifeLiquidityController
from hummingbot.strategy_v2.life_liquidity.storage.contracts import Backend, OwnerToken, StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.file import open_store
from hummingbot.strategy_v2.life_liquidity.storage.runtime import FileControllerRuntime
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction


@pytest.mark.asyncio
async def test_real_controller_async_runtime_uid_owner_config_and_local_stop_fences(tmp_path):
    clock = FakeClock()
    c, executor, route, _, _, _ = _setup(tmp_path, recovery_account_uid='12345', clock=clock)
    session = c._order_safety_manager.current_session
    _, binding, _ = create_fixture(tmp_path / 'store-fixture')
    binding = replace(binding, config_hash=fingerprint(c.config.strategy.model_dump(mode='json')))
    c.config = c.config.model_copy(update={'storage_binding': binding})
    owner = OwnerToken(UUID(int=2), UUID(int=3), session.epoch, UUID(session.session_id), session.started_at, session.expires_at)
    store = await open_store(Backend.FILE, path=tmp_path / 'runtime.json', binding=binding, limits=CAPACITY,
                             create=True, initial={}, owner=owner, clock=lambda: clock.wall)
    route.get_account_uid = AsyncMock(return_value='12345')
    runtime = FileControllerRuntime(store, c, owner)
    c.install_storage_runtime(runtime)
    action = CreateExecutorAction(controller_id=c.config.id, executor_config=executor.config)
    try:
        assert c.async_dispatch_enabled
        assert not await c.async_authorize_runner_create_action(action)  # Actual release remains false.
        c.trading_permissions_ready = lambda: True  # Explicit isolated fixture release substitution only.
        assert await c.async_authorize_runner_create_action(action)
        assert c.local_runner_create_fence(action)
        route.get_account_uid.return_value = '999'
        assert not await c.async_authorize_runner_create_action(action)
        assert not c.local_runner_create_fence(action)
        route.get_account_uid.return_value = '12345'
        c.config_update_state.reject('CONFIG_LOAD_FAILED')
        assert not c.local_runner_create_fence(action)
        assert not await c.async_authorize_runner_create_action(action)
        runtime.revoke()
        assert not c.local_runner_create_fence(action)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_runtime_rejects_namespace_or_config_alias_without_touching_a_path(tmp_path):
    c, _, _, _, _, _ = _setup(tmp_path, recovery_account_uid='12345')
    _, binding, owner = create_fixture(tmp_path / 'fixture')
    c.config = c.config.model_copy(update={'storage_binding': binding})
    store = await open_store(Backend.FILE, path=tmp_path / 'runtime.json', binding=binding,
                             limits=CAPACITY, create=True, initial={}, owner=owner, clock=lambda: owner.valid_from)
    try:
        with pytest.raises(StorageError):
            FileControllerRuntime(store, c, owner)
    finally:
        await store.close()


def test_postgres_selection_fails_before_legacy_file_bootstrap(tmp_path):
    from unittest.mock import MagicMock
    config = LifeLiquidityConfig.model_construct(storage_backend='postgres', recovery_state_dir=str(tmp_path / 'must-not-exist'))
    with pytest.raises(ValueError, match='LIFE_POSTGRES_RUNTIME_NOT_IMPLEMENTED'):
        LifeLiquidityController(config, MagicMock(), MagicMock())
    assert not (tmp_path / 'must-not-exist').exists()


@pytest.mark.asyncio
async def test_real_controller_through_actual_dispatcher_qualifies_once_and_releases_cached_action(tmp_path):
    from types import SimpleNamespace

    from hummingbot.strategy.strategy_v2_base import StrategyV2Base
    from hummingbot.strategy_v2.life_liquidity.storage.async_dispatch import AsyncActionDispatcher

    clock = FakeClock()
    c, executor, route, _, _, _ = _setup(tmp_path, recovery_account_uid='12345', clock=clock)
    session = c._order_safety_manager.current_session
    _, binding, _ = create_fixture(tmp_path / 'fixture')
    binding = replace(binding, config_hash=fingerprint(c.config.strategy.model_dump(mode='json')))
    c.config = c.config.model_copy(update={'storage_binding': binding})
    owner = OwnerToken(UUID(int=2), UUID(int=3), session.epoch, UUID(session.session_id), session.started_at, session.expires_at)
    store = await open_store(Backend.FILE, path=tmp_path / 'runtime.json', binding=binding, limits=CAPACITY,
                             create=True, initial={}, owner=owner, clock=lambda: clock.wall)
    route.get_account_uid = AsyncMock(return_value='12345')
    c.trading_permissions_ready = lambda: True  # Isolated release substitution, no actual exchange transport.
    runtime = FileControllerRuntime(store, c, owner)
    c.install_storage_runtime(runtime)
    runner = SimpleNamespace(controllers={'life': c}, _is_stop_triggered=False, executor_orchestrator=MagicMock(), logger=c.logger)
    runner._filter_authorized_actions = lambda actions: StrategyV2Base._filter_authorized_actions(runner, actions)
    dispatch = AsyncActionDispatcher(runner, max_pending=1, shutdown_timeout_s=.05)
    try:
        action = CreateExecutorAction(controller_id='life', executor_config=executor.config)
        dispatch.submit([action])
        await dispatch.drain()
        runner.executor_orchestrator.execute_action.assert_called_once_with(action)
        assert runtime._action_hashes == {}
    finally:
        await dispatch.close()
        await store.close()
