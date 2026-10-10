"""Diagnostics failures block new risk while cancellation stays independent."""

from dataclasses import replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.o5_scenarios import system, wire
from unittest.mock import patch

import pytest

from hummingbot.strategy_v2.life_liquidity.telemetry import TelemetryRecorder


@pytest.mark.asyncio
async def test_units_quality_cost_reconciliation_and_no_authority_from_missing_value(tmp_path):
    async with system(tmp_path) as f:
        f.runner.tick(1)
        f.connector.sent[0]["pre_send_check"](wire(f))
        assert f.c._record_telemetry("SNAPSHOT", reason_code="OBSERVED")
        metrics = f.log.records()[-1]["metrics"]
        assert metrics["nav_quote"]["value"] == "20"
        assert metrics["liquidation_value_quote"]["value"] == "19.89010"
        assert metrics["drawdown_bps"]["value"] == "0"
        costs = sum(Decimal(metrics[name]["value"]) for name in (
            "quote_maker_cost_quote", "quote_exit_cost_quote", "quote_impact_cost_quote",
            "quote_carry_cost_quote", "quote_inventory_risk_quote", "quote_uncertainty_cost_quote"))
        assert Decimal(metrics["quote_net_edge_quote"]["value"]) == (
            Decimal(metrics["quote_gross_edge_quote"]["value"]) - costs)
        f.c._telemetry_value = lambda: None
        assert f.c._record_telemetry("SNAPSHOT", reason_code="OBSERVATION_MISSING")
        metrics = f.log.records()[-1]["metrics"]
        assert metrics["nav_quote"]["value"] is None
        assert metrics["reference_source_age_ms"]["value"] is None
        assert metrics["nav_quote"]["quality"] == "stale"
        # An observation callback error also stays missing; it cannot inject zero NAV.

        def unavailable():
            raise OSError("unavailable")
        f.c._telemetry_value = unavailable
        assert f.c._record_telemetry("SNAPSHOT", reason_code="OBSERVATION_MISSING")
        assert f.log.healthy


@pytest.mark.asyncio
async def test_cancel_capture_never_samples_or_flushes_and_failed_flush_cannot_block_cancel(tmp_path):
    async with system(tmp_path) as f:
        f.runner.tick(1)
        with patch("controllers.generic.life_liquidity.collect_spot_metrics", side_effect=AssertionError("sampling")):
            with patch.object(f.log, "flush", side_effect=AssertionError("disk I/O")):
                await f.gateway.request_cancel(f.session.session_id, f.session.epoch)
        assert f.connector.cancels
        assert f.log.records()[-1]["stage"] == "CANCEL_REQUEST"
        assert f.log.records()[-1]["metrics"]["nav_quote"]["value"] is None
        f.log.path.unlink()
        with pytest.raises((OSError, ValueError)):
            f.log.flush()
        assert not f.c.allow_create_executor_actions()
        assert f.c.telemetry_reason_code == "TELEMETRY_UNAVAILABLE"
        await f.gateway.request_cancel(f.session.session_id, f.session.epoch)
        assert len(f.connector.cancels) == 2
        assert not f.ledger.is_terminal_intent(f.config.id)


@pytest.mark.asyncio
async def test_buffer_capacity_failure_revokes_final_send_and_queue_rejections_are_scoped(tmp_path):
    async with system(tmp_path) as f:
        f.runner.tick(1)
        final = f.connector.sent[0]["pre_send_check"]
        final(wire(f))
        f.log._policy["max_records"] = len(f.log.records())
        with pytest.raises(PermissionError):
            final(wire(f))
        assert not f.log.healthy
        assert len(f.connector.sent) == 1
    async with system(tmp_path / "reject") as f:
        f.feed["health"] = replace(f.feed["health"], connected=False)
        f.runner.tick(1)
        rejects = [row for row in f.log.records() if row["stage"] == "QUEUE_REJECT"]
        assert rejects[-1]["intent_id"] == f.config.id
        assert rejects[-1]["config_version"] == f.session.config_version
        assert rejects[-1]["metrics"]["reject_count"]["value"] == "1"
        assert not f.connector.sent


@pytest.mark.asyncio
async def test_opt_in_recorder_restore_cannot_restore_send_permission(tmp_path):
    async with system(tmp_path) as f:
        f.c._record_telemetry("SNAPSHOT", reason_code="OBSERVED")
        f.log.flush()
        restored = TelemetryRecorder(f.log.path, clock_ms=f.log.clock_ms, synthetic=True,
                                     max_records=1000, create=False)
        assert restored.records() == f.log.records()
        assert not type(f.c).trading_permissions_ready(f.c)
        with pytest.raises(ValueError, match="TELEMETRY_BINDING_INVALID"):
            f.c.install_telemetry(restored, independent_value=lambda: None,
                                  utc_clock_ms=lambda: 0, max_value_age_ms=1)


@pytest.mark.asyncio
async def test_actual_ack_and_account_confirmation_have_distinct_latency_metrics(tmp_path):
    async with system(tmp_path) as f:
        f.runner.tick(1)
        f.connector.sent[0]["pre_send_check"](wire(f))
        f.state["elapsed"] = 20
        f.connector.sent[0]["on_ack"]("exchange-primary")
        ack = f.log.records()[-1]
        assert ack["stage"] == "ACK"
        assert ack["metrics"]["ack_latency_ms"]["value"] == "20"
        assert ack["metrics"]["cancel_confirm_latency_ms"]["value"] is None
        f.state["elapsed"] = 100
        await f.gateway.request_cancel(f.session.session_id, f.session.epoch)
        assert not f.ledger.is_terminal_intent(f.config.id)
        f.state["elapsed"] = 140
        f.connector.status[wire(f)["clOrdId"]] = {
            "clOrdId": wire(f)["clOrdId"], "ordId": "exchange-primary", "state": "canceled", "accFillSz": "0"}
        f.c._runner_halt_ok = f.c._halt_runner_orders()
        await f.gateway.reconcile(f.session.session_id, f.session.epoch)
        await f.gateway.reconcile(f.session.session_id, f.session.epoch)
        confirmed = [row for row in f.log.records() if row["stage"] == "EXCHANGE_CONFIRM"][-1]
        assert confirmed["metrics"]["cancel_confirm_latency_ms"]["value"] == "40"
        assert f.ledger.is_terminal_intent(f.config.id)


@pytest.mark.asyncio
async def test_changed_quote_snapshot_cannot_mix_new_costs_with_old_plan_economics(tmp_path):
    async with system(tmp_path) as f:
        old = f.quote["snapshot"]
        f.quote["snapshot"] = replace(old, costs=replace(old.costs, impact_cost_quote=Decimal("0.0002")))
        assert f.c._quote_action_planner.session_snapshot() is not None
        assert f.c._record_telemetry("SNAPSHOT", reason_code="CHANGED_PLAN_INPUTS")
        metric = f.log.records()[-1]["metrics"]["quote_net_edge_quote"]
        assert metric["value"] is None
        assert metric["quality"] == "missing"
