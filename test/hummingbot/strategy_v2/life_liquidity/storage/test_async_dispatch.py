"""Real V2 tick schedules bounded creates; stops never wait behind authority."""
import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.life_liquidity.storage.async_dispatch import AsyncActionDispatcher
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, StopExecutorAction


def action(intent='one'):
    return CreateExecutorAction(controller_id='life', executor_config=OrderExecutorConfig(
        id=intent, controller_id='life', connector_name='okx', trading_pair='LIFE-USDT',
        side=TradeType.BUY, amount=Decimal('1'), price=Decimal('1'), execution_strategy=ExecutionStrategy.LIMIT_MAKER))


class Controller:
    async_dispatch_enabled = True

    def __init__(self):
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.allowed = True
        self.project_entered, self.project_release = asyncio.Event(), asyncio.Event()
        self.project_release.set()
        self.revoked = set()

    async def async_authorize_runner_create_action(self, _action):
        self.entered.set()
        await self.release.wait()
        return self.allowed

    async def async_before_runner_dispatch(self, _action):
        self.project_entered.set()
        await self.project_release.wait()
        return True

    def local_runner_create_fence(self, action):
        return self.allowed and action.executor_config.id not in self.revoked

    def on_runner_stop_action(self, action):
        self.revoked.add(action.executor_id)

    def on_runner_create_action_rejected(self, action):
        self.revoked.add(action.executor_config.id)

    def suppress_create_for_stop_batch(self):
        return True


def runner(controller):
    value = SimpleNamespace(controllers={'life': controller}, executor_orchestrator=MagicMock(),
                            logger=lambda: MagicMock(), _is_stop_triggered=False)
    value._filter_authorized_actions = lambda actions: StrategyV2Base._filter_authorized_actions(value, actions)
    return value


@pytest.mark.asyncio
async def test_stop_dispatches_while_create_waits_and_late_reply_creates_nothing():
    controller = Controller()
    value = runner(controller)
    dispatch = AsyncActionDispatcher(value, max_pending=1, shutdown_timeout_s=.05)
    dispatch.submit([action()])
    await asyncio.wait_for(controller.entered.wait(), 2)
    stop = StopExecutorAction(controller_id='life', executor_id='one')
    dispatch.submit([stop])
    value.executor_orchestrator.execute_action.assert_called_once_with(stop)
    controller.release.set()
    await dispatch.drain()
    assert value.executor_orchestrator.execute_action.call_count == 1
    await dispatch.close()


@pytest.mark.asyncio
async def test_bounded_admission_duplicate_and_changed_identity_do_not_launch_more_work():
    controller = Controller()
    value = runner(controller)
    dispatch = AsyncActionDispatcher(value, max_pending=1, shutdown_timeout_s=.05)
    dispatch.submit([action(), action(), action('two')])
    await asyncio.wait_for(controller.entered.wait(), 2)
    assert dispatch.pending_count == 1 and 'two' in controller.revoked
    changed = action()
    changed.executor_config.price = Decimal('2')
    dispatch.submit([changed])
    controller.release.set()
    await dispatch.drain()
    assert not value.executor_orchestrator.execute_action.called
    await dispatch.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['authority', 'projection'])
async def test_config_revocation_after_any_storage_await_never_dispatches(boundary):
    controller = Controller()
    if boundary == 'projection':
        controller.release.set()
        controller.project_release.clear()
    value = runner(controller)
    dispatch = AsyncActionDispatcher(value, max_pending=1, shutdown_timeout_s=.05)
    dispatch.submit([action()])
    await asyncio.wait_for((controller.entered if boundary == 'authority' else controller.project_entered).wait(), 2)
    controller.allowed = False
    controller.release.set()
    controller.project_release.set()
    await dispatch.drain()
    assert not value.executor_orchestrator.execute_action.called
    await dispatch.close()


@pytest.mark.asyncio
async def test_shutdown_and_late_storage_reply_never_launch_executor():
    controller = Controller()
    value = runner(controller)
    dispatch = AsyncActionDispatcher(value, max_pending=1, shutdown_timeout_s=.05)
    dispatch.submit([action()])
    await asyncio.wait_for(controller.entered.wait(), 2)
    await dispatch.close()
    controller.release.set()
    await asyncio.sleep(0)
    dispatch.submit([action('two')])
    assert not value.executor_orchestrator.execute_action.called


@pytest.mark.asyncio
async def test_actual_tick_uses_dispatcher_and_returns_before_authority_reply():
    controller = Controller()
    value = runner(controller)
    dispatch = AsyncActionDispatcher(value, max_pending=1, shutdown_timeout_s=.05)
    value._async_action_dispatcher = dispatch
    value.update_executors_info = lambda: None
    value.update_controllers_configs = lambda: None
    value.market_data_provider = SimpleNamespace(ready=True)
    create = action()
    value.determine_executor_actions = lambda: [create]
    StrategyV2Base.on_tick(value)
    assert not value.executor_orchestrator.execute_action.called
    await asyncio.wait_for(controller.entered.wait(), 2)
    controller.release.set()
    await dispatch.drain()
    value.executor_orchestrator.execute_action.assert_called_once_with(create)
    await dispatch.close()


