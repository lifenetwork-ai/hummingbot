"""Exact horizon observations over already-accounted SPOT fills."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, identifier, utc
from hummingbot.strategy_v2.life_liquidity.storage.financial import decimal


@dataclass(frozen=True)
class ObserveSpotMarkout:
    operation_id: str
    trade_id: str
    horizon_ms: int
    independent_price_usdt: Decimal
    value_at_ms: int
    reference_observed_at_ms: int
    observed_at: datetime
    source_kind: str = 'independent_market'

    def __post_init__(self):
        identifier(self.operation_id)
        identifier(self.trade_id)
        if len(self.operation_id) > 96 or self.source_kind != 'independent_market':
            raise StorageError('SPOT_MARKOUT_COMMAND_INVALID')
        object.__setattr__(self, 'independent_price_usdt', decimal(self.independent_price_usdt, positive=True))
        for value in (self.horizon_ms, self.value_at_ms, self.reference_observed_at_ms):
            if type(value) is not int or not 0 < value <= 253402300799999:
                raise StorageError('SPOT_MARKOUT_TIME_INVALID')
        if self.reference_observed_at_ms < self.value_at_ms:
            raise StorageError('SPOT_MARKOUT_TIME_INVALID')
        utc(self.observed_at)

    def json(self):
        return {key: str(value) if type(value) is Decimal else value.isoformat() if isinstance(value, datetime) else value
                for key, value in self.__dict__.items()}
