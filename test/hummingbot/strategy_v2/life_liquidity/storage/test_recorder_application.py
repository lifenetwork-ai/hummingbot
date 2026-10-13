"""The actual executor orchestrator uses bounded repository reporting."""
import asyncio
import json
import threading
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_application import setup
from test.hummingbot.strategy_v2.life_liquidity.test_recorder_cold_restart import _recorder
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.data_type.order_book import OrderBook
from hummingbot.core.data_type.order_book_row import OrderBookRow
from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator
from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.recorder import FileRecorderRepository
from hummingbot.strategy_v2.life_liquidity.storage.recorder_application import FileRecorderApplication
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, StoreExecutorAction


async def assembly(tmp_path, monkeypatch):
    app, store, controller, prototype, sent, clock = await setup(tmp_path)
    recorder = _recorder(tmp_path / 'recorder.sqlite')
    repo = FileRecorderRepository(store, recorder, 'life')
    reporting = await FileRecorderApplication.install(repo, app.runtime, max_pending=2, shutdown_timeout_s=.5)
    strategy = SimpleNamespace(connectors={'okx': app.connector}, controllers={'life': controller})
    book = OrderBook()
    book.apply_snapshot([OrderBookRow(1, 10, 1)], [OrderBookRow(1.01, 10, 1)], 1)  # Explicit synthetic book.
    app.connector.order_book_tracker._order_books['LIFE-USDT'] = book
    app.connector._trading_rules['LIFE-USDT'] = TradingRule('LIFE-USDT', min_order_size=Decimal('1'),
                                                            min_price_increment=Decimal('.01'),
                                                            min_base_amount_increment=Decimal('1'))
    # Actual constructor/runner hooks remain; deterministic ticks control orders.
    monkeypatch.setattr(OrderExecutor, 'start', lambda executor: setattr(executor, '_status', RunnableStatus.RUNNING))
    monkeypatch.setattr(MarketsRecorder, 'get_instance', MagicMock(side_effect=AssertionError('global SQL forbidden')))
    runner = ExecutorOrchestrator(strategy, repository_reporting={'life': reporting})
    controller._runner_orchestrator = runner
    action = CreateExecutorAction(controller_id='life', executor_config=prototype.config.model_copy(deep=True))
    return app, reporting, repo, runner, action, sent, clock


@pytest.mark.asyncio
async def test_actual_runner_creation_progress_terminal_removal_and_fresh_worker_read(tmp_path, monkeypatch):
    app, reporting, repo, runner, action, sent, _ = await assembly(tmp_path, monkeypatch)
    try:
        assert await app.controller.async_before_runner_dispatch(action)
        runner.execute_action(action)
        executor = runner.active_executors['life'][0]
        assert isinstance(executor, OrderExecutor) and executor.status == RunnableStatus.RUNNING
        await runner.drain_repository_reporting()
        executor.place_open_order()
        await app.drain()
        assert len(sent) == 1
        executor._status = RunnableStatus.TERMINATED
        runner.execute_action(StoreExecutorAction(controller_id='life', executor_id=executor.config.id))
        assert executor in runner.active_executors['life']  # No removal before receipt.
        await runner.drain_repository_reporting()
        assert runner.active_executors['life'] == []
        row = json.loads((await repo.read(app.binding))[0].payload_json)
        assert row['status'] == RunnableStatus.TERMINATED.value
        assert row['custom_info']['recovery_order_ids'] == list(executor.recovery_order_ids())
        with pytest.raises(StorageError, match='STORAGE_WORKER_REQUIRED'):
            runner.get_stored_executors_by_controller('life')
        stored = await app.store._call(runner.get_stored_executors_by_controller, 'life')
        assert len(stored) == 1 and stored[0].id == executor.config.id
        # Reporting a terminated runner never terminalizes/refunds the live order.
        assert (await app.store.attempt(executor.config.id, app.binding)).state.value == 'ACKED'
        assert (await app.store.snapshot(app.binding)).balance.reserved_quote_usdt == 2
    finally:
        await reporting.close()
        await app.close()
        await app.store.close()


@pytest.mark.asyncio
async def test_blocked_reporting_does_not_block_stop_or_remove_executor_early(tmp_path, monkeypatch):
    app, reporting, repo, runner, action, _, _ = await assembly(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    try:
        assert await app.controller.async_before_runner_dispatch(action)
        runner.execute_action(action)
        await reporting.drain()
        executor = runner.active_executors['life'][0]
        executor._status = RunnableStatus.TERMINATED
        original = repo._recorder.update_executor_info

        def blocked(info):
            entered.set()
            assert release.wait(5)
            return original(info)
        monkeypatch.setattr(repo._recorder, 'update_executor_info', blocked)
        runner.store_executor(StoreExecutorAction(controller_id='life', executor_id=executor.config.id))
        assert await asyncio.to_thread(entered.wait, 3)
        app.controller.stop()
        await asyncio.sleep(0)
        assert not app.runtime._local_ready() and executor in runner.active_executors['life']
        assert not await reporting.before_dispatch(action)
        release.set()
        await reporting.drain()
        assert runner.active_executors['life'] == [] and not app.runtime._local_ready()
    finally:
        release.set()
        await reporting.close()
        await app.close()
        await app.store.close()


@pytest.mark.asyncio
async def test_reporting_failure_retains_runner_and_requires_explicit_cold_repair(tmp_path, monkeypatch):
    app, reporting, repo, runner, action, _, _ = await assembly(tmp_path, monkeypatch)
    assert await app.controller.async_before_runner_dispatch(action)
    runner.execute_action(action)
    await reporting.drain()
    executor = runner.active_executors['life'][0]
    executor._status = RunnableStatus.TERMINATED
    monkeypatch.setattr(repo._recorder, 'update_executor_info', lambda info: (_ for _ in ()).throw(OSError('synthetic')))
    runner.store_executor(StoreExecutorAction(controller_id='life', executor_id=executor.config.id))
    try:
        with pytest.raises(StorageError):
            await reporting.drain()
        assert executor in runner.active_executors['life'] and not app.runtime._local_ready()
        with pytest.raises(StorageError):
            await reporting.close()
    finally:
        await app.close()
        await app.store.close()
