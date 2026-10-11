"""One actual controller with spot, shared SWAP, hedge, carry and inventory."""
from decimal import Decimal
from random import Random
from test.hummingbot.strategy_v2.life_liquidity.o7_scenarios import publish, send, system

import pytest

from controllers.generic.life_liquidity import LifeLiquidityController
from hummingbot.strategy_v2.life_liquidity.carry_monitor import FundingPayment

D = Decimal


@pytest.mark.asyncio
@pytest.mark.parametrize("seed", [7, 19])
async def test_combined_mm_hedge_funding_inventory_and_mm_return(tmp_path, seed):
    async with system(tmp_path) as f:
        c, pool, sender, spot, swap, wal, clock, account, feed, strategy, sc, pc = f.v
        modes = [f.inventory._state()["mode"]]
        quote = c._quote_action_planner
        quote.intent_id_factory = lambda: "mm-primary"
        actions = quote.propose()
        assert len(actions) == 1, (quote.reason_code, c.runtime_risk_reason_code, c.hedge_reason_code, c._permission_gates)
        mm, _ = send(f, actions[0])
        assert mm.side == "BUY" and mm.price_usdt == D("0.99")
        publish(f, mm, life="10.5", cash="9.504", long="1")
        c._runner_halt_ok = c._halt_runner_orders()
        assert await f.joint.reconcile(), f.joint.reason_code
        await c._order_safety_gateway.reconcile(mm.session_id, mm.epoch)
        quote._release_reconciled_proposals()
        actions = f.hedge.propose()
        assert len(actions) == 1, f.hedge.reason_code
        hedge, hedge_wire = send(f, actions[0])
        assert hedge.quantity_base == D("0.5")
        assert not await f.inventory.enter_inventory(operator_id="offline-op")
        assert pool.claim_ids() == (hedge.intent_id,)
        with pytest.raises(PermissionError):
            swap.sent[-1]["pre_send_check"](hedge_wire)
        publish(f, hedge, life="10.5", cash="9.5035", long="0.5", pnl="0.0005")
        assert await f.inventory.enter_inventory(operator_id="offline-op"), f.inventory.reason_code
        modes.append(f.inventory._state()["mode"])
        f.joint.manual_rearm(operator_id="offline-op")  # Missing history disarmed joint creation.
        now = int(clock.wall.timestamp() * 1000)
        payments = [FundingPayment("101", now, D("-0.01")), FundingPayment("102", now, D("0.004"))]
        Random(seed).shuffle(payments)  # Accounting must not depend on bill input ordering.
        publish(f, life="10.5", cash="9.4975", long="0.5", payments=tuple(payments))
        assert await f.inventory.reconcile(), f.inventory.reason_code
        assert f.carry.check().funding_debits_quote == D("0.01")
        actions = f.inventory.propose("inventory-child")
        assert len(actions) == 1, (f.inventory.reason_code, c.joint_risk_reason_code, c.hedge_reason_code, c._runner_scope_invalid)
        inventory, _ = send(f, actions[0])
        publish(f, inventory, life="10", cash="9.9915", long="0.5", payments=tuple(payments))
        assert await f.inventory.return_to_mm(operator_id="offline-op"), f.inventory.reason_code
        modes.append(f.inventory._state()["mode"])
        assert f.inventory.ledger.report(clock.wall).state == "COMPLETED"
        actions = f.hedge.propose()
        assert len(actions) == 1, f.hedge.reason_code
        rebalance, _ = send(f, actions[0])
        publish(f, rebalance, life="10", cash="9.9905", long="1", payments=tuple(payments))
        c._runner_halt_ok = c._halt_runner_orders()
        assert await f.joint.reconcile(), f.joint.reason_code
        assert f.hedge.allows_spot(), f.hedge.reason_code
        assert pool.claim_ids() == ()
        assert f.joint.measure().nav_quote == D("19.9910")
        assert f.joint.measure().drawdown_bps == (D("20.004") - D("19.9910")) / D("20.004") * 10000
        before = (f.joint.measure(), f.inventory.budget.campaign_committed_quote, f.hedge.journal._read()["cost_hold"])
        assert await f.joint.reconcile()
        assert await f.inventory.reconcile()
        assert before == (f.joint.measure(), f.inventory.budget.campaign_committed_quote, f.hedge.journal._read()["cost_hold"])
        assert c._record_telemetry("SNAPSHOT", reason_code="COMBINED_REPLAY")
        metrics = f.log.records()[-1]["metrics"]
        assert D(metrics["nav_quote"]["value"]) == D("19.9910")
        assert D(metrics["fees_quote"]["value"]) == D("0.004")
        assert D(metrics["funding_quote"]["value"]) == D("-0.006")
        assert metrics["liquidation_value_quote"]["value"] is None
        assert metrics["hedge_cost_quote"]["reason_code"] == "GROSS_HEDGE_CAMPAIGN_COST_HOLD"
        from hummingbot.strategy_v2.life_liquidity.telemetry import TelemetryRecorder
        f.log.flush()
        restored = TelemetryRecorder(f.log.path, clock_ms=f.log.clock_ms, synthetic=True, max_records=10000, create=False)
        assert restored.records() == f.log.records()
        assert len(spot.sent) == len(swap.sent) == 2
        import json
        from test.hummingbot.strategy_v2.life_liquidity.o7_acceptance import base
        expected = json.loads((base.ROOT / "docs/plans/evidence/life_o7_combined_replay.json").read_text())
        assert seed in expected["seeds"]
        events = []
        for role, claim in (("mm", mm), ("hedge", hedge), ("inventory", inventory), ("rebalance", rebalance)):
            order = next(o for o in f.history["value"].orders if o.claim.intent_id == claim.intent_id)
            trade = order.trades[0]
            events.append(dict(role=role, product=claim.product, side=claim.side, action=claim.position_action,
                               requested_base=str(claim.quantity_base.normalize()), filled_base=str(trade.quantity_base.normalize()),
                               price_usdt=str(trade.price_usdt.normalize()), signed_fee_quote=str(trade.signed_fee.normalize()),
                               realized_pnl_quote=str(trade.realized_pnl_quote.normalize())))
        actual = dict(events=events,
                      metrics={key: str(D(metrics[key]["value"]).normalize()) for key in expected["result"]["metrics"]},
                      funding=[dict(bill_id=p.bill_id, signed_quote=str(p.signed_payment_quote.normalize()))
                               for p in sorted(payments, key=lambda p: p.bill_id)],
                      modes=modes, remaining_capital_claims=list(pool.claim_ids()))
        assert actual == expected["result"]
        quote.intent_id_factory = lambda: "mm-successor"
        assert len(quote.propose()) == 1, (quote.reason_code, c.runtime_risk_reason_code, c.hedge_reason_code, c._permission_gates, c._runner_scope_invalid)
        assert LifeLiquidityController.trading_permissions_ready(c) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["spot", "swap"])
