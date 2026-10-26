"""HTTP contracts shared by validation, serialization and handler OpenAPI metadata."""
import math
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, TypeAdapter, field_validator

from controllers.market_making.life_mm import ReferencePair
from hummingbot.client.config.mm_profiles import MarketMode, MMProfilesConfig
from hummingbot.core.admin_reconciliation import ReconciliationRequest
from hummingbot.core.mm_session import MMSessionRequest, MMSessionStatus
from hummingbot.strategy_v2.models.executors import CloseType
from hummingbot.strategy_v2.models.executors_info import PerformanceReport


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


# Apply the Python callback only to timestamps, including timestamps in mappings.
UTCDateTime = Annotated[datetime, PlainSerializer(_as_utc, return_type=datetime, when_used="json")]


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuntimeIncident(APIModel):
    reason_code: str
    scope: str
    severity: Literal["warning", "error"]
    detail: Optional[str] = None
    first_seen_at: UTCDateTime
    last_seen_at: UTCDateTime
    last_healthy_at: Optional[UTCDateTime] = None
    retry_count: int
    next_retry_at: Optional[UTCDateTime] = None
    active_fallback: Optional[str] = None
    operator_action_required: bool


class ServiceStatus(APIModel):
    reason_code: Optional[str] = None
    observed_at: Optional[UTCDateTime] = None
    state_source: Literal["postgres", "process"]
    persisted_state_available: bool
    phase: str
    observer_age_seconds: Optional[float] = None
    incidents: List[RuntimeIncident] = Field(default_factory=list)
    stale: bool = False


class ErrorResponse(APIModel):
    error: str
    detail: Optional[str] = None
    service: Optional[ServiceStatus] = None


class StartCommand(APIModel):
    action: Literal["start"]
    expected_version: int = Field(ge=0, strict=True)
    market_mode: MarketMode
    selection: Literal["fixed", "random"] = Field(default="fixed", description="Fixed runs one profile; random chooses timed steps from a profile pool.")
    profile_id: Optional[str] = Field(default=None, description="Deployed profile ID for fixed selection, such as follow_market, sideways or follow_token. Omit for random.")
    references: Optional[List[ReferencePair]] = None
    profile_ids: Optional[List[str]] = Field(default=None, description="Optional subset of random.profile_ids for random selection; omit to use that YAML pool. Omit for fixed.")
    duration_seconds: Optional[int] = Field(default=None, gt=0, strict=True)
    step_duration_seconds: Optional[int] = Field(default=None, gt=0, strict=True)

    def session_request(self, catalogue):
        values = self.model_dump(exclude={"action", "expected_version", "session_id"}, exclude_none=True)
        values["duration_seconds"] = self.duration_seconds or catalogue.default_duration_seconds
        return MMSessionRequest.model_validate(values)


class SwitchCommand(StartCommand):
    action: Literal["switch"]
    session_id: str = Field(min_length=1, max_length=128)


class StopCommand(APIModel):
    action: Literal["stop"]
    expected_version: int = Field(ge=0, strict=True)
    session_id: str = Field(min_length=1, max_length=128)


CommandRequest = Annotated[Union[StartCommand, SwitchCommand, StopCommand], Field(discriminator="action")]
COMMAND_REQUEST = TypeAdapter(CommandRequest)


class CommandResponse(APIModel):
    id: str
    action: Literal["start", "switch", "stop"]
    session_id: str
    expected_version: int
    effective_version: int
    status: Literal["accepted", "applying", "succeeded", "failed", "superseded"]
    payload: CommandRequest
    error: Optional[str] = None
    created_at: UTCDateTime
    updated_at: UTCDateTime


class ProfilesResponse(APIModel):
    catalogue: MMProfilesConfig
    market_modes: List[MarketMode]
    reporting_timezone: str


class ReferenceAnchors(APIModel):
    life: Decimal
    sources: Dict[str, Decimal]


class SessionState(APIModel):
    id: str
    request: MMSessionRequest
    config_hash: str
    profile_id: str
    generation: int
    step_index: int
    status: MMSessionStatus
    started_at: Optional[UTCDateTime]
    expires_at: Optional[UTCDateTime]
    step_expires_at: Optional[UTCDateTime]
    anchors: Dict[str, ReferenceAnchors]


