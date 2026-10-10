"""Reproducible O.5 spot research fixtures. Never connects to an exchange.

Run with the Hummingbot test runtime:
python -m test.hummingbot.strategy_v2.life_liquidity.o5_scenarios --output report.json
"""

import argparse
import asyncio
import json
import tempfile
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from test.hummingbot.strategy_v2.life_liquidity.offline_market import disconnect, install_market
from test.hummingbot.strategy_v2.life_liquidity.test_capital_risk_binding import _nav
from test.hummingbot.strategy_v2.life_liquidity.test_executor_protected_send import _setup
from test.hummingbot.strategy_v2.life_liquidity.test_fee_quote_binding import _binding, _costs, _fee
from test.hummingbot.strategy_v2.life_liquidity.test_final_quote_send import _attach_quote_planner
from test.hummingbot.strategy_v2.life_liquidity.test_markout_runtime_binding import _horizon, _monitor
from test.hummingbot.strategy_v2.life_liquidity.test_o2_turnover_replay import replay as turnover_replay
from test.hummingbot.strategy_v2.life_liquidity.test_reference import CORRELATION, POLICY, btc, engine, life
from test.hummingbot.strategy_v2.life_liquidity.test_request_budget import _budget
from test.hummingbot.strategy_v2.life_liquidity.test_session import FakeClock
from test.hummingbot.strategy_v2.life_liquidity.test_spot_session_replay import FakeTradingOkx, _runner
from test.hummingbot.strategy_v2.life_liquidity.test_subsidy_runtime_binding import NOW, NOW_MS
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from hummingbot.strategy_v2.life_liquidity.capital_risk import CapitalRiskMonitor
from hummingbot.strategy_v2.life_liquidity.fill_attribution import IndependentFillObservation, ReconciledFillAttributor
from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetLedger
from hummingbot.strategy_v2.life_liquidity.order_gateway import (
    OkxSpotOrderGateway,
    SpotAccountReconciler,
    SpotFill,
    SpotReservationReconciler,
)
from hummingbot.strategy_v2.life_liquidity.queue_latency_simulation import (
    QueueFillAssumptions,
    QueueFillEvent,
    QueueFillOrder,
    simulate_queue_fills,
)
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
from hummingbot.strategy_v2.life_liquidity.safety import SafetyGate, SafetyObservation
from hummingbot.strategy_v2.life_liquidity.spot_quotes import AdaptiveQuotePolicy, AdaptiveQuoteSignals
from hummingbot.strategy_v2.life_liquidity.spot_risk import SpotRiskBinding, SpotStressObservation
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL
from hummingbot.strategy_v2.life_liquidity.telemetry import TelemetryRecorder, liquidity_availability, risk_latency

D = Decimal
SEEDS = (7, 19)  # Frozen development/evaluation seeds; no fitting or economic calibration.
BUDGETS = {"block_ms": 5, "cancel_request_ms": 10, "confirmation_ms": 50}
REVOCATIONS = ("empty_book", "stale_book", "disconnect", "resync", "benchmark_spike",
               "benchmark_stale", "transient_depth", "latency_spike", "fee_regime",
               "rate_limit", "cancel_timeout", "delayed_cancel", "expiry_disconnected", "crash_restart")