@pytest.mark.parametrize("fault", ["funding_scope", "basis", "history", "disconnect"])
async def test_combined_final_revocation_retains_holds_until_complete_joint_proof(tmp_path, route, fault):
    from dataclasses import replace

    async with system(tmp_path) as f:
        c, pool, sender, spot, swap, wal, clock, account, feed, strategy, sc, pc = f.v
        quote = c._quote_action_planner
        quote.intent_id_factory = lambda: "mm-primary"
        claim, wire = send(f, quote.propose()[0])
        if route == "swap":
            publish(f, claim, life="10.5", cash="9.504", long="1")
            c._runner_halt_ok = c._halt_runner_orders()
            assert await f.joint.reconcile()
            await c._order_safety_gateway.reconcile(claim.session_id, claim.epoch)
            quote._release_reconciled_proposals()
            claim, wire = send(f, f.hedge.propose()[0])
        route_connector = spot if route == "spot" else swap
        check = route_connector.sent[-1]["pre_send_check"]
        retained = pool.claims()
        counts = len(spot.sent), len(swap.sent)
        collect = f.joint.collect
        if fault == "funding_scope":
            b = f.feeds["bundle"]
            f.feeds["bundle"] = replace(b, funding=replace(b.funding, sequence=b.funding.sequence + 1, scope_complete=False))
        elif fault == "basis":
            from test.hummingbot.strategy_v2.life_liquidity.test_carry_monitor import refresh
            refresh(f.v, f.feeds, mark_price_usdt=D("2"))
        else:
            c._runner_halt_ok = c._halt_runner_orders()
            if fault == "disconnect":
                async def disconnected():
                    raise ConnectionError("synthetic disconnect")
                f.joint.collect = disconnected
            assert not await f.joint.reconcile()
            assert not f.joint.allocation_ready()
        with pytest.raises(PermissionError):
            check(wire)
        assert pool.claims() == retained
        assert (len(spot.sent), len(swap.sent)) == counts
        assert c._quote_action_planner.propose() == []
        f.joint.collect = collect
        b = f.feeds["bundle"]
        f.feeds["bundle"] = replace(b, funding=replace(b.funding, scope_complete=True))
        feed["snapshot"] = replace(feed["snapshot"], mark_price_usdt=D("1"))
        if route == "spot":
            publish(f, claim, life="10.5", cash="9.504", long="1")
        else:
            publish(f, claim, life="10.5", cash="9.5035", long="0.5", pnl="0.0005")
        c._runner_halt_ok = c._halt_runner_orders()
        assert await f.joint.reconcile(), f.joint.reason_code
        assert pool.claim_ids() == ()
        if fault in ("history", "disconnect"):
            assert not f.joint.allocation_ready()  # Complete facts cannot silently rearm creation.
            f.joint.manual_rearm(operator_id="offline-op")
        assert f.joint.allocation_ready()
        assert c._record_telemetry("SNAPSHOT", reason_code="RESTORED_COMBINED_PROOF")
        assert f.log.records()[-1]["metrics"]["nav_quote"]["quality"] == "synthetic"
        assert LifeLiquidityController.trading_permissions_ready(c) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["config", "foreign_wire", "missing_joint", "missing_wal", "fake_executor"])
