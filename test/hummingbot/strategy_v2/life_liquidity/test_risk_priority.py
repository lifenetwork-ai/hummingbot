"""P4 priority: HALT, pause/cancel, limited reduction, then new quotes."""

from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch

import pytest

from hummingbot.strategy_v2.life_liquidity.safety import SafetyGate, SafetyObservation


def observe(*, at=0, fresh=True, latency=True, account=True, model=True,
            drawdown="0", margin="100"):
    return SafetyObservation(at, fresh, latency, account, model,
                             Decimal(drawdown), Decimal(margin))


def test_quality_failure_blocks_risk_and_requests_cancel_then_stable_recovery(tmp_path):
    gate = SafetyGate(tmp_path / "safety.json", max_drawdown_bps=Decimal("500"),
                      min_margin_buffer_quote=Decimal("10"), stable_data_ms=5000,
                      recovery_probe_base=Decimal("1"))
    gate.initialize_empty()
    assert gate.state == "PAUSED"
    degraded = gate.evaluate(observe(at=1000, fresh=False))
    assert degraded.state == "PAUSED" and degraded.cancel_required
    assert not degraded.allow_new_quotes
    assert degraded.reason_code == "MARKET_DATA_STALE"
    assert not gate.evaluate(observe(at=2000)).allow_new_quotes
    recovery = gate.evaluate(observe(at=7000))
    assert recovery.state == "DEGRADED"
    assert recovery.max_new_quote_base == Decimal("1")


def test_drawdown_halt_survives_restart_and_reload(tmp_path):
    path = tmp_path / "safety.json"
    gate = SafetyGate(path, max_drawdown_bps=Decimal("500"),
                      min_margin_buffer_quote=Decimal("10"), stable_data_ms=5000,
                      recovery_probe_base=Decimal("1"))
    gate.initialize_empty()
    halted = gate.evaluate(observe(at=1000, drawdown="501", fresh=False))
    assert halted.state == "HALTED" and halted.cancel_required
    restarted = SafetyGate(path, max_drawdown_bps=Decimal("9999"),
                           min_margin_buffer_quote=Decimal("0"), stable_data_ms=0,
                           recovery_probe_base=Decimal("100"))
    assert restarted.evaluate(observe(at=9000)).state == "HALTED"
    assert not restarted.evaluate(observe(at=9000)).allow_new_quotes


def test_missing_account_or_low_margin_has_priority_over_new_quotes(tmp_path):
    gate = SafetyGate(tmp_path / "safety.json", max_drawdown_bps=Decimal("500"),
                      min_margin_buffer_quote=Decimal("10"), stable_data_ms=0,
                      recovery_probe_base=Decimal("1"))
    gate.initialize_empty()
    missing = gate.evaluate(observe(account=False))
    assert missing.reason_code == "ACCOUNT_DATA_UNAVAILABLE" and missing.cancel_required
    low_margin = gate.evaluate(observe(at=1, margin="9"))
    assert low_margin.state == "HALTED" and low_margin.reason_code == "MARGIN_BUFFER_BREACHED"


def test_non_boolean_readiness_never_grants_quote_permission(tmp_path):
    gate = SafetyGate(tmp_path / "safety.json", max_drawdown_bps=Decimal("500"),
                      min_margin_buffer_quote=Decimal("10"), stable_data_ms=0,
                      recovery_probe_base=Decimal("1"))
    gate.initialize_empty()
    invalid = replace(observe(at=100), account_ready=1)

    decision = gate.evaluate(invalid)

    assert decision.state == "PAUSED"
    assert decision.reason_code == "RISK_DATA_UNAVAILABLE"
    assert not decision.allow_new_quotes


