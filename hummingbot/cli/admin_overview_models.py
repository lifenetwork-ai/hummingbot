"""Reporting-only Overview contracts for optional owner observations.

These models are referenced by StatusResponse and published by make swagger.
Unavailable observations are null, never synthetic zeroes or trading decisions.
"""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Generic, Literal, Optional, TypeVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, PlainSerializer

Market = Literal['spot', 'perpetual']
Money = Annotated[Decimal, Field(allow_inf_nan=False)]
Fraction = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Seconds = Annotated[float, Field(ge=0, allow_inf_nan=False)]
UTCDateTime = Annotated[AwareDatetime, PlainSerializer(lambda value: value.astimezone(timezone.utc), return_type=datetime, when_used='json')]
T = TypeVar('T')


class OverviewModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class OverviewObservation(OverviewModel, Generic[T]):
    value: Optional[T]
    observed_at: Optional[UTCDateTime]
    stale: bool
    unavailable_reason: Optional[str]


class OverviewValuationPrice(OverviewModel):
    observed_at: UTCDateTime
    asset: str
    pair: str
    connector: str
    price_usdt: Money


class OverviewValuation(OverviewModel):
    currency: Literal['USDT']
    valuation_mark: Literal['mid', 'mark']
    valued_at: UTCDateTime
    prices: list[OverviewValuationPrice]


class OverviewCapitalAllocated(OverviewModel):
    usdt: Money
    life: Money
    source: Literal['session_configuration']


class OverviewMarketFees(OverviewModel):
    market: Market
    total_usdt: Money


class OverviewSessionFees(OverviewModel):
    total_usdt: Money = Field(description='Signed cost: positive paid, negative rebate; converted at fill time.')
    by_market: Optional[list[OverviewMarketFees]]
    conversion_basis: Literal['at_fill']
    history_complete: bool
    through_at: UTCDateTime


class OverviewPortfolio(OverviewModel):
    total_usdt: Money = Field(description='Account equity; include perp unrealized PnL once, do not double count margin.')
    life_usdt: Money
    usdt: Money = Field(description='USDT equity component, not available cash.')
    other_assets_usdt: Money
    valuation: OverviewValuation


class OverviewSessionDelta(OverviewModel):
    baseline_at: UTCDateTime
    baseline_total_usdt: Money
    net_transfers_usdt: Optional[Money] = Field(description='Net external inflows valued at transfer time; null when incomplete.')
    adjusted_for_transfers: bool
    usdt: Optional[Money] = Field(description='End equity minus baseline minus net inflows; null if transfers unknown.')
    pct: Optional[Money] = Field(description='Percentage points: 0.82 means 0.82%; delta / baseline * 100; null if baseline <= 0.')
    through_at: UTCDateTime


class OverviewHoldBenchmark(OverviewModel):
    basis: Literal['initial_spot_assets']
    baseline_at: UTCDateTime
    initial_life: Money
    initial_usdt: Money
    session_pnl_usdt: Money
    hold_pnl_usdt: Money
    outperform_usdt: Money = Field(description='session_pnl_usdt minus hold_pnl_usdt.')
    valuation: OverviewValuation
    transfer_treatment: Literal['excluded_from_both']


class OverviewRiskRule(OverviewModel):
    rule_id: str
    controller_id: Optional[str]
    market: Optional[Market]
    unit: Literal['USDT', 'LIFE', 'fraction', 'seconds', 'bps']
    lower_limit: Optional[Money]
    upper_limit: Optional[Money]
    current: Optional[Money]
    status: Literal['ok', 'violated', 'disabled', 'unknown']


class OverviewDepthLevel(OverviewModel):
    offset_bps: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    bid_usdt: Money
    ask_usdt: Money


class OverviewDepth(OverviewModel):
    basis: Literal['own_working_orders']
    reference_mid: Money
    levels: list[OverviewDepthLevel] = Field(description='Cumulative remaining own order notional within offset_bps of mid.')


class OverviewCoverageMinute(OverviewModel):
    minute_ts: UTCDateTime
    observed_seconds: Seconds
    unknown_seconds: Seconds
    two_sided_seconds: Seconds


