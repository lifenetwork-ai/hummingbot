"""Independent LIFE valuation, cash-flow-adjusted NAV, and execution attribution."""

from dataclasses import dataclass
from decimal import Decimal


def _valid(value: Decimal, *, positive: bool = False) -> bool:
    return (isinstance(value, Decimal) and value.is_finite()
            and (value > 0 if positive else value >= 0))


@dataclass(frozen=True)
class CapitalMeasurement:
    nav_quote: Decimal
    adjusted_nav_quote: Decimal
    highwater_quote: Decimal
    drawdown_bps: Decimal
    execution_loss_quote: Decimal
    starting_inventory_pnl_quote: Decimal


class CapitalLedger:
    def __init__(self, *, opening_life: Decimal, opening_usdt: Decimal,
                 opening_independent_price_usdt: Decimal):
        if (not _valid(opening_life) or not _valid(opening_usdt)
                or not _valid(opening_independent_price_usdt, positive=True)):
            raise ValueError("opening capital needs finite independent values")
        self.opening_life = opening_life
        self.opening_price_usdt = opening_independent_price_usdt
        self.life_balance = opening_life
        self.usdt_balance = opening_usdt
        self.opening_inventory_remaining = opening_life
        self.realized_starting_inventory_pnl_quote = Decimal("0")
        self.net_cashflows_quote = Decimal("0")
        self.execution_loss_quote = Decimal("0")
        self.highwater_quote = opening_usdt + opening_life * opening_independent_price_usdt
        if self.highwater_quote <= 0:
            raise ValueError("opening capital must be positive")
        self._fills: dict[str, tuple] = {}
        self._cashflows: dict[str, tuple] = {}
        self._funding: dict[str, Decimal] = {}

    def record_fill(self, trade_id: str, side: str, quantity: Decimal, price_usdt: Decimal,
                    fee_cost_quote: Decimal, *, independent_value_usdt: Decimal,
                    fee_currency: str = "USDT", signed_fee: Decimal | None = None) -> bool:
        if (not trade_id or side not in ("BUY", "SELL") or not _valid(quantity, positive=True)
                or not _valid(price_usdt, positive=True)
                or not _valid(independent_value_usdt, positive=True)
                or not isinstance(fee_cost_quote, Decimal) or not fee_cost_quote.is_finite()):
            raise ValueError("fill accounting observation invalid")
        if signed_fee is None and fee_currency == "USDT":
            signed_fee = -fee_cost_quote
        if (fee_currency not in ("LIFE", "USDT")
                or not isinstance(signed_fee, Decimal) or not signed_fee.is_finite()
                or fee_cost_quote != -signed_fee * (
                    independent_value_usdt if fee_currency == "LIFE" else Decimal("1"))):
            raise ValueError("fill fee conversion invalid")
        event = (side, quantity, price_usdt, fee_cost_quote, independent_value_usdt,
                 fee_currency, signed_fee)
        if trade_id in self._fills:
            if self._fills[trade_id] != event:
                raise ValueError("fill accounting ID conflict")
            return False
        sign = Decimal("1") if side == "BUY" else Decimal("-1")
        if side == "SELL" and quantity > self.life_balance:
            raise ValueError("insufficient LIFE for accounted fill")
        if side == "BUY" and quantity * price_usdt + (
                fee_cost_quote if fee_currency == "USDT" else Decimal("0")) > self.usdt_balance:
            raise ValueError("insufficient USDT for accounted fill")
        next_life = self.life_balance + sign * quantity + (
            signed_fee if fee_currency == "LIFE" else Decimal("0"))
        next_usdt = self.usdt_balance - sign * quantity * price_usdt - (
            fee_cost_quote if fee_currency == "USDT" else Decimal("0"))
        if next_life < 0 or next_usdt < 0:
            raise ValueError("insufficient balance for accounted fee")
        self.life_balance, self.usdt_balance = next_life, next_usdt
        edge = sign * (independent_value_usdt - price_usdt) * quantity - fee_cost_quote
        self.execution_loss_quote += max(Decimal("0"), -edge)
        if side == "SELL":
            sold_initial = min(self.opening_inventory_remaining, quantity)
            self.opening_inventory_remaining -= sold_initial
            self.realized_starting_inventory_pnl_quote += (
                sold_initial * (independent_value_usdt - self.opening_price_usdt))
            if fee_currency == "LIFE" and signed_fee < 0:
                fee_initial = min(self.opening_inventory_remaining, -signed_fee)
                self.opening_inventory_remaining -= fee_initial
                self.realized_starting_inventory_pnl_quote += (
                    fee_initial * (independent_value_usdt - self.opening_price_usdt))
        self._fills[trade_id] = event
        return True

    def record_asset_cashflow(self, cashflow_id: str, currency: str, quantity: Decimal,
                              *, independent_value_usdt: Decimal) -> bool:
        if (not cashflow_id or currency not in ("LIFE", "USDT")
                or not isinstance(quantity, Decimal) or not quantity.is_finite()
                or not _valid(independent_value_usdt, positive=True)
                or currency == "USDT" and independent_value_usdt != 1):
            raise ValueError("cashflow value invalid")
        event = (currency, quantity, independent_value_usdt)
        if cashflow_id in self._cashflows:
            if self._cashflows[cashflow_id] != event:
                raise ValueError("cashflow ID conflict")
            return False
        balance = self.life_balance if currency == "LIFE" else self.usdt_balance
        if balance + quantity < 0:
            raise ValueError("cashflow exceeds asset balance")
        if currency == "LIFE":
            self.life_balance += quantity
            if quantity < 0:
                withdrawn = min(self.opening_inventory_remaining, -quantity)
                self.opening_inventory_remaining -= withdrawn
                self.realized_starting_inventory_pnl_quote += (
                    withdrawn * (independent_value_usdt - self.opening_price_usdt))
        else:
            self.usdt_balance += quantity
        self.net_cashflows_quote += quantity * independent_value_usdt
        self._cashflows[cashflow_id] = event
        return True

    def record_cashflow(self, cashflow_id: str, amount_quote: Decimal) -> bool:
        return self.record_asset_cashflow(cashflow_id, "USDT", amount_quote,
                                          independent_value_usdt=Decimal("1"))

    def record_funding(self, event_id: str, cost_quote: Decimal) -> bool:
        if not event_id or not isinstance(cost_quote, Decimal) or not cost_quote.is_finite():
            raise ValueError("funding event invalid")
        if event_id in self._funding:
            if self._funding[event_id] != cost_quote:
                raise ValueError("funding event conflict")
            return False
        self.usdt_balance -= cost_quote
        self._funding[event_id] = cost_quote
        return True

    def measure(self, independent_price_usdt: Decimal | None, *,
                source_kind: str) -> CapitalMeasurement | None:
        if source_kind != "independent_market" or not _valid(independent_price_usdt, positive=True):
            return None
        nav = self.usdt_balance + self.life_balance * independent_price_usdt
        adjusted = nav - self.net_cashflows_quote
        self.highwater_quote = max(self.highwater_quote, adjusted)
        drawdown = max(Decimal("0"), self.highwater_quote - adjusted) / self.highwater_quote * Decimal("10000")
        inventory_pnl = (self.opening_inventory_remaining
                         * (independent_price_usdt - self.opening_price_usdt)
                         + self.realized_starting_inventory_pnl_quote)
        return CapitalMeasurement(nav, adjusted, self.highwater_quote, drawdown,
                                  self.execution_loss_quote, inventory_pnl)
