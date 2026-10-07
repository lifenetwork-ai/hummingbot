"""P5.1/P5.6 opt-in quote actions stay bounded while the controller is disabled."""

from dataclasses import replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_session import FakeClock, begin, manager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from controllers.generic.life_liquidity import LifeLiquidityConfig, LifeLiquidityController
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy
from hummingbot.strategy_v2.life_liquidity.config import QuotesConfig
from hummingbot.strategy_v2.life_liquidity.economics import EconomicPolicy
from hummingbot.strategy_v2.life_liquidity.market_data import InstrumentRules
from hummingbot.strategy_v2.life_liquidity.quote_actions import QuoteActionPlanner, QuotePlanningSnapshot
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, RiskLimits, SpotIntent
from hummingbot.strategy_v2.life_liquidity.spot_quotes import QuoteCosts
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL
from hummingbot.strategy_v2.models.base import RunnableStatus


def _setup(tmp_path, *, enabled=True, ttl=2, max_actions=2, gross="30"):
    clock = FakeClock()
    sessions = manager(tmp_path, clock)
    begin(sessions)
    current = sessions.current_session
    config = LifeLiquidityConfig.model_construct(id="life")
    quotes = QuotesConfig(spreads_bps=(Decimal("30"),), sizes_base=(Decimal("1"),))
    config = config.model_copy(update={"strategy": config.strategy.model_copy(update={"quotes": quotes})})
    controller = LifeLiquidityController(config, MagicMock(), MagicMock())
    controller._order_safety_manager = sessions
    controller._runner_orchestrator = SimpleNamespace(active_executors={"life": []})
    controller.allow_create_executor_actions = lambda: enabled
    wal = IntentWAL(tmp_path / "intents.json")
    ledger = ReservationLedger(
        life_balance=Decimal("10"), usdt_balance=Decimal("10"),
        limits=RiskLimits(Decimal("0"), Decimal("20"), Decimal(gross), Decimal("20")),
        path=tmp_path / "reservations.json")
    controller._order_safety_wal = wal
    controller._order_safety_reservations = ledger
    controller._protected_spot_sender = SimpleNamespace(
        manager=sessions, gateway=SimpleNamespace(wal=wal), reservations=ledger,
        submit=lambda *_args, **_kwargs: "wire")
    snapshot = QuotePlanningSnapshot(
        session_id=current.session_id, epoch=current.epoch,
        config_version=current.config_version, observed_monotonic=99,
        expires_monotonic=99 + ttl, reference_ready=True,
        all_gates_ready=True, market_reference_ready=True,
        qualified_reference_usdt=Decimal("1"), qualified_exit_value_usdt=Decimal("1"),
        best_bid_usdt=Decimal("0.98"), best_ask_usdt=Decimal("1.02"),
        rules=InstrumentRules(Decimal("0.01"), Decimal("0.1"), Decimal("0.1")),
        costs=QuoteCosts(Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"),
                         Decimal("0"), Decimal("0")),
        policy=EconomicPolicy("profit_mm", Decimal("0")))
    planner = QuoteActionPlanner(controller, wal=wal, reservations=ledger,
                                 snapshot=lambda: snapshot, monotonic_clock=lambda: 100,
                                 intent_id_factory=iter((f"intent-{index}" for index in range(10))).__next__,
                                 max_actions_per_tick=max_actions)
    return controller, planner, wal, ledger, snapshot


def test_controller_emits_bounded_post_only_actions_once_per_slot(tmp_path):
    controller, planner, _, _, _ = _setup(tmp_path)
    assert controller.determine_executor_actions() == []
    controller.install_quote_action_planner(planner)
    actions = controller.determine_executor_actions()
    assert len(actions) == 2
    configs = [action.executor_config for action in actions]
    assert {(config.side, config.level_id, config.price, config.amount) for config in configs} == {
        (TradeType.BUY, "0", Decimal("0.99"), Decimal("1")),
        (TradeType.SELL, "0", Decimal("1.01"), Decimal("1"))}
    assert all(action.controller_id == "life" and config.execution_strategy == ExecutionStrategy.LIMIT_MAKER
               and config.trading_pair == "LIFE-USDT" and config.connector_name == "okx"
               for action, config in zip(actions, configs))
    assert controller.determine_executor_actions() == []


def test_disabled_or_stale_snapshot_never_creates_actions(tmp_path):
    controller, planner, wal, _, _ = _setup(tmp_path, enabled=False)
    controller.install_quote_action_planner(planner)
    assert controller.determine_executor_actions() == []
    assert wal.all_records() == ()
    controller.allow_create_executor_actions = lambda: True
    planner.monotonic_clock = lambda: 102
    assert controller.determine_executor_actions() == []
    assert wal.all_records() == ()


def test_snapshot_session_mismatch_and_missing_runner_scope_fail_closed(tmp_path):
    controller, planner, _, _, snapshot = _setup(tmp_path)
    controller.install_quote_action_planner(planner)
    planner.snapshot = lambda: snapshot.__class__(**{**snapshot.__dict__, "epoch": 2})
    assert controller.determine_executor_actions() == []
    planner.snapshot = lambda: snapshot
    controller._runner_orchestrator = None
    assert controller.determine_executor_actions() == []


def test_unaccepted_action_is_not_reproposed_on_each_tick(tmp_path):
    controller, planner, wal, _, _ = _setup(tmp_path, max_actions=1)
    controller.install_quote_action_planner(planner)
    first = controller.determine_executor_actions()
    second = controller.determine_executor_actions()
    third = controller.determine_executor_actions()
    assert len(first) == 1
    assert len(second) == 1
    assert third == []
    assert first[0].executor_config.id != second[0].executor_config.id
    assert wal.all_records() == ()


