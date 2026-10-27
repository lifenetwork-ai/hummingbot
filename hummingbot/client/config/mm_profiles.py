"""Validated MM catalogue; execution remains in the existing V2 controllers."""
from decimal import Decimal
from typing import Dict, List, Literal, Optional, Set

from pydantic import BaseModel, ConfigDict, Field, model_validator

from controllers.market_making.life_mm import LifeMMConfig, ReferencePair
from hummingbot.client.config.config_helpers import load_yml_into_dict
from hummingbot.core.control_errors import RequestError
from hummingbot.core.listing_bootstrap import BootstrapMarketPolicy, canonical_policy
from hummingbot.core.mm_drawdown import MMDrawdownMarket, drawdown_policy
from hummingbot.core.spot_outflow import SpotOutflowConfig, SpotOutflowPolicy
from hummingbot.strategy_v2.utils.quote_guard import SidewaysRange

Scenario = Literal["follow_market", "sideways", "follow_token"]
MarketMode = Literal["spot", "perpetual", "both"]


class MMProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario: Scenario
    references: List[ReferencePair] = Field(default_factory=list)
    anchor: Literal["session_start_mid"] = "session_start_mid"
    sideways_range: Optional[SidewaysRange] = None

    @model_validator(mode="after")
    def range_scenario(self):
        if self.sideways_range is not None and self.scenario != "sideways":
            raise ValueError("Manual range only applies to a sideways profile")
        return self


class RandomSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_ids: List[str] = Field(min_length=1, description="Profiles available for random selection; each has equal selection probability.")
    step_duration_seconds: int = Field(default=600, gt=0, strict=True)

    @model_validator(mode="after")
    def unique_profiles(self):
        if len(set(self.profile_ids)) != len(self.profile_ids):
            raise ValueError("Random profile IDs must be unique")
        return self


class MMProfilesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference_connector: Literal["okx"] = "okx"
    profiles: Dict[str, MMProfile]
    markets: Dict[Literal["spot", "perpetual"], LifeMMConfig]
    random: RandomSelection
    min_duration_seconds: int = Field(default=60, gt=0, strict=True)
    default_duration_seconds: int = Field(default=3600, gt=0, strict=True)
    max_duration_seconds: int = Field(default=86400, gt=0, strict=True)
    max_total_amount_quote: Decimal = Field(gt=0, allow_inf_nan=False)
    max_global_drawdown_quote: Optional[Decimal] = Field(default=None, gt=0, allow_inf_nan=False)
    max_controller_drawdown_quote: Optional[Decimal] = Field(default=None, gt=0, allow_inf_nan=False)
    spot_outflow: Optional[SpotOutflowConfig] = None

    def bootstrap_policies(self):
        values = {}
        for template in self.markets.values():
            if template.quote_guards.mode == "bootstrap_then_market":
                fields = {name: getattr(template, name) for name in BootstrapMarketPolicy.model_fields}
                values[template.id] = canonical_policy(BootstrapMarketPolicy(**fields).model_dump())
        return values or None

    def outflow_policy(self):
        if self.spot_outflow is None:
            return None
        if "spot" not in self.markets:
            raise ValueError("Spot outflow requires a stable spot market template")
        policy = SpotOutflowPolicy(**self.spot_outflow.model_dump(), trading_pair=self.markets["spot"].trading_pair).model_dump(mode="json")
        for field in ("max_net_outflow_quote", "fee_allowance_rate"):
            policy[field] = format(Decimal(policy[field]).normalize(), "f")
        return policy

    def drawdown_policy(self):
        if self.max_global_drawdown_quote is None and self.max_controller_drawdown_quote is None:
            return None
        scope = {config.id: MMDrawdownMarket(connector_name=config.connector_name, trading_pair=config.trading_pair)
                 for config in self.markets.values()}
        return drawdown_policy(scope, self.max_global_drawdown_quote, self.max_controller_drawdown_quote)

    @classmethod
    async def load(cls, path):
        return cls.model_validate(await load_yml_into_dict(str(path)))

    @model_validator(mode="after")
    def validate_catalogue(self):
        self.outflow_policy()
        bootstrap = self.bootstrap_policies()
        if bootstrap is not None:
            if self.max_global_drawdown_quote is None or self.spot_outflow is None:
                raise ValueError("Bootstrap requires existing global drawdown and spot outflow protection")
            spot = self.markets.get("spot")
            if (spot is not None and spot.quote_guards.mode == "bootstrap_then_market"
                    and spot.quote_guards.bootstrap.duration_seconds > self.spot_outflow.window_seconds):
                raise ValueError("Spot bootstrap duration cannot exceed its outflow window")
        if not self.profiles or not self.markets:
            raise ValueError("Profiles and market templates cannot be empty")
        if not self.min_duration_seconds <= self.default_duration_seconds <= self.max_duration_seconds:
            raise ValueError("Default duration must be within configured bounds")
        if self.random.step_duration_seconds > self.max_duration_seconds:
            raise ValueError("Random step exceeds maximum session duration")
        if not set(self.random.profile_ids) <= set(self.profiles):
            raise ValueError("Each random profile ID must exist in profiles")
        if len({template.id for template in self.markets.values()}) != len(self.markets):
            raise ValueError("Market controller IDs must be unique")
        if len({template.trading_pair for template in self.markets.values()}) != 1:
            raise ValueError("Both markets must use the same LIFE trading pair")
        for market, template in self.markets.items():
            if self.drawdown_policy() is not None and template.initial_positions:
                raise ValueError("Managed drawdown restores recorded exposure; initial position overrides are unsupported")
            if template.sideways_range is not None:
                raise ValueError("Put manual sideways_range in its profile, not in the shared market template")
            if template.connector_name != {"spot": "okx", "perpetual": "okx_perpetual"}[market]:
                raise ValueError("Market template has the wrong connector")
            if template.total_amount_quote > self.max_total_amount_quote:
                raise ValueError("Market budget exceeds total budget")
            for profile in self.profiles.values():
                LifeMMConfig.model_validate({**template.model_dump(), "scenario": profile.scenario,
                                             "references": profile.references,
                                             "sideways_range": profile.sideways_range,
                                             "reference_connector": self.reference_connector})
        return self

    def validate_duration(self, duration_seconds):
        if (type(duration_seconds) is not int
                or not self.min_duration_seconds <= duration_seconds <= self.max_duration_seconds):
            raise RequestError("Duration is outside the configured integer-second bounds", code="duration_out_of_range", field="duration_seconds")
        return duration_seconds

    def controller_configs(self, profile_id: str, market_mode: MarketMode,
                           supported_pairs: Optional[Dict[str, Set[str]]],
                           references: Optional[List[ReferencePair]] = None,
                           available_quote: Optional[Decimal] = None):
        """Select defaults or API references against full, tradable instrument metadata."""
        if market_mode not in ("spot", "perpetual", "both"):
            raise ValueError("Unsupported market mode")
        if profile_id not in self.profiles:
            raise RequestError("Unknown profile", code="profile_unavailable", field="profile_id")
        profile = self.profiles[profile_id]
        selected = ("spot", "perpetual") if market_mode == "both" else (market_mode,)
        configs = []
        for market in selected:
            if market not in self.markets:
                raise ValueError(f"Market is not configured: {market}")
            config = LifeMMConfig.model_validate({
                **self.markets[market].model_dump(), "scenario": profile.scenario,
                "reference_connector": self.reference_connector,
                "references": profile.references if references is None else references,
                "sideways_range": profile.sideways_range,
                "runtime_config_managed": True,
            })
            if supported_pairs is not None and config.trading_pair not in supported_pairs.get(config.connector_name, set()):
                raise ValueError(f"Trading pair unavailable on {config.connector_name}")
            if supported_pairs is not None and any(
                    ref.pair not in supported_pairs.get(self.reference_connector, set()) for ref in config.references):
                raise ValueError("reference_pair_unavailable")
            configs.append(config)
        declared = sum(config.total_amount_quote for config in configs)
        if declared > self.max_total_amount_quote:
            raise ValueError("Combined market budgets exceed the configured total")
        if available_quote is not None:
            if not available_quote.is_finite() or available_quote < declared:
                raise ValueError("Combined market budgets exceed available shared collateral")
        return configs
