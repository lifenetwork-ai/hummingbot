"""O.7.4 actual V2/executor/REST boundaries with synthetic qualified carry feeds."""

import asyncio
from dataclasses import replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_carry_monitor import refresh, setup_carry
from test.hummingbot.strategy_v2.life_liquidity.test_hedge_coordinator import setup_coordinator
from test.hummingbot.strategy_v2.life_liquidity.test_protected_swap_send import connector_with_transport
from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital_runner import dispatch
from test.hummingbot.strategy_v2.life_liquidity.test_swap_executor import wire_for

import pytest

from controllers.generic.life_liquidity import LifeLiquidityController
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.data_type.common import PositionMode
from hummingbot.strategy_v2.life_liquidity.carry_monitor import FundingPayment
from hummingbot.strategy_v2.life_liquidity.shared_capital import MarginTier
from hummingbot.strategy_v2.models.executor_actions import StopExecutorAction

D = Decimal


def shock(v, feed, fault):
    b = feed["bundle"]
    if fault == "funding":
        feed["bundle"] = replace(b, funding=replace(b.funding, sequence=2, rate=D("1")))
    elif fault == "fee":
        feed["bundle"] = replace(b, terms=replace(b.terms, sequence=2, taker_fee_rate=D("0.9")))
    elif fault == "tier":
        feed["bundle"] = replace(b, terms=replace(b.terms, sequence=2, tiers=(MarginTier(D("0.5"), D("1"), D("0.9")),)))
    else:
        refresh(v, feed, mark_price_usdt=D("1.02"))


@pytest.mark.parametrize("route", ["spot", "swap"])
@pytest.mark.parametrize("fault", ["funding", "fee", "tier", "basis"])
def test_final_boundary_rechecks_carry_and_never_refunds_on_revocation(tmp_path, route, fault):
    m, v, feed = setup_carry(tmp_path, collateral="12", funding_budget_quote=D("1"))
    c, cap, sender, spot, swap, wal, clock, account, state, strategy, sc, pc = v
    config = sc if route == "spot" else pc
    action = None if route == "spot" else sender.propose(pc)
    dispatch(c, strategy, config, action)
    pending = (spot if route == "spot" else swap).sent[-1]
    shock(v, feed, fault)
    if route == "swap":
        wire = wire_for(config, pending["order_id"], PositionMode.ONEWAY)
        wire["sz"] = "24"
    else:
        wire = {"clOrdId": pending["order_id"], "instId": "LIFE-USDT", "side": "buy", "ordType": "post_only",
                "tdMode": "cash", "px": "1", "sz": "6"}
    with pytest.raises(PermissionError):
        pending["pre_send_check"](wire)
    assert cap.retained_claim_ids() == (config.id,)
    selected_wal = wal if route == "swap" else c._order_safety_wal
    assert selected_wal.get(config.id).state == "SEND_UNKNOWN"
    assert c.determine_executor_actions() == [StopExecutorAction(controller_id="life", executor_id=config.id)]
    assert not LifeLiquidityController.allow_create_executor_actions(c)


def test_prospective_swap_cannot_exceed_funding_budget_before_reserving_or_sending(tmp_path):
    m, v, feed = setup_carry(tmp_path, funding_budget_quote=D("0.02"))
    assert m.check().allowed
    with pytest.raises(PermissionError):
        v[2].propose(v[11])  # Pending OPEN 6 + actual long 1, despite a hedge reducing delta.
    assert v[1].claim_ids() == () and v[5].all_records() == () and v[4].sent == []


def test_prospective_spot_fails_before_creating_local_or_capital_holds(tmp_path):
    m, v, feed = setup_carry(tmp_path, collateral="12")
    b = feed["bundle"]
    feed["bundle"] = replace(b, terms=replace(b.terms, taker_fee_rate=D("0.9")))
    assert m.check().allowed  # Current position is affordable; the proposed BUY is not.
    with pytest.raises(PermissionError):
        dispatch(v[0], v[9], v[10])
    assert v[1].claim_ids() == () and v[3].sent == []
    assert v[0]._order_safety_reservations.reservation_ids == frozenset()
    assert v[0]._order_safety_wal.all_records() == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("revoke", [False, True])
