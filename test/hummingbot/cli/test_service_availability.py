"""Optional observation failures must not stop a healthy trading owner."""
import asyncio
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import BasicAuth
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.exc import OperationalError

from hummingbot.cli.service import ServiceHost, create_service_app
from hummingbot.client.config.admin_config import AdminSettings
from hummingbot.client.config.runtime_config import RuntimeSettings


def host():
    item = ServiceHost(RuntimeSettings.from_environment({"DATABASE_URL": "postgresql://unused/test"}),
                       asyncio.Event(), MagicMock())
    core = SimpleNamespace(connector_manager=SimpleNamespace(connectors={}), strategy=None,
                           get_status=lambda: {"strategy_running": False, "clock_running": False})
    item.hb = SimpleNamespace(trading_core=core)
    return item


def test_reporting_failure_preserves_control_snapshot_but_invalidates_old_observations(monkeypatch):
    item = host()
    item.reporting = {"accounts": [{"account_id": "a", "environment": "demo", "balances": []}]}

    def broken(*args):
        raise ValueError("private-input-must-not-leak")

    monkeypatch.setattr("hummingbot.cli.admin_observations.capture_reporting", broken)
    value = item.snapshot()
    assert value["engine"]["strategy_running"] is False
    assert value["reporting_unavailable"] is True
    assert value["availability"]["incidents"][0]["reason_code"] == "reporting_unavailable"
    assert "private-input" not in json.dumps(value)
    monkeypatch.setattr("hummingbot.cli.admin_observations.capture_reporting", lambda *args: {})
    assert not item.snapshot()["reporting_unavailable"]
    assert item.snapshot()["availability"]["incidents"] == []


@pytest.mark.asyncio
async def test_failed_observational_claim_retries_without_stopping_owner_or_losing_reason(monkeypatch):
    item = host()
    item.store, item.owner = MagicMock(), MagicMock()
    claim = MagicMock(side_effect=[ValueError("secret"), None])
    monkeypatch.setattr("hummingbot.model.admin_store.AdminStore.claim_check", claim)
    await item.poll_reconciliation()
    await item.reconciliation_task
    assert item.availability.snapshot()["incidents"][0]["reason_code"] == "reconciliation_unavailable"
    assert item.next_check_poll > time.monotonic()
    await item.poll_reconciliation()
    assert claim.call_count == 1
    item.next_check_poll = 0
    await item.poll_reconciliation()
    await item.reconciliation_task
    assert item.availability.snapshot()["incidents"] == []
    item.owner.require_recovery.assert_not_called()
    assert not item.stop.is_set()


@pytest.mark.asyncio
async def test_liveness_reports_stalled_observer_but_not_deliberate_wait():
    item = host()
    item.bootstrapped = True
    item.phase = "waiting_for_mm_readiness"
    item.observer_progress_at = time.monotonic()
    assert (await item.health(SimpleNamespace(path="/live"))).status == 200
    assert (await item.health(SimpleNamespace(path="/ready"))).status == 503
    item.observer_progress_at -= 10
    assert (await item.health(SimpleNamespace(path="/live"))).status == 503


@pytest.mark.asyncio
async def test_database_unavailable_status_keeps_local_diagnostics_without_inventing_control_state():
    item = host()
    item.store = MagicMock()
    item.store.read.side_effect = OperationalError("query", {}, RuntimeError("private-dsn"))
    settings = AdminSettings(True, "operator", "test-password")
    async with TestClient(TestServer(create_service_app(item, settings)), headers={
            "Authorization": BasicAuth("operator", "test-password").encode()}) as client:
        response = await client.get("/api/v1/admin/status")
        value = await response.json()
        assert response.status == 503
        assert value["service"]["state_source"] == "process"
        assert not value["service"]["persisted_state_available"]
        assert value["service"]["observed_at"].endswith("Z")
        assert "control_version" not in value and "private-dsn" not in json.dumps(value)
        assert (await client.get("/live")).status == 200


@pytest.mark.asyncio
async def test_failed_controller_task_halts_instead_of_waiting_forever(monkeypatch):
    from test.hummingbot.strategy_v2.controllers.test_life_mm import configuration, make_controller
    item = host()
    controller, _ = make_controller(configuration())
    controller.on_start = AsyncMock(side_effect=RuntimeError("private-error"))
    controller.start()
    await controller.wait_closed()
    core = item.hb.trading_core
    core.strategy = SimpleNamespace(controllers={controller.config.id: controller}, drawdown_exited_controllers=[])
    core.trade_fill_db = SimpleNamespace(persistence_healthy=True)
    core._strategy_running = True
    item.running, item.applied_version = True, 0
    row = dict(control_version=0, config_type="controller", config_file_path="test.yml", desired_state="running",
               recovery_state={}, mm_session=None)
    item.owner = MagicMock()
    item.owner.try_acquire.return_value = True
    item.owner.check_owner.return_value = row
    item.owner.allow_new_orders = True
    item.stop_core = AsyncMock(return_value=True)
    item.poll_reconciliation = AsyncMock()
    item.poll_funding = MagicMock()
    monkeypatch.setattr("hummingbot.core.order_submission_guard.install_guard", lambda owner: None)
    await item.observe_once()
    item.owner.require_recovery.assert_called_once()
    item.stop_core.assert_awaited_once()
    assert item.phase == "halted"
    assert item.availability.snapshot()["incidents"][0]["operator_action_required"]


