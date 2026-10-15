"""Configured service creates the actual controller/runner on worker-backed storage."""
import asyncio
from dataclasses import replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import (
    BUDGET,
    CAPACITY,
    NOW,
    RISK,
    create_fixture,
)
from unittest.mock import MagicMock

import pytest

from controllers.generic.life_liquidity import LifeLiquidityConfig
from hummingbot.strategy_v2.life_liquidity.storage.capital import SpotAccountingPolicy
from hummingbot.strategy_v2.life_liquidity.storage.contracts import fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.financial import SpotFillPolicy
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryPolicy
from hummingbot.strategy_v2.life_liquidity.storage.terminal import SpotTerminalPolicy


async def settings(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.storage.service_config import FileServiceConfig
    files, binding, owner = create_fixture(tmp_path / 'state')
    config = LifeLiquidityConfig(id='life', recovery_account_uid=binding.account_uid,
                                 recovery_reconciliation_max_age_ms=1000, cancel_retry_interval_ms=100,
                                 cancel_max_requests_per_cycle=1)
    binding = replace(binding, config_hash=fingerprint(config.strategy.model_dump(mode='json')))
    config = config.model_copy(update={'storage_binding': binding})
    return FileServiceConfig(controller=config, state_dir=str(files.binding.parent),
                             account_lock_root=str(tmp_path / 'locks'), owner=owner, file_limits=CAPACITY,
                             risk_limits=RISK, subsidy_policy=BUDGET, loss_policy=BUDGET,
                             accounting_policy=SpotAccountingPolicy(Decimal('5'), Decimal('10'), Decimal('1'), NOW, '1', ()),
                             fill_policy=SpotFillPolicy(1000), terminal_policy=SpotTerminalPolicy(1000),
                             recovery_policy=SpotFinancialRecoveryPolicy(1000), config_version=1, max_pending=1,
                             shutdown_timeout_s=.2, ack_timeout_s=1, recorder_path=str(tmp_path / 'recorder.sqlite'),
                             recorder_config_name='synthetic-life', observation_max_pages=2)


@pytest.mark.asyncio
async def test_configured_spot_service_assembles_controller_and_runner_then_starts_cold_unarmed(tmp_path):
    from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
    from hummingbot.strategy_v2.life_liquidity.storage.service import FileLiquidityService, ServiceSources
    from hummingbot.strategy_v2.life_liquidity.storage.spot_application import SpotAdmission
    config = await settings(tmp_path)
    connector = OkxExchange('', '', '', trading_pairs=['LIFE-USDT'], trading_required=False)

    async def uid():
        return config.controller.recovery_account_uid
    connector.get_account_uid = uid
    sources = ServiceSources(clock=lambda: NOW, risk_epoch=lambda: 1, authorize_permit=lambda _: True,
                             spot_admission=lambda _: SpotAdmission(Decimal('1'), Decimal('.1'), Decimal('0'), Decimal('0')))
    service = await FileLiquidityService.open(config, connectors={'okx': connector}, sources=sources,
                                              market_data_provider=MagicMock(), create_binding=True)
    try:
        assert service.controller._storage_runtime is service.runtime
        assert service.runner.controllers['life'] is service.controller
        assert service.controller._order_safety_wal is service.store._wal
        assert not service.controller.trading_permissions_ready()
    finally:
        await service.close()
    cold = await FileLiquidityService.open(config, connectors={'okx': connector}, sources=sources,
                                           market_data_provider=MagicMock(), create_binding=False)
    try:
        assert cold.store._cold_unarmed and not cold.runtime._local_ready()
        assert cold.runner.executor_orchestrator.active_executors['life'] == []
    finally:
        await cold.close()


async def joint_service(tmp_path, *, create=True):
    from test.hummingbot.strategy_v2.life_liquidity.test_hedge_coordinator import setup_coordinator

    from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
    from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
    from hummingbot.core.data_type.common import PositionMode
    from hummingbot.strategy_v2.life_liquidity.joint_recovery import RecoveryBundle
    from hummingbot.strategy_v2.life_liquidity.storage.joint import JointLimits
    from hummingbot.strategy_v2.life_liquidity.storage.service import FileLiquidityService, ServiceSources
    from hummingbot.strategy_v2.life_liquidity.storage.service_config import FileServiceConfig, JointServiceConfig
    from hummingbot.strategy_v2.life_liquidity.storage.spot_application import SpotAdmission
    basic = await settings(tmp_path)
    native, values, market, _ = setup_coordinator(tmp_path / 'reference', budget_capacity=50)
    strategy = values[0].config.strategy
    binding = replace(basic.controller.storage_binding, config_hash=fingerprint(strategy.model_dump(mode='json')))
    controller = basic.controller.model_copy(update={'strategy': strategy, 'storage_binding': binding})
    config = FileServiceConfig.model_validate({**{key: getattr(basic, key) for key in FileServiceConfig.model_fields}, 'controller': controller,
                                               'capital_policy': values[1].policy, 'swap_contract': values[2].contract,
                                               'request_policy': values[2].request_budget.policy, 'hedge_policy': native.policy,
                                               'joint': JointServiceConfig(limits=JointLimits(16, 64, 65536),
                                                                           max_age_ms=1000, cancel_retry_ms=100, max_cancels_per_cycle=2, poll_interval_ms=250,
                                                                           opening_long_entry=Decimal('1'), opening_short_entry=Decimal('0'))})
    at = int(NOW.timestamp() * 1000)
    capital = replace(values[8]['snapshot'], spot_life_base=Decimal('5'), observed_at_ms=at)
    account = replace(values[7]['value'], observed_at_ms=at)
    bundle = RecoveryBundle(binding.account_uid, at, at, 1, (), (), (), True, True, 'okx_joint_reconciled')

    async def collect():
        return bundle

    async def cancel(_):
        pass
    sources = ServiceSources(clock=lambda: NOW, risk_epoch=lambda: 1, authorize_permit=lambda _: True,
                             spot_admission=lambda _: SpotAdmission(Decimal('1'), Decimal('.1'), Decimal('0'), Decimal('0')),
                             capital_snapshot=lambda: capital, swap_account=lambda: account, joint_collect=collect,
                             cancel_swap=cancel, hedge_market=lambda: replace(market['value'], observed_at_ms=at))
    spot = OkxExchange('', '', '', trading_pairs=['LIFE-USDT'], trading_required=False)
    swap = OkxPerpetualDerivative(trading_pairs=['LIFE-USDT'], trading_required=False)
    swap._perpetual_trading._position_mode = PositionMode.ONEWAY
    swap._perpetual_trading.set_leverage('LIFE-USDT', 1)
    swap._contract_sizes = {'LIFE-USDT': values[2].contract.contract_value_life}

    async def uid():
        return binding.account_uid
    spot.get_account_uid = swap.get_account_uid = uid
    service = await FileLiquidityService.open(config, connectors={'okx': spot, 'okx_perpetual': swap}, sources=sources,
                                              market_data_provider=MagicMock(), create_binding=create)
    return service, config, sources, spot, swap


@pytest.mark.asyncio
async def test_configured_joint_service_builds_swap_hedge_and_reporting_from_policies(tmp_path):
    service, config, _, _, _ = await joint_service(tmp_path)
    binding = config.controller.storage_binding
    try:
        assert service.controller._hedge_coordinator is service.hedge.native
        assert service.controller._protected_swap_sender is service.swap.sender
        assert service.controller._execution_loss_budget is service.store._loss
        assert service.runtime._swap_application is service.swap
        assert await service.joint.reconcile(binding, config.owner)
        assert not service.controller.trading_permissions_ready()
        assert service.runner.controllers['life'] is service.controller
        service.runner.listen_to_executor_actions_task.cancel()
        await asyncio.gather(service.runner.listen_to_executor_actions_task, return_exceptions=True)
        service.controller.trading_permissions_ready = lambda: True  # Synthetic release only.
        service.controller.executors_update_event.set()
        await service.controller.control_task()
        actions = await asyncio.wait_for(service.controller.actions_queue.get(), 2)
        assert len(actions) == 1 and service.swap.authorizes_config(actions[0].executor_config)
        assert service.recognizes_config(actions[0].executor_config)
        from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
        with pytest.raises(StorageError, match='STORAGE_WORKER_REQUIRED'):
            service.controller.determine_executor_actions()
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['postgres', 'binding', 'scope', 'owner', 'float_count', 'negative_timeout'])
async def test_invalid_service_configuration_is_refused_before_bootstrap(tmp_path, fault):
    import json

    from pydantic import ValidationError

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    from hummingbot.strategy_v2.life_liquidity.storage.service_config import FileServiceConfig
    config = await settings(tmp_path)
    values = json.loads(config.model_dump_json())
    if fault == 'postgres':
        values['controller']['storage_backend'] = 'postgres'
    elif fault == 'binding':
        values['controller']['storage_binding']['config_hash'] = 'f' * 64
    elif fault == 'scope':
        values['controller']['strategy']['perpetual']['enabled'] = True
    elif fault == 'owner':
        values['owner']['expires_at'] = values['owner']['valid_from']
    elif fault == 'float_count':
        values['max_pending'] = True
    else:
        values['shutdown_timeout_s'] = -1
    with pytest.raises((StorageError, ValidationError)):
        FileServiceConfig.model_validate_json(json.dumps(values))
    assert not (tmp_path / 'state' / 'binding.json').exists()