async def test_carry_at_actual_swap_rest_throttler(tmp_path, monkeypatch, revoke):
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
    m, v, feed = setup_carry(tmp_path, route=connector)
    dispatch(v[0], v[9], v[11], v[2].propose(v[11]))
    try:
        await asyncio.wait_for(throttle.entered.wait(), 2)
        if revoke:
            shock(v, feed, "funding")
        throttle.release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 2)
        assert len(requests) == (0 if revoke else 1)
        assert v[5].get(v[11].id).state == ("SEND_UNKNOWN" if revoke else "ACKED")
        assert v[1].claim_ids() == (v[11].id,)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_safety_tick_keeps_qualifying_funding_and_margin_after_quote_expiry(tmp_path):
    m, v, feed = setup_carry(tmp_path, funding_budget_quote=D("2"))
    c, cap, sender, spot, swap, wal, clock, account, state, strategy, sc, pc = v
    c._order_safety_wal.initialize_empty()
    assert m.check().allowed
    clock.advance(11)
    now = int(clock.wall.timestamp() * 1000)
    refresh(v, feed, payments=(FundingPayment("post-expiry", now, D("-2")),))
    # All unrelated readiness checks are false; observation must still run.
    c._runtime_risk_ready = lambda: False
    c.on_safety_tick(clock.wall.timestamp())
    assert c._order_safety_manager.state == "EXPIRED"
    assert c.carry_reason_code == "CARRY_FUNDING_BUDGET"
    assert m.last_decision.funding_debits_quote == 2 and state["snapshot"].long_base == 1
    clock.advance(1)
    refresh(v, feed, payments=feed["bundle"].funding.payments, collateral_quote=D("0"))
    c.on_safety_tick(clock.wall.timestamp())
    assert m.last_decision.capital is not None and not m.last_decision.capital.allowed
    assert c._order_safety_manager.state == "EXPIRED"
    assert "carry:" in c.to_format_status()[0]
    if c.order_safety_task is not None:
        await c.order_safety_task


def test_dynamic_carry_cost_floor_binds_hedge_campaign_and_final_send(tmp_path):
    h, v, market, settlements = setup_coordinator(tmp_path)
    m, _, feed = setup_carry(tmp_path, values=v)
    action = h.propose()[0]
    config = action.executor_config
    dispatch(v[0], v[9], config, action)
    pending = v[4].sent[-1]
    # Rate remains below account funding budget, but exceeds the campaign's original cost hold.
    b = feed["bundle"]
    feed["bundle"] = replace(b, funding=replace(b.funding, sequence=2, rate=D("0.1")))
    assert m.check().allowed
    wire = wire_for(config, pending["order_id"], PositionMode.ONEWAY)
    wire.update(sz=str(config.amount / D("0.25")), px=str(config.price))
    with pytest.raises(PermissionError):
        pending["pre_send_check"](wire)
    assert v[1].claim_ids() == (config.id,)
    with h.journal.locked() as saved:
        assert D(saved["cost_hold"]) >= D("0.004")


def test_carry_binding_cannot_be_replaced_and_never_grants_production_permission(tmp_path):
    m, v, _ = setup_carry(tmp_path)
    with pytest.raises(ValueError, match="BINDING"):
        v[0].install_carry_monitor(m)
    assert LifeLiquidityController.trading_permissions_ready(v[0]) is False
    v[0]._carry_monitor = None
    v[0].config = v[0].config.model_copy(update={"recovery_state_dir": str(tmp_path / "different")})
    with pytest.raises(ValueError, match="PATH"):
        v[0].install_carry_monitor(m)


@pytest.mark.asyncio
@pytest.mark.parametrize("revoke", [False, True])
async def test_carry_at_actual_spot_rest_throttler(tmp_path, monkeypatch, revoke):
    from test.hummingbot.strategy_v2.life_liquidity.test_protected_okx_send import PausedThrottler
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
    from hummingbot.core.web_assistant.connections.rest_connection import RESTConnection
    from hummingbot.core.web_assistant.rest_assistant import RESTAssistant

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
    m, v, feed = setup_carry(tmp_path, spot_route=connector, funding_budget_quote=D("1"))
    dispatch(v[0], v[9], v[10])
    try:
        await asyncio.wait_for(throttle.entered.wait(), 2)
        if revoke:
            shock(v, feed, "funding")
        throttle.release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 2)
        assert len(requests) == (0 if revoke else 1)
        assert v[0]._order_safety_wal.get(v[10].id).state == ("SEND_UNKNOWN" if revoke else "ACKED")
        assert v[1].claim_ids() == (v[10].id,)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def test_optional_carry_gate_records_and_restores_without_breaking_telemetry(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.telemetry import TelemetryRecorder
    m, v, feed = setup_carry(tmp_path, funding_budget_quote=D("1"))
    c = v[0]
    c._order_safety_wal.initialize_empty()
    log = TelemetryRecorder(tmp_path / "carry-telemetry.jsonl", clock_ms=lambda: 0,
                            synthetic=True, max_records=20, create=True)
    c.install_telemetry(log, independent_value=lambda: None, utc_clock_ms=m.clock_ms, max_value_age_ms=1000)
    assert not LifeLiquidityController.allow_create_executor_actions(c)  # Other production gates remain closed.
    assert log.healthy and log.records()[-1]["gates"]["carry"] is True
    shock(v, feed, "funding")
    assert not LifeLiquidityController.allow_create_executor_actions(c)
    assert log.healthy and log.records()[-1]["gates"]["carry"] is False
    log.flush()
    restored = TelemetryRecorder(log.path, clock_ms=lambda: 0, synthetic=True, max_records=20, create=False)
    assert restored.records() == log.records()
