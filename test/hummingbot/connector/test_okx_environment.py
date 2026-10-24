from types import SimpleNamespace

import pytest

from hummingbot.connector.okx_environment import OKXEnvironment, OKXEnvironmentHeaders
from hummingbot.core.web_assistant.connections.data_types import RESTMethod, RESTRequest


@pytest.mark.parametrize("values", [{"OKX_TRADING_ENV": "paper"}, {"OKX_SITE": "unknown"}])
def test_invalid_environment_never_selects_a_default_live_route(values):
    with pytest.raises(ValueError):
        OKXEnvironment.load(environ=values)


def test_conflicting_site_and_legacy_demo_route_are_rejected():
    with pytest.raises(ValueError, match="conflict"):
        OKXEnvironment.load(site="app", environ={"OKX_SITE": "www"})
    with pytest.raises(ValueError, match="conflict"):
        OKXEnvironment.load(legacy_demo=True, environ={"OKX_TRADING_ENV": "live"})


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["live", "demo"])
async def test_environment_owns_simulated_header_and_cannot_be_overridden(mode):
    request = RESTRequest(method=RESTMethod.GET, url="https://www.okx.com/api/v5/account/config",
                          headers={"X-Simulated-Trading": "1" if mode == "live" else "0"})
    result = await OKXEnvironmentHeaders(OKXEnvironment(mode, "www")).pre_process(request)
    assert result.headers == ({"x-simulated-trading": "1"} if mode == "demo" else {})


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["spot", "perpetual"])
async def test_candles_keep_demo_headers_after_attaching_existing_connector(monkeypatch, kind):
    monkeypatch.setenv("OKX_TRADING_ENV", "demo")
    monkeypatch.setenv("OKX_SITE", "my")
    from hummingbot.core.api_throttler.async_throttler import AsyncThrottler
    from hummingbot.data_feed.candles_feed.okx_perpetual_candles.okx_perpetual_candles import OKXPerpetualCandles
    from hummingbot.data_feed.candles_feed.okx_spot_candles.okx_spot_candles import OKXSpotCandles
    candle = (OKXSpotCandles if kind == "spot" else OKXPerpetualCandles)("BTC-USDT")
    candle.attach_connector(SimpleNamespace(throttler=AsyncThrottler([])))
    request = RESTRequest(method=RESTMethod.GET, url=candle.candles_url)
    for processor in candle._api_factory._rest_pre_processors:
        request = await processor.pre_process(request)
    assert request.headers["x-simulated-trading"] == "1"
    assert candle.wss_url == "wss://wseeapap.okx.com/ws/v5/business"
    assert candle.rest_url == "https://eea.okx.com"
