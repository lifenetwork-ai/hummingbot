"""Crash points across the spot runner, WAL, reservation journal, and gateway."""

import asyncio
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from test.hummingbot.strategy_v2.life_liquidity.test_controller_order_safety import (
    _cashflows,
    _limits,
    _recovery_config,
    _seed_recovery,
)
from test.hummingbot.strategy_v2.life_liquidity.test_executor_protected_send import _setup
from test.hummingbot.strategy_v2.life_liquidity.test_final_quote_send import _attach_quote_planner
from test.hummingbot.strategy_v2.life_liquidity.test_order_gateway import FakeOkx, order
from test.hummingbot.strategy_v2.life_liquidity.test_spot_session_replay import FakeTradingOkx, _runner
from unittest.mock import MagicMock

import pytest

from controllers.generic.life_liquidity import LifeLiquidityController
from hummingbot.strategy_v2.life_liquidity import account_lock
from hummingbot.strategy_v2.life_liquidity.order_gateway import (
    OkxSpotOrderGateway,
    SpotAccountReconciler,
    SpotReservationReconciler,
)
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
from hummingbot.strategy_v2.life_liquidity.safety import SafetyGate
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL
from hummingbot.strategy_v2.models.base import RunnableStatus

NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)


def _connector(state="canceled", filled="0"):
    connector = FakeOkx()
    connector.status["wire-1"] = order(state, filled=filled)
    connector.bill_pages[None] = [{"billId": "100"}]
    if state == "live":
        connector.open_pages[None] = [{"clOrdId": "wire-1", "ordId": "exchange-1",
                                       "instId": "LIFE-USDT", "state": "live"}]
    return connector


def _controller(directory, connector):
    provider = MagicMock()
    provider.get_connector_with_fallback.return_value = connector
    return LifeLiquidityController(_recovery_config(directory), provider, MagicMock())


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_history", [False, True])
async def test_runner_send_unknown_cold_restart_requires_stored_executor_proof(
        tmp_path, monkeypatch, stored_history):
    monkeypatch.setattr(account_lock, "ACCOUNT_LOCK_ROOT", tmp_path / "locks")
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
        executors[0]._status = RunnableStatus.TERMINATED
        executors[0].config = executors[0].config.model_copy(update={"timestamp": 1.0})
        stored = executors[0].executor_info
    finally:
        runner.listen_to_executor_actions_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner.listen_to_executor_actions_task

    _cashflows(tmp_path)
    connector.status[wire_id] = {"clOrdId": wire_id, "ordId": "exchange-1",
                                 "state": "canceled", "accFillSz": "0"}
    connector.bill_pages[None] = [{"billId": "100"}]
    restored = _controller(tmp_path, connector)
    restored_runner = _runner(restored, connector, [], proposed.config)
    if stored_history:
        restored_runner.executor_orchestrator.get_stored_executors_by_controller.return_value = (stored,)
    try:
        restored_runner.tick(2)
        await restored.order_safety_task
        restored_wal = IntentWAL(wal.path).get("quote-1")
        restored_ledger = ReservationLedger.restore(ledger.path, limits=_limits())
        if stored_history:
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
async def test_restart_replays_terminal_reservation_when_wal_checkpoint_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(account_lock, "ACCOUNT_LOCK_ROOT", tmp_path / "locks")
    directory = tmp_path / "recovery"
    _seed_recovery(directory)
    wal = IntentWAL(directory / "intents.json")
    ledger = ReservationLedger.restore(directory / "reservations.json", limits=_limits())
    connector = _connector()
    reconciler = SpotReservationReconciler(wal, ledger, require_fees=True)
    gateway = OkxSpotOrderGateway(
        connector, wal, trading_pair="LIFE-USDT", clock=lambda: NOW,
        apply_fills=reconciler.apply_fills, confirm_terminal=reconciler.confirm_terminal,
        account_check=lambda: True, scope_check=lambda *_: True)

    def fail_terminal_checkpoint(*_args):
        raise OSError("crash after reservation checkpoint")

    wal.mark_terminal = fail_terminal_checkpoint
    session = wal.get("i1")
    first = await gateway.reconcile(session.session_id, session.epoch)
    assert not first.trade_events_reconciled
    assert ReservationLedger.restore(ledger.path, limits=_limits()).is_terminal_intent("i1")
    interrupted = IntentWAL(wal.path).get("i1")
    assert interrupted.state == "ACKED" and interrupted.exchange_terminal_observed

    controller = _controller(directory, connector)
    try:
        controller._restore_order_safety()
        assert not controller.allow_create_executor_actions()
        controller.on_safety_tick(1)
        await controller.order_safety_task
        assert IntentWAL(wal.path).get("i1").state == "TERMINAL"
        assert ReservationLedger.restore(ledger.path, limits=_limits()).life_balance == Decimal("10")
        assert connector.cancels == []  # terminal status was already persisted before the crash
    finally:
        controller.stop()


