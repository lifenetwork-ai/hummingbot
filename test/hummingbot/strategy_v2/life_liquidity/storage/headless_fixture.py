"""Explicit offline-only process fixture; never a production source factory."""
from decimal import Decimal

from hummingbot.core.network_iterator import NetworkStatus
from hummingbot.data_feed.market_data_provider import MarketDataProvider
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.headless import HeadlessInputs
from hummingbot.strategy_v2.life_liquidity.storage.headless_connectors import HeadlessOkxExchange
from hummingbot.strategy_v2.life_liquidity.storage.service import ServiceSources
from hummingbot.strategy_v2.life_liquidity.storage.spot_application import SpotAdmission


def build(config):
    if config.controller.storage_binding.environment != 'OFFLINE_SIMULATION':
        raise StorageError('SYNTHETIC_FACTORY_OFFLINE_ONLY')
    connector = HeadlessOkxExchange('', '', '', trading_pairs=['LIFE-USDT'], trading_required=False)

    async def uid():
        return config.controller.storage_binding.account_uid

    async def disconnected():
        return NetworkStatus.NOT_CONNECTED

    async def instruments(*args, **kwargs):
        return {'code': '0', 'data': []}

    connector.get_account_uid = uid
    connector.check_network = disconnected
    connector._api_get = instruments
    sources = ServiceSources(clock=lambda: config.owner.valid_from, risk_epoch=lambda: 1,
                             authorize_permit=lambda _: False,
                             spot_admission=lambda _: SpotAdmission(Decimal('1'), Decimal('0'), Decimal('0'), Decimal('0')))
    connectors = {'okx': connector}
    return HeadlessInputs(connectors, sources, MarketDataProvider(connectors))