def test_recovery_requires_a_durable_safety_journal(tmp_path):
    path = tmp_path / "safety.json"
    settings = dict(max_drawdown_bps=Decimal("500"),
                    min_margin_buffer_quote=Decimal("10"), stable_data_ms=5000,
                    recovery_probe_base=Decimal("1"))
    with pytest.raises(ValueError, match="SAFETY_JOURNAL_UNAVAILABLE"):
        SafetyGate(path, require_existing=True, **settings)

    fresh = SafetyGate(path, **settings)
    assert fresh.evaluate(observe(at=0)).reason_code == "SAFETY_JOURNAL_UNAVAILABLE"
    assert not fresh.evaluate(observe(at=1)).allow_new_quotes
    fresh.initialize_empty()
    with pytest.raises(ValueError, match="SAFETY_JOURNAL_ALREADY_EXISTS"):
        fresh.initialize_empty()
    recovered = SafetyGate(path, require_existing=True, **settings)
    assert recovered.state == "PAUSED"
    assert not recovered.evaluate(observe(at=1)).allow_new_quotes
    assert recovered.reason_code == "MANUAL_REARM_REQUIRED"
    with pytest.raises(ValueError, match="SAFETY_REARM_PROOF_REQUIRED"):
        recovered.arm_after_reconciliation(reconciled=False, operator_id="operator")
    recovered.arm_after_reconciliation(reconciled=True, operator_id="operator")
    assert not recovered.evaluate(observe(at=2)).allow_new_quotes

    recovered.halt("MANUAL_KILL_SWITCH")
    assert SafetyGate(path, require_existing=True, **settings).state == "HALTED"
    path.unlink()
    with pytest.raises(ValueError, match="SAFETY_JOURNAL_UNAVAILABLE"):
        SafetyGate(path, require_existing=True, **settings)


def test_corrupt_safety_journal_fails_closed_on_recovery(tmp_path):
    path = tmp_path / "safety.json"
    path.write_text('{"schema_version": 1, "halted": "yes"}')
    with pytest.raises(ValueError, match="safety journal invalid"):
        SafetyGate(path, require_existing=True, max_drawdown_bps=Decimal("500"),
                   min_margin_buffer_quote=Decimal("10"), stable_data_ms=0,
                   recovery_probe_base=Decimal("1"))


def test_halt_checkpoint_failure_latches_memory_and_revokes_permission(tmp_path):
    gate = SafetyGate(tmp_path / "safety.json", max_drawdown_bps=Decimal("500"),
                      min_margin_buffer_quote=Decimal("10"), stable_data_ms=0,
                      recovery_probe_base=Decimal("1"))
    gate.initialize_empty()
    gate.evaluate(observe(at=1))
    assert gate.evaluate(observe(at=2)).state == "NORMAL"

    with patch.object(gate, "_persist_halt", side_effect=OSError("disk failed")):
        with pytest.raises(OSError, match="disk failed"):
            gate.evaluate(observe(at=3, drawdown="501"))

    assert gate.state == "HALTED"
    assert not gate.evaluate(observe(at=4)).allow_new_quotes
    assert not gate.journal_verified()
    gate.halt("DRAWDOWN_LIMIT_BREACHED")
    assert gate.journal_verified()


def test_stale_safety_gate_cannot_quote_after_another_gate_halts(tmp_path):
    path = tmp_path / "safety.json"
    settings = dict(max_drawdown_bps=Decimal("500"),
                    min_margin_buffer_quote=Decimal("10"), stable_data_ms=0,
                    recovery_probe_base=Decimal("1"))
    first = SafetyGate(path, **settings)
    first.initialize_empty()
    stale = SafetyGate(path, require_existing=True, **settings)
    stale.arm_after_reconciliation(reconciled=True, operator_id="operator")
    stale.evaluate(observe(at=1))
    assert stale.evaluate(observe(at=2)).state == "NORMAL"

    first.halt("MANUAL_KILL_SWITCH")

    decision = stale.evaluate(observe(at=3))
    assert decision.state == "PAUSED"
    assert decision.reason_code == "SAFETY_JOURNAL_UNAVAILABLE"
    assert not decision.allow_new_quotes


def test_failed_halt_cannot_auto_recover_from_the_previous_clear_journal(tmp_path):
    path = tmp_path / "safety.json"
    settings = dict(max_drawdown_bps=Decimal("500"),
                    min_margin_buffer_quote=Decimal("10"), stable_data_ms=0,
                    recovery_probe_base=Decimal("1"))
    running = SafetyGate(path, **settings)
    running.initialize_empty()
    running.evaluate(observe(at=1))
    assert running.evaluate(observe(at=2)).state == "NORMAL"
    with patch.object(running, "_persist_halt", side_effect=OSError("disk unavailable")):
        with pytest.raises(OSError, match="disk unavailable"):
            running.halt("MANUAL_KILL_SWITCH")

    recovered = SafetyGate(path, require_existing=True, **settings)
    assert recovered.evaluate(observe(at=3)).reason_code == "MANUAL_REARM_REQUIRED"
    assert not recovered.evaluate(observe(at=4)).allow_new_quotes
    with pytest.raises(ValueError, match="SAFETY_REARM_PROOF_REQUIRED"):
        recovered.arm_after_reconciliation(reconciled=True, operator_id="")
