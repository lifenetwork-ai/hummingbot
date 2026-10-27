"""Bounded synthetic assumptions and exogenous tapes, never fitted LIFE prices."""
import hashlib
import json
import random
from decimal import Decimal
from typing import Annotated, Dict, List, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from controllers.market_making.life_mm import ReferencePair
from hummingbot.client.config.mm_profiles import MMProfile

SCENARIOS = Literal['empty_no_flow', 'sideways', 'sustained_buys', 'sustained_sells', 'reference_divergence', 'gap_slow_cancel']
PROFILE = Literal['sideways', 'follow_market', 'follow_token']
MODE = Literal['spot', 'perpetual', 'both']
D = Decimal


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, frozen=True)


class ExecutionAssumptions(StrictModel):
    maker_fee_rate: Decimal = Field(ge=D('-.01'), lt=D('.1'))
    placement_latency_seconds: int = Field(ge=1, le=30, strict=True)
    cancel_latency_seconds: int = Field(ge=1, le=30, strict=True)
    queue_ahead_base: Decimal = Field(ge=0)
    max_fill_fraction_per_event: Decimal = Field(gt=0, le=1)
    funding_rate_per_event: Decimal = Field(gt=-1, lt=1)


class Variant(StrictModel):
    fee_floor: bool
    volatility: bool
    inventory_skew: bool
    defense: bool = False

    @model_validator(mode='after')
    def defense_dependencies(self):
        if self.defense and not (self.fee_floor and self.volatility):
            raise ValueError('Defense replay requires volatility and maker fee floor')
        return self


class Experiment(StrictModel):
    version: Literal[1]
    duration_seconds: int = Field(ge=60, le=3600, strict=True)
    seeds: List[Annotated[int, Field(ge=0, lt=2**32, strict=True)]] = Field(min_length=1, max_length=8)
    scenarios: List[SCENARIOS] = Field(min_length=1, max_length=6)
    profiles: List[PROFILE] = Field(min_length=1, max_length=3)
    market_references: List[ReferencePair] = Field(min_length=1, max_length=8)
    trend_reference: ReferencePair
    market_modes: List[MODE] = Field(min_length=1, max_length=3)
    variants: Dict[str, Variant] = Field(min_length=1, max_length=8)
    sensitivities: Dict[str, ExecutionAssumptions] = Field(min_length=1, max_length=8)
    initial_cash: Decimal = Field(gt=0)
    initial_base: Decimal = Field(ge=0)
    initial_mark: Decimal = Field(gt=0)
    external_depth_base: Decimal = Field(gt=0)
    external_half_spread: Decimal = Field(gt=0, lt=1)
    quote_budget_per_market: Decimal = Field(gt=0)
    spreads: List[Decimal] = Field(min_length=1, max_length=4)
    refresh_seconds: int = Field(ge=1, le=3600, strict=True)
    max_spot_base: Decimal = Field(gt=0)
    max_perpetual_side_base: Decimal = Field(gt=0)
    max_perpetual_gross_base: Decimal = Field(gt=0)
    outflow_window_seconds: int = Field(ge=1, le=604800, strict=True)
    outflow_cap_quote: Decimal = Field(gt=0)
    fee_allowance_rate: Decimal = Field(ge=0, le=1)
    bootstrap_seconds: int = Field(ge=1, le=86400, strict=True)
    funding_interval_seconds: int = Field(ge=1, le=86400, strict=True)
    operating_cost_per_hour_quote: Decimal = Field(ge=0)
    max_quoted_spread_bps: Decimal = Field(gt=0, lt=10000)
    minimum_side_quote: Decimal = Field(gt=0)

    @model_validator(mode='after')
    def bounded_matrix(self):
        MMProfile(scenario='follow_market', references=self.market_references)
        MMProfile(scenario='follow_token', references=[self.trend_reference])
        if (sum(ref.weight for ref in self.market_references) != 1 or self.trend_reference.weight != 1
                or len({ref.pair for ref in self.market_references}) != len(self.market_references)):
            raise ValueError('References require unique pairs and weights totaling one')
        if any(ref.pair not in ('BTC-USDT', 'ETH-USDT', 'AVAX-USDT') for ref in [*self.market_references, self.trend_reference]):
            raise ValueError('Reference is not present in the declared synthetic tape')
        for values in (self.seeds, self.scenarios, self.profiles, self.market_modes):
            if len(set(values)) != len(values):
                raise ValueError('Duplicate experiment dimension')
        if any(type(seed) is not int or not 0 <= seed < 2**32 for seed in self.seeds):
            raise ValueError('Seeds must be unsigned 32-bit integers')
        if any(not 0 < spread < 1 for spread in self.spreads) or self.spreads != sorted(self.spreads):
            raise ValueError('Spreads must be ordered finite fractions')
        if self.bootstrap_seconds > min(self.duration_seconds, self.outflow_window_seconds):
            raise ValueError('Bootstrap exceeds experiment or outflow window')
        if self.initial_base > self.max_spot_base or self.quote_budget_per_market * 2 > self.initial_cash:
            raise ValueError('Initial wallet cannot support declared comparison budgets')
        if any(value.maker_fee_rate > self.fee_allowance_rate for value in self.sensitivities.values()):
            raise ValueError('Fee allowance does not cover assumed fees')
        count = self.case_count()
        if count > 2000 or count * self.duration_seconds > 2000000:
            raise ValueError('Experiment exceeds bounded replay budget')
        return self

    def case_count(self):
        return (len(self.seeds) * len(self.scenarios) * len(self.profiles) * len(self.market_modes)
                * len(self.variants) * len(self.sensitivities))


