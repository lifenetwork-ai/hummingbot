"""Subsidy holds require a complete, physically flat reconciled fill cycle."""

import json
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_reconciled_fill_attribution import AT, AT_MS, _attributor
from test.hummingbot.strategy_v2.life_liquidity.test_runner_fill_events import _setup
from unittest.mock import patch

import pytest

from hummingbot.strategy_v2.life_liquidity.config import SubsidyBudgetConfig
from hummingbot.strategy_v2.life_liquidity.economics import SubsidyBudgetLedger
from hummingbot.strategy_v2.life_liquidity.fill_attribution import IndependentFillObservation
from hummingbot.strategy_v2.life_liquidity.order_gateway import SpotFill, SpotReservationReconciler
from hummingbot.strategy_v2.life_liquidity.risk import SpotIntent

D = Decimal


def cycle(tmp_path, *, life_fee=False, exit_price="0.8"):
    controller, _, wal, reservations = _setup(tmp_path)
    first = wal.get("i1")
    subsidy = SubsidyBudgetLedger(
        tmp_path / "subsidy.json", campaign_id="life", campaign_limit_quote=D("1"),
        day_limit_quote=D("1"), session_limit_quote=D("1"))
    subsidy.initialize_empty()
    quantity = D("0.39") if life_fee else D("0.4")
    wal.begin("i2", client_order_id="wire-2", session_id=first.session_id,
              epoch=first.epoch, reservation_id="i2", slot_market="LIFE-USDT",
              slot_side="SELL", slot_level=0)
    assert reservations.reserve(SpotIntent(
        "i2", "SELL", quantity, D(exit_price), first.session_id, first.epoch),
        reference_price=D("1")).allowed
    for intent in ("i1", "i2"):
        assert subsidy.reserve(intent, D("0.2"), session_id=first.session_id, at_utc=AT)
    attribution, loss = _attributor(tmp_path, wal, reservations, subsidy=subsidy)
    attribution.independent_value = lambda fill: IndependentFillObservation(
        fill.price_usdt, fill.fill_at_ms, fill.fill_at_ms, "independent_market")
    reconciler = SpotReservationReconciler(wal, reservations, require_fees=True)
    fills = (
        SpotFill("entry", D("0.4"), D("1"), "LIFE" if life_fee else "USDT", D("-0.01"), AT_MS),
        SpotFill("exit", quantity, D(exit_price), "USDT", D("-0.01"), AT_MS + 1))
    for intent, wire, fill in zip(("i1", "i2"), ("wire-1", "wire-2"), fills):
        assert reconciler.apply_fills(wire, (fill,), fill.quantity_base)
        assert attribution.apply(wire, (fill,))
        wal.mark_terminal(intent, f"exchange-{intent}")
        reservations.confirm_terminal(intent, cumulative_filled=fill.quantity_base,
                                      fills_reconciled=True, exchange_state="CANCELED")
    return attribution, subsidy, loss, controller


@pytest.mark.parametrize("life_fee,expected", [(False, "0.10"), (True, "0.098")])
def test_flat_cycle_settles_realized_inventory_loss_and_replays_once(tmp_path, life_fee, expected):
    attribution, subsidy, _, _ = cycle(tmp_path, life_fee=life_fee)
    assert subsidy.campaign_committed_quote == D("0.4")
    assert attribution.settle_inventory_cycle("cycle-1", ("i1", "i2"))
    assert subsidy.campaign_committed_quote == D(expected)
    assert attribution.ready()
    assert attribution.recover()
    assert not attribution.settle_inventory_cycle("cycle-1", ("i1", "i2"))
    restored_subsidy = SubsidyBudgetLedger(
        subsidy.path, campaign_id="life", campaign_limit_quote=D("1"),
        day_limit_quote=D("1"), session_limit_quote=D("1"))
    restored_reservations = type(attribution.reservations).restore(
        attribution.reservations.path, limits=attribution.reservations.limits)
    restored, _ = _attributor(
        tmp_path, attribution.wal, restored_reservations, restore=True, subsidy=restored_subsidy)
    assert restored.ready()
    assert restored_subsidy.campaign_committed_quote == D(expected)
    with pytest.raises(ValueError, match="SUBSIDY_CYCLE_ALREADY_SETTLED"):
        restored.settle_inventory_cycle("another-cycle", ("i1", "i2"))