class MarketReadiness(APIModel):
    market: Literal["spot", "perpetual"]
    connector: str
    pair: str
    ready: bool
    admitted: bool
    reference_price: Optional[Decimal]
    reason: Optional[str]


class FundingSyncStatus(APIModel):
    state: Literal["not_configured", "waiting", "backfilling", "observed", "unavailable"]
    account_id: Optional[str] = None
    environment: Optional[Literal["live", "demo"]] = None
    pairs: List[str] = Field(default_factory=list)
    covered_from_ms: Optional[int] = Field(default=None, description="Start of the verified bill-read interval; UTC epoch milliseconds.")
    covered_until_ms: Optional[int] = Field(default=None, description="Exclusive end of the committed interval; not lifetime-history coverage.")
    last_success_ms: Optional[int] = None
    backfill_pending: bool = False
    stale: bool = True
    history_gap_from_ms: Optional[int] = None
    history_gap_until_ms: Optional[int] = None
    error: Optional[str] = None
    legacy_history_unverified: Optional[bool] = None
    source: Literal["okx_account_bills"] = "okx_account_bills"


class StatusResponse(APIModel):
    service: Optional[ServiceStatus] = None
    funding_sync: Optional[FundingSyncStatus] = None
    bot_id: str
    desired_state: Literal["running", "stopped"]
    control_version: int
    acknowledged_version: int
    running: bool
    phase: str
    owner_available: bool
    heartbeat_age_seconds: Optional[float]
    session: Optional[SessionState] = None
    observed_session: Optional[SessionState] = None
    markets: List[MarketReadiness] = Field(default_factory=list)
    server_time: Optional[UTCDateTime] = None
    time_remaining_seconds: Optional[float] = None
    error: Optional[str] = None


class HistoryQuery(APIModel):
    market: Literal["spot", "perpetual", "all"] = "all"
    pair: Optional[str] = Field(default=None, max_length=120, pattern=r"^[A-Z0-9][A-Z0-9._]*-[A-Z0-9][A-Z0-9._]*$")
    from_time: Optional[UTCDateTime] = Field(default=None, alias="from")
    to_time: Optional[UTCDateTime] = Field(default=None, alias="to")
    limit: int = Field(default=100, ge=1, le=500)
    cursor: Optional[str] = Field(default=None, max_length=2048)

    def filters(self):
        for value in (self.from_time, self.to_time):
            if value is not None and value.tzinfo is None:
                raise ValueError("History timestamps require an explicit timezone")
        if self.from_time is not None and self.to_time is not None and self.from_time >= self.to_time:
            raise ValueError("History range must be non-empty and half-open")
        return dict(market=self.market, pair=self.pair, limit=self.limit, cursor=self.cursor,
                    start_ms=math.ceil(self.from_time.timestamp() * 1000) if self.from_time else None,
                    end_ms=math.ceil(self.to_time.timestamp() * 1000) if self.to_time else None)


class OrdersQuery(HistoryQuery):
    state: Literal["open", "closed", "all"] = "all"

    def filters(self):
        return {**super().filters(), "state": self.state}


class FillsQuery(HistoryQuery):
    order_id: Optional[str] = Field(default=None, min_length=1, max_length=128)

    def filters(self):
        return {**super().filters(), "order_id": self.order_id}


class OrderDetailQuery(APIModel):
    timeline_limit: int = Field(default=100, ge=1, le=500)


class OrderView(APIModel):
    id: str
    market: Literal["spot", "perpetual"]
    connector: str
    pair: str
    side: Optional[Literal["BUY", "SELL"]]
    amount: Decimal
    price: Decimal
    order_type: str
    position: Optional[str]
    leverage: int
    status: str
    creation_pending: bool
    exchange_order_id: Optional[str]
    created_at: UTCDateTime
    updated_at: UTCDateTime


class OrdersPage(APIModel):
    items: List[OrderView]
    next_cursor: Optional[str]


class OrderTimeline(APIModel):
    id: int
    timestamp: UTCDateTime
    status: str


class OrderDetail(APIModel):
    order: OrderView
    timeline: List[OrderTimeline]
    timeline_truncated: bool
    fills_url: str


class FeeAmount(APIModel):
    token: str
    amount: Decimal


class RecordedFee(APIModel):
    fee_type: Optional[str] = None
    percent: Decimal = Decimal("0")
    percent_token: Optional[str] = None
    flat_fees: List[FeeAmount] = Field(default_factory=list)


