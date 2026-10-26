import asyncio
from decimal import Decimal
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executors_info import PerformanceReport
from scripts.v2_with_controllers import V2WithControllers, V2WithControllersConfig

SCOPE = {"life_spot": {"connector_name": "okx", "trading_pair": "LIFE-USDT"},
         "life_perpetual": {"connector_name": "okx_perpetual", "trading_pair": "LIFE-USDT"}}


def runner(global_limit="10", controller_limit=None, spot="0", perpetual="0"):
    config = V2WithControllersConfig(max_global_drawdown_quote=global_limit,
                                     max_controller_drawdown_quote=controller_limit, mm_drawdown_scope=SCOPE)
    controller = SimpleNamespace(config=SimpleNamespace(manual_kill_switch=False), status=RunnableStatus.RUNNING,
                                 _session_admitted=True, stop=MagicMock(), start=MagicMock())
    item = SimpleNamespace(config=config, controllers={"life_spot": controller},
                           max_global_pnl=Decimal("0"), max_pnl_by_controller={}, drawdown_exited_controllers=[],
                           _is_stop_triggered=False, controller_reports_timestamp=1000, controller_reports_error=None,
                           market_data_provider=SimpleNamespace(time=lambda: 1000),
                           executor_orchestrator=SimpleNamespace(mm_valuation_ready=lambda: True,
                                                                 execute_actions=MagicMock()),
                           controller_reports={key: {"performance": PerformanceReport(global_pnl_quote=Decimal(value))}
                                               for key, value in (("life_spot", spot), ("life_perpetual", perpetual))},
                           mm_drawdown_checkpoint_callback=MagicMock(), _mm_drawdown_last_checkpoint=None,
                           _mm_drawdown_state=None, _mm_drawdown_breach=None, _mm_drawdown_bound=False)
    for name in ("control_max_drawdown", "_control_mm_drawdown", "mm_drawdown_policy", "mm_drawdown_checkpoint",
                 "mm_drawdown_status", "mm_drawdown_ready", "_mm_report_values", "_halt_mm_drawdown", "check_manual_kill_switch"):
        setattr(item, name, MethodType(getattr(V2WithControllers, name), item))
    return item


@pytest.mark.parametrize("limit", ["0", "-1", "NaN", "Infinity"])
def test_mm_drawdown_rejects_nonpositive_or_nonfinite_limits(limit):
    with pytest.raises(ValidationError):
        V2WithControllersConfig(max_global_drawdown_quote=limit, mm_drawdown_scope=SCOPE)


def test_zero_floor_and_observed_peaks_use_existing_report_basis_and_checkpoint_only_changes():
    item = runner(spot="-2")
    item.control_max_drawdown()
    assert item.max_global_pnl == Decimal("0")
    assert item.mm_drawdown_status()["global_drawdown_quote"] == Decimal("2")
    item.control_max_drawdown()
    assert item.mm_drawdown_checkpoint_callback.call_count == 1
    item.controller_reports["life_spot"]["performance"].global_pnl_quote = Decimal("3")
    item.control_max_drawdown()
    assert item.max_global_pnl == Decimal("3")
    assert item.max_pnl_by_controller["life_spot"] == Decimal("3")
    assert item.mm_drawdown_checkpoint_callback.call_count == 2


def test_threshold_equality_is_permitted_and_breach_uses_position_preserving_service_stop():
    item = runner(spot="-10")
    with patch("scripts.v2_with_controllers.HummingbotApplication.main_application") as app:
        item.control_max_drawdown()
        assert item.mm_drawdown_ready()
        app.return_value.stop.assert_not_called()
        item.controller_reports["life_spot"]["performance"].global_pnl_quote = Decimal("-10.0000001")
        item.control_max_drawdown()
    assert item._is_stop_triggered
    assert not item.controllers["life_spot"]._session_admitted
    app.return_value.stop.assert_called_once_with(mm_drawdown_reason="drawdown_global_limit")
    item.executor_orchestrator.execute_actions.assert_not_called()
    item.controllers["life_spot"].stop.assert_not_called()
    assert not item.mm_drawdown_ready()
    item.controllers["life_spot"].status = RunnableStatus.TERMINATED
    item.check_manual_kill_switch()
    item.controllers["life_spot"].start.assert_not_called()


def test_per_controller_limit_includes_retained_disabled_controller():
    item = runner(global_limit=None, controller_limit="5", perpetual="-6")
    with patch("scripts.v2_with_controllers.HummingbotApplication.main_application") as app:
        item.control_max_drawdown()
    assert item._is_stop_triggered
    assert item.mm_drawdown_status()["breached_controller_id"] == "life_perpetual"
    app.return_value.stop.assert_called_once_with(mm_drawdown_reason="drawdown_controller_limit")


@pytest.mark.parametrize("failure", ["stale", "report_error", "valuation", "missing", "nonfinite"])
def test_missing_required_coverage_is_not_fresh_zero_pnl_or_a_new_peak(failure):
    item = runner()
    item.control_max_drawdown()
    if failure == "stale":
        item.controller_reports_timestamp = 990
    elif failure == "report_error":
        item.controller_reports_error = "ValueError"
    elif failure == "valuation":
        item.executor_orchestrator.mm_valuation_ready = lambda: False
    elif failure == "missing":
        item.controller_reports.pop("life_perpetual")
    else:
        item.controller_reports["life_perpetual"]["performance"].global_pnl_quote = Decimal("NaN")
    assert not item.mm_drawdown_ready()
    item.control_max_drawdown()
    status = item.mm_drawdown_status()
    assert not status["ready"] and status["stale"]
    assert status["global_pnl_quote"] is None
    assert status["global_peak_quote"] == Decimal("0")
    assert not item._is_stop_triggered
    assert item.mm_drawdown_checkpoint_callback.call_count == 1


