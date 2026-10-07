"""P5 final network gate rechecks a queued LIFE quote against fresh inputs."""

import asyncio
from dataclasses import replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_executor_protected_send import _setup
from test.hummingbot.strategy_v2.life_liquidity.test_protected_okx_send import PausedThrottler
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.web_assistant.connections.rest_connection import RESTConnection
from hummingbot.core.web_assistant.rest_assistant import RESTAssistant
from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
from hummingbot.strategy_v2.life_liquidity.config import QuotesConfig
from hummingbot.strategy_v2.life_liquidity.economics import EconomicPolicy
from hummingbot.strategy_v2.life_liquidity.market_data import InstrumentRules
from hummingbot.strategy_v2.life_liquidity.quote_actions import QuoteActionPlanner, QuotePlanningSnapshot
from hummingbot.strategy_v2.life_liquidity.risk import SpotIntent
from hummingbot.strategy_v2.life_liquidity.spot_quotes import QuoteCosts


def _attach_quote_planner(controller, template, wal, ledger, *,
                          reference_engine=None, book_sequence_id=None,
                          reference_model_version=None):
    quotes = QuotesConfig(spreads_bps=(Decimal("30"),), sizes_base=(Decimal("1"),))
    controller.config = controller.config.model_copy(update={
        "strategy": controller.config.strategy.model_copy(update={"quotes": quotes})})
    controller._runner_orchestrator = SimpleNamespace(active_executors={"life": []})
    current = controller._order_safety_manager.current_session
    state = {"now": 100, "snapshot": QuotePlanningSnapshot(
        session_id=current.session_id, epoch=current.epoch,
        config_version=current.config_version, observed_monotonic=99,
        expires_monotonic=101, reference_ready=True,
        all_gates_ready=True, market_reference_ready=True,
        qualified_reference_usdt=Decimal("1"), qualified_exit_value_usdt=Decimal("1"),
        best_bid_usdt=Decimal("0.98"), best_ask_usdt=Decimal("1.02"),
        rules=InstrumentRules(Decimal("0.01"), Decimal("0.1"), Decimal("0.1")),
        costs=QuoteCosts(Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"),
                         Decimal("0"), Decimal("0")),
        policy=EconomicPolicy("profit_mm", Decimal("0")),
        book_sequence_id=book_sequence_id,
        reference_model_version=reference_model_version)}
    planner = QuoteActionPlanner(
        controller, wal=wal, reservations=ledger,
        snapshot=lambda: state["snapshot"], monotonic_clock=lambda: state["now"],
        intent_id_factory=lambda: "quote-1", max_actions_per_tick=1,
        reference_engine=reference_engine)
    controller.install_quote_action_planner(planner)
    config = controller.determine_executor_actions()[0].executor_config
    executor = OrderExecutor(template._strategy, config)
    executor.get_order_price = lambda: config.price
    return state, executor


def _queued_quote(tmp_path):
    controller, template, connector, wal, ledger, _ = _setup(tmp_path)
    state, executor = _attach_quote_planner(controller, template, wal, ledger)
    executor.place_open_order()
    wire = {"clOrdId": executor._order.order_id, "instId": "LIFE-USDT",
            "side": "buy", "ordType": "post_only", "tdMode": "cash",
            "px": str(executor.config.price), "sz": str(executor.config.amount)}
    return state, connector.sent[0]["pre_send_check"], wire, wal, ledger


def test_fresh_queued_quote_passes_final_network_gate(tmp_path):
    _, check, wire, wal, ledger = _queued_quote(tmp_path)
    check(wire)
    assert wal.get("quote-1").state == "SEND_UNKNOWN"
    assert ledger.has_open_intent("quote-1")


@pytest.mark.parametrize("change", ["expired", "book_cross", "costly", "reference_shift",
                                    "session_mismatch", "gate_revoked"])
