"""Actual OKX REST throttler proof for O.7.3, with fake HTTP only."""

import asyncio
from dataclasses import replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_hedge_coordinator import setup_coordinator
from test.hummingbot.strategy_v2.life_liquidity.test_protected_swap_send import connector_with_transport
from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital_runner import dispatch

import pytest

from controllers.generic.hedge_asset import HedgeAssetConfig
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.data_type.common import MarketDict

D = Decimal


@pytest.mark.asyncio
@pytest.mark.parametrize("revoke", [False, True])
async def test_cost_revoked_during_real_swap_rest_throttle_makes_no_http_request(tmp_path, monkeypatch, revoke):
    connector, throttle, requests = connector_with_transport()
    connector._perpetual_trading.set_leverage("LIFE-USDT", 1)
    connector._trading_rules["LIFE-USDT"] = TradingRule(
        "LIFE-USDT", min_order_size=D("0.25"), min_price_increment=D("0.0001"),
        min_base_amount_increment=D("0.025"), buy_order_collateral_token="USDT", sell_order_collateral_token="USDT")
    tasks = []

    def schedule(coro):
        task = asyncio.create_task(coro)
        tasks.append(task)
        return task

    monkeypatch.setattr("hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative.safe_ensure_future", schedule)
    h, values, market, settlements = setup_coordinator(tmp_path, route=connector)
    action = values[0].determine_executor_actions()[0]
    cfg = action.executor_config
    dispatch(values[0], values[9], cfg, action)
    try:
        await asyncio.wait_for(throttle.entered.wait(), 2)
        if revoke:
            market["value"] = replace(market["value"], sequence=2, fee_rate=D("0.1"))
        throttle.release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 2)
        assert len(requests) == (0 if revoke else 1)
        assert values[5].get(cfg.id).state == ("SEND_UNKNOWN" if revoke else "ACKED")
        assert values[1].claim_ids() == (cfg.id,)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def test_example_hedge_market_uses_explicit_life_usdt_metadata_subscription():
    config = HedgeAssetConfig(id="hedge-example", asset_to_hedge="LIFE", spot_connector_name="okx", spot_trading_pair="LIFE-USDT",
                              hedge_connector_name="okx_perpetual", hedge_trading_pair="LIFE-USDT")
    markets = config.update_markets(MarketDict())
    assert markets["okx"] == {"LIFE-USDT"} and markets["okx_perpetual"] == {"LIFE-USDT"}
    with pytest.raises(ValueError):
        HedgeAssetConfig(id="bad-example", asset_to_hedge="LIFE", spot_trading_pair="BTC-USDT")
