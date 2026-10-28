"""Small owner-fenced pause checkpoints on the existing recovery JSON."""
from typing import Dict, Literal
from weakref import WeakValueDictionary

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hummingbot.connector.okx_account_observations import account_identity
from hummingbot.core.order_submission_guard import SubmissionBlocked
from hummingbot.strategy_v2.utils.mm_spread import SpreadPolicyConfig
from hummingbot.strategy_v2.utils.quote_guard import QuoteGuardConfig


class VolatilityMarketPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    connector_name: Literal['okx', 'okx_perpetual']
    trading_pair: str = Field(pattern=r'^[A-Z0-9][A-Z0-9._]*-USDT$')
    spread_policy: SpreadPolicyConfig
    quote_guards: QuoteGuardConfig

    @model_validator(mode='after')
    def enabled(self):
        if self.spread_policy.volatility.defense is None or not self.quote_guards.enabled:
            raise ValueError('Checkpoint requires enabled volatility defense and quote guards')
        return self

    @property
    def key(self):
        return self.connector_name + '|' + self.trading_pair


class VolatilityPause(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    policy: VolatilityMarketPolicy
    paused_at: float = Field(ge=0, allow_inf_nan=False)


class AccountBinding(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    account_id: str = Field(min_length=1, max_length=120)
    environment: Literal['live', 'demo']


class VolatilityCheckpoint(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    schema_version: Literal[1] = 1
    binding: AccountBinding
    pauses: Dict[str, VolatilityPause] = Field(max_length=2)

    @model_validator(mode='after')
    def keys(self):
        if any(key != value.policy.key for key, value in self.pauses.items()):
            raise ValueError('Invalid volatility checkpoint market identity')
        return self


def market_policy(config):
    if config.spread_policy.volatility.defense is None:
        return None
    return VolatilityMarketPolicy(connector_name=config.connector_name, trading_pair=config.trading_pair,
                                  spread_policy=config.spread_policy, quote_guards=config.quote_guards)


def validate_catalogue_pauses(recovery, catalogue):
    """Called inside existing control transactions; changing session is no reset."""
    saved = recovery.get('volatility_defense')
    if saved is None:
        return
    frame = VolatilityCheckpoint.model_validate(saved)
    policies = {policy.key: policy for config in catalogue.markets.values() if (policy := market_policy(config)) is not None}
    if any(policies.get(key) != pause.policy for key, pause in frame.pauses.items()):
        from hummingbot.core.control_errors import ControlConflict
        raise ControlConflict('A volatility pause is active. Restore its YAML policy and wait for fresh-data recovery.',
                              code='volatility_pause_policy_bound')


class VolatilityCheckpoints:
    def __init__(self, owner, controllers):
        self.owner, self.controllers = owner, WeakValueDictionary(controllers)
        self.binding = None
        self.attached = False

    def attach(self):
        try:
            binding = account_identity(self.owner.connectors)
        except ValueError as error:
            if str(error) == 'identity_unavailable':
                return False
            self.owner.require_recovery('volatility_binding_mismatch')
            raise SubmissionBlocked('Volatility account binding mismatch') from error
        if self.attached:
            if binding != self.binding:
                self.owner.require_recovery('volatility_binding_mismatch')
                raise SubmissionBlocked('Volatility account binding mismatch')
            return True
        row = self.owner.check_owner()
        try:
            raw = (row['recovery_state'] or {}).get('volatility_defense')
            frame = VolatilityCheckpoint.model_validate(raw) if raw is not None else None
            if frame is not None and frame.binding.model_dump() != binding:
                raise ValueError('binding_mismatch')
            for controller in self.controllers.values():
                key = controller.config.connector_name + '|' + controller.config.trading_pair
                pause = frame.pauses.get(key) if frame else None
                policy = market_policy(controller.config)
                if pause is not None and pause.policy != policy:
                    raise ValueError('policy_mismatch')
                if policy is not None:
                    controller.bind_volatility_defense(paused=pause is not None,
                                                       checkpoint=lambda paused, policy=policy: self.checkpoint(policy, paused))
        except (ValueError, TypeError) as error:
            self.owner.require_recovery('volatility_checkpoint_invalid')
            raise SubmissionBlocked('Volatility checkpoint requires operator review') from error
        self.binding, self.attached = binding, True
        return True

    def checkpoint(self, policy, paused):
        if account_identity(self.owner.connectors) != self.binding:
            raise SubmissionBlocked('Volatility account binding changed')
        row = self.owner.check_owner()
        if not paused and (row['recovery_state'] or {}).get('halt'):
            return False
        now = float(row['database_now'])

        def change(state):
            raw = state.get('volatility_defense')
            frame = VolatilityCheckpoint.model_validate(raw) if raw is not None else VolatilityCheckpoint(binding=self.binding, pauses={})
            if frame.binding.model_dump() != self.binding or state.get('halt') and not paused:
                raise SubmissionBlocked('Volatility checkpoint authority changed')
            pauses = dict(frame.pauses)
            previous = pauses.get(policy.key)
            if previous is not None and previous.policy != policy:
                raise SubmissionBlocked('Volatility pause policy changed')
            if paused:
                pauses[policy.key] = previous or VolatilityPause(policy=policy, paused_at=now)
            else:
                pauses.pop(policy.key, None)
            state['volatility_defense'] = VolatilityCheckpoint(binding=self.binding, pauses=pauses).model_dump(mode='json')
        # A superseding Stop/Switch cannot discard a newly observed breach.
        # Recording it grants no trading permission. Clearing still requires
        # the exact current control version in addition to owner fencing.
        return self.owner._mutate_recovery(change, expected_version=None if paused else self.owner.version)
