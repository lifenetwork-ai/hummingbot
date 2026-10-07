"""Cold-start quote action provenance is separate from safety cancellation."""

import json
from pathlib import Path
from test.hummingbot.strategy_v2.life_liquidity.test_controller_order_safety import (
    _limits,
    _recovery_config,
    _seed_recovery,
)
from test.hummingbot.strategy_v2.life_liquidity.test_order_gateway import FakeOkx
from test.hummingbot.strategy_v2.life_liquidity.test_quote_actions import _setup
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from controllers.generic.life_liquidity import LifeLiquidityController
from hummingbot.strategy_v2.life_liquidity.action_journal import QuoteActionJournal, QuoteActionRecord
from hummingbot.strategy_v2.life_liquidity.quote_actions import QuoteActionPlanner
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL


def _restore(directory: Path, *, required: bool = True):
    provider = MagicMock()
    provider.get_connector_with_fallback.return_value = FakeOkx()
    config = _recovery_config(directory).model_copy(update={
        "require_quote_action_journal": required})
    controller = LifeLiquidityController(config, provider, MagicMock())
    controller._restore_order_safety()
    return controller


def test_missing_required_action_journal_blocks_quotes_but_restores_safety(tmp_path):
    directory = tmp_path / "recovery"
    _seed_recovery(directory)
    controller = _restore(directory)
    try:
        assert controller._order_safety_manager is not None
        assert controller._quote_action_recovery_ready is False
        assert controller.quote_action_recovery_reason_code == "QUOTE_ACTION_JOURNAL_MISSING"
        assert "QUOTE_ACTION_JOURNAL_MISSING" in controller.to_format_status()[0]
    finally:
        controller.stop()


@pytest.mark.parametrize("contents", ["{broken", '{"schema_version": 1, "records": {"bad": {}}}'])
def test_corrupt_action_journal_blocks_quotes_without_blocking_safety(tmp_path, contents):
    directory = tmp_path / "recovery"
    _seed_recovery(directory)
    (directory / "quote_actions.json").write_text(contents)
    controller = _restore(directory)
    try:
        assert controller._order_safety_manager is not None
        assert controller._quote_action_recovery_ready is False
        assert controller.quote_action_recovery_reason_code == "QUOTE_ACTION_JOURNAL_INVALID"
    finally:
        controller.stop()


@pytest.mark.asyncio
async def test_corrupt_action_journal_does_not_block_wal_cancellation(tmp_path):
    directory = tmp_path / "recovery"
    _seed_recovery(directory)
    (directory / "quote_actions.json").write_text("{broken")
    controller = _restore(directory)
    try:
        connector = controller._order_safety_gateway.connector
        controller.on_safety_tick(10)
        await controller.order_safety_task
        assert connector.cancels == [("LIFE-USDT", "wire-1")]
        assert not controller._quote_action_recovery_ready
    finally:
        controller.stop()


def test_empty_journal_with_matching_uid_is_recovered(tmp_path):
    directory = tmp_path / "recovery"
    _seed_recovery(directory)
    QuoteActionJournal(directory / "quote_actions.json", account_uid="12345").initialize_empty()
    controller = _restore(directory)
    try:
        assert controller._quote_action_recovery_ready is True
        assert controller.quote_action_recovery_reason_code == "QUOTE_ACTION_JOURNAL_VERIFIED"
    finally:
        controller.stop()


def test_action_journal_account_mismatch_blocks_quotes(tmp_path):
    directory = tmp_path / "recovery"
    _seed_recovery(directory)
    QuoteActionJournal(directory / "quote_actions.json", account_uid="99999").initialize_empty()
    controller = _restore(directory)
    try:
        assert controller._order_safety_manager is not None
        assert controller._quote_action_recovery_ready is False
        assert controller.quote_action_recovery_reason_code == "QUOTE_ACTION_JOURNAL_INVALID"
    finally:
        controller.stop()


def test_deleting_verified_journal_does_not_reset_its_state(tmp_path):
    path = tmp_path / "quote_actions.json"
    journal = QuoteActionJournal(path, account_uid="12345")
    journal.initialize_empty()
    path.unlink()
    with pytest.raises(ValueError, match="ACTION_JOURNAL_UNCERTAIN"):
        journal.verified_records()
    with pytest.raises(ValueError, match="ACTION_JOURNAL_UNCERTAIN"):
        journal.claim_batch([QuoteActionRecord(
            intent_id="i1", controller_id="life", session_id="s1", epoch=1,
            config_version=1, market="LIFE-USDT", side="BUY", level=0)])


@pytest.mark.parametrize("old_session", [False, True])
def test_pre_wal_claim_stays_unresolved_after_restart(tmp_path, old_session):
    directory = tmp_path / "recovery"
    _seed_recovery(directory)
    old = IntentWAL(directory / "intents.json").get("i1")
    journal = QuoteActionJournal(directory / "quote_actions.json", account_uid="12345")
    journal.claim_batch([QuoteActionRecord(
        intent_id="claim-1", controller_id="life",
        session_id="old-session" if old_session else old.session_id,
        epoch=old.epoch, config_version=1, market="LIFE-USDT", side="SELL", level=0)])
    controller = _restore(directory)
    try:
        assert controller._order_safety_manager is not None
        assert controller._quote_action_recovery_ready is False
        assert controller.quote_action_recovery_reason_code == "QUOTE_ACTION_DISPATCH_UNRESOLVED"
        assert QuoteActionJournal(journal.path, account_uid="12345").get("claim-1").state == "PROPOSED"
    finally:
        controller.stop()


