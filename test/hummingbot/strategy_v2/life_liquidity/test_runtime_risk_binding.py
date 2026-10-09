"""The opt-in P4 risk gate must revoke queued and wire-bound LIFE creates."""

import asyncio
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_executor_protected_send import _setup as sender_setup
from test.hummingbot.strategy_v2.life_liquidity.test_final_quote_send import _attach_quote_planner
from test.hummingbot.strategy_v2.life_liquidity.test_protected_okx_send import PausedThrottler
from test.hummingbot.strategy_v2.life_liquidity.test_quote_actions import _setup as planner_setup
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.web_assistant.connections.rest_connection import RESTConnection
from hummingbot.core.web_assistant.rest_assistant import RESTAssistant
from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.life_liquidity.action_journal import QuoteActionJournal
from hummingbot.strategy_v2.life_liquidity.safety import SafetyGate, SafetyObservation


def _observation(at, *, fresh=True, drawdown="0"):
    return SafetyObservation(at, fresh, True, True, True, Decimal(drawdown), Decimal("100"))


def _install(controller, tmp_path, state):
    del controller.allow_create_executor_actions
    controller._spot_quote_gates_ready = lambda: True
    controller.order_safety_watchdog_task = SimpleNamespace(done=lambda: False)
    gate = SafetyGate(tmp_path / "safety.json", max_drawdown_bps=Decimal("500"),
                      min_margin_buffer_quote=Decimal("10"), stable_data_ms=0,
                      recovery_probe_base=Decimal("1"))
    controller.install_runtime_risk_gate(
        gate, observation=lambda: state["observation"],
        monotonic_clock_ms=lambda: state["now"], max_observation_age_ms=5)
    assert controller.allow_create_executor_actions() is False  # DEGRADED is still blocked.
    assert controller.allow_create_executor_actions() is True
    return gate


def test_stale_runtime_risk_observation_rejects_queued_v2_create(tmp_path):
    controller, planner, _, _, _ = planner_setup(tmp_path, max_actions=1)
    state = {"now": 100, "observation": _observation(100)}
    gate = _install(controller, tmp_path, state)
    controller.install_quote_action_planner(planner)
    create = planner.propose()[0]
    state["now"] = 106
    runner = SimpleNamespace(controllers={"life": controller}, logger=lambda: MagicMock())

    assert StrategyV2Base._filter_authorized_actions(runner, [create]) == []
    assert gate.state == "PAUSED"
    assert controller.runtime_risk_reason_code == "RISK_OBSERVATION_STALE"
    assert QuoteActionJournal(planner.action_journal.path).get(create.executor_config.id).state == "REJECTED"


def test_drawdown_halt_revokes_queued_quote_at_final_wire_check(tmp_path):
    controller, template, connector, wal, ledger, _ = sender_setup(tmp_path)
    state = {"now": 100, "observation": _observation(100)}
    gate = _install(controller, tmp_path, state)
    _, executor = _attach_quote_planner(controller, template, wal, ledger)
    executor.place_open_order()
    check = connector.sent[0]["pre_send_check"]
    wire = {"clOrdId": executor._order.order_id, "instId": "LIFE-USDT",
            "side": "buy", "ordType": "post_only", "tdMode": "cash",
            "px": str(executor.config.price), "sz": str(executor.config.amount)}
    state["now"] = 101
    state["observation"] = _observation(101, drawdown="501")

    with pytest.raises(PermissionError, match="SEND_PERMISSION_REVOKED"):
        check(wire)
    assert gate.state == "HALTED"
    assert SafetyGate(gate.path, max_drawdown_bps=Decimal("9999"),
                      min_margin_buffer_quote=Decimal("0"), stable_data_ms=0,
                      recovery_probe_base=Decimal("10")).state == "HALTED"
    state["now"] = 107
    assert not controller.allow_create_executor_actions()
    assert gate.state == "HALTED"
    assert wal.get(executor.config.id).state == "SEND_UNKNOWN"
    assert ledger.has_open_intent(executor.config.id)


def test_runtime_risk_provider_failure_resets_recovery_hysteresis(tmp_path):
    controller, _, _, _, _ = planner_setup(tmp_path)
    state = {"now": 100, "observation": _observation(100)}
    gate = _install(controller, tmp_path, state)
    state["observation"] = None

    assert controller.allow_create_executor_actions() is False
    assert gate.state == "PAUSED"
    assert controller.runtime_risk_reason_code == "RISK_OBSERVATION_UNAVAILABLE"
    state["now"] = 101
    state["observation"] = _observation(101)
    assert controller.allow_create_executor_actions() is False  # Probe only.
    assert controller.allow_create_executor_actions() is True


@pytest.mark.asyncio
async def test_runtime_risk_pause_starts_controller_cancel_reconciliation(tmp_path):
    controller, template, _, wal, ledger, _ = sender_setup(tmp_path)
    state = {"now": 100, "observation": _observation(100)}
    _install(controller, tmp_path, state)
    _attach_quote_planner(controller, template, wal, ledger)
    controller._quote_action_planner.snapshot = MagicMock(side_effect=AssertionError("quote snapshot after risk pause"))
    controller._cancel_and_reconcile_orders = AsyncMock()
    state["now"] = 101
    state["observation"] = _observation(101, fresh=False)

    controller.on_safety_tick(101)
    await asyncio.sleep(0)

    assert controller._order_safety_manager.state == "PAUSED"
    assert controller.runtime_risk_reason_code == "MARKET_DATA_STALE"
    controller._quote_action_planner.snapshot.assert_not_called()
    controller._cancel_and_reconcile_orders.assert_awaited_once()


@pytest.mark.asyncio
async def test_halt_while_okx_throttler_waits_blocks_network_send(tmp_path):
    requests = []
    throttler = PausedThrottler()

    class Session:
        async def request(self, **kwargs):
            requests.append(kwargs)
            raise AssertionError("HALTED quote reached network")

    connector = OkxExchange("key", "secret", "passphrase", trading_pairs=[], trading_required=False)
    connector._trading_rules["LIFE-USDT"] = TradingRule(
        trading_pair="LIFE-USDT", min_order_size=Decimal("0.1"),
        min_price_increment=Decimal("0.01"), min_base_amount_increment=Decimal("0.1"))
    connector._on_order_failure = Mock()
    connector._web_assistants_factory = SimpleNamespace(get_rest_assistant=AsyncMock(
        return_value=RESTAssistant(connection=RESTConnection(Session()), throttler=throttler)))
    connector._api_request_url = AsyncMock(return_value="https://www.okx.com/api/v5/trade/order")
    connector.exchange_symbol_associated_to_pair = AsyncMock(return_value="LIFE-USDT")
    controller, template, _, wal, ledger, _ = sender_setup(tmp_path, connector=connector)
    state = {"now": 100, "observation": _observation(100)}
    gate = _install(controller, tmp_path, state)
    _, executor = _attach_quote_planner(controller, template, wal, ledger)

    executor.place_open_order()
    await asyncio.wait_for(throttler.entered.wait(), timeout=2)
    state["now"] = 101
    state["observation"] = _observation(101, drawdown="501")
    throttler.release.set()
    for _ in range(50):
        if connector._on_order_failure.called:
            break
        await asyncio.sleep(0.01)

    assert connector._on_order_failure.called
    assert gate.state == "HALTED"
    assert requests == []
    assert wal.get(executor.config.id).state == "SEND_UNKNOWN"
    assert ledger.has_open_intent(executor.config.id)
