"""Optional LIFE spread composition using existing maker fees and NATR only."""
import math
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hummingbot.core.data_type.common import OrderType, PositionAction, TradeType

ONE = Decimal('1')


class VolatilityDefenseConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    reduce_at_natr: Decimal = Field(gt=0, lt=1, allow_inf_nan=False)
    stop_at_natr: Decimal = Field(gt=0, lt=1, allow_inf_nan=False)
    resume_below_natr: Decimal = Field(gt=0, lt=1, allow_inf_nan=False)
    reduced_size_factor: Decimal = Field(gt=0, lt=1, allow_inf_nan=False)
    recovery_seconds: int = Field(gt=0, le=3600, strict=True)

    @model_validator(mode='after')
    def ordered(self):
        if not self.resume_below_natr < self.reduce_at_natr < self.stop_at_natr:
            raise ValueError('Volatility defense thresholds must be strictly ordered')
        return self


class VolatilityDefense:
    """Bounded sampled decision; callers supply monotonic recovery time."""

    def __init__(self, config):
        self.config = config
        self.state = 'normal'
        self.good_since = self.last_sample = None
        self.good_samples = 0
        self.last_observation = None

    def reset_recovery(self):
        self.good_since = self.last_sample = None
        self.good_samples = 0

    def sample(self, raw, token, monotonic_now):
        if raw is None:
            self.reset_recovery()
            return
        if raw >= self.config.stop_at_natr:
            self.state = 'paused'
            self.reset_recovery()
        elif self.state != 'paused' and raw >= self.config.reduce_at_natr:
            self.state = 'reduced'
            self.reset_recovery()
        elif self.state in ('reduced', 'paused'):
            threshold = self.config.reduce_at_natr if self.state == 'paused' else self.config.resume_below_natr
            if raw >= threshold:
                self.reset_recovery()
            elif self.last_observation is None or token > self.last_observation:
                self.good_since = monotonic_now if self.good_since is None else self.good_since
                self.last_sample = token
                self.good_samples += 1
                if self.good_samples >= 2 and monotonic_now - self.good_since >= self.config.recovery_seconds:
                    self.state = 'reduced' if self.state == 'paused' else 'normal'
                    self.reset_recovery()
        self.last_observation = max(token, self.last_observation) if self.last_observation is not None else token


class VolatilitySpreadConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    enabled: bool = False
    interval: Literal['1s', '1m', '3m', '5m', '15m', '30m', '1h'] = '1m'
    length: int = Field(default=14, ge=2, le=100, strict=True)
    target_natr: Decimal = Field(default=Decimal('.01'), gt=0, lt=1, allow_inf_nan=False)
    min_multiplier: Decimal = Field(default=Decimal('.5'), gt=0, allow_inf_nan=False)
    max_multiplier: Decimal = Field(default=Decimal('3'), gt=0, le=20, allow_inf_nan=False)
    max_age_seconds: int = Field(default=120, ge=1, le=86400, strict=True)
    on_unavailable: Literal['static', 'wait'] = 'wait'
    fallback_buy_spreads: Optional[List[Decimal]] = None
    fallback_sell_spreads: Optional[List[Decimal]] = None
    defense: Optional[VolatilityDefenseConfig] = None

    @model_validator(mode='after')
    def check_limits(self):
        if self.defense is not None and (not self.enabled or self.min_multiplier < 1):
            raise ValueError('Volatility defense requires enabled volatility and min_multiplier >= 1')
        if self.min_multiplier > self.max_multiplier:
            raise ValueError('Volatility multiplier limits are reversed')
        if self.enabled and self.on_unavailable == 'static' and (
                not self.fallback_buy_spreads or not self.fallback_sell_spreads):
            raise ValueError('Static fallback requires explicit buy and sell spreads')
        for values in (self.fallback_buy_spreads, self.fallback_sell_spreads):
            if values is not None and (not values or any(not x.is_finite() or not 0 < x < 1 for x in values)
                                       or values != sorted(values)):
                raise ValueError('Fallback spreads must be finite, ordered fractions')
        return self


class SpreadPolicyConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    fee_floor: bool = False
    volatility: VolatilitySpreadConfig = Field(default_factory=VolatilitySpreadConfig)

    @model_validator(mode='after')
    def defense_fee_floor(self):
        if self.volatility.defense is not None and not self.fee_floor:
            raise ValueError('Volatility defense requires maker fee floor')
        return self

    @property
    def enabled(self):
        return self.fee_floor or self.volatility.enabled


def natr_value(frame, length):
    # Same indicator as upstream PMM dynamic, with no MACD or anchor shift.
    import pandas_ta as ta
    result = ta.natr(frame['high'], frame['low'], frame['close'], length=length, talib=False)
    value = Decimal(str(result.iloc[-1])) / 100 if result is not None else Decimal('NaN')
    if not value.is_finite() or value < 0:
        raise ValueError('candles_unavailable')
    return value


class SpreadPolicy:
    def __init__(self, config):
        self.config = config
        self.key = None
        self.value = None
        self.raw_natr = None
        self.valid_until = None
        self.completed_at = None
        self.state = 'disabled' if not config.enabled else 'static'
        self.effective = None
        self.defense = VolatilityDefense(config.volatility.defense) if config.volatility.defense is not None else None

    def refresh(self, feed, now):
        """Only owner/control samples inspect bounded candles; send gates use cache."""
        cfg = self.config.volatility
        if not cfg.enabled:
            return
        try:
            rows = feed._candles
            interval = feed.interval_in_seconds
            # Feed itself keeps ordered rows. Validate the selected bounded window
            # before NATR; the forming candle is never used, even at boundaries.
            candidate = rows[-1] if rows[-1][0] + interval <= now else rows[-2]
            completed = float(candidate[0])
            if not math.isfinite(completed) or not 0 <= now - completed - interval <= cfg.max_age_seconds:
                raise ValueError('candles_unavailable')
            key = (id(feed), completed)
            if self.key == key:
                return
            import pandas as pd
            selected = [row[:5] for row in rows if row[0] <= completed][-cfg.length - 100:]
            # A feed gap resets the indicator's history, not its controller.
            # Older disconnected rows must not postpone recovery for 100 intervals.
            for index in range(len(selected) - 1, 0, -1):
                if selected[index][0] - selected[index - 1][0] != interval:
                    selected = selected[index:]
                    break
            if len(selected) < cfg.length + 1:
                raise ValueError('candles_unavailable')
            for index, row in enumerate(selected):
                stamp, opening, high, low, close = map(float, row)
                if (not all(math.isfinite(x) for x in (stamp, opening, high, low, close))
                        or min(opening, low, close) <= 0 or not low <= opening <= high or not low <= close <= high
                        or index and stamp - selected[index - 1][0] != interval):
                    raise ValueError('candles_unavailable')
            frame = pd.DataFrame(selected, columns=['timestamp', 'open', 'high', 'low', 'close'])
            raw = natr_value(frame, cfg.length)
            value = raw / cfg.target_natr
            if not value.is_finite() or value < 0:
                raise ValueError('candles_unavailable')
            self.value = max(cfg.min_multiplier, min(cfg.max_multiplier, value))
            self.raw_natr = raw
            self.key, self.completed_at = key, completed + interval
            self.valid_until = self.completed_at + cfg.max_age_seconds
        except Exception:
            # Optional outage follows the declared policy and recovers next sample.
            self.key, self.value, self.completed_at, self.valid_until = None, None, None, None
            self.raw_natr = None

    def sample_defense(self, now, monotonic_now):
        if self.defense is not None:
            self.defense.sample(self.raw_natr if self.multiplier(now) is not None else None,
                                self.completed_at, monotonic_now)

    def size_factor(self, now):
        if self.defense is None:
            return ONE
        if self.defense.state == 'paused':
            return Decimal('0')
        return (self.defense.config.reduced_size_factor
                if self.defense.state != 'normal' or self.multiplier(now) is None else ONE)

    def multiplier(self, now):
        if not self.config.volatility.enabled:
            return ONE
        if self.value is None or not self.completed_at <= now <= self.valid_until:
            return None
        return self.value

    def _fee(self, connector, pair, side, price, amount):
        base, quote = pair.split('-')
        try:
            extra = dict(position_action=PositionAction.OPEN) if connector.name == "okx_perpetual" else {}
            fee = connector.get_fee(base_currency=base, quote_currency=quote, order_type=OrderType.LIMIT_MAKER,
                                    order_side=side, amount=amount, price=price, is_maker=True, **extra)
            if (not fee.percent.is_finite() or fee.percent_token not in (None, base, quote)
                    or any(item.token not in (base, quote) or not item.amount.is_finite() for item in fee.flat_fees)):
                raise ValueError('fee_basis_unavailable')
            value = fee.fee_amount_in_token(pair, price, amount, quote)
            if not value.is_finite():
                raise ValueError('fee_basis_unavailable')
            # Do not spend a hypothetical rebate in advance or narrow base spreads.
            return max(Decimal('0'), value)
        except Exception as error:
            raise ValueError('fee_basis_unavailable') from error

    def quote_valid(self, connector, pair, side, price, amount, reference):
        if any(not value.is_finite() or value <= 0 for value in (price, amount, reference)):
            return False
        if not self.config.fee_floor:
            return True
        fee = self._fee(connector, pair, side, price, amount)
        edge = amount * (reference - price if side == TradeType.BUY else price - reference)
        return edge >= fee

    def compose(self, connector, pair, reference, buy, sell, buy_quote, sell_quote, *, now):
        if self.defense is not None and self.defense.state == 'paused':
            self.state = 'waiting'
            raise ValueError('volatility_paused')
        factor = self.multiplier(now)
        cfg = self.config.volatility
        if factor is None:
            if cfg.on_unavailable == 'wait':
                self.state = 'waiting'
                raise ValueError('candles_unavailable')
            buy, sell = cfg.fallback_buy_spreads, cfg.fallback_sell_spreads
            factor, self.state = ONE, 'static_fallback'
        else:
            self.state = 'dynamic' if cfg.enabled else 'static'
        result = []
        for side, spreads, allocations in ((TradeType.BUY, buy, buy_quote), (TradeType.SELL, sell, sell_quote)):
            values, previous = [], Decimal('0')
            for spread, allocation in zip(spreads, allocations):
                spread = max(previous, Decimal(str(spread)) * factor)
                if self.config.fee_floor:
                    for _ in range(8):
                        if not 0 < spread < 1:
                            raise ValueError('quote_out_of_bounds')
                        price = reference * (1 + (-1 if side == TradeType.BUY else 1) * spread)
                        amount = Decimal(allocation) / price
                        fee = self._fee(connector, pair, side, price, amount)
                        edge = amount * (reference - price if side == TradeType.BUY else price - reference)
                        if edge >= fee:
                            break
                        rate = fee / Decimal(allocation)
                        if rate >= 1:
                            raise ValueError('fee_basis_unavailable')
                        spread = max(spread.next_plus(), rate / (1 - rate) + Decimal('1e-24'))
                    else:
                        raise ValueError('fee_basis_unavailable')
                if not 0 < spread < 1:
                    raise ValueError('quote_out_of_bounds')
                values.append(spread)
                previous = spread
            result.append(values)
        self.effective = tuple(result)
        return self.effective

    def status(self, now):
        state = self.state
        if self.config.volatility.enabled and self.multiplier(now) is None:
            state = 'waiting' if self.config.volatility.on_unavailable == 'wait' else 'static_fallback'
        return dict(enabled=self.config.enabled, fee_floor=self.config.fee_floor, state=state,
                    raw_natr=str(self.raw_natr) if self.multiplier(now) is not None and self.raw_natr is not None else None,
                    multiplier=str(self.multiplier(now)) if self.multiplier(now) is not None else None, completed_at=self.completed_at, valid_until=self.valid_until,
                    buy_spreads=[str(x) for x in self.effective[0]] if self.effective else [], sell_spreads=[str(x) for x in self.effective[1]] if self.effective else [])
