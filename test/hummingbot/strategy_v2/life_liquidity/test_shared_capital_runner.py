"""Both actual executors share one authority; unrelated quote gates are isolated."""

import asyncio
from dataclasses import replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_protected_okx_send import PausedThrottler
from test.hummingbot.strategy_v2.life_liquidity.test_protected_swap_send import connector_with_transport
from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital import policy, snapshot
from test.hummingbot.strategy_v2.life_liquidity.test_swap_executor import setup_swap, wire_for
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.data_type.common import PositionAction, PositionMode, TradeType
from hummingbot.core.web_assistant.connections.rest_connection import RESTConnection
from hummingbot.core.web_assistant.rest_assistant import RESTAssistant
from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
from hummingbot.strategy_v2.life_liquidity.shared_capital import SharedCapitalAuthority
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction

D = Decimal


def setup_shared(tmp_path, *, collateral="12", route=None, spot_route=None):
    c, sender, swap, wal, clock, account, risk, config = setup_swap(tmp_path, action=PositionAction.OPEN, route=route, spot_route=spot_route)
    account["value"] = replace(account["value"], snapshot_sequence=1)
    now = int(clock.wall.timestamp() * 1000)
    state = {"snapshot": snapshot(
        position_mode="ONEWAY", leverage=1, observed_at_ms=now,
        spot_life_base=D("10"), long_base=D("1"), collateral_quote=D(collateral))}
    capital = SharedCapitalAuthority(
        tmp_path / "shared_capital.json", policy=policy(position_mode="ONEWAY", leverage=1),
        observation=lambda: state["snapshot"], clock_ms=lambda: int(clock.wall.timestamp() * 1000), create=True)
    c.install_shared_capital_authority(capital)
    # O.7.2 isolates financial allocation. O.6 tests qualify the spot feed/quote gates.
    c.allow_create_executor_actions = lambda: True
    spot = c._protected_spot_sender.gateway.connector
    strategy = SimpleNamespace(connectors={"okx": spot, "okx_perpetual": swap}, controllers={"life": c})
    spot_config = OrderExecutorConfig(id="spot-child", controller_id="life", connector_name="okx", trading_pair="LIFE-USDT",
                                      side=TradeType.BUY, amount=D("6"), price=D("1"),
                                      execution_strategy=ExecutionStrategy.LIMIT_MAKER, level_id="0")
    swap_config = config.model_copy(update={"amount": D("6"), "side": TradeType.SELL})
    return c, capital, sender, spot, swap, wal, clock, account, state, strategy, spot_config, swap_config


def dispatch(c, strategy, config, action=None):
    runner = SimpleNamespace(controllers={"life": c}, logger=c.logger)
    action = action or CreateExecutorAction(controller_id="life", executor_config=config)
    assert StrategyV2Base._filter_authorized_actions(runner, [action]) == [action]
    executor = OrderExecutor(strategy, config)
    executor.get_order_price = lambda: config.price
    executor.place_open_order()
    return executor


@pytest.mark.parametrize("first", ["spot", "swap"])
def test_real_executor_routes_cannot_overallocate_account_in_either_order(tmp_path, first):
    c, cap, sender, spot, swap, wal, clock, account, state, strategy, sc, pc = setup_shared(tmp_path)
    if first == "spot":
        dispatch(c, strategy, sc)
        with pytest.raises(PermissionError, match="CAPITAL_INITIAL_MARGIN_LOW"):
            sender.propose(pc)
        assert len(spot.sent) == 1 and swap.sent == []
        assert wal.all_records() == ()
    else:
        action = sender.propose(pc)
        dispatch(c, strategy, pc, action)
        with pytest.raises(PermissionError, match="CAPITAL_INITIAL_MARGIN_LOW"):
            dispatch(c, strategy, sc)
        assert len(swap.sent) == 1 and spot.sent == []
        assert c._order_safety_wal.get(sc.id).state == "ABORTED_BEFORE_SEND"
        assert not c._order_safety_reservations.has_open_intent(sc.id)
    assert len(cap.claim_ids()) == 1


@pytest.mark.parametrize("route", ["spot", "swap"])
@pytest.mark.parametrize("fault", ["collateral", "basis", "funding", "stale", "sequence", "position", "disk"])
def test_shared_authority_revokes_either_dispatched_route_and_keeps_claim(tmp_path, route, fault):
    c, cap, sender, spot, swap, wal, clock, account, state, strategy, sc, pc = setup_shared(tmp_path, collateral="100")
    config = sc if route == "spot" else pc
    action = None if route == "spot" else sender.propose(pc)
    dispatch(c, strategy, config, action)
    connector = spot if route == "spot" else swap
    sent = connector.sent[0]
    obs = state["snapshot"]
    changes = {"collateral": {"collateral_quote": D("0")}, "basis": {"mark_price_usdt": D("100")},
               "funding": {"funding_liability_quote": D("11")}, "sequence": {"sequence": 0},
               "position": {"spot_life_base": D("9")} if route == "spot" else {"long_base": D("2")}}
    if fault == "stale":
        clock.advance(2)
    elif fault == "disk":
        cap.journal.path.unlink()
    else:
        state["snapshot"] = replace(obs, sequence=2, **{k: v for k, v in changes[fault].items() if k != "sequence"})
        if fault == "sequence":
            state["snapshot"] = replace(state["snapshot"], sequence=0)
        account["value"] = replace(account["value"], snapshot_sequence=state["snapshot"].sequence)
    wire = (wire_for(config, sent["order_id"], PositionMode.ONEWAY) if route == "swap" else {
        "clOrdId": sent["order_id"], "instId": "LIFE-USDT", "side": "buy", "ordType": "post_only",
        "tdMode": "cash", "px": "1", "sz": "6"})
    if route == "swap":
        wire["sz"] = "24"
    with pytest.raises(PermissionError):
        sent["pre_send_check"](wire)
    assert cap.retained_claim_ids() == (config.id,)
    ledger_wal = wal if route == "swap" else c._order_safety_wal
    assert ledger_wal.get(config.id).state == "SEND_UNKNOWN"


