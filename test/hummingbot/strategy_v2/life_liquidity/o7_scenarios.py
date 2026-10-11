"""Frozen synthetic combined LIFE scope. No exchange or credential access."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.offline_market import install_market
from test.hummingbot.strategy_v2.life_liquidity.test_carry_monitor import refresh, setup_carry
from test.hummingbot.strategy_v2.life_liquidity.test_final_quote_send import _attach_quote_planner
from test.hummingbot.strategy_v2.life_liquidity.test_joint_recovery import setup_recovery
from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital_runner import setup_shared
from test.hummingbot.strategy_v2.life_liquidity.test_spot_session_replay import FakeTradingOkx
from test.hummingbot.strategy_v2.life_liquidity.test_swap_executor import wire_for
from types import SimpleNamespace

from hummingbot.core.data_type.common import PositionMode
from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
from hummingbot.strategy_v2.life_liquidity.capital_risk import IndependentNavObservation
from hummingbot.strategy_v2.life_liquidity.economics import SubsidyBudgetLedger
from hummingbot.strategy_v2.life_liquidity.fill_attribution import IndependentFillObservation, ReconciledFillAttributor
from hummingbot.strategy_v2.life_liquidity.hedge import HedgePolicy
from hummingbot.strategy_v2.life_liquidity.hedge_coordinator import HedgeCoordinator, HedgeExecutionPolicy, HedgeMarket
from hummingbot.strategy_v2.life_liquidity.inventory_execution import InventoryExecutionLedger, InventoryExecutionPolicy
from hummingbot.strategy_v2.life_liquidity.inventory_runner import InventoryExecutor, InventoryMarketObservation
from hummingbot.strategy_v2.life_liquidity.joint_exposure import JointExposureObservation, LinearLifeContractSpec
from hummingbot.strategy_v2.life_liquidity.joint_recovery import RecoveryOrder, RecoveryPayment, RecoveryTrade
from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetLedger
from hummingbot.strategy_v2.life_liquidity.order_gateway import SpotAccountReconciler, SpotReservationReconciler
from hummingbot.strategy_v2.life_liquidity.safety import SafetyGate, SafetyObservation
from hummingbot.strategy_v2.life_liquidity.telemetry import TelemetryRecorder

D = Decimal


@asynccontextmanager
async def system(path, *, inventory_seconds=1):
    joint, v, history, canceled = setup_recovery(path, values=setup_shared(
        path, collateral="100", budget_capacity=500, spot_route=FakeTradingOkx()))
    c, pool, sender, spot, swap, sw, clock, account, feed, strategy, sc, pc = v
    del c.allow_create_executor_actions  # Restore the real controller permission chain.
    watchdog = asyncio.create_task(asyncio.Event().wait())
    c.order_safety_watchdog_task = watchdog
    try:
        now = int(clock.wall.timestamp() * 1000)
        await install_market(c, spot, exchange_ms=now)
        original = c.config
        _attach_quote_planner(c, SimpleNamespace(_strategy=strategy), c._order_safety_wal,
                              c._order_safety_reservations, propose=False)
        c.config = original
        c._runner_orchestrator.get_stored_executors_by_controller = lambda _: ()
        gate = SafetyGate(path / "safety.json", max_drawdown_bps=D("500"), min_margin_buffer_quote=D("0"),
                          stable_data_ms=0, recovery_probe_base=D("1"))
        gate.initialize_empty()
        c.install_runtime_risk_gate(gate, observation=lambda: SafetyObservation(100, True, True, True, True, D("0"), D("100")),
                                    monotonic_clock_ms=lambda: 100, max_observation_age_ms=5)
        budget = SubsidyBudgetLedger(path / "exit.json", campaign_id="inventory", campaign_limit_quote=D("1"),
                                     day_limit_quote=D("1"), session_limit_quote=D("1"))
        budget.initialize_empty()
        loss = LossBudgetLedger(path / "loss.json", campaign_id="combined", campaign_limit_quote=D("1"),
                                day_limit_quote=D("1"), session_limit_quote=D("1"))
        loss.record("anchor", D("0"), session_id=c._order_safety_manager.current_session.session_id, at_utc=clock.wall)
        attribution = ReconciledFillAttributor(path / "attribution.json", wal=c._order_safety_wal,
                                               reservations=c._order_safety_reservations, loss_budget=loss,
                                               opening_life=D("10"), opening_usdt=D("10"), opening_independent_price_usdt=D("1"),
                                               independent_value=lambda f: IndependentFillObservation(D("1"), f.fill_at_ms, f.fill_at_ms, "independent_market"),
                                               max_reference_skew_ms=1000, create=True, exit_budget=budget)
        c._order_safety_gateway.account_check = SpotAccountReconciler(spot, c._order_safety_reservations).check
        reconciler = SpotReservationReconciler(c._order_safety_wal, c._order_safety_reservations, require_fees=True)
        gateway = c._order_safety_gateway
        gateway.apply_fills = reconciler.apply_fills
        gateway.confirm_terminal = reconciler.confirm_terminal
        gateway.on_cancel_requested = reconciler.request_cancel
        gateway.on_unknown = reconciler.mark_unknown
        c.install_execution_loss_budget(loss, utc_clock=lambda: clock.wall)
        c.install_fill_attributor(attribution)
        quote = c._quote_action_planner
        snap = quote.snapshot()
        quote.snapshot = lambda: replace(snap, loss_budget_status=c.execution_loss_status)
        market = {"value": HedgeMarket("12345", "LIFE-USDT-SWAP", 1, now,
                                       ((D("0.999"), D("100")),), ((D("1.001"), D("100")),),
                                       D("0.001"), D("0"), D("10"), True, "okx_depth_fee")}
        hp = HedgePolicy("LIFE-USDT-SWAP", D("0.1"), D("0.1"), D("2"), D("3"), 1000, 5,
                         D("1"), D("100"), D("0.001"))
        hedge = HedgeCoordinator(c, path / "hedge.json", policy=HedgeExecutionPolicy(hp, D("11"), D("5"), 500, "bounded_maker_and_pause"),
                                 market=lambda: market["value"], settlement=lambda _: None, create=True)
        c.install_hedge_coordinator(hedge)

        def joint_risk():
            obs = pool.qualified_snapshot()
            reservations = c._order_safety_reservations.preview()
            claims = pool.claims().values()
            return JointExposureObservation(c._order_safety_reservations.life_balance,
                                            reservations.unresolved_quantity_base("BUY"), reservations.unresolved_quantity_base("SELL"),
                                            (obs.long_base - obs.short_base) / D("0.25"),
                                            sum((x.quantity_base / D("0.25") for x in claims if x.product == "SWAP" and x.side == "BUY"), D("0")),
                                            sum((x.quantity_base / D("0.25") for x in claims if x.product == "SWAP" and x.side == "SELL"), D("0")),
                                            obs.mark_price_usdt, obs.index_price_usdt, obs.mark_source, obs.index_source,
                                            obs.collateral_quote, obs.maintenance_margin_quote, obs.funding_liability_quote)
        c.perpetual_contract = sender.contract
        c.install_joint_risk_gate(LinearLifeContractSpec(D("0.25"), D("0.1")), pool.policy.limits, observation=joint_risk)

        carry, _, feeds = setup_carry(path, values=v)
        book = InventoryExecutionLedger(path / "inventory.json", session_id="inventory", policy=InventoryExecutionPolicy(
            "SELL", D("0.5"), D("1"), clock.wall + timedelta(seconds=inventory_seconds), D("0.5"), D("2"), D("200"), D("0.1"), D("0.2"),
            D("0.1"), D("1"), D("0.01"), D("0.1"), D("0.1")), create=True)
        observation = {"value": InventoryMarketObservation(now, D("0.98"), D("0.99"), D("100"), D("0"),
                                                           D("5"), D("0.001"), D("0.001"), "independent_market", "inventory")}
        inventory = InventoryExecutor(c, path / "inventory_mode.json", ledger=book, budget=budget,
                                      observation=lambda: observation["value"], clock_ms=lambda: int(clock.wall.timestamp() * 1000),
                                      max_age_ms=1000, create=True)
        c.install_inventory_executor(inventory)
        log = TelemetryRecorder(path / "telemetry.jsonl", clock_ms=lambda: int(clock.mono * 1000), synthetic=True,
                                max_records=10000, create=True)
        c.install_telemetry(log, independent_value=lambda: IndependentNavObservation(D("1"), now, now, "independent_market"),
                            utc_clock_ms=lambda: int(clock.wall.timestamp() * 1000), max_value_age_ms=1000)
        c._runner_halt_ok = c._halt_runner_orders()
        assert await joint.reconcile(), joint.reason_code
        c._runtime_risk_ready()
        yield SimpleNamespace(c=c, v=v, joint=joint, history=history, inventory=inventory, hedge=hedge,
                              carry=carry, feeds=feeds, market=market, observation=observation, log=log, canceled=canceled)
    finally:
        watchdog.cancel()
        for task in (c.order_safety_task, c._joint_reconciliation_task):
            if task is not None:
                task.cancel()
        await asyncio.gather(watchdog, *[t for t in (c.order_safety_task, c._joint_reconciliation_task) if t], return_exceptions=True)


def send(f, action):
    c, pool, sender, spot, swap, sw, clock, account, feed, strategy, sc, pc = f.v
    cfg = action.executor_config
    runner = SimpleNamespace(controllers={"life": c}, logger=c.logger)
    assert StrategyV2Base._filter_authorized_actions(runner, [action]) == [action]
    child = OrderExecutor(strategy, cfg)
    child.get_order_price = lambda: cfg.price
    c._runner_orchestrator.active_executors["life"].append(child)
    assert c.on_runner_create_action_dispatched(action)
    child.place_open_order()
    claim = pool.claims()[cfg.id]
    route = spot if claim.product == "SPOT" else swap
    wire = ({"clOrdId": claim.wire_id, "instId": "LIFE-USDT", "tdMode": "cash", "ordType": "post_only",
             "side": cfg.side.name.lower(), "px": str(cfg.price), "sz": str(cfg.amount)} if claim.product == "SPOT" else
            {**wire_for(cfg, claim.wire_id, PositionMode.ONEWAY), "px": str(cfg.price), "sz": str(cfg.amount / D("0.25"))})
    route.sent[-1]["pre_send_check"](wire)
    return claim, wire


def publish(f, claim=None, *, quantity="0.5", life, cash, long, pnl="0", payments=(), terminal=True):
    c, pool, sender, spot, swap, sw, clock, account, feed, strategy, sc, pc = f.v
    orders = list(f.history["value"].orders)
    clock.advance(0.001)  # Preserve financial event chronology across child orders.
    now = int(clock.wall.timestamp() * 1000)
    if claim is not None:
        trade = RecoveryTrade("trade-" + claim.intent_id, D(quantity), claim.price_usdt, "USDT", D("-0.001"), D(pnl), now)
        order = RecoveryOrder(claim, "exchange-" + claim.intent_id, "canceled" if terminal else "partially_filled", (trade,))
        orders = [o for o in orders if o.claim.intent_id != claim.intent_id] + [order]
        if claim.product == "SPOT":
            spot.status[claim.wire_id] = {"clOrdId": claim.wire_id, "ordId": order.exchange_order_id,
                                          "state": order.state, "accFillSz": quantity}
            spot.fills[order.exchange_order_id] = [{"tradeId": trade.trade_id, "ordId": order.exchange_order_id,
                                                   "fillSz": quantity, "fillPx": str(trade.price_usdt), "feeCcy": "USDT",
                                                    "fee": "-0.001", "fillTime": str(now)}]
    refresh(f.v, f.feeds, payments=payments, spot_life_base=D(life), spot_cash_quote=D(cash), long_base=D(long))
    account["value"] = replace(account["value"], net_contracts=D(long) / D("0.25"))
    f.history["value"] = replace(f.history["value"], snapshot_sequence=feed["snapshot"].sequence, observed_at_ms=now,
                                 orders=tuple(orders), payments=tuple(RecoveryPayment(p.bill_id, p.signed_payment_quote, p.settled_at_ms) for p in payments),
                                 pending_wire_ids=tuple(o.claim.wire_id for o in orders if o.state == "partially_filled"))
    spot.cash_balances = {"LIFE": life, "USDT": cash}
    spot.open_pages[None] = []
    f.market["value"] = replace(f.market["value"], sequence=feed["snapshot"].sequence)
