"""Actual V2 tick/filter/dispatch and independent controller watchdog, fake I/O."""
import asyncio
import json
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.o7_scenarios import publish, system
from test.hummingbot.strategy_v2.life_liquidity.o8_restart import restore_assembly
from test.hummingbot.strategy_v2.life_liquidity.test_carry_monitor import refresh
from test.hummingbot.strategy_v2.life_liquidity.test_swap_executor import wire_for
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from controllers.generic.life_liquidity import LifeLiquidityController
from hummingbot.core.data_type.common import PositionAction, PositionMode
from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
from hummingbot.strategy_v2.life_liquidity.joint_recovery import RecoveryOrder
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction


@pytest.mark.asyncio
@pytest.mark.parametrize("maker_side", ["BUY", "SELL"])
async def test_continuous_v2_watchdog_maker_hedge_resume_expiry(tmp_path, maker_side):
    async with system(tmp_path, inventory_seconds=5) as f:
        c, pool, _, spot, swap, _, clock, _, _, strategy, _, _ = f.v
        orchestrator = c._runner_orchestrator
        queued, created = [], []
        trace = []
        publications = []

        async def collect():
            # The fake collector publishes its consistently cut account/carry
            # evidence atomically with the returned bundle, after I/O completes.
            for update in publications:
                f.v[8]["snapshot"] = replace(f.v[8]["snapshot"], pending=())
                update()
            publications.clear()
            history = f.history["value"]
            known = {o.claim.intent_id for o in history.orders}
            new = [claim for claim in pool.claims().values() if claim.intent_id not in known]
            if new:
                orders = history.orders + tuple(RecoveryOrder(claim, "exchange-" + claim.intent_id, "live", ()) for claim in new)
                pending = tuple(o.claim for o in orders if o.state not in ("filled", "canceled", "unsent"))
                refresh(f.v, f.feeds, pending=pending)
                obs = f.v[8]["snapshot"]
                f.history["value"] = replace(history, snapshot_sequence=obs.sequence, observed_at_ms=obs.observed_at_ms,
                                             orders=orders, pending_wire_ids=tuple(p.wire_id for p in pending))
            return f.history["value"]

        f.joint.collect = collect

        def execute(action):
            if isinstance(action, CreateExecutorAction):
                child = OrderExecutor(strategy, action.executor_config)
                child.get_order_price = lambda: child.config.price
                orchestrator.active_executors["life"].append(child)
                created.append(child)
            else:
                next(e for e in orchestrator.active_executors["life"] if e.config.id == action.executor_id).early_stop()

        orchestrator.execute_action = execute
        orchestrator.get_all_reports = lambda: {}
        with patch("hummingbot.strategy.strategy_v2_base._get_executor_orchestrator_class",
                   return_value=lambda **_: orchestrator):
            runner = StrategyV2Base({}, config=None)
        runner.controllers = {"life": c}
        runner.ready_to_trade = True
        runner.market_data_provider = SimpleNamespace(ready=True)
        runner.update_controllers_configs = lambda: None  # No YAML filesystem watcher in this fixture.
        runner.determine_executor_actions = lambda: list(queued)
        # Replace the fixture's dormant task with the production watchdog coroutine.
        c.order_safety_watchdog_task.cancel()
        await asyncio.gather(c.order_safety_watchdog_task, return_exceptions=True)
        watchdog = asyncio.create_task(c._order_safety_watchdog(.01))
        c.order_safety_watchdog_task = watchdog

        market_sequence = 1000

        async def tick():
            nonlocal market_sequence
            market_sequence += 1
            f.market["value"] = replace(f.market["value"], sequence=market_sequence,
                                        observed_at_ms=int(clock.wall.timestamp() * 1000))
            runner.tick(clock.mono)
            queued.clear()
            for child in created:
                if child._order is not None:
                    continue
                child.place_open_order()
                cfg = child.config
                claim = pool.claims()[cfg.id]
                route = spot if claim.product == "SPOT" else swap
                wire = ({"clOrdId": claim.wire_id, "instId": "LIFE-USDT", "tdMode": "cash", "ordType": "post_only",
                         "side": cfg.side.name.lower(), "px": str(cfg.price), "sz": str(cfg.amount)} if claim.product == "SPOT" else
                        {**wire_for(cfg, claim.wire_id, PositionMode.ONEWAY), "px": str(cfg.price), "sz": str(cfg.amount / D(".25"))})
                route.sent[-1]["pre_send_check"](wire)
            created.clear()
            for task in (c._joint_reconciliation_task, c.order_safety_task):
                if task and not task.done():
                    await task
            await asyncio.sleep(.015)
            trace.append((c._order_safety_manager.state, f.joint.reason_code, tuple(pool.claim_ids())))

        try:
            for _ in range(2):
                clock.advance(.251)
                await tick()
                assert f.joint.allocation_ready(), trace
            quote = c._quote_action_planner
            quote.max_actions_per_tick = 100
            ids = iter("continuous-" + str(i) for i in range(100))
            quote.intent_id_factory = lambda: next(ids)
            actions = quote.propose()
            assert actions, (quote.reason_code, c.runtime_risk_reason_code, f.hedge.reason_code, trace)
            selected = next(a for a in actions if a.executor_config.side.name == maker_side)
            for a in actions:
                if a is not selected:
                    quote.on_runner_action_rejected(a)
            queued.append(selected)
            await tick()
            maker = pool.claims()[selected.executor_config.id]
            # Pending evidence is collected by the normal runner task.
            history = f.history["value"]

            def pending_snapshot():
                refresh(f.v, f.feeds, pending=(maker,))
                obs = f.v[8]["snapshot"]
                f.history["value"] = replace(history, snapshot_sequence=obs.sequence, observed_at_ms=obs.observed_at_ms,
                                             orders=(RecoveryOrder(maker, "exchange-" + maker.intent_id, "live", ()),),
                                             pending_wire_ids=(maker.wire_id,))
            publications.append(pending_snapshot)
            clock.advance(.251)
            await tick()
            assert f.joint.allocation_ready(), trace
            for _ in range(3):
                await tick()
                assert f.hedge.allows_spot(), trace
                assert not spot.cancels, (trace, c.order_safety_reason_code, c.runtime_risk_reason_code)
            life, cash = ("10.5", "9.504") if maker_side == "BUY" else ("9.5", "10.504")
            publications.append(lambda: publish(f, maker, life=life, cash=cash, long="1"))
            clock.advance(.251)
            for _ in range(5):
                await tick()
            quote._release_reconciled_proposals()
            hedges = f.hedge.propose()
            assert len(hedges) == 1, (f.hedge.reason_code, trace)
            assert hedges[0].executor_config.position_action == (PositionAction.CLOSE if maker_side == "BUY" else PositionAction.OPEN)
            queued.extend(hedges)
            await tick()
            hedge = pool.claims()[hedges[0].executor_config.id]
            publications.append(lambda: publish(f, hedge, life=life, cash=str(D(cash) - D(".001") + (D(".0005") if maker_side == "BUY" else D("0"))),
                                                long=".5" if maker_side == "BUY" else "1.5", pnl=".0005" if maker_side == "BUY" else "0"))
            clock.advance(.251)
            for _ in range(5):
                await tick()
            assert f.hedge.allows_spot(), str((f.hedge.reason_code, trace, f.joint.reason_code))
            assert pool.claim_ids() == ()
            assert c._order_safety_manager.state == "ACTIVE", trace
            resumed = quote.propose()
            assert resumed, (quote.reason_code, trace)
            for action in resumed:
                quote.on_runner_action_rejected(action)
            if maker_side == "BUY":
                now = int(clock.wall.timestamp() * 1000)
                f.observation["value"] = replace(f.observation["value"], observed_at_ms=now)
                assert await f.inventory.enter_inventory(operator_id="offline-op"), f.inventory.reason_code
                actions = f.inventory.propose("continuous-inventory")
                assert len(actions) == 1, f.inventory.reason_code
                queued.extend(actions)
                await tick()
                inventory = pool.claims()[actions[0].executor_config.id]
                prior = f.v[8]["snapshot"]
                inv_life = prior.spot_life_base - D(".5")
                inv_cash = prior.spot_cash_quote + D(".5") * inventory.price_usdt - D(".001")
                publications.append(lambda: publish(f, inventory, life=str(inv_life), cash=str(inv_cash), long=str(prior.long_base)))
                clock.advance(.251)
                for _ in range(5):
                    await tick()
                assert await f.inventory.return_to_mm(operator_id="offline-op"), f.inventory.reason_code
                assert f.inventory.ledger.report(clock.wall).state == "COMPLETED"
                hedges = f.hedge.propose()
                assert len(hedges) == 1, f.hedge.reason_code
                queued.extend(hedges)
                await tick()
                rebalance = pool.claims()[hedges[0].executor_config.id]
                publications.append(lambda: publish(f, rebalance, life=str(inv_life), cash=str(inv_cash - D(".001")),
                                                    long=str(prior.long_base + D(".5"))))
                clock.advance(.251)
                for _ in range(5):
                    await tick()
                assert f.hedge.allows_spot(), (f.hedge.reason_code, trace)
                assert not pool.claim_ids()
            # A qualified funding source failure is a hard gate, even after
            # successful normal MM/inventory operation.
            bundle = f.feeds["bundle"]
            f.feeds["bundle"] = replace(bundle, funding=replace(bundle.funding, scope_complete=False))
            await tick()
            assert not c.allow_create_executor_actions()
            assert not f.carry.check().allowed
            # Expiry must be enforced even with runner market data unavailable.
            runner.market_data_provider.ready = False
            clock.advance(20)
            await tick()
            assert c._order_safety_manager.state == "EXPIRED"
            assert not c.allow_create_executor_actions()
            assert not LifeLiquidityController.trading_permissions_ready(c)
            # Lose the entire in-memory controller/runner assembly after expiry.
            # Reopen every durable component; feeds and fake transport remain
            # external to the restarted process, as an exchange would.
            watchdog.cancel()
            runner.listen_to_executor_actions_task.cancel()
            await asyncio.gather(watchdog, runner.listen_to_executor_actions_task, return_exceptions=True)
            f.log.flush()
            recovered = await restore_assembly(f)
            fresh = recovered.c
            assert fresh is not c and fresh._order_safety_manager is not c._order_safety_manager
            assert fresh._order_safety_reservations is not c._order_safety_reservations
            assert fresh._order_safety_wal.all_records() == c._order_safety_wal.all_records()
            assert recovered.v[5].all_records() == f.v[5].all_records()
            assert fresh._order_safety_reservations.reservation_snapshot() == c._order_safety_reservations.reservation_snapshot()
            assert fresh._fill_attributor._events == c._fill_attributor._events
            assert fresh._execution_loss_budget._events == c._execution_loss_budget._events
            assert recovered.inventory.ledger._entries == f.inventory.ledger._entries
            assert recovered.inventory.budget._entries == f.inventory.budget._entries
            assert recovered.hedge.journal._read() == f.hedge.journal._read()
            assert recovered.carry.journal._read() == f.carry.journal._read()
            assert recovered.joint.journal._read() == f.joint.journal._read()
            assert fresh._quote_action_planner.action_journal._read() == quote.action_journal._read()
            assert not fresh._inventory_executor.allows_mm()  # Explicit restore latch.
            assert not recovered.joint.allocation_ready()
            assert fresh._runtime_risk_gate.rearm_required
            assert not LifeLiquidityController.trading_permissions_ready(fresh)
            orch = fresh._runner_orchestrator
            orch.get_all_reports = lambda: {}
            with patch("hummingbot.strategy.strategy_v2_base._get_executor_orchestrator_class",
                       return_value=lambda **_: orch):
                reboot = StrategyV2Base({}, config=None)
            reboot.controllers = {"life": fresh}
            reboot.ready_to_trade = True
            reboot.market_data_provider = SimpleNamespace(ready=False)
            reboot.update_controllers_configs = lambda: None
            reboot.determine_executor_actions = lambda: []
            restarted_watchdog = asyncio.create_task(fresh._order_safety_watchdog(.01))
            fresh.order_safety_watchdog_task = restarted_watchdog
            wires_before = (len(spot.sent), len(swap.sent))
            try:
                for _ in range(3):
                    clock.advance(.251)
                    reboot.tick(clock.mono)
                    await asyncio.sleep(.015)
                    for task in (fresh._joint_reconciliation_task, fresh.order_safety_task):
                        if task and not task.done():
                            await task
                    assert fresh._order_safety_manager.state == "EXPIRED"
                    assert not fresh.allow_create_executor_actions()
                    assert not recovered.v[1].claim_ids()
                assert (len(spot.sent), len(swap.sent)) == wires_before
            finally:
                tasks = [t for t in (restarted_watchdog, reboot.listen_to_executor_actions_task,
                                     fresh._joint_reconciliation_task, fresh.order_safety_task) if t]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            print("O8_CONTINUOUS " + json.dumps({"maker_side": maker_side, "trace": trace,
                                                "inventory_covered": maker_side == "BUY",
                                                 "full_assembly_cold_restart": "expired_no_rearm_no_orders",
                                                 "spot_wires": len(spot.sent), "swap_wires": len(swap.sent),
                                                 "production_permission": False}))
        finally:
            watchdog.cancel()
            runner.listen_to_executor_actions_task.cancel()
            await asyncio.gather(watchdog, runner.listen_to_executor_actions_task, return_exceptions=True)
