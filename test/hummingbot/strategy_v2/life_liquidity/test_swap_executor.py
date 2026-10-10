"""O.7 isolated protected SWAP runner contract; shared capital/recovery is separate."""

import asyncio
from dataclasses import replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_contract_spec import _life_swap
from test.hummingbot.strategy_v2.life_liquidity.test_executor_protected_send import Connector, _setup
from test.hummingbot.strategy_v2.life_liquidity.test_protected_swap_send import connector_with_transport
from test.hummingbot.strategy_v2.life_liquidity.test_request_budget import _budget
from test.hummingbot.strategy_v2.life_liquidity.test_session import FakeClock, manager
from types import SimpleNamespace

import pytest

from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.data_type.common import OrderType, PositionAction, PositionMode, TradeType
from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
from hummingbot.strategy_v2.life_liquidity.config import PerpetualConfig
from hummingbot.strategy_v2.life_liquidity.market_data import LinearSwapContract
from hummingbot.strategy_v2.life_liquidity.protected_swap import ProtectedSwapExecutorSender, SwapAccountObservation
from hummingbot.strategy_v2.life_liquidity.safety import SafetyGate, SafetyObservation
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL
from hummingbot.strategy_v2.models.executor_actions import StopExecutorAction

D = Decimal


def setup_swap(tmp_path, *, mode=PositionMode.ONEWAY, action=PositionAction.CLOSE, side=TradeType.SELL, route=None):
    c, _, _, _, _, _ = _setup(tmp_path, recovery_account_uid="12345", quote_levels=3)
    clock = FakeClock()
    c._order_safety_manager = manager(tmp_path, clock)
    del c.allow_create_executor_actions
    cfg = PerpetualConfig(enabled=True, position_mode=mode.name, margin_mode="cross", leverage=1)
    c.config = c.config.model_copy(update={"strategy": c.config.strategy.model_copy(update={"perpetual": cfg})})
    account = {"value": SwapAccountObservation(
        "12345", "2", mode, "cross", 1, int(clock.wall.timestamp() * 1000),
        D("4") if mode == PositionMode.ONEWAY else D("0"),
        D("4") if mode == PositionMode.HEDGE else D("0"), D("4") if mode == PositionMode.HEDGE else D("0"),
        D("0"), D("0"), True)}
    if route is None:
        route = Connector()
        route.name = "okx_perpetual"
        route.position_mode = mode
        route._contract_sizes = {"LIFE-USDT": D("0.25")}
        route.get_leverage = lambda _: 1
    now = [clock.wall]
    budget = _budget(tmp_path, now, capacity=5)
    risk = {"allowed": True, "epoch": 1}
    wal = IntentWAL(tmp_path / "swap_wal.json")
    wal.initialize_empty()
    sender = ProtectedSwapExecutorSender(
        c, route, wal, LinearSwapContract.from_okx(_life_swap(), "LIFE-USDT"),
        tmp_path / "swap_actions.json", account_observation=lambda: account["value"],
        authorize_reservation=lambda _: risk["allowed"], risk_epoch=lambda: risk["epoch"],
        clock_ms=lambda: int(clock.wall.timestamp() * 1000), max_age_ms=1000,
        account_mode="2", request_budget=budget, create=True)
    c.install_protected_swap_sender(sender)
    # Only the production release switch is substituted for this isolated path.
    c.trading_permissions_ready = lambda: True
    config = OrderExecutorConfig(id="swap-1", controller_id="life", connector_name="okx_perpetual",
                                 trading_pair="LIFE-USDT", side=side, amount=D("0.5"), price=D("1"),
                                 position_action=action, execution_strategy=ExecutionStrategy.LIMIT_MAKER,
                                 leverage=1, level_id="0")
    return c, sender, route, wal, clock, account, risk, config


