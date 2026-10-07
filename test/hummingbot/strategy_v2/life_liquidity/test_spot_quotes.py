"""P5.1 offline quote planning never places orders or mutates reservations."""

from decimal import Decimal

import pytest

from hummingbot.strategy_v2.life_liquidity.config import QuotesConfig
from hummingbot.strategy_v2.life_liquidity.economics import EconomicPolicy
from hummingbot.strategy_v2.life_liquidity.market_data import InstrumentRules
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, RiskLimits, SpotIntent
from hummingbot.strategy_v2.life_liquidity.spot_quotes import QuoteCosts, plan_spot_quotes


def D(value):
    return Decimal(value)


def ledger(*, gross="100", usdt="20", life="10"):
    return ReservationLedger(
        life_balance=D(life), usdt_balance=D(usdt),
        limits=RiskLimits(D("0"), D("30"), D(gross), D("30")))


def plan(book, **changes):
    values = dict(
        session_id="s1", epoch=1,
        qualified_reference_usdt=D("1"), qualified_exit_value_usdt=D("1"),
        best_bid_usdt=book[0], best_ask_usdt=book[1],
        quotes=QuotesConfig(spreads_bps=(D("30"),), sizes_base=(D("1.5"),)),
        rules=InstrumentRules(D("0.01"), D("0.5"), D("0.5")),
        costs=QuoteCosts(D("0"), D("0"), D("0"), D("0"), D("0"), D("0")),
        policy=EconomicPolicy("profit_mm", D("0")), reservations=ledger())
    values.update(changes)
    return plan_spot_quotes(**values)


def test_rounded_bid_ask_use_qualified_reference_and_never_cross_book():
    result = plan((D("0.98"), D("1.02")))
    levels = [(item.side, item.level, item.price_usdt, item.quantity_base)
              for item in result.candidates]
    assert levels == [("BUY", 0, D("0.99"), D("1.5")),
                      ("SELL", 0, D("1.01"), D("1.5"))]
    assert result.rejections == ()


def test_post_only_crossing_and_costly_quotes_are_reported_not_submitted():
    crossing = plan((D("1.01"), D("1.02")))
    assert [(item.side, item.price_usdt) for item in crossing.candidates] == [("BUY", D("0.99"))]
    assert crossing.rejections[0].reason_code == "QUOTE_WOULD_CROSS_BOOK"
    costly = plan((D("0.98"), D("1.02")),
                  costs=QuoteCosts(D("0.02"), D("0.02"), D("0"), D("0"), D("0"), D("0")))
    assert costly.candidates == ()
    assert all(item.reason_code == "NET_EDGE_BELOW_MINIMUM" for item in costly.rejections)


def test_multiple_levels_preview_shared_risk_without_mutating_real_ledger():
    reservations = ledger(gross="13")
    result = plan((D("0.98"), D("1.02")), reservations=reservations,
                  quotes=QuotesConfig(spreads_bps=(D("30"),), sizes_base=(D("2"),)))
    assert [item.side for item in result.candidates] == ["BUY"]
    assert result.rejections[0].reason_code == "GROSS_EXPOSURE_LIMIT"
    assert reservations.reservation_ids == frozenset()


def test_service_subsidy_is_consumed_only_for_candidates_that_pass_risk():
    result = plan((D("0.98"), D("1.02")),
                  policy=EconomicPolicy("liquidity_service", D("0")),
                  costs=QuoteCosts(D("0.02"), D("0.02"), D("0"), D("0"), D("0"), D("0")),
                  subsidy_remaining_quote=D("0.05"))
    assert len(result.candidates) == 1
    assert result.rejections[0].reason_code == "SUBSIDY_BUDGET_EXCEEDED"
    assert result.subsidy_proposed_quote > 0


def test_unqualified_market_inputs_fail_closed():
    result = plan((D("0.98"), D("1.02")), qualified_reference_usdt=None)
    assert result.candidates == ()
    assert result.reason_code == "QUOTE_MARKET_INPUT_UNAVAILABLE"


def test_depth_target_reports_shortfall_without_forcing_extra_quotes():
    result = plan((D("0.98"), D("1.02")), min_depth_base_per_side=D("2"))
    assert result.bid_depth_base == D("1.5")
    assert result.ask_depth_base == D("1.5")
    assert result.depth_target_met is False
    assert len(result.candidates) == 2


