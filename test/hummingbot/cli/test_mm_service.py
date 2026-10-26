import asyncio
import random
from decimal import Decimal
from test.hummingbot.client.config.test_mm_profiles import CATALOGUE
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from controllers.market_making.life_mm import LifeMMConfig, LifeMMController
from hummingbot.cli.service import ServiceHost
from hummingbot.client.config.mm_profiles import MMProfilesConfig
from hummingbot.client.config.runtime_config import RuntimeSettings
from hummingbot.core.data_type.order_book import OrderBook
from hummingbot.core.data_type.order_book_row import OrderBookRow
from hummingbot.core.mm_session import MMSession, MMSessionRequest
from hummingbot.strategy_v2.utils.quote_guard import QuoteGuard


async def setup_host():
    catalogue = await MMProfilesConfig.load(CATALOGUE)
    session = MMSession.create(catalogue, MMSessionRequest(
        market_mode="both", profile_id="follow_market", duration_seconds=3600), rng=random.Random(1))
    host = ServiceHost(RuntimeSettings.from_environment({"DATABASE_URL": "postgresql://unused/test"}),
                       asyncio.Event(), MagicMock())
    host.mm_session = session
    host.applied_version = 1
    host.owner = MagicMock(generation=1, owner_uuid="owner")
    host.store = MagicMock()
    provider = MagicMock()
    provider.time.return_value = 1000
    provider.ready = True
    provider.get_order_book.side_effect = lambda name, pair: SimpleNamespace(
        snapshot_uid=1000000 if name == "okx_perpetual" else 1000, last_diff_uid=0)
    prices = {"LIFE-USDT": Decimal("0.1"), "BTC-USDT": Decimal("100"), "ETH-USDT": Decimal("10")}
    provider.get_price_by_type.side_effect = lambda name, pair, *args: prices[pair]
    connectors = {name: MagicMock() for name in ("okx", "okx_perpetual")}
    for connector in connectors.values():
        connector.trading_rules = dict.fromkeys(prices, object())
        connector.account_positions = {}
        connector.get_balance.return_value = Decimal("1000")
        connector.get_available_balance.return_value = Decimal("1000")
    provider.get_connector_with_fallback.side_effect = connectors.__getitem__
    configs = catalogue.controller_configs("follow_market", "both", None)
    controllers = {}
    for config in configs:
        config = LifeMMConfig.model_validate({**config.model_dump(), "mm_session_id": session.id, "mm_generation": 0})
        controller = LifeMMController(config, provider, asyncio.Queue())
        controller._account_ready = True
        controller.update_reference()
        controllers[config.id] = controller
    host.hb = SimpleNamespace(trading_core=SimpleNamespace(connector_manager=SimpleNamespace(connectors=connectors), strategy=SimpleNamespace(
        controllers=controllers, market_data_provider=provider)))
    return host, controllers, provider


@pytest.mark.asyncio
async def test_effective_service_config_reuses_runner_and_binds_session_scope():
    host, _, _ = await setup_host()
    config = host.mm_effective_config(host.mm_session)
    assert config["script_file_name"] == "v2_with_controllers.py"
    assert len(config["controller_config_overrides"]) == 2
    for controller in config["controller_config_overrides"]:
        assert controller["mm_session_id"] == host.mm_session.id
        assert controller["mm_generation"] == 0


@pytest.mark.asyncio
async def test_drawdown_thresholds_are_wired_to_existing_runner_without_an_inactive_controller():
    host, _, _ = await setup_host()
    data = host.mm_session.catalogue.model_dump(mode="json")
    data.update(max_global_drawdown_quote="10", max_controller_drawdown_quote="5")
    host.mm_session.catalogue = MMProfilesConfig.model_validate(data)
    host.mm_session.request.market_mode = "spot"
    effective = host.mm_effective_config(host.mm_session)
    assert effective["max_global_drawdown_quote"] == "10"
    assert effective["max_controller_drawdown_quote"] == "5"
    assert len(effective["controller_config_overrides"]) == 1
    assert set(effective["mm_drawdown_scope"]) == {"life_spot", "life_perpetual"}