def test_checkpoint_failure_cannot_allow_new_risk_or_become_optional_report_error():
    item = runner(spot="5")
    item.mm_drawdown_checkpoint_callback.side_effect = RuntimeError("private-database-input")
    with patch("scripts.v2_with_controllers.HummingbotApplication.main_application") as app:
        with pytest.raises(RuntimeError):
            item.control_max_drawdown()
    assert not item.mm_drawdown_ready()
    assert item._is_stop_triggered
    app.return_value.stop.assert_called_once_with(mm_drawdown_reason="drawdown_checkpoint_unavailable")


def test_restored_peaks_and_breach_do_not_depend_on_current_controller_selection():
    old = runner(spot="30", perpetual="70")
    old.control_max_drawdown()
    saved = old.mm_drawdown_checkpoint()
    current = runner(spot="0", perpetual="89")
    current.max_global_pnl = Decimal(saved["max_global_pnl"])
    current.max_pnl_by_controller = {key: Decimal(value) for key, value in saved["max_pnl_by_controller"].items()}
    with patch("scripts.v2_with_controllers.HummingbotApplication.main_application"):
        current.control_max_drawdown()
    assert current._is_stop_triggered
    assert current.max_global_pnl == Decimal("100")
    assert current.mm_drawdown_policy() == old.mm_drawdown_policy()


def test_new_report_cache_activity_cannot_certify_an_old_drawdown_decision():
    item = runner()
    item.control_max_drawdown()
    item.market_data_provider.time = lambda: 1010
    item.controller_reports_timestamp = 1010
    assert not item.mm_drawdown_ready()
    assert item.mm_drawdown_status()["stale"]
    item.control_max_drawdown()
    assert item.mm_drawdown_ready()


@pytest.mark.asyncio
async def test_retained_market_uses_existing_public_subscription_and_safe_read_retry():
    item = runner()
    item.connectors = {"okx": object()}
    item.executor_orchestrator.positions_held = {"life_perpetual": [SimpleNamespace(
        connector_name="okx_perpetual", trading_pair="LIFE-USDT", net_amount_base=Decimal("1"))]}
    source = SimpleNamespace(is_trading_required=False, _update_trading_rules=AsyncMock(side_effect=[asyncio.TimeoutError(), None]))
    item.market_data_provider.get_connector_with_fallback = lambda name: source
    item.market_data_provider.acquire_order_books = AsyncMock(return_value={"LIFE-USDT": True})
    item.market_data_provider.release_order_books = AsyncMock()
    with patch("scripts.v2_with_controllers.asyncio.sleep", new=AsyncMock()):
        await V2WithControllers.initialize_mm_valuations(item)
    assert source._update_trading_rules.await_count == 2
    item.market_data_provider.acquire_order_books.assert_awaited_once_with("okx_perpetual", ["LIFE-USDT"], item)


@pytest.mark.asyncio
async def test_stop_can_cancel_pending_read_only_valuation_initialization():
    item = runner()
    item.connectors = {"okx": object()}
    item.executor_orchestrator.positions_held = {"life_perpetual": [SimpleNamespace(
        connector_name="okx_perpetual", trading_pair="LIFE-USDT", net_amount_base=Decimal("1"))]}
    item.market_data_provider.get_connector_with_fallback = lambda name: SimpleNamespace(is_trading_required=False, _update_trading_rules=AsyncMock())
    entered = asyncio.Event()

    async def waiting(*args):
        entered.set()
        await asyncio.Event().wait()
    item.market_data_provider.acquire_order_books = waiting
    task = asyncio.create_task(V2WithControllers.initialize_mm_valuations(item))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_legacy_runner_keeps_original_threshold_dispatch_and_zero_disable():
    item = SimpleNamespace(config=V2WithControllersConfig(max_global_drawdown_quote="10"),
                           check_max_global_drawdown=MagicMock(), check_max_controller_drawdown=MagicMock())
    V2WithControllers.control_max_drawdown(item)
    item.check_max_global_drawdown.assert_called_once()
    item.check_max_controller_drawdown.assert_not_called()
    item.config.max_global_drawdown_quote = Decimal("0")
    V2WithControllers.control_max_drawdown(item)
    assert item.check_max_global_drawdown.call_count == 1


def test_stopped_drawdown_status_preserves_peak_and_breach_without_inventing_current_pnl():
    from hummingbot.cli.admin_models import DrawdownStatus
    from hummingbot.core.mm_drawdown import checkpoint_drawdown_status
    item = runner(spot="20")
    item.control_max_drawdown()
    item.controller_reports["life_spot"]["performance"].global_pnl_quote = Decimal("9")
    with patch("scripts.v2_with_controllers.HummingbotApplication.main_application"):
        item.control_max_drawdown()
    stopped = checkpoint_drawdown_status(item.mm_drawdown_checkpoint())
    data = DrawdownStatus.model_validate(stopped).model_dump(mode="json")
    assert data["global_peak_quote"] == "20" and data["global_pnl_quote"] is None
    assert data["stale"] and data["reason_code"] == "drawdown_global_limit"
    assert data["operator_action_required"] and data["funding"] == "excluded"
    live = DrawdownStatus.model_validate(item.mm_drawdown_status()).model_dump(mode="json")
    assert live["observed_at"] == "1970-01-01T00:16:40Z"
    broken = item.mm_drawdown_checkpoint()
    broken["max_global_pnl"] = "NaN"
    data = DrawdownStatus.model_validate(checkpoint_drawdown_status(broken)).model_dump(mode="json")
    assert data["global_peak_quote"] is None and data["reason_code"] == "drawdown_checkpoint_invalid"
