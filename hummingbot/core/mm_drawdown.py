"""Identity for the existing V2 drawdown/checkpoint adapter; no PnL ledger."""
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MMDrawdownMarket(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    connector_name: Literal["okx", "okx_perpetual"]
    trading_pair: str = Field(pattern=r"^[A-Z0-9][A-Z0-9._]*-USDT$")


def drawdown_policy(scope, global_limit, controller_limit):
    if not scope or (global_limit is None and controller_limit is None):
        return None

    def amount(value):
        return format(Decimal(str(value)).normalize(), "f") if value is not None else None

    return dict(metric="covered_v2_report_drawdown", max_global_drawdown_quote=amount(global_limit),
                max_controller_drawdown_quote=amount(controller_limit),
                scope={identity: market.model_dump(mode="json") if isinstance(market, MMDrawdownMarket) else dict(market)
                       for identity, market in sorted(scope.items())})


def checkpoint_drawdown_status(checkpoint):
    policy = checkpoint.get("mm_drawdown_policy")
    if not isinstance(policy, dict):
        return None
    breach = checkpoint.get("mm_drawdown_breach") or {}
    result = dict(enabled=True, ready=False, stale=True, metric="covered_v2_report_drawdown",
                  reason_code=breach.get("reason_code", "drawdown_not_running"),
                  breached_controller_id=breach.get("controller_id"), observed_at=None,
                  global_pnl_quote=None, global_drawdown_quote=None,
                  fees="executor_and_held_trade_quote_fees", funding="excluded",
                  operator_action_required=bool(breach), scope={}, controllers={}, global_peak_quote=None,
                  max_global_drawdown_quote=None, max_controller_drawdown_quote=None)
    try:
        scope = {identity: MMDrawdownMarket.model_validate(market).model_dump(mode="json")
                 for identity, market in policy["scope"].items()}
        peak = Decimal(checkpoint["max_global_pnl"])
        peaks = {identity: Decimal(checkpoint["max_pnl_by_controller"][identity]) for identity in scope}
        limits = {name: Decimal(policy[name]) if policy[name] is not None else None
                  for name in ("max_global_drawdown_quote", "max_controller_drawdown_quote")}
        if (not peak.is_finite() or peak < 0 or any(not value.is_finite() or value < 0 for value in peaks.values())
                or any(value is not None and (not value.is_finite() or value <= 0) for value in limits.values())):
            raise ValueError("Invalid checkpoint observation")
        result.update(scope=scope, global_peak_quote=peak, **limits,
                      controllers={identity: dict(peak_quote=value, pnl_quote=None, drawdown_quote=None)
                                   for identity, value in peaks.items()})
    except (KeyError, TypeError, ValueError, ArithmeticError):
        result.update(reason_code="drawdown_checkpoint_invalid", operator_action_required=True)
    return result