@pytest.mark.asyncio
async def test_outflow_policy_survives_perpetual_selection_without_a_global_buy_cap_gate():
    host, _, _ = await setup_host()
    data = host.mm_session.catalogue.model_dump(mode="json")
    data["spot_outflow"] = dict(window_seconds=60, max_net_outflow_quote="20", fee_allowance_rate=".001")
    host.mm_session.catalogue = MMProfilesConfig.model_validate(data)
    host.mm_session.request.market_mode = "perpetual"
    effective = host.mm_effective_config(host.mm_session)
    assert effective["mm_spot_outflow_policy"]["trading_pair"] == "LIFE-USDT"
    assert len(effective["controller_config_overrides"]) == 1
    # Scope of this limit is BUY authorization, not shared MM readiness.
    host.mm_session.request.market_mode = "both"
    host.owner.spot_outflow = SimpleNamespace(status=lambda: dict(ready=False, remaining_quote=0))
    assert host.mm_runtime_ready()


@pytest.mark.asyncio
async def test_both_runtime_gate_rejects_one_stale_market():
    host, controllers, provider = await setup_host()
    assert host.mm_runtime_ready()
    provider.get_order_book.side_effect = lambda name, pair: SimpleNamespace(
        snapshot_uid=980000 if name == "okx_perpetual" else 1000, last_diff_uid=0)
    assert not host.mm_runtime_ready()
    assert not controllers["life_perpetual"].processed_data["ready"]


@pytest.mark.asyncio
async def test_one_market_guard_failure_blocks_both_and_readiness_does_not_advance_recovery(monkeypatch):
    host, controllers, provider = await setup_host()
    controller = controllers["life_perpetual"]
    config = controller.config.model_dump()
    config["quote_guards"] = dict(enabled=True, max_deviation_bps="200", resume_deviation_bps="100", recovery_delay_seconds="2")
    controller.config = LifeMMConfig.model_validate(config)
    controller._quote_guard = QuoteGuard(controller.config.quote_guards)
    connector = provider.get_connector_with_fallback("okx_perpetual")
    connector.name = "okx_perpetual"
    connector.in_flight_orders = {}
    connector._order_tracker = SimpleNamespace(cached_orders={}, lost_orders={})
    connector.trading_rules["LIFE-USDT"] = SimpleNamespace(min_order_size=Decimal(".01"), min_base_amount_increment=Decimal(".01"))
    guarded_book = OrderBook()
    original = provider.get_order_book.side_effect
    provider.get_order_book.side_effect = lambda name, pair: guarded_book if name == "okx_perpetual" else original(name, pair)
    tick = [0]
    monkeypatch.setattr("controllers.market_making.life_mm.time.monotonic", lambda: tick[0])

    def update(price, timestamp):
        provider.time.return_value = timestamp
        guarded_book.apply_snapshot([OrderBookRow(float(price - Decimal(".001")), 1000, timestamp * 1000)],
                                    [OrderBookRow(float(price + Decimal(".001")), 1000, timestamp * 1000)], timestamp * 1000)
    update(Decimal(".1"), 1000)
    await controller.update_processed_data()
    tick[0] = 2
    update(Decimal(".1"), 1002)
    await controller.update_processed_data()
    assert controller.processed_data["ready"]
    # Restore fresh spot/reference fixture timestamps while testing the guarded market.
    provider.get_order_book.side_effect = lambda name, pair: guarded_book if name == "okx_perpetual" else SimpleNamespace(snapshot_uid=provider.time(), last_diff_uid=0)
    assert host.mm_runtime_ready()
    anchors = controller.reference_state()
    update(Decimal(".12"), 1003)
    assert not host.mm_runtime_ready()
    assert host.mm_readiness_reason == "reference_deviation"
    assert controllers["life_spot"].processed_data["ready"]
    update(Decimal(".1"), 1004)
    for _ in range(50):
        tick[0] = 100
        assert not host.mm_runtime_ready()
    assert controller.reference_state() == anchors


@pytest.mark.asyncio
async def test_collateral_readiness_wait_reports_its_actual_reason():
    host, controllers, _ = await setup_host()
    host.phase = "waiting_for_mm_readiness"
    config = next(iter(controllers.values())).config
    controllers[config.id].executors_info = [SimpleNamespace(is_active=True, is_trading=False,
                                                             config=SimpleNamespace(amount=Decimal("1"), entry_price=Decimal("1"),
                                                                                    leverage=1, connector_name="okx", side=SimpleNamespace(name="BUY")))]
    host._mm_available_quote = lambda items: Decimal("0")
    assert not host.mm_runtime_ready()
    assert host.service_status()["reason_code"] == "insufficient_collateral"
    host.owner.require_recovery.assert_not_called()


