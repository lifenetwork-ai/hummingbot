"""Actual V2 recorder callbacks over PostgreSQL; explicit offline assembly."""
import asyncio
from dataclasses import asdict, replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_positions import held
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_quote_transport import transport
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from controllers.generic.life_liquidity import LifeLiquidityConfig
from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator
from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.postgres.recorder_application import PgRecorderApplication
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import EventSource
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import StoreExecutorAction


async def setup(database, monkeypatch, *, max_pending=2, max_callbacks=2):
    template = LifeLiquidityConfig(id='controller1', storage_backend='postgres')
    source = EventSource(template.id, 'life.yml', 'strategy_v2', 'okx')
    t = await transport(database, config_hash=fingerprint(template.strategy.model_dump(mode='json')),
                        configuration_body=lambda binding: {
                            'controller': template.model_copy(update={'storage_binding': binding, 'recovery_account_uid': binding.account_uid}).model_dump(mode='json'),
                            'recorder_sources': [asdict(source)]})
    await t.repo.close()
    cfg = replace(t.cfg, safety_size=2)
    financial = PgSpotRepository(await PgRuntime.open(cfg))
    controller = SimpleNamespace(config=template.model_copy(update={'storage_binding': cfg.binding, 'recovery_account_uid': cfg.binding.account_uid}))
    revoked = []
    ready = [True]
    kwargs = dict(controller=controller, owner=t.owner, sources=(source,), revoke=lambda: revoked.append(True),
                  local_ready=lambda: ready[0], max_pending=max_pending, max_callbacks=max_callbacks,
                  shutdown_timeout_s=2, max_rows=64, max_read_bytes=1048576)
    app = await PgRecorderApplication.install(financial, **kwargs)
    monkeypatch.setattr(OrderExecutor, 'start', lambda _: None)
    strategy = SimpleNamespace(controllers={controller.config.id: controller}, connectors={'okx': MagicMock()},
                               buy=MagicMock(), sell=MagicMock(), current_timestamp=t.create.executor_config.timestamp,
                               market_data_provider=SimpleNamespace(get_price_by_type=lambda *args: D('1')))
    strategy.connectors['okx'].get_price_by_type.return_value = t.create.executor_config.price
    runner = ExecutorOrchestrator(strategy, repository_reporting={controller.config.id: app})
    return SimpleNamespace(**locals())


async def close(s, *, failed=False):
    try:
        if failed:
            with pytest.raises(StorageError):
                await s.app.close()
        else:
            await s.app.close()
    finally:
        await s.financial.close()
        await s.t.close()