class FillView(APIModel):
    id: str
    order_id: str
    market: Literal["spot", "perpetual"]
    connector: str
    pair: str
    side: Literal["BUY", "SELL"]
    price: Decimal
    amount: Decimal
    timestamp: UTCDateTime
    leverage: int
    position: Optional[str]
    fee: RecordedFee
    fee_in_quote: Optional[Decimal]
    quote_currency: str
    conversion_status: Literal["recorded", "unavailable", "legacy_ambiguous"]


class FillsPage(APIModel):
    items: List[FillView]
    next_cursor: Optional[str]


class ReferencePairsQuery(APIModel):
    search: str = Field(default="", max_length=120, pattern=r"^[a-zA-Z0-9._-]*$")
    after: str = Field(default="", max_length=120, pattern=r"^[A-Z0-9._-]*$")
    limit: int = Field(default=100, ge=1, le=500)


class ReferencePairsResponse(APIModel):
    connector: Literal["okx"] = "okx"
    price_source: Literal["spot_order_book_midpoint"] = "spot_order_book_midpoint"
    items: List[str]
    next_after: Optional[str]
    observed_at: Optional[UTCDateTime]
    stale: bool


class MarketQuery(APIModel):
    market: Literal["spot", "perpetual", "all"] = "all"


class BalanceObservation(APIModel):
    currency: str
    cash_balance: Optional[Decimal]
    available_cash: Optional[Decimal]
    equity: Optional[Decimal]
    available_equity: Optional[Decimal]
    observed_at: UTCDateTime
    source: Literal["okx_account_balance"]
    connector: str
    stale: bool
    present_in_latest_snapshot: bool = True


class AccountBalances(APIModel):
    account_id: Optional[str]
    environment: str
    identity_verified: bool
    balances: List[BalanceObservation]


class BalancesResponse(APIModel):
    scope: Literal["account"] = "account"
    accounts: List[AccountBalances]
    server_time: UTCDateTime
    owner_available: bool


class PositionObservation(APIModel):
    pair: str
    side: Literal["LONG", "SHORT"]
    amount: Decimal
    entry_price: Decimal
    unrealized_pnl: Decimal
    leverage: Decimal
    margin_mode: Optional[str] = None
    margin: Optional[Decimal] = None
    liquidation_price: Optional[Decimal] = None
    observed_at: Optional[UTCDateTime] = None
    stale: bool


class PositionMarket(APIModel):
    market: Literal["spot", "perpetual"]
    connector: str
    coverage: Literal["observed", "unavailable", "not_applicable"]
    observed_at: Optional[UTCDateTime] = None
    stale: bool = True
    source: Literal["okx_account_positions"] = "okx_account_positions"
    pair_observations: Dict[str, UTCDateTime] = Field(default_factory=dict)
    items: List[PositionObservation] = Field(default_factory=list)


class PositionsResponse(APIModel):
    markets: List[PositionMarket]
    server_time: UTCDateTime
    owner_available: bool


class PerformanceObservation(APIModel):
    controller_id: str
    connector: str
    pair: str
    market: Literal["spot", "perpetual"]
    observed_at: UTCDateTime
    source: Literal["v2_controller_report_cache"]
    valuation_basis: Literal["executor_and_position_hold_reports"]
    # Reuse the upstream report model; no second PnL calculation.
    report: PerformanceReport
    stale: bool

    @field_validator("report", mode="before")
    @classmethod
    def decode_persisted_close_type_keys(cls, value):
        # JSONB stringifies numeric enum map keys. Rehydrate the upstream enum
        # before its report model validates; do not recalculate performance.
        if isinstance(value, dict) and "close_type_counts" in value:
            value = {**value, "close_type_counts": {
                CloseType(int(key)) if isinstance(key, str) and key.isdecimal() else key: count
                for key, count in value["close_type_counts"].items()}}
        return value


class PerformanceMarket(APIModel):
    market: Literal["spot", "perpetual"]
    coverage: Literal["observed", "unavailable"]
    funding_status: Literal["not_applicable", "observed_only"]
    observations: List[PerformanceObservation]
    daily_statistics_url: str


class PerformanceResponse(APIModel):
    scope: Literal["controller_lifetime_and_active_exposure"] = "controller_lifetime_and_active_exposure"
    markets: List[PerformanceMarket]
    server_time: UTCDateTime
    owner_available: bool


