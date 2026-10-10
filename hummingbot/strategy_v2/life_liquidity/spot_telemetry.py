"""Read spot journals into telemetry without granting orders or updating monitors."""

from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.capital_risk import IndependentNavObservation
from hummingbot.strategy_v2.life_liquidity.telemetry import METRIC_UNITS, Metric

D = Decimal


def collect_spot_metrics(controller, *, utc_now_ms, independent_value,
                         max_value_age_ms, synthetic, monotonic_ms):
    quality = "synthetic" if synthetic else "verified"
    result = {key: Metric(None, unit, "missing", "NOT_OBSERVED") for key, unit in METRIC_UNITS.items()}
    markouts = {}

    def put(key, value, reason="JOURNAL_OBSERVATION"):
        result[key] = Metric(D(value), METRIC_UNITS[key], quality, reason)

    for name in ("margin_quote", "funding_quote", "hedge_cost_quote"):
        result[name] = Metric(None, "USDT", "not_applicable", "SPOT_ONLY")
    if controller.config.strategy.perpetual.enabled:
        for name in ("margin_quote", "funding_quote", "hedge_cost_quote"):
            result[name] = Metric(None, "USDT", "missing", "SWAP_OBSERVER_REQUIRED")
    manager = controller._order_safety_manager
    if manager is not None and manager.current_session is not None:
        remaining = (manager.current_session.expires_at - manager.wall_clock()).total_seconds()
        put("session_remaining_ms", D(str(max(0, remaining))) * 1000, "ABSOLUTE_SESSION_DEADLINE")
    book = controller.snapshot_gate.snapshot
    if book is not None and monotonic_ms >= int(book.received_monotonic * 1000):
        put("book_source_age_ms", monotonic_ms - int(book.received_monotonic * 1000), "MONOTONIC_SOURCE_AGE")
    value_valid = (type(utc_now_ms) is int and type(max_value_age_ms) is int and max_value_age_ms >= 0
                   and isinstance(independent_value, IndependentNavObservation)
                   and independent_value.source_kind == "independent_market"
                   and isinstance(independent_value.price_usdt, D) and independent_value.price_usdt.is_finite()
                   and independent_value.price_usdt > 0
                   and type(independent_value.value_at_ms) is int
                   and type(independent_value.observed_at_ms) is int
                   and 0 < independent_value.value_at_ms <= independent_value.observed_at_ms <= utc_now_ms)
    if value_valid:
        put("reference_source_age_ms", utc_now_ms - independent_value.value_at_ms, "UTC_SOURCE_AGE")
        value_valid = utc_now_ms - independent_value.value_at_ms <= max_value_age_ms
    ledger, wal = controller._order_safety_reservations, controller._order_safety_wal
    if ledger is not None and wal is not None:
        try:
            ledger.assert_healthy()
            from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
            from hummingbot.strategy_v2.life_liquidity.state import IntentWAL
            if (ReservationLedger.restore(ledger.path, limits=ledger.limits).reservation_snapshot()
                    != ledger.reservation_snapshot() or IntentWAL(wal.path).all_records() != wal.all_records()):
                raise ValueError("TELEMETRY_JOURNAL_CHANGED")
            put("inventory_base", ledger.life_balance)
            put("usdt_balance", ledger.usdt_balance)
            pending = ledger.preview().pending_orders()
            for side, name in (("BUY", "pending_buy_base"), ("SELL", "pending_sell_base")):
                put(name, sum((quantity for intent, quantity in pending if intent.side == side), D("0")))
            # ACK-backed remaining quantities are a journal view, not a guarantee
            # of externally executable or independently eligible liquidity.
            acked = {record.intent_id for record in wal.all_records()
                     if record.state == "ACKED" and not record.cancel_requested}
            quotes = [(intent, quantity) for intent, quantity in pending if intent.intent_id in acked]
            for side, name in (("BUY", "quoted_bid_depth_base"), ("SELL", "quoted_ask_depth_base")):
                put(name, sum((quantity for intent, quantity in quotes if intent.side == side), D("0")),
                    "ACK_JOURNAL_DEPTH_NOT_LIVE_GUARANTEE")
            bids = [intent.limit_price_usdt for intent, _ in quotes if intent.side == "BUY"]
            asks = [intent.limit_price_usdt for intent, _ in quotes if intent.side == "SELL"]
            if bids and asks and value_valid:
                put("quoted_spread_bps", (min(asks) - max(bids)) / independent_value.price_usdt * 10000)
        except Exception:
            for name in ("inventory_base", "usdt_balance", "pending_buy_base", "pending_sell_base"):
                result[name] = Metric(None, METRIC_UNITS[name], "missing", "JOURNAL_UNVERIFIED")
    attribution = controller._fill_attributor
    if attribution is not None and attribution.ready():
        capital = attribution.capital()
        # CapitalLedger has already applied persisted independent fee conversion.
        fees = sum((fill[3] for fill in capital._fills.values()), D("0"))
        gross = sum(((D("1") if fill[0] == "BUY" else D("-1"))
                     * (fill[4] - fill[2]) * fill[1] for fill in capital._fills.values()), D("0"))
        put("fees_quote", fees, "ACTUAL_CONVERTED_FILL_FEES")
        put("fill_time_gross_edge_quote", gross, "INDEPENDENT_FILL_TIME_EDGE")
        put("fill_time_net_edge_quote", gross - fees, "FILL_TIME_EDGE_NOT_NAV_PNL")
        put("execution_loss_quote", capital.execution_loss_quote)
        if value_valid:
            opening = capital.highwater_quote
            monitor = controller._capital_risk_monitor
            highwater_verified = monitor is not None and monitor._verified()
            if highwater_verified:
                capital.highwater_quote = D(monitor._state["highwater_quote"])
            measurement = capital.measure(independent_value.price_usdt, source_kind="independent_market")
            for name, value in (("nav_quote", measurement.nav_quote),
                                ("adjusted_nav_quote", measurement.adjusted_nav_quote),
                                ("net_pnl_quote", measurement.adjusted_nav_quote - opening),
                                ("starting_inventory_pnl_quote", measurement.starting_inventory_pnl_quote)):
                put(name, value, "INDEPENDENT_NAV_MARK_TO_MARKET")
            net_pnl = measurement.adjusted_nav_quote - opening
            # Spot ledger contains fills/converted fees and external flows only.
            # Keep the derived inventory residual explicit, never add markouts.
            inventory_pnl = net_pnl - (gross - fees)
            put("inventory_pnl_quote", inventory_pnl, "NAV_RECONCILED_INVENTORY_CONTRIBUTION")
            put("other_inventory_pnl_quote", inventory_pnl - measurement.starting_inventory_pnl_quote,
                "DERIVED_NONSTARTING_INVENTORY_RESIDUAL")
            if highwater_verified:
                put("drawdown_bps", measurement.drawdown_bps, "PERSISTED_HIGHWATER_NAV")
        else:
            for name in ("nav_quote", "adjusted_nav_quote", "net_pnl_quote", "starting_inventory_pnl_quote",
                         "drawdown_bps", "inventory_pnl_quote", "other_inventory_pnl_quote"):
                result[name] = Metric(None, METRIC_UNITS[name], "stale", "INDEPENDENT_VALUE_UNAVAILABLE")
    if manager is not None and manager.current_session is not None and type(utc_now_ms) is int:
        at = datetime.fromtimestamp(utc_now_ms / 1000, timezone.utc)
        loss = controller._execution_loss_budget
        if loss is not None:
            try:
                status = loss.verified_status(session_id=manager.current_session.session_id, at_utc=at)
                put("loss_budget_available_quote", min(
                    loss.session_limit_quote - status.session_loss_quote,
                    loss.day_limit_quote - status.day_loss_quote,
                    loss.campaign_limit_quote - status.campaign_loss_quote))
            except Exception:
                pass  # Preserve an explicit missing metric.
        planner = controller._quote_action_planner
        if planner is not None:
            if planner.subsidy_budget is not None:
                try:
                    status = planner.subsidy_budget.verified_status(session_id=manager.current_session.session_id, at_utc=at)
                    put("subsidy_committed_quote", status.campaign_committed_quote)
                    put("subsidy_available_quote", status.available_quote)
                except Exception:
                    pass
            elif controller.config.strategy.economics.objective == "profit_mm":
                for name in ("subsidy_committed_quote", "subsidy_available_quote"):
                    result[name] = Metric(None, "USDT", "not_applicable", "PROFIT_MM_OBJECTIVE")
            observed, plan = planner.last_plan_snapshot, planner.last_plan
            if (observed is not None and observed == planner.last_qualified_snapshot
                    and plan is not None and plan.candidates
                    and observed.observed_monotonic * 1000 <= monotonic_ms < observed.expires_monotonic * 1000):
                # Proposed economics are estimates, separate from actual fills/NAV.
                totals = dict.fromkeys(("quote_gross_edge_quote", "quote_maker_cost_quote", "quote_exit_cost_quote",
                                        "quote_impact_cost_quote", "quote_carry_cost_quote", "quote_inventory_risk_quote",
                                        "quote_uncertainty_cost_quote", "quote_net_edge_quote"), D("0"))
                for candidate in plan.candidates:
                    notional = candidate.quantity_base * observed.qualified_exit_value_usdt
                    totals["quote_gross_edge_quote"] += candidate.economics.gross_edge_quote
                    totals["quote_net_edge_quote"] += candidate.economics.net_edge_quote
                    totals["quote_maker_cost_quote"] += max(D("0"), observed.costs.maker_fee_rate) * candidate.price_usdt * candidate.quantity_base
                    totals["quote_exit_cost_quote"] += max(D("0"), observed.costs.exit_fee_rate) * notional
                    totals["quote_impact_cost_quote"] += observed.costs.impact_cost_quote
                    totals["quote_carry_cost_quote"] += observed.costs.carry_cost_quote
                    totals["quote_inventory_risk_quote"] += observed.costs.inventory_risk_quote
                    totals["quote_uncertainty_cost_quote"] += observed.costs.uncertainty_bps / 10000 * notional
                for name, value in totals.items():
                    put(name, value, "PLAN_CANDIDATE_COST_ESTIMATE")
    stress = controller._spot_risk_binding
    if stress is not None and ledger is not None:
        try:
            observed = stress.stress_observation()
            if (observed.source_kind == "independent_market"
                    and 0 <= utc_now_ms - observed.value_at_ms <= stress.max_age_ms
                    and observed.value_at_ms <= observed.observed_at_ms <= utc_now_ms
                    and observed.exit_depth_base >= ledger.life_balance
                    and observed.stressed_exit_usdt > 0 and 0 <= observed.exit_fee_rate < 1):
                put("liquidation_value_quote", ledger.usdt_balance + ledger.life_balance * observed.stressed_exit_usdt
                    * (1 - observed.exit_fee_rate), "STRESSED_SPOT_CASH_PLUS_EXIT_ESTIMATE")
        except Exception:
            pass
    monitor = controller._markout_monitor
    if monitor is not None:
        for side, size, horizon in monitor.monitored_cohorts:
            summary = monitor.summary(side, size, horizon)
            markouts[f"{side}:{size}:{horizon}"] = Metric(
                summary.mean_markout_quote, "USDT", quality if summary.mean_markout_quote is not None else "missing",
                summary.reason_code)
    return result, markouts