def load_experiment(path):
    return Experiment.model_validate(yaml.safe_load(path.read_text()))


def scenario_tape(name, seed, duration, initial_mark):
    """Quotes and fills cannot influence future marks, references or taker flow."""
    if name not in SCENARIOS.__args__:
        raise ValueError('Unknown scenario')
    rng = random.Random(seed)
    result = []
    for tick in range(duration + 1):
        progress = D(tick) / duration
        wave = D(((tick // 3) % 8) - 4) * D('.001') if tick else D('0')
        trend = (D('.30') * progress if name == 'sustained_buys' else
                 D('-.35') * progress if name == 'sustained_sells' else
                 D('-.25') * progress if name == 'reference_divergence' else D('0'))
        gap = (D('.25') if duration // 3 <= tick < 2 * duration // 3 else
               D('-.20') if tick >= 2 * duration // 3 else D('0')) if name == 'gap_slow_cancel' else D('0')
        factor = 1 + trend + gap + (wave if name != 'empty_no_flow' else 0)
        refs = {pair: str(start * (1 + (D('.4') * progress if name == 'reference_divergence'
                                        else trend * beta + wave / 2)))
                for pair, start, beta in (('BTC-USDT', D('100'), D('.6')),
                                          ('ETH-USDT', D('10'), D('.4')), ('AVAX-USDT', D('20'), D('.8')))}
        flows = {}
        for market in ('spot', 'perpetual'):
            side = ('BUY' if name == 'sustained_buys' else 'SELL' if name == 'sustained_sells'
                    else rng.choice(('BUY', 'SELL')))
            distance = D(rng.randint(15, 120)) / 10000
            mark = (initial_mark * factor).quantize(D('.00000001'))
            flows[market] = dict(side=side, amount=str(D(rng.randint(10, 60))),
                                 limit=str(mark * (1 + (distance if side == 'BUY' else -distance))))
        result.append(dict(at=tick, mark=str(mark), references=refs,
                           external_book=name != 'empty_no_flow', flows={} if name == 'empty_no_flow' else flows))
    return result


def tape_hash(tape):
    return hashlib.sha256(json.dumps(tape, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
