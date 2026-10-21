"""Business identity for preallocation, before a time-sensitive V2 capture.

A wire identity is not a reservation or trading capability. The owning source
captures it at most once after capital/carry preflight. It never re-dates an
existing executor action, claim, observation or preparation.
"""
from dataclasses import dataclass
from decimal import Decimal

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken, StorageError, fingerprint, identifier
from hummingbot.strategy_v2.life_liquidity.storage.financial import decimal


@dataclass(frozen=True)
class PgSpotQuoteIntent:
    intent_id: str
    controller_id: str
    owner: OwnerToken
    config_version: int
    side: TradeType
    level: int
    quantity_base: Decimal
    price_usdt: Decimal

    def __post_init__(self):
        identifier(self.intent_id)
        identifier(self.controller_id)
        if (type(self.owner) is not OwnerToken or self.side not in (TradeType.BUY, TradeType.SELL)
                or type(self.side) is not TradeType or type(self.config_version) is not int
                or not 0 < self.config_version <= 9223372036854775807
                or type(self.level) is not int or not 0 <= self.level <= 9999999999):
            raise StorageError('PG_SPOT_INTENT_INVALID')
        for key in ('quantity_base', 'price_usdt'):
            object.__setattr__(self, key, decimal(getattr(self, key), positive=True))

    @property
    def wire_id(self):
        return 'L' + fingerprint(dict(kind='native-spot-intent-v1', intent_id=self.intent_id,
                                      controller_id=self.controller_id, owner=self.owner.json(),
                                      config_version=self.config_version, side=self.side.name, level=self.level,
                                      quantity_base=str(self.quantity_base), price_usdt=str(self.price_usdt)))[:31]

    def capture_template(self):
        """Unissued native input; zero timestamp cannot authorize V2 dispatch."""
        from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
        from hummingbot.strategy_v2.life_liquidity.storage.actions import SpotQuoteAction
        cfg = OrderExecutorConfig(id=self.intent_id, controller_id=self.controller_id, timestamp=0.0,
                                  connector_name='okx', trading_pair='LIFE-USDT', side=self.side,
                                  amount=self.quantity_base, price=self.price_usdt, level_id=str(self.level),
                                  execution_strategy=ExecutionStrategy.LIMIT_MAKER)
        return SpotQuoteAction(self.intent_id, self.controller_id, str(self.owner.session_id), self.owner.epoch,
                               self.config_version, 'LIFE-USDT', self.side.name, self.level, cfg.model_dump_json())
