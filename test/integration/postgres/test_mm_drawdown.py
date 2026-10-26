import asyncio
import json
import random
from decimal import Decimal
from test.hummingbot.client.config.test_mm_profiles import CATALOGUE
from test.hummingbot.strategy_v2.test_mm_drawdown import runner
from test.integration.postgres.test_ownership import owned as _owned
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError

from hummingbot.client.config.mm_profiles import MMProfilesConfig
from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.core.mm_drawdown import checkpoint_drawdown_status
from hummingbot.core.mm_session import MMSession, MMSessionRequest
from hummingbot.core.order_submission_guard import SubmissionBlocked
from hummingbot.model.bot_state_store import ControlConflict
from hummingbot.model.position import Position
from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator

owned = _owned


def arm(context, *, spot="0", perpetual="0", bind=True):
    original = asyncio.run(MMProfilesConfig.load(CATALOGUE))
    data = original.model_dump(mode="json")
    data["max_global_drawdown_quote"] = "10"
    catalogue = MMProfilesConfig.model_validate(data)
    session = MMSession.create(catalogue, MMSessionRequest(market_mode="both", profile_id="sideways", duration_seconds=60),
                               rng=random.Random(1))
    with context.engine.begin() as connection:
        connection.execute(text("UPDATE bot_state SET config_file_path='life_mm_profiles.yaml', config_type='mm-session', mm_session=CAST(:value AS jsonb)"),
                           {"value": json.dumps(session.model_dump(mode="json"))})
    context.owner.config_file_path = context.recorder._config_file_path = "life_mm_profiles.yaml"
    strategy = runner(spot=spot, perpetual=perpetual)
    strategy.mm_drawdown_checkpoint_callback = None
    strategy.current_timestamp = 1000
    if bind:
        assert context.owner.bind_mm_drawdown(strategy)
    return strategy, catalogue, session


def checkpoint(store):
    return store.read()["recovery_state"]["strategy_checkpoints"]["life_mm_profiles.yaml"]


def clean_stop(owner, strategy):
    core = SimpleNamespace(strategy=strategy, KILL_TIMEOUT=3,
                           trade_fill_db=SimpleNamespace(assert_persistence_healthy=lambda: None))

    async def shutdown(**kwargs):
        assert kwargs["keep_positions"]
        await core.after_order_cancellation()
        return True
    core.shutdown = AsyncMock(side_effect=shutdown)
    assert asyncio.run(owner.shutdown_core(core, timeout=3))


def test_observed_peaks_are_durable_monotonic_and_preserve_other_recovery_state(owned):
    strategy, _, _ = arm(owned, spot="15", perpetual="25")
    owned.owner._mutate_recovery(lambda state: state.update(funding_sync={"covered_until_ms": 1000}))
    strategy.control_max_drawdown()
    saved = checkpoint(owned.store)
    assert saved["max_global_pnl"] == "40"
    assert saved["max_pnl_by_controller"] == {"life_spot": "15", "life_perpetual": "25"}
    stale = {**saved, "max_global_pnl": "0", "max_pnl_by_controller": {key: "0" for key in saved["max_pnl_by_controller"]}}
    owned.owner.checkpoint_mm_drawdown(stale)
    assert checkpoint(owned.store)["max_global_pnl"] == "40"
    assert checkpoint(owned.store)["max_pnl_by_controller"] == saved["max_pnl_by_controller"]
    assert owned.store.read()["recovery_state"]["funding_sync"] == {"covered_until_ms": 1000}