@pytest.mark.asyncio
async def test_admission_persists_joint_anchors_before_allowing_quotes():
    host, controllers, _ = await setup_host()

    def persist(**kwargs):
        assert all(not controller._session_admitted for controller in controllers.values())
        kwargs["change"](host.mm_session, 1000)
        return host.mm_session

    host.store.update_mm_session.side_effect = persist
    assert await host.admit_mm_session()
    assert host.mm_session.started_at == 1000
    assert host.mm_session.expires_at == 4600
    assert host.mm_session.anchors["life_spot"]["sources"] == host.mm_session.anchors["life_perpetual"]["sources"]
    assert all(controller._session_admitted for controller in controllers.values())


@pytest.mark.asyncio
async def test_failed_db_admission_keeps_all_quotes_blocked():
    host, controllers, _ = await setup_host()
    host.store.update_mm_session.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError):
        await host.admit_mm_session()
    assert all(not controller._session_admitted for controller in controllers.values())


def running_row(host, controllers, now):
    host.mm_session.ready(now=1000, anchors={key: controller.reference_state() for key, controller in controllers.items()})
    host.applied_mm_identity = (host.mm_session.id, host.mm_session.generation)
    host.owner.try_acquire.return_value = True
    host.owner.allow_new_orders = True
    host.snapshot = lambda: {}
    host.validate_selection = AsyncMock()
    host.poll_reconciliation = AsyncMock()
    host.poll_funding = MagicMock()
    row = dict(config_type="mm-session", config_file_path="life_mm_profiles.yaml", control_version=1,
               desired_state="running", recovery_state={}, database_now=now,
               mm_session=host.mm_session.model_dump(mode="json"))
    host.owner.check_owner.side_effect = lambda: row
    host.hb.trading_core.trade_fill_db = SimpleNamespace(persistence_healthy=True)
    host.hb.trading_core._strategy_running = True
    return row


@pytest.mark.asyncio
async def test_observer_expires_session_even_when_feed_unready_without_restarting_core():
    host, controllers, provider = await setup_host()
    row = running_row(host, controllers, now=4600)
    host.running = True
    provider.ready = False

    def persist(**kwargs):
        session = MMSession.model_validate(row["mm_session"])
        kwargs["change"](session, row["database_now"])
        row["mm_session"] = session.model_dump(mode="json")
        if session.status == "expired":
            row["desired_state"] = "stopped"
        return session

    async def stop():
        host.running = False
        return True

    host.store.update_mm_session.side_effect = persist
    host.stop_core = AsyncMock(side_effect=stop)
    host.start_strategy = AsyncMock()
    await host.observe_once()
    assert row["mm_session"]["status"] == "expired"
    assert row["control_version"] == 1
    host.stop_core.assert_awaited_once()
    host.start_strategy.assert_not_awaited()


@pytest.mark.asyncio
async def test_observer_cancels_both_on_one_stale_feed_and_preserves_session_deadline():
    host, controllers, provider = await setup_host()
    row = running_row(host, controllers, now=1001)
    host.running = True
    for key, controller in controllers.items():
        controller._session_admitted = True
        controller.executors_info = [SimpleNamespace(id=key + "-open", is_active=True, is_trading=True)]
    provider.get_order_book.side_effect = lambda name, pair: SimpleNamespace(
        snapshot_uid=980000 if name == "okx_perpetual" else 1000, last_diff_uid=0)
    await host.observe_once()
    assert host.phase == "waiting_for_mm_readiness"
    for controller in controllers.values():
        assert not controller._session_admitted
        assert controller.actions_queue.get_nowait()[0].keep_position
    assert row["mm_session"]["expires_at"] == 4600
    host.store.update_mm_session.assert_not_called()


@pytest.mark.asyncio
async def test_mm_start_uses_the_existing_trading_core_and_runner():
    host, _, _ = await setup_host()
    host.hb.trading_core.start_strategy = AsyncMock(return_value=True)
    row = {"config_type": "mm-session", "config_file_path": "life_mm_profiles.yaml",
           "mm_session": host.mm_session.model_dump(mode="json")}
    assert await host.start_strategy(row)
    args = host.hb.trading_core.start_strategy.call_args.args
    assert args[0] == "v2_with_controllers"
    assert args[2] == "life_mm_profiles.yaml"
    assert len(args[1]["controller_config_overrides"]) == 2