@pytest.mark.asyncio
async def test_capital_revoked_inside_real_swap_rest_throttler_sends_zero_requests(tmp_path, monkeypatch):
    connector, throttle, requests = connector_with_transport()
    connector._perpetual_trading.set_leverage("LIFE-USDT", 1)
    connector._trading_rules["LIFE-USDT"] = TradingRule(
        "LIFE-USDT", min_order_size=D("0.25"), min_price_increment=D("0.0001"),
        min_base_amount_increment=D("0.025"), buy_order_collateral_token="USDT", sell_order_collateral_token="USDT")
    tasks = []

    def schedule(coro):
        task = asyncio.create_task(coro)
        tasks.append(task)
        return task

    monkeypatch.setattr("hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative.safe_ensure_future", schedule)
    c, cap, sender, spot, swap, wal, clock, account, state, strategy, sc, pc = setup_shared(tmp_path, collateral="100", route=connector)
    dispatch(c, strategy, pc, sender.propose(pc))
    try:
        await asyncio.wait_for(throttle.entered.wait(), 2)
        state["snapshot"] = replace(state["snapshot"], sequence=2, collateral_quote=D("0"))
        account["value"] = replace(account["value"], snapshot_sequence=2)
        throttle.release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 2)
        assert requests == [] and wal.get(pc.id).state == "SEND_UNKNOWN"
        assert cap.claim_ids() == (pc.id,)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def test_shared_authority_cannot_be_replaced_or_installed_over_existing_orders(tmp_path):
    c, cap, sender, spot, swap, wal, clock, account, state, strategy, sc, pc = setup_shared(tmp_path)
    with pytest.raises(ValueError, match="BINDING"):
        c.install_shared_capital_authority(cap)
    sender.propose(pc)
    c._shared_capital_authority = None  # Model an attempted reinstallation during recovery.
    with pytest.raises(ValueError, match="RECOVERY_REQUIRED"):
        c.install_shared_capital_authority(cap)


@pytest.mark.asyncio
@pytest.mark.parametrize("revoke", [False, True])
async def test_shared_capital_at_actual_spot_rest_boundary(tmp_path, monkeypatch, revoke):
    requests, tasks = [], []
    throttle = PausedThrottler()

    class Response:
        status = 200
        content_type = "application/json"

        async def json(self):
            return {"data": [{"sCode": "0", "ordId": "spot-ack"}]}

    class Session:
        async def request(self, **kwargs):
            requests.append(kwargs)
            return Response()

    connector = OkxExchange("synthetic-key", "synthetic-secret", "synthetic-passphrase", trading_pairs=[], trading_required=False)
    connector._trading_rules["LIFE-USDT"] = TradingRule(
        "LIFE-USDT", min_order_size=D("1"), min_price_increment=D("0.01"), min_base_amount_increment=D("1"))
    connector._web_assistants_factory = SimpleNamespace(get_rest_assistant=AsyncMock(
        return_value=RESTAssistant(connection=RESTConnection(Session()), throttler=throttle)))
    connector._api_request_url = AsyncMock(return_value="https://www.okx.com/api/v5/trade/order")
    connector.exchange_symbol_associated_to_pair = AsyncMock(return_value="LIFE-USDT")

    def schedule(coro):
        task = asyncio.create_task(coro)
        tasks.append(task)
        return task

    monkeypatch.setattr("hummingbot.connector.exchange.okx.okx_exchange.safe_ensure_future", schedule)
    c, cap, sender, spot, swap, wal, clock, account, state, strategy, sc, pc = setup_shared(
        tmp_path, collateral="100", spot_route=connector)
    dispatch(c, strategy, sc)
    try:
        await asyncio.wait_for(throttle.entered.wait(), 2)
        if revoke:
            state["snapshot"] = replace(state["snapshot"], sequence=2, collateral_quote=D("0"))
        throttle.release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 2)
        assert len(requests) == (0 if revoke else 1)
        assert c._order_safety_wal.get(sc.id).state == ("SEND_UNKNOWN" if revoke else "ACKED")
        assert cap.claim_ids() == (sc.id,)  # ACK never returns shared funds.
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def test_both_affordable_routes_send_and_ack_without_releasing_the_shared_pool(tmp_path):
    c, cap, sender, spot, swap, wal, clock, account, state, strategy, sc, pc = setup_shared(tmp_path, collateral="100")
    dispatch(c, strategy, sc)
    dispatch(c, strategy, pc, sender.propose(pc))
    spot_send, swap_send = spot.sent[0], swap.sent[0]
    spot_send["pre_send_check"]({
        "clOrdId": spot_send["order_id"], "instId": "LIFE-USDT", "side": "buy",
        "ordType": "post_only", "tdMode": "cash", "px": "1", "sz": "6",
    })
    wire = wire_for(pc, swap_send["order_id"], PositionMode.ONEWAY)
    wire["sz"] = "24"
    swap_send["pre_send_check"](wire)
    spot_send["on_ack"]("spot-accepted")
    swap_send["on_ack"]("swap-accepted")
    assert cap.claim_ids() == tuple(sorted((sc.id, pc.id)))
    assert c._order_safety_wal.get(sc.id).state == wal.get(pc.id).state == "ACKED"
