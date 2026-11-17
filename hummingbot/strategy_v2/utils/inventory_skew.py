"""Validated Decimal adapter around the existing PMM inventory interpolation.

Only ratios cross the upstream double interface. LIFE allocations, rounding and
hard limits stay Decimal. No alternate inventory algorithm is implemented here.
"""
import math
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from hummingbot.strategy.pure_market_making.inventory_skew_calculator import (
    calculate_bid_ask_ratios_from_base_asset_ratio,
)


def _conditional_range_schema(schema):
    # Either range can be absent in serialized snapshots. A schema default
    # would make generated clients incorrectly require the inactive field.
    schema.pop('default', None)


class InventorySkewConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = False
    target_base_pct: Decimal = Field(default=Decimal(".5"), ge=0, le=1, allow_inf_nan=False)
    inventory_range_multiplier: Decimal = Field(
        default=Decimal("1"), gt=0, allow_inf_nan=False, json_schema_extra=_conditional_range_schema,
        description='Legacy range in multiples of total unskewed base order size; defaults to 1 when neither range is supplied. Omitted in percentage mode.')
    inventory_range_pct: Optional[Decimal] = Field(
        default=None, gt=0, le=Decimal('.5'), allow_inf_nan=False, json_schema_extra=_conditional_range_schema,
        description='Half-width as a fraction of observed base+quote cash value: 0.02 is two percentage points. Omit for legacy order-size multiplier mode; the two modes are mutually exclusive.')

    @model_validator(mode='after')
    def validate_range_mode(self):
        if self.inventory_range_pct is not None:
            if 'inventory_range_multiplier' in self.model_fields_set:
                raise ValueError('Choose inventory_range_pct or inventory_range_multiplier, not both')
            if self.inventory_range_pct > min(self.target_base_pct, 1 - self.target_base_pct):
                raise ValueError('inventory_range_pct must fit on both sides of target_base_pct within 0..1')
        return self

    @model_serializer(mode='wrap')
    def serialize_range_mode(self, handler):
        values = handler(self)
        # Preserve archived catalogue hashes and make percent snapshots reloadable.
        values.pop('inventory_range_pct' if self.inventory_range_pct is None else 'inventory_range_multiplier', None)
        return values


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
