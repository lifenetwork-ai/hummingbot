"""LIFE cold restart reads executor evidence from a file-backed MarketsRecorder."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path
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
from hummingbot.strategy_v2.executors.order_executor.data_types import OrderExecutorConfig
from hummingbot.strategy_v2.life_liquidity import account_lock, action_journal
from hummingbot.strategy_v2.life_liquidity.action_journal import QuoteActionJournal
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


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_executor", [True, False])
async def test_dispatch_fsync_failure_replays_across_action_wal_reservation_and_sqlite(
        tmp_path, monkeypatch, stored_executor):
    monkeypatch.setattr(account_lock, "ACCOUNT_LOCK_ROOT", tmp_path / "locks")
    db_path = tmp_path / "executors.sqlite"
    recorder = _recorder(db_path)
    monkeypatch.setattr(MarketsRecorder, "_shared_instance", recorder)
    connector = FakeTradingOkx()
    controller, template, _, wal, ledger, _ = _setup(
        tmp_path, connector=connector, recovery_account_uid="12345")
    _, proposed = _attach_quote_planner(controller, template, wal, ledger)
    controller._spot_quote_gates_ready = lambda: True
    journal = controller._quote_action_planner.action_journal
    original_save = journal._save
    fsync_failed = False

    def fail_dispatch_directory_fsync(records):
        if records["quote-1"].state != "DISPATCHED":
            return original_save(records)
        original_fsync = action_journal.os.fsync
        calls = 0

        def fail_second_fsync(descriptor):
            nonlocal calls, fsync_failed
            calls += 1
            if calls == 2:
                fsync_failed = True
                raise OSError("dispatch directory fsync failed")
            return original_fsync(descriptor)

        with monkeypatch.context() as patcher:
            patcher.setattr(action_journal.os, "fsync", fail_second_fsync)
            return original_save(records)

    journal._save = fail_dispatch_directory_fsync
    executors = []
    runner = _runner(controller, connector, executors, proposed.config)
    try:
        runner.tick(1)
        assert fsync_failed
        assert len(connector.sent) == 1
        wire_id = connector.sent[0]["order_id"]
        assert journal.get("quote-1").state == "PROPOSED"
        assert QuoteActionJournal(journal.path, account_uid="12345").get("quote-1").state == "DISPATCHED"
        assert IntentWAL(wal.path).get("quote-1").state == "SEND_UNKNOWN"
        assert ReservationLedger.restore(ledger.path, limits=_limits()).has_open_intent("quote-1")
        if stored_executor:
            executor = executors[0]
            executor._status = RunnableStatus.TERMINATED
            executor.config = executor.config.model_copy(update={"timestamp": 1.0})
            checkpoint = ExecutorOrchestrator(strategy=runner)
            checkpoint.active_executors["life"] = [executor]
            checkpoint.store_executor(StoreExecutorAction(executor_id="quote-1", controller_id="life"))
        assert [row.id for row in recorder.get_executors_by_controller("life")] == (
            ["quote-1"] if stored_executor else [])
    finally:
        runner.listen_to_executor_actions_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner.listen_to_executor_actions_task

    monkeypatch.setattr(MarketsRecorder, "_shared_instance", _recorder(db_path))
    _cashflows(tmp_path)
    connector.status[wire_id] = {"clOrdId": wire_id, "ordId": "exchange-1",
                                 "state": "canceled", "accFillSz": "0"}
    connector.bill_pages[None] = [{"billId": "100"}]
    provider = MagicMock()
    provider.get_connector_with_fallback.return_value = connector
    config = _recovery_config(tmp_path).model_copy(update={"require_quote_action_journal": True})
    restored = LifeLiquidityController(config, provider, MagicMock())
    restored_runner = _runner(restored, connector, [], proposed.config)
    restored_runner.executor_orchestrator.get_stored_executors_by_controller.side_effect = (
        lambda controller_id: ExecutorOrchestrator.get_stored_executors_by_controller(
            restored_runner.executor_orchestrator, controller_id))
    try:
        restored_runner.tick(2)
        await restored.order_safety_task
        assert restored.quote_action_recovery_reason_code == "QUOTE_ACTION_JOURNAL_VERIFIED"
        assert IntentWAL(wal.path).get("quote-1").state == "TERMINAL"
        assert ReservationLedger.restore(ledger.path, limits=_limits()).is_terminal_intent("quote-1")
        assert len(connector.sent) == 1
    finally:
        restored_runner.listen_to_executor_actions_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await restored_runner.listen_to_executor_actions_task
        restored.stop()

    second = LifeLiquidityController(config, provider, MagicMock())
    try:
        second._restore_order_safety()
        assert second.quote_action_recovery_reason_code == "QUOTE_ACTION_JOURNAL_VERIFIED"
        assert QuoteActionJournal(journal.path, account_uid="12345").get("quote-1").state == "RECONCILED"
        assert len(connector.sent) == 1
    finally:
        second.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("claim_state", [
    "valid", "missing", "wrong_uid", "read_failure", "journal_changed",
])
async def test_pre_send_journals_can_reconcile_without_late_executor_checkpoint(
        tmp_path, monkeypatch, claim_state):
    monkeypatch.setattr(account_lock, "ACCOUNT_LOCK_ROOT", tmp_path / "locks")
    db_path = tmp_path / "executors.sqlite"
    monkeypatch.setattr(MarketsRecorder, "_shared_instance", _recorder(db_path))
    connector = FakeTradingOkx()
    controller, template, _, wal, ledger, _ = _setup(
        tmp_path, connector=connector, recovery_account_uid="12345")
    _, proposed = _attach_quote_planner(controller, template, wal, ledger)
    controller._spot_quote_gates_ready = lambda: True
    action_path = tmp_path / "quote_actions.json"

    def assert_pre_send_provenance(_request):
        claim = QuoteActionJournal(action_path, account_uid="12345").get("quote-1")
        intent = IntentWAL(wal.path).get("quote-1")
        reservation = ReservationLedger.restore(ledger.path, limits=_limits())
        assert claim.state == "PROPOSED"
        assert intent.state == "SEND_UNKNOWN"
        assert reservation.has_open_intent("quote-1")

    connector.before_send = assert_pre_send_provenance
    runner = _runner(controller, connector, [], proposed.config)
    try:
        runner.tick(1)
        assert len(connector.sent) == 1
        wire_id = connector.sent[0]["order_id"]
        assert MarketsRecorder.get_instance().get_executors_by_controller("life") == []
    finally:
        runner.listen_to_executor_actions_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner.listen_to_executor_actions_task

    if claim_state in ("missing", "wrong_uid"):
        with action_path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if claim_state == "missing":
            data["records"].pop("quote-1")
        else:
            data["account_uid"] = "67890"
        action_path.write_text(json.dumps(data))
    reopened = _recorder(db_path)
    if claim_state == "read_failure":
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
    config = _recovery_config(tmp_path).model_copy(update={"require_quote_action_journal": True})
    restored = LifeLiquidityController(config, provider, MagicMock())
    restored_runner = _runner(restored, connector, [], proposed.config)
    restored_runner.executor_orchestrator.get_stored_executors_by_controller.side_effect = (
        lambda controller_id: ExecutorOrchestrator.get_stored_executors_by_controller(
            restored_runner.executor_orchestrator, controller_id))
    if claim_state == "journal_changed":
        original_check = restored._pre_send_provenance_complete

        def change_journal_after_restore(records):
            with action_path.open(encoding="utf-8") as handle:
                data = json.load(handle)
            data["records"]["quote-1"]["config_version"] += 1
            action_path.write_text(json.dumps(data))
            return original_check(records)

        restored._pre_send_provenance_complete = change_journal_after_restore
    try:
        restored_runner.tick(2)
        await restored.order_safety_task
        restored_wal = IntentWAL(wal.path).get("quote-1")
        restored_ledger = ReservationLedger.restore(ledger.path, limits=_limits())
        if claim_state == "valid":
            assert restored.quote_action_recovery_reason_code == "QUOTE_ACTION_JOURNAL_VERIFIED"
            assert restored_wal.state == "TERMINAL"
            assert restored_ledger.is_terminal_intent("quote-1")
        else:
            if claim_state in ("read_failure", "journal_changed"):
                assert restored.quote_action_recovery_reason_code == "QUOTE_ACTION_JOURNAL_VERIFIED"
            else:
                assert restored.quote_action_recovery_reason_code != "QUOTE_ACTION_JOURNAL_VERIFIED"
            assert restored.order_safety_reason_code == "RECONCILIATION_INCOMPLETE"
            assert restored_wal.state != "TERMINAL"
            assert restored_ledger.requires_reconciliation("quote-1")
        assert len(connector.sent) == 1
    finally:
        restored_runner.listen_to_executor_actions_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await restored_runner.listen_to_executor_actions_task
        restored.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", [
    "before_insert", "after_insert", "before_commit", "after_commit",
    "before_update", "after_update", "before_update_commit", "after_update_commit",
])
async def test_process_kill_inside_executor_insert_replays_pre_send_provenance(
        tmp_path, monkeypatch, boundary):
    """An INSERT visible only inside its transaction must not become recovery proof."""
    monkeypatch.setattr(account_lock, "ACCOUNT_LOCK_ROOT", tmp_path / "locks")
    db_path = tmp_path / "executors.sqlite"
    marker_path = tmp_path / "sent.json"
    child = """
