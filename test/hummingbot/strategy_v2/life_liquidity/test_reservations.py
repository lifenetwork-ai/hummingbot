"""P4.1-P4.4: conservative, atomic spot reservations."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, RiskLimits, SpotIntent


def D(value):
    return Decimal(value)


def ledger(*, life="100", usdt="100", min_life="0", max_life="200",
           gross="1000", net="200"):
    return ReservationLedger(life_balance=D(life), usdt_balance=D(usdt),
                             limits=RiskLimits(D(min_life), D(max_life), D(gross), D(net)))


def intent(key, side, quantity, price="1"):
    return SpotIntent(key, side, D(quantity), D(price), "session-a", 1)


def test_balances_reserve_each_side_without_assuming_opposite_side_fills():
    book = ledger(usdt="100", life="100")
    assert book.reserve(intent("b1", "BUY", "80"), reference_price=D("1")).allowed
    assert book.reserve(intent("s1", "SELL", "80"), reference_price=D("1")).allowed
    buy = book.reserve(intent("b2", "BUY", "30"), reference_price=D("1"))
    sell = book.reserve(intent("s2", "SELL", "30"), reference_price=D("1"))
    assert buy.reason_code == "INSUFFICIENT_USDT"
    assert sell.reason_code == "INSUFFICIENT_LIFE"
    assert book.reserved_usdt == D("80")
    assert book.reserved_life == D("80")


def test_inventory_gross_and_worst_case_net_limits_are_separate():
    max_inventory = ledger(max_life="105")
    assert max_inventory.reserve(intent("b", "BUY", "6"), reference_price=D("1")).reason_code == "INVENTORY_MAX"
    min_inventory = ledger(min_life="95")
    assert min_inventory.reserve(intent("s", "SELL", "6"), reference_price=D("1")).reason_code == "INVENTORY_MIN"
    gross = ledger(gross="110")
    assert gross.reserve(intent("b", "BUY", "11"), reference_price=D("1")).reason_code == "GROSS_EXPOSURE_LIMIT"
    net = ledger(net="105")
    assert net.reserve(intent("b", "BUY", "6"), reference_price=D("1")).reason_code == "NET_EXPOSURE_LIMIT"


def test_partial_fill_converts_reservation_once_and_pending_cancel_keeps_remainder():
    book = ledger()
    assert book.reserve(intent("b", "BUY", "50"), reference_price=D("1")).allowed
    assert book.record_fill("b", "trade-1", D("10"), D("0.9"))
    assert not book.record_fill("b", "trade-1", D("10"), D("0.9"))
    assert book.life_balance == D("110")
    assert book.usdt_balance == D("91")
    assert book.reserved_usdt == D("40")
    book.request_cancel("b")
    assert book.reserved_usdt == D("40")
    book.mark_unknown("b")
    assert book.requires_reconciliation("b")
    assert book.reserved_usdt == D("40")
    book.confirm_terminal("b", cumulative_filled=D("10"), fills_reconciled=True,
                          exchange_state="CANCELED")
    assert book.reserved_usdt == 0
    assert not book.requires_reconciliation("b")


def test_timeout_or_unreconciled_cancel_does_not_release_and_duplicate_intent_is_rejected():
    book = ledger()
    assert book.reserve(intent("b", "BUY", "50"), reference_price=D("1")).allowed
    assert book.reserve(intent("b", "BUY", "1"), reference_price=D("1")).reason_code == "DUPLICATE_INTENT"
    book.mark_unknown("b")
    with pytest.raises(ValueError, match="FILLS_UNRECONCILED"):
        book.confirm_terminal("b", cumulative_filled=D("0"), fills_reconciled=False,
                              exchange_state="CANCELED")
    assert book.reserved_usdt == D("50")
    with pytest.raises(ValueError, match="ORDER_NOT_TERMINAL"):
        book.confirm_terminal("b", cumulative_filled=D("0"), fills_reconciled=True,
                              exchange_state="OPEN")
    with pytest.raises(ValueError, match="FILL_CUMULATIVE_MISMATCH"):
        book.confirm_terminal("b", cumulative_filled=D("1"), fills_reconciled=True,
                              exchange_state="CANCELED")
    assert book.reserved_usdt == D("50")


def test_competing_threads_cannot_oversubscribe_quote_balance():
    book = ledger(usdt="100", life="0", max_life="1000", net="1000")

    def try_reserve(index):
        return book.reserve(intent(str(index), "BUY", "20"), reference_price=D("1"))

    with ThreadPoolExecutor(max_workers=10) as pool:
        decisions = list(pool.map(try_reserve, range(10)))
    assert sum(decision.allowed for decision in decisions) == 5
    assert book.reserved_usdt == D("100")