@pytest.mark.asyncio
async def test_backend_swallowing_cancellation_cannot_hold_shutdown_or_send_after_close():
    class LateController(Controller):
        async def async_authorize_runner_create_action(self, _action):
            self.entered.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                await self.release.wait()
            return True

    controller = LateController()
    value = runner(controller)
    dispatch = AsyncActionDispatcher(value, max_pending=1, shutdown_timeout_s=.05)
    dispatch.submit([action()])
    await asyncio.wait_for(controller.entered.wait(), 2)
    await asyncio.wait_for(dispatch.close(), .5)
    assert dispatch.pending_count == 1
    controller.release.set()
    await dispatch.drain()
    assert not value.executor_orchestrator.execute_action.called


@pytest.mark.asyncio
async def test_async_required_controller_has_no_sync_authorization_fallback():
    controller = Controller()
    controller.allow_create_executor_actions = lambda: True
    value = runner(controller)
    assert value._filter_authorized_actions([action()]) == []


@pytest.mark.parametrize('field,value', [('amount', Decimal('2')), ('price', Decimal('.5')),
                                         ('side', TradeType.SELL), ('level_id', '1'), ('connector_name', 'other'), ('trading_pair', 'OTHER-USDT')])
def test_authorization_digest_binds_all_subclass_order_terms(field, value):
    from hummingbot.strategy_v2.life_liquidity.storage.runtime import FileControllerRuntime
    original = action()
    changed = original.model_copy(deep=True)
    setattr(changed.executor_config, field, value)
    assert AsyncActionDispatcher._payload(original) != AsyncActionDispatcher._payload(changed)
    assert FileControllerRuntime._action_digest(original) != FileControllerRuntime._action_digest(changed)


@pytest.mark.asyncio
async def test_payload_mutation_during_projection_cannot_dispatch_previously_qualified_action():
    class MutatingController(Controller):
        async def async_before_runner_dispatch(self, proposed):
            proposed.executor_config.amount = Decimal('1000')
            return True
    controller = MutatingController()
    controller.release.set()
    value = runner(controller)
    dispatch = AsyncActionDispatcher(value, max_pending=1, shutdown_timeout_s=.05)
    try:
        dispatch.submit([action()])
        await dispatch.drain()
        assert not value.executor_orchestrator.execute_action.called
    finally:
        await dispatch.close()


@pytest.mark.asyncio
async def test_awaited_dispatch_checkpoint_holds_capacity_and_never_calls_sync_fallback():
    controller = Controller()
    controller.release.set()
    entered, release = asyncio.Event(), asyncio.Event()
    sync = MagicMock()
    controller.on_runner_create_action_dispatched = sync

    async def after(action):
        entered.set()
        await release.wait()
        return True
    controller.async_on_runner_create_action_dispatched = after
    value = runner(controller)
    dispatch = AsyncActionDispatcher(value, max_pending=1, shutdown_timeout_s=.05)
    try:
        dispatch.submit([action()])
        await asyncio.wait_for(entered.wait(), 2)
        assert dispatch.pending_count == 1 and value.executor_orchestrator.execute_action.call_count == 1
        dispatch.submit([action('two')])
        assert 'two' in controller.revoked
        release.set()
        await dispatch.drain()
        sync.assert_not_called()
    finally:
        release.set()
        await dispatch.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['false', 'raise', 'stop', 'cancel'])
async def test_failed_or_revoked_awaited_acceptance_revokes_locally_and_preserves_cancel_boundary(failure):
    controller = Controller()
    controller.release.set()
    entered, release = asyncio.Event(), asyncio.Event()
    async_reject = MagicMock()

    async def after(action):
        entered.set()
        await release.wait()
        if failure == 'raise':
            raise OSError('acceptance checkpoint unavailable')
        return failure != 'false'

    async def reject(action):
        assert action.executor_config.id in controller.revoked
        async_reject(action)
    controller.async_on_runner_create_action_dispatched = after
    controller.async_on_runner_create_action_rejected = reject
    value = runner(controller)
    dispatch = AsyncActionDispatcher(value, max_pending=1, shutdown_timeout_s=.05)
    try:
        dispatch.submit([action()])
        await asyncio.wait_for(entered.wait(), 2)
        if failure == 'stop':
            dispatch.submit([StopExecutorAction(controller_id='life', executor_id='one')])
        elif failure == 'cancel':
            await dispatch.close()
        release.set()
        await dispatch.drain()
        assert 'one' in controller.revoked
        assert async_reject.call_count == 0
        assert value.executor_orchestrator.execute_action.call_count == (2 if failure == 'stop' else 1)
    finally:
        release.set()
        await dispatch.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['authority', 'acceptance'])
async def test_mutated_intent_id_revokes_original_identity_after_any_await(boundary):
    controller = Controller()
    controller.release.set()

    async def mutate(action):
        action.executor_config.id = 'mutated'
        return True
    if boundary == 'authority':
        controller.async_authorize_runner_create_action = mutate
    else:
        controller.async_on_runner_create_action_dispatched = mutate
    value = runner(controller)
    dispatch = AsyncActionDispatcher(value, max_pending=1, shutdown_timeout_s=.05)
    try:
        dispatch.submit([action('original')])
        await dispatch.drain()
        assert 'original' in controller.revoked
        assert 'mutated' not in controller.revoked
        assert value.executor_orchestrator.execute_action.call_count == (1 if boundary == 'acceptance' else 0)
    finally:
        await dispatch.close()
