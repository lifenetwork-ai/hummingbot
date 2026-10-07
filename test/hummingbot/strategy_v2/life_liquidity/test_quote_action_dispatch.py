"""Durable quote-action claims and runner rejection evidence (P5.6)."""

from test.hummingbot.strategy_v2.life_liquidity.test_quote_actions import _setup
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.life_liquidity.action_journal import QuoteActionJournal
from hummingbot.strategy_v2.life_liquidity.quote_actions import QuoteActionPlanner
from hummingbot.strategy_v2.models.base import RunnableStatus


def _restarted_planner(controller, planner):
    return QuoteActionPlanner(
        controller, wal=planner.wal, reservations=planner.reservations,
        snapshot=planner.snapshot, monotonic_clock=planner.monotonic_clock,
        intent_id_factory=iter((f"restart-{index}" for index in range(10))).__next__,
        max_actions_per_tick=planner.max_actions_per_tick)


def test_claim_survives_restart_without_wal_and_blocks_reproposal(tmp_path):
    controller, planner, wal, _, _ = _setup(tmp_path)
    controller.install_quote_action_planner(planner)
    first = planner.propose()
    assert len(first) == 2
    assert wal.all_records() == ()

    restarted = _restarted_planner(controller, planner)
    controller._quote_action_planner = restarted
    assert restarted.propose() == []
    assert {action.executor_config.id for action in first} == {
        item.intent_id for item in QuoteActionJournal(planner.action_journal.path).active_records()}


def test_runner_rejection_is_durable_and_old_action_cannot_be_dispatched(tmp_path):
    controller, planner, _, _, _ = _setup(tmp_path, max_actions=1)
    controller.install_quote_action_planner(planner)
    first = planner.propose()[0]
    controller.allow_create_executor_actions = lambda: False
    runner = SimpleNamespace(controllers={"life": controller}, logger=lambda: MagicMock())

    assert StrategyV2Base._filter_authorized_actions(runner, [first]) == []
    assert QuoteActionJournal(planner.action_journal.path).get(first.executor_config.id).state == "REJECTED"
    controller.allow_create_executor_actions = lambda: True
    assert not planner.authorizes_config(first.executor_config)
    assert StrategyV2Base._filter_authorized_actions(runner, [first]) == []
    replacement = planner.propose()
    assert replacement and replacement[0].executor_config.id != first.executor_config.id


def test_ambiguous_dispatch_keeps_durable_claim_across_restart(tmp_path):
    controller, planner, _, _, _ = _setup(tmp_path)
    controller.install_quote_action_planner(planner)
    action = planner.propose()[0]
    orchestrator = MagicMock()
    orchestrator.execute_action.side_effect = RuntimeError("dispatch outcome unknown")
    runner = SimpleNamespace(
        controllers={"life": controller}, market_data_provider=SimpleNamespace(ready=True),
        _is_stop_triggered=False, executor_orchestrator=orchestrator,
        update_executors_info=MagicMock(), update_controllers_configs=MagicMock(),
        determine_executor_actions=MagicMock(return_value=[action]),
        logger=lambda: MagicMock())
    runner._filter_authorized_actions = lambda actions: StrategyV2Base._filter_authorized_actions(runner, actions)

    with pytest.raises(RuntimeError, match="dispatch outcome unknown"):
        StrategyV2Base.on_tick(runner)
    restarted = _restarted_planner(controller, planner)
    controller._quote_action_planner = restarted
    assert restarted.propose() == []
    assert QuoteActionJournal(planner.action_journal.path).get(action.executor_config.id).state == "PROPOSED"


def test_runner_records_dispatch_only_after_executor_is_visible(tmp_path):
    controller, planner, _, _, _ = _setup(tmp_path, max_actions=1)
    controller.install_quote_action_planner(planner)
    action = planner.propose()[0]
    orchestrator = MagicMock()
    orchestrator.execute_action.side_effect = lambda sent: controller._runner_orchestrator.active_executors[
        "life"].append(SimpleNamespace(config=sent.executor_config, status=RunnableStatus.RUNNING))
    runner = SimpleNamespace(
        controllers={"life": controller}, market_data_provider=SimpleNamespace(ready=True),
        _is_stop_triggered=False, executor_orchestrator=orchestrator,
        update_executors_info=MagicMock(), update_controllers_configs=MagicMock(),
        determine_executor_actions=MagicMock(return_value=[action]),
        logger=lambda: MagicMock())
    runner._filter_authorized_actions = lambda actions: StrategyV2Base._filter_authorized_actions(runner, actions)

    StrategyV2Base.on_tick(runner)

    assert QuoteActionJournal(planner.action_journal.path).get(action.executor_config.id).state == "DISPATCHED"
    assert _restarted_planner(controller, planner).propose() == []