@asynccontextmanager
async def system(directory):
    directory.mkdir(parents=True, exist_ok=True)
    connector = FakeTradingOkx()
    clock = FakeClock()
    clock.wall = NOW
    with patch("test.hummingbot.strategy_v2.life_liquidity.test_executor_protected_send.FakeClock", return_value=clock):
        c, template, _, wal, ledger, _ = _setup(directory, connector=connector, recovery_account_uid="12345")
    wal.initialize_empty()
    del c.allow_create_executor_actions
    feed = await install_market(c, connector, exchange_ms=NOW_MS)
    watchdog = asyncio.create_task(asyncio.Event().wait())
    state = {"elapsed": 0, "utc": NOW_MS, "value": "1", "fill_value": "1", "latency_ok": True}
    c._order_safety_manager.wall_clock = lambda: datetime.fromtimestamp(
        (NOW_MS + state["elapsed"]) / 1000, timezone.utc)
    c._order_safety_manager.monotonic_clock = lambda: 100 + state["elapsed"] / 1000
    c.snapshot_gate.clock = lambda: 100 + state["elapsed"] / 1000
    session = c._order_safety_manager.current_session
    loss = LossBudgetLedger(directory / "loss_budget.json", campaign_id="life",
                            campaign_limit_quote=D("1"), day_limit_quote=D("1"), session_limit_quote=D("1"))
    loss.record("opening", D("0"), session_id=session.session_id, at_utc=NOW)
    attribution = ReconciledFillAttributor(
        directory / "fill_attribution.json", wal=wal, reservations=ledger, loss_budget=loss,
        opening_life=D("10"), opening_usdt=D("10"), opening_independent_price_usdt=D("1"),
        independent_value=lambda fill: IndependentFillObservation(
            D(state["fill_value"]), fill.fill_at_ms, fill.fill_at_ms, "independent_market"),
        max_reference_skew_ms=200, create=True)
    reconciler = SpotReservationReconciler(wal, ledger, require_fees=True)
    gateway = OkxSpotOrderGateway(
        connector, wal, trading_pair="LIFE-USDT", clock=lambda: c._order_safety_manager.wall_clock(),
        apply_fills=reconciler.apply_fills, confirm_terminal=reconciler.confirm_terminal,
        on_cancel_requested=reconciler.request_cancel, on_unknown=reconciler.mark_unknown,
        account_check=SpotAccountReconciler(connector, ledger).check)
    c.install_order_safety(c._order_safety_manager, gateway, wal, reservations=ledger)
    c.order_safety_watchdog_task = watchdog
    c.install_execution_loss_budget(loss, utc_clock=lambda: NOW)
    c.install_fill_attributor(attribution)
    gate = SafetyGate(directory / "safety.json", max_drawdown_bps=D("500"),
                      min_margin_buffer_quote=D("0"), stable_data_ms=0, recovery_probe_base=D("1"))
    gate.initialize_empty()
    c.install_runtime_risk_gate(
        gate, observation=lambda: SafetyObservation(100 + state["elapsed"], True, state["latency_ok"],
                                                    True, True, D("0"), D("100")),
        monotonic_clock_ms=lambda: 100 + state["elapsed"], max_observation_age_ms=5)

    def nav():
        return _nav(state["value"], value_at=state["utc"], observed_at=state["utc"])
    capital = CapitalRiskMonitor(directory / "capital_risk.json", attributor=attribution,
                                 independent_value=nav, utc_clock_ms=lambda: NOW_MS + state["elapsed"],
                                 max_value_age_ms=5000, create=True)
    c.install_capital_risk_monitor(capital)
    risk = c.config.strategy.risk.model_copy(update={"rolling_fill_window": "1s",
                                                     "max_filled_base_per_window": D("1"),
                                                     "stress_loss_budget_quote": D("0.5")})
    economics = c.config.strategy.economics.model_copy(update={"fee_policy": "pause", "fee_max_age": "1s"})
    c.config = c.config.model_copy(update={"strategy": c.config.strategy.model_copy(update={
        "risk": risk, "economics": economics})})
    stress = SpotRiskBinding(
        directory / "spot_risk.json", attributor=attribution, utc_clock_ms=lambda: state["utc"],
        window_ms=1000, max_filled_base=D("1"), target_inventory_base=D("10"),
        max_stress_loss_quote=D("0.5"), max_observation_age_ms=2000, create=True,
        stress_observation=lambda: SpotStressObservation(
            state["utc"], state["utc"], "independent_market", D(state["value"]),
            D(state["value"]) * D("0.99"), D(state["value"]) * D("1.01"), D("20"),
            D("10"), D("0.0008"), D("0.001")))
    c.install_spot_risk_binding(stress)
    fee = {"now": 2000500, "fee": _fee()}
    quote, _ = _attach_quote_planner(c, template, wal, ledger, propose=False, fee_binding=_binding(fee))
    c._quote_action_planner.monotonic_clock = lambda: 100 + state["elapsed"] / 1000
    quote["snapshot"] = replace(quote["snapshot"], costs=replace(
        _costs(), impact_cost_quote=D("0.0001"), carry_cost_quote=D("0.0001"),
        inventory_risk_quote=D("0.0001"), uncertainty_bps=D("1")),
        loss_budget_status=c.execution_loss_status)
    assert not c.allow_create_executor_actions()
    assert c.allow_create_executor_actions()
    quote["snapshot"] = replace(quote["snapshot"], loss_budget_status=c.execution_loss_status)
    actions = c.determine_executor_actions()
    assert len(actions) == 1, (c._quote_action_planner.reason_code, c._quote_action_planner.fee_reason_code,
                               c._quote_action_planner.last_plan)
    executors = []
    runner = _runner(c, connector, executors, actions[0].executor_config)
    log = TelemetryRecorder(directory / "telemetry.jsonl", clock_ms=lambda: 100000 + state["elapsed"],
                            synthetic=True, max_records=1000, create=True)
    c.install_telemetry(log, independent_value=nav, utc_clock_ms=lambda: NOW_MS + state["elapsed"], max_value_age_ms=5000)
    fixture = SimpleNamespace(c=c, connector=connector, wal=wal, ledger=ledger, feed=feed, state=state,
                              gate=gate, quote=quote, fee=fee, runner=runner, log=log,
                              gateway=gateway, attribution=attribution, capital=capital, loss=loss,
                              config=actions[0].executor_config, session=session, executors=executors)
    try:
        yield fixture
    finally:
        watchdog.cancel()
        runner.listen_to_executor_actions_task.cancel()
        if c.order_safety_task is not None:
            await c.order_safety_task
        await asyncio.gather(watchdog, runner.listen_to_executor_actions_task, return_exceptions=True)