def wire_for(config, order_id, mode):
    wire = {"clOrdId": order_id, "tdMode": "cross", "ordType": "post_only", "instId": "LIFE-USDT-SWAP",
            "side": config.side.name.lower(), "sz": "2", "px": "1"}
    if mode == PositionMode.ONEWAY:
        wire["posSide"] = "net"
        if config.position_action == PositionAction.CLOSE:
            wire["reduceOnly"] = True
    else:
        wire["posSide"] = ("long" if config.side == TradeType.SELL else "short") if config.position_action == PositionAction.CLOSE else (
            "long" if config.side == TradeType.BUY else "short")
    return wire


@pytest.mark.parametrize("mode,side", [(PositionMode.ONEWAY, TradeType.SELL),
                                       (PositionMode.HEDGE, TradeType.SELL), (PositionMode.HEDGE, TradeType.BUY)])
def test_actual_executor_routes_one_wal_owned_swap_with_close_semantics(tmp_path, mode, side):
    c, sender, route, wal, clock, account, risk, config = setup_swap(tmp_path, mode=mode, side=side)
    action = sender.propose(config)
    runner = SimpleNamespace(controllers={"life": c}, logger=c.logger)
    assert StrategyV2Base._filter_authorized_actions(runner, [action]) == [action]
    strategy = SimpleNamespace(connectors={"okx_perpetual": route}, controllers={"life": c})
    executor = OrderExecutor(strategy, config)
    executor.get_order_price = lambda: config.price
    executor.place_open_order()
    assert len(route.sent) == 1
    sent = route.sent[0]
    assert wal.get(config.id).state == "SEND_UNKNOWN"
    assert sent["position_action"] == PositionAction.CLOSE
    sent["pre_send_check"](wire_for(config, sent["order_id"], mode))
    sent["on_ack"]("exchange-1")
    assert wal.get(config.id).state == "ACKED"
    assert sender.unresolved_order_ids() == (sent["order_id"],)
    with pytest.raises((PermissionError, ValueError)):
        executor.place_open_order()
    assert len(route.sent) == 1


@pytest.mark.parametrize("change", ["expiry", "risk_epoch", "risk", "uid", "stale", "mode", "margin", "leverage", "metadata", "shrink", "pending_close", "clock", "stop", "config", "storage", "stopped", "invalid_update", "runner_scope"])
def test_changed_evidence_revokes_final_swap_request_and_retains_wal(tmp_path, change):
    c, sender, route, wal, clock, account, risk, config = setup_swap(tmp_path)
    sender.propose(config)
    sender.submit(config, amount=config.amount, price=config.price, order_type=OrderType.LIMIT_MAKER)
    pending = route.sent[0]
    if change == "expiry":
        clock.advance(11)
    elif change == "risk_epoch":
        risk["epoch"] = 2
    elif change == "risk":
        risk["allowed"] = False
    elif change == "uid":
        account["value"] = replace(account["value"], account_uid="99999")
    elif change == "stale":
        clock.advance(2)
    elif change == "mode":
        route.position_mode = PositionMode.HEDGE
    elif change == "margin":
        account["value"] = replace(account["value"], margin_mode="isolated")
    elif change == "leverage":
        account["value"] = replace(account["value"], leverage=2)
    elif change == "metadata":
        route._contract_sizes["LIFE-USDT"] = D("0.5")
    elif change == "shrink":
        account["value"] = replace(account["value"], net_contracts=D("1"))
    elif change == "pending_close":
        account["value"] = replace(account["value"], pending_close_sell_contracts=D("3"))
    elif change == "clock":
        clock.advance(-1)  # FakeClock intentionally permits this fault.
    elif change == "stop":
        c.on_runner_stop_action(StopExecutorAction(controller_id="life", executor_id=config.id))
    elif change == "config":
        c.config = c.config.model_copy(update={"recovery_account_uid": "99999"})
    elif change == "storage":
        sender.journal.path.unlink()
    elif change == "stopped":
        c._order_safety_stopped = True
    elif change == "invalid_update":
        c.config_update_state.reject("CONFIG_LOAD_FAILED")
    elif change == "runner_scope":
        c._runner_scope_invalid = True
    with pytest.raises(PermissionError):
        pending["pre_send_check"](wire_for(config, pending["order_id"], PositionMode.ONEWAY))
    assert wal.get(config.id).state in ("SEND_UNKNOWN", "ACKED")
    assert sender.unresolved_order_ids() == (pending["order_id"],)


