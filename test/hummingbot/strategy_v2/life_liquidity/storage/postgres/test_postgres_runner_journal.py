"""Actual awaited V2 dispatch with PostgreSQL; no exchange transport authority."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_action_preparation import bridge, command
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.executors.order_executor.data_types import OrderExecutorConfig
from hummingbot.strategy_v2.life_liquidity.storage.actions import RunnerActionFacts
from hummingbot.strategy_v2.life_liquidity.storage.async_dispatch import AsyncActionDispatcher
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runner_journal import PgQuoteRunnerJournal
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, StopExecutorAction


async def assembled(database, **seed_options):
    repo, cfg, owner, row = await bridge(database, claim=False, **seed_options)
    active = set()
    ready = [True]

    async def capture():
        return RunnerActionFacts(cfg.binding, await database[0].fetchval('SELECT clock_timestamp()'),
                                 row.controller_id, tuple(sorted(active)), True)

    app = PgQuoteRunnerJournal(repo, owner=owner, controller_id=row.controller_id, config_version=1, runner_facts=capture,
                               local_ready=lambda: ready[0], clock=lambda: datetime.now(timezone.utc), max_actions=8)
    create = CreateExecutorAction(controller_id=row.controller_id,
                                  executor_config=OrderExecutorConfig.model_validate_json(row.payload_json))
    runner = SimpleNamespace(controllers={row.controller_id: app}, _is_stop_triggered=False,
                             executor_orchestrator=MagicMock(), logger=lambda: MagicMock())

    def execute(action):
        if isinstance(action, CreateExecutorAction):
            active.add(action.executor_config.id)
        elif isinstance(action, StopExecutorAction):
            active.discard(action.executor_id)
    runner.executor_orchestrator.execute_action.side_effect = execute
    runner._filter_authorized_actions = lambda actions: StrategyV2Base._filter_authorized_actions(runner, actions)
    dispatch = AsyncActionDispatcher(runner, max_pending=2, shutdown_timeout_s=.05)
    return repo, cfg, owner, row, app, create, runner, dispatch, active, ready


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['copied', 'task', 'operation', 'ingress'])
async def test_publication_preflight_cut_is_original_one_use_and_task_bound(database, fault):
    repo, cfg, _, _, app, create, _, dispatch, _, _ = await assembled(database)
    try:
        before = await repo.balance(cfg.binding)
        ticket = await app.preflight_publication('new-claim')
        if fault == 'ingress':
            repo._runtime._ingress_epoch += 1
        operation = 'other' if fault == 'operation' else 'new-claim'
        token = object() if fault == 'copied' else ticket
        with pytest.raises(StorageError, match='PREFLIGHT_ORIGIN_REQUIRED'):
            work = app.publish(operation, (create,), _publication=token)
            await (asyncio.create_task(work) if fault == 'task' else work)
        with pytest.raises(StorageError, match='PREFLIGHT_ORIGIN_REQUIRED'):
            await app.publish('new-claim', (create,), _publication=ticket)
        assert not await repo.quote_actions(cfg.binding)
        assert not app._entries and await repo.balance(cfg.binding) == before
    finally:
        await dispatch.close()
        await repo.close()


@pytest.mark.asyncio
async def test_new_publication_preflight_reads_its_original_cut_on_the_command_pool(database):
    repo, _, _, _, app, create, _, dispatch, _, _ = await assembled(database)
    try:
        ticket = await app.preflight_publication('new-claim')
        read = next(d for d in reversed(repo._runtime.diagnostics) if d.operation == 'spot_observation_cut')
        assert read.safety is False
        await app.publish('new-claim', (create,), _publication=ticket)
        claim = next(d for d in reversed(repo._runtime.diagnostics) if d.operation == 'spot_action_command')
        assert claim.backend_pid == read.backend_pid
    finally:
        await dispatch.close()
        await repo.close()


@pytest.mark.asyncio
async def test_publication_and_acceptance_need_verified_cuts_without_materializing_wallet_budget_views(database, monkeypatch):
    repo, cfg, owner, row, app, create, runner, dispatch, active, _ = await assembled(database)

    async def unwanted(*args, **kwargs):
        raise AssertionError('runner does not need a wallet/budget projection')
    monkeypatch.setattr(repo, 'snapshot', unwanted)
    try:
        await app.publish('compact-claim', (create,))
        active.add(row.intent_id)
        assert await app.async_on_runner_create_action_dispatched(create)
        assert (await repo.quote_actions(cfg.binding))[0].state == 'DISPATCHED'
        assert (await repo.observation_cut(cfg.binding)).cut.checkpoint.revision > 0
    finally:
        await dispatch.close()
        await repo.close()


@pytest.mark.asyncio
async def test_single_claim_dispatch_verifies_current_state_once_without_a_separate_cut_read(database, monkeypatch):
    repo, cfg, _, row, app, create, _, dispatch, active, _ = await assembled(database)
    try:
        await app.publish('single-claim', (create,))
        before = await repo.balance(cfg.binding)

        async def redundant_cut(*args, **kwargs):
            raise AssertionError('dispatch must capture its cut inside the metadata transaction')

        monkeypatch.setattr(repo, 'observation_cut', redundant_cut)
        active.add(row.intent_id)
        assert await app.async_on_runner_create_action_dispatched(create)
        assert (await repo.quote_actions(cfg.binding))[0].state == 'DISPATCHED'
        assert await repo.balance(cfg.binding) == before
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
    finally:
        await dispatch.close()
        await repo.close()


@pytest.mark.asyncio
async def test_single_claim_dispatch_refuses_an_intervening_account_writer(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_session import tick
    repo, cfg, owner, row, app, create, _, dispatch, active, _ = await assembled(database)
    try:
        await app.publish('single-claim', (create,))
        await repo.tick_session(tick(owner),
                                cut=(await repo.observation_cut(cfg.binding)).cut, owner=owner)
        active.add(row.intent_id)
        with pytest.raises(StorageError, match='EXPECTED_VERSION_CONFLICT'):
            await app.async_on_runner_create_action_dispatched(create)
        assert (await repo.quote_actions(cfg.binding))[0].state == 'PROPOSED'
        assert not app.local_runner_create_fence(create)
        assert await repo.attempt(row.intent_id, cfg.binding) is None
    finally:
        await dispatch.close()
        await repo.close()


@pytest.mark.asyncio
async def test_atomic_dispatch_private_view_is_not_a_runtime_or_reporter_capability(database):
    repo, _, _, _, _, _, _, dispatch, _, _ = await assembled(database)
    signature = 'life_financial.spot_action_with_verified_view(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb,jsonb)'
    public = 'life_financial.spot_dispatch_claimed_action(uuid,jsonb,jsonb,bigint,life_financial.digest,jsonb)'
    try:
        for principal in ('life_pg2_runtime', 'life_pg2_reporter'):
            assert not await database[0].fetchval('SELECT has_function_privilege($1,$2,\'EXECUTE\')', principal, signature)
            assert await database[0].fetchval('SELECT has_function_privilege($1,$2,\'EXECUTE\')', principal, public) is (principal == 'life_pg2_runtime')
    finally:
        await dispatch.close()
        await repo.close()


@pytest.mark.asyncio
async def test_atomic_dispatch_detects_same_version_stored_action_corruption(database):
    repo, cfg, _, row, app, create, _, dispatch, active, _ = await assembled(database)
    try:
        await app.publish('single-claim', (create,))
        c = database[0]
        await c.execute('ALTER TABLE life_financial.spot_action_descriptors DISABLE TRIGGER USER')
        await c.execute("UPDATE life_financial.spot_action_descriptors SET descriptor=descriptor||'{\"level\":1}'::jsonb")
        await c.execute('ALTER TABLE life_financial.spot_action_descriptors ENABLE TRIGGER USER')
        active.add(row.intent_id)
        with pytest.raises(StorageError, match='UNVERIFIED'):
            await app.async_on_runner_create_action_dispatched(create)
        assert not app.local_runner_create_fence(create)
        assert await c.fetchval("SELECT state FROM life_financial.quote_actions WHERE action_id=$1", row.intent_id) == 'PROPOSED'
        assert await c.fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
    finally:
        await dispatch.close()
        await repo.close()


@pytest.mark.asyncio
async def test_atomic_dispatch_rechecks_generation_at_physical_commit(database, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import install_generation_fault
    repo, cfg, _, row, app, create, _, dispatch, active, _ = await assembled(database)
    try:
        await app.publish('single-claim', (create,))
        await install_generation_fault(database)
        original = repo._runtime._commit

        async def changed(transaction):
            await transaction._connection.execute('SELECT life_financial.test_change_generation($1)', cfg.account_id)
            await original(transaction)

        monkeypatch.setattr(repo._runtime, '_commit', changed)
        active.add(row.intent_id)
        with pytest.raises(StorageError):
            await app.async_on_runner_create_action_dispatched(create)
        assert not app.local_runner_create_fence(create)
        assert await database[0].fetchval("SELECT state FROM life_financial.quote_actions WHERE action_id=$1", row.intent_id) == 'PROPOSED'
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
    finally:
        await dispatch.close()
        await repo.close()


@pytest.mark.asyncio
async def test_actual_dispatch_waits_for_persisted_acceptance_before_financial_preparation(database, monkeypatch):
    repo, cfg, owner, row, app, create, runner, dispatch, active, _ = await assembled(database)
    entered, release = asyncio.Event(), asyncio.Event()
    original = repo.transition_quote_action

    async def held(cmd, **kwargs):
        entered.set()
        await release.wait()
        return await original(cmd, **kwargs)
    monkeypatch.setattr(repo, 'transition_quote_action', held)
    try:
        await app.publish('claim', (create,))
        dispatch.submit([create])
        await asyncio.wait_for(entered.wait(), 3)
        assert active == {'intent1'} and dispatch.pending_count == 1
        cmd = await command(database, cfg, owner, row)
        with pytest.raises(StorageError, match='RUNNER_ACCEPTANCE_REQUIRED'):
            await app.prepare_quote_action(create, cmd)
        release.set()
        await dispatch.drain()
        assert (await repo.quote_actions(cfg.binding))[0].state == 'DISPATCHED'
        result = await app.prepare_quote_action(create, await command(database, cfg, owner, row))
        assert result.state == 'PREPARED'
        assert (await repo.balance(cfg.binding)).reserved_quote_usdt == cmd.prepare.quantity_base * cmd.prepare.limit_price_usdt + cmd.prepare.fee_buffer_quote_usdt
        assert (await repo.attempt('intent1', cfg.binding)).state.value == 'PREPARED'
        # Neither runner acceptance nor financial preparation arms or sends.
        assert await repo.lookup_prepare('arm:operation1', cfg.binding) is None
    finally:
        release.set()
        await dispatch.close()
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['before', 'after'])
async def test_stop_at_dispatch_boundary_never_grants_preparation_and_reconciles_only_absent_runner(database, phase, monkeypatch):
    repo, cfg, owner, row, app, create, runner, dispatch, active, ready = await assembled(database)
    entered, release = asyncio.Event(), asyncio.Event()
    method = 'async_authorize_runner_create_action' if phase == 'before' else 'async_on_runner_create_action_dispatched'
    original = getattr(app, method)

    async def held(action):
        entered.set()
        await release.wait()
        return await original(action)
    monkeypatch.setattr(app, method, held)
    try:
        await app.publish('claim', (create,))
        dispatch.submit([create])
        await asyncio.wait_for(entered.wait(), 3)
        dispatch.submit([StopExecutorAction(controller_id=row.controller_id, executor_id=row.intent_id)])
        release.set()
        await dispatch.drain()
        assert active == set()
        if phase == 'after':
            assert (await repo.quote_actions(cfg.binding))[0].state == 'PROPOSED'
            await app.reconcile(create)
        assert (await repo.quote_actions(cfg.binding))[0].state == 'REJECTED'
        with pytest.raises(StorageError, match='RUNNER_ACCEPTANCE_REQUIRED'):
            await app.prepare_quote_action(create, await command(database, cfg, owner, row))
        assert await repo.attempt('intent1', cfg.binding) is None
    finally:
        release.set()
        await dispatch.close()
        await repo.close()


@pytest.mark.asyncio
async def test_lost_dispatch_reply_keeps_live_claim_and_cold_process_cannot_adopt(database, monkeypatch):
    repo, cfg, owner, row, app, create, runner, dispatch, active, ready = await assembled(database)
    original = repo.transition_quote_action

    async def lost(cmd, **kwargs):
        receipt = await original(cmd, **kwargs)
        if cmd.state == 'DISPATCHED':
            raise OSError('reply lost after commit')
        return receipt
    monkeypatch.setattr(repo, 'transition_quote_action', lost)
    try:
        await app.publish('claim', (create,))
        dispatch.submit([create])
        await dispatch.drain()
        assert active == {'intent1'}
        assert (await repo.quote_actions(cfg.binding))[0].state == 'DISPATCHED'
        with pytest.raises(StorageError, match='RUNNER_ACCEPTANCE_REQUIRED'):
            await app.prepare_quote_action(create, await command(database, cfg, owner, row))
        cold = PgQuoteRunnerJournal(repo, owner=owner, controller_id=row.controller_id, config_version=1, runner_facts=app.runner_facts,
                                    local_ready=lambda: True, clock=app.clock, max_actions=8)
        with pytest.raises(StorageError, match='RUNNER_CLAIM_REPLAY_REQUIRES_RECOVERY'):
            await cold.publish('claim', (create,))
        assert not cold.local_runner_create_fence(create)
        active.clear()
        await app.reconcile(create)
        assert (await repo.quote_actions(cfg.binding))[0].state == 'REJECTED'
        assert await repo.attempt('intent1', cfg.binding) is None
    finally:
        await dispatch.close()
        await repo.close()


@pytest.mark.asyncio
async def test_cancelled_acceptance_keeps_unresolved_claim_without_async_cleanup(database, monkeypatch):
    repo, cfg, owner, row, app, create, runner, dispatch, active, _ = await assembled(database)
    entered = asyncio.Event()

    async def waiting(*args, **kwargs):
        entered.set()
        await asyncio.Future()
    monkeypatch.setattr(repo, 'transition_quote_action', waiting)
    try:
        await app.publish('claim', (create,))
        dispatch.submit([create])
        await asyncio.wait_for(entered.wait(), 3)
        await dispatch.close()
        await dispatch.drain()
        assert (await repo.quote_actions(cfg.binding))[0].state == 'PROPOSED'
        assert active == {'intent1'} and not app.local_runner_create_fence(create)
        assert await repo.attempt('intent1', cfg.binding) is None
    finally:
        await dispatch.close()
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('amount', '3'), ('controller_id', 'other'), ('level_id', '1')])
async def test_mutated_runner_action_cannot_dispatch_or_prepare(database, field, value):
    from decimal import Decimal
    repo, cfg, owner, row, app, create, runner, dispatch, active, _ = await assembled(database)
    try:
        await app.publish('claim', (create,))
        setattr(create.executor_config, field, Decimal(value) if field == 'amount' else value)
        dispatch.submit([create])
        await dispatch.drain()
        assert not active and await repo.attempt('intent1', cfg.binding) is None
        assert not app.local_runner_create_fence(create)
    finally:
        await dispatch.close()
        await repo.close()


@pytest.mark.asyncio
async def test_live_prepared_action_reconciliation_does_not_refund_and_closed_action_can_retire(database):
    from hummingbot.strategy_v2.life_liquidity.storage.spot_lifecycle import SpotLifecycleCommand
    repo, cfg, owner, row, app, create, runner, dispatch, active, _ = await assembled(database)
    try:
        await app.publish('claim', (create,))
        dispatch.submit([create])
        await dispatch.drain()
        await app.prepare_quote_action(create, await command(database, cfg, owner, row))
        active.clear()
        with pytest.raises(StorageError, match='ACTION_RECONCILIATION_UNPROVEN'):
            await app.reconcile(create)
        assert (await repo.balance(cfg.binding)).reserved_quote_usdt > 0
        await repo.apply_lifecycle(SpotLifecycleCommand('abort', 'operation1', 'intent1', 'wire1', 'ABORT_PREPARED'),
                                   cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await app.reconcile(create)
        assert (await repo.quote_actions(cfg.binding))[0].state == 'RECONCILED'
        assert not app.local_runner_create_fence(create)
    finally:
        await dispatch.close()
        await repo.close()


@pytest.mark.asyncio
async def test_real_orchestrator_registers_real_order_executor_before_journal_commit(database, monkeypatch):
    from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator
    from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
    repo, cfg, owner, row, app, create, runner, dispatch, _, _ = await assembled(database)
    # Actual construction/registration with the autonomous control loop held.
    # A fake connector catches legacy fallback; no HTTP client is installed.
    monkeypatch.setattr(OrderExecutor, 'start', lambda self: None)
    runner.connectors = {'okx': MagicMock()}
    runner.connectors['okx'].get_price_by_type.return_value = create.executor_config.price
    runner.buy, runner.sell = MagicMock(return_value='unsafeBuy'), MagicMock(return_value='unsafeSell')
    orchestrator = ExecutorOrchestrator.__new__(ExecutorOrchestrator)
    orchestrator.strategy = runner
    orchestrator.active_executors, orchestrator.positions_held, orchestrator.cached_performance = {}, {}, {}
    orchestrator.executors_update_interval, orchestrator.executors_max_retries = 1., 1
    orchestrator._repository_reporting = None
    runner.executor_orchestrator = orchestrator

    async def capture():
        return RunnerActionFacts(cfg.binding, await database[0].fetchval('SELECT clock_timestamp()'), row.controller_id,
                                 tuple(v.config.id for v in orchestrator.active_executors.get(row.controller_id, [])), True)
    app.runner_facts = capture
    try:
        await app.publish('claim', (create,))
        dispatch.submit([create])
        await dispatch.drain()
        executor, = orchestrator.active_executors[row.controller_id]
        assert type(executor) is OrderExecutor and executor._order is None
        with pytest.raises(StorageError, match='PG_RUNNER_TRANSPORT_ASSEMBLY_REQUIRED'):
            executor.place_open_order()
        assert (await repo.quote_actions(cfg.binding))[0].state == 'DISPATCHED'
        await app.prepare_quote_action(create, await command(database, cfg, owner, row))
        with pytest.raises(StorageError, match='PG_RUNNER_TRANSPORT_ASSEMBLY_REQUIRED'):
            executor.place_open_order()
        runner.buy.assert_not_called()
        runner.sell.assert_not_called()
        assert executor._order is None and (await repo.attempt('intent1', cfg.binding)).state.value == 'PREPARED'
    finally:
        await dispatch.close()
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['incomplete', 'foreign', 'missing', 'stale'])
async def test_dispatch_requires_complete_fresh_original_runner_scope(database, case):
    from datetime import timedelta
    repo, cfg, owner, row, app, create, runner, dispatch, active, _ = await assembled(database)
    capture = app.runner_facts

    async def invalid():
        facts = await capture()
        if case == 'incomplete':
            return replace(facts, scope_complete=False)
        if case == 'foreign':
            return replace(facts, controller_id='other')
        if case == 'missing':
            return replace(facts, executor_ids=())
        return replace(facts, observed_at=facts.observed_at - timedelta(seconds=2))
    app.runner_facts = invalid
    try:
        await app.publish('claim', (create,))
        dispatch.submit([create])
        await dispatch.drain()
        assert active == {'intent1'}
        assert (await repo.quote_actions(cfg.binding))[0].state == 'PROPOSED'
        with pytest.raises(StorageError, match='RUNNER_ACCEPTANCE_REQUIRED'):
            await app.prepare_quote_action(create, await command(database, cfg, owner, row))
        assert await repo.attempt('intent1', cfg.binding) is None
    finally:
        await dispatch.close()
        await repo.close()


@pytest.mark.asyncio
async def test_one_shot_preparation_cannot_retry_ambiguous_commit_with_new_id(database, monkeypatch):
    repo, cfg, owner, row, app, create, runner, dispatch, active, _ = await assembled(database)
    original = repo.prepare_quote_action

    async def lost(cmd, **kwargs):
        await original(cmd, **kwargs)
        raise OSError('prepare reply lost')
    monkeypatch.setattr(repo, 'prepare_quote_action', lost)
    try:
        await app.publish('claim', (create,))
        dispatch.submit([create])
        await dispatch.drain()
        cmd = await command(database, cfg, owner, row)
        with pytest.raises(OSError, match='prepare reply lost'):
            await app.prepare_quote_action(create, cmd)
        with pytest.raises(StorageError, match='RUNNER_ACCEPTANCE_REQUIRED'):
            await app.prepare_quote_action(create, replace(cmd, prepare=replace(cmd.prepare, operation_id='newop')))
        assert (await repo.lookup_prepare('operation1', cfg.binding)).state == 'PREPARED'
        assert await repo.lookup_prepare('newop', cfg.binding) is None
        assert (await repo.balance(cfg.binding)).reserved_quote_usdt == cmd.prepare.quantity_base + cmd.prepare.fee_buffer_quote_usdt
    finally:
        await dispatch.close()
        await repo.close()


@pytest.mark.asyncio
async def test_creation_exception_after_registration_keeps_claim_for_explicit_reconciliation(database):
    repo, cfg, owner, row, app, create, runner, dispatch, active, _ = await assembled(database)

    def failed(action):
        active.add(action.executor_config.id)
        raise OSError('registration reply lost')
    runner.executor_orchestrator.execute_action.side_effect = failed
    try:
        await app.publish('claim', (create,))
        dispatch.submit([create])
        await dispatch.drain()
        assert (await repo.quote_actions(cfg.binding))[0].state == 'PROPOSED'
        assert not app.local_runner_create_fence(create)
        with pytest.raises(StorageError, match='RUNNER_EXECUTOR_STILL_PRESENT'):
            await app.reconcile(create)
        active.clear()
        await app.reconcile(create)
        assert (await repo.quote_actions(cfg.binding))[0].state == 'REJECTED'
        assert await repo.attempt('intent1', cfg.binding) is None
    finally:
        await dispatch.close()
        await repo.close()


@pytest.mark.asyncio
async def test_journal_capacity_and_same_batch_stop_keep_unqueued_claims_without_detached_tasks(database):
    repo, cfg, owner, row, app, create, runner, dispatch, active, _ = await assembled(database)
    app.max_actions = 1
    try:
        await app.publish('claim', (create,))
        another = create.model_copy(deep=True)
        another.executor_config.id = 'another'
        with pytest.raises(StorageError, match='RUNNER_JOURNAL_CAPACITY'):
            await app.publish('anotherclaim', (another,))
        dispatch.submit([create, StopExecutorAction(controller_id=row.controller_id, executor_id=row.intent_id)])
        await dispatch.drain()
        assert not active and dispatch.pending_count == 0
        assert (await repo.quote_actions(cfg.binding))[0].state == 'PROPOSED'
        assert len(await repo.quote_actions(cfg.binding)) == 1
        await app.reconcile(create)
        assert (await repo.quote_actions(cfg.binding))[0].state == 'REJECTED'
    finally:
        await dispatch.close()
        await repo.close()


@pytest.mark.asyncio
async def test_local_policy_revocation_during_claim_commit_registers_no_dispatch_permission(database, monkeypatch):
    repo, cfg, owner, row, app, create, runner, dispatch, active, ready = await assembled(database)
    original = repo.claim_quote_actions

    async def revoked(cmd, **kwargs):
        result = await original(cmd, **kwargs)
        ready[0] = False
        return result
    monkeypatch.setattr(repo, 'claim_quote_actions', revoked)
    try:
        await app.publish('claim', (create,))
        assert not app.local_runner_create_fence(create)
        dispatch.submit([create])
        await dispatch.drain()
        assert not active and await repo.attempt('intent1', cfg.binding) is None
        assert (await repo.quote_actions(cfg.binding))[0].state == 'REJECTED'
    finally:
        await dispatch.close()
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('timestamp', float('nan')), ('timestamp', None), ('amount', None),
                                         ('leverage', True), ('price', 1), ('type', 'position_executor')])
async def test_invalid_mutable_executor_fields_allocate_no_claim(database, field, value):
    repo, cfg, owner, row, app, create, runner, dispatch, active, _ = await assembled(database)
    try:
        setattr(create.executor_config, field, value)
        with pytest.raises(StorageError, match='RUNNER_QUOTE_CONFIG_INVALID|SPOT_DECIMAL_INVALID'):
            await app.publish('claim', (create,))
        assert await repo.quote_actions(cfg.binding) == ()
        assert not active and await repo.attempt('intent1', cfg.binding) is None
    finally:
        await dispatch.close()
        await repo.close()


@pytest.mark.asyncio
async def test_runner_bound_preparation_can_arm_exact_wire_once_but_submit_remains_denied(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_quote_arm import wire

    from hummingbot.strategy_v2.life_liquidity.storage.quote_send import ArmQuoteAction
    repo, cfg, owner, row, app, create, runner, dispatch, active, _ = await assembled(database)
    try:
        await app.publish('claim', (create,))
        dispatch.submit([create])
        await dispatch.drain()
        with pytest.raises(StorageError, match='RUNNER_PREPARATION_REQUIRED'):
            cmd = await command(database, cfg, owner, row)
            await app.arm_quote_action(create, ArmQuoteAction('operation1', row, cmd.admission, wire(row)))
        await app.prepare_quote_action(create, await command(database, cfg, owner, row))
        cmd = await command(database, cfg, owner, row)
        bound = ArmQuoteAction('operation1', row, cmd.admission, wire(row))
        result = await app.arm_quote_action(create, bound)
        assert result.state == 'SEND_UNKNOWN' and not result.trading_permission
        with pytest.raises(StorageError, match='RUNNER_PREPARATION_REQUIRED'):
            await app.arm_quote_action(create, bound)
        with pytest.raises(StorageError, match='PG_RUNNER_TRANSPORT_ASSEMBLY_REQUIRED'):
            app.submit_executor_spot_order(create.executor_config)
        assert (await repo.attempt('intent1', cfg.binding)).state.value == 'SEND_UNKNOWN'
    finally:
        await dispatch.close()
        await repo.close()