def wire(f):
    return {"clOrdId": f.connector.sent[0]["order_id"], "instId": "LIFE-USDT", "side": "buy",
            "ordType": "post_only", "tdMode": "cash", "px": str(f.config.price), "sz": str(f.config.amount)}


def canonical_rows(rows, session_id, wire_id):
    """Normalize only nondeterministic identity strings, preserving scoped equality."""
    encoded = json.dumps(rows, sort_keys=True).replace(session_id, "session-primary")
    if wire_id:
        encoded = encoded.replace(wire_id, "wire-primary")
    return json.loads(encoded)


def report(f):
    rows = canonical_rows(f.log.records(), f.session.session_id,
                          f.connector.sent[0]["order_id"] if f.connector.sent else None)
    # Deduplicate metric/input snapshots; every event points at its complete observation.
    snapshots, events = [], []
    for row in rows:
        snapshot = {key: row.pop(key) for key in ("metrics", "markouts", "inputs", "gates")}
        if snapshot not in snapshots:
            snapshots.append(snapshot)
        events.append({**row, "snapshot": snapshots.index(snapshot)})
    return {"events": events, "snapshots": snapshots,
            "orders_dispatched": len(f.connector.sent),
            "wal_state": f.wal.get(f.config.id).state if f.connector.sent else None,
            "reservation_retained": not f.ledger.is_terminal_intent(f.config.id)}