@pytest.mark.parametrize("fault", ["halt", "stale", "journal"])
def test_installed_runtime_safety_revokes_swap_after_dispatch(tmp_path, fault):
    c, sender, route, wal, clock, account, risk, config = setup_swap(tmp_path)
    now = {"ms": 100}
    gate = SafetyGate(tmp_path / "swap_safety.json", max_drawdown_bps=D("500"),
                      min_margin_buffer_quote=D("0"), stable_data_ms=0, recovery_probe_base=D("1"))
    gate.initialize_empty()
    c.install_runtime_risk_gate(
        gate, observation=lambda: SafetyObservation(100, True, True, True, True, D("0"), D("100")),
        monotonic_clock_ms=lambda: now["ms"], max_observation_age_ms=5)
    c._runtime_risk_ready()
    assert c._runtime_risk_ready()
    sender.propose(config)
    sender.submit(config, amount=config.amount, price=config.price, order_type=OrderType.LIMIT_MAKER)
    if fault == "halt":
        gate.halt("MANUAL_KILL_SWITCH")
    elif fault == "stale":
        now["ms"] = 106
    else:
        gate.path.unlink()
    sent = route.sent[0]
    with pytest.raises(PermissionError):
        sent["pre_send_check"](wire_for(config, sent["order_id"], PositionMode.ONEWAY))
    assert wal.get(config.id).state == "SEND_UNKNOWN"


@pytest.mark.parametrize("change", ["sz", "px", "posSide", "reduceOnly", "extra"])
def test_changed_swap_wire_is_rejected_before_io(tmp_path, change):
    c, sender, route, wal, clock, account, risk, config = setup_swap(tmp_path)
    sender.propose(config)
    sender.submit(config, amount=config.amount, price=config.price, order_type=OrderType.LIMIT_MAKER)
    sent = route.sent[0]
    wire = wire_for(config, sent["order_id"], PositionMode.ONEWAY)
    wire[change] = "wrong"
    with pytest.raises(PermissionError, match="ORDER_CHANGED"):
        sent["pre_send_check"](wire)


def test_swap_sender_cannot_issue_under_default_production_permission(tmp_path):
    c, sender, *_rest, config = setup_swap(tmp_path)
    del c.trading_permissions_ready
    with pytest.raises(PermissionError):
        sender.propose(config)


def test_swap_restore_retains_unknown_claim_and_cannot_resume_or_bypass_it(tmp_path):
    c, sender, route, wal, clock, account, risk, config = setup_swap(tmp_path)
    sender.propose(config)
    sender.submit(config, amount=config.amount, price=config.price, order_type=OrderType.LIMIT_MAKER)
    restored = ProtectedSwapExecutorSender(
        c, route, IntentWAL(wal.path), sender.contract, sender.journal.path,
        account_observation=sender.account_observation, authorize_reservation=sender.authorize_reservation,
        risk_epoch=sender.risk_epoch, clock_ms=sender.clock_ms, max_age_ms=sender.max_age_ms,
        account_mode=sender.account_mode, request_budget=sender.request_budget, create=False)
    assert restored.unresolved_order_ids() == sender.unresolved_order_ids()
    assert not restored.authorizes_config(config)
    with pytest.raises(PermissionError):
        restored.propose(config.model_copy(update={"id": "bypass", "level_id": "1"}))
    assert len(route.sent) == 1


def test_pending_close_claims_and_external_orders_cannot_double_spend_position(tmp_path):
    c, sender, route, wal, clock, account, risk, config = setup_swap(tmp_path)
    sender.propose(config)  # 2 of 4 contracts claimed locally before any network send.
    account["value"] = replace(account["value"], pending_close_sell_contracts=D("1"))
    with pytest.raises(PermissionError):
        sender.propose(config.model_copy(update={"id": "second", "level_id": "1"}))
    assert len(wal.all_records()) == 1
    assert route.sent == []