async def test_spot_slot_scan_rejects_unproven_swap_children(tmp_path, fault):
    from types import SimpleNamespace

    async with system(tmp_path) as f:
        c = f.c
        quote = c._quote_action_planner
        quote.intent_id_factory = lambda: "mm-primary"
        mm, _ = send(f, quote.propose()[0])
        publish(f, mm, life="10.5", cash="9.504", long="1")
        c._runner_halt_ok = c._halt_runner_orders()
        assert await f.joint.reconcile()
        await c._order_safety_gateway.reconcile(mm.session_id, mm.epoch)
        quote._release_reconciled_proposals()
        claim, _ = send(f, f.hedge.propose()[0])
        publish(f, claim, life="10.5", cash="9.5035", long="0.5", pnl="0.0005")
        c._runner_halt_ok = c._halt_runner_orders()
        assert await f.joint.reconcile()
        assert quote._runner_occupied() == {("BUY", 0)}  # Only the shutting-down spot child occupies a spot slot.
        child = c._runner_orchestrator.active_executors["life"][-1]
        if fault == "config":
            child.config = child.config.model_copy(update={"amount": D("99")})
        elif fault == "foreign_wire":
            child.recovery_order_ids = lambda: ("foreign-wire",)
        elif fault == "missing_joint":
            c._joint_recovery = None
        elif fault == "missing_wal":
            child.config = child.config.model_copy(update={"id": "foreign-intent"})
        else:
            c._runner_orchestrator.active_executors["life"][-1] = SimpleNamespace(config=child.config, status=child.status)
        assert quote._runner_occupied() is None
        assert quote.propose() == []
