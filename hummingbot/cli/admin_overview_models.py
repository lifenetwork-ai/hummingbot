"""Reporting-only Overview contracts for optional owner observations.

These models are referenced by StatusResponse and published by make swagger.
Unavailable observations are null, never synthetic zeroes or trading decisions.
"""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Generic, Literal, Optional, TypeVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, PlainSerializer, model_validator

Market = Literal['spot', 'perpetual']
Money = Annotated[Decimal, Field(allow_inf_nan=False),
                  PlainSerializer(lambda value: format(value, 'f'), return_type=str, when_used='json')]
Asset = Annotated[str, Field(pattern=r'^[A-Z0-9][A-Z0-9._]{0,31}$')]
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
    processing_state: Literal['ready', 'catching_up'] = 'ready'
    processed_fill_count: Optional[Annotated[int, Field(ge=0)]] = None
    caught_up_at: Optional[UTCDateTime] = None
    reported_projection_version: Optional[Annotated[int, Field(ge=1)]] = None
    processing_projection_version: Optional[Annotated[int, Field(ge=1)]] = None
    backfill_processed_fill_count: Optional[Annotated[int, Field(ge=0)]] = None


class OverviewPortfolio(OverviewModel):
    total_usdt: Money = Field(description='Account equity; include perp unrealized PnL once, do not double count margin.')
    life_usdt: Money
    usdt: Money = Field(description='USDT equity component, not available cash.')
    other_assets_usdt: Money
    valuation: OverviewValuation
    assets: list['OverviewPortfolioAsset'] = Field(default_factory=list)


class OverviewPortfolioAsset(OverviewModel):
    asset: Asset
    equity: Money
    value_usdt: Money


class OverviewAccountEquity(OverviewModel):
    total_usd: Money = Field(description='Whole-account totalEq from OKX, once; not USDT, free cash or allocated capital. Includes venue position PnL already.')
    currency: Literal['USD'] = 'USD'
    source: Literal['okx_account_balance_total_eq']


class OverviewQuoteBudgetMarket(OverviewModel):
    market: Market
    budget_usdt: Money


class OverviewQuoteBudget(OverviewModel):
    total_usdt: Money
    by_market: list[OverviewQuoteBudgetMarket]
    source: Literal['pinned_session_catalogue']
    basis: Literal['configured_working_orders_notional'] = 'configured_working_orders_notional'


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
    unit: Annotated[str, Field(pattern=r'^(?:[A-Z0-9][A-Z0-9._]{0,31}|fraction|seconds|bps)$')]
    lower_limit: Optional[Money]
    upper_limit: Optional[Money]
    current: Optional[Money]
    status: Literal['ok', 'violated', 'disabled', 'unknown']
    unavailable_reason: Optional[str] = None


class OverviewDepthLevel(OverviewModel):
    offset_bps: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    bid_usdt: Money
    ask_usdt: Money


class OverviewDepth(OverviewModel):
    basis: Literal['own_working_orders']
    reference_mid: Money
    levels: list[OverviewDepthLevel] = Field(description='Cumulative remaining own order notional within offset_bps of mid.')


class OverviewCoverageEligibility(OverviewModel):
    eligible_seconds: Seconds = 0
    two_sided_eligible_seconds: Seconds = 0
    restricted_seconds: Seconds = 0
    restriction_seconds: dict[Literal['balance', 'inventory', 'bootstrap', 'guard', 'policy'], Seconds] = Field(default_factory=dict)
    eligibility_unknown_seconds: Seconds = 0


class OverviewCoverageMinute(OverviewCoverageEligibility):
    minute_ts: UTCDateTime
    observed_seconds: Seconds
    unknown_seconds: Seconds
    two_sided_seconds: Seconds


class OverviewRollingCoverage(OverviewCoverageEligibility):
    measurement: Literal['sampled_interval_lower_bound'] = 'sampled_interval_lower_bound'
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
    conditional_eligible_fraction: Optional[Fraction] = None
    conservative_eligible_fraction: Optional[Fraction] = None
    measurement_ready: bool = False
    measurement_reason: Optional[str] = 'eligibility_history_unavailable'


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
    asset: Asset
    long_limit: Optional[Money] = None
    short_limit: Optional[Money] = None
    gross_limit: Optional[Money] = None
    inventory_limit: Optional[Money] = Field(description='Null if policy defines no absolute quantity limit.')
    long_exposure: Optional[Money] = None
    short_exposure: Optional[Money] = None
    exposure_observed_at: Optional[UTCDateTime] = None
    exposure_basis: Optional[Literal['engine_cap_assessment']] = None

    @model_validator(mode='after')
    def asset_matches_pair(self):
        parts = self.pair.split('-')
        if len(parts) != 2 or parts[0] != self.asset or not parts[1]:
            raise ValueError('Inventory asset does not match its trading pair')
        return self


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
    spread_target: OverviewObservation['OverviewSpreadTarget'] = Field(default_factory=lambda: OverviewObservation(
        value=None, observed_at=None, stale=True, unavailable_reason='spread_plan_unavailable'))


class OverviewSpreadTarget(OverviewModel):
    target_bps: Money
    buy_distance_bps: Money
    sell_distance_bps: Money
    basis: Literal['two_sided_primary_plan']
    source: Literal['controller_spread_policy', 'controller_config']


class OverviewAccountScope(OverviewModel):
    account_ids: list[str]
    environment: Optional[Literal['live', 'demo']]
    identity_verified: bool = False
    site: Optional[Literal['www', 'app', 'my']] = None


class OverviewSession(OverviewModel):
    capital_allocated: OverviewObservation[OverviewCapitalAllocated]
    quote_budget: OverviewObservation[OverviewQuoteBudget] = Field(default_factory=lambda: OverviewObservation(
        value=None, observed_at=None, stale=True, unavailable_reason='quote_budget_unavailable'))
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
    account_equity: OverviewObservation[OverviewAccountEquity] = Field(default_factory=lambda: OverviewObservation(
        value=None, observed_at=None, stale=True, unavailable_reason='account_equity_unavailable'))
    risk_rules: OverviewObservation[list[OverviewRiskRule]]
    markets: list[OverviewMarketData]
    balances: OverviewObservation[list[OverviewReservedBalance]]
    inventory_limits: OverviewObservation[list[OverviewInventoryLimit]]
    references: OverviewObservation[list[OverviewReferencePrice]]
