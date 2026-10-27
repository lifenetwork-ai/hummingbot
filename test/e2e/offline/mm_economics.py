"""Test-only economic primitives; no exchange, credentials or database access."""
from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.backtesting.backtesting_engine_base import BacktestPositionHold

ZERO = Decimal("0")


@dataclass
class PassiveOrder:
    id: str
    market: str
    side: str
    price: Decimal
    amount: Decimal
    created_at: int
    active_at: int
    queue_ahead: Decimal
    filled: Decimal = ZERO
    cancel_at: int = None
    level: str = ""
    config: object = None

    def live(self, now):
        return self.filled < self.amount and (self.cancel_at is None or now < self.cancel_at)

    @property
    def remaining(self):
        return self.amount - self.filled


def match_flow(orders, *, now, aggressor, limit, quantity, fraction):
    """Independent aggressor sweep, strict trade-through and conservative queue.

    One flow budget is shared across all eligible levels. A partial allocation
    cap discards residual flow rather than inventing execution at worse prices.
    Queue has no cancellation benefit; touch alone never proves execution.
    """
    if aggressor not in ("BUY", "SELL") or not ZERO < fraction <= 1 or quantity < 0 or limit <= 0:
        raise ValueError("Invalid flow assumptions")
    candidates = [order for order in orders if order.live(now) and now >= order.active_at
                  and order.side != aggressor and (order.price < limit if aggressor == "BUY" else order.price > limit)]
    candidates.sort(key=lambda order: (order.price if aggressor == "BUY" else -order.price, order.created_at, order.id))
    fills = []
    for order in candidates:
        ahead = min(quantity, order.queue_ahead)
        order.queue_ahead -= ahead
        quantity -= ahead
        amount = min(quantity, order.remaining, order.amount * fraction)
        if amount > 0:
            order.filled += amount
            quantity -= amount
            fills.append((order, amount))
        if quantity <= 0 or order.remaining > 0:
            break
    return fills


class Wallet:
    """One shared cash wallet; upstream holds value HEDGE legs independently."""

    def __init__(self, *, cash, base, initial_mark):
        self.cash = self.initial_cash = cash
        self.base = self.initial_base = base
        self.initial_mark = initial_mark
        self.holds = {side: BacktestPositionHold("okx_perpetual", "LIFE-USDT") for side in ("BUY", "SELL")}
        self.fees = self.funding = ZERO
        self.seen = set()

    def fill(self, fill_id, market, side, amount, price, fee):
        if fill_id in self.seen:
            raise ValueError("duplicate fill")
        if market not in ("spot", "perpetual") or side not in ("BUY", "SELL") or amount <= 0 or price <= 0:
            raise ValueError("Invalid fill")
        delta = (-amount * price if side == "BUY" else amount * price) if market == "spot" else ZERO
        if self.cash + delta - fee < 0 or (market == "spot" and side == "SELL" and self.base < amount):
            raise ValueError("Insufficient balance")
        self.seen.add(fill_id)
        self.cash += delta - fee
        self.fees += fee
        if market == "spot":
            self.base += amount if side == "BUY" else -amount
        else:
            self.holds[side].add_executor(SimpleNamespace(
                config=SimpleNamespace(id=fill_id), filled_amount_quote=amount * price,
                cum_fees_quote=fee, side=TradeType[side]), entry_price=price)

    def fund(self, mark, rate):
        net_base = sum((hold.net_amount_base for hold in self.holds.values()), ZERO)
        payment = -net_base * mark * rate
        self.cash += payment
        self.funding += payment
        return payment

    def unrealized(self, mark):
        return sum((hold.get_position_summary(mark).unrealized_pnl_quote for hold in self.holds.values()), ZERO)

    def equity(self, mark):
        return self.cash + self.base * mark + self.unrealized(mark)

    def hold_equity(self, mark):
        return self.initial_cash + self.initial_base * mark


def markouts(fills, marks, horizons=(1, 5, 30)):
    result = {}
    for horizon in horizons:
        values = [(marks[row["at"] + horizon] - Decimal(row["price"])) * Decimal(row["amount"])
                  * (1 if row["side"] == "BUY" else -1) for row in fills if row["at"] + horizon in marks]
        total = sum(values, ZERO)
        result[str(horizon)] = dict(observed=len(values), censored=len(fills) - len(values),
                                    sum_quote=str(total), mean_quote=str(total / len(values)) if values else None)
    return result
