"""Controller callbacks use a public application port, never backend internals."""
import ast
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_startup import joint_service
from test.hummingbot.strategy_v2.life_liquidity.test_executor_protected_send import _setup
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from controllers.generic.life_liquidity import LifeLiquidityController
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


class OpaqueRuntime:
    """No file objects, service internals or native worker exposed to callers."""

    def __init__(self, application):
        self.application = application

    def __getattr__(self, name):
        raise AssertionError(f'controller accessed runtime implementation: {name}')


@pytest.mark.asyncio
async def test_callbacks_only_use_public_application_port(tmp_path):
    controller, *_ = _setup(tmp_path)
    application = SimpleNamespace(
        enqueue_runner_event=Mock(), update_controller_config=Mock(return_value='reload'),
        config_load_failed=Mock(return_value='invalid'), request_halt=Mock(),
        settle_inventory=AsyncMock(return_value=True), refresh_processed_data=AsyncMock(),
        status_lines=Mock(return_value=['opaque backend status']), stop_local=Mock(),
        rejected=Mock(return_value=False), dispatched=Mock(return_value=True),
        on_runner_safety_tick=Mock(), on_safety_tick=Mock(),
        recognizes_config=Mock(return_value=True),
    )
    controller._storage_runtime = OpaqueRuntime(application)
    event, action, orchestrator = object(), object(), object()
    controller.on_runner_order_filled(event)
    controller.on_runner_order_canceled(event)
    assert controller.update_config(controller.config) == 'reload'
    assert controller.on_config_load_failure() == 'invalid'
    controller.manual_kill_switch()
    assert await controller.settle_service_inventory('cycle', ('intent',))
    await controller.update_processed_data()
    assert controller.to_format_status() == ['opaque backend status']
    controller.on_runner_stop_action(action)
    assert not controller.on_runner_create_action_rejected(action)
    assert controller.on_runner_create_action_dispatched(action)
    controller.on_runner_safety_tick(1., orchestrator)
    controller.on_safety_tick(2.)
    assert controller._runner_product_config_valid(object())
    assert application.enqueue_runner_event.call_args_list[0].args == ('fill', event)
    assert application.enqueue_runner_event.call_args_list[1].args == ('cancel', event)
    application.request_halt.assert_called_once_with()
    application.on_runner_safety_tick.assert_called_once_with(1.)
    application.on_safety_tick.assert_called_once_with(2.)
    application.refresh_processed_data.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_repository_rearm_cannot_enter_native_path_even_without_service(tmp_path):
    controller, *_ = _setup(tmp_path)
    controller._storage_runtime = OpaqueRuntime(None)
    controller._reconcile_for_manual_rearm = AsyncMock(side_effect=AssertionError('native rearm entered'))
    with pytest.raises(StorageError, match='RUNTIME_SIGNED_REARM_REQUIRED'):
        await controller.manual_rearm(operator_id='operator')
    controller._reconcile_for_manual_rearm.assert_not_awaited()


@pytest.mark.asyncio
async def test_private_native_async_reconciliation_cannot_bypass_repository(tmp_path):
    controller, *_ = _setup(tmp_path)
    controller._storage_runtime = OpaqueRuntime(None)
    with pytest.raises(StorageError, match='RUNTIME_SIGNED_REARM_REQUIRED'):
        await controller._reconcile_for_manual_rearm()
    with pytest.raises(StorageError, match='RUNTIME_REPOSITORY_RECONCILIATION_REQUIRED'):
        await controller._cancel_and_reconcile_orders()


@pytest.mark.asyncio
async def test_native_proof_entry_requires_repository_worker(tmp_path):
    service, *_ = await joint_service(tmp_path)
    try:
        for callback in (service.controller.verify_runner_fill_events,
                         service.controller._runner_executor_scope_complete,
                         service.controller.allow_create_executor_actions,
                         service.controller._execution_loss_ready,
                         service.controller._fill_attribution_ready,
                         service.controller._runtime_risk_ready,
                         service.controller._carry_ready,
                         service.controller.bounded_probe_active,
                         service.controller.markout_probe_active,
                         lambda: service.controller.quote_probe_authorizes('BUY', Decimal('1')),
                         lambda: service.controller.markout_probe_authorizes('BUY', Decimal('1')),
                         service.controller.determine_executor_actions):
            with pytest.raises(StorageError, match='WORKER'):
                callback()
        assert await service.store._call(service.controller.verify_runner_fill_events)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_legacy_telemetry_cannot_read_native_proof_on_event_loop(tmp_path):
    service, *_ = await joint_service(tmp_path)
    try:
        service.controller._telemetry = Mock()
        with pytest.raises(StorageError, match='WORKER'):
            service.controller._record_telemetry('SNAPSHOT')
    finally:
        service.controller._telemetry = None
        await service.close()


def test_controller_does_not_reach_into_runtime_implementation():
    import inspect
    tree = ast.parse(inspect.getsource(LifeLiquidityController))
    runtime_names = {'runtime', 'self._storage_runtime'}
    leaks = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if ast.unparse(node.value) in runtime_names and (node.attr.startswith('_') or node.attr == 'store'):
            leaks.append((node.lineno, node.attr))
    assert leaks == []


def test_file_paths_are_confined_to_explicit_legacy_adapter():
    import inspect
    tree = ast.parse(inspect.getsource(LifeLiquidityController))
    leaks = [node.lineno for node in ast.walk(tree)
             if isinstance(node, ast.Name) and node.id == 'Path'
             or isinstance(node, ast.Attribute) and node.attr == 'path']
    assert leaks == []