@pytest.mark.asyncio
async def test_service_recovery_is_observational_and_cold_repeat_never_activates(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.storage.service import FileLiquidityService
    service, config, sources, spot, swap = await joint_service(tmp_path)
    try:
        await service.recover()
        first = await service.store.snapshot(service.store.binding)
        await service.recover()
        second = await service.store.snapshot(service.store.binding)
        assert first.balance == second.balance
        assert not service.controller.trading_permissions_ready()
    finally:
        await service.close()
    cold = await FileLiquidityService.open(config, connectors={'okx': spot, 'okx_perpetual': swap},
                                           sources=sources, market_data_provider=MagicMock())
    try:
        await cold.recover()
        assert cold.store._cold_unarmed and not cold.runtime._local_ready()
        assert await cold.plan() == []
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_service_joint_watchdog_has_one_bounded_cycle_and_closes_admission(tmp_path):
    from dataclasses import replace
    service, _, _, _, _ = await joint_service(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    original = service.sources.joint_collect

    async def collect():
        calls.append(1)
        entered.set()
        await release.wait()
        return await original()
    service.sources = replace(service.sources, joint_collect=collect)
    service.joint._recovery.collect = collect
    try:
        service.on_safety_tick(1)
        await asyncio.wait_for(entered.wait(), 2)
        assert service._cycle is service.controller.order_safety_task
        assert not service.runtime._local_ready()
        service.on_safety_tick(2)
        assert len(calls) == 1
        release.set()
        await service._cycle
        assert service.controller.order_safety_reason_code == 'JOINT_ORDERS_RECONCILED'
        assert not service.controller.trading_permissions_ready()
    finally:
        release.set()
        await service.close()


@pytest.mark.asyncio
async def test_actual_runner_dispatch_service_never_reads_native_journals_on_loop(tmp_path, monkeypatch):
    import threading

    from hummingbot.strategy_v2.life_liquidity.policy_state import PolicyState
    service, _, _, _, _ = await joint_service(tmp_path)
    original = PolicyState.locked
    loop = threading.get_ident()

    def worker_only(journal, *args, **kwargs):
        assert threading.get_ident() != loop, 'native file access on event loop'
        return original(journal, *args, **kwargs)
    try:
        await service.recover()
        service.controller.trading_permissions_ready = lambda: True  # Synthetic release only.
        service.connectors["okx_perpetual"].get_price_by_type = lambda *args, **kwargs: Decimal("1")
        with monkeypatch.context() as patches:
            patches.setattr(PolicyState, 'locked', worker_only)
            actions = await service.plan()
            assert len(actions) == 1
            service.runner._async_action_dispatcher.submit(actions)
            await service.runner._async_action_dispatcher.drain()
            children = service.runner.executor_orchestrator.active_executors['life']
            assert len(children) == 1
            assert children[0].config.id not in service.controller._runner_stop_revoked_ids
            assert not service.runtime._action_hashes
            from hummingbot.strategy_v2.models.executor_actions import StopExecutorAction
            stop = StopExecutorAction(controller_id='life', executor_id=children[0].config.id)
            service.runner._async_action_dispatcher.submit([stop, *actions])
            await service.runner._async_action_dispatcher.drain()
            assert stop.executor_id in service.controller._runner_stop_revoked_ids
            assert not service.swap.authorizes_config(actions[0].executor_config)
            assert await service.store._call(service.controller._shared_capital_authority.claim_ids) == (stop.executor_id,)
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['uid', 'timeout', 'stop'])
async def test_configured_joint_safety_failure_never_publishes_late_health(tmp_path, fault):
    service, _, _, spot, _ = await joint_service(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    original = service.joint._recovery.collect

    async def collect():
        entered.set()
        await release.wait()
        return await original()
    try:
        if fault == 'uid':
            async def foreign():
                return 'foreign'
            spot.get_account_uid = foreign
        else:
            service.joint._recovery.collect = collect
        service.on_safety_tick(1)
        if fault != 'uid':
            await asyncio.wait_for(entered.wait(), 2)
        if fault == 'stop':
            service.revoke()
            release.set()
        from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
        with pytest.raises(TimeoutError if fault == 'timeout' else StorageError):
            await service._cycle
        assert service._revoked and service.joint._revoked
        assert not service.runtime._local_ready()
        assert not service.joint._recovery._healthy and not service.swap.sender._runtime_enabled
        assert await service.plan() == []
        assert (await service.store.snapshot(service.store.binding)).balance.total_quote_usdt == Decimal('10')
    finally:
        release.set()
        await service.close()
        await service.close()  # Joins the same physical cleanup, never double-disposes.


@pytest.mark.asyncio
async def test_cancelled_close_still_owns_cleanup_and_account_lock_until_worker_finishes(tmp_path):
    import threading

    from hummingbot.strategy_v2.life_liquidity.storage.service import FileLiquidityService
    service, config, sources, spot, swap = await joint_service(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def blocked():
        entered.set()
        assert release.wait(5)
    worker = asyncio.create_task(service.store._call(blocked))
    while not entered.is_set():
        await asyncio.sleep(.001)
    closing = asyncio.create_task(service.close())
    try:
        await asyncio.sleep(.01)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert service._close_task is not None and not service._close_task.done()
        assert service._revoked
        assert not service.runtime._local_ready()
        from hummingbot.strategy_v2.life_liquidity.account_lock import AccountLockUnavailable
        with pytest.raises(AccountLockUnavailable, match='ACCOUNT_LOCK_HELD_ELSEWHERE'):
            await FileLiquidityService.open(config, connectors={'okx': spot, 'okx_perpetual': swap},
                                            sources=sources, market_data_provider=MagicMock())
    finally:
        release.set()
        await worker
        await service.close()


@pytest.mark.asyncio
async def test_yaml_startup_detaches_exact_settings_and_refuses_changed_binding(tmp_path):
    import json

    import yaml
    from pydantic import ValidationError

    from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    from hummingbot.strategy_v2.life_liquidity.storage.service import FileLiquidityService, ServiceSources
    from hummingbot.strategy_v2.life_liquidity.storage.spot_application import SpotAdmission
    config = await settings(tmp_path)
    source = tmp_path / 'service.yml'
    values = json.loads(config.model_dump_json())
    source.write_text(yaml.safe_dump(values))
    connector = OkxExchange('', '', '', trading_pairs=['LIFE-USDT'], trading_required=False)

    async def uid():
        return config.controller.recovery_account_uid
    connector.get_account_uid = uid
    sources = ServiceSources(clock=lambda: NOW, risk_epoch=lambda: 1, authorize_permit=lambda _: False,
                             spot_admission=lambda _: SpotAdmission(Decimal('1'), Decimal('.1'), Decimal('0'), Decimal('0')))
    service = await FileLiquidityService.open_yaml(source, connectors={'okx': connector}, sources=sources,
                                                   market_data_provider=MagicMock(), create_binding=True)
    try:
        assert service.config == config and service.config is not config
        assert await service.plan() == []
    finally:
        await service.close()
    values['controller']['storage_binding']['account_uid'] = 'foreign'
    source.write_text(yaml.safe_dump(values))
    with pytest.raises((StorageError, ValidationError)):
        await FileLiquidityService.open_yaml(source, connectors={'okx': connector}, sources=sources,
                                             market_data_provider=MagicMock())


@pytest.mark.asyncio
async def test_recovery_admission_is_bounded_and_cancellation_never_restores_permission(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, StorageUnavailable
    service, _, _, _, _ = await joint_service(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    original = service.joint._recovery.collect

    async def collect():
        entered.set()
        await release.wait()
        return await original()
    service.joint._recovery.collect = collect
    pending = asyncio.create_task(service.recover())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        with pytest.raises(StorageUnavailable, match='SERVICE_RECOVERY_BACKPRESSURE'):
            await service.recover()
        assert service._recovery_admitted == 1 and await service.plan() == []
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        release.set()
        assert service._recovery_admitted == 0 and service._revoked and service.joint._revoked
        with pytest.raises(StorageError, match='SERVICE_RECOVERY_REVOKED'):
            await service.recover()
    finally:
        release.set()
        await service.close()


@pytest.mark.asyncio
async def test_service_runner_fill_hint_is_detached_worker_only_and_never_books_money(tmp_path, monkeypatch):
    import threading

    from hummingbot.core.data_type.common import OrderType
    from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount
    from hummingbot.core.event.events import OrderFilledEvent
    from hummingbot.strategy_v2.life_liquidity.policy_state import PolicyState
    service, _, _, _, _ = await joint_service(tmp_path)
    original = PolicyState.locked
    loop = threading.get_ident()

    def worker_only(journal, *args, **kwargs):
        assert threading.get_ident() != loop, 'native runner event access on loop'
        return original(journal, *args, **kwargs)
    try:
        await service.recover()
        service.controller.trading_permissions_ready = lambda: True  # Synthetic release only.
        action, = await service.plan()
        command, _receipt, _ = service.swap._issued[action.executor_config.id]
        await service.swap.repository.arm(command.operation_id,
                                          cut=await service.swap.repository.cut(service.store.binding), owner=service.swap.owner)
        fee = AddedToCostTradeFee(flat_fees=[TokenAmount('USDT', Decimal('.01'))])
        event = OrderFilledEvent(NOW.timestamp(), command.client_order_id, 'LIFE-USDT', action.executor_config.side,
                                 OrderType.LIMIT_MAKER, action.executor_config.price, Decimal('.25'), fee, 'synthetic-trade', '101')
        with monkeypatch.context() as patches:
            patches.setattr(PolicyState, 'locked', worker_only)
            service.controller.on_runner_order_filled(event)
            fee.flat_fees[0].amount = Decimal('.99')
            assert not service.runtime._local_ready()
            await service.drain_runner_events()
        hint = await service.store._call(lambda: service.joint._recovery.journal._read()['runner_fills']['synthetic-trade'])
        assert hint[-1] == '-0.01'
        assert not service.runtime._local_ready() and await service.plan() == []
        assert not service.controller._runner_scope_invalid
        view = await service.store.snapshot(service.store.binding)
        assert (view.balance.total_base_life, view.balance.total_quote_usdt) == (Decimal('5'), Decimal('10'))
        assert view.budget.session_execution_loss_usdt == 0
        assert await service.store._call(service.controller._shared_capital_authority.claim_ids) == (action.executor_config.id,)
        from hummingbot.strategy_v2.life_liquidity.joint_recovery import RecoveryBundle, RecoveryOrder, RecoveryTrade
        capital = service.controller._shared_capital_authority
        claim = (await service.store._call(capital.claims))[action.executor_config.id]
        before = capital.observation()
        at = int(NOW.timestamp() * 1000)
        realized = Decimal('.25') * (action.executor_config.price - Decimal('1'))
        expected_cash = Decimal('10') + realized - Decimal('.01')
        observation = replace(before, sequence=2, long_base=Decimal('.75'), spot_cash_quote=expected_cash, pending=(claim,))
        account = replace(service.swap.sender.account_observation(), snapshot_sequence=2,
                          net_contracts=Decimal('.75') / service.config.swap_contract.contract_value_life)
        capital.observation = lambda: observation
        service.swap.sender.account_observation = lambda: account
        trade = RecoveryTrade('synthetic-trade', Decimal('.25'), action.executor_config.price, 'USDT', Decimal('-.01'), realized, at)
        proof = RecoveryOrder(claim, '101', 'partially_filled', (trade,))
        bundle = RecoveryBundle(service.store.binding.account_uid, at, at, 2, (proof,), (claim.wire_id,),
                                (), True, True, 'okx_joint_reconciled')

        async def collect():
            return bundle
        service.joint._recovery.collect = collect
        await service.recover()
        await service.recover()
        view = await service.store.snapshot(service.store.binding)
        assert view.balance.total_quote_usdt == expected_cash
        assert view.budget.session_execution_loss_usdt == Decimal('.01')
        assert await service.store._call(lambda: service.joint._recovery.journal._read()['runner_fills']) == {}
        assert service._qualified_hint_generation == service._hint_generation
        assert await service.store._call(capital.claim_ids) == (action.executor_config.id,)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_service_runner_cancel_hint_preserves_claim_and_requires_independent_source(tmp_path, monkeypatch):
    import threading

    from hummingbot.core.event.events import OrderCancelledEvent
    from hummingbot.strategy_v2.life_liquidity.policy_state import PolicyState
    service, _, _, _, _ = await joint_service(tmp_path)
    original = PolicyState.locked
    loop = threading.get_ident()

    def worker_only(journal, *args, **kwargs):
        assert threading.get_ident() != loop
        return original(journal, *args, **kwargs)
    try:
        await service.recover()
        service.controller.trading_permissions_ready = lambda: True  # Synthetic release only.
        action, = await service.plan()
        command, _, _ = service.swap._issued[action.executor_config.id]
        await service.swap.repository.arm(command.operation_id,
                                          cut=await service.swap.repository.cut(service.store.binding), owner=service.swap.owner)
        event = OrderCancelledEvent(NOW.timestamp(), command.client_order_id, '101')
        with monkeypatch.context() as patches:
            patches.setattr(PolicyState, 'locked', worker_only)
            service.controller.on_runner_order_canceled(event)
            event.exchange_order_id = 'mutated'
            await service.drain_runner_events()
        hint = await service.store._call(lambda: service.joint._recovery.journal._read()['runner_cancels'][action.executor_config.id])
        assert hint == '101'
        assert await service.store._call(service.controller._shared_capital_authority.claim_ids) == (action.executor_config.id,)
        assert not service.runtime._local_ready()
        assert await service.plan() == []
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_service_runner_hint_backpressure_revokes_without_unbounded_queue(tmp_path):
    import threading

    from hummingbot.core.event.events import OrderCancelledEvent
    service, _, _, _, _ = await joint_service(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def blocked():
        entered.set()
        assert release.wait(5)
    worker = asyncio.create_task(service.store._call(blocked))
    while not entered.is_set():
        await asyncio.sleep(.001)
    try:
        service.controller.on_runner_order_canceled(OrderCancelledEvent(NOW.timestamp(), 'wire1', '101'))
        assert len(service._pending_events) == 1
        service.controller.on_runner_order_canceled(OrderCancelledEvent(NOW.timestamp(), 'wire2', '102'))
        assert len(service._pending_events) == 1 and service._revoked and service.controller._runner_scope_invalid
        assert await service.plan() == []
    finally:
        release.set()
        await worker
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_stage", ["none", "throttler", "ack"])
async def test_configured_service_actual_runner_executor_and_rest_ack_share_repository(tmp_path, stop_stage):
    from test.hummingbot.strategy_v2.life_liquidity.test_protected_swap_send import connector_with_transport
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from hummingbot.connector.trading_rule import TradingRule
    from hummingbot.core.web_assistant.connections.rest_connection import RESTConnection
    from hummingbot.core.web_assistant.rest_assistant import RESTAssistant
    service, _, _, _, connector = await joint_service(tmp_path)
    transport, throttle, sent = connector_with_transport({'data': [{'sCode': '0', 'ordId': '101'}]})
    connector._web_assistants_factory = transport._web_assistants_factory
    connector._api_request_url = transport._api_request_url
    connector.exchange_symbol_associated_to_pair = transport.exchange_symbol_associated_to_pair
    connector.get_price_by_type = lambda *args, **kwargs: Decimal('1')
    connector._account_balances = connector._account_available_balances = {'USDT': Decimal('100')}
    connector._trading_rules['LIFE-USDT'] = TradingRule('LIFE-USDT', min_order_size=Decimal('.25'),
                                                        min_price_increment=Decimal('.001'), min_base_amount_increment=Decimal('.25'),
                                                        buy_order_collateral_token='USDT', sell_order_collateral_token='USDT')
    response_entered, response_release = asyncio.Event(), asyncio.Event()
    if stop_stage == 'ack':
        class Response:
            status = 200
            content_type = 'application/json'

            async def json(self):
                response_entered.set()
                await response_release.wait()
                return {'data': [{'sCode': '0', 'ordId': '101'}]}

        class Session:
            async def request(self, **kwargs):
                sent.append(kwargs)
                return Response()
        assistant = RESTAssistant(connection=RESTConnection(Session()), throttler=throttle)
        connector._web_assistants_factory = SimpleNamespace(get_rest_assistant=AsyncMock(return_value=assistant))
    if stop_stage != 'throttler':
        throttle.release.set()
    connector._set_current_timestamp(NOW.timestamp())
    service.runner._set_current_timestamp(NOW.timestamp())
    try:
        await service.recover()
        service.controller.trading_permissions_ready = lambda: True  # Synthetic release only.
        action, = await service.plan()
        service.runner._async_action_dispatcher.submit([action])
        await service.runner._async_action_dispatcher.drain()
        if stop_stage == 'throttler':
            await asyncio.wait_for(throttle.entered.wait(), 3)
            service.revoke()
            throttle.release.set()
            await service.swap.drain()
            await service.reporting.drain()
            assert sent == [] and action.executor_config.id not in service.swap._acknowledged_ids
            assert (await service.store._call(service.swap.sender.wal.get, action.executor_config.id)).state == 'SEND_UNKNOWN'
            assert await service.store._call(service.controller._shared_capital_authority.claim_ids) == (action.executor_config.id,)
            return

        if stop_stage == 'ack':
            await asyncio.wait_for(response_entered.wait(), 3)
            service.revoke()
            response_release.set()

        async def acked():
            while action.executor_config.id not in service.swap._acknowledged_ids:
                failure = service.swap.failure_reason(action.executor_config.id)
                assert failure is None, failure
                await asyncio.sleep(.001)
        await asyncio.wait_for(acked(), 3)
        await service.swap.drain()
        await service.reporting.drain()
        assert (await service.store._call(service.swap.sender.wal.get, action.executor_config.id)).state == 'ACKED'
        assert len(sent) == 1
        assert await service.store._call(service.controller._shared_capital_authority.claim_ids) == (action.executor_config.id,)
        assert (await service.store.snapshot(service.store.binding)).balance.total_quote_usdt == Decimal('10')
        child = service.runner.executor_orchestrator.active_executors['life'][0]
        assert child._order.order_id == service.swap._issued[action.executor_config.id][0].client_order_id
        service.revoke()
        assert not service.swap.authorizes_config(action.executor_config)
    finally:
        throttle.release.set()
        response_release.set()
        await service.close()
