"""P5.10 synthetic repeated fills consume durable, shared quote capacity."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_spot_quotes import D, plan

from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetLedger
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, RiskLimits, SpotIntent


def _limits():
    return RiskLimits(D("0"), D("30"), D("100"), D("30"))


def _fill(ledger, trade_id, side, price):
    intent = SpotIntent(trade_id, side, D("1"), D(price), "s1", 1)
    assert ledger.reserve(intent, reference_price=D("1")).allowed
    assert ledger.record_fill(trade_id, trade_id, D("1"), D(price))
    ledger.confirm_terminal(trade_id, cumulative_filled=D("1"),
                            fills_reconciled=True, exchange_state="FILLED")


def test_one_sided_and_alternating_fills_share_campaign_capacity_after_restart(tmp_path):
    path = tmp_path / "reservations.json"
    ledger = ReservationLedger(life_balance=D("10"), usdt_balance=D("20"),
                               limits=_limits(), path=path)
    _fill(ledger, "buy-1", "BUY", "0.99")
    _fill(ledger, "sell-1", "SELL", "1.01")
    assert ledger.life_balance == D("10")  # round trip cannot restore fill capacity

    recovered = ReservationLedger.restore(path, limits=_limits())
    remaining = plan((D("0.98"), D("1.02")), reservations=recovered,
                     max_campaign_filled_base=D("3"))
    assert sum((item.quantity_base for item in remaining.candidates), D("0")) == D("1")
    assert remaining.depth_target_met is None

    _fill(recovered, "buy-2", "BUY", "0.99")
    exhausted = plan((D("0.98"), D("1.02")), reservations=recovered,
                     max_campaign_filled_base=D("3"), min_depth_base_per_side=D("1"))
    assert exhausted.candidates == ()
    assert exhausted.depth_target_met is False
    assert all(item.reason_code == "CAMPAIGN_FILL_CAPACITY_EXHAUSTED"
               for item in exhausted.rejections)


def test_unresolved_orders_use_capacity_before_any_new_quote(tmp_path):
    ledger = ReservationLedger(life_balance=D("10"), usdt_balance=D("20"),
                               limits=_limits(), path=tmp_path / "reservations.json")
    assert ledger.reserve(SpotIntent("open", "BUY", D("1"), D("0.99"), "s1", 1),
                          reference_price=D("1")).allowed
    result = plan((D("0.98"), D("1.02")), reservations=ledger,
                  max_campaign_filled_base=D("2"))
    assert sum((item.quantity_base for item in result.candidates), D("0")) <= D("1")


def test_adverse_alternating_losses_remain_exhausted_after_cooldown(tmp_path):
    at = datetime(2026, 10, 8, tzinfo=timezone.utc)
    path = tmp_path / "loss.json"
    losses = LossBudgetLedger(path, campaign_id="life", campaign_limit_quote=D("1"),
                              day_limit_quote=D("1"), session_limit_quote=D("1"))
    assert losses.record("buy-1", D("0.6"), session_id="s1", at_utc=at)
    assert losses.record("sell-1", D("0.5"), session_id="s1", at_utc=at)
    recovered = LossBudgetLedger(path, campaign_id="life", campaign_limit_quote=D("1"),
                                 day_limit_quote=D("1"), session_limit_quote=D("1"))
    after_cooldown = at + timedelta(days=1)
    result = plan((D("0.98"), D("1.02")),
                  loss_budget_status=recovered.status(session_id="s2", at_utc=after_cooldown))
    assert result.candidates == ()
    assert result.reason_code == "EXECUTION_LOSS_BUDGET_EXHAUSTED"
    assert recovered.status(session_id="s2", at_utc=after_cooldown).campaign_loss_quote == D("1.1")


def test_invalid_campaign_cap_fails_closed():
    result = plan((D("0.98"), D("1.02")),
                  max_campaign_filled_base=Decimal("NaN"))
    assert result.candidates == ()
    assert result.reason_code == "QUOTE_CAPACITY_INPUT_UNAVAILABLE"
