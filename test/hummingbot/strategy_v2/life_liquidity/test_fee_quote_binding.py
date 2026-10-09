"""Account-specific fees are rechecked at the LIFE quote and final send gates."""

from dataclasses import replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_executor_protected_send import _setup
from test.hummingbot.strategy_v2.life_liquidity.test_fees import SPOT_INSTRUMENT, fee_response
from test.hummingbot.strategy_v2.life_liquidity.test_final_quote_send import _attach_quote_planner

import pytest

from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
from hummingbot.strategy_v2.life_liquidity.fees import FeeDataError, FeeQuoteBinding, FeeRateSnapshot
from hummingbot.strategy_v2.life_liquidity.spot_quotes import QuoteCosts


def _fee(*, maker="-0.0008", taker="-0.001", account="12345", ts="2000000"):
    return FeeRateSnapshot.from_okx(
        fee_response(maker=maker, taker=taker, ts=ts),
        account_id=account, connector_name="okx",
        instrument=SPOT_INSTRUMENT, notional_currency="USDT")


def _binding(state, *, policy="pause", ceiling=None):
    return FeeQuoteBinding(
        snapshot=lambda: state["fee"], exchange_now_ms=lambda: state["now"],
        account_id="12345", connector_name="okx", instrument_id="LIFE-USDT",
        group_id="1", max_age_ms=1000, fee_policy=policy,
        fee_ceiling_bps=ceiling)


def _costs(maker="0.0008", exit_rate="0.001"):
    return QuoteCosts(Decimal(maker), Decimal(exit_rate),
                      Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"))


def test_fresh_exact_account_fee_bounds_quote_costs_and_rebates_are_not_credited():
    state = {"now": 2000500, "fee": _fee(maker="0.0002")}
    binding = _binding(state)
    assert binding.evaluate(_costs(maker="0", exit_rate="0.001")).allowed
    assert binding.evaluate(_costs(maker="0", exit_rate="0")).reason_code == "FEE_COST_UNDERSTATED"
    assert not binding.evaluate(_costs(maker="-0.0002")).allowed


@pytest.mark.parametrize("change,reason", [
    ("wrong_account", "FEE_IDENTITY_MISMATCH"),
    ("stale", "FEE_RATE_STALE"),
    ("future", "FEE_RATE_STALE"),
    ("missing", "FEE_RATE_UNAVAILABLE"),
    ("malformed", "FEE_SNAPSHOT_INVALID"),
    ("clock", "FEE_CLOCK_INVALID"),
])
def test_pause_policy_blocks_unqualified_fee_snapshot(change, reason):
    state = {"now": 2000500, "fee": _fee()}
    if change == "wrong_account":
        state["fee"] = _fee(account="other")
    elif change == "stale":
        state["now"] = 2001001
    elif change == "future":
        state["now"] = 1999999
    elif change == "missing":
        state["fee"] = None
    elif change == "malformed":
        state["fee"] = replace(state["fee"], exchange_timestamp_ms="bad")
    else:
        state["now"] = 0
    assert _binding(state).evaluate(_costs()).reason_code == reason


def test_explicit_ceiling_fallback_never_hides_wrong_account_or_higher_actual_fee():
    state = {"now": 2001001, "fee": _fee()}
    binding = _binding(state, policy="conservative_ceiling", ceiling=Decimal("20"))
    assert binding.evaluate(_costs("0.002", "0.002")).reason_code == "FEE_CEILING_FALLBACK"
    assert binding.evaluate(_costs()).reason_code == "FEE_COST_UNDERSTATED"
    state["fee"] = _fee(account="other")
    assert binding.evaluate(_costs("0.002", "0.002")).reason_code == "FEE_IDENTITY_MISMATCH"
    state["now"] = 2000500
    state["fee"] = _fee(taker="-0.003")
    assert binding.evaluate(_costs("0.003", "0.003")).reason_code == "FEE_CEILING_BREACHED"


def test_missing_fee_data_cannot_use_zero_as_a_conservative_ceiling():
    with pytest.raises(FeeDataError, match="FEE_QUOTE_POLICY_INVALID"):
        _binding({"now": 2000500, "fee": None},
                 policy="conservative_ceiling", ceiling=Decimal("0"))


def _queued_fee_quote(tmp_path):
    controller, template, connector, wal, reservations, _ = _setup(
        tmp_path, recovery_account_uid="12345")
    economics = controller.config.strategy.economics.model_copy(update={
        "fee_policy": "pause", "fee_max_age": "1s"})
    controller.config = controller.config.model_copy(update={
        "strategy": controller.config.strategy.model_copy(update={"economics": economics})})
    state = {"now": 2000500, "fee": _fee()}
    _, _ = _attach_quote_planner(
        controller, template, wal, reservations,
        fee_binding=_binding(state), propose=False)
    planner = controller._quote_action_planner
    quote_state = planner.snapshot()
    planner.snapshot = lambda: replace(quote_state, costs=_costs())
    actions = controller.determine_executor_actions()
    assert len(actions) == 1
    executor = OrderExecutor(template._strategy, actions[0].executor_config)
    executor.get_order_price = lambda: executor.config.price
    executor.place_open_order()
    wire = {"clOrdId": executor._order.order_id, "instId": "LIFE-USDT",
            "side": "buy", "ordType": "post_only", "tdMode": "cash",
            "px": str(executor.config.price), "sz": str(executor.config.amount)}
    return controller, connector, wal, reservations, executor, state, wire


@pytest.mark.parametrize("change,reason", [
    ("increased", "FEE_COST_UNDERSTATED"),
    ("stale", "FEE_RATE_STALE"),
    ("wrong_account", "FEE_IDENTITY_MISMATCH"),
    ("missing", "FEE_RATE_UNAVAILABLE"),
])
def test_fee_change_revokes_queued_quote_at_final_wire_check(tmp_path, change, reason):
    controller, connector, wal, reservations, executor, state, wire = _queued_fee_quote(tmp_path)
    check = connector.sent[0]["pre_send_check"]
    if change == "increased":
        state["fee"] = _fee(maker="-0.02")
    elif change == "stale":
        state["now"] = 2001001
    elif change == "wrong_account":
        state["fee"] = _fee(account="other")
    else:
        state["fee"] = None

    with pytest.raises(PermissionError, match="SEND_PERMISSION_REVOKED"):
        check(wire)
    assert controller._quote_action_planner.fee_reason_code == reason
    assert wal.get(executor.config.id).state == "SEND_UNKNOWN"
    assert reservations.has_open_intent(executor.config.id)


def test_fee_policy_config_change_revokes_queued_quote(tmp_path):
    controller, connector, wal, reservations, executor, _, wire = _queued_fee_quote(tmp_path)
    economics = controller.config.strategy.economics.model_copy(update={
        "fee_policy": "conservative_ceiling", "fee_ceiling_bps": Decimal("20")})
    controller.config = controller.config.model_copy(update={
        "strategy": controller.config.strategy.model_copy(update={"economics": economics})})

    with pytest.raises(PermissionError, match="SEND_PERMISSION_REVOKED"):
        connector.sent[0]["pre_send_check"](wire)
    assert controller._quote_action_planner.fee_reason_code == "FEE_BINDING_CONFIG_MISMATCH"
    assert wal.get(executor.config.id).state == "SEND_UNKNOWN"
    assert reservations.has_open_intent(executor.config.id)