def test_two_durable_close_claims_are_order_independent_and_cannot_exceed_actual_position(tmp_path):
    c, sender, route, wal, clock, account, risk, config = setup_swap(tmp_path)
    first = config.model_copy(update={"id": "zebra"})
    second = config.model_copy(update={"id": "aardvark", "level_id": "1"})
    sender.propose(first)
    sender.propose(second)
    with pytest.raises(PermissionError):
        sender.propose(config.model_copy(update={"id": "third", "level_id": "2"}))
    for child in (first, second):
        sender.submit(child, amount=child.amount, price=child.price, order_type=OrderType.LIMIT_MAKER)
        sent = route.sent[-1]
        sent["pre_send_check"](wire_for(child, sent["order_id"], PositionMode.ONEWAY))
    assert len(route.sent) == 2


@pytest.mark.parametrize("mode,side", [
    (PositionMode.ONEWAY, TradeType.BUY), (PositionMode.HEDGE, TradeType.BUY), (PositionMode.HEDGE, TradeType.SELL)])
def test_open_permit_exposes_signed_product_units_to_capital_authority(tmp_path, mode, side):
    c, sender, route, wal, clock, account, risk, config = setup_swap(
        tmp_path, mode=mode, side=side, action=PositionAction.OPEN)
    approved = []

    def authority(permit):
        approved.append(permit)
        return risk["allowed"]

    sender.authorize_reservation = authority
    sender.propose(config)
    sender.submit(config, amount=config.amount, price=config.price, order_type=OrderType.LIMIT_MAKER)
    sent = route.sent[0]
    sent["pre_send_check"](wire_for(config, sent["order_id"], mode))
    assert all(p.side == side and p.position_action == PositionAction.OPEN and p.contracts == D("2")
               and p.quantity_base == D("0.5") and p.account_uid == "12345"
               and p.instrument == "LIFE-USDT-SWAP" for p in approved)
    sent["on_ack"]("open-ack")
    assert wal.get(config.id).state == "ACKED"


@pytest.mark.parametrize("change", ["account_mode", "future", "nan", "negative_pending", "no_reservation", "leveraged", "flat", "wrong_side"])
def test_invalid_initial_swap_evidence_creates_no_action_or_order(tmp_path, change):
    c, sender, route, wal, clock, account, risk, config = setup_swap(tmp_path)
    obs = account["value"]
    changes = {"account_mode": {"account_mode": "1"}, "future": {"observed_at_ms": obs.observed_at_ms + 1},
               "nan": {"net_contracts": D("NaN")}, "negative_pending": {"pending_close_sell_contracts": D("-1")},
               "leveraged": {"leverage": 2}, "flat": {"net_contracts": D("0")}, "wrong_side": {"net_contracts": D("-4")}}
    if change == "no_reservation":
        risk["allowed"] = False
    else:
        account["value"] = replace(obs, **changes[change])
    with pytest.raises(PermissionError):
        sender.propose(config)
    assert wal.all_records() == () and route.sent == []


def test_ambiguous_connector_failure_keeps_claim_and_forbids_retry(tmp_path):
    c, sender, route, wal, clock, account, risk, config = setup_swap(tmp_path)
    sender.propose(config)

    def fail(_):
        raise TimeoutError("unknown transport outcome")

    route.before_send = fail
    with pytest.raises(TimeoutError):
        sender.submit(config, amount=config.amount, price=config.price, order_type=OrderType.LIMIT_MAKER)
    assert wal.get(config.id).state == "SEND_UNKNOWN"
    with pytest.raises(PermissionError):
        sender.submit(config, amount=config.amount, price=config.price, order_type=OrderType.LIMIT_MAKER)
    assert len(route.sent) == 1 and sender.unresolved_order_ids()