def test_terminal_entry_alone_cannot_release_inventory_hold(tmp_path):
    attribution, subsidy, _, _ = cycle(tmp_path)
    with pytest.raises(ValueError, match="SUBSIDY_INVENTORY_NOT_FLAT"):
        attribution.settle_inventory_cycle("entry-only", ("i1",))
    assert subsidy.campaign_committed_quote == D("0.4")


def test_profitable_cycle_does_not_refund_independent_fill_loss_floors(tmp_path):
    attribution, subsidy, _, _ = cycle(tmp_path, exit_price="1.1")
    assert attribution.capital().usdt_balance == D("10.02")
    assert attribution.settle_inventory_cycle("profitable-cycle", ("i1", "i2"))
    assert subsidy.campaign_committed_quote == D("0.02")
    assert attribution.ready()


def test_cycle_requires_durable_terminal_and_attributed_fill_proof(tmp_path):
    attribution, subsidy, _, _ = cycle(tmp_path)
    data = json.loads(attribution.wal.path.read_text())
    # A different writer corrupting the WAL must not authorize any release.
    attribution.wal.path.write_text("{}")
    with pytest.raises(ValueError, match="SUBSIDY_CYCLE_EVIDENCE_UNAVAILABLE"):
        attribution.settle_inventory_cycle("cycle-1", ("i1", "i2"))
    assert subsidy.campaign_committed_quote == D("0.4")
    attribution.wal.path.write_text(json.dumps(data))
    assert attribution.ready()


def test_cycle_checkpoint_failure_retains_hold_or_restores_single_settlement(tmp_path):
    attribution, subsidy, _, _ = cycle(tmp_path)
    real_save = subsidy._save

    def write_then_fail(entries):
        real_save(entries)
        raise OSError("ambiguous settlement checkpoint")

    with patch.object(subsidy, "_save", side_effect=write_then_fail):
        with pytest.raises(OSError):
            attribution.settle_inventory_cycle("cycle-1", ("i1", "i2"))
    assert not attribution.ready()
    restored_subsidy = SubsidyBudgetLedger(
        subsidy.path, campaign_id="life", campaign_limit_quote=D("1"),
        day_limit_quote=D("1"), session_limit_quote=D("1"))
    restored_reservations = type(attribution.reservations).restore(
        attribution.reservations.path, limits=attribution.reservations.limits)
    restored, _ = _attributor(
        tmp_path, attribution.wal, restored_reservations, restore=True, subsidy=restored_subsidy)
    assert restored.ready()
    assert not restored.settle_inventory_cycle("cycle-1", ("i1", "i2"))