import asyncio
import json
import os
import sys
from pathlib import Path
from sqlalchemy import event
from sqlalchemy.orm import Session
from test.hummingbot.strategy_v2.life_liquidity.test_executor_protected_send import _setup
from test.hummingbot.strategy_v2.life_liquidity.test_final_quote_send import _attach_quote_planner
from test.hummingbot.strategy_v2.life_liquidity.test_recorder_cold_restart import _recorder
from test.hummingbot.strategy_v2.life_liquidity.test_spot_session_replay import FakeTradingOkx, _runner
from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator
from hummingbot.strategy_v2.life_liquidity.action_journal import QuoteActionJournal
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import StoreExecutorAction

directory, db_path, marker_path = map(Path, sys.argv[1:4])
boundary = sys.argv[4]

async def main():
    recorder = _recorder(db_path)
    MarketsRecorder._shared_instance = recorder
    connector = FakeTradingOkx()
    controller, template, _, wal, ledger, _ = _setup(
        directory, connector=connector, recovery_account_uid="12345")
    _, proposed = _attach_quote_planner(controller, template, wal, ledger)
    controller._spot_quote_gates_ready = lambda: True

    def record_send(request):
        assert QuoteActionJournal(directory / "quote_actions.json", account_uid="12345").get(
            "quote-1").state == "PROPOSED"
        assert IntentWAL(wal.path).get("quote-1").state == "SEND_UNKNOWN"
        assert ReservationLedger.restore(ledger.path, limits=ledger.limits).has_open_intent("quote-1")
        with marker_path.open("w", encoding="utf-8") as handle:
            json.dump({"wire_id": request["order_id"],
                       "config": proposed.config.model_dump_json()}, handle)
            handle.flush()
            os.fsync(handle.fileno())

    connector.before_send = record_send
    executors = []
    runner = _runner(controller, connector, executors, proposed.config)
    runner.tick(1)
    assert len(connector.sent) == 1
    executor = executors[0]
    executor._status = RunnableStatus.TERMINATED
    executor.config = executor.config.model_copy(update={"timestamp": 1.0})
    checkpoint = ExecutorOrchestrator(strategy=runner)
    checkpoint.active_executors["life"] = [executor]

    updating = "update" in boundary
    if updating:
        checkpoint.store_executor(StoreExecutorAction(executor_id="quote-1", controller_id="life"))
        checkpoint.active_executors["life"] = [executor]
        executor.config = executor.config.model_copy(update={"timestamp": 2.0})
    if boundary in ("before_insert", "after_insert", "before_update", "after_update"):
        hook = "before_cursor_execute" if boundary.startswith("before") else "after_cursor_execute"
        @event.listens_for(recorder.sql_manager.engine, hook)
        def kill_at_insert(_connection, _cursor, statement, _parameters, _context, _executemany):
            command = 'UPDATE "Executors"' if updating else 'INSERT INTO "Executors"'
            if command in statement:
                os._exit(31)
    else:
        hook = "before_commit" if boundary.startswith("before") else "after_commit"
        @event.listens_for(Session, hook)
        def kill_at_commit(_session):
            os._exit(31)

    checkpoint.store_executor(StoreExecutorAction(executor_id="quote-1", controller_id="life"))
    raise AssertionError("executor INSERT did not interrupt the child")