@pytest.mark.asyncio
async def test_actual_executor_waits_for_receipt_then_cold_orchestrator_reads_without_resend(database, monkeypatch):
    s = await setup(database, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    original = s.app.repository.apply
    try:
        assert await s.app.before_dispatch(s.t.create)
        s.runner.execute_action(s.t.create)
        await s.app.drain()
        child, = s.runner.active_executors['controller1']
        assert type(child) is OrderExecutor
        child._status = RunnableStatus.TERMINATED

        async def blocked(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)
        monkeypatch.setattr(s.app.repository, 'apply', blocked)
        s.runner.store_executor(StoreExecutorAction(controller_id='controller1', executor_id=child.config.id))
        await asyncio.wait_for(entered.wait(), 3)
        assert child in s.runner.active_executors['controller1']
        assert len(await s.app.repository.pending(s.cfg.binding)) == 1
        release.set()
        await s.runner.drain_repository_reporting()
        assert s.runner.active_executors['controller1'] == []
        infos = await s.runner.async_get_stored_executors_by_controller('controller1')
        assert len(infos) == 1 and infos[0].id == child.config.id
        with pytest.raises(StorageError, match='PG_RECORDER_ASYNC_READ_REQUIRED'):
            s.runner.get_stored_executors_by_controller('controller1')
        cold = await PgRecorderApplication.install(s.financial, **s.kwargs)
        restored = ExecutorOrchestrator(s.strategy, repository_reporting={'controller1': cold})
        assert restored.active_executors['controller1'] == []
        assert len(cold.startup_infos) == 1
        await cold.close()
        s.strategy.buy.assert_not_called()
        s.strategy.sell.assert_not_called()
        assert not s.t.session.sent
    finally:
        release.set()
        await close(s)


@pytest.mark.asyncio
async def test_actual_position_callback_preserves_new_exposure_during_database_wait(database, monkeypatch):
    s = await setup(database, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    original = s.app.position_repository.apply
    position = held().restore()
    s.runner.positions_held['controller1'] = [position]
    try:
        async def blocked(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)
        monkeypatch.setattr(s.app.position_repository, 'apply', blocked)
        s.runner.store_all_positions()
        await asyncio.wait_for(entered.wait(), 3)
        position.net_amount_base += D('1')
        release.set()
        await s.runner.drain_repository_reporting()
        assert s.runner.positions_held['controller1'] == [position]
        s.runner.store_all_positions()
        await s.runner.drain_repository_reporting()
        assert s.runner.positions_held['controller1'] == []
        state = await s.app.position_repository.positions(s.cfg.binding)
        assert state.positions[0].net_amount_base == position.net_amount_base
        s.strategy.buy.assert_not_called()
        s.strategy.sell.assert_not_called()
    finally:
        release.set()
        await close(s)


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_lost_projection_reply_keeps_child_and_cold_repairs_only_original(database, monkeypatch, committed):
    s = await setup(database, monkeypatch)
    original = s.financial._runtime._commit
    try:
        assert await s.app.before_dispatch(s.t.create)
        s.runner.execute_action(s.t.create)
        await s.app.drain()
        child, = s.runner.active_executors['controller1']
        child._status = RunnableStatus.TERMINATED
        # The stage is known; lose only the application's delivery COMMIT reply.
        real_apply = s.app.repository.apply

        async def lost_apply(*args, **kwargs):
            async def lost(tx):
                if committed:
                    await original(tx)
                raise ConnectionError('synthetic reporting COMMIT reply loss')
            monkeypatch.setattr(s.financial._runtime, '_commit', lost)
            return await real_apply(*args, **kwargs)
        monkeypatch.setattr(s.app.repository, 'apply', lost_apply)
        s.runner.store_executor(StoreExecutorAction(controller_id='controller1', executor_id=child.config.id))
        with pytest.raises(CommitUnknown):
            await s.app.drain()
        assert child in s.runner.active_executors['controller1'] and s.revoked
        monkeypatch.setattr(s.financial._runtime, '_commit', original)
        cold = await PgRecorderApplication.install(s.financial, **s.kwargs)
        assert (await cold.stored_infos())[0].id == child.config.id
        assert s.revoked and not s.t.session.sent
        await cold.close()
    finally:
        await close(s, failed=True)


@pytest.mark.asyncio
async def test_queue_rejects_excess_work_and_still_finishes_admitted_report(database, monkeypatch):
    s = await setup(database, monkeypatch, max_pending=1)
    entered, release = asyncio.Event(), asyncio.Event()
    original = s.app.position_repository.apply
    try:
        async def blocked(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)
        monkeypatch.setattr(s.app.position_repository, 'apply', blocked)
        s.app.enqueue_positions((held(),), int((await s.financial.observation_cut(s.cfg.binding)).observed_at.timestamp() * 1000))
        await asyncio.wait_for(entered.wait(), 3)
        with pytest.raises(StorageError, match='RECORDER_APPLICATION_BACKPRESSURE'):
            s.app.enqueue_positions((held(mark_price=D('1.4')),), 1)
        assert s.revoked and not s.app.ready
        release.set()
        await s.app.drain()
        assert len((await s.app.position_repository.positions(s.cfg.binding)).positions) == 1
    finally:
        release.set()
        await close(s)


@pytest.mark.asyncio
async def test_event_replay_keeps_original_capture_and_never_restores_sdk_dedup(database, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_events import event

    from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import json_text
    s = await setup(database, monkeypatch)
    try:
        at = int((await s.financial.observation_cut(s.cfg.binding)).observed_at.timestamp() * 1000)
        captured = event(s.cfg.binding, s.source, at, 'OrderFilled')
        await s.app.enqueue_event(captured)
        await s.app.drain()
        changed_tracking = replace(captured, tracking_json=json_text({'wire1': {'new': 'snapshot'}}))
        await s.app.enqueue_event(changed_tracking)
        await s.app.drain()
        origin = await s.app.events[0].event_origin(captured.event_id, s.cfg.binding)
        assert origin.command == captured
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."TradeFill"') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.fills') == 0
        connector = s.strategy.connectors['okx']
        connector.add_trade_fills_from_market_recorder.assert_not_called()
        connector.restore_tracking_states.assert_not_called()
        assert not s.t.session.sent
    finally:
        await close(s)


@pytest.mark.asyncio
async def test_original_pending_event_is_repaired_before_startup_views(database, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_events import event
    s = await setup(database, monkeypatch)
    try:
        at = int((await s.financial.observation_cut(s.cfg.binding)).observed_at.timestamp() * 1000)
        captured = event(s.cfg.binding, s.source, at)
        await s.app.events[0].stage(captured, cut=(await s.financial.snapshot(s.cfg.binding)).cut, owner=s.t.owner)
        assert (await s.financial.snapshot(s.cfg.binding)).recovery_required
        await s.app.recover()
        assert s.app.ready and await s.app.events[0].pending(s.cfg.binding) == ()
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."Order"') == 1
        assert not s.t.session.sent
    finally:
        await close(s)


@pytest.mark.asyncio
async def test_bounded_duplicate_callbacks_finish_admitted_report_and_revoke_excess(database, monkeypatch):
    s = await setup(database, monkeypatch, max_pending=1, max_callbacks=2)
    entered, release = asyncio.Event(), asyncio.Event()
    original = s.app.position_repository.apply
    callbacks = []
    try:
        async def blocked(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)
        monkeypatch.setattr(s.app.position_repository, 'apply', blocked)
        at = int((await s.financial.observation_cut(s.cfg.binding)).observed_at.timestamp() * 1000)
        first = s.app.enqueue_positions((held(),), at, on_committed=lambda receipt: callbacks.append(receipt))
        await asyncio.wait_for(entered.wait(), 3)
        assert s.app.enqueue_positions((held(),), at, on_committed=lambda receipt: callbacks.append(receipt)) is first
        with pytest.raises(StorageError, match='RECORDER_CALLBACK_BACKPRESSURE'):
            s.app.enqueue_positions((held(),), at, on_committed=lambda receipt: callbacks.append(receipt))
        assert s.revoked
        release.set()
        await s.app.drain()
        assert len(callbacks) == 2 and all(not receipt.trading_permission for receipt in callbacks)
        assert await database[0].fetchval("SELECT count(*) FROM life_financial.recorder_outbox WHERE stream LIKE 'position:%'") == 1
    finally:
        release.set()
        await close(s)


@pytest.mark.asyncio
async def test_reporting_uses_one_safety_slot_while_primary_readback_remains_available(database, monkeypatch):
    s = await setup(database, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    original = s.financial._runtime._commit
    try:
        async def blocked(tx):
            entered.set()
            await release.wait()
            await original(tx)
        monkeypatch.setattr(s.financial._runtime, '_commit', blocked)
        at = int((await s.financial.observation_cut(s.cfg.binding)).observed_at.timestamp() * 1000)
        s.app.enqueue_positions((held(),), at)
        await asyncio.wait_for(entered.wait(), 3)
        assert s.financial._runtime._counts[True] == 1
        # Real primary read through the second pool slot, while physical COMMIT
        # is withheld. No synchronous SQLAlchemy work runs on the event loop.
        current = await asyncio.wait_for(s.financial.snapshot(s.cfg.binding), 1)
        assert current.balance.total_base_life >= 0
        release.set()
        await s.app.drain()
    finally:
        release.set()
        await close(s)


@pytest.mark.asyncio
async def test_unregistered_event_source_prevents_reporter_installation(database, monkeypatch):
    s = await setup(database, monkeypatch)
    try:
        kwargs = {**s.kwargs, 'sources': (replace(s.source, config_file_path='foreign.yml'),)}
        with pytest.raises(StorageError, match='PG_RECORDER_REGISTERED_SOURCES_REQUIRED'):
            await PgRecorderApplication.install(s.financial, **kwargs)
        assert s.revoked and not s.t.session.sent
    finally:
        await close(s)


@pytest.mark.asyncio
async def test_unchanged_executor_notifications_reuse_original_applied_receipt(database, monkeypatch):
    s = await setup(database, monkeypatch)
    try:
        assert await s.app.before_dispatch(s.t.create)
        s.runner.execute_action(s.t.create)
        await s.app.drain()
        child, = s.runner.active_executors['controller1']
        count = await database[0].fetchval('SELECT count(*) FROM life_financial.recorder_outbox')
        callbacks = []
        receipt = await s.app.enqueue(child.executor_info, on_committed=lambda r: callbacks.append(r))
        await s.app.drain()
        assert receipt.replayed and callbacks == [receipt]
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.recorder_outbox') == count
        assert not receipt.trading_permission and not s.t.session.sent
    finally:
        await close(s)


@pytest.mark.asyncio
async def test_late_executor_observation_during_projection_wait_keeps_child_for_new_report(database, monkeypatch):
    s = await setup(database, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    original = s.app.repository.apply
    try:
        assert await s.app.before_dispatch(s.t.create)
        s.runner.execute_action(s.t.create)
        await s.app.drain()
        child, = s.runner.active_executors['controller1']
        child._status = RunnableStatus.TERMINATED

        async def blocked(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)
        monkeypatch.setattr(s.app.repository, 'apply', blocked)
        action = StoreExecutorAction(controller_id='controller1', executor_id=child.config.id)
        s.runner.store_executor(action)
        await asyncio.wait_for(entered.wait(), 3)
        child._held_position_orders.append({'client_order_id': 'wire1', 'executed_amount_base': '1',
                                           'executed_amount_quote': '1', 'cumulative_fee_paid_quote': '0', 'trade_type': 'BUY'})
        release.set()
        await s.app.drain()
        assert child in s.runner.active_executors['controller1']
        assert ('controller1', child.config.id) not in s.runner._repository_stored_ids
        s.runner.store_executor(action)
        await s.app.drain()
        assert child not in s.runner.active_executors['controller1']
        infos = await s.app.stored_infos()
        assert infos[0].custom_info['recovery_order_ids'] == ['wire1']
        assert not s.t.session.sent
    finally:
        release.set()
        await close(s)


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['REJECTED', 'APPLIED'])
async def test_configuration_kill_or_rejection_revokes_at_capture_and_survives_restart(database, monkeypatch, outcome):
    from hummingbot.strategy_v2.life_liquidity.storage.recorder_configuration import ConfigurationReport
    s = await setup(database, monkeypatch)
    cold = None
    try:
        config = s.controller.config.model_copy(deep=True)
        if outcome == 'APPLIED':
            config.manual_kill_switch = True
        before = await s.financial.balance(s.cfg.binding)
        observed = await s.financial.observation_cut(s.cfg.binding)
        captured = ConfigurationReport(
            'config-update', s.cfg.binding, config.id, 1, 2 if outcome == 'APPLIED' else 1, config.model_dump_json(),
            s.cfg.binding.policy_hash, fingerprint(config.model_dump(mode='json')), outcome,
            'CONFIG_UPDATE_APPLIED' if outcome == 'APPLIED' else 'CONFIG_VALIDATION_FAILED', int(observed.observed_at.timestamp() * 1000))
        task = s.app.enqueue_configuration(captured)
        assert s.revoked and not s.app._admission_ready()  # Before any await or SQL.
        await task
        await s.app.drain()
        s.controller.config = config
        state = await s.app.configurations.configuration(s.cfg.binding)
        assert state.rejection_latched is (outcome == 'REJECTED') and state.kill_latched is (outcome == 'APPLIED')
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        assert await s.financial.balance(s.cfg.binding) == before
        cold = await PgRecorderApplication.install(s.financial, **s.kwargs)
        assert cold.ready and not cold._admission_ready()
        assert not await cold.before_dispatch(s.t.create)
        assert not s.t.session.sent
    finally:
        if cold is not None:
            await cold.close()
        await close(s)
