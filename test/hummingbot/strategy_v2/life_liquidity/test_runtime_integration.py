"""Integration checks against Hummingbot's real CLI, controller, and V2 runner."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from typer.testing import CliRunner

from controllers.generic.life_liquidity import LifeLiquidityConfig, LifeLiquidityController
from hummingbot.cli import bot, strategy_configs
from hummingbot.cli.main import app
from hummingbot.cli.strategy_configs import available_controllers, describe_strategy, edit_config, validate_controller
from hummingbot.client import settings
from hummingbot.client.config import config_helpers
from hummingbot.connector.exchange.okx.okx_book_health import BookFeedHealth
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase
from hummingbot.strategy_v2.life_liquidity.market_data import InstrumentRules
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction


def _controller():
    config = LifeLiquidityConfig.model_construct(id="life-test")
    provider = MagicMock()
    provider.initialize_order_book = AsyncMock(return_value=True)
    return LifeLiquidityController(config, provider, MagicMock())


def _synchronized_health(epoch=1):
    return BookFeedHealth(connected=True, synchronized=True, epoch=epoch, sequence_id=10,
                          snapshot_exchange_timestamp_ms=1800, snapshot_received_monotonic=0,
                          last_message_monotonic=0, reason_code="BOOK_FEED_SYNCHRONIZED")


def test_real_cli_discovers_and_round_trips_nested_controller(tmp_path):
    assert "life_liquidity" in available_controllers()
    data, required, updatable = describe_strategy("controller", "life_liquidity")
    assert required == []
    assert "strategy" not in updatable
    assert data["strategy"]["session"]["duration"] == "4h"
    config_path = tmp_path / "life.yml"
    config_path.write_text(yaml.safe_dump(data))

    config, _ = validate_controller(config_path)
    assert isinstance(config, LifeLiquidityConfig)
    assert config.strategy.session.duration == "4h"
    value, _ = edit_config(config_path, "controller", "strategy.session.duration", "2h")
    assert value == "2h"
    updated, _ = validate_controller(config_path)
    assert updated.strategy.session.duration == "2h"


def test_hbot_create_command_writes_valid_nested_life_config(tmp_path, monkeypatch):
    monkeypatch.setitem(strategy_configs.TYPE_DIRS, "controller", tmp_path)
    loaded = []
    monkeypatch.setattr(bot, "write_loaded", lambda file, kind: loaded.append((file, kind)))

    result = CliRunner().invoke(app, [
        "create", "life_liquidity", "--controller", "--name", "life_cli.yml",
        "--set", "strategy.session.duration=2h",
    ])

    assert result.exit_code == 0, result.output
    assert loaded == [("life_cli.yml", "controller")]
    config, _ = validate_controller(tmp_path / "life_cli.yml")
    assert config.strategy.session.duration == "2h"
    assert config.strategy.execution_mode == "simulation"


def test_hbot_config_command_edits_nested_field_and_rolls_back_invalid_value(tmp_path, monkeypatch):
    data, _, _ = describe_strategy("controller", "life_liquidity")
    path = tmp_path / "life_cli.yml"
    path.write_text(yaml.safe_dump(data))
    monkeypatch.setitem(strategy_configs.TYPE_DIRS, "controller", tmp_path)
    monkeypatch.setattr(bot, "running", lambda: False)
    monkeypatch.setattr(bot, "read_loaded", lambda: {"file": path.name, "type": "controller"})
    monkeypatch.setattr(config_helpers, "load_client_config_map_from_file", lambda: SimpleNamespace(config_paths=lambda: []))

    valid = CliRunner().invoke(app, ["config", "strategy.session.duration", "3h"])
    assert valid.exit_code == 0, valid.output
    assert validate_controller(path)[0].strategy.session.duration == "3h"
    before_invalid = path.read_text()

    invalid = CliRunner().invoke(app, ["config", "strategy.session.duration", "invalid"])
    assert invalid.exit_code != 0
    assert path.read_text() == before_invalid


def test_real_cli_rejects_invalid_nested_edit_without_changing_file(tmp_path):
    data, _, _ = describe_strategy("controller", "life_liquidity")
    config_path = tmp_path / "life.yml"
    config_path.write_text(yaml.safe_dump(data))
    before = config_path.read_text()

    with pytest.raises(Exception):
        edit_config(config_path, "controller", "strategy.session.duration", "invalid")

    assert config_path.read_text() == before


def test_real_v2_loader_resolves_life_controller_class(tmp_path, monkeypatch):
    data, _, _ = describe_strategy("controller", "life_liquidity")
    (tmp_path / "life.yml").write_text(yaml.safe_dump(data))
    monkeypatch.setattr(settings, "CONTROLLERS_CONF_DIR_PATH", tmp_path)

    loaded = StrategyV2ConfigBase(controllers_config=["life.yml"]).load_controller_configs()

    assert len(loaded) == 1
    assert isinstance(loaded[0], LifeLiquidityConfig)
    assert loaded[0].get_controller_class() is LifeLiquidityController


def test_runner_notifies_life_controller_when_config_loading_fails(tmp_path, monkeypatch):
    controller = _controller()
    data = controller.config.model_dump(mode="json")
    data["strategy"]["session"]["duration"] = "invalid"
    (tmp_path / "life.yml").write_text(yaml.safe_dump(data))
    monkeypatch.setattr(settings, "CONTROLLERS_CONF_DIR_PATH", tmp_path)

    runner = SimpleNamespace(
        config=StrategyV2ConfigBase(controllers_config=["life.yml"]),
        controllers={controller.config.id: controller},
        _last_config_update_ts=0, config_update_interval=10, current_timestamp=11,
        logger=lambda: MagicMock(),
    )

    StrategyV2Base.update_controllers_configs(runner)

    assert controller.config_update_state.last_rejection.reason_code == "CONFIG_LOAD_FAILED"
    assert controller.config_update_state.config is controller.config.strategy
    assert controller.config_update_state.order_permission() is False


def test_runner_filters_queued_create_action_after_invalid_config():
    controller = _controller()
    controller.trading_permissions_ready = lambda: True
    controller.listing_gate.instrument_found = True
    controller.listing_gate.instrument_rules = True
    controller.listing_gate.instrument_state = "live"
    controller.book_ready = True
    controller.continuous_gate.ready = True
    controller.snapshot_gate.ready = True
    controller.snapshot_gate.snapshot = SimpleNamespace(received_monotonic=0, observed_age_ms=0)
    controller.snapshot_gate.clock = lambda: 0
    controller._live_spot_book_ready = lambda: True
    controller._book_feed_health = _synchronized_health
    controller.continuity_gate.confirmed_epoch = 1
    action = CreateExecutorAction.model_construct(controller_id=controller.config.id, executor_config=None)
    runner = SimpleNamespace(controllers={controller.config.id: controller}, logger=lambda: MagicMock())
    assert StrategyV2Base._filter_authorized_actions(runner, [action]) == [action]

    controller.on_config_load_failure()

    assert StrategyV2Base._filter_authorized_actions(runner, [action]) == []


@pytest.mark.asyncio
async def test_controller_polling_unlisted_life_never_submits_a_queued_order():
    from hummingbot.connector.exchange.okx import okx_constants

    controller = _controller()
    calls = []

    async def api_get(*, path_url, params):
        calls.append((path_url, params))
        return {"code": "0", "data": [{"instType": "SPOT", "instId": "BTC-USDT"}]}

    connector = SimpleNamespace(_api_get=api_get, trading_pairs_request_path=okx_constants.OKX_INSTRUMENTS_PATH)
    controller.market_data_provider.get_connector_with_fallback.return_value = connector
    controller.market_data_provider.ready = False
    controller.listing_gate.clock = lambda: 0
    controller.trading_permissions_ready = lambda: True
    action = CreateExecutorAction.model_construct(controller_id=controller.config.id, executor_config=None)
    runner = SimpleNamespace(controllers={controller.config.id: controller}, logger=lambda: MagicMock())

    await controller.control_task()
    await controller.control_task()

    assert calls == [(okx_constants.OKX_INSTRUMENTS_PATH, {"instType": "SPOT"})]
    assert controller.listing_gate.state == "WAITING_READY"
    assert controller.processed_data["reason_code"] == "INSTRUMENT_NOT_FOUND"
    assert controller.determine_executor_actions() == []
    controller.actions_queue.put.assert_not_called()
    assert StrategyV2Base._filter_authorized_actions(runner, [action]) == []


@pytest.mark.asyncio
async def test_controller_blocks_orders_when_rules_book_or_market_state_fail():
    from hummingbot.connector.exchange.okx import okx_constants

    controller = _controller()
    state = {"item": {"instType": "SPOT", "instId": "LIFE-USDT", "state": "live",
                      "tickSz": "0.0001", "lotSz": "0.1", "minSz": "1",
                      "openType": "", "listTime": "1000", "contTdSwTime": ""}, "book_ready": False}

    async def api_get(*, path_url, params=None):
        if path_url == okx_constants.OKX_SERVER_TIME_PATH:
            return {"code": "0", "data": [{"ts": "2000"}]}
        if path_url == okx_constants.OKX_ORDER_BOOK_PATH:
            assert params == {"instId": "LIFE-USDT", "sz": "5"}
            return {"code": "0", "data": [{"ts": "1900", "seqId": 11, "bids": [["1", "10"]],
                                          "asks": [["1.1", "10"]]}]}
        assert path_url == okx_constants.OKX_INSTRUMENTS_PATH and params == {"instType": "SPOT"}
        return {"code": "0", "data": [state["item"]]}

    book = SimpleNamespace(get_price=lambda is_buy: 2 if is_buy else 1)
    connector = SimpleNamespace(
        _api_get=api_get, trading_pairs_request_path=okx_constants.OKX_INSTRUMENTS_PATH,
        check_network_request_path=okx_constants.OKX_SERVER_TIME_PATH,
        get_order_book=lambda pair: book,
    )
    controller.market_data_provider.get_connector_with_fallback.return_value = connector
    controller.market_data_provider.ready = False
    controller.listing_gate.clock = lambda: state["now"]
    controller.snapshot_gate.clock = lambda: state["now"]
    controller.trading_permissions_ready = lambda: True
    controller._book_feed_health = _synchronized_health
    action = CreateExecutorAction.model_construct(controller_id=controller.config.id, executor_config=None)
    runner = SimpleNamespace(controllers={controller.config.id: controller}, logger=lambda: MagicMock())

    for now, item_state, tick_size, book_ready, reason in [
        (0, "live", "", True, "INSTRUMENT_RULES_INVALID"),
        (5, "suspend", "0.0001", True, "INSTRUMENT_NOT_LIVE"),
        (10, "live", "0.0001", False, "ORDER_BOOK_NOT_READY"),
    ]:
        state["now"] = now
        state["item"]["state"] = item_state
        state["item"]["tickSz"] = tick_size
        connector.ready = book_ready
        await controller.control_task()
        assert controller.processed_data["reason_code"] == reason
        assert controller.allow_create_executor_actions() is False
        assert StrategyV2Base._filter_authorized_actions(runner, [action]) == []
        controller.actions_queue.put.assert_not_called()

    state["now"] = 15
    connector.ready = True
    await controller.control_task()
    assert controller.listing_gate.metadata_ready is True
    assert controller.book_ready is True
    assert controller.allow_create_executor_actions() is True  # P4 permit is stubbed above.
    feed = {"health": _synchronized_health()}
    controller._book_feed_health = lambda: feed["health"]
    assert StrategyV2Base._filter_authorized_actions(runner, [action]) == [action]
    feed["health"] = _synchronized_health(epoch=2)
    assert StrategyV2Base._filter_authorized_actions(runner, [action]) == []
    feed["health"] = BookFeedHealth(epoch=2, reason_code="BOOK_FEED_DISCONNECTED")
    assert StrategyV2Base._filter_authorized_actions(runner, [action]) == []
    assert controller.determine_executor_actions() == []
    controller.actions_queue.put.assert_not_called()


@pytest.mark.asyncio
async def test_spot_book_bootstrap_failure_blocks_then_retries_without_swap():
    from hummingbot.connector.exchange.okx import okx_constants

    controller = _controller()
    controller.listing_gate.instrument_found = True
    controller.listing_gate.instrument_state = "live"
    controller.listing_gate.instrument_rules = InstrumentRules.from_okx(
        {"tickSz": "0.0001", "lotSz": "0.1", "minSz": "1"})
    controller.listing_gate.instrument_info = {
        "state": "live", "openType": "", "listTime": "1000", "contTdSwTime": ""}
    controller.listing_gate.next_refresh_at = 100
    controller.listing_gate.clock = lambda: 0
    now = {"monotonic": 0}
    controller.snapshot_gate.clock = lambda: now["monotonic"]
    controller._book_feed_health = _synchronized_health
    controller.market_data_provider.ready = False
    controller.market_data_provider.initialize_order_book = AsyncMock(side_effect=[False, True, True])

    async def api_get(*, path_url, params=None):
        if path_url == okx_constants.OKX_SERVER_TIME_PATH:
            return {"code": "0", "data": [{"ts": "2000"}]}
        assert path_url == okx_constants.OKX_ORDER_BOOK_PATH
        return {"code": "0", "data": [{"ts": "1900", "seqId": 11,
                                      "bids": [["1", "10"]], "asks": [["1.1", "10"]]}]}

    book = SimpleNamespace(get_price=lambda is_buy: 1.1 if is_buy else 1)
    tracked_books = {"LIFE-USDT": book}
    connector = SimpleNamespace(
        _api_get=api_get, check_network_request_path=okx_constants.OKX_SERVER_TIME_PATH,
        ready=True, order_book_tracker=SimpleNamespace(order_books=tracked_books),
        # Some connectors may retain a cached book object after tracker removal.
        get_order_book=lambda pair: book,
    )
    controller.market_data_provider.get_connector_with_fallback.return_value = connector

    await controller.control_task()
    assert controller.processed_data["reason_code"] == "ORDER_BOOK_BOOTSTRAP_FAILED"
    assert controller.processed_data["spot_market_data_ready"] is False
    controller.actions_queue.put.assert_not_called()

    await controller.control_task()
    assert controller.market_data_provider.initialize_order_book.await_count == 1
    assert controller.processed_data["spot_market_data_ready"] is False

    now["monotonic"] = 5
    await controller.control_task()
    assert controller.processed_data["spot_book_bootstrap_ready"] is True
    assert controller.processed_data["spot_market_data_ready"] is True
    assert controller.processed_data["perpetual_contract_listed"] is False
    assert controller.market_data_provider.initialize_order_book.await_count == 2
    controller.actions_queue.put.assert_not_called()

    controller.trading_permissions_ready = lambda: True
    assert controller.allow_create_executor_actions() is True
    tracked_books.clear()
    assert controller.allow_create_executor_actions() is False

    now["monotonic"] = 10
    await controller.control_task()
    assert controller.processed_data["reason_code"] == "ORDER_BOOK_TRACKING_LOST"
    assert controller.processed_data["spot_book_bootstrap_ready"] is False
    assert controller.market_data_provider.initialize_order_book.await_count == 2

    tracked_books["LIFE-USDT"] = book
    now["monotonic"] = 15
    await controller.control_task()
    assert controller.processed_data["spot_market_data_ready"] is True
    assert controller.market_data_provider.initialize_order_book.await_count == 3


@pytest.mark.asyncio
async def test_live_auction_cannot_create_even_with_book_and_other_permits():
    from hummingbot.connector.exchange.okx import okx_constants

    controller = _controller()
    now = {"monotonic": 0, "server": "1500"}
    instrument = {"instType": "SPOT", "instId": "LIFE-USDT", "state": "live",
                  "tickSz": "0.0001", "lotSz": "0.1", "minSz": "1",
                  "openType": "call_auction", "listTime": "1000", "contTdSwTime": "2000"}

    async def api_get(*, path_url, params=None):
        if path_url == okx_constants.OKX_INSTRUMENTS_PATH:
            return {"code": "0", "data": [instrument]}
        if path_url == okx_constants.OKX_ORDER_BOOK_PATH:
            return {"code": "0", "data": [{"ts": "1450", "bids": [["1", "10"]],
                                          "asks": [["1.1", "10"]]}]}
        assert path_url == okx_constants.OKX_SERVER_TIME_PATH
        if now["server"] == "error":
            raise OSError("OKX time endpoint unavailable")
        return {"code": "0", "data": [{"ts": now["server"]}]}

    book = SimpleNamespace(get_price=lambda is_buy: 2 if is_buy else 1)
    connector = SimpleNamespace(
        _api_get=api_get, trading_pairs_request_path=okx_constants.OKX_INSTRUMENTS_PATH,
        check_network_request_path=okx_constants.OKX_SERVER_TIME_PATH,
        ready=True, get_order_book=lambda pair: book,
    )
    controller.market_data_provider.get_connector_with_fallback.return_value = connector
    controller.market_data_provider.ready = False
    controller.listing_gate.clock = lambda: now["monotonic"]
    controller.trading_permissions_ready = lambda: True
    action = CreateExecutorAction.model_construct(controller_id=controller.config.id, executor_config=None)
    runner = SimpleNamespace(controllers={controller.config.id: controller}, logger=lambda: MagicMock())

    await controller.control_task()
    assert controller.listing_gate.metadata_ready is True
    assert controller.book_ready is True
    assert controller.continuous_gate.ready is False
    assert controller.processed_data["reason_code"] == "CONTINUOUS_TRADING_NOT_STARTED"
    assert "CONTINUOUS_TRADING_NOT_STARTED" in controller.to_format_status()[0]
    assert StrategyV2Base._filter_authorized_actions(runner, [action]) == []
    controller.actions_queue.put.assert_not_called()

    now["monotonic"] = 5
    now["server"] = "2000"
    await controller.control_task()
    assert controller.continuous_gate.ready is True
    assert controller.determine_executor_actions() == []

    now["monotonic"] = 10
    now["server"] = "error"
    await controller.control_task()
    assert controller.continuous_gate.ready is False
    assert controller.processed_data["reason_code"] == "EXCHANGE_TIME_FETCH_ERROR"
    assert StrategyV2Base._filter_authorized_actions(runner, [action]) == []

    now["monotonic"] = 15
    now["server"] = "3000"
    instrument["state"] = "suspend"
    await controller.control_task()
    assert controller.continuous_gate.ready is False
    assert StrategyV2Base._filter_authorized_actions(runner, [action]) == []


@pytest.mark.asyncio
async def test_stale_book_revokes_queued_create_permission_between_controller_ticks():
    from hummingbot.connector.exchange.okx import okx_constants

    controller = _controller()
    now = {"monotonic": 0, "book_error": False}

    async def api_get(*, path_url, params=None):
        if path_url == okx_constants.OKX_INSTRUMENTS_PATH:
            return {"code": "0", "data": [{
                "instType": "SPOT", "instId": "LIFE-USDT", "state": "live",
                "tickSz": "0.0001", "lotSz": "0.1", "minSz": "1",
                "openType": "", "listTime": "1000", "contTdSwTime": "",
            }]}
        if path_url == okx_constants.OKX_ORDER_BOOK_PATH:
            assert params == {"instId": "LIFE-USDT", "sz": "5"}
            if now["book_error"]:
                raise OSError("book endpoint unavailable")
            return {"code": "0", "data": [{"ts": "1900", "seqId": 11, "bids": [["1", "10"]],
                                          "asks": [["1.1", "10"]]}]}
        assert path_url == okx_constants.OKX_SERVER_TIME_PATH
        return {"code": "0", "data": [{"ts": "2000"}]}

    tracked_book = SimpleNamespace(get_price=lambda is_buy: 1.1 if is_buy else 1)
    connector = SimpleNamespace(
        _api_get=api_get, trading_pairs_request_path=okx_constants.OKX_INSTRUMENTS_PATH,
        check_network_request_path=okx_constants.OKX_SERVER_TIME_PATH,
        ready=True, get_order_book=lambda pair: tracked_book,
    )
    controller.market_data_provider.get_connector_with_fallback.return_value = connector
    controller.market_data_provider.ready = False
    controller.listing_gate.clock = lambda: now["monotonic"]
    controller.snapshot_gate.clock = lambda: now["monotonic"]
    controller.trading_permissions_ready = lambda: True
    controller._book_feed_health = _synchronized_health
    action = CreateExecutorAction.model_construct(controller_id=controller.config.id, executor_config=None)
    runner = SimpleNamespace(controllers={controller.config.id: controller}, logger=lambda: MagicMock())

    await controller.control_task()
    assert controller.snapshot_gate.snapshot.exchange_timestamp_ms == 1900
    assert controller.snapshot_gate.snapshot.received_monotonic == 0
    assert StrategyV2Base._filter_authorized_actions(runner, [action]) == [action]
    now["monotonic"] = 2.0
    assert StrategyV2Base._filter_authorized_actions(runner, [action]) == []
    assert "BOOK_STALE" in controller.to_format_status()[0]
    controller.actions_queue.put.assert_not_called()

    now["monotonic"] = 5
    now["book_error"] = True
    await controller.control_task()
    assert controller.processed_data["reason_code"] == "BOOK_FETCH_ERROR"
    assert StrategyV2Base._filter_authorized_actions(runner, [action]) == []


def test_runner_keeps_unmanaged_script_create_actions():
    action = CreateExecutorAction.model_construct(controller_id="main", executor_config=None)
    runner = SimpleNamespace(controllers={}, logger=lambda: MagicMock())
    assert StrategyV2Base._filter_authorized_actions(runner, [action]) == [action]


@pytest.mark.asyncio
async def test_real_runner_listener_drops_queued_create_after_rejection():
    controller = _controller()
    controller.on_config_load_failure()
    action = CreateExecutorAction.model_construct(controller_id=controller.config.id, executor_config=None)

    class OneActionQueue:
        def __init__(self):
            self.delivered = False

        async def get(self):
            if self.delivered:
                await asyncio.Future()
            self.delivered = True
            return [action]

    queue = OneActionQueue()
    orchestrator = MagicMock()
    runner = SimpleNamespace(
        controllers={controller.config.id: controller}, actions_queue=queue,
        executor_orchestrator=orchestrator, logger=lambda: MagicMock(),
    )
    runner._filter_authorized_actions = lambda actions: StrategyV2Base._filter_authorized_actions(runner, actions)

    task = asyncio.create_task(StrategyV2Base.listen_to_executor_actions(runner))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert queue.delivered is True
    orchestrator.execute_actions.assert_not_called()
