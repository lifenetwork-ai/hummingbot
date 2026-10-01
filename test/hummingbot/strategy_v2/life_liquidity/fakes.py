"""Deterministic exchange and clock fakes for LIFE strategy tests."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional


class OrderState(str, Enum):
    OPEN = "open"
    CANCEL_PENDING = "cancel_pending"
    CANCELED = "canceled"
    FILLED = "filled"


@dataclass(frozen=True)
class BookSnapshot:
    best_bid: Optional[Decimal] = None
    best_ask: Optional[Decimal] = None


@dataclass
class OrderRecord:
    order_id: str
    side: str
    quantity: Decimal
    price: Decimal
    filled: Decimal = Decimal("0")
    state: OrderState = OrderState.OPEN


class ManualClock:
    def __init__(self, now_utc: datetime):
        if now_utc.tzinfo is None or now_utc.utcoffset() is None:
            raise ValueError("clock requires a timezone-aware start time")
        self.now_utc = now_utc.astimezone(timezone.utc)
        self.monotonic_seconds = Decimal("0")

    def advance(self, seconds: Decimal) -> None:
        if not seconds.is_finite() or seconds < 0:
            raise ValueError("elapsed seconds must be finite and nonnegative")
        microseconds = seconds * Decimal("1000000")
        if microseconds != microseconds.to_integral_value():
            raise ValueError("clock precision is one microsecond")
        self.now_utc += timedelta(microseconds=int(microseconds))
        self.monotonic_seconds += seconds


class OfflineExchange:
    """Orders fill only through inject_fill; balances exclude P4 reservations."""

    def __init__(self, life: Decimal, usdt: Decimal, book: Optional[BookSnapshot] = None):
        self.balances = {"LIFE": life, "USDT": usdt}
        self.book = book or BookSnapshot()
        self.orders: dict[str, OrderRecord] = {}
        self.fills: dict[str, tuple[str, Decimal, Decimal]] = {}

    @property
    def traded_base(self) -> Decimal:
        return sum((fill[1] for fill in self.fills.values()), Decimal("0"))

    def place(self, order_id: str, side: str, quantity: Decimal, price: Decimal) -> OrderRecord:
        if not order_id or order_id in self.orders:
            raise ValueError("order ID must be unique and nonempty")
        if side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        self._validate_positive(quantity, "quantity")
        self._validate_positive(price, "price")
        order = OrderRecord(order_id=order_id, side=side, quantity=quantity, price=price)
        self.orders[order_id] = order
        return order

    def request_cancel(self, order_id: str) -> None:
        order = self.orders[order_id]
        if order.state == OrderState.OPEN:
            order.state = OrderState.CANCEL_PENDING
        elif order.state != OrderState.CANCEL_PENDING:
            raise ValueError("cannot cancel a terminal order")

    def acknowledge_cancel(self, order_id: str) -> OrderState:
        order = self.orders[order_id]
        if order.state == OrderState.CANCEL_PENDING:
            order.state = OrderState.CANCELED
        elif order.state not in {OrderState.CANCELED, OrderState.FILLED}:
            raise ValueError("cancellation was not requested")
        return order.state

    def inject_fill(self, order_id: str, trade_id: str, quantity: Decimal, price: Decimal) -> bool:
        order = self.orders[order_id]
        self._validate_positive(quantity, "fill quantity")
        self._validate_positive(price, "fill price")
        if not trade_id:
            raise ValueError("trade ID must be nonempty")
        fill = (order_id, quantity, price)
        if trade_id in self.fills:
            if self.fills[trade_id] != fill:
                raise ValueError("conflicting duplicate trade ID")
            return False
        if order.state not in {OrderState.OPEN, OrderState.CANCEL_PENDING}:
            raise ValueError("cannot fill a terminal order")
        if quantity > order.quantity - order.filled:
            raise ValueError("fill exceeds remaining order quantity")
        if (order.side == "BUY" and price > order.price) or (order.side == "SELL" and price < order.price):
            raise ValueError("fill violates limit price")
        if order.side == "BUY":
            if self.balances["USDT"] < quantity * price:
                raise ValueError("insufficient USDT for fill")
            self.balances["LIFE"] += quantity
            self.balances["USDT"] -= quantity * price
        else:
            if self.balances["LIFE"] < quantity:
                raise ValueError("insufficient LIFE for fill")
            self.balances["LIFE"] -= quantity
            self.balances["USDT"] += quantity * price
        order.filled += quantity
        if order.filled == order.quantity:
            order.state = OrderState.FILLED
        self.fills[trade_id] = fill
        return True

    def checkpoint(self) -> dict:
        return {
            "balances": {asset: str(amount) for asset, amount in self.balances.items()},
            "book": {
                "best_bid": str(self.book.best_bid) if self.book.best_bid is not None else None,
                "best_ask": str(self.book.best_ask) if self.book.best_ask is not None else None,
            },
            "orders": {
                order_id: {
                    "side": order.side,
                    "quantity": str(order.quantity),
                    "price": str(order.price),
                    "filled": str(order.filled),
                    "state": order.state.value,
                }
                for order_id, order in self.orders.items()
            },
            "fills": {
                trade_id: {"order_id": order_id, "quantity": str(quantity), "price": str(price)}
                for trade_id, (order_id, quantity, price) in self.fills.items()
            },
        }

    @classmethod
    def from_checkpoint(cls, checkpoint: dict) -> "OfflineExchange":
        book = checkpoint["book"]
        exchange = cls(
            life=Decimal(checkpoint["balances"]["LIFE"]),
            usdt=Decimal(checkpoint["balances"]["USDT"]),
            book=BookSnapshot(
                best_bid=Decimal(book["best_bid"]) if book["best_bid"] is not None else None,
                best_ask=Decimal(book["best_ask"]) if book["best_ask"] is not None else None,
            ),
        )
        exchange.orders = {
            order_id: OrderRecord(
                order_id=order_id,
                side=value["side"],
                quantity=Decimal(value["quantity"]),
                price=Decimal(value["price"]),
                filled=Decimal(value["filled"]),
                state=OrderState(value["state"]),
            )
            for order_id, value in checkpoint["orders"].items()
        }
        exchange.fills = {
            trade_id: (value["order_id"], Decimal(value["quantity"]), Decimal(value["price"]))
            for trade_id, value in checkpoint["fills"].items()
        }
        return exchange

    @staticmethod
    def _validate_positive(value: Decimal, label: str) -> None:
        if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
            raise ValueError(f"{label} must be a finite positive Decimal")
