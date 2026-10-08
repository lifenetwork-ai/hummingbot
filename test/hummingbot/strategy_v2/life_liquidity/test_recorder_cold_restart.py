"""LIFE cold restart reads executor evidence from a file-backed MarketsRecorder."""

import asyncio
from test.hummingbot.strategy_v2.life_liquidity.test_controller_order_safety import (
    _cashflows,
    _limits,
    _recovery_config,
)
from test.hummingbot.strategy_v2.life_liquidity.test_executor_protected_send import _setup
from test.hummingbot.strategy_v2.life_liquidity.test_final_quote_send import _attach_quote_planner
from test.hummingbot.strategy_v2.life_liquidity.test_spot_session_replay import FakeTradingOkx, _runner
from unittest.mock import MagicMock

import pytest
from sqlalchemy import event

from controllers.generic.life_liquidity import LifeLiquidityController
from hummingbot.client.config.client_config_map import ClientConfigMap, MarketDataCollectionConfigMap
from hummingbot.client.config.config_helpers import ClientConfigAdapter
from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.model.executors import Executors
from hummingbot.model.sql_connection_manager import SQLConnectionManager, SQLConnectionType
from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator
from hummingbot.strategy_v2.life_liquidity import account_lock
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import StoreExecutorAction


def _recorder(path):
    sql = SQLConnectionManager(ClientConfigAdapter(ClientConfigMap()),
                               SQLConnectionType.TRADE_FILLS, db_path=str(path))
    return MarketsRecorder(
        sql=sql, markets=[], config_file_path="life-test", strategy_name="v2",
        market_data_collection=MarketDataCollectionConfigMap(
            market_data_collection_enabled=False,
            market_data_collection_interval=60,
            market_data_collection_depth=20))


@pytest.mark.asyncio
@pytest.mark.parametrize("database_state", [
    "valid", "missing", "wrong_wire", "foreign_uid", "missing_uid", "read_failure",
])
async def test_file_backed_executor_history_gates_cold_restart(tmp_path, monkeypatch, database_state):
    monkeypatch.setattr(account_lock, "ACCOUNT_LOCK_ROOT", tmp_path / "locks")
    db_path = tmp_path / "executors.sqlite"
    recorder = _recorder(db_path)
    monkeypatch.setattr(MarketsRecorder, "_shared_instance", recorder)
    connector = FakeTradingOkx()
    controller, template, _, wal, ledger, _ = _setup(
        tmp_path, connector=connector, recovery_account_uid="12345")
    _, proposed = _attach_quote_planner(controller, template, wal, ledger)
    controller._spot_quote_gates_ready = lambda: True
    executors = []
    runner = _runner(controller, connector, executors, proposed.config)
    try:
        runner.tick(1)
        assert len(connector.sent) == 1
        wire_id = connector.sent[0]["order_id"]
        assert IntentWAL(wal.path).get("quote-1").state == "SEND_UNKNOWN"
        executor = executors[0]
        executor._status = RunnableStatus.TERMINATED
        executor.config = executor.config.model_copy(update={"timestamp": 1.0})
        controller.config = controller.config.model_copy(update={"recovery_account_uid": "67890"})
        assert executor.executor_info.custom_info["recovery_account_uid"] == "12345"
        checkpoint_orchestrator = ExecutorOrchestrator(strategy=runner)
        checkpoint_orchestrator.active_executors["life"] = [executor]
        checkpoint_orchestrator.store_executor(
            StoreExecutorAction(executor_id="quote-1", controller_id="life"))
        assert checkpoint_orchestrator.active_executors["life"] == []
        assert [row.id for row in recorder.get_executors_by_controller("life")] == ["quote-1"]
    finally:
        runner.listen_to_executor_actions_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner.listen_to_executor_actions_task

    with recorder.sql_manager.get_new_session() as session:
        row = session.query(Executors).filter(Executors.id == "quote-1").one()
        if database_state == "missing":
            session.delete(row)
        elif database_state in ("wrong_wire", "foreign_uid", "missing_uid"):
            metadata = dict(row.custom_info)
            if database_state == "wrong_wire":
                metadata["recovery_order_ids"] = ["other-wire"]
            elif database_state == "foreign_uid":
                metadata["recovery_account_uid"] = "67890"
            else:
                metadata.pop("recovery_account_uid")
            row.custom_info = metadata
        session.commit()

    reopened = _recorder(db_path)
    if database_state == "read_failure":
        @event.listens_for(reopened.sql_manager.engine, "before_cursor_execute")
        def fail_executor_read(_connection, _cursor, statement, _parameters, _context, _executemany):
            if 'FROM "Executors"' in statement:
                raise OSError("executor database read failed")
    monkeypatch.setattr(MarketsRecorder, "_shared_instance", reopened)
    _cashflows(tmp_path)
    connector.status[wire_id] = {"clOrdId": wire_id, "ordId": "exchange-1",
                                 "state": "canceled", "accFillSz": "0"}
    connector.bill_pages[None] = [{"billId": "100"}]
    provider = MagicMock()
    provider.get_connector_with_fallback.return_value = connector
    restored = LifeLiquidityController(_recovery_config(tmp_path), provider, MagicMock())
    restored_runner = _runner(restored, connector, [], proposed.config)
    restored_runner.executor_orchestrator.get_stored_executors_by_controller.side_effect = (
        lambda controller_id: ExecutorOrchestrator.get_stored_executors_by_controller(
            restored_runner.executor_orchestrator, controller_id))
    try:
        restored_runner.tick(2)
        await restored.order_safety_task
        restored_wal = IntentWAL(wal.path).get("quote-1")
        restored_ledger = ReservationLedger.restore(ledger.path, limits=_limits())
        if database_state == "valid":
            assert restored_wal.state == "TERMINAL"
            assert restored_ledger.is_terminal_intent("quote-1")
        else:
            assert restored.order_safety_reason_code == "RECONCILIATION_INCOMPLETE"
            assert restored_wal.state != "TERMINAL"
            assert restored_ledger.requires_reconciliation("quote-1")
        assert len(connector.sent) == 1
    finally:
        restored_runner.listen_to_executor_actions_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await restored_runner.listen_to_executor_actions_task
        restored.stop()