@pytest.mark.asyncio
async def test_killed_process_after_reservation_replace_replays_terminal_wal(tmp_path, monkeypatch):
    monkeypatch.setattr(account_lock, "ACCOUNT_LOCK_ROOT", tmp_path / "locks")
    directory = tmp_path / "recovery"
    _seed_recovery(directory)
    wal = IntentWAL(directory / "intents.json")
    assert wal.get("i1").state == "SEND_UNKNOWN"
    wal.acknowledge("i1", "exchange-1")
    ledger_path = directory / "reservations.json"
    child = """
import os
import sys
from decimal import Decimal
from pathlib import Path
from hummingbot.strategy_v2.life_liquidity import risk
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, RiskLimits
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL

wal_path, ledger_path = map(Path, sys.argv[1:])
IntentWAL(wal_path).mark_exchange_terminal_observed("i1", "exchange-1")
ledger = ReservationLedger.restore(
    ledger_path, limits=RiskLimits(Decimal("0"), Decimal("20"), Decimal("20"), Decimal("20")))
original_replace = risk.os.replace

def replace_then_die(source, destination):
    original_replace(source, destination)
    if Path(destination) == ledger_path:
        os._exit(23)

risk.os.replace = replace_then_die
ledger.confirm_terminal("i1", cumulative_filled=Decimal("0"),
                        fills_reconciled=True, exchange_state="CANCELED")
raise AssertionError("process should have exited during the reservation checkpoint")
"""
    process = subprocess.run(
        [sys.executable, "-c", child, str(wal.path), str(ledger_path)],
        cwd=Path(__file__).resolve().parents[4], capture_output=True, text=True, timeout=15)
    assert process.returncode == 23, process.stderr
    interrupted = IntentWAL(wal.path).get("i1")
    assert interrupted.state == "ACKED" and interrupted.exchange_terminal_observed
    assert ReservationLedger.restore(ledger_path, limits=_limits()).is_terminal_intent("i1")

    connector = _connector(state="canceled")
    controller = _controller(directory, connector)
    try:
        controller.on_safety_tick(1)
        await controller.order_safety_task
        assert IntentWAL(wal.path).get("i1").state == "TERMINAL"
        assert ReservationLedger.restore(ledger_path, limits=_limits()).is_terminal_intent("i1")
        assert connector.cancels == []
        assert not controller.allow_create_executor_actions()
    finally:
        controller.stop()


@pytest.mark.asyncio
async def test_cancel_intent_checkpoint_replays_after_restart_without_releasing_reservation(tmp_path, monkeypatch):
    monkeypatch.setattr(account_lock, "ACCOUNT_LOCK_ROOT", tmp_path / "locks")
    directory = tmp_path / "recovery"
    _seed_recovery(directory)
    wal = IntentWAL(directory / "intents.json")
    wal.mark_cancel_requested("i1")  # crash before the reservation callback and REST request
    connector = _connector(state="live")
    first = _controller(directory, connector)
    try:
        first.on_safety_tick(1)
        await first.order_safety_task
        assert connector.cancels == [("LIFE-USDT", "wire-1")]
        assert ReservationLedger.restore(directory / "reservations.json", limits=_limits()).requires_reconciliation("i1")
        assert IntentWAL(wal.path).get("i1").state != "TERMINAL"
    finally:
        first.stop()

    connector.status["wire-1"] = order("canceled")
    connector.open_pages[None] = []
    second = _controller(directory, connector)
    try:
        second.on_safety_tick(2)
        await second.order_safety_task
        assert IntentWAL(wal.path).get("i1").state == "TERMINAL"
        assert ReservationLedger.restore(directory / "reservations.json", limits=_limits()).reserved_usdt == 0
        assert not second.allow_create_executor_actions()
    finally:
        second.stop()


@pytest.mark.asyncio
async def test_fill_checkpoint_failure_replays_exchange_trade_once_after_restart(tmp_path, monkeypatch):
    directory = tmp_path / "recovery"
    _seed_recovery(directory)
    wal = IntentWAL(directory / "intents.json")
    ledger = ReservationLedger.restore(directory / "reservations.json", limits=_limits())
    connector = _connector(state="partially_filled", filled="0.4")
    connector.fills["exchange-1"] = [
        {"tradeId": "trade-1", "ordId": "exchange-1", "fillSz": "0.4", "fillPx": "1"}]
    reconciler = SpotReservationReconciler(wal, ledger)
    gateway = OkxSpotOrderGateway(
        connector, wal, trading_pair="LIFE-USDT", clock=lambda: NOW,
        apply_fills=reconciler.apply_fills, confirm_terminal=reconciler.confirm_terminal,
        scope_check=lambda *_: True)
    monkeypatch.setattr(ledger, "_save", lambda *_args: (_ for _ in ()).throw(OSError("crash")))
    session = wal.get("i1")
    assert not (await gateway.reconcile(session.session_id, session.epoch)).trade_events_reconciled
    assert ReservationLedger.restore(ledger.path, limits=_limits()).trade_ids == set()

    restored_wal = IntentWAL(wal.path)
    restored_ledger = ReservationLedger.restore(ledger.path, limits=_limits())
    replay = SpotReservationReconciler(restored_wal, restored_ledger)
    restarted = OkxSpotOrderGateway(
        connector, restored_wal, trading_pair="LIFE-USDT", clock=lambda: NOW,
        apply_fills=replay.apply_fills, confirm_terminal=replay.confirm_terminal,
        scope_check=lambda *_: True)
    assert (await restarted.reconcile(session.session_id, session.epoch)).trade_events_reconciled
    assert (await restarted.reconcile(session.session_id, session.epoch)).trade_events_reconciled
    assert restored_ledger.trade_ids == {"trade-1"}
    assert restored_ledger.life_balance == Decimal("10.4")
    assert restored_ledger.usdt_balance == Decimal("9.6")


