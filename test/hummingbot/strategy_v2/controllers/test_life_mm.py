import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from controllers.market_making.life_mm import LifeMMConfig, LifeMMController
from hummingbot.core.data_type.common import OrderType, PositionMode, PositionSide, TradeType
from hummingbot.core.data_type.order_book import OrderBook


def configuration(**updates):
    values = dict(id="life-spot", min_price="0.01", max_price="1", initial_anchor="0.1",
                  max_base_inventory="10000", max_long_base="10000", max_short_base="10000",
                  max_gross_base="15000", buy_spreads=[0.01], sell_spreads=[0.01])
    values.update(updates)
    return LifeMMConfig(**values)


def make_controller(config):
    provider = MagicMock()
    provider.time.return_value = 1000
    prices = {"LIFE-USDT": Decimal("0.1"), "BTC-USDT": Decimal("100"), "ETH-USDT": Decimal("10")}
    provider.get_price_by_type.side_effect = lambda connector, pair, *args: prices[pair]
    provider.get_order_book.side_effect = lambda connector, pair: SimpleNamespace(snapshot_uid=1000, last_diff_uid=1000)
    provider.get_connector_with_fallback.return_value.order_books = {
        pair: SimpleNamespace(snapshot_uid=1000, last_diff_uid=1000) for pair in prices
    }
    provider.get_connector_with_fallback.return_value.trading_rules = dict.fromkeys(prices, object())
    connector = provider.get_connector_with_fallback.return_value
    connector._set_position_mode_lock = asyncio.Lock()
    connector._fetch_account_position_mode = AsyncMock(return_value=PositionMode.ONEWAY)
    connector.account_positions = {}
    connector.in_flight_orders = {}
    balances = {"LIFE": Decimal("1000"), "USDT": Decimal("1000")}
    connector.get_balance.side_effect = lambda asset: balances[asset]
    connector.get_available_balance.side_effect = lambda asset: balances[asset]
    return LifeMMController(config, provider, asyncio.Queue()), prices


@pytest.mark.asyncio
async def test_weighted_returns_use_saved_life_and_source_anchors():
    config = configuration(scenario="follow_market", references=[
        {"pair": "BTC-USDT", "weight": "0.6"}, {"pair": "ETH-USDT", "weight": "0.4"}])
    controller, prices = make_controller(config)
    await controller.update_processed_data()
    prices.update({"BTC-USDT": Decimal("110"), "ETH-USDT": Decimal("9"), "LIFE-USDT": Decimal("0.5")})
    await controller.update_processed_data()
    assert controller.processed_data["reference_price"] == Decimal("0.102")


@pytest.mark.asyncio
async def test_sideways_keeps_its_anchor():
    controller, prices = make_controller(configuration())
    await controller.update_processed_data()
    prices["LIFE-USDT"] = Decimal("0.5")
    await controller.update_processed_data()
    assert controller.processed_data["reference_price"] == Decimal("0.1")


@pytest.mark.parametrize("updates", [
    {"scenario": "follow_market"},
    {"scenario": "sideways", "references": [{"pair": "BTC-USDT", "weight": "1"}]},
    {"scenario": "follow_token", "references": [{"pair": "BTC-USDT", "weight": "0.5"}, {"pair": "ETH-USDT", "weight": "0.5"}]},
    {"scenario": "follow_market", "references": [{"pair": "BTC-USDT", "weight": "0.9"}]},
    {"scenario": "follow_market", "references": [{"pair": "BTC-USDT", "weight": "0.5"}, {"pair": "BTC-USDT", "weight": "0.5"}]},
    {"scenario": "follow_token", "references": [{"pair": "BTC-USDC", "weight": "1"}]},
    {"scenario": "follow_token", "references": [{"pair": "BTC-USDT", "weight": "NaN"}]},
    {"scenario": "follow_token", "references": [{"pair": "BTC-USDT", "weight": "-1"}]},
    {"min_price": "2", "max_price": "1"},
    {"position_mode": PositionMode.ONEWAY},
    {"skip_rebalance": False},
    {"time_limit": 60},
    {"stop_loss": "0.1"},
    {"leverage": 2},
])
def test_invalid_or_unsafe_configuration_is_rejected(updates):
    with pytest.raises(ValidationError):
        configuration(**updates)


def test_entries_are_explicitly_post_only_and_no_automatic_market_exits():
    config = configuration()
    assert config.triple_barrier_config.open_order_type == OrderType.LIMIT_MAKER
    assert config.triple_barrier_config.stop_loss is None
    assert config.triple_barrier_config.time_limit is None
    assert config.skip_rebalance


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["follow_market", "follow_token"])
async def test_single_pair_follow_modes_are_equivalent(scenario):
    controller, prices = make_controller(configuration(
        scenario=scenario, references=[{"pair": "ETH-USDT", "weight": "1"}]))
    await controller.update_processed_data()
    prices["ETH-USDT"] = Decimal("12")
    await controller.update_processed_data()
    assert controller.processed_data["reference_price"] == Decimal("0.12")