def test_terminal_wal_and_reservation_allow_fresh_replacement(tmp_path):
    controller, planner, wal, ledger, _ = _setup(tmp_path, max_actions=1)
    controller.install_quote_action_planner(planner)
    first = controller.determine_executor_actions()[0].executor_config
    wal.begin(first.id, client_order_id="wire-1", session_id=planner.manager.current_session.session_id,
              epoch=1, reservation_id=first.id, slot_market="LIFE-USDT", slot_side="BUY", slot_level=0)
    assert ledger.reserve(SpotIntent(first.id, "BUY", first.amount, first.price,
                                     planner.manager.current_session.session_id, 1),
                          reference_price=Decimal("1")).allowed
    wal.arm_send(first.id, client_order_id="wire-1", session_id=planner.manager.current_session.session_id,
                 epoch=1, reservation_id=first.id)
    assert controller.determine_executor_actions()
    assert controller.determine_executor_actions() == []
    wal.mark_terminal(first.id, "exchange-1")
    assert controller.determine_executor_actions() == []
    ledger.confirm_terminal(first.id, cumulative_filled=Decimal("0"),
                            fills_reconciled=True, exchange_state="CANCELED")
    assert len(controller.determine_executor_actions()) == 1


def test_controller_rechecks_exact_quote_before_sender(tmp_path):
    controller, planner, _, _, snapshot = _setup(tmp_path)
    controller.install_quote_action_planner(planner)
    config = controller.determine_executor_actions()[0].executor_config
    assert controller.submit_executor_spot_order(
        config, amount=config.amount, price=config.price,
        order_type=OrderType.LIMIT_MAKER) == "wire"
    with pytest.raises(PermissionError, match="QUOTE_ACTION_NOT_AUTHORIZED"):
        controller.submit_executor_spot_order(
            config, amount=config.amount, price=config.price + Decimal("0.01"),
            order_type=OrderType.LIMIT_MAKER)
    planner.snapshot = lambda: replace(snapshot, best_ask_usdt=Decimal("0.99"))
    with pytest.raises(PermissionError, match="QUOTE_ACTION_NOT_AUTHORIZED"):
        controller.submit_executor_spot_order(
            config, amount=config.amount, price=config.price,
            order_type=OrderType.LIMIT_MAKER)


def test_invalid_snapshot_type_fails_closed(tmp_path):
    controller, planner, _, _, snapshot = _setup(tmp_path)
    controller.install_quote_action_planner(planner)
    planner.snapshot = lambda: replace(snapshot, observed_monotonic="not-a-clock")
    assert controller.determine_executor_actions() == []


def test_untracked_active_executor_blocks_new_quote_actions(tmp_path):
    controller, planner, _, _, _ = _setup(tmp_path)
    controller.install_quote_action_planner(planner)
    existing = controller.determine_executor_actions()[0].executor_config
    controller._runner_orchestrator.active_executors["life"] = [
        SimpleNamespace(config=existing.model_copy(update={"id": "foreign"}),
                        status=RunnableStatus.RUNNING)]
    assert controller.determine_executor_actions() == []
    assert planner.reason_code == "QUOTE_ACTION_RUNNER_SCOPE_UNAVAILABLE"


def test_duplicate_intent_ids_abort_whole_action_batch(tmp_path):
    controller, planner, wal, _, _ = _setup(tmp_path)
    controller.install_quote_action_planner(planner)
    planner.intent_id_factory = iter(("duplicate", "duplicate")).__next__
    assert controller.determine_executor_actions() == []
    assert planner.reason_code == "QUOTE_ACTION_ID_INVALID"
    assert wal.all_records() == ()
    assert planner._proposed == {}


def test_action_replay_after_wal_send_is_rejected_before_sender(tmp_path):
    controller, planner, wal, ledger, _ = _setup(tmp_path)
    controller.install_quote_action_planner(planner)
    config = controller.determine_executor_actions()[0].executor_config
    current = planner.manager.current_session
    wal.begin(config.id, client_order_id="wire-1", session_id=current.session_id,
              epoch=current.epoch, reservation_id=config.id,
              slot_market="LIFE-USDT", slot_side=config.side.name, slot_level=0)
    assert ledger.reserve(SpotIntent(config.id, "BUY", config.amount, config.price,
                                     current.session_id, current.epoch),
                          reference_price=Decimal("1")).allowed
    wal.arm_send(config.id, client_order_id="wire-1", session_id=current.session_id,
                 epoch=current.epoch, reservation_id=config.id)
    with pytest.raises(PermissionError, match="QUOTE_ACTION_NOT_AUTHORIZED"):
        controller.submit_executor_spot_order(
            config, amount=config.amount, price=config.price,
            order_type=OrderType.LIMIT_MAKER)


def test_second_planned_side_rechecks_only_its_own_incremental_risk(tmp_path):
    controller, planner, wal, ledger, _ = _setup(tmp_path, gross="12")
    controller.install_quote_action_planner(planner)
    actions = controller.determine_executor_actions()
    assert len(actions) == 2
    buy, sell = (action.executor_config for action in actions)
    current = planner.manager.current_session
    wal.begin(buy.id, client_order_id="wire-1", session_id=current.session_id,
              epoch=current.epoch, reservation_id=buy.id,
              slot_market="LIFE-USDT", slot_side="BUY", slot_level=0)
    assert ledger.reserve(SpotIntent(buy.id, "BUY", buy.amount, buy.price,
                                     current.session_id, current.epoch),
                          reference_price=Decimal("1")).allowed
    wal.arm_send(buy.id, client_order_id="wire-1", session_id=current.session_id,
                 epoch=current.epoch, reservation_id=buy.id)
    assert planner.authorizes_config(sell)