class OverviewRollingCoverage(OverviewModel):
    window_seconds: Literal[1800]
    window_start: UTCDateTime
    window_end: UTCDateTime
    elapsed_seconds: Seconds
    observed_seconds: Seconds
    unknown_seconds: Seconds
    two_sided_seconds: Seconds
    rolling_30m_fraction: Optional[Fraction] = Field(description='two_sided_seconds / elapsed_seconds; unknown remains in denominator.')
    target_fraction: Optional[Fraction]
    full_window: bool
    per_minute: Optional[list[OverviewCoverageMinute]]


class OverviewSlippageEstimate(OverviewModel):
    side: Literal['BUY', 'SELL']
    notional_usdt: Money
    expected_slippage_bps: Optional[Money]
    sufficient_depth: bool


class OverviewSlippage(OverviewModel):
    own_orders_excluded: Literal[True] = True
    notional_basis: Literal['reference_mid'] = 'reference_mid'
    basis: Literal['exchange_order_book']
    reference_mid: Money
    estimates: list[OverviewSlippageEstimate]


class OverviewReservedBalance(OverviewModel):
    asset: str
    free: Optional[Money]
    reserved_for_open_orders: Optional[Money] = Field(description='Exchange-confirmed reservation, not equity minus free.')


class OverviewInventoryLimit(OverviewModel):
    controller_id: str
    market: Market
    pair: str
    asset: Literal['LIFE']
    long_limit: Optional[Money] = None
    short_limit: Optional[Money] = None
    gross_limit: Optional[Money] = None
    inventory_limit: Optional[Money] = Field(description='Null if policy defines no absolute quantity limit.')


class OverviewReferencePrice(OverviewModel):
    controller_id: str
    connector: str
    pair: str
    anchor_price: Money
    last_price: Money = Field(description='Current fresh reference midpoint, not last traded price.')
    price_basis: Literal['mid'] = 'mid'
    price_observed_at: UTCDateTime
    anchor_at: UTCDateTime


class OverviewPostFillRecovery(OverviewModel):
    measurement: Literal['sampled_upper_bound'] = 'sampled_upper_bound'
    basis: Literal['fill_to_acknowledged_two_sided_quotes']
    window_start: UTCDateTime
    window_end: UTCDateTime
    recovered_count: Annotated[int, Field(ge=0)]
    pending_count: Annotated[int, Field(ge=0)]
    history_complete: bool
    p50_seconds: Optional[Seconds]
    p95_seconds: Optional[Seconds]


class OverviewMarketData(OverviewModel):
    market: Market
    connector: str
    pair: str
    depth: OverviewObservation[OverviewDepth]
    coverage: OverviewObservation[OverviewRollingCoverage]
    slippage_estimate: OverviewObservation[OverviewSlippage]
    post_fill_recovery: OverviewObservation[OverviewPostFillRecovery]


class OverviewAccountScope(OverviewModel):
    account_ids: list[str]
    environment: Optional[Literal['live', 'demo']]
    identity_verified: bool = False


class OverviewSession(OverviewModel):
    capital_allocated: OverviewObservation[OverviewCapitalAllocated]
    fees: OverviewObservation[OverviewSessionFees]
    delta: OverviewObservation[OverviewSessionDelta]
    benchmark_hold: OverviewObservation[OverviewHoldBenchmark]


class OverviewSupplement(OverviewModel):
    """Account/session observations only; never changes quote, sizing or risk enforcement."""
    contract_version: Literal[1]
    server_time: UTCDateTime
    account_scope: OverviewAccountScope
    session_id: Optional[str]
    session_started_at: Optional[UTCDateTime]
    session: OverviewSession
    portfolio: OverviewObservation[OverviewPortfolio]
    risk_rules: OverviewObservation[list[OverviewRiskRule]]
    markets: list[OverviewMarketData]
    balances: OverviewObservation[list[OverviewReservedBalance]]
    inventory_limits: OverviewObservation[list[OverviewInventoryLimit]]
    references: OverviewObservation[list[OverviewReferencePrice]]