class DailyQuery(MarketQuery):
    start_day: Optional[date] = Field(default=None, alias="from", description="Inclusive local reporting date; defaults to seven days before the exclusive end.")
    end_day: Optional[date] = Field(default=None, alias="to", description="Exclusive local reporting date; defaults to tomorrow. Maximum range: 90 days.")
    pair: Optional[str] = Field(default=None, max_length=120, pattern=r"^[A-Z0-9][A-Z0-9._]*-USDT$")


class DailyFee(APIModel):
    currency: str
    paid: Decimal
    rebates: Decimal
    net: Decimal


class DailyBucket(APIModel):
    day: date
    observed_at: UTCDateTime
    last_event_at: Optional[UTCDateTime]
    fill_count: int
    buy_volume_base: Decimal
    sell_volume_base: Decimal
    buy_volume_quote: Decimal
    sell_volume_quote: Decimal
    converted_fill_count: int
    unconverted_fill_count: int
    percentage_fee_fill_count: int
    fee_in_usdt: Optional[Decimal]
    fees: List[DailyFee]
    funding_paid: Optional[Decimal] = Field(description="Observed settlement payments; null when no settlement basis is recorded. Consult funding_sync coverage.")
    funding_received: Optional[Decimal] = Field(description="Observed settlement receipts; null when no settlement basis is recorded. Consult funding_sync coverage.")
    source: Literal["recorder_tables"] = "recorder_tables"


class DailyMarket(APIModel):
    market: Literal["spot", "perpetual"]
    connector: str
    funding_status: Literal["not_applicable", "observed_only"]
    days: List[DailyBucket]


class DailyBalance(APIModel):
    account_id: str
    environment: str
    currency: str
    reporting_timezone: str
    day: date
    observed_at: UTCDateTime
    updated_at: UTCDateTime
    connector: str
    source: str
    cash_balance: Optional[Decimal]
    available_cash: Optional[Decimal]
    equity: Optional[Decimal]
    available_equity: Optional[Decimal]


class DailyResponse(APIModel):
    start_day: date
    end_day: date
    reporting_timezone: str
    server_time: UTCDateTime
    coverage: Literal["observed_recorder_events"]
    balance_scope: Literal["account_last_observed"]
    trade_storage_scale: Optional[int] = Field(default=None, description="PostgreSQL NUMERIC has no fixed storage scale.")
    funding_precision: Literal["decimal_bills_with_legacy_float"] = "decimal_bills_with_legacy_float"
    funding_sync: Optional[FundingSyncStatus] = None
    markets: List[DailyMarket]
    balances: List[DailyBalance]


class ReconciliationDifference(APIModel):
    kind: str
    local: Optional[str]
    exchange: Optional[str]
    order_id: Optional[str] = None
    fill_id: Optional[str] = None
    pair: Optional[str] = None
    side: Optional[str] = None


class ReconciliationMarket(APIModel):
    market: Literal["spot", "perpetual"]
    state: Literal["matched", "mismatch", "incomplete", "unavailable"]
    complete: bool
    local_observed_at: Optional[UTCDateTime]
    exchange_observed_at: Optional[UTCDateTime]
    orders_checked: int
    fills_checked: int
    positions_checked: int
    reasons: List[str]
    differences: List[ReconciliationDifference]


class ReconciliationReport(APIModel):
    scope: Literal["bot_owned_orders_and_configured_perpetual_exposure"] = "bot_owned_orders_and_configured_perpetual_exposure"
    state: Literal["not_checked", "checking", "matched", "mismatch", "incomplete", "unavailable"] = "not_checked"
    started_at: Optional[UTCDateTime] = None
    finished_at: Optional[UTCDateTime] = None
    markets: List[ReconciliationMarket] = Field(default_factory=list)


class ReconciliationReceipt(APIModel):
    id: str
    status: Literal["accepted", "applying", "succeeded", "failed"]
    payload: ReconciliationRequest
    created_at: UTCDateTime
    updated_at: UTCDateTime
    error: Optional[str] = None
    report: ReconciliationReport


class ReconciliationResponse(APIModel):
    check: Optional[ReconciliationReceipt] = None
    report: ReconciliationReport = Field(default_factory=ReconciliationReport)