asyncio.run(main())
"""
    process = subprocess.run(
        [sys.executable, "-c", child, str(tmp_path), str(db_path), str(marker_path), boundary],
        cwd=Path(__file__).resolve().parents[4], capture_output=True, text=True, timeout=30)
    assert process.returncode == 31, process.stderr
    with marker_path.open(encoding="utf-8") as handle:
        sent = json.load(handle)
    wire_id = sent["wire_id"]
    assert IntentWAL(tmp_path / "intents.json").get("quote-1").client_order_id == wire_id
    assert ReservationLedger.restore(tmp_path / "reservations.json", limits=_limits()).has_open_intent(
        "quote-1")

    reopened = _recorder(db_path)
    monkeypatch.setattr(MarketsRecorder, "_shared_instance", reopened)
    rows = reopened.get_executors_by_controller("life")
    assert [row.id for row in rows] == (["quote-1"] if "update" in boundary or boundary == "after_commit" else [])
    if rows:
        assert rows[0].timestamp == (2.0 if boundary == "after_update_commit" else 1.0)
    _cashflows(tmp_path)
    connector = FakeTradingOkx()
    connector.status[wire_id] = {"clOrdId": wire_id, "ordId": "exchange-1",
                                 "state": "live", "accFillSz": "0"}
    connector.open_pages[None] = [{"clOrdId": wire_id, "ordId": "exchange-1",
                                   "instId": "LIFE-USDT", "state": "live"}]
    connector.bill_pages[None] = [{"billId": "100"}]
    provider = MagicMock()
    provider.get_connector_with_fallback.return_value = connector
    config = _recovery_config(tmp_path).model_copy(update={"require_quote_action_journal": True})
    restored = LifeLiquidityController(config, provider, MagicMock())
    queued_config = OrderExecutorConfig.model_validate_json(sent["config"])
    restored_runner = _runner(restored, connector, [], queued_config)
    restored_runner.executor_orchestrator.get_stored_executors_by_controller.side_effect = (
        lambda controller_id: ExecutorOrchestrator.get_stored_executors_by_controller(
            restored_runner.executor_orchestrator, controller_id))
    try:
        restored_runner.tick(2)
        await restored.order_safety_task
        assert restored.quote_action_recovery_reason_code == "QUOTE_ACTION_JOURNAL_VERIFIED"
        assert restored.order_safety_reason_code == "OLD_ORDERS_UNRESOLVED"
        assert ReservationLedger.restore(tmp_path / "reservations.json", limits=_limits()).requires_reconciliation(
            "quote-1")
        assert connector.sent == []

        connector.status[wire_id]["state"] = "canceled"
        connector.open_pages[None] = []
        restored_runner.tick(3)
        await restored.order_safety_task
        assert IntentWAL(tmp_path / "intents.json").get("quote-1").state == "TERMINAL"
        assert ReservationLedger.restore(tmp_path / "reservations.json", limits=_limits()).is_terminal_intent(
            "quote-1")
        assert connector.sent == []
    finally:
        restored_runner.listen_to_executor_actions_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await restored_runner.listen_to_executor_actions_task
        restored.stop()