def test_optional_engine_detail_failure_retains_only_known_control_flags(monkeypatch):
    item = host()
    core = item.hb.trading_core
    core.get_status = MagicMock(side_effect=ValueError("private-detail"))
    core._strategy_running = True
    value = item.snapshot()
    assert value["engine"] == {"strategy_running": True, "clock_running": None}
    assert "private-detail" not in json.dumps(value)


@pytest.mark.asyncio
async def test_queued_order_is_blocked_after_task_failure_but_cancellation_is_allowed():
    from hummingbot.core.bot_ownership import BotOwnership
    from hummingbot.core.order_submission_guard import SubmissionBlocked
    owner = BotOwnership(MagicMock(), "hummingbot")
    owner.check_owner = MagicMock(return_value={})
    owner.runtime_ready_check = lambda: False
    with pytest.raises(SubmissionBlocked):
        owner.before_order(SimpleNamespace(), SimpleNamespace())
    with pytest.raises(SubmissionBlocked):
        await owner.authorize_rest(SimpleNamespace(url="https://www.okx.com/api/v5/trade/order"))
    assert await owner.authorize_rest(SimpleNamespace(url="https://www.okx.com/api/v5/trade/cancel-order")) is None


def test_declared_initialization_wait_does_not_look_like_a_stalled_task():
    item = host()
    core = item.hb.trading_core
    controller = SimpleNamespace(config=SimpleNamespace(manual_kill_switch=False), _control_error=None,
                                 processed_data={}, _control_progress_at=time.monotonic() - 10,
                                 update_interval=1, _control_wait_until=time.monotonic() + 2)
    core.strategy = SimpleNamespace(controllers={"id": controller})
    assert item.runtime_problem() is None
    controller._control_wait_until = None
    assert item.runtime_problem() == "controller_task_stalled"


@pytest.mark.asyncio
async def test_reconciled_startup_does_not_wait_for_observer_running_ack():
    item = host()
    item.phase, item.running = "starting", False
    item.owner = SimpleNamespace(allow_new_orders=True)
    task = asyncio.create_task(asyncio.sleep(100))
    item.hb.trading_core._clock_task = task
    try:
        assert item.runtime_healthy()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_slow_optional_claim_is_single_flight_and_does_not_block_owner_poll(monkeypatch):
    item = host()
    item.store, item.owner = MagicMock(), MagicMock()
    release = threading.Event()

    def claim(*args):
        release.wait(2)
        return None

    monkeypatch.setattr("hummingbot.model.admin_store.AdminStore.claim_check", claim)
    try:
        await asyncio.wait_for(item.poll_reconciliation(), .1)
        task = item.reconciliation_task
        await item.poll_reconciliation()
        assert item.reconciliation_task is task
        assert (await item.health(SimpleNamespace(path="/live"))).status == 200
    finally:
        release.set()
        await item.reconciliation_task


@pytest.mark.asyncio
async def test_optional_claim_failure_cannot_hide_lost_ownership(monkeypatch):
    from hummingbot.core.bot_ownership import OwnershipLost
    item = host()
    item.store, item.owner = MagicMock(), MagicMock()
    item.owner.check_owner.side_effect = OwnershipLost("owner lost")
    monkeypatch.setattr("hummingbot.model.admin_store.AdminStore.claim_check", MagicMock(side_effect=OSError()))
    await item.poll_reconciliation()
    with pytest.raises(OwnershipLost):
        await item.reconciliation_task
    with pytest.raises(OwnershipLost):
        await item.poll_reconciliation()


def test_daily_persistence_is_cadenced_but_new_currencies_are_not_hidden():
    item = host()
    item.owner = MagicMock()
    item.owner.store.publish.return_value = True
    observation = dict(account_id="account", environment="demo", identity_verified=True, balances=[dict(
        currency="USDT", observed_at=time.time(), present_in_latest_snapshot=True)])
    status = dict(reporting={"accounts": [observation]}, reporting_timezone="UTC")
    item.snapshot = lambda: status
    item.publish_status(1)
    item.publish_status(1)
    observation["balances"].append(dict(currency="LIFE", observed_at=time.time(), present_in_latest_snapshot=True))
    item.publish_status(1)
    item.publish_status(1)
    assert [call.kwargs["save_balances"] for call in item.owner.store.publish.call_args_list] == [True, False, True, False]


def test_optional_snapshot_error_retains_reason_and_backs_off_instead_of_retrying_each_tick():
    item = host()
    item.owner = MagicMock()
    item.snapshot = lambda: {}

    def publish(**kwargs):
        if kwargs["save_balances"]:
            kwargs["status"]["daily_snapshot_error"] = "ValueError"
        return True

    item.owner.store.publish.side_effect = publish
    item.publish_status(1)
    item.publish_status(1)
    assert [call.kwargs["save_balances"] for call in item.owner.store.publish.call_args_list] == [True, False]
    assert item.availability.snapshot()["incidents"][0]["reason_code"] == "daily_snapshot_unavailable"


def test_checkpoint_day_rollover_and_fenced_rejection_do_not_hide_first_observation():
    item = host()
    item.owner = MagicMock()
    item.owner.store.publish.side_effect = [False, True, True, True]
    balance = dict(currency="USDT", observed_at=1793030399)
    item.snapshot = lambda: dict(reporting_timezone="UTC", reporting={"accounts": [dict(
        account_id="account", environment="demo", identity_verified=True, balances=[balance])]})
    item.publish_status(1)
    item.publish_status(1)
    item.publish_status(1)
    balance["observed_at"] += 86400
    item.publish_status(1)
    assert [call.kwargs["save_balances"] for call in item.owner.store.publish.call_args_list] == [True, True, False, True]