@pytest.mark.parametrize("fault", ["wal_deleted", "wal_cancelled", "ack_before_send", "mutated_config"])
def test_durable_or_identity_faults_cannot_reach_swap_io(tmp_path, fault):
    c, sender, route, wal, clock, account, risk, config = setup_swap(tmp_path)
    sender.propose(config)
    sender.submit(config, amount=config.amount, price=config.price, order_type=OrderType.LIMIT_MAKER)
    sent = route.sent[0]
    if fault == "ack_before_send":
        with pytest.raises(PermissionError, match="ACK_WITHOUT_SEND"):
            sent["on_ack"]("not-sent")
        assert wal.get(config.id).state == "SEND_UNKNOWN"
        return
    if fault == "wal_deleted":
        wal.path.unlink()
    elif fault == "wal_cancelled":
        wal.mark_cancel_requested(config.id)
    else:
        config.price = D("2")
    with pytest.raises(PermissionError):
        sent["pre_send_check"](wire_for(config, sent["order_id"], PositionMode.ONEWAY))
    assert wal.get(config.id).state == "SEND_UNKNOWN"


def test_shared_request_budget_preserves_cancel_capacity_and_forbids_second_boundary_attempt(tmp_path):
    c, sender, route, wal, clock, account, risk, config = setup_swap(tmp_path)
    sender.propose(config)
    for number in range(4):
        sender.request_budget.charge("STATUS", f"spot-status-{number}")
    sender.submit(config, amount=config.amount, price=config.price, order_type=OrderType.LIMIT_MAKER)
    sent = route.sent[0]
    with pytest.raises(PermissionError, match="REQUEST_BUDGET"):
        sent["pre_send_check"](wire_for(config, sent["order_id"], PositionMode.ONEWAY))
    sender.request_budget.charge("CANCEL", "still-reserved")
    with pytest.raises(PermissionError, match="RECONCILE"):
        sent["pre_send_check"](wire_for(config, sent["order_id"], PositionMode.ONEWAY))
    with pytest.raises(PermissionError, match="ACK_WITHOUT_SEND"):
        sent["on_ack"]("not-sent")


def test_action_checkpoint_failure_retains_orphan_wal_and_blocks_further_proposals(tmp_path, monkeypatch):
    c, sender, route, wal, clock, account, risk, config = setup_swap(tmp_path)
    commit = sender.journal.commit

    def fail_new_action(state):
        if state["actions"]:
            raise OSError("synthetic disk failure")
        commit(state)

    monkeypatch.setattr(sender.journal, "commit", fail_new_action)
    with pytest.raises(OSError):
        sender.propose(config)
    assert wal.get(config.id).state == "PREPARED"
    with pytest.raises(PermissionError):
        sender.propose(config.model_copy(update={"id": "second", "level_id": "1"}))
    assert route.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("revoke", [False, True])
async def test_real_executor_scheduler_and_rest_boundary_preserve_swap_authority(tmp_path, monkeypatch, revoke):
    connector, throttle, requests = connector_with_transport()
    connector._perpetual_trading.set_leverage("LIFE-USDT", 1)
    connector._trading_rules["LIFE-USDT"] = TradingRule(
        "LIFE-USDT", min_order_size=D("0.25"), min_price_increment=D("0.0001"),
        min_base_amount_increment=D("0.025"), buy_order_collateral_token="USDT", sell_order_collateral_token="USDT")
    tasks = []

    def schedule(coroutine):
        task = asyncio.create_task(coroutine)
        tasks.append(task)
        return task

    monkeypatch.setattr("hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative.safe_ensure_future", schedule)
    c, sender, route, wal, clock, account, risk, config = setup_swap(tmp_path, route=connector)
    action = sender.propose(config)
    runner = SimpleNamespace(controllers={"life": c}, logger=c.logger)
    assert StrategyV2Base._filter_authorized_actions(runner, [action]) == [action]
    executor = OrderExecutor(SimpleNamespace(connectors={"okx_perpetual": connector}, controllers={"life": c}), config)
    executor.get_order_price = lambda: config.price
    executor.place_open_order()
    try:
        await asyncio.wait_for(throttle.entered.wait(), 2)
        assert requests == [] and wal.get(config.id).state == "SEND_UNKNOWN"
        if revoke:
            risk["allowed"] = False
        throttle.release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 2)
        assert len(requests) == (0 if revoke else 1)
        assert wal.get(config.id).state == ("SEND_UNKNOWN" if revoke else "ACKED")
        assert sender.unresolved_order_ids()
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
