"""LIFE V2 controller adapter for configuration discovery and offline validation.

The trading controller is deliberately inert until the P2–P9 data, accounting,
order, and release gates are implemented. Loading this module cannot place orders.
"""

from decimal import Decimal
from typing import Literal

from pydantic import Field

from hummingbot.strategy_v2.controllers.controller_base import ControllerBase, ControllerConfigBase
from hummingbot.strategy_v2.life_liquidity.config import ConfigUpdateState, StrategyConfig


def _simulation_template() -> StrategyConfig:
    """Synthetic CLI create template; never a live allocation or trading policy."""
    return StrategyConfig.model_validate({
        "execution_mode": "simulation",
        "session": {"duration": "4h"},
        "reference": {"mode": "market", "lookback": "15m"},
        "quotes": {"spreads_bps": ["30"], "sizes_base": ["10"]},
        "economics": {"objective": "profit_mm"},
    })


class LifeLiquidityConfig(ControllerConfigBase):
    controller_type: Literal["generic"] = "generic"
    controller_name: Literal["life_liquidity"] = "life_liquidity"
    total_amount_quote: Decimal = Field(
        default=Decimal("0"),
        json_schema_extra={"is_updatable": False},
    )
    strategy: StrategyConfig = Field(
        default_factory=_simulation_template,
        description="Nested settings can be edited in YAML or with hbot config dotted keys; restart to apply.",
        json_schema_extra={"prompt_on_new": False, "is_updatable": False},
    )

    def update_markets(self, markets):
        # Register LIFE only after the P2 listing and market-data gate exists.
        return markets


class LifeLiquidityController(ControllerBase):
    def __init__(self, config: LifeLiquidityConfig, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        self.config_update_state = ConfigUpdateState(config.strategy)

    def update_config(self, new_config: LifeLiquidityConfig):
        if new_config.strategy != self.config.strategy:
            self.config_update_state.reject("CONFIG_UPDATE_UNSUPPORTED")
            raise ValueError("LIFE nested hot reload is unavailable until the controller gate is implemented")
        super().update_config(new_config)

    def on_config_load_failure(self):
        return self.config_update_state.reject("CONFIG_LOAD_FAILED")

    def trading_permissions_ready(self) -> bool:
        # P4 will replace this with the final order-permission checks.
        return False

    def allow_create_executor_actions(self) -> bool:
        return self.config_update_state.order_permission() and self.trading_permissions_ready()

    async def update_processed_data(self):
        self.processed_data = {"reason_code": "LIFE_CONTROLLER_INERT"}

    def determine_executor_actions(self):
        return []

    def to_format_status(self):
        return ["LIFE liquidity: configuration adapter only; trading is disabled pending P2–P9 gates."]
