"""Validated Decimal adapter around the existing PMM inventory interpolation.

Only ratios cross the upstream double interface. LIFE allocations, rounding and
hard limits stay Decimal. No alternate inventory algorithm is implemented here.
"""
import math
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from hummingbot.strategy.pure_market_making.inventory_skew_calculator import (
    calculate_bid_ask_ratios_from_base_asset_ratio,
)


class InventorySkewConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = False
    target_base_pct: Decimal = Field(default=Decimal(".5"), ge=0, le=1, allow_inf_nan=False)
    inventory_range_multiplier: Decimal = Field(default=Decimal("1"), gt=0, allow_inf_nan=False)


def inventory_skew_ratios(base, quote, price, target, base_range):
    inputs = (base, quote, price, target, base_range)
    if (any(not value.is_finite() for value in inputs) or min(base, quote) < 0 or price <= 0
            or not 0 <= target <= 1 or base_range <= 0 or base * price + quote <= 0):
        raise ValueError("Inventory interpolation requires finite portfolio, price and range")
    converted = tuple(float(value) for value in inputs)
    if any(not math.isfinite(value) or (original > 0 and value == 0) for original, value in zip(inputs, converted)):
        raise ValueError("Inventory inputs exceed the legacy numeric representation")
    ratios = calculate_bid_ask_ratios_from_base_asset_ratio(*converted)
    values = Decimal(str(ratios.bid_ratio)), Decimal(str(ratios.ask_ratio))
    if any(not value.is_finite() or not 0 <= value <= 2 for value in values):
        raise ValueError("Inventory interpolation returned invalid ratios")
    return values
