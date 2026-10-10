"""O.5 replay exercises real permission, runner, fill and reconciliation code."""

from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.o5_scenarios import REVOCATIONS, SEEDS, fill_scenario, revocation

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("case", REVOCATIONS)
async def test_adversarial_revocations_have_replayable_decisions_and_latency(tmp_path, case):
    result = await revocation(tmp_path / case, case)
    assert result["orders_dispatched"] == 1
    snapshots = result["snapshots"]
    for event in result["events"]:
        assert event["session_id"] in ("session-primary", None)
        assert event["reason_code"]
        if event["stage"] == "PERMISSION":
            gates = snapshots[event["snapshot"]]["gates"]
            # Reconstruct ordered short-circuit controller permission from the captured inputs.
            assert event["allowed"] == all(value is True for value in gates.values())
    if case == "benchmark_stale":
        assert result["benchmark"]["decision"]["influence_applied"] == 0
        assert result["benchmark"]["decision"]["price_usdt"] == Decimal("1")
        assert result["latency"]["block_ms"]["value"] is None
        return
    assert result["latency"]["block_ms"]["value"] == "3"
    if case == "delayed_cancel":
        assert result["latency"]["confirmation_ms"]["within_budget"] is False
    elif case in ("cancel_timeout", "expiry_disconnected", "crash_restart", "rate_limit"):
        assert result["latency"]["confirmation_ms"]["value"] is None
    else:
        assert result["latency"]["confirmation_ms"]["within_budget"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("case", ["no_trades", "candle_touch", "unqualified_print", "delayed_ack",
                                  "one_sided", "cancel_fill_race", "slow_adverse"])
async def test_external_print_queue_models_feed_real_accounting_without_fabricated_fills(tmp_path, seed, case):
    result = await fill_scenario(tmp_path / case, seed, case)
    filled = Decimal(result["queue_model"]["filled_base"])
    if case in ("no_trades", "candle_touch", "unqualified_print", "delayed_ack"):
        assert filled == 0
        assert result["fill_count"] == 0
    else:
        assert filled > 0
        assert result["markout_reason"] == "MARKOUT_ADVERSE"
    assert result["reservation_retained"]
    assert result["capital"]["adjusted_nav_quote"] < Decimal("20")
    latest = result["snapshots"][-1]["metrics"]
    assert latest["margin_quote"]["value"] is None
    assert latest["margin_quote"]["quality"] == "not_applicable"
    assert Decimal(latest["fees_quote"]["value"]) == result["queue_model"]["fee_quote"]
    assert Decimal(latest["fill_time_net_edge_quote"]["value"]) == (
        Decimal(latest["fill_time_gross_edge_quote"]["value"]) - Decimal(latest["fees_quote"]["value"]))
    assert Decimal(latest["other_inventory_pnl_quote"]["value"]) == filled * Decimal("-0.1")
    assert Decimal(latest["net_pnl_quote"]["value"]) == sum(Decimal(latest[key]["value"]) for key in (
        "starting_inventory_pnl_quote", "other_inventory_pnl_quote", "fill_time_net_edge_quote"))


@pytest.mark.asyncio
async def test_frozen_evidence_bundle_matches_fresh_replay(tmp_path):
    import json
    from pathlib import Path
    from test.hummingbot.strategy_v2.life_liquidity.o5_scenarios import run_report

    expected = json.loads((Path(__file__).resolve().parents[4]
                           / "docs/plans/evidence/life_o5_spot_replay.json").read_text())
    actual = await run_report(tmp_path)
    assert actual == expected
    assert actual["economic_viability"] == "INSUFFICIENT_EVIDENCE_UNCALIBRATED_SIMULATION"
    low, high = actual["turnover"]
    assert Decimal(high["filled_base"]) == Decimal(low["filled_base"]) * 100
    assert high["capital"] == low["capital"]
    assert high["loss"] == low["loss"]
    assert Decimal(high["subsidy_quote"]) == Decimal(low["subsidy_quote"]) == Decimal("0.01")
    assert actual["availability"]["eligible"]["value"] is None