@pytest.mark.asyncio
async def test_stale_basket_member_cancels_quotes_even_if_provider_unready():
    controller, _ = make_controller(configuration(
        scenario="follow_market", references=[{"pair": "BTC-USDT", "weight": "1"}]))
    await controller.update_processed_data()
    controller.executors_info = [SimpleNamespace(id="open", is_active=True)]
    controller.market_data_provider.get_order_book.return_value = SimpleNamespace(snapshot_uid=980, last_diff_uid=980)
    controller.market_data_provider.get_order_book.side_effect = None
    controller.market_data_provider.ready = False
    await controller.control_task()
    actions = controller.actions_queue.get_nowait()
    assert [action.executor_id for action in actions] == ["open"]
    assert actions[0].keep_position
    assert not controller.processed_data["ready"]
    assert "reference_price" not in controller.processed_data
    await controller.control_task()
    assert controller.actions_queue.empty()


def test_periodic_yaml_reload_does_not_overwrite_api_selection():
    controller, _ = make_controller(configuration(runtime_config_managed=True, total_amount_quote="20"))
    controller.update_config(configuration(total_amount_quote="100"))
    assert controller.config.total_amount_quote == Decimal("20")


def test_controller_discovery_returns_the_life_extension():
    assert configuration().get_controller_class() is LifeMMController


@pytest.mark.asyncio
async def test_refresh_preserves_filled_positions():
    controller, _ = make_controller(configuration(executor_refresh_time=10))
    controller.executors_info = [SimpleNamespace(id="filled", is_active=True, is_trading=True, timestamp=980)]
    actions = controller.executors_to_refresh()
    assert len(actions) == 1
    assert actions[0].executor_id == "filled"
    assert actions[0].keep_position


@pytest.mark.asyncio
async def test_instrument_missing_from_exchange_metadata_cannot_quote():
    controller, _ = make_controller(configuration())
    connector = controller.market_data_provider.get_connector_with_fallback.return_value
    connector.trading_rules = {}
    await controller.update_processed_data()
    assert not controller.processed_data["ready"]


@pytest.mark.asyncio
async def test_perpetual_setup_requires_successful_mode_and_leverage_ack():
    controller, _ = make_controller(configuration(connector_name="okx_perpetual", leverage=2))
    connector = controller.market_data_provider.get_connector_with_fallback.return_value
    connector._trading_pair_position_mode_set = AsyncMock(return_value=(False, "rejected"))
    connector._set_trading_pair_leverage = AsyncMock(return_value=(True, ""))
    with pytest.raises(ValueError, match="rejected"):
        await controller.on_start()
    connector._set_trading_pair_leverage.assert_not_awaited()


@pytest.mark.asyncio
async def test_spot_inventory_cap_blocks_buy_but_keeps_funded_sell():
    controller, _ = make_controller(configuration(max_base_inventory="1000"))
    await controller.update_processed_data()
    actions = controller.create_actions_proposal()
    assert [action.executor_config.side for action in actions] == [TradeType.SELL]


@pytest.mark.asyncio
async def test_perpetual_gross_cap_cannot_be_bypassed_by_offsetting_long_and_short():
    controller, _ = make_controller(configuration(connector_name="okx_perpetual", max_gross_base="1500"))
    controller._account_ready = True
    connector = controller.market_data_provider.get_connector_with_fallback.return_value
    connector.account_positions = {
        "long": SimpleNamespace(trading_pair="LIFE-USDT", position_side=PositionSide.LONG, amount=Decimal("1000")),
        "short": SimpleNamespace(trading_pair="LIFE-USDT", position_side=PositionSide.SHORT, amount=Decimal("-1000")),
    }
    controller.market_data_provider.get_order_book.return_value = SimpleNamespace(snapshot_uid=1000000, last_diff_uid=1000000)
    controller.market_data_provider.get_order_book.side_effect = None
    await controller.update_processed_data()
    assert controller.create_actions_proposal() == []


@pytest.mark.asyncio
async def test_pending_executor_entry_is_counted_before_its_order_is_visible():
    controller, _ = make_controller(configuration(max_base_inventory="1700", buy_spreads=[0.01, 0.02]))
    await controller.update_processed_data()
    entry = controller.get_executor_config("buy_0", Decimal("0.099"), Decimal("600"))
    controller.executors_info = [SimpleNamespace(id="pending", is_active=True, is_trading=False,
                                                 config=entry, custom_info={"level_id": "buy_0"})]
    assert all(action.executor_config.side != TradeType.BUY for action in controller.create_actions_proposal())