def test_runner_rejection_cannot_release_claim_with_wal_or_active_executor(tmp_path):
    controller, planner, wal, _, _ = _setup(tmp_path, max_actions=1)
    controller.install_quote_action_planner(planner)
    action = planner.propose()[0]
    config = action.executor_config
    controller._runner_orchestrator.active_executors["life"].append(
        SimpleNamespace(config=config, status=RunnableStatus.RUNNING))
    assert not planner.on_runner_action_rejected(action)
    controller._runner_orchestrator.active_executors["life"].clear()
    current = planner.manager.current_session
    wal.begin(config.id, client_order_id="wire-1", session_id=current.session_id,
              epoch=current.epoch, reservation_id=config.id,
              slot_market="LIFE-USDT", slot_side=config.side.name, slot_level=0)
    assert not planner.on_runner_action_rejected(action)
    assert QuoteActionJournal(planner.action_journal.path).get(config.id).state == "PROPOSED"


def test_journal_write_failure_prevents_action_return(tmp_path, monkeypatch):
    controller, planner, _, _, _ = _setup(tmp_path, max_actions=1)
    controller.install_quote_action_planner(planner)
    monkeypatch.setattr(planner.action_journal, "_save", MagicMock(side_effect=OSError("disk full")))

    assert planner.propose() == []
    assert planner.reason_code == "QUOTE_ACTION_JOURNAL_UNAVAILABLE"
    assert planner._issued == {}


def test_ambiguous_fsync_after_claim_stays_blocked_on_restart(tmp_path, monkeypatch):
    controller, planner, _, _, _ = _setup(tmp_path)
    controller.install_quote_action_planner(planner)
    original_save = planner.action_journal._save

    def save_then_fail(records):
        original_save(records)
        raise OSError("directory fsync outcome unknown")

    monkeypatch.setattr(planner.action_journal, "_save", save_then_fail)
    assert planner.propose() == []
    assert planner.reason_code == "QUOTE_ACTION_JOURNAL_UNAVAILABLE"
    assert planner._issued == {}
    assert len(QuoteActionJournal(planner.action_journal.path).active_records()) == 2
    assert _restarted_planner(controller, planner).propose() == []


def test_stale_journal_instance_cannot_overwrite_new_claim(tmp_path):
    controller, planner, _, _, _ = _setup(tmp_path, max_actions=1)
    stale = QuoteActionJournal(planner.action_journal.path)
    controller.install_quote_action_planner(planner)
    action = planner.propose()[0]

    with pytest.raises(ValueError, match="ACTION_JOURNAL_UNCERTAIN"):
        stale.claim_batch([planner.action_journal.get(action.executor_config.id)])


def test_stale_journal_view_cannot_authorize_action(tmp_path):
    controller, planner, _, _, _ = _setup(tmp_path, max_actions=1)
    controller.install_quote_action_planner(planner)
    action = planner.propose()[0]
    fresh = QuoteActionJournal(planner.action_journal.path)
    fresh.transition(action.executor_config.id, expected="PROPOSED", state="REJECTED")

    assert not planner.authorizes_config(action.executor_config)
    assert planner.propose() == []
    assert planner.reason_code == "QUOTE_ACTION_JOURNAL_UNAVAILABLE"


def test_corrupt_durable_action_journal_blocks_planner_startup(tmp_path):
    controller, planner, _, _, _ = _setup(tmp_path)
    planner.action_journal.path.write_text('{"schema_version": 1, "records": {"broken": {}}}')

    with pytest.raises(ValueError, match="ACTION_JOURNAL_INVALID"):
        _restarted_planner(controller, planner)
