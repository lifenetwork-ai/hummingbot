"""Synthetic independent LIFE reference proof for opt-in quote actions."""

from dataclasses import replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_executor_protected_send import _setup as sender_setup
from test.hummingbot.strategy_v2.life_liquidity.test_final_quote_send import _attach_quote_planner
from test.hummingbot.strategy_v2.life_liquidity.test_own_depth import book
from test.hummingbot.strategy_v2.life_liquidity.test_quote_actions import _setup
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hummingbot.connector.client_order_tracker import ClientOrderTracker
from hummingbot.strategy_v2.life_liquidity.market_data import InstrumentRules
from hummingbot.strategy_v2.life_liquidity.reference import ReferenceEngine, ReferencePolicy


def D(value):
    return Decimal(value)


def market_engine():
    policy = ReferencePolicy(
        max_age_ms=500, min_life_depth_base=D("10"),
        min_source_depth_quote=D("100"), max_deviation_bps=D("500"),
        max_influence=D("0.5"), min_correlation=D("0.2"),
        min_correlation_samples=3)
    return ReferenceEngine(
        policy, life_anchor_usdt=D("1"), model_version="test-v1",
        life_source_id="okx:LIFE-USDT", source_anchors_usdt={})


def strict_quote_source(tmp_path):
    controller, planner, _, _, snapshot = _setup(tmp_path)
    controller.config = controller.config.model_copy(update={
        "own_depth_max_observation_skew_ms": 100,
        "own_depth_max_book_age_ms": 500,
        "own_depth_max_distance_bps": D("200")})
    connector = MagicMock()
    connector._order_tracker = ClientOrderTracker(connector)
    controller.market_data_provider.get_connector_with_fallback.return_value = connector
    controller._order_safety_gateway = SimpleNamespace(connector=connector)
    controller._order_safety_account_lock = object()
    controller._account_uid_verified = True
    controller.listing_gate.instrument_found = True
    controller.listing_gate.instrument_state = "live"
    controller.listing_gate.instrument_rules = InstrumentRules(D("0.01"), D("0.1"), D("0.1"))
    controller.continuous_gate.ready = True
    controller.continuous_gate.exchange_time_ms = 1100
    controller.snapshot_gate.clock = lambda: 2.05
    controller.snapshot_gate.ready = True
    controller.snapshot_gate.snapshot = book(
        bids=[["0.98", "20"]], asks=[["1.02", "20"]])
    planner.reference_engine = market_engine()
    snapshot = replace(snapshot, book_sequence_id=42, reference_model_version="test-v1")
    planner.snapshot = lambda: snapshot
    controller.install_quote_action_planner(planner)
    return controller, planner, connector, snapshot


def test_qualified_book_and_reference_allow_synthetic_proposals(tmp_path):
    controller, _, _, _ = strict_quote_source(tmp_path)
    actions = controller.determine_executor_actions()
    assert len(actions) == 2
    assert controller._own_depth_decision.evidence.mid_usdt == D("1")


def test_changed_book_sequence_revokes_queued_quote(tmp_path):
    controller, planner, _, _ = strict_quote_source(tmp_path)
    config = controller.determine_executor_actions()[0].executor_config
    controller.snapshot_gate.snapshot = replace(controller.snapshot_gate.snapshot, sequence_id=43)
    assert planner.authorizes_config(config) is False
    assert controller._own_depth_decision.evidence is None
    assert controller._own_depth_decision.reason_code == "LIFE_REFERENCE_BOOK_CHANGED"


def test_missing_tracker_or_reference_engine_blocks_actions(tmp_path):
    controller, planner, connector, snapshot = strict_quote_source(tmp_path)
    connector._order_tracker = None
    assert controller.determine_executor_actions() == []
    connector._order_tracker = ClientOrderTracker(connector)
    planner.reference_engine = None
    assert controller.determine_executor_actions() == []
    planner.reference_engine = ReferenceEngine(
        ReferencePolicy(500, D("10"), D("100"), D("500"), D("0.5"), D("0.2"), 3),
        life_anchor_usdt=D("1"), model_version="wrong-source",
        life_source_id="other:LIFE-USDT", source_anchors_usdt={})
    assert controller.determine_executor_actions() == []
    assert snapshot.book_sequence_id == 42


