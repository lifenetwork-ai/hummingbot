"""Stable DTO identity shared by factories and the ``python -m`` entrypoint."""
from dataclasses import dataclass

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.headless_connectors import (
    HeadlessOkxExchange,
    HeadlessOkxPerpetualDerivative,
)
from hummingbot.strategy_v2.life_liquidity.storage.service import ServiceSources


@dataclass(frozen=True)
class HeadlessInputs:
    connectors: dict
    sources: ServiceSources
    market_data_provider: object

    def __post_init__(self):
        if (type(self.connectors) is not dict or not self.connectors or type(self.sources) is not ServiceSources
                or any(not isinstance(v, (HeadlessOkxExchange, HeadlessOkxPerpetualDerivative))
                       for v in self.connectors.values()) or self.market_data_provider is None):
            raise StorageError('HEADLESS_INPUTS_INVALID')
        object.__setattr__(self, 'connectors', dict(self.connectors))
