"""Joint offline diagnostics, separate from send authority and risk budgets."""
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.telemetry import METRIC_UNITS, OPTIONAL_METRIC_UNITS, Metric

D = Decimal
UNITS = {**METRIC_UNITS, **OPTIONAL_METRIC_UNITS}
KEYS = ("nav_quote", "adjusted_nav_quote", "net_pnl_quote", "drawdown_bps", "fees_quote",
        "funding_quote", "margin_quote", "hedge_cost_quote", "starting_inventory_pnl_quote",
        "inventory_pnl_quote", "other_inventory_pnl_quote", "fill_time_gross_edge_quote", "fill_time_net_edge_quote",
        "liquidation_value_quote", *OPTIONAL_METRIC_UNITS)


def collect_extension_metrics(controller, *, synthetic):
    """Read fresh joint accounting; a stale joint cut never falls back to spot NAV.

    Margin is observed maintenance requirement. Collateral is reported separately,
    never added to NAV. Hedge cost is a gross campaign hold, not realized PnL.
    Liquidation value stays unknown without a qualified joint executable exit.
    """
    quality = "synthetic" if synthetic else "verified"
    result = {key: Metric(None, UNITS[key], "missing", "JOINT_OBSERVATION_UNAVAILABLE") for key in KEYS}

    def put(key, value, reason):
        result[key] = Metric(D(value), UNITS[key], quality, reason)

    try:
        joint, attribution = controller._joint_recovery, controller._fill_attributor
        if joint is None or attribution is None or not attribution.ready():
            return result
        state = joint._fresh()
        obs = joint.capital.qualified_snapshot()
        anchor, valuation, bundle = state["anchor"], state["valuation"], state["bundle"]
        capital = attribution.capital()
        spot_fees = sum((f[3] for f in capital._fills.values()), D("0"))
        spot_edge = sum(((D("1") if f[0] == "BUY" else D("-1")) * (f[4] - f[2]) * f[1]
                        for f in capital._fills.values()), D("0"))
        swaps = [t for o in bundle["orders"] if o["claim"]["product"] == "SWAP" for t in o["trades"]]
        swap_fees = -sum((D(t["signed_fee"]) for t in swaps), D("0"))
        realized = sum((D(t["realized_pnl_quote"]) for t in swaps), D("0"))
        funding = sum((D(p["signed_amount_quote"]) for p in bundle["payments"]), D("0"))
        opening_upl = (D(anchor["long_base"]) * (D(anchor["mark_price_usdt"]) - D(joint._binding["opening_long_entry"]))
                       + D(anchor["short_base"]) * (D(joint._binding["opening_short_entry"]) - D(anchor["mark_price_usdt"])))
        opening_nav = D(anchor["spot_cash_quote"]) + D(anchor["spot_life_base"]) * D(anchor["index_price_usdt"]) + opening_upl
        net = D(valuation["adjusted_nav_quote"]) - opening_nav
        fees = spot_fees + swap_fees
        inventory = net - (spot_edge - fees + funding)
        starting = (D(anchor["spot_life_base"]) * (obs.index_price_usdt - D(anchor["index_price_usdt"]))
                    + (D(anchor["long_base"]) - D(anchor["short_base"])) * (obs.mark_price_usdt - D(anchor["mark_price_usdt"])))
        for name in ("nav_quote", "adjusted_nav_quote", "drawdown_bps"):
            put(name, valuation[name], "JOINT_WALLET_LIFE_INDEX_AND_SWAP_UPL")
        for name, value, reason in (
            ("net_pnl_quote", net, "JOINT_CASHFLOW_ADJUSTED_PNL"),
            ("swap_unrealized_pnl_quote", valuation["unrealized_quote"], "REMAINING_SWAP_MARK_UPL"),
            ("swap_realized_pnl_quote", realized, "RECONCILED_SWAP_CLOSE_PNL"),
            ("spot_fees_quote", spot_fees, "ACTUAL_CONVERTED_SPOT_FEES"),
            ("swap_fees_quote", swap_fees, "ACTUAL_SIGNED_SWAP_FEES"),
            ("fees_quote", fees, "ACTUAL_COMBINED_FEES_NOT_FUNDING"),
            ("funding_quote", funding, "SIGNED_SETTLED_FUNDING_NET_PAYMENT"),
            ("margin_quote", obs.maintenance_margin_quote, "OBSERVED_MAINTENANCE_REQUIREMENT"),
            ("shared_collateral_quote", obs.collateral_quote, "ONE_POOL_COLLATERAL_NOT_ADDED_TO_NAV"),
            ("swap_long_base", obs.long_base, "RECONCILED_SWAP_LONG_BASE"),
            ("swap_short_base", obs.short_base, "RECONCILED_SWAP_SHORT_BASE"),
            ("starting_inventory_pnl_quote", starting, "OPENING_HOLDINGS_MARK_CHANGE"),
            ("inventory_pnl_quote", inventory, "JOINT_PNL_RESIDUAL_AFTER_SPOT_EDGE_FEES_FUNDING"),
            ("other_inventory_pnl_quote", inventory - starting, "DERIVED_NONSTARTING_JOINT_RESIDUAL"),
            ("fill_time_gross_edge_quote", spot_edge, "SPOT_INDEPENDENT_FILL_TIME_EDGE"),
            ("fill_time_net_edge_quote", spot_edge - spot_fees, "SPOT_FILL_EDGE_NOT_JOINT_NAV_PNL"),
        ):
            put(name, value, reason)
        hedge = controller._hedge_coordinator
        if hedge is not None:
            with hedge.journal.locked() as history:
                hedge._validate_state(history)
                put("hedge_cost_quote", history["cost_hold"], "GROSS_HEDGE_CAMPAIGN_COST_HOLD")
    except Exception:
        return {key: Metric(None, UNITS[key], "missing", "JOINT_OBSERVATION_UNAVAILABLE") for key in KEYS}
    return result
