import asyncio
import json
from decimal import Decimal
from test.isolated_asyncio_wrapper_test_case import IsolatedAsyncioWrapperTestCase
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

from aioresponses import aioresponses

from hummingbot.client.config.client_config_map import ClientConfigMap
from hummingbot.client.config.config_helpers import ClientConfigAdapter
from hummingbot.client.config.config_var import ConfigVar
from hummingbot.client.config.security import Security
from hummingbot.client.settings import ConnectorSetting, ConnectorType
from hummingbot.connector.exchange.binance import binance_constants as CONSTANTS, binance_web_utils
from hummingbot.core.data_type.trade_fee import TradeFeeSchema
from hummingbot.core.utils.trading_pair_fetcher import TradingPairFetcher


class TestTradingPairFetcher(IsolatedAsyncioWrapperTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        Security.decrypt_all()

    def setUp(self) -> None:
        super().setUp()
        self.enterContext(patch.object(TradingPairFetcher, "_sf_shared_instance", None))

    class MockConnectorSetting(MagicMock):
        def __init__(self, name, parent_name=None, connector=None, *args, **kwargs) -> None:
            super().__init__(name, *args, **kwargs)
            self._name = name
            self._parent_name = parent_name
            self._connector = connector

        @property
        def name(self) -> str:
            return self._name

        @property
        def parent_name(self) -> str:
            return self._parent_name

        def base_name(self) -> str:
            return self.name

        def connector_connected(self) -> bool:
            return True

        def add_domain_parameter(*_, **__) -> Dict[str, Any]:
            return {}

        def uses_gateway_generic_connector(self) -> bool:
            return False

        def non_trading_connector_instance_with_default_configuration(self, trading_pairs = None):
            return self._connector

    @patch.object(TradingPairFetcher, "_all_connector_settings", return_value={})
    async def test_trading_pair_fetcher_returns_same_instance_when_get_new_instance_once_initialized(self, _):
        instance = TradingPairFetcher.get_instance(ClientConfigAdapter(ClientConfigMap()))
        self.assertIs(instance, TradingPairFetcher.get_instance())
        await asyncio.wait_for(instance._fetch_task, timeout=1)

    @patch("hummingbot.core.utils.trading_pair_fetcher.TradingPairFetcher._all_connector_settings")
    async def test_fetched_connector_trading_pairs(self, mock_connector_settings):
        connector = AsyncMock()
        connector.all_trading_pairs.return_value = ["MOCK-HBOT"]
        mock_connector_settings.return_value = {
            "mock_exchange_1": self.MockConnectorSetting(name="mockConnector", connector=connector),
            "mock_paper_trade": self.MockConnectorSetting(name="mock_paper_trade", parent_name="mock_exchange_1")
        }

        client_config_map = ClientConfigAdapter(ClientConfigMap())
        client_config_map.fetch_pairs_from_all_exchanges = True
        trading_pair_fetcher = TradingPairFetcher(client_config_map)
        await asyncio.wait_for(trading_pair_fetcher._fetch_task, timeout=1)
        trading_pairs = trading_pair_fetcher.trading_pairs
        self.assertEqual(2, len(trading_pairs))
        self.assertEqual({"mockConnector": ["MOCK-HBOT"], "mock_paper_trade": ["MOCK-HBOT"]}, trading_pairs)

    @patch("hummingbot.core.utils.trading_pair_fetcher.TradingPairFetcher._all_connector_settings")
    @patch("hummingbot.client.config.security.Security.connector_config_file_exists")
    @patch("hummingbot.client.config.security.Security.wait_til_decryption_done")
    async def test_fetched_connected_trading_pairs(self, _, __: MagicMock, mock_connector_settings):
        connector = AsyncMock()
        connector.all_trading_pairs.return_value = ["MOCK-HBOT"]
        mock_connector_settings.return_value = {
            "mock_exchange_1": self.MockConnectorSetting(name="binance", connector=connector),
            "mock_paper_trade": self.MockConnectorSetting(name="mock_paper_trade", parent_name="mock_exchange_1")
        }

        client_config_map = ClientConfigAdapter(ClientConfigMap())
        client_config_map.fetch_pairs_from_all_exchanges = False
        self.assertTrue(Security.connector_config_file_exists("binance"))
        trading_pair_fetcher = TradingPairFetcher(client_config_map)
        await asyncio.wait_for(trading_pair_fetcher._fetch_task, timeout=1)
        trading_pairs = trading_pair_fetcher.trading_pairs
        self.assertEqual(2, len(trading_pairs))
        self.assertEqual({"binance": ["MOCK-HBOT"], "mock_paper_trade": ["MOCK-HBOT"]}, trading_pairs)

    @aioresponses()
    @patch("hummingbot.core.utils.trading_pair_fetcher.TradingPairFetcher._all_connector_settings")
    async def test_fetch_all(self, mock_api, all_connector_settings_mock):
        client_config_map = ClientConfigAdapter(ClientConfigMap())
        client_config_map.fetch_pairs_from_all_exchanges = True
        all_connector_settings_mock.return_value = {
            "binance": ConnectorSetting(
                name='binance',
                type=ConnectorType.Exchange,
                example_pair='ZRX-ETH',
                centralised=True,
                use_ethereum_wallet=False,
                trade_fee_schema=TradeFeeSchema(
                    percent_fee_token=None,
                    maker_percent_fee_decimal=Decimal('0.001'),
                    taker_percent_fee_decimal=Decimal('0.001'),
                    buy_percent_fee_deducted_from_returns=False,
                    maker_fixed_fees=[],
                    taker_fixed_fees=[]),
                config_keys={
                    'binance_api_key': ConfigVar(key='binance_api_key', prompt=""),
                    'binance_api_secret': ConfigVar(key='binance_api_secret', prompt="")},
                is_sub_domain=False,
                parent_name=None,
                domain_parameter=None,
                use_eth_gas_lookup=False),
        }

        url = binance_web_utils.public_rest_url(path_url=CONSTANTS.EXCHANGE_INFO_PATH_URL)
        mock_response: Dict[str, Any] = {
            "timezone": "UTC",
            "serverTime": 1639598493658,
            "rateLimits": [],
            "exchangeFilters": [],
            "symbols": [
                {
                    "symbol": "ETHBTC",
                    "status": "TRADING",
                    "baseAsset": "ETH",
                    "baseAssetPrecision": 8,
                    "quoteAsset": "BTC",
                    "quotePrecision": 8,
                    "quoteAssetPrecision": 8,
                    "baseCommissionPrecision": 8,
                    "quoteCommissionPrecision": 8,
                    "orderTypes": [
                        "LIMIT",
                        "LIMIT_MAKER",
                        "MARKET",
                        "STOP_LOSS_LIMIT",
                        "TAKE_PROFIT_LIMIT"
                    ],
                    "icebergAllowed": True,
                    "ocoAllowed": True,
                    "quoteOrderQtyMarketAllowed": True,
                    "isSpotTradingAllowed": True,
                    "isMarginTradingAllowed": True,
                    "filters": [],
                    "permissionSets": [[
                        "SPOT",
                        "MARGIN"
                    ]]
                },
                {
                    "symbol": "LTCBTC",
                    "status": "TRADING",
                    "baseAsset": "LTC",
                    "baseAssetPrecision": 8,
                    "quoteAsset": "BTC",
                    "quotePrecision": 8,
                    "quoteAssetPrecision": 8,
                    "baseCommissionPrecision": 8,
                    "quoteCommissionPrecision": 8,
                    "orderTypes": [
                        "LIMIT",
                        "LIMIT_MAKER",
                        "MARKET",
                        "STOP_LOSS_LIMIT",
                        "TAKE_PROFIT_LIMIT"
                    ],
                    "icebergAllowed": True,
                    "ocoAllowed": True,
                    "quoteOrderQtyMarketAllowed": True,
                    "isSpotTradingAllowed": True,
                    "isMarginTradingAllowed": True,
                    "filters": [],
                    "permissionSets": [[
                        "SPOT",
                        "MARGIN"
                    ]]
                },
                {
                    "symbol": "BNBBTC",
                    "status": "TRADING",
                    "baseAsset": "BNB",
                    "baseAssetPrecision": 8,
                    "quoteAsset": "BTC",
                    "quotePrecision": 8,
                    "quoteAssetPrecision": 8,
                    "baseCommissionPrecision": 8,
                    "quoteCommissionPrecision": 8,
                    "orderTypes": [
                        "LIMIT",
                        "LIMIT_MAKER",
                        "MARKET",
                        "STOP_LOSS_LIMIT",
                        "TAKE_PROFIT_LIMIT"
                    ],
                    "icebergAllowed": True,
                    "ocoAllowed": True,
                    "quoteOrderQtyMarketAllowed": True,
                    "isSpotTradingAllowed": True,
                    "isMarginTradingAllowed": True,
                    "filters": [],
                    "permissionSets": [[
                        "MARGIN"
                    ]]
                },
            ]
        }
        mock_api.get(url, body=json.dumps(mock_response))

        fetcher = TradingPairFetcher(client_config_map)
        await asyncio.wait_for(fetcher._fetch_task, timeout=1)
        trading_pairs = fetcher.trading_pairs

        self.assertEqual(1, len(trading_pairs.keys()))
        self.assertIn("binance", trading_pairs)
        binance_pairs = trading_pairs["binance"]
        self.assertEqual(2, len(binance_pairs))
        self.assertIn("ETH-BTC", binance_pairs)
        self.assertIn("LTC-BTC", binance_pairs)
        self.assertNotIn("BNB-BTC", binance_pairs)