def test_clean_handover_and_market_switch_restore_peaks_then_persist_breach(owned):
    strategy, catalogue, session = arm(owned, spot="30", perpetual="70")
    strategy.control_max_drawdown()
    request = MMSessionRequest(market_mode="spot", profile_id="sideways", duration_seconds=60)
    changed = owned.store.request_mm_session(action="switch", expected_version=1, current_session_id=session.id,
                                             catalogue=catalogue, request=request)
    assert changed["session_id"] != session.id
    clean_stop(owned.owner, strategy)
    owned.owner.release()
    successor = BotOwnership(owned.engine, "hummingbot")
    assert successor.try_acquire()
    try:
        successor.prepare(2, "life_mm_profiles.yaml")
        current = runner(spot="0", perpetual="89")
        current.mm_drawdown_checkpoint_callback = None
        assert successor.bind_mm_drawdown(current)
        assert current.max_global_pnl == Decimal("100")
        with patch("scripts.v2_with_controllers.HummingbotApplication.main_application") as app:
            app.return_value.stop.side_effect = lambda **kwargs: successor.require_recovery(kwargs["mm_drawdown_reason"])
            current.control_max_drawdown()
        assert owned.store.read()["recovery_state"]["halt"]
        assert checkpoint(owned.store)["_is_stop_triggered"]
        assert checkpoint(owned.store)["mm_drawdown_breach"]["reason_code"] == "drawdown_global_limit"
        with pytest.raises(ControlConflict, match="HALT"):
            owned.store.request_mm_session(action="switch", expected_version=2, current_session_id=changed["session_id"],
                                           catalogue=catalogue, request=request)
    finally:
        successor.release()


def test_armed_policy_cannot_be_disabled_renamed_or_bypassed_by_legacy_start(owned):
    strategy, catalogue, session = arm(owned)
    strategy.control_max_drawdown()
    original = checkpoint(owned.store)
    for field, value in (("max_global_drawdown_quote", None), ("max_global_drawdown_quote", "20"), ("id", "renamed")):
        data = catalogue.model_dump(mode="json")
        if field == "id":
            data["markets"]["spot"]["id"] = value
        else:
            data[field] = value
        with pytest.raises(ControlConflict, match="policy/scope"):
            owned.store.request_mm_session(action="switch", expected_version=1, current_session_id=session.id,
                                           catalogue=MMProfilesConfig.model_validate(data),
                                           request=MMSessionRequest(market_mode="spot", profile_id="sideways", duration_seconds=60))
    for desired in (None, "running"):
        with pytest.raises(ControlConflict, match="admin MM"):
            owned.store.control(expected_version=1, desired_state=desired, config_file_path="legacy.yml", config_type="controller")
    owned.store.control(expected_version=1, desired_state="stopped")
    assert checkpoint(owned.store) == original


def test_random_generation_reuses_bot_peak_and_policy_instead_of_starting_a_new_baseline(owned):
    strategy, catalogue, session = arm(owned, spot="30", perpetual="70")
    strategy.control_max_drawdown()
    changed = owned.store.request_mm_session(
        action="switch", expected_version=1, current_session_id=session.id, catalogue=catalogue,
        request=MMSessionRequest(market_mode="spot", selection="random", profile_ids=["sideways"],
                                 step_duration_seconds=15, duration_seconds=60))
    clean_stop(owned.owner, strategy)
    owned.owner.prepare(2, "life_mm_profiles.yaml")

    def update(generation, change):
        return owned.store.update_mm_session(owner_uuid=owned.owner.owner_uuid, owner_generation=owned.owner.generation,
                                             version=2, session_id=changed["session_id"], generation=generation, change=change)

    active = update(0, lambda value, now: value.ready(now=1000, anchors={"life_spot": dict(life=".1", sources={})}))
    following = update(0, lambda value, now: value.advance(now=1016, rng=random.Random(1)))
    assert following.generation == 1 and following.expires_at == active.expires_at
    current = runner(spot="9", perpetual="90")
    current.mm_drawdown_checkpoint_callback = None
    assert owned.owner.bind_mm_drawdown(current)
    current.control_max_drawdown()
    assert current.mm_drawdown_ready() and current.max_global_pnl == Decimal("100")
    assert current.mm_drawdown_status()["global_drawdown_quote"] == Decimal("1")
    assert checkpoint(owned.store)["mm_drawdown_policy"] == catalogue.drawdown_policy()