async def revocation(directory, case):
    async with system(directory) as f:
        f.runner.tick(1)
        assert len(f.connector.sent) == 1
        final = f.connector.sent[0]["pre_send_check"]
        final(wire(f))
        f.state["elapsed"] = 100
        f.log.begin_risk_event("risk-1", case.upper())
        benchmark = None
        if case == "empty_book":
            f.connector.get_order_book("LIFE-USDT").apply_snapshot([], [], 11)
        elif case == "stale_book":
            f.c.snapshot_gate.snapshot = replace(f.c.snapshot_gate.snapshot, received_monotonic=97)
        elif case in ("disconnect", "expiry_disconnected"):
            disconnect(f.feed)
            if case == "expiry_disconnected":
                f.c._order_safety_manager.wall_clock = lambda: f.session.expires_at
        elif case == "resync":
            f.feed["health"] = replace(f.feed["health"], epoch=2)
        elif case in ("benchmark_spike", "benchmark_stale"):
            market = life(ts=NOW_MS)
            component = btc(current="150000" if case == "benchmark_spike" else "100000",
                            ts=NOW_MS if case == "benchmark_spike" else NOW_MS - 2001)
            correlation = replace(CORRELATION, observed_until_ms=NOW_MS)
            decision = engine().evaluate("bounded_benchmark", execution_mode="simulation", market=market,
                                         components=(component,), influence=D("0.5"),
                                         exchange_now_ms=NOW_MS, correlation=correlation)
            if case == "benchmark_stale":
                assert decision.price_usdt == D("1") and decision.influence_applied == 0
            else:
                assert decision.price_usdt is None
            f.quote["snapshot"] = replace(f.quote["snapshot"], reference_ready=decision.price_usdt is not None)
            benchmark = {"policy": asdict(POLICY), "market": asdict(market),
                         "component": asdict(component), "correlation": asdict(correlation),
                         "exchange_now_ms": NOW_MS, "influence": "0.5", "decision": asdict(decision)}
        elif case == "transient_depth":
            f.quote["snapshot"] = replace(
                f.quote["snapshot"], adaptive_policy=AdaptiveQuotePolicy(
                    max_spread_bps=D("100"), max_depth_fraction=D("0.5"), target_inventory_base=D("10"),
                    inventory_band_base=D("5"), max_inventory_widen_bps=D("0")),
                adaptive_signals=AdaptiveQuoteSignals(D("0"), D("0"), D("0"), D("0"), D("2")))
        elif case == "latency_spike":
            f.state["latency_ok"] = False
        elif case == "fee_regime":
            f.fee["fee"] = _fee(maker="-0.02")
        elif case == "rate_limit":
            budget = _budget(directory, [NOW], capacity=2, reserve=1)
            budget.charge("STATUS", "already-status")
            f.c._protected_spot_sender.gateway.request_budget = budget
        else:
            f.gate.halt("OFFLINE_OPERATOR_STOP")
        f.state["elapsed"] = 103
        if case == "benchmark_stale":
            final(wire(f))  # Independent LIFE remains qualified; stale BTC influence becomes zero.
            f.log.flush()
            return {"case": case, "benchmark": benchmark, **report(f), "latency": risk_latency(
                100100, block_ms=None, cancel_request_ms=None, confirmed_ms=None,
                budgets=BUDGETS, synthetic=True)}
        try:
            final(wire(f))
        except PermissionError:
            pass
        else:
            raise AssertionError(f"{case}: final send was not revoked")
        assert len(f.connector.sent) == 1
        if case == "rate_limit":
            # Proven never-sent rejection releases the reservation; a previously
            # successful synthetic final check was not an actual REST write.
            assert f.wal.get(f.config.id).state == "ABORTED_BEFORE_SEND"
            f.log.flush()
            return {"case": case, "benchmark": benchmark, **report(f), "latency": risk_latency(
                100100, block_ms=100103, cancel_request_ms=None, confirmed_ms=None,
                budgets=BUDGETS, synthetic=True)}
        f.state["elapsed"] = 109
        if case == "cancel_timeout":
            f.connector.cancel_by_client_id = AsyncMock(side_effect=TimeoutError("synthetic timeout"))
        try:
            await f.gateway.request_cancel(f.session.session_id, f.session.epoch)
        except TimeoutError:
            pass
        # A cancel ACK alone must retain the reservation and unknown send.
        assert not f.ledger.is_terminal_intent(f.config.id)
        confirmed = None
        if case not in ("cancel_timeout", "expiry_disconnected", "crash_restart"):
            f.state["elapsed"] = 160 if case == "delayed_cancel" else 140
            f.connector.status[wire(f)["clOrdId"]] = {
                "clOrdId": wire(f)["clOrdId"], "ordId": "exchange-primary", "state": "canceled", "accFillSz": "0"}
            f.c._runner_halt_ok = f.c._halt_runner_orders()
            await f.gateway.reconcile(f.session.session_id, f.session.epoch)
            result = await f.gateway.reconcile(f.session.session_id, f.session.epoch)
            assert result.scope_complete and result.trade_events_reconciled
            assert f.ledger.is_terminal_intent(f.config.id)
            confirmed = 100000 + f.state["elapsed"]
        if case == "crash_restart":
            restored = ReservationLedger.restore(f.ledger.path, limits=f.ledger.limits)
            assert not restored.is_terminal_intent(f.config.id)
            assert IntentWAL(f.wal.path).get(f.config.id).cancel_requested
            assert SafetyGate(f.gate.path, max_drawdown_bps=D("500"), min_margin_buffer_quote=D("0"),
                              stable_data_ms=0, recovery_probe_base=D("1")).state == "HALTED"
        f.log.flush()
        restored_log = TelemetryRecorder(f.log.path, clock_ms=f.log.clock_ms, synthetic=True,
                                         max_records=1000, create=False)
        assert restored_log.records() == f.log.records()
        rows = f.log.records()
        block = next(row["at_ms"] for row in rows if row["stage"] == "FINAL_SEND" and row["allowed"] is False)
        cancel = next(row["at_ms"] for row in rows if row["stage"] == "CANCEL_REQUEST")
        confirmations = [row["at_ms"] for row in rows if row["stage"] == "EXCHANGE_CONFIRM"]
        assert (min(confirmations) if confirmations else None) == confirmed
        return {"case": case, "benchmark": benchmark, **report(f), "latency": risk_latency(
            100100, block_ms=block, cancel_request_ms=cancel, confirmed_ms=confirmed,
            budgets=BUDGETS, synthetic=True)}


