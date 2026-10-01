"""Validated LIFE strategy configuration. No connector or order-sending imports.

The V2 controller/CLI adapter is a separate integration point. Live mode remains
disabled until the later risk, execution, and release gates have been implemented.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, field_validator, model_validator

_DURATION = re.compile(r"([0-9]+(?:\.[0-9]+)?)([smhd])")
_SECONDS_PER_UNIT = {"s": Decimal("1"), "m": Decimal("60"), "h": Decimal("3600"), "d": Decimal("86400")}


def parse_duration_seconds(value: str) -> Decimal:
    """Parse a positive duration with an explicit unit and exact arithmetic."""
    match = _DURATION.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ValueError("duration must be a positive number followed by s, m, h, or d")
    seconds = Decimal(match.group(1)) * _SECONDS_PER_UNIT[match.group(2)]
    if not seconds.is_finite() or seconds <= 0:
        raise ValueError("duration must be finite and positive")
    if seconds > Decimal(str(timedelta.max.total_seconds())):
        raise ValueError("duration exceeds the supported clock range")
    return seconds


def bps_to_fraction(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("basis points must be a finite Decimal")
    return value / Decimal("10000")


def _exact_decimal(value: object) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("use a decimal string or integer, never a binary float")
    try:
        number = value if isinstance(value, Decimal) else Decimal(value)
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise ValueError("value must be a decimal number") from exc
    if not number.is_finite():
        raise ValueError("value must be finite")
    return number


ExactDecimal = Annotated[Decimal, BeforeValidator(_exact_decimal)]


class StrictConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SpotConfig(StrictConfig):
    enabled: bool = True
    connector: Literal["okx"] = "okx"
    pair: Literal["LIFE-USDT"] = "LIFE-USDT"


class PerpetualConfig(StrictConfig):
    enabled: bool = False
    connector: Literal["okx_perpetual"] = "okx_perpetual"
    pair: Literal["LIFE-USDT"] = "LIFE-USDT"
    position_mode: Optional[Literal["HEDGE", "ONEWAY"]] = None
    margin_mode: Optional[Literal["cross"]] = None
    leverage: Optional[int] = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_explicit_mode_when_enabled(self) -> "PerpetualConfig":
        if self.enabled and (self.position_mode is None or self.margin_mode is None or self.leverage is None):
            raise ValueError("enabled perpetual requires position_mode, margin_mode, and leverage")
        return self


class SessionConfig(StrictConfig):
    duration: str
    start_policy: str = "when_ready"
    on_expiry: Literal["pause_quotes", "switch_to_market_reference"] = "pause_quotes"
    successor_duration: Optional[str] = None

    @field_validator("duration", "successor_duration")
    @classmethod
    def validate_duration(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            parse_duration_seconds(value)
        return value

    @field_validator("start_policy")
    @classmethod
    def validate_start_policy(cls, value: str) -> str:
        if value == "when_ready":
            return value
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("start_policy must be when_ready or an ISO 8601 UTC timestamp") from exc
        if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
            raise ValueError("start_policy timestamp must specify UTC")
        return value

    @model_validator(mode="after")
    def validate_expiry_policy(self) -> "SessionConfig":
        if self.on_expiry == "switch_to_market_reference" and self.successor_duration is None:
            raise ValueError("switch_to_market_reference requires successor_duration")
        if self.on_expiry == "pause_quotes" and self.successor_duration is not None:
            raise ValueError("successor_duration requires switch_to_market_reference")
        return self

    @property
    def duration_seconds(self) -> Decimal:
        return parse_duration_seconds(self.duration)

    @property
    def successor_duration_seconds(self) -> Optional[Decimal]:
        return parse_duration_seconds(self.successor_duration) if self.successor_duration is not None else None


class ReferenceSourceConfig(StrictConfig):
    connector: str
    pair: str
    quote_currency: Literal["USDT"]
    weight: ExactDecimal

    @model_validator(mode="after")
    def validate_source(self) -> "ReferenceSourceConfig":
        if not self.connector or not re.fullmatch(r"[A-Z0-9]+-USDT", self.pair):
            raise ValueError("source requires a connector and a supported USDT-quoted pair")
        if self.weight <= 0:
            raise ValueError("source weight must be positive")
        return self


class ReferenceConfig(StrictConfig):
    mode: Literal["market", "bounded_benchmark", "bootstrap_simulation"] = "market"
    sources: tuple[ReferenceSourceConfig, ...] = ()
    lookback: str
    influence: ExactDecimal = Decimal("0")
    max_age: Optional[str] = None
    max_deviation_bps: Optional[ExactDecimal] = None
    transition_duration: Optional[str] = None

    @field_validator("lookback", "max_age", "transition_duration")
    @classmethod
    def validate_duration(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            parse_duration_seconds(value)
        return value

    @model_validator(mode="after")
    def validate_reference(self) -> "ReferenceConfig":
        if not Decimal("0") <= self.influence <= Decimal("1"):
            raise ValueError("reference influence must be a fraction between zero and one")
        if self.max_deviation_bps is not None and self.max_deviation_bps <= 0:
            raise ValueError("reference deviation limit must be positive")
        if self.mode == "bounded_benchmark":
            if len(self.sources) != 1 or self.sources[0].weight != Decimal("1"):
                raise ValueError("MVP benchmark mode requires exactly one source with weight one")
        elif self.sources or self.influence != 0:
            raise ValueError("market/bootstrap modes cannot use benchmark sources or influence")
        return self

    @property
    def lookback_seconds(self) -> Decimal:
        return parse_duration_seconds(self.lookback)


class QuotesConfig(StrictConfig):
    spreads_bps: tuple[ExactDecimal, ...]
    sizes_base: tuple[ExactDecimal, ...]

    @model_validator(mode="after")
    def validate_levels(self) -> "QuotesConfig":
        if not self.spreads_bps or len(self.spreads_bps) != len(self.sizes_base):
            raise ValueError("quote spread and size levels must be nonempty and match")
        if any(spread <= 0 for spread in self.spreads_bps):
            raise ValueError("quote spreads must be positive")
        if any(size <= 0 for size in self.sizes_base):
            raise ValueError("quote sizes must be positive")
        return self


class RiskConfig(StrictConfig):
    min_inventory_base: Optional[ExactDecimal] = None
    max_inventory_base: Optional[ExactDecimal] = None
    max_gross_quote: Optional[ExactDecimal] = None
    max_net_base: Optional[ExactDecimal] = None
    max_drawdown_bps: Optional[ExactDecimal] = None
    margin_buffer_quote: Optional[ExactDecimal] = None
    rolling_fill_window: Optional[str] = None
    max_filled_base_per_window: Optional[ExactDecimal] = None
    markout_horizons: tuple[str, ...] = ()
    markout_min_samples: Optional[int] = Field(default=None, ge=1)
    stress_loss_budget_quote: Optional[ExactDecimal] = None
    execution_loss_budget_quote: Optional[ExactDecimal] = None
    resume_policy: "ResumePolicyConfig" = Field(default_factory=lambda: ResumePolicyConfig())

    @field_validator("rolling_fill_window")
    @classmethod
    def validate_fill_window(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            parse_duration_seconds(value)
        return value

    @model_validator(mode="after")
    def validate_limits(self) -> "RiskConfig":
        for value in (
            self.max_gross_quote, self.max_net_base, self.max_drawdown_bps,
            self.stress_loss_budget_quote, self.execution_loss_budget_quote,
            self.max_filled_base_per_window,
        ):
            if value is not None and value <= 0:
                raise ValueError("risk limits must be positive")
        for value in (self.min_inventory_base, self.max_inventory_base, self.margin_buffer_quote):
            if value is not None and value < 0:
                raise ValueError("inventory/margin limits must be nonnegative")
        if (self.min_inventory_base is not None and self.max_inventory_base is not None
                and self.min_inventory_base > self.max_inventory_base):
            raise ValueError("minimum inventory cannot exceed maximum inventory")
        if (self.rolling_fill_window is None) != (self.max_filled_base_per_window is None):
            raise ValueError("rolling fill limit needs both a window and a maximum quantity")
        if bool(self.markout_horizons) != (self.markout_min_samples is not None):
            raise ValueError("markout horizons and minimum samples must be configured together")
        for horizon in self.markout_horizons:
            parse_duration_seconds(horizon)
        if len(self.markout_horizons) != len(set(self.markout_horizons)):
            raise ValueError("markout horizons must be unique")
        return self


class ResumePolicyConfig(StrictConfig):
    stable_data_duration: Optional[str] = None
    probe_size_base: Optional[ExactDecimal] = None
    clear_halt_automatically: Literal[False] = False

    @model_validator(mode="after")
    def validate_resume(self) -> "ResumePolicyConfig":
        if self.stable_data_duration is not None:
            parse_duration_seconds(self.stable_data_duration)
        if self.probe_size_base is not None and self.probe_size_base <= 0:
            raise ValueError("probe size must be positive")
        if (self.stable_data_duration is None) != (self.probe_size_base is None):
            raise ValueError("recovery probes need both a stable-data period and size")
        return self


class SubsidyBudgetConfig(StrictConfig):
    campaign: ExactDecimal
    day: Optional[ExactDecimal] = None
    session: Optional[ExactDecimal] = None

    @model_validator(mode="after")
    def validate_windows(self) -> "SubsidyBudgetConfig":
        if self.campaign <= 0 or any(value is not None and value <= 0 for value in (self.day, self.session)):
            raise ValueError("subsidy budgets must be positive")
        if self.day is not None and self.day > self.campaign:
            raise ValueError("daily subsidy cannot exceed campaign budget")
        if self.session is not None and self.session > (self.day or self.campaign):
            raise ValueError("session subsidy cannot exceed its enclosing budget")
        return self


class EconomicsConfig(StrictConfig):
    objective: Literal["profit_mm", "liquidity_service"] = "profit_mm"
    min_net_edge_bps: Optional[ExactDecimal] = None
    uncertainty_buffer_bps: Optional[ExactDecimal] = None
    subsidy_budget_quote: Optional[SubsidyBudgetConfig] = None
    fee_policy: Optional[Literal["pause", "conservative_ceiling"]] = None
    fee_ceiling_bps: Optional[ExactDecimal] = None
    fee_max_age: Optional[str] = None
    holding_horizon: Optional[str] = None

    @field_validator("fee_max_age", "holding_horizon")
    @classmethod
    def validate_duration(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            parse_duration_seconds(value)
        return value

    @model_validator(mode="after")
    def validate_economics(self) -> "EconomicsConfig":
        for value in (
            self.min_net_edge_bps, self.uncertainty_buffer_bps,
            self.fee_ceiling_bps,
        ):
            if value is not None and value < 0:
                raise ValueError("economic limits and budgets must be nonnegative")
        if self.fee_policy == "conservative_ceiling" and self.fee_ceiling_bps is None:
            raise ValueError("conservative_ceiling requires fee_ceiling_bps")
        if self.fee_policy != "conservative_ceiling" and self.fee_ceiling_bps is not None:
            raise ValueError("fee_ceiling_bps requires conservative_ceiling policy")
        if self.objective == "liquidity_service" and self.subsidy_budget_quote is None:
            raise ValueError("liquidity_service requires an explicit subsidy budget")
        return self


class InventoryConfig(StrictConfig):
    target_base: Optional[ExactDecimal] = None
    deadline: Optional[str] = None
    price_limit: Optional[ExactDecimal] = None
    max_participation: Optional[ExactDecimal] = None

    @model_validator(mode="after")
    def validate_inventory(self) -> "InventoryConfig":
        if self.target_base is not None and self.target_base < 0:
            raise ValueError("spot inventory target cannot be negative")
        if self.deadline is not None:
            parse_duration_seconds(self.deadline)
        if self.target_base is not None and self.deadline is None:
            raise ValueError("inventory target requires an execution deadline")
        if self.price_limit is not None and self.price_limit <= 0:
            raise ValueError("inventory price limit must be positive")
        if self.max_participation is not None and not Decimal("0") < self.max_participation <= Decimal("1"):
            raise ValueError("inventory participation must be a fraction in (0, 1]")
        return self


class HedgeConfig(StrictConfig):
    deadband_base: Optional[ExactDecimal] = None
    max_unhedged_base: Optional[ExactDecimal] = None
    max_unhedged_duration: Optional[str] = None
    max_slippage_bps: Optional[ExactDecimal] = None
    basis_limit_bps: Optional[ExactDecimal] = None
    funding_budget_quote: Optional[ExactDecimal] = None

    @model_validator(mode="after")
    def validate_hedge(self) -> "HedgeConfig":
        if self.deadband_base is not None and self.deadband_base < 0:
            raise ValueError("hedge deadband cannot be negative")
        for value in (
            self.max_unhedged_base, self.max_slippage_bps,
            self.basis_limit_bps, self.funding_budget_quote,
        ):
            if value is not None and value <= 0:
                raise ValueError("hedge limits must be positive")
        if self.max_unhedged_duration is not None:
            parse_duration_seconds(self.max_unhedged_duration)
        if (self.max_unhedged_base is None) != (self.max_unhedged_duration is None):
            raise ValueError("unhedged exposure needs both a maximum size and duration")
        return self

    @property
    def max_unhedged_duration_seconds(self) -> Optional[Decimal]:
        return parse_duration_seconds(self.max_unhedged_duration) if self.max_unhedged_duration is not None else None


class StrategyConfig(StrictConfig):
    execution_mode: Literal["simulation", "shadow", "demo", "live"] = "simulation"
    spot: SpotConfig = Field(default_factory=SpotConfig)
    perpetual: PerpetualConfig = Field(default_factory=PerpetualConfig)
    session: SessionConfig
    reference: ReferenceConfig
    quotes: QuotesConfig
    risk: RiskConfig = Field(default_factory=RiskConfig)
    economics: EconomicsConfig = Field(default_factory=EconomicsConfig)
    inventory: InventoryConfig = Field(default_factory=InventoryConfig)
    hedge: HedgeConfig = Field(default_factory=HedgeConfig)
    config_version: int = Field(default=1, ge=1)

    @model_validator(mode="before")
    @classmethod
    def require_explicit_live_objective(cls, value: object) -> object:
        if isinstance(value, dict) and value.get("execution_mode") == "live":
            economics = value.get("economics")
            if not isinstance(economics, dict) or "objective" not in economics:
                raise ValueError("live configuration requires explicit economics.objective")
        return value

    @model_validator(mode="after")
    def validate_mode(self) -> "StrategyConfig":
        if self.reference.mode == "bootstrap_simulation" and self.execution_mode != "simulation":
            raise ValueError("bootstrap_simulation is simulation-only")
        if self.perpetual.enabled and self.execution_mode != "simulation":
            raise ValueError("perpetual execution is simulation-only until its P6 gate")
        if self.reference.mode == "bounded_benchmark" and self.execution_mode == "demo":
            raise ValueError("bounded benchmark demo requires the P3 evidence gate")
        if not self.spot.enabled and not self.perpetual.enabled:
            raise ValueError("at least one market must be enabled")
        if self.execution_mode == "live":
            required = {
                "economics.min_net_edge_bps": self.economics.min_net_edge_bps,
                "economics.uncertainty_buffer_bps": self.economics.uncertainty_buffer_bps,
                "economics.fee_policy": self.economics.fee_policy,
                "economics.fee_max_age": self.economics.fee_max_age,
                "economics.holding_horizon": self.economics.holding_horizon,
                "risk.max_gross_quote": self.risk.max_gross_quote,
                "risk.max_net_base": self.risk.max_net_base,
                "risk.max_drawdown_bps": self.risk.max_drawdown_bps,
                "risk.stress_loss_budget_quote": self.risk.stress_loss_budget_quote,
                "risk.execution_loss_budget_quote": self.risk.execution_loss_budget_quote,
                "reference.max_age": self.reference.max_age,
                "reference.max_deviation_bps": self.reference.max_deviation_bps,
            }
            missing = [name for name, value in required.items() if value is None]
            if missing:
                raise ValueError(f"live configuration requires: {', '.join(missing)}")
            raise ValueError("live execution remains disabled until the P9 release gate")
        return self


_HOT_RELOAD_FIELDS = {
    "session": {"duration"},
    "reference": {"lookback"},
    "quotes": {"spreads_bps", "sizes_base"},
}


def apply_update(config: StrategyConfig, changes: dict) -> StrategyConfig:
    """Validate an allowed nested update as one new immutable config version."""
    if not isinstance(changes, dict) or not changes:
        raise ValueError("hot reload requires a nonempty mapping")
    data = config.model_dump(mode="python")
    for section, fields in changes.items():
        allowed = _HOT_RELOAD_FIELDS.get(section)
        if allowed is None or not isinstance(fields, dict) or not fields or set(fields) - allowed:
            raise ValueError(f"unsupported hot-reload field in {section}")
        data[section].update(fields)
    data["config_version"] = config.config_version + 1
    return StrategyConfig.model_validate(data)


@dataclass(frozen=True)
class ConfigUpdateDecision:
    applied: bool
    reason_code: Literal[
        "CONFIG_UPDATE_APPLIED", "CONFIG_UPDATE_UNSUPPORTED", "CONFIG_VALIDATION_FAILED", "CONFIG_LOAD_FAILED",
    ]
    active_version: int


class ConfigUpdateState:
    """Atomic config versions and a fail-closed flag for the future order gate.

    A rejected update latches the gate closed. A later valid update does not
    implicitly resume quoting; the controller must reconcile and use a separate
    authorized resume path before it may send new orders.
    """

    def __init__(self, config: StrategyConfig):
        self.config = config
        self.last_decision: Optional[ConfigUpdateDecision] = None
        self.last_rejection: Optional[ConfigUpdateDecision] = None
        self._rejected_update = False

    def try_update(self, changes: dict) -> ConfigUpdateDecision:
        try:
            candidate = apply_update(self.config, changes)
        except ValidationError:
            decision = ConfigUpdateDecision(False, "CONFIG_VALIDATION_FAILED", self.config.config_version)
            self._rejected_update = True
        except ValueError:
            decision = ConfigUpdateDecision(False, "CONFIG_UPDATE_UNSUPPORTED", self.config.config_version)
            self._rejected_update = True
        else:
            self.config = candidate
            decision = ConfigUpdateDecision(True, "CONFIG_UPDATE_APPLIED", candidate.config_version)
        self.last_decision = decision
        if not decision.applied:
            self.last_rejection = decision
        return decision

    def order_permission(self) -> bool:
        return not self._rejected_update

    def reject(self, reason_code: Literal["CONFIG_LOAD_FAILED", "CONFIG_UPDATE_UNSUPPORTED"]) -> ConfigUpdateDecision:
        """Record a rejection raised before a candidate reaches ``apply_update``."""
        decision = ConfigUpdateDecision(False, reason_code, self.config.config_version)
        self._rejected_update = True
        self.last_decision = decision
        self.last_rejection = decision
        return decision