def collect_quote_inputs(controller):
    """Bounded decision inputs; never copy raw config, auth, or connector payloads."""
    result = {"runtime_reason": controller.runtime_risk_reason_code,
              "markout_reason": controller.markout_reason_code}
    planner = controller._quote_action_planner
    if planner is None:
        return result
    result.update(planner_reason=planner.reason_code, fee_reason=planner.fee_reason_code,
                  subsidy_reason=planner.subsidy_reason_code)
    observed = planner.snapshot()
    if observed is not None:
        result.update(reference_usdt=str(observed.qualified_reference_usdt),
                      exit_value_usdt=str(observed.qualified_exit_value_usdt),
                      best_bid_usdt=str(observed.best_bid_usdt), best_ask_usdt=str(observed.best_ask_usdt),
                      reference_ready=observed.reference_ready, all_gates_ready=observed.all_gates_ready,
                      market_reference_ready=observed.market_reference_ready,
                      observed_monotonic_ms=str(D(str(observed.observed_monotonic)) * 1000),
                      expires_monotonic_ms=str(D(str(observed.expires_monotonic)) * 1000),
                      min_net_edge_bps=str(observed.policy.min_net_edge_bps),
                      maker_fee_rate=str(observed.costs.maker_fee_rate),
                      exit_fee_rate=str(observed.costs.exit_fee_rate),
                      impact_cost_usdt=str(observed.costs.impact_cost_quote),
                      carry_cost_usdt=str(observed.costs.carry_cost_quote),
                      inventory_cost_usdt=str(observed.costs.inventory_risk_quote),
                      uncertainty_bps=str(observed.costs.uncertainty_bps))
        for source in (observed.adaptive_policy, observed.adaptive_signals):
            if source is not None:
                result.update({key: str(value) for key, value in asdict(source).items()})
    return result