def test_lot_rounding_below_minimum_rejects_both_sides():
    result = plan((D("0.98"), D("1.02")),
                  quotes=QuotesConfig(spreads_bps=(D("30"),), sizes_base=(D("0.9"),)),
                  rules=InstrumentRules(D("0.01"), D("0.5"), D("1")))
    assert result.candidates == ()
    assert all(item.reason_code == "ORDER_BELOW_MINIMUM" for item in result.rejections)


def test_preview_counts_existing_unresolved_reservations():
    reservations = ledger(gross="13")
    existing = SpotIntent("existing", "BUY", D("2"), D("0.99"), "old", 1)
    assert reservations.reserve(existing, reference_price=D("1")).allowed
    result = plan((D("0.98"), D("1.02")), reservations=reservations,
                  quotes=QuotesConfig(spreads_bps=(D("30"),), sizes_base=(D("2"),)))
    assert result.candidates == ()
    assert all(item.reason_code == "GROSS_EXPOSURE_LIMIT" for item in result.rejections)
    assert reservations.reservation_ids == frozenset({"existing"})


def test_buy_size_tapers_with_inventory_and_pending_buys():
    reservations = ledger(life="14")
    existing = SpotIntent("existing", "BUY", D("1"), D("0.99"), "old", 1)
    assert reservations.reserve(existing, reference_price=D("1")).allowed
    result = plan((D("0.98"), D("1.02")), reservations=reservations,
                  quotes=QuotesConfig(
                      spreads_bps=(D("30"),), sizes_base=(D("2"),),
                      buy_taper_start_base=D("10"), buy_block_base=D("20")))
    assert [(item.side, item.quantity_base) for item in result.candidates] == [
        ("BUY", D("1")), ("SELL", D("2"))]
    assert reservations.reservation_ids == frozenset({"existing"})


def test_inventory_block_and_multiple_levels_preserve_hard_limits():
    quotes = QuotesConfig(
        spreads_bps=(D("30"), D("40")), sizes_base=(D("4"), D("4")),
        buy_taper_start_base=D("10"), buy_block_base=D("16"))
    blocked = plan((D("0.98"), D("1.02")), reservations=ledger(life="16"),
                   quotes=quotes)
    assert all(item.side == "SELL" for item in blocked.candidates)
    assert [item.reason_code for item in blocked.rejections] == [
        "INVENTORY_BUY_BLOCKED", "INVENTORY_BUY_BLOCKED"]
    tapered = plan((D("0.98"), D("1.02")), reservations=ledger(life="10"),
                   quotes=quotes)
    assert sum((item.quantity_base for item in tapered.candidates
                if item.side == "BUY"), D("0")) <= D("6")
    hard = plan((D("0.98"), D("1.02")), reservations=ledger(life="10", gross="11"),
                quotes=quotes)
    assert not [item for item in hard.candidates if item.side == "BUY"]
    assert any(item.reason_code == "GROSS_EXPOSURE_LIMIT" for item in hard.rejections)


@pytest.mark.parametrize("start,block", [
    (None, "20"), ("10", None), ("20", "20"), ("21", "20"), ("-1", "20")])
def test_inventory_policy_requires_an_explicit_valid_pair(start, block):
    with pytest.raises(ValueError):
        QuotesConfig(spreads_bps=(D("30"),), sizes_base=(D("1"),),
                     buy_taper_start_base=start, buy_block_base=block)


def test_repeated_buy_fills_shrink_new_bids_without_resetting_inventory():
    reservations = ledger(life="10")
    quotes = QuotesConfig(
        spreads_bps=(D("30"),), sizes_base=(D("4"),),
        buy_taper_start_base=D("10"), buy_block_base=D("16"))
    first = plan((D("0.98"), D("1.02")), reservations=reservations, quotes=quotes)
    first_buy = next(item for item in first.candidates if item.side == "BUY")
    assert first_buy.quantity_base == D("4")
    intent = SpotIntent("filled-buy", "BUY", D("4"), D("0.99"), "s1", 1)
    assert reservations.reserve(intent, reference_price=D("1")).allowed
    assert reservations.record_fill("filled-buy", "trade-1", D("4"), D("0.99"))
    reservations.confirm_terminal("filled-buy", cumulative_filled=D("4"),
                                  fills_reconciled=True, exchange_state="FILLED")
    second = plan((D("0.98"), D("1.02")), reservations=reservations, quotes=quotes)
    assert next(item for item in second.candidates if item.side == "BUY").quantity_base == D("1")
    assert reservations.life_balance == D("14")