def test_claimed_reference_price_must_match_recomputed_p3_market_reference(tmp_path):
    controller, planner, _, snapshot = strict_quote_source(tmp_path)
    planner.snapshot = lambda: replace(snapshot, qualified_reference_usdt=D("1.01"))
    assert controller.determine_executor_actions() == []


def test_successor_anchor_must_match_independent_book_reference(tmp_path):
    controller, planner, _, snapshot = strict_quote_source(tmp_path)
    planner.snapshot = lambda: replace(snapshot, market_anchor_usdt=D("1.02"))
    assert planner.session_snapshot() is None
    assert controller._own_depth_decision.reason_code == "LIFE_MARKET_ANCHOR_CHANGED"
    planner.snapshot = lambda: replace(snapshot, market_anchor_usdt=D("1"))
    assert planner.session_snapshot() is not None


def strict_queued_quote(tmp_path):
    controller, template, connector, wal, ledger, _ = sender_setup(tmp_path)
    controller.config = controller.config.model_copy(update={
        "own_depth_max_observation_skew_ms": 100,
        "own_depth_max_book_age_ms": 500,
        "own_depth_max_distance_bps": D("200")})
    connector._order_tracker = ClientOrderTracker(connector)
    controller.market_data_provider.get_connector_with_fallback.return_value = connector
    controller._order_safety_account_lock = object()
    controller._account_uid_verified = True
    controller.listing_gate.instrument_found = True
    controller.listing_gate.instrument_state = "live"
    controller.listing_gate.instrument_rules = InstrumentRules(D("0.01"), D("0.1"), D("0.1"))
    controller.continuous_gate.ready = True
    controller.continuous_gate.exchange_time_ms = 1100
    controller.snapshot_gate.clock = lambda: 2.05
    controller.snapshot_gate.ready = True
    controller.snapshot_gate.snapshot = book(
        bids=[["0.98", "20"]], asks=[["1.02", "20"]])
    state, executor = _attach_quote_planner(
        controller, template, wal, ledger,
        reference_engine=market_engine(), book_sequence_id=42,
        reference_model_version="test-v1")
    executor.place_open_order()
    wire = {"clOrdId": executor._order.order_id, "instId": "LIFE-USDT",
            "side": "buy", "ordType": "post_only", "tdMode": "cash",
            "px": str(executor.config.price), "sz": str(executor.config.amount)}
    return controller, connector.sent[0]["pre_send_check"], wire, wal, ledger


def test_final_send_rechecks_independent_reference_after_wal_arm(tmp_path):
    controller, check, wire, wal, ledger = strict_queued_quote(tmp_path)
    check(wire)
    assert wal.get("quote-1").state == "SEND_UNKNOWN"
    assert ledger.has_open_intent("quote-1")
    controller.snapshot_gate.snapshot = replace(controller.snapshot_gate.snapshot, sequence_id=43)
    with pytest.raises(PermissionError, match="SEND_PERMISSION_REVOKED"):
        check(wire)


def test_final_send_revokes_when_tracker_disappears_after_proposal(tmp_path):
    controller, check, wire, wal, ledger = strict_queued_quote(tmp_path)
    connector = controller._order_safety_gateway.connector
    connector._order_tracker = None
    with pytest.raises(PermissionError, match="SEND_PERMISSION_REVOKED"):
        check(wire)
    assert wal.get("quote-1").state == "SEND_UNKNOWN"
    assert ledger.has_open_intent("quote-1")


def test_reference_model_version_change_revokes_queued_quote(tmp_path):
    controller, check, wire, _, _ = strict_queued_quote(tmp_path)
    controller._quote_action_planner.reference_engine.model_version = "test-v2"
    with pytest.raises(PermissionError, match="SEND_PERMISSION_REVOKED"):
        check(wire)