def test_matching_wal_claim_is_verified_without_freeing_slot(tmp_path):
    directory = tmp_path / "recovery"
    _seed_recovery(directory)
    wal_path = directory / "intents.json"
    data = json.loads(wal_path.read_text())
    data["records"]["i1"].update({
        "slot_market": "LIFE-USDT", "slot_side": "BUY", "slot_level": 0})
    wal_path.write_text(json.dumps(data))
    old = IntentWAL(wal_path).get("i1")
    journal = QuoteActionJournal(directory / "quote_actions.json", account_uid="12345")
    journal.claim_batch([QuoteActionRecord(
        intent_id="i1", controller_id="life", session_id=old.session_id,
        epoch=old.epoch, config_version=1, market="LIFE-USDT", side="BUY", level=0,
        state="PROPOSED")])
    controller = _restore(directory)
    try:
        assert controller._quote_action_recovery_ready
        assert QuoteActionJournal(journal.path, account_uid="12345").get("i1").state == "PROPOSED"
    finally:
        controller.stop()


def test_terminal_wal_and_reservation_retire_claim_during_recovery(tmp_path):
    directory = tmp_path / "recovery"
    _seed_recovery(directory)
    wal_path = directory / "intents.json"
    data = json.loads(wal_path.read_text())
    data["records"]["i1"].update({
        "slot_market": "LIFE-USDT", "slot_side": "BUY", "slot_level": 0})
    wal_path.write_text(json.dumps(data))
    wal = IntentWAL(wal_path)
    old = wal.get("i1")
    wal.mark_terminal("i1", "exchange-1")
    reservations = ReservationLedger.restore(directory / "reservations.json", limits=_limits())
    reservations.confirm_terminal("i1", cumulative_filled=0,
                                  fills_reconciled=True, exchange_state="CANCELED")
    journal = QuoteActionJournal(directory / "quote_actions.json", account_uid="12345")
    journal.claim_batch([QuoteActionRecord(
        intent_id="i1", controller_id="life", session_id=old.session_id,
        epoch=old.epoch, config_version=1, market="LIFE-USDT", side="BUY", level=0)])

    controller = _restore(directory)
    try:
        assert controller._quote_action_recovery_ready
        assert QuoteActionJournal(journal.path, account_uid="12345").get("i1").state == "RECONCILED"
    finally:
        controller.stop()


def test_recovery_journal_cannot_claim_a_different_wal_slot(tmp_path):
    directory = tmp_path / "recovery"
    _seed_recovery(directory)
    old = IntentWAL(directory / "intents.json").get("i1")
    journal = QuoteActionJournal(directory / "quote_actions.json", account_uid="12345")
    journal.claim_batch([QuoteActionRecord(
        intent_id="i1", controller_id="life", session_id=old.session_id,
        epoch=old.epoch, config_version=1, market="LIFE-USDT", side="BUY", level=0)])
    controller = _restore(directory)
    try:
        assert controller._quote_action_recovery_ready is False
        assert controller.quote_action_recovery_reason_code == "QUOTE_ACTION_JOURNALS_DISAGREE"
    finally:
        controller.stop()


def test_recovery_enabled_planner_requires_verified_action_journal(tmp_path):
    controller, planner, _, _, _ = _setup(tmp_path)
    controller.config = controller.config.model_copy(update={"recovery_state_dir": str(tmp_path)})
    with pytest.raises(ValueError, match="QUOTE_ACTION_RECOVERY_UNVERIFIED"):
        controller.install_quote_action_planner(planner)


def test_revoked_recovery_state_prevents_new_action_proposals(tmp_path):
    controller, planner, _, _, _ = _setup(tmp_path)
    controller.install_quote_action_planner(planner)
    controller.config = controller.config.model_copy(update={"recovery_state_dir": str(tmp_path)})
    controller._quote_action_recovery_ready = False
    assert controller.determine_executor_actions() == []


def test_planner_install_rejects_journal_changed_after_recovery(tmp_path):
    directory = tmp_path / "recovery"
    _seed_recovery(directory)
    path = directory / "quote_actions.json"
    QuoteActionJournal(path, account_uid="12345").initialize_empty()
    controller = _restore(directory)
    try:
        assert controller._quote_action_recovery_ready
        old = IntentWAL(directory / "intents.json").get("i1")
        QuoteActionJournal(path, account_uid="12345").claim_batch([QuoteActionRecord(
            intent_id="late", controller_id="life", session_id=old.session_id,
            epoch=old.epoch, config_version=1, market="LIFE-USDT", side="SELL", level=0)])
        wal = controller._order_safety_wal
        reservations = controller._order_safety_reservations
        controller._protected_spot_sender = SimpleNamespace(
            manager=controller._order_safety_manager, gateway=SimpleNamespace(wal=wal),
            reservations=reservations)
        planner = QuoteActionPlanner(
            controller, wal=wal, reservations=reservations,
            snapshot=lambda: None, monotonic_clock=lambda: 0,
            intent_id_factory=lambda: "new", max_actions_per_tick=1)
        with pytest.raises(ValueError, match="QUOTE_ACTION_RECOVERY_UNVERIFIED"):
            controller.install_quote_action_planner(planner)
    finally:
        controller.stop()
