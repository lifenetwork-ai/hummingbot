"""P4 priority: HALT, pause/cancel, limited reduction, then new quotes."""

from dataclasses import replace
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.safety import SafetyGate, SafetyObservation


def observe(*, at=0, fresh=True, latency=True, account=True, model=True,
            drawdown="0", margin="100"):
    return SafetyObservation(at, fresh, latency, account, model,
                             Decimal(drawdown), Decimal(margin))


def test_quality_failure_blocks_risk_and_requests_cancel_then_stable_recovery(tmp_path):
    gate = SafetyGate(tmp_path / "safety.json", max_drawdown_bps=Decimal("500"),
                      min_margin_buffer_quote=Decimal("10"), stable_data_ms=5000,
                      recovery_probe_base=Decimal("1"))
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
    missing = gate.evaluate(observe(account=False))
    assert missing.reason_code == "ACCOUNT_DATA_UNAVAILABLE" and missing.cancel_required
    low_margin = gate.evaluate(observe(at=1, margin="9"))
    assert low_margin.state == "HALTED" and low_margin.reason_code == "MARGIN_BUFFER_BREACHED"


def test_non_boolean_readiness_never_grants_quote_permission(tmp_path):
    gate = SafetyGate(tmp_path / "safety.json", max_drawdown_bps=Decimal("500"),
                      min_margin_buffer_quote=Decimal("10"), stable_data_ms=0,
                      recovery_probe_base=Decimal("1"))
    invalid = replace(observe(at=100), account_ready=1)

    decision = gate.evaluate(invalid)

    assert decision.state == "PAUSED"
    assert decision.reason_code == "RISK_DATA_UNAVAILABLE"
    assert not decision.allow_new_quotes