def queue_case(seed, case):
    assumptions = QueueFillAssumptions(seed, 1, 3, D("0.1"), 20, 50, D("0.0008"))
    order = QueueFillOrder("BUY", D("0.99"), D("1"), 0, 100)
    events = {
        "no_trades": [],
        "candle_touch": [QueueFillEvent(30, "CANDLE_TOUCH", D("0.99"), D("100"))],
        "unqualified_print": [QueueFillEvent(30, "TRADE", D("0.99"), D("100"), "SELL", False)],
        "delayed_ack": [QueueFillEvent(19, "TRADE", D("0.99"), D("100"), "SELL", True)],
        "one_sided": [QueueFillEvent(30, "TRADE", D("0.99"), D("0.7"), "SELL", True)],
        "cancel_fill_race": [QueueFillEvent(149, "TRADE", D("0.99"), D("0.7"), "SELL", True),
                             QueueFillEvent(150, "TRADE", D("0.99"), D("100"), "SELL", True)],
        "slow_adverse": [QueueFillEvent(t, "TRADE", D("0.99"), D("0.1"), "SELL", True)
                         for t in (25, 45, 65, 85, 105, 125, 145)],
    }[case]
    return assumptions, order, events, simulate_queue_fills(assumptions, order, events)


async def fill_scenario(directory, seed, case):
    assumptions, order, events, result = queue_case(seed, case)
    async with system(directory) as f:
        f.runner.tick(1)
        f.connector.sent[0]["pre_send_check"](wire(f))
        f.state["elapsed"] = 20
        f.connector.sent[0]["on_ack"]("exchange-primary")
        f.state["fill_value"] = "0.9"
        previous = D("0")
        trades, fills = [], []
        cancellation_requested = False
        for index, event in enumerate(events):
            if event.observed_ms >= 100 and not cancellation_requested:
                f.state["elapsed"] = 100
                await f.gateway.request_cancel(f.session.session_id, f.session.epoch)
                cancellation_requested = True
            f.state["elapsed"] = max(20, event.observed_ms)
            prefix = simulate_queue_fills(assumptions, order, events[:index + 1])
            quantity = prefix.filled_base - previous
            if quantity > 0:
                # Only qualified external-print model output enters the real fill reconciler.
                trade_id = f"trade-{index}"
                fee = quantity * f.config.price * assumptions.maker_fee_rate
                fill = SpotFill(trade_id, quantity, f.config.price, "USDT", -fee, NOW_MS + event.observed_ms)
                fills.append(fill)
                assert f.gateway.apply_fills(wire(f)["clOrdId"], tuple(fills), prefix.filled_base)
                trades.append((trade_id, event.observed_ms))
            previous = prefix.filled_base
        assert f.attribution.ready()
        f.state["elapsed"] = 400
        f.state["utc"] = NOW_MS + 400
        markout_state = {"now": f.state["utc"], "observations": {}}
        markout = _monitor(directory, f.attribution, markout_state)
        f.c.install_markout_monitor(markout)
        assert not f.c.allow_create_executor_actions()  # Missing or pending evidence never becomes zero.
        pending = markout.summary("BUY", "small", 1000)
        assert pending.mean_markout_quote is None
        f.c._record_telemetry("SNAPSHOT", reason_code="PENDING_MARKOUT")
        for trade_id, at_ms in trades:
            markout_state["observations"][(trade_id, 1000)] = _horizon(
                "0.8", value_at=NOW_MS + at_ms + 1000, observed_at=NOW_MS + at_ms + 1000)
        f.state["elapsed"] = 1200
        f.state["utc"] = NOW_MS + 1200
        markout_state["now"] = f.state["utc"]
        assert not markout.evaluate()
        if trades:
            assert markout.reason_code == "MARKOUT_ADVERSE"
        f.state["value"] = "0.8"
        assert not f.c.allow_create_executor_actions()
        assert f.gate.state == "HALTED"
        f.c._record_telemetry("SNAPSHOT", reason_code="ADVERSE_NAV")
        f.log.flush()
        measured = f.capital.measure()
        assert measured is not None
        assert not f.ledger.is_terminal_intent(f.config.id)
        return {"case": case, "seed": seed, "queue_model": asdict(result),
                "capital": asdict(measured), "markout_reason": markout.reason_code,
                "fill_count": len(trades), **report(f)}