def test_queued_quote_is_revoked_when_market_economics_or_freshness_changes(tmp_path, change):
    state, check, wire, wal, ledger = _queued_quote(tmp_path)
    snapshot = state["snapshot"]
    if change == "expired":
        state["now"] = 101
    elif change == "book_cross":
        state["snapshot"] = replace(snapshot, best_ask_usdt=Decimal("0.99"))
    elif change == "costly":
        state["snapshot"] = replace(snapshot, costs=replace(
            snapshot.costs, maker_fee_rate=Decimal("0.1")))
    elif change == "reference_shift":
        state["snapshot"] = replace(snapshot, qualified_reference_usdt=Decimal("1.1"))
    elif change == "session_mismatch":
        state["snapshot"] = replace(snapshot, session_id="other-session")
    else:
        state["snapshot"] = replace(snapshot, all_gates_ready=False)
    with pytest.raises(PermissionError, match="SEND_PERMISSION_REVOKED"):
        check(wire)
    assert wal.get("quote-1").state == "SEND_UNKNOWN"
    assert ledger.has_open_intent("quote-1")


def test_queued_quote_is_revoked_if_account_budget_shrinks_after_reservation(tmp_path):
    _, check, wire, wal, ledger = _queued_quote(tmp_path)
    ledger.record_cashflow("123", "USDT", Decimal("-9.5"))
    with pytest.raises(PermissionError, match="SEND_PERMISSION_REVOKED"):
        check(wire)
    assert wal.get("quote-1").state == "SEND_UNKNOWN"
    assert ledger.has_open_intent("quote-1")


def test_reservation_preview_can_exclude_only_the_exact_unfilled_open_intent(tmp_path):
    _, _, _, wal, ledger = _queued_quote(tmp_path)
    record = wal.get("quote-1")
    intent = SpotIntent("quote-1", "BUY", Decimal("1"), Decimal("0.99"),
                        record.session_id, record.epoch)
    assert ledger.preview(exclude_open_intent=intent).check_and_hold(
        intent, reference_price=Decimal("1")).allowed
    with pytest.raises(ValueError, match="RESERVATION_EXCLUSION_UNSAFE"):
        ledger.preview(exclude_open_intent=replace(intent, quantity_base=Decimal("2")))
    ledger.record_fill("quote-1", "trade-1", Decimal("0.1"), Decimal("0.99"))
    with pytest.raises(ValueError, match="RESERVATION_EXCLUSION_UNSAFE"):
        ledger.preview(exclude_open_intent=intent)


@pytest.mark.asyncio
async def test_changed_book_after_okx_throttler_blocks_network_send(tmp_path):
    requests = []
    throttler = PausedThrottler()

    class Session:
        async def request(self, **kwargs):
            requests.append(kwargs)
            raise AssertionError("stale quote reached network")

    connector = OkxExchange("key", "secret", "passphrase", trading_pairs=[], trading_required=False)
    connector._trading_rules["LIFE-USDT"] = TradingRule(
        trading_pair="LIFE-USDT", min_order_size=Decimal("0.1"),
        min_price_increment=Decimal("0.01"), min_base_amount_increment=Decimal("0.1"))
    connector._on_order_failure = Mock()
    connector._web_assistants_factory = SimpleNamespace(get_rest_assistant=AsyncMock(
        return_value=RESTAssistant(connection=RESTConnection(Session()), throttler=throttler)))
    connector._api_request_url = AsyncMock(return_value="https://www.okx.com/api/v5/trade/order")
    connector.exchange_symbol_associated_to_pair = AsyncMock(return_value="LIFE-USDT")
    controller, template, _, wal, ledger, _ = _setup(tmp_path, connector=connector)
    state, executor = _attach_quote_planner(controller, template, wal, ledger)

    executor.place_open_order()
    await asyncio.wait_for(throttler.entered.wait(), timeout=2)
    state["snapshot"] = replace(state["snapshot"], best_ask_usdt=Decimal("0.99"))
    throttler.release.set()
    for _ in range(50):
        if connector._on_order_failure.called:
            break
        await asyncio.sleep(0.01)
    assert connector._on_order_failure.called
    assert requests == []
    assert wal.get("quote-1").state == "SEND_UNKNOWN"
    assert ledger.has_open_intent("quote-1")
