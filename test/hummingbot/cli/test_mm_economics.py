"""Economic replay must not manufacture fills, cash or a profitable baseline."""
from decimal import Decimal as D

import pytest


def test_touch_does_not_fill_and_queue_consumes_flow_once():
    from test.e2e.offline.mm_economics import PassiveOrder, match_flow
    orders = [PassiveOrder(str(i), "spot", "BUY", D(".1"), D("10"), 0, 1, D("5")) for i in range(2)]
    assert not match_flow(orders, now=0, aggressor="SELL", limit=D(".09"), quantity=D("100"), fraction=D(".5"))
    assert not match_flow(orders, now=1, aggressor="SELL", limit=D(".1"), quantity=D("100"), fraction=D(".5"))
    fills = match_flow(orders, now=2, aggressor="SELL", limit=D(".09"), quantity=D("12"), fraction=D(".5"))
    # A partial best-price fill cannot reuse leftover flow at a worse level.
    assert [(order.id, amount) for order, amount in fills] == [("0", D("5"))]
    assert orders[1].queue_ahead == D("5")


def test_price_priority_cancel_latency_and_no_fill_after_terminal():
    from test.e2e.offline.mm_economics import PassiveOrder, match_flow
    worse = PassiveOrder("worse", "spot", "BUY", D(".09"), D("10"), 0, 0, D("0"))
    better = PassiveOrder("better", "spot", "BUY", D(".1"), D("10"), 0, 0, D("0"))
    better.cancel_at = 3
    fills = match_flow([worse, better], now=2, aggressor="SELL", limit=D(".08"), quantity=D("2"), fraction=D("1"))
    assert [(order.id, amount) for order, amount in fills] == [("better", D("2"))]
    fills = match_flow([worse, better], now=3, aggressor="SELL", limit=D(".08"), quantity=D("2"), fraction=D("1"))
    assert [(order.id, amount) for order, amount in fills] == [("worse", D("2"))]


def test_exact_wallet_equity_hold_and_signed_funding():
    from test.e2e.offline.mm_economics import Wallet
    wallet = Wallet(cash=D("100"), base=D("1000"), initial_mark=D(".1"))
    wallet.fill("a", "spot", "BUY", D("2"), D(".1"), D(".01"))
    wallet.fill("b", "perpetual", "SELL", D("3"), D(".1"), D(".02"))
    assert wallet.cash == D("99.77") and wallet.base == D("1002")
    wallet.fund(D(".1"), D(".01"))  # Positive funding: short receives.
    assert wallet.funding == D(".003")
    assert wallet.equity(D(".08")) == D("179.993")
    assert wallet.equity(D(".08")) - wallet.hold_equity(D(".08")) == D("-.007")
    with pytest.raises(ValueError, match="duplicate"):
        wallet.fill("a", "spot", "BUY", D("2"), D(".1"), D(".01"))


def test_spot_no_short_and_markouts_are_side_signed_and_tail_censored():
    from test.e2e.offline.mm_economics import Wallet, markouts
    wallet = Wallet(cash=D("1"), base=D("0"), initial_mark=D(".1"))
    with pytest.raises(ValueError, match="balance"):
        wallet.fill("a", "spot", "SELL", D("1"), D(".1"), D("0"))
    fills = [dict(at=0, side="BUY", price=".1", amount="1"),
             dict(at=1, side="SELL", price=".1", amount="1")]
    result = markouts(fills, {0: D(".1"), 1: D(".09"), 2: D(".08")}, (1, 5))
    assert result["1"]["sum_quote"] == "0.01" and result["1"]["observed"] == 2
    assert result["5"]["observed"] == 0 and result["5"]["mean_quote"] is None
    assert result["5"]["censored"] == 2


@pytest.mark.asyncio
async def test_independent_exchange_weights_entry_prices_and_marks_short_with_correct_sign():
    from test.e2e.offline.exchange import Exchange
    exchange = Exchange(fixtures={"balances": {"data": [{"details": []}]},
                                  "order_event": {"arg": {}, "data": [{}]}})
    for index, price in enumerate((".1", ".2")):
        order = dict(instId="LIFE-USDT-SWAP", clOrdId=str(index), ordId=str(index),
                     side="sell", posSide="short", sz="100", px=price, accFillSz="0", state="live")
        exchange.orders[str(index)] = order
        await exchange.control({"fill": {"clOrdId": str(index), "amount": "100", "fee": "0"}})
    position = exchange.positions[("LIFE-USDT-SWAP", "short")]
    assert D(position["avgPx"]) == D(".15")
    assert D(position["upl"]) == D(".1")  # Two base short, mark .1.
