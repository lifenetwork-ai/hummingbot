"""An in-flight, failed, or overdue fee refresh cannot expose stale account rates."""

import asyncio
from test.hummingbot.strategy_v2.life_liquidity.test_fee_quote_binding import _costs, _fee
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hummingbot.strategy_v2.life_liquidity.fees import CachedFeeRateSource, FeeQuoteBinding
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction


@pytest.mark.asyncio
async def test_fee_refresh_invalidates_before_await_and_rejects_overlap_failure_and_rollback():
    now = {"value": 2000000}
    source = SimpleNamespace(fetch=AsyncMock(return_value=_fee()))
    cache = CachedFeeRateSource(source, utc_clock_ms=lambda: now["value"], refresh_interval_ms=500)
    binding = FeeQuoteBinding(snapshot=cache.snapshot, exchange_now_ms=lambda: now["value"],
                              account_id="12345", connector_name="okx", instrument_id="LIFE-USDT",
                              group_id="1", max_age_ms=1000, fee_policy="pause")
    assert not binding.evaluate(_costs()).allowed
    assert await cache.refresh_if_due()
    assert binding.evaluate(_costs()).allowed
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed():
        entered.set()
        await release.wait()
        return _fee(maker="-0.02", ts="2000500")

    source.fetch.side_effect = delayed
    now["value"] += 500
    task = asyncio.create_task(cache.refresh_if_due())
    await entered.wait()
    assert not binding.evaluate(_costs()).allowed
    assert not await cache.refresh_if_due()
    release.set()
    assert await task
    assert binding.evaluate(_costs()).reason_code == "FEE_COST_UNDERSTATED"
    now["value"] += 500
    source.fetch.side_effect = OSError("fee endpoint down")
    assert not await cache.refresh_if_due()
    assert cache.snapshot() is None
    now["value"] -= 1
    assert not await cache.refresh_if_due()
    assert cache.snapshot() is None


@pytest.mark.asyncio
async def test_authenticated_fee_refresh_lifecycle_revokes_real_executor_final_send(tmp_path):
    from dataclasses import replace
    from test.hummingbot.strategy_v2.life_liquidity.test_fees import SPOT_INSTRUMENT, fee_response
    from test.hummingbot.strategy_v2.life_liquidity.test_o2_revocation_replay import setup

    from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
    from hummingbot.strategy_v2.life_liquidity.fees import FEE_RATES_PATH, OkxFeeRateSource

    c, connector, wal, ledger, _, _, _, _, old, watchdog = await setup(tmp_path)
    try:
        planner = c._quote_action_planner
        assert planner.on_runner_action_rejected(CreateExecutorAction(controller_id="life", executor_config=old.config))
        planner.intent_id_factory = lambda: "account-fee-quote"
        c.config = c.config.model_copy(update={"recovery_account_uid": "12345", "strategy":
                                               c.config.strategy.model_copy(update={"economics":
                                                                                    c.config.strategy.economics.model_copy(update={"fee_policy": "pause", "fee_max_age": "1s"})})})
        c.market_data_provider.connectors = {"okx": connector}
        now = {"ms": 2000000}
        connector._api_get = AsyncMock(return_value=fee_response())
        source = OkxFeeRateSource(c.market_data_provider, connector_name="okx", account_id="12345",
                                  instrument=SPOT_INSTRUMENT, notional_currency="USDT")
        cache = CachedFeeRateSource(source, utc_clock_ms=lambda: now["ms"], refresh_interval_ms=500)
        planner.fee_binding = FeeQuoteBinding(
            snapshot=cache.snapshot, exchange_now_ms=lambda: now["ms"], account_id="12345",
            connector_name="okx", instrument_id="LIFE-USDT", group_id="1",
            max_age_ms=1000, fee_policy="pause")
        snapshot = planner.snapshot()
        planner.snapshot = lambda: replace(snapshot, costs=_costs())
        c.install_fee_rate_source(cache)
        assert c.determine_executor_actions() == []
        assert await cache.refresh_if_due()
        connector._api_get.assert_awaited_once_with(
            path_url=FEE_RATES_PATH, params={"instType": "SPOT", "instId": "LIFE-USDT"},
            is_auth_required=True)
        actions = c.determine_executor_actions()
        assert len(actions) == 1
        executor = OrderExecutor(old._strategy, actions[0].executor_config)
        executor.get_order_price = lambda: executor.config.price
        executor.place_open_order()
        wire = {"clOrdId": executor._order.order_id, "instId": "LIFE-USDT", "side": executor.config.side.name.lower(),
                "ordType": "post_only", "tdMode": "cash", "px": str(executor.config.price),
                "sz": str(executor.config.amount)}
        connector.sent[0]["pre_send_check"](wire)
        now["ms"] += 500
        connector._api_get.side_effect = OSError("authenticated endpoint unavailable")
        c.on_safety_tick(1)
        await c._fee_refresh_task
        with pytest.raises(PermissionError):
            connector.sent[0]["pre_send_check"](wire)
        assert ledger.has_open_intent(executor.config.id)
        assert cache.snapshot() is None
        if c.order_safety_task is not None:
            await c.order_safety_task
    finally:
        watchdog.cancel()