async def run_report(directory):
    output = {"schema_version": 1, "scope": "SPOT_ONLY", "quality": "synthetic",
              "economic_viability": "INSUFFICIENT_EVIDENCE_UNCALIBRATED_SIMULATION",
              "identity_normalization": "session-primary and wire-primary preserve equality within each case",
              "cost_policy": "maker=0.0008, exit=0.001, impact/carry/inventory=0.0001 USDT each per candidate, uncertainty=1 bps; no rebates assumed",
              "latency_budgets": {"quality": "synthetic", "unit": "ms", **BUDGETS},
              "seeds": {"development": SEEDS[0], "evaluation": SEEDS[1]},
              "clock_domains": {"telemetry_monotonic_origin_ms": 100000, "utc_origin_ms": NOW_MS,
                                "runtime_risk_origin_ms": 100, "book_monotonic_origin_s": 100,
                                "fee_exchange_now_ms": 2000500,
                                "session_wall_origin": NOW.isoformat()},
              "policy": {"opening_life": "10", "opening_usdt": "10", "opening_value_usdt": "1",
                         "drawdown_halt_bps": "500", "stress_loss_cap_usdt": "0.5",
                         "execution_loss_cap_usdt": "1", "rolling_fill_cap_life": "1",
                         "rolling_window_ms": 1000, "session_duration_ms": 10000},
              "cancellation_driver": "explicit gateway request scheduled at event+9ms; not a production scheduling guarantee",
              "revocations": [], "fills": [], "turnover": [],
              "out_of_scope": ["funding_regime", "basis_shock", "spot_perpetual_divergence"],
              "availability": {key: metric.payload() for key, metric in liquidity_availability(
                  [(0, 100, True, None), (100, 200, False, False)], synthetic=True).items()}}
    for case in REVOCATIONS:
        output["revocations"].append(await revocation(directory / case, case))
    for seed in SEEDS:
        for case in ("no_trades", "candle_touch", "unqualified_print", "delayed_ack",
                     "one_sided", "cancel_fill_race", "slow_adverse"):
            output["fills"].append(await fill_scenario(directory / f"{case}-{seed}", seed, case))
    for cycles in (1, 100):
        clock = FakeClock()
        clock.wall = NOW
        with patch("test.hummingbot.strategy_v2.life_liquidity.test_executor_protected_send.FakeClock", return_value=clock):
            capital, loss, subsidy, filled = await turnover_replay(directory / f"turnover-{cycles}", cycles)
        output["turnover"].append({"case": "net_flat_loss", "cycles": cycles,
                                   "fill_source": "explicit synthetic exchange history; not queue-derived",
                                   "drawdown_halt_bps": "4", "capital": asdict(capital), "loss": asdict(loss),
                                   "subsidy_quote": subsidy, "filled_base": filled})
    # Keep complete trace snapshots once across cases; events preserve their index.
    shared = []
    for case in output["revocations"] + output["fills"]:
        local = case.pop("snapshots")
        indices = []
        for snapshot in local:
            if snapshot not in shared:
                shared.append(snapshot)
            indices.append(shared.index(snapshot))
        for event in case["events"]:
            event["snapshot"] = indices[event["snapshot"]]
    metric_snapshots = []
    for snapshot in shared:
        metrics = snapshot["metrics"]
        if metrics not in metric_snapshots:
            metric_snapshots.append(metrics)
        snapshot["metrics"] = metric_snapshots.index(metrics)
    output["snapshots"] = shared
    output["metric_snapshots"] = metric_snapshots
    return json.loads(json.dumps(output, default=str, allow_nan=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="life-o5-") as temporary:
        payload = asyncio.run(run_report(Path(temporary)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