@pytest.mark.asyncio
async def test_runner_cancel_timeout_keeps_unknown_send_until_exchange_terminal_proof(
        tmp_path, monkeypatch):
    connector = FakeTradingOkx()
    controller, template, _, wal, ledger, _ = _setup(tmp_path, connector=connector)
    _, proposed = _attach_quote_planner(controller, template, wal, ledger)
    controller._spot_quote_gates_ready = lambda: True
    reconciler = SpotReservationReconciler(wal, ledger, require_fees=True)
    gateway = OkxSpotOrderGateway(
        connector, wal, trading_pair="LIFE-USDT",
        clock=lambda: controller._order_safety_manager.wall_clock(),
        apply_fills=reconciler.apply_fills, confirm_terminal=reconciler.confirm_terminal,
        on_cancel_requested=reconciler.request_cancel, on_unknown=reconciler.mark_unknown,
        account_check=SpotAccountReconciler(connector, ledger).check)
    controller.install_order_safety(controller._order_safety_manager, gateway, wal,
                                    reservations=ledger)
    executors = []
    runner = _runner(controller, connector, executors, proposed.config)
    try:
        runner.tick(1)
        assert len(connector.sent) == 1
        wire_id = connector.sent[0]["order_id"]
        connector.status[wire_id] = {"clOrdId": wire_id, "ordId": "exchange-1",
                                     "state": "live", "accFillSz": "0"}
        connector.open_pages[None] = [{"clOrdId": wire_id, "ordId": "exchange-1",
                                       "instId": "LIFE-USDT", "state": "live"}]

        async def lost_cancel_ack(*_args):
            raise TimeoutError("cancel result unknown")

        with monkeypatch.context() as patcher:
            patcher.setattr(connector, "cancel_by_client_id", lost_cancel_ack)
            controller._spot_quote_gates_ready = lambda: False
            runner.tick(2)
            await controller.order_safety_task
        assert wal.get(proposed.config.id).state != "TERMINAL"
        assert ledger.requires_reconciliation(proposed.config.id)
        assert len(connector.sent) == 1
        assert IntentWAL(wal.path).get(proposed.config.id).state != "TERMINAL"
        assert ReservationLedger.restore(ledger.path, limits=ledger.limits).requires_reconciliation(
            proposed.config.id)

        connector.status[wire_id]["state"] = "canceled"
        connector.open_pages[None] = []
        runner.tick(3)
        await controller.order_safety_task
        assert wal.get(proposed.config.id).state == "TERMINAL"
        assert ledger.is_terminal_intent(proposed.config.id)
        assert len(connector.sent) == 1
    finally:
        runner.listen_to_executor_actions_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner.listen_to_executor_actions_task


def test_process_kill_after_manual_halt_replace_restores_latched_state(tmp_path):
    journal = tmp_path / "safety.json"
    child = """
import os
import sys
from decimal import Decimal
from pathlib import Path
from hummingbot.strategy_v2.life_liquidity import safety
from hummingbot.strategy_v2.life_liquidity.safety import SafetyGate

path = Path(sys.argv[1])
gate = SafetyGate(path, max_drawdown_bps=Decimal("500"),
                  min_margin_buffer_quote=Decimal("10"), stable_data_ms=0,
                  recovery_probe_base=Decimal("1"))
replace = safety.os.replace

def replace_then_die(source, destination):
    replace(source, destination)
    if Path(destination) == path:
        os._exit(29)

safety.os.replace = replace_then_die
gate.halt("MANUAL_KILL_SWITCH")
raise AssertionError("child should stop after the HALT journal replacement")
"""
    process = subprocess.run(
        [sys.executable, "-c", child, str(journal)],
        cwd=Path(__file__).resolve().parents[4], capture_output=True, text=True, timeout=15)
    assert process.returncode == 29, process.stderr
    restored = SafetyGate(journal, max_drawdown_bps=Decimal("500"),
                          min_margin_buffer_quote=Decimal("10"), stable_data_ms=0,
                          recovery_probe_base=Decimal("1"))
    assert restored.state == "HALTED"
    assert restored.reason_code == "MANUAL_KILL_SWITCH"
