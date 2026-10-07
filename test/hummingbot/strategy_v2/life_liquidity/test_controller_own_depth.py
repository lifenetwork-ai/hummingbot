"""P5.13 controller observation remains fail closed and order inert."""

from test.hummingbot.strategy_v2.life_liquidity.test_own_depth import book
from test.hummingbot.strategy_v2.life_liquidity.test_own_depth_runner import D, setup_order
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from controllers.generic.life_liquidity import LifeLiquidityConfig, LifeLiquidityController
from hummingbot.strategy_v2.life_liquidity.market_data import InstrumentRules


def controller_with_local_order(tmp_path):
    config = LifeLiquidityConfig(
        id="life", own_depth_max_observation_skew_ms=100,
        own_depth_max_book_age_ms=500, own_depth_max_distance_bps=D("200"))
    controller = LifeLiquidityController(config, MagicMock(), MagicMock())
    wal, reservations, tracker, _ = setup_order(tmp_path)
    connector = MagicMock()
    connector._order_tracker = tracker
    controller.market_data_provider.get_connector_with_fallback.return_value = connector
    controller._order_safety_wal = wal
    controller._order_safety_reservations = reservations
    controller._order_safety_gateway = SimpleNamespace(connector=connector)
    controller._order_safety_account_lock = object()
    controller._account_uid_verified = True
    controller.listing_gate.instrument_found = True
    controller.listing_gate.instrument_state = "live"
    controller.listing_gate.instrument_rules = InstrumentRules(D("0.01"), D("0.5"), D("0.5"))
    controller.continuous_gate.ready = True
    controller.snapshot_gate.clock = lambda: 2.05
    controller.snapshot_gate.snapshot = book()
    controller.snapshot_gate.ready = True
    return controller, connector


@pytest.mark.asyncio
async def test_controller_surfaces_independent_reference_without_enabling_orders(tmp_path):
    controller, _ = controller_with_local_order(tmp_path)
    await controller.update_processed_data()
    assert controller.processed_data["own_depth_reason_code"] == "OWN_DEPTH_SEPARATED"
    assert controller.processed_data["independent_life_mid_usdt"] == "1.05"
    assert controller.allow_create_executor_actions() is False


@pytest.mark.asyncio
async def test_controller_revokes_reference_when_tracker_or_identity_is_missing(tmp_path):
    controller, connector = controller_with_local_order(tmp_path)
    await controller.update_processed_data()
    connector._order_tracker = None
    await controller.update_processed_data()
    assert controller.processed_data["independent_life_mid_usdt"] is None
    assert controller.processed_data["own_depth_reason_code"] == "OWN_ORDER_TRACKER_UNAVAILABLE"
    assert "OWN_ORDER_TRACKER_UNAVAILABLE" in controller.to_format_status()[0]
    connector._order_tracker = MagicMock()
    controller._account_uid_verified = False
    await controller.update_processed_data()
    assert controller.processed_data["own_depth_reason_code"] == "OWN_ORDER_SCOPE_UNVERIFIED"


@pytest.mark.asyncio
async def test_controller_requires_explicit_policy_and_current_book(tmp_path):
    controller, _ = controller_with_local_order(tmp_path)
    controller.config.own_depth_max_distance_bps = None
    await controller.update_processed_data()
    assert controller.processed_data["own_depth_reason_code"] == "OWN_DEPTH_POLICY_UNCONFIGURED"
    controller.config.own_depth_max_distance_bps = D("200")
    controller.listing_gate.instrument_found = False
    await controller.update_processed_data()
    assert controller.processed_data["own_depth_reason_code"] == "LIFE_MARKET_NOT_READY"
    controller.listing_gate.instrument_found = True
    controller.snapshot_gate.ready = False
    await controller.update_processed_data()
    assert controller.processed_data["own_depth_reason_code"] == "BOOK_SNAPSHOT_UNAVAILABLE"
