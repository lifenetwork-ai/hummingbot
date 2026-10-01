"""P0/A04: no fabricated fills, deterministic time, and durable fill identity."""

import json
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from test.hummingbot.strategy_v2.life_liquidity.fakes import BookSnapshot, ManualClock, OfflineExchange, OrderState


class TestOfflineExchange(unittest.TestCase):
    def setUp(self):
        self.exchange = OfflineExchange(
            life=Decimal("100"),
            usdt=Decimal("1000"),
            book=BookSnapshot(best_bid=Decimal("1.99"), best_ask=Decimal("2.01")),
        )

    def test_placing_canceling_and_changing_book_never_create_trades(self):
        before = self.exchange.balances.copy()
        self.exchange.place("bid-1", "BUY", Decimal("2"), Decimal("2"))
        self.exchange.book = BookSnapshot(best_bid=Decimal("2.5"), best_ask=Decimal("2.6"))
        self.exchange.request_cancel("bid-1")

        self.assertEqual(self.exchange.orders["bid-1"].state, OrderState.CANCEL_PENDING)
        self.assertEqual(self.exchange.traded_base, Decimal("0"))
        self.assertEqual(self.exchange.balances, before)

        self.exchange.acknowledge_cancel("bid-1")
        self.assertEqual(self.exchange.orders["bid-1"].state, OrderState.CANCELED)
        self.assertEqual(self.exchange.traded_base, Decimal("0"))

    def test_partial_fill_during_pending_cancel_is_applied_once(self):
        self.exchange.place("ask-1", "SELL", Decimal("5"), Decimal("2"))
        self.assertTrue(self.exchange.inject_fill("ask-1", "trade-1", Decimal("1.25"), Decimal("2")))
        self.exchange.request_cancel("ask-1")
        self.assertFalse(self.exchange.inject_fill("ask-1", "trade-1", Decimal("1.25"), Decimal("2")))
        self.assertTrue(self.exchange.inject_fill("ask-1", "trade-2", Decimal("0.75"), Decimal("2.1")))
        self.exchange.acknowledge_cancel("ask-1")

        self.assertEqual(self.exchange.orders["ask-1"].filled, Decimal("2.00"))
        self.assertEqual(self.exchange.orders["ask-1"].state, OrderState.CANCELED)
        self.assertEqual(self.exchange.balances["LIFE"], Decimal("98"))
        self.assertEqual(self.exchange.balances["USDT"], Decimal("1004.075"))
        self.assertEqual(self.exchange.traded_base, Decimal("2"))

    def test_invalid_late_or_overfilled_trades_do_not_change_balances(self):
        self.exchange.place("bid-1", "BUY", Decimal("1"), Decimal("2"))
        self.exchange.inject_fill("bid-1", "trade-1", Decimal("0.4"), Decimal("1.9"))
        before = self.exchange.balances.copy()

        with self.assertRaises(ValueError):
            self.exchange.inject_fill("bid-1", "trade-1", Decimal("0.5"), Decimal("1.9"))
        with self.assertRaises(ValueError):
            self.exchange.inject_fill("bid-1", "trade-2", Decimal("0.7"), Decimal("1.9"))
        self.assertEqual(self.exchange.balances, before)

        self.exchange.request_cancel("bid-1")
        self.exchange.acknowledge_cancel("bid-1")
        with self.assertRaises(ValueError):
            self.exchange.inject_fill("bid-1", "trade-3", Decimal("0.1"), Decimal("1.9"))
        self.assertEqual(self.exchange.balances, before)

    def test_restart_keeps_orders_fills_and_deduplication_keys(self):
        self.exchange.place("bid-1", "BUY", Decimal("2"), Decimal("2"))
        self.exchange.inject_fill("bid-1", "trade-1", Decimal("0.5"), Decimal("1.9"))
        restarted = OfflineExchange.from_checkpoint(json.loads(json.dumps(self.exchange.checkpoint())))

        self.assertEqual(restarted.orders["bid-1"].state, OrderState.OPEN)
        self.assertFalse(restarted.inject_fill("bid-1", "trade-1", Decimal("0.5"), Decimal("1.9")))
        restarted.inject_fill("bid-1", "trade-2", Decimal("0.25"), Decimal("1.8"))
        self.assertEqual(restarted.orders["bid-1"].filled, Decimal("0.75"))
        self.assertEqual(restarted.balances["LIFE"], Decimal("100.75"))
        self.assertEqual(restarted.balances["USDT"], Decimal("998.6"))

    def test_final_fill_before_cancel_ack_keeps_order_filled(self):
        self.exchange.place("ask-1", "SELL", Decimal("1"), Decimal("2"))
        self.exchange.request_cancel("ask-1")
        self.exchange.inject_fill("ask-1", "trade-1", Decimal("1"), Decimal("2"))

        self.assertEqual(self.exchange.acknowledge_cancel("ask-1"), OrderState.FILLED)
        self.assertEqual(self.exchange.balances["LIFE"], Decimal("99"))
        self.assertEqual(self.exchange.balances["USDT"], Decimal("1002"))

    def test_empty_and_one_sided_books_do_not_generate_fills(self):
        for book in (BookSnapshot(), BookSnapshot(best_bid=Decimal("2"))):
            exchange = OfflineExchange(Decimal("100"), Decimal("1000"), book)
            exchange.place("ask-1", "SELL", Decimal("1"), Decimal("2"))
            self.assertEqual(exchange.traded_base, Decimal("0"))
            self.assertEqual(exchange.balances["LIFE"], Decimal("100"))

    def test_duplicate_order_id_is_rejected(self):
        self.exchange.place("bid-1", "BUY", Decimal("1"), Decimal("2"))
        with self.assertRaises(ValueError):
            self.exchange.place("bid-1", "BUY", Decimal("1"), Decimal("2"))


class TestManualClock(unittest.TestCase):
    def test_deadline_is_controlled_without_sleeping(self):
        started_at = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)
        clock = ManualClock(started_at)
        deadline = started_at + timedelta(hours=4)

        clock.advance(Decimal("14399"))
        self.assertLess(clock.now_utc, deadline)
        clock.advance(Decimal("1"))
        self.assertEqual(clock.now_utc, deadline)
        self.assertEqual(clock.monotonic_seconds, Decimal("14400"))

    def test_clock_rejects_negative_time_travel(self):
        clock = ManualClock(datetime(2026, 10, 1, tzinfo=timezone.utc))
        with self.assertRaises(ValueError):
            clock.advance(Decimal("-1"))
