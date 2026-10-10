"""Manual rearm requires a fresh authenticated account/runner reconciliation."""

import asyncio
from datetime import timedelta
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_controller_order_safety import _cashflows, _recovery_config
from test.hummingbot.strategy_v2.life_liquidity.test_executor_protected_send import _setup
from test.hummingbot.strategy_v2.life_liquidity.test_final_quote_send import _attach_quote_planner
from test.hummingbot.strategy_v2.life_liquidity.test_recorder_cold_restart import _recorder
from test.hummingbot.strategy_v2.life_liquidity.test_runtime_risk_binding import _observation
from test.hummingbot.strategy_v2.life_liquidity.test_spot_session_replay import FakeTradingOkx, _runner
from unittest.mock import MagicMock

import pytest

from controllers.generic.life_liquidity import LifeLiquidityController
from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator
from hummingbot.strategy_v2.life_liquidity import account_lock
from hummingbot.strategy_v2.life_liquidity.safety import SafetyGate
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [
    None, "live", "uid", "regular", "algo", "bills", "balance", "recorder",
    "action_missing", "action_corrupt", "reservation_missing", "wal_changed",
    "halt", "expired", "clock_rollback", "stale_during_fetch", "stopped_during_fetch",
    "halt_during_fetch", "config_during_fetch", "lock_lost", "timeout", "cancelled",
])
async def test_rearm_uses_fresh_account_proof_and_never_retries_unknown(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(account_lock, "ACCOUNT_LOCK_ROOT", tmp_path / "locks")
    monkeypatch.setattr(MarketsRecorder, "_shared_instance", _recorder(tmp_path / "executors.sqlite"))
    connector = FakeTradingOkx()
    first, template, _, wal, ledger, _ = _setup(
        tmp_path, connector=connector, recovery_account_uid="12345")
    _, proposed = _attach_quote_planner(first, template, wal, ledger)
    first._spot_quote_gates_ready = lambda: True
    runner = _runner(first, connector, [], proposed.config)
    try:
        runner.tick(1)
        assert len(connector.sent) == 1
    finally:
        runner.listen_to_executor_actions_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner.listen_to_executor_actions_task
    wire = connector.sent[0]["order_id"]
    clock = first._order_safety_manager
    _cashflows(tmp_path)
    connector.bill_pages[None] = [{"billId": "100"}]
    connector.status[wire] = {"clOrdId": wire, "ordId": "exchange-1", "state": "canceled", "accFillSz": "0"}
    settings = dict(max_drawdown_bps=Decimal("500"), min_margin_buffer_quote=Decimal("10"),
                    stable_data_ms=10, recovery_probe_base=Decimal("0.1"))
    SafetyGate(tmp_path / "safety.json", **settings).initialize_empty()
    provider = MagicMock()
    provider.get_connector_with_fallback.return_value = connector
    config = _recovery_config(tmp_path).model_copy(update={"require_quote_action_journal": True})
    recovered = LifeLiquidityController(config, provider, MagicMock())
    recovered._restore_order_safety()
    recovered._order_safety_manager.wall_clock = clock.wall_clock
    recovered._order_safety_manager._reset_monotonic_deadline()
    recovered._order_safety_gateway.clock = clock.wall_clock
    gate = SafetyGate(tmp_path / "safety.json", require_existing=True, **settings)
    recovered.install_runtime_risk_gate(
        gate, observation=lambda: _observation(100), monotonic_clock_ms=lambda: 100,
        max_observation_age_ms=5)
    restored_runner = _runner(recovered, connector, [], proposed.config)
    recovered._runner_orchestrator = restored_runner.executor_orchestrator
    restored_runner.executor_orchestrator.get_stored_executors_by_controller.side_effect = (
        lambda controller_id: ExecutorOrchestrator.get_stored_executors_by_controller(
            restored_runner.executor_orchestrator, controller_id))
    assert not gate.evaluate(_observation(100)).allow_new_quotes
    with pytest.raises(ValueError, match="SAFETY_REARM_PROOF_REQUIRED"):
        gate.arm_after_reconciliation(reconciled=True, operator_id="operator")

    if fault == "live":
        connector.status[wire]["state"] = "live"
        connector.open_pages[None] = [{**connector.status[wire], "instId": "LIFE-USDT"}]
    elif fault == "uid":
        connector.account_uid = "99999"
    elif fault == "regular":
        connector.open_pages[None] = [{"clOrdId": "foreign", "ordId": "foreign", "instId": "BTC-USDT"}]
    elif fault == "algo":
        connector.algo_pages["trigger"] = {"code": "0", "data": [{"algoId": "foreign"}]}
    elif fault == "bills":
        connector.bill_pages[None] = []
    elif fault == "balance":
        connector.cash_balances["USDT"] = "9"
    elif fault == "recorder":
        restored_runner.executor_orchestrator.get_stored_executors_by_controller.side_effect = OSError("read failed")
    elif fault == "action_missing":
        (tmp_path / "quote_actions.json").unlink()
    elif fault == "action_corrupt":
        (tmp_path / "quote_actions.json").write_text("{")
    elif fault == "reservation_missing":
        (tmp_path / "reservations.json").unlink()
    elif fault == "wal_changed":
        IntentWAL(wal.path).mark_cancel_requested("quote-1")
    elif fault == "halt":
        gate.halt("MANUAL_KILL_SWITCH")
    elif fault in ("expired", "clock_rollback"):
        clock.wall_clock = lambda: (clock.current_session.expires_at if fault == "expired" else
                                    clock.current_session.started_at - timedelta(seconds=1))
        recovered._order_safety_manager.wall_clock = clock.wall_clock
        recovered._order_safety_gateway.clock = clock.wall_clock
    elif fault in ("stale_during_fetch", "stopped_during_fetch", "halt_during_fetch",
                   "config_during_fetch", "lock_lost", "timeout", "cancelled"):
        real_scope = connector.get_all_pending_spot_algo_orders_page

        async def interrupt(*args, **kwargs):
            result = await real_scope(*args, **kwargs)
            if fault == "stopped_during_fetch":
                recovered.stop()
            elif fault == "halt_during_fetch":
                recovered.manual_kill_switch()
                assert recovered.order_safety_task is not None
            elif fault == "config_during_fetch":
                recovered.config = recovered.config.model_copy(update={"id": "changed"})
            elif fault == "lock_lost":
                recovered._order_safety_account_lock.release()
            elif fault == "timeout":
                await asyncio.Future()
            elif fault == "cancelled":
                raise asyncio.CancelledError()
            else:
                recovered._order_safety_gateway.clock = lambda: clock.wall_clock() + timedelta(seconds=2)
            return result
        connector.get_all_pending_spot_algo_orders_page = interrupt
        if fault == "timeout":
            recovered.config = recovered.config.model_copy(update={"recovery_reconciliation_max_age_ms": 10})

    try:
        if fault == "cancelled":
            with pytest.raises(asyncio.CancelledError):
                await recovered.manual_rearm(operator_id="operator")
            assert not recovered._safety_rearm_in_progress
            assert gate.rearm_required
        elif fault is not None:
            with pytest.raises(ValueError, match="SAFETY_REARM_PROOF_REQUIRED"):
                await recovered.manual_rearm(operator_id="operator")
            assert not gate.evaluate(_observation(100)).allow_new_quotes
        else:
            await recovered.manual_rearm(operator_id="operator")
            assert gate.evaluate(_observation(100)).reason_code == "STABLE_DATA_WAIT"
            assert gate.evaluate(_observation(110)).state == "DEGRADED"
            assert gate.evaluate(_observation(120)).state == "NORMAL"
            assert IntentWAL(wal.path).get("quote-1").state == "TERMINAL"
        assert len(connector.sent) == 1
        with pytest.raises(ValueError, match="SAFETY_REARM_PROOF_REQUIRED"):
            gate.arm_after_reconciliation(reconciled=True, operator_id="operator")
    finally:
        restored_runner.listen_to_executor_actions_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await restored_runner.listen_to_executor_actions_task
        recovered.stop()
