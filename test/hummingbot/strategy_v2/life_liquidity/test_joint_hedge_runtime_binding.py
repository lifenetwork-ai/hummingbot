"""Opt-in LIFE spot send is revoked when enabled SWAP hedge becomes unavailable."""

from dataclasses import replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_executor_protected_send import _setup
from test.hummingbot.strategy_v2.life_liquidity.test_final_quote_send import _attach_quote_planner
from test.hummingbot.strategy_v2.life_liquidity.test_hedge import (
    _observation as hedge_observation,
    _policy as hedge_policy,
)
from test.hummingbot.strategy_v2.life_liquidity.test_joint_exposure import _limits, _observation
from types import SimpleNamespace

import pytest

from hummingbot.strategy_v2.life_liquidity.joint_exposure import LinearLifeContractSpec
from hummingbot.strategy_v2.life_liquidity.market_data import LinearSwapContract

D = Decimal


def _enabled_controller(tmp_path):
    controller, template, connector, wal, reservations, _ = _setup(tmp_path)
    perpetual = controller.config.strategy.perpetual.model_copy(update={
        "enabled": True, "position_mode": "ONEWAY", "margin_mode": "cross", "leverage": 1})
    controller.config = controller.config.model_copy(update={
        "strategy": controller.config.strategy.model_copy(update={"perpetual": perpetual})})
    controller.perpetual_contract = LinearSwapContract(
        instrument="LIFE-USDT-SWAP", trading_pair="LIFE-USDT",
        contract_value_life=D("0.5"), lot_size_contracts=D("0.1"),
        min_size_contracts=D("0.1"), tick_size_usdt=D("0.01"))
    del controller.allow_create_executor_actions
    controller._spot_quote_gates_ready = lambda: True
    controller.order_safety_watchdog_task = SimpleNamespace(done=lambda: False)
    return controller, template, connector, wal, reservations


def test_enabled_perpetual_without_joint_and_hedge_evidence_blocks_spot(tmp_path):
    controller, _, _, _, _ = _enabled_controller(tmp_path)
    assert not controller.allow_create_executor_actions()
    assert controller.joint_risk_reason_code == "JOINT_RISK_NOT_INSTALLED"


def test_swap_disconnect_revokes_queued_spot_at_final_wire_check(tmp_path):
    controller, template, connector, wal, reservations = _enabled_controller(tmp_path)
    contract = LinearLifeContractSpec(D("0.5"), D("0.1"))
    state = {"joint": _observation(),
             "hedge": hedge_observation(perp_contracts_signed=D("-20"))}

    def joint_observation():
        preview = reservations.preview()
        return replace(state["joint"], spot_life_base=reservations.life_balance,
                       spot_buy_pending_base=preview.unresolved_quantity_base("BUY"),
                       spot_sell_pending_base=preview.unresolved_quantity_base("SELL"))

    controller.install_joint_risk_gate(contract, _limits(), observation=joint_observation)
    controller.install_hedge_gate(hedge_policy(), observation=lambda: state["hedge"])
    assert controller.allow_create_executor_actions()
    _, executor = _attach_quote_planner(controller, template, wal, reservations)
    executor.place_open_order()
    wire = {"clOrdId": executor._order.order_id, "instId": "LIFE-USDT",
            "side": "buy", "ordType": "post_only", "tdMode": "cash",
            "px": str(executor.config.price), "sz": str(executor.config.amount)}
    state["joint"] = replace(state["joint"], perp_contracts_signed=D("-16"))
    state["hedge"] = replace(state["hedge"], perp_contracts_signed=D("-16"),
                             connector_ready=False)
    with pytest.raises(PermissionError, match="SEND_PERMISSION_REVOKED"):
        connector.sent[0]["pre_send_check"](wire)
    assert controller.hedge_reason_code == "HEDGE_CONNECTOR_UNAVAILABLE"
    assert wal.get(executor.config.id).state == "SEND_UNKNOWN"
    assert reservations.has_open_intent(executor.config.id)


def test_joint_gate_rejects_spot_observer_that_omits_new_reservation(tmp_path):
    controller, template, connector, wal, reservations = _enabled_controller(tmp_path)
    contract = LinearLifeContractSpec(D("0.5"), D("0.1"))
    controller.install_joint_risk_gate(contract, _limits(), observation=lambda: _observation())
    controller.install_hedge_gate(
        hedge_policy(), observation=lambda: hedge_observation(perp_contracts_signed=D("-20")))
    _, executor = _attach_quote_planner(controller, template, wal, reservations)
    with pytest.raises(PermissionError, match="SEND_PERMISSION_REVOKED"):
        executor.place_open_order()
    assert controller.joint_risk_reason_code == "JOINT_SPOT_RESERVATION_MISMATCH"
    assert connector.sent == []
    assert wal.get(executor.config.id).state == "ABORTED_BEFORE_SEND"