@pytest.mark.asyncio
async def test_one_stale_reference_blocks_whole_basket_without_partial_anchors():
    controller, _ = make_controller(configuration(scenario="follow_market", references=[
        {"pair": "BTC-USDT", "weight": "0.6"}, {"pair": "ETH-USDT", "weight": "0.4"}]))
    controller.market_data_provider.get_order_book.side_effect = lambda connector, pair: SimpleNamespace(
        snapshot_uid=980 if pair == "ETH-USDT" else 1000, last_diff_uid=0)
    await controller.update_processed_data()
    assert not controller.processed_data["ready"]
    assert controller._source_anchors == {}
    assert controller.create_actions_proposal() == []


@pytest.mark.asyncio
async def test_perpetual_cannot_quote_before_account_confirmation():
    controller, _ = make_controller(configuration(connector_name="okx_perpetual"))
    await controller.update_processed_data()
    assert controller.create_actions_proposal() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("anchor,expected_ready", [("0.1", True), (None, False)])
async def test_empty_listing_book_requires_explicit_initial_anchor(anchor, expected_ready):
    controller, prices = make_controller(configuration(initial_anchor=anchor))
    book = OrderBook()
    book.apply_snapshot([], [], 1000)
    controller.market_data_provider.get_order_book.side_effect = None
    controller.market_data_provider.get_order_book.return_value = book
    prices["LIFE-USDT"] = Decimal("NaN")
    await controller.update_processed_data()
    assert controller.processed_data["ready"] is expected_ready
    if expected_ready:
        assert controller.processed_data["reference_price"] == Decimal("0.1")


@pytest.mark.asyncio
async def test_initial_anchor_never_bypasses_stale_listing_book():
    controller, prices = make_controller(configuration(initial_anchor="0.1"))
    book = OrderBook()
    book.apply_snapshot([], [], 980)
    controller.market_data_provider.get_order_book.side_effect = None
    controller.market_data_provider.get_order_book.return_value = book
    prices["LIFE-USDT"] = Decimal("NaN")
    await controller.update_processed_data()
    assert not controller.processed_data["ready"]


@pytest.mark.asyncio
async def test_quote_bound_violation_cancels_existing_quotes():
    controller, _ = make_controller(configuration(min_price="0.0995"))
    controller.executors_info = [SimpleNamespace(id="old", is_active=True)]
    await controller.control_task()
    assert not controller.processed_data["ready"]
    assert controller.actions_queue.get_nowait()[0].executor_id == "old"


@pytest.mark.asyncio
async def test_initial_anchor_is_only_a_fallback_when_no_life_mid_exists():
    controller, _ = make_controller(configuration(initial_anchor="0.2"))
    await controller.update_processed_data()
    assert controller.processed_data["reference_price"] == Decimal("0.1")


@pytest.mark.asyncio
async def test_confirmed_hedge_and_leverage_allow_perpetual_quotes():
    controller, _ = make_controller(configuration(connector_name="okx_perpetual", leverage=2))
    connector = controller.market_data_provider.get_connector_with_fallback.return_value
    connector._fetch_account_position_mode.return_value = PositionMode.HEDGE
    connector._set_trading_pair_leverage = AsyncMock(return_value=(True, ""))
    await controller.on_start()
    connector._perpetual_trading.set_leverage.assert_called_once_with("LIFE-USDT", 2)
    controller.market_data_provider.get_order_book.side_effect = None
    controller.market_data_provider.get_order_book.return_value = SimpleNamespace(snapshot_uid=1000000, last_diff_uid=1000000)
    await controller.update_processed_data()
    actions = controller.create_actions_proposal()
    assert len(actions) == 2
    assert all(action.executor_config.leverage == 2 for action in actions)
    assert all(action.executor_config.triple_barrier_config.open_order_type == OrderType.LIMIT_MAKER for action in actions)


@pytest.mark.asyncio
async def test_leverage_rejection_keeps_perpetual_entries_blocked():
    controller, _ = make_controller(configuration(connector_name="okx_perpetual", leverage=2))
    connector = controller.market_data_provider.get_connector_with_fallback.return_value
    connector._fetch_account_position_mode.return_value = PositionMode.HEDGE
    connector._set_trading_pair_leverage = AsyncMock(return_value=(False, "rejected"))
    with pytest.raises(ValueError, match="rejected"):
        await controller.on_start()
    await controller.update_processed_data()
    assert controller.create_actions_proposal() == []


@pytest.mark.asyncio
async def test_market_mode_change_with_positions_is_rejected_before_exchange_mutation():
    controller, _ = make_controller(configuration(connector_name="okx_perpetual"))
    connector = controller.market_data_provider.get_connector_with_fallback.return_value
    connector.account_positions = {"held": object()}
    connector._trading_pair_position_mode_set = AsyncMock()
    with pytest.raises(ValueError, match="existing orders or positions"):
        await controller.on_start()
    connector._trading_pair_position_mode_set.assert_not_awaited()