def test_failed_cycle_write_keeps_every_hold_and_allows_verified_retry(tmp_path):
    attribution, subsidy, _, _ = cycle(tmp_path)
    with patch.object(subsidy, "_save", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            attribution.settle_inventory_cycle("cycle-1", ("i1", "i2"))
    assert subsidy.campaign_committed_quote == D("0.4")
    assert attribution.ready()
    assert attribution.settle_inventory_cycle("cycle-1", ("i1", "i2"))


@pytest.mark.parametrize("change", ["proof", "cost", "member"])
def test_restored_cycle_requires_exact_recomputed_fill_proof(tmp_path, change):
    attribution, subsidy, _, _ = cycle(tmp_path)
    assert attribution.settle_inventory_cycle("cycle-1", ("i1", "i2"))
    data = json.loads(subsidy.path.read_text())
    for entry in data["entries"].values():
        proof = entry["inventory_cycle"]
        if change == "proof":
            proof["proof"] = "0" * 64
        elif change == "cost":
            proof["actuals"]["i2"] = "0.01"
        else:
            proof["members"] = ["i1"]
    if change == "cost":
        data["entries"]["i2"]["actual"] = "0.01"
    subsidy.path.write_text(json.dumps(data))
    if change == "member":
        with pytest.raises(ValueError):
            SubsidyBudgetLedger(
                subsidy.path, campaign_id="life", campaign_limit_quote=D("1"),
                day_limit_quote=D("1"), session_limit_quote=D("1"))
    else:
        attribution.subsidy_budget = SubsidyBudgetLedger(
            subsidy.path, campaign_id="life", campaign_limit_quote=D("1"),
            day_limit_quote=D("1"), session_limit_quote=D("1"))
        assert not attribution.ready()
        with pytest.raises(ValueError, match="SUBSIDY_CYCLE_EVIDENCE_UNAVAILABLE"):
            attribution.settle_inventory_cycle("cycle-1", ("i1", "i2"))


def test_settled_fill_floor_cannot_change_after_inventory_release(tmp_path):
    attribution, subsidy, _, _ = cycle(tmp_path)
    assert attribution.settle_inventory_cycle("cycle-1", ("i1", "i2"))
    assert not subsidy.record_fill_floor("i1", D("0.01"))
    with pytest.raises(ValueError, match="SUBSIDY_ALREADY_SETTLED"):
        subsidy.record_fill_floor("i1", D("0.02"))
    assert subsidy.campaign_committed_quote == D("0.1")


def test_inventory_settlement_cannot_charge_a_different_session_budget(tmp_path):
    attribution, subsidy, _, _ = cycle(tmp_path)
    data = json.loads(subsidy.path.read_text())
    data["entries"]["i2"]["session_id"] = "unrelated-session"
    subsidy.path.write_text(json.dumps(data))
    attribution.subsidy_budget = SubsidyBudgetLedger(
        subsidy.path, campaign_id="life", campaign_limit_quote=D("1"),
        day_limit_quote=D("1"), session_limit_quote=D("1"))
    assert not attribution.ready()
    with pytest.raises(ValueError, match="SUBSIDY_CYCLE_EVIDENCE_UNAVAILABLE"):
        attribution.settle_inventory_cycle("cycle-1", ("i1", "i2"))


@pytest.mark.asyncio
async def test_controller_requires_fresh_complete_account_scope_before_cycle_release(tmp_path):
    attribution, subsidy, loss, controller = cycle(tmp_path)
    economics = controller.config.strategy.economics.model_copy(update={
        "objective": "liquidity_service",
        "subsidy_budget_quote": SubsidyBudgetConfig(campaign=D("1"), day=D("1"), session=D("1"))})
    controller.config = controller.config.model_copy(update={
        "strategy": controller.config.strategy.model_copy(update={"economics": economics})})
    controller.install_execution_loss_budget(loss, utc_clock=lambda: AT)
    controller.install_fill_attributor(attribution)
    connector = controller._order_safety_gateway.connector
    for intent, wire, trade, price in (("i1", "wire-1", "entry", "1"),
                                       ("i2", "wire-2", "exit", "0.8")):
        exchange_id = f"exchange-{intent}"
        connector.status[wire] = {"clOrdId": wire, "ordId": exchange_id,
                                  "state": "canceled", "accFillSz": "0.4"}
        connector.fills[exchange_id] = [{
            "tradeId": trade, "ordId": exchange_id, "fillSz": "0.4", "fillPx": price,
            "feeCcy": "USDT", "fee": "-0.01", "fillTime": str(AT_MS + (intent == "i2"))}]
    connector.cash_balances = {"LIFE": "10", "USDT": "9.90"}
    connector.open_pages[None] = [{"clOrdId": "foreign", "ordId": "manual",
                                   "instId": "BTC-USDT", "state": "live"}]
    with pytest.raises(ValueError, match="SUBSIDY_ACCOUNT_SCOPE_UNAVAILABLE"):
        await controller.settle_service_inventory("cycle-1", ("i1", "i2"))
    assert subsidy.campaign_committed_quote == D("0.4")
    connector.open_pages[None] = []
    assert await controller.settle_service_inventory("cycle-1", ("i1", "i2"))
    assert subsidy.campaign_committed_quote == D("0.1")
