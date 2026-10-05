"""P2.8: benchmark data routing cannot replace an order-capable connector."""

from unittest.mock import Mock

import pytest

from controllers.generic.life_liquidity import LifeLiquidityConfig, LifeLiquidityController
from hummingbot.data_feed.market_data_provider import MarketDataProvider
from hummingbot.strategy_v2.life_liquidity.config import StrategyConfig
from hummingbot.strategy_v2.life_liquidity.market_data import BenchmarkConnectorRoute


def _strategy(source_connector="binance", source_pair="BTC-USDT"):
    return StrategyConfig.model_validate({
        "execution_mode": "simulation",
        "spot": {"enabled": True},
        "session": {"duration": "4h"},
        "reference": {
            "mode": "bounded_benchmark", "lookback": "15m",
            "sources": [{"connector": source_connector, "pair": source_pair,
                         "quote_currency": "USDT", "weight": "1"}],
        },
        "quotes": {"spreads_bps": ["30"], "sizes_base": ["10"]},
        "economics": {"objective": "profit_mm"},
    })


def _provider(trading_connectors, public_connector):
    provider = MarketDataProvider.__new__(MarketDataProvider)
    provider.connectors = trading_connectors
    provider.get_non_trading_connector = Mock(return_value=public_connector)
    return provider


def test_other_exchange_benchmark_uses_public_connector_without_mutating_trading_map():
    okx_trading, binance_public = object(), object()
    trading = {"okx": okx_trading}
    provider = _provider(trading, binance_public)
    route = BenchmarkConnectorRoute.from_strategy(_strategy())

    assert route.source_id == "binance:BTC-USDT"
    assert route.resolve(provider) is binance_public
    provider.get_non_trading_connector.assert_called_once_with("binance")
    assert trading == {"okx": okx_trading}
    assert route.trading_pair == "BTC-USDT"


def test_same_name_benchmark_reuses_order_capable_connector_without_replacing_it():
    okx_trading, public_connector = object(), object()
    trading = {"okx": okx_trading}
    provider = _provider(trading, public_connector)
    route = BenchmarkConnectorRoute.from_strategy(_strategy("okx", "ETH-USDT"))

    assert route.source_id == "okx:ETH-USDT"
    assert route.resolve(provider) is okx_trading
    provider.get_non_trading_connector.assert_not_called()
    assert trading["okx"] is okx_trading


def test_resolution_rejects_a_fallback_that_disagrees_with_registered_trading_connector():
    provider = _provider({"okx": object()}, object())
    provider.get_connector_with_fallback = Mock(return_value=object())
    route = BenchmarkConnectorRoute.from_strategy(_strategy("okx"))

    with pytest.raises(ValueError, match="BENCHMARK_CONNECTOR_COLLISION"):
        route.resolve(provider)


def test_missing_source_and_unavailable_connector_fail_closed():
    assert BenchmarkConnectorRoute.from_strategy(
        _strategy().model_copy(update={"reference": _strategy().reference.model_copy(
            update={"mode": "market", "sources": (), "influence": 0})})) is None
    provider = _provider({"okx": object()}, object())
    provider.get_non_trading_connector.side_effect = ValueError("unknown exchange")
    with pytest.raises(ValueError, match="BENCHMARK_CONNECTOR_UNAVAILABLE"):
        BenchmarkConnectorRoute.from_strategy(_strategy()).resolve(provider)


def test_controller_exposes_source_route_without_registering_benchmark_as_trading_market():
    strategy = _strategy()
    config = LifeLiquidityConfig.model_construct(id="life-benchmark", strategy=strategy)
    controller = LifeLiquidityController(config, Mock(), Mock())
    okx_trading, binance_public = object(), object()
    controller.market_data_provider = _provider({"okx": okx_trading}, binance_public)

    assert controller.benchmark_connector() is binance_public
    assert controller.benchmark_route.trading_pair == "BTC-USDT"
    assert controller.market_data_provider.connectors == {"okx": okx_trading}
    assert config.update_markets({}) == {}
