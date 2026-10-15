"""Explicit file-service settings. Values are detached before worker startup."""
import math
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, model_validator

from controllers.generic.life_liquidity import LifeLiquidityConfig
from hummingbot.strategy_v2.life_liquidity.hedge_coordinator import HedgeExecutionPolicy
from hummingbot.strategy_v2.life_liquidity.market_data import LinearSwapContract
from hummingbot.strategy_v2.life_liquidity.request_budget import RequestBudgetPolicy
from hummingbot.strategy_v2.life_liquidity.risk import RiskLimits
from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalPolicy
from hummingbot.strategy_v2.life_liquidity.storage.capital import SpotAccountingPolicy
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken, StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.file import FileLimits
from hummingbot.strategy_v2.life_liquidity.storage.financial import BudgetPolicy, SpotFillPolicy
from hummingbot.strategy_v2.life_liquidity.storage.joint import JointLimits
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryPolicy
from hummingbot.strategy_v2.life_liquidity.storage.terminal import SpotTerminalPolicy


class JointServiceConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    limits: JointLimits
    max_age_ms: int
    cancel_retry_ms: int
    max_cancels_per_cycle: int
    poll_interval_ms: int
    opening_long_entry: Decimal
    opening_short_entry: Decimal

    @model_validator(mode='after')
    def validate_values(self):
        values = (self.max_age_ms, self.cancel_retry_ms, self.max_cancels_per_cycle, self.poll_interval_ms)
        if (any(type(v) is not int or v <= 0 for v in values) or self.poll_interval_ms > self.max_age_ms
                or any(not v.is_finite() or v < 0 for v in (self.opening_long_entry, self.opening_short_entry))):
            raise StorageError('SERVICE_JOINT_CONFIG_INVALID')
        return self


class FileServiceConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    controller: LifeLiquidityConfig
    state_dir: str
    account_lock_root: str
    owner: OwnerToken
    file_limits: FileLimits
    risk_limits: RiskLimits
    subsidy_policy: BudgetPolicy
    loss_policy: BudgetPolicy
    accounting_policy: SpotAccountingPolicy
    fill_policy: SpotFillPolicy
    terminal_policy: SpotTerminalPolicy
    recovery_policy: SpotFinancialRecoveryPolicy
    config_version: int
    max_pending: int
    shutdown_timeout_s: float
    ack_timeout_s: float
    recorder_path: str
    recorder_config_name: str
    observation_max_pages: int
    capital_policy: CapitalPolicy | None = None
    swap_contract: LinearSwapContract | None = None
    request_policy: RequestBudgetPolicy | None = None
    joint: JointServiceConfig | None = None
    hedge_policy: HedgeExecutionPolicy | None = None

    @model_validator(mode='after')
    def validate_values(self):
        binding = self.controller.storage_binding
        if (self.controller.storage_backend != 'file' or binding is None
                or binding.account_uid != self.controller.recovery_account_uid
                or binding.config_hash != fingerprint(self.controller.strategy.model_dump(mode='json'))
                or self.config_version < 1 or not 0 < self.observation_max_pages <= 64 or not 0 < self.max_pending <= self.file_limits.max_pending
                or any(not math.isfinite(v) or v <= 0 for v in (self.shutdown_timeout_s, self.ack_timeout_s))
                or not self.controller.strategy.spot.enabled):
            raise StorageError('SERVICE_CONFIG_INVALID')
        for value in (self.state_dir, self.account_lock_root, self.recorder_path):
            if not Path(value).is_absolute():
                raise StorageError('SERVICE_PATH_NOT_ABSOLUTE')
        enabled = self.controller.strategy.perpetual.enabled
        required = (self.capital_policy, self.swap_contract, self.request_policy, self.joint)
        incomplete = any(v is None for v in required) if enabled else any(v is not None for v in required)
        if incomplete:
            raise StorageError('SERVICE_PRODUCT_CONFIG_INCOMPLETE')
        if self.hedge_policy is not None and not enabled:
            raise StorageError('SERVICE_HEDGE_REQUIRES_SWAP')
        if enabled and self.capital_policy.account_uid != binding.account_uid:
            raise StorageError('SERVICE_CAPITAL_UID_MISMATCH')
        return self