def test_superseding_stop_wins_the_initial_policy_binding_transaction(owned, monkeypatch):
    strategy, _, _ = arm(owned, spot="100", bind=False)
    real = owned.owner.checkpoint_mm_drawdown

    def stopped_before_lock(value, **kwargs):
        owned.store.control(expected_version=1, desired_state="stopped")
        return real(value, **kwargs)
    monkeypatch.setattr(owned.owner, "checkpoint_mm_drawdown", stopped_before_lock)
    assert not owned.owner.bind_mm_drawdown(strategy)
    strategy.control_max_drawdown()
    assert not strategy.mm_drawdown_ready()
    assert strategy.max_global_pnl == 0 and not strategy._is_stop_triggered
    assert not (owned.store.read()["recovery_state"].get("strategy_checkpoints") or {}).get("life_mm_profiles.yaml", {}).get("mm_drawdown_policy")
    clean_stop(owned.owner, strategy)
    assert checkpoint(owned.store).get("mm_drawdown_policy") is None
    assert not owned.store.read()["recovery_state"].get("halt")


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1"])
def test_invalid_checkpoint_is_not_replaced_with_a_fresh_zero_peak(owned, value):
    arm(owned)

    def corrupt(state):
        state["strategy_checkpoints"]["life_mm_profiles.yaml"]["max_global_pnl"] = value
    owned.owner._mutate_recovery(corrupt)
    with pytest.raises(SubmissionBlocked, match="checkpoint coverage"):
        owned.owner.bind_mm_drawdown(runner())
    assert owned.store.read()["recovery_state"]["halt"]
    status = checkpoint_drawdown_status(checkpoint(owned.store))
    assert status["global_peak_quote"] is None and status["reason_code"] == "drawdown_checkpoint_invalid"


def test_unclean_handover_retains_peak_and_existing_halt(owned):
    strategy, _, _ = arm(owned, spot="20")
    strategy.control_max_drawdown()
    owned.owner.release()  # Intentionally no clean runtime marker/checkpoint shutdown.
    successor = BotOwnership(owned.engine, "hummingbot")
    assert successor.try_acquire()
    try:
        assert owned.store.read()["recovery_state"]["halt"]
        assert checkpoint(owned.store)["max_global_pnl"] == "20"
    finally:
        successor.release()


def test_actual_pg_position_restore_and_report_include_disabled_market_net_fees(owned):
    strategy, _, _ = arm(owned)
    with owned.engine.begin() as connection:
        connection.execute(Position.__table__.insert().values(id="retained", controller_id="life_perpetual", connector_name="okx_perpetual",
                                                              trading_pair="LIFE-USDT", side="BUY", timestamp=1000000, amount=Decimal("2"), breakeven_price=Decimal("100"),
                                                              realized_pnl_quote=Decimal("3"), unrealized_pnl_quote=Decimal("0"), cum_fees_quote=Decimal("1"), volume_traded_quote=Decimal("200")))
    strategy.markets = {"okx": {"LIFE-USDT"}}
    strategy.market_data_provider.get_price_by_type = lambda *args: Decimal("110")
    strategy.market_data_provider.get_order_book = lambda name, pair: SimpleNamespace(snapshot_uid=1000000 if name == "okx_perpetual" else 1000, last_diff_uid=0)
    strategy.executor_orchestrator = ExecutorOrchestrator(strategy)
    strategy.controller_reports = strategy.executor_orchestrator.get_all_reports()
    strategy.control_max_drawdown()
    assert strategy.mm_drawdown_status()["global_pnl_quote"] == Decimal("22")
    strategy.executor_orchestrator.store_all_positions()
    reloaded = ExecutorOrchestrator(strategy)
    assert reloaded.generate_performance_report("life_perpetual").global_pnl_quote == Decimal("22")
    assert checkpoint(owned.store)["max_global_pnl"] == "22"


def test_failed_peak_checkpoint_rolls_back_and_revokes_risk(owned):
    strategy, _, _ = arm(owned)
    strategy.control_max_drawdown()

    def broken(connection, cursor, statement, parameters, context, many):
        if statement.startswith("UPDATE bot_state"):
            raise OperationalError("checkpoint", {}, RuntimeError("private-input"))
    event.listen(owned.engine, "before_cursor_execute", broken)
    try:
        strategy.controller_reports["life_spot"]["performance"].global_pnl_quote = Decimal("5")
        with patch("scripts.v2_with_controllers.HummingbotApplication.main_application") as app:
            app.return_value.stop.side_effect = lambda **kwargs: owned.owner.require_recovery(kwargs["mm_drawdown_reason"])
            with pytest.raises(OperationalError):
                strategy.control_max_drawdown()
        assert not strategy.mm_drawdown_ready()
        assert not owned.owner.allow_new_orders
    finally:
        event.remove(owned.engine, "before_cursor_execute", broken)
    assert checkpoint(owned.store)["max_global_pnl"] == "0"
