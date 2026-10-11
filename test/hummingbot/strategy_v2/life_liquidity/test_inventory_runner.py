"""O.7.6 synthetic inventory sessions use actual protected executor children."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_protected_exit import exit_setup

import pytest

from hummingbot.strategy_v2.life_liquidity.inventory_execution import InventoryExecutionLedger, InventoryExecutionPolicy
from hummingbot.strategy_v2.life_liquidity.inventory_runner import InventoryExecutor, InventoryMarketObservation

D = Decimal


async def setup_inventory(tmp_path, *, side="SELL", **changes):
    c, connector, wal, reservations, gate, old, watchdog, exits, snapshot, now = await exit_setup(tmp_path)
    c._runner_orchestrator.get_stored_executors_by_controller = lambda _: ()
    c._spot_exit_planner = None  # Reuse the real accounting fixture with its separate execution budget.
    now["ms"] = int(c._order_safety_manager.wall_clock().timestamp() * 1000)
    at = datetime.fromtimestamp(now["ms"] / 1000, timezone.utc)
    policy = InventoryExecutionPolicy(
        side=side, target_base=D("1"), benchmark_price_usdt=D("1"),
        deadline_utc=at + timedelta(seconds=1), max_child_base=D("0.5"),
        max_total_notional_quote=D("2"), max_slippage_bps=D("200"), max_impact_quote=D("0.1"),
        max_exit_loss_quote=D("0.2"), participation_fraction=D("0.1"),
        min_independent_volume_base=D("1"), tick_size=D("0.01"), lot_size=D("0.1"), min_size_base=D("0.1"))
    policy = replace(policy, **changes)
    book = InventoryExecutionLedger(tmp_path / "inventory.json", session_id="inventory-only", policy=policy, create=True)
    obs = {"value": InventoryMarketObservation(now["ms"], D("0.98"), D("0.99"), D("100"), D("0"),
                                               D("5"), D("0.001"), D("0.001"), "independent_market", "inventory-only")}
    execution = InventoryExecutor(c, tmp_path / "inventory_mode.json", ledger=book, budget=exits.budget,
                                  observation=lambda: obs["value"], clock_ms=lambda: now["ms"], max_age_ms=100,
                                  create=True)
    c.install_inventory_executor(execution)
    return execution, c, connector, wal, reservations, gate, old, watchdog, obs, now


@pytest.mark.asyncio
async def test_inventory_mode_blocks_mm_and_issues_only_bounded_maker_child(tmp_path):
    execution, c, connector, wal, reservations, gate, old, watchdog, obs, now = await setup_inventory(tmp_path)
    try:
        assert await execution.enter_inventory(operator_id="offline-operator"), execution.reason_code
        assert not c.allow_create_executor_actions()
        actions = execution.propose("inventory-1")
        assert len(actions) == 1, execution.reason_code
        assert actions[0].executor_config.amount == D("0.5")
        assert execution.ledger.pending_base == D("0.5")
    finally:
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)


def send_child(execution, c, connector, old, *, intent_id="inventory-1"):
    from types import SimpleNamespace

    from hummingbot.strategy.strategy_v2_base import StrategyV2Base
    from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
    actions = execution.propose(intent_id)
    assert len(actions) == 1, execution.reason_code
    runner = SimpleNamespace(controllers={"life": c}, logger=c.logger)
    assert StrategyV2Base._filter_authorized_actions(runner, actions) == actions
    child = OrderExecutor(old._strategy, actions[0].executor_config)
    c._runner_orchestrator.active_executors["life"].append(child)
    assert c.on_runner_create_action_dispatched(actions[0])
    child.get_order_price = lambda: child.config.price
    child.place_open_order()
    wire = {"clOrdId": child._order.order_id, "instId": "LIFE-USDT", "side": child.config.side.name.lower(),
            "ordType": "post_only", "tdMode": "cash", "px": str(child.config.price), "sz": str(child.config.amount)}
    connector.sent[-1]["pre_send_check"](wire)
    return child, wire


def exchange_report(connector, child, now, *, quantity="0.2", terminal=False, fee="-0.001", currency="USDT"):
    q, px = D(quantity), child.config.price
    side = child.config.side.name
    wire = child._order.order_id
    exchange_id = "exchange-" + child.config.id
    connector.status[wire] = {"clOrdId": wire, "ordId": exchange_id,
                              "state": "canceled" if terminal else "partially_filled", "accFillSz": quantity}
    fill_time = (connector.fills.get(exchange_id) or [{}])[0].get("fillTime", str(now["ms"]))
    connector.fills[exchange_id] = ([{"tradeId": "fill-" + child.config.id, "ordId": exchange_id,
                                      "fillSz": quantity, "fillPx": str(px), "feeCcy": currency,
                                      "fee": fee, "fillTime": fill_time}] if q else [])
    life = D("10") + (q if side == "BUY" else -q) + (D(fee) if currency == "LIFE" else 0)
    cash = D("10") + (-q * px if side == "BUY" else q * px) + (D(fee) if currency == "USDT" else 0)
    connector.cash_balances = {"LIFE": str(life), "USDT": str(cash)}
    connector.open_pages[None] = ([] if terminal else [{"instId": "LIFE-USDT", "clOrdId": wire,
                                                       "ordId": exchange_id, "state": "partially_filled"}])


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["BUY", "SELL"])
async def test_actual_executor_partial_fill_terminal_release_and_mm_return(tmp_path, side):
    e, c, connector, wal, reservations, gate, old, watchdog, obs, now = await setup_inventory(tmp_path, side=side)
    try:
        assert await e.enter_inventory(operator_id="op")
        child, wire = send_child(e, c, connector, old)
        assert wal.get(child.config.id).state == "SEND_UNKNOWN"
        assert e.ledger.pending_base == D("0.5")
        exchange_report(connector, child, now)
        assert not await e.reconcile(), e.reason_code
        assert e.ledger.filled_base == D("0.2") and e.ledger.pending_base == D("0.3")
        assert e.propose("competing") == []
        assert not await e.return_to_mm(operator_id="op")
        assert not e.allows_mm()
        exchange_report(connector, child, now, terminal=True)
        assert await e.return_to_mm(operator_id="op"), e.reason_code
        assert e.ledger.pending_base == 0
        assert e.ledger.filled_base == D("0.2")
        assert e.allows_mm()
        assert e.ledger.report(datetime.fromtimestamp(now["ms"] / 1000, timezone.utc)).fee_quote == D("0.001")
        assert c._fill_attributor._events["fill-inventory-1"]["purpose"] == "inventory_execution"
        before = (reservations.life_balance, reservations.usdt_balance, e.budget.campaign_committed_quote)
        assert await e.reconcile()
        assert before == (reservations.life_balance, reservations.usdt_balance, e.budget.campaign_committed_quote)
        with pytest.raises(PermissionError):
            connector.sent[0]["pre_send_check"](wire)
    finally:
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["depth", "volume", "own", "impact", "price", "fee", "age", "future", "source", "halt", "session", "clock", "policy", "rules", "disconnect", "anchor"])
async def test_final_wire_rechecks_hard_market_budget_and_permission_limits(tmp_path, fault):
    e, c, connector, wal, reservations, gate, old, watchdog, obs, now = await setup_inventory(tmp_path)
    try:
        assert await e.enter_inventory(operator_id="op")
        child, wire = send_child(e, c, connector, old)
        if fault in ("depth", "volume", "own", "impact", "price", "fee", "age", "future", "source", "anchor"):
            changes = {"depth": {"independent_depth_base": D("0.1")}, "volume": {"public_volume_base": D("2")},
                       "own": {"known_own_volume_base": D("100")}, "impact": {"expected_impact_quote": D("1")},
                       "price": {"best_bid_usdt": D("0.8"), "best_ask_usdt": D("0.9")},
                       "fee": {"fee_rate": D("0.1")}, "age": {"observed_at_ms": now["ms"] - 101},
                       "future": {"observed_at_ms": now["ms"] + 1}, "source": {"source_kind": "local_quote"}, "anchor": {"volume_anchor": "reset-window"}}
            obs["value"] = replace(obs["value"], **changes[fault])
        elif fault == "halt":
            gate.halt("OPERATOR_STOP")
        elif fault == "session":
            c._order_safety_manager.wall_clock = lambda: c._order_safety_manager.current_session.expires_at
        elif fault == "clock":
            now["ms"] -= 1
        elif fault == "policy":
            e.ledger.policy = replace(e.ledger.policy, benchmark_price_usdt=D("0.5"))
        elif fault == "rules":
            from hummingbot.strategy_v2.life_liquidity.market_data import InstrumentRules
            c.listing_gate.instrument_rules = InstrumentRules(D("0.1"), D("0.1"), D("0.1"))
        else:
            c.book_ready = False
        with pytest.raises(PermissionError):
            connector.sent[0]["pre_send_check"](wire)
        assert e.ledger.pending_base == D("0.5") if fault != "policy" else True
        assert reservations.has_open_intent(child.config.id)
    finally:
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["BUY", "SELL"])
async def test_inflated_volume_cannot_override_quantity_depth_and_notional_caps(tmp_path, side):
    e, c, connector, wal, reservations, gate, old, watchdog, obs, now = await setup_inventory(tmp_path, side=side,
                                                                                              max_child_base=D("100"), max_total_notional_quote=D("0.3"))
    try:
        obs["value"] = replace(obs["value"], public_volume_base=D("100000000"), independent_depth_base=D("0.2"))
        assert await e.enter_inventory(operator_id="op")
        actions = e.propose("thin")
        assert len(actions) == 1, e.reason_code
        assert actions[0].executor_config.amount == D("0.2")
        assert actions[0].executor_config.amount * actions[0].executor_config.price <= D("0.3")
        assert e.propose("more") == []
    finally:
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("filled", ["0", "0.2"])
async def test_deadline_reports_residual_and_cancels_without_a_market_sweep(tmp_path, filled):
    e, c, connector, wal, reservations, gate, old, watchdog, obs, now = await setup_inventory(tmp_path)
    try:
        assert await e.enter_inventory(operator_id="op")
        child, wire = send_child(e, c, connector, old)
        exchange_report(connector, child, now, quantity=filled, fee="0")
        assert not await e.reconcile()
        now["ms"] += 1000
        e.on_safety_tick()
        assert not e.propose("late")
        assert not await e.return_to_mm(operator_id="op")  # Cancel ACK is not terminal.
        assert e.ledger.pending_base == D("0.5") - D(filled)
        exchange_report(connector, child, now, quantity=filled, fee="0", terminal=True)
        assert await e.return_to_mm(operator_id="op"), e.reason_code
        report = e.ledger.report(datetime.fromtimestamp(now["ms"] / 1000, timezone.utc))
        assert report.state == ("EXPIRED_UNFILLED" if filled == "0" else "EXPIRED_PARTIAL")
        assert report.remaining_base == D("1") - D(filled)
        assert report.pending_base == 0
        assert len(connector.sent) == 1
    finally:
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)


def restore_execution(e, c):
    book = InventoryExecutionLedger(e.ledger.path, session_id=e.ledger.session_id, policy=e.ledger.policy, create=False)
    c._inventory_executor = None
    restored = InventoryExecutor(c, e.journal.path, ledger=book, budget=e.budget, observation=e.observation,
                                 clock_ms=e.clock_ms, max_age_ms=e.max_age_ms, create=False)
    c.install_inventory_executor(restored)
    return restored


@pytest.mark.asyncio
@pytest.mark.parametrize("cut", ["reserve", "action", "fill", "terminal", "budget"])
async def test_fresh_inventory_objects_recover_issuance_and_settlement_cutpoints(tmp_path, monkeypatch, cut):
    e, c, connector, wal, reservations, gate, old, watchdog, obs, now = await setup_inventory(tmp_path)
    try:
        assert await e.enter_inventory(operator_id="op")
        if cut in ("reserve", "action"):
            owner, name = ((e.ledger, "reserve_child") if cut == "reserve" else (e.quote.action_journal, "claim_batch"))
            original = getattr(owner, name)

            def fail(*args, **kwargs):
                original(*args, **kwargs)
                raise OSError("interruption after issuance checkpoint")

            with monkeypatch.context() as patcher:
                patcher.setattr(owner, name, fail)
                assert e.propose("gap") == []
            assert connector.sent == []
            restored = restore_execution(e, c)
            assert not restored.allows_mm()
            assert not restored.propose("gap")
            assert await restored.enter_inventory(operator_id="recovery"), restored.reason_code
            assert restored.ledger.pending_base == 0
            assert restored.propose("gap") == []
            assert len(restored.propose("new-child")) == 1, restored.reason_code
        else:
            child, wire = send_child(e, c, connector, old)
            exchange_report(connector, child, now, terminal=True)
            owner, name = {"fill": (e.ledger, "record_fill"), "terminal": (e.ledger, "confirm_terminal"),
                           "budget": (e.budget, "_reconcile")}[cut]
            original = getattr(owner, name)

            def fail(*args, **kwargs):
                original(*args, **kwargs)
                raise OSError("interruption after settlement checkpoint")

            with monkeypatch.context() as patcher:
                patcher.setattr(owner, name, fail)
                assert not await e.reconcile()
            restored = restore_execution(e, c)
            assert not restored.allows_mm()
            assert not restored.handles(child.config)
            assert await restored.enter_inventory(operator_id="recovery"), restored.reason_code
            assert restored.ledger.filled_base == D("0.2")
            assert restored.ledger.pending_base == 0
            assert restored.target_base == D("9")
            assert restored.budget.campaign_committed_quote == D("0.003")
            assert len(connector.sent) == 1
            with pytest.raises(PermissionError):
                connector.sent[0]["pre_send_check"](wire)
            assert await restored.reconcile()
            assert restored.budget.campaign_committed_quote == D("0.003")
    finally:
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)


@pytest.mark.asyncio
async def test_canceled_quantity_can_be_replaced_without_resetting_target_or_benchmark(tmp_path):
    e, c, connector, wal, reservations, gate, old, watchdog, obs, now = await setup_inventory(tmp_path)
    try:
        assert await e.enter_inventory(operator_id="op")
        child, _ = send_child(e, c, connector, old)
        exchange_report(connector, child, now, terminal=True)
        assert await e.reconcile(), e.reason_code
        assert e.ledger.filled_base == D("0.2")
        actions = e.propose("residual")
        assert len(actions) == 1, e.reason_code
        assert actions[0].executor_config.amount == D("0.5")
        assert e.ledger.filled_base + e.ledger.pending_base == D("0.7")
        assert e.ledger.policy.benchmark_price_usdt == D("1")
        assert e.propose("extra") == []
        with pytest.raises(ValueError, match="UNPROVEN"):
            e.ledger.confirm_terminal("residual", authority=None, proof_id="0" * 64)
    finally:
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["foreign", "disconnect", "missing_fee", "balance", "history", "stale_proof"])
async def test_incomplete_terminal_evidence_never_releases_inventory_or_returns_mm(tmp_path, fault):
    e, c, connector, wal, reservations, gate, old, watchdog, obs, now = await setup_inventory(tmp_path)
    try:
        assert await e.enter_inventory(operator_id="op")
        child, wire = send_child(e, c, connector, old)
        exchange_report(connector, child, now, terminal=True)
        if fault == "foreign":
            connector.algo_pages["trigger"] = {"code": "0", "data": [{"algoId": "foreign"}]}
        elif fault == "disconnect":
            async def offline(*_):
                raise OSError("disconnected")
            connector.get_order_by_client_id = offline
        elif fault == "missing_fee":
            connector.fills["exchange-inventory-1"][0].pop("fee")
        elif fault == "balance":
            connector.cash_balances["USDT"] = "10"
        elif fault == "history":
            connector.fills["exchange-inventory-1"] = []
        else:
            from dataclasses import replace as dc_replace
            original = c._order_safety_gateway.reconcile

            async def stale(*args):
                return dc_replace(await original(*args), observed_at=c._order_safety_manager.wall_clock() - timedelta(seconds=1))
            c._order_safety_gateway.reconcile = stale
        assert not await e.return_to_mm(operator_id="op")
        assert not e.allows_mm()
        assert e.ledger.pending_base == D("0.5")
        assert e.budget.campaign_committed_quote > 0
    finally:
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("currency,fee", [("USDT", "-0.01"), ("USDT", "0.001"), ("LIFE", "-0.001")])
async def test_actual_signed_fee_converts_once_and_preserves_fixed_shortfall(tmp_path, currency, fee):
    e, c, connector, wal, reservations, gate, old, watchdog, obs, now = await setup_inventory(tmp_path)
    try:
        assert await e.enter_inventory(operator_id="op")
        child, _ = send_child(e, c, connector, old)
        exchange_report(connector, child, now, terminal=True, currency=currency, fee=fee)
        assert await e.reconcile(), e.reason_code
        report = e.ledger.report(datetime.fromtimestamp(now["ms"] / 1000, timezone.utc))
        assert report.fee_quote == -D(fee)  # Independently valued LIFE at 1 USDT.
        assert report.implementation_shortfall_quote == D("0.002") - D(fee)
        assert e.ledger.gross_cost() == D("0.002") + max(D("0"), -D(fee))
        assert await e.reconcile()
        assert e.ledger.report(datetime.fromtimestamp(now["ms"] / 1000, timezone.utc)) == report
    finally:
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)


@pytest.mark.asyncio
async def test_dispatched_child_before_wal_is_proven_unsent_only_after_runner_halt(tmp_path):
    from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
    e, c, connector, wal, reservations, gate, old, watchdog, obs, now = await setup_inventory(tmp_path)
    try:
        assert await e.enter_inventory(operator_id="op")
        action = e.propose("dispatch-gap")[0]
        child = OrderExecutor(old._strategy, action.executor_config)
        c._runner_orchestrator.active_executors["life"].append(child)
        child.get_order_price = lambda: child.config.price
        assert c.on_runner_create_action_dispatched(action)
        restored = restore_execution(e, c)
        assert await restored.enter_inventory(operator_id="recovery"), restored.reason_code
        assert restored.ledger.pending_base == 0
        assert connector.sent == []
        with pytest.raises(PermissionError):
            child.place_open_order()
    finally:
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)


@pytest.mark.asyncio
async def test_completed_target_and_fee_breach_are_reported_without_refunding_budget(tmp_path):
    e, c, connector, wal, reservations, gate, old, watchdog, obs, now = await setup_inventory(tmp_path, target_base=D("0.5"),
                                                                                              max_exit_loss_quote=D("0.01"))
    try:
        assert await e.enter_inventory(operator_id="op")
        obs["value"] = replace(obs["value"], fee_rate=D("0"))
        child, _ = send_child(e, c, connector, old)
        exchange_report(connector, child, now, quantity="0.5", terminal=True, fee="-0.02")
        assert await e.reconcile(), e.reason_code  # Facts settle despite an observed loss breach.
        report = e.ledger.report(datetime.fromtimestamp(now["ms"] / 1000, timezone.utc))
        assert report.state == "COMPLETED" and report.remaining_base == 0
        assert report.implementation_shortfall_quote == D("0.025")
        assert e.ledger.gross_cost() == e.budget.campaign_committed_quote == D("0.025")
        assert e.propose("over-target") == []
        assert await e.reconcile()
        assert e.budget.campaign_committed_quote == D("0.025")
    finally:
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)


@pytest.mark.asyncio
async def test_favorable_fills_and_rebates_do_not_erase_gross_execution_budget(tmp_path):
    e, c, connector, wal, reservations, gate, old, watchdog, obs, now = await setup_inventory(tmp_path)
    try:
        assert await e.enter_inventory(operator_id="op")
        child, _ = send_child(e, c, connector, old)
        exchange_report(connector, child, now, quantity="0.4", terminal=True)
        exchange_id = "exchange-inventory-1"
        connector.fills[exchange_id] = [
            {"tradeId": "adverse", "ordId": exchange_id, "fillSz": "0.2", "fillPx": "0.99", "feeCcy": "USDT", "fee": "-0.002", "fillTime": str(now["ms"])},
            {"tradeId": "favorable", "ordId": exchange_id, "fillSz": "0.2", "fillPx": "1.1", "feeCcy": "USDT", "fee": "0.002", "fillTime": str(now["ms"])},
        ]
        connector.cash_balances = {"LIFE": "9.6", "USDT": "10.418"}
        assert await e.reconcile(), e.reason_code
        report = e.ledger.report(datetime.fromtimestamp(now["ms"] / 1000, timezone.utc))
        assert report.implementation_shortfall_quote == D("-0.018")
        assert e.ledger.gross_cost() == e.budget.campaign_committed_quote == D("0.004")
        with pytest.raises(ValueError, match="TERMINAL_FILL_CHANGED"):
            e.ledger.record_fill(child.config.id, "late-mutation", D("0.1"), D("0.99"), D("0"))
    finally:
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["ledger_deleted", "mode_deleted", "foreign_claim", "operator", "stop", "invalid_config"])
async def test_missing_storage_and_control_revocation_deny_queued_inventory(tmp_path, fault):
    from types import SimpleNamespace

    from hummingbot.strategy.strategy_v2_base import StrategyV2Base
    e, c, connector, wal, reservations, gate, old, watchdog, obs, now = await setup_inventory(tmp_path)
    try:
        assert await e.enter_inventory(operator_id="op")
        actions = e.propose("queued")
        assert actions
        if fault == "ledger_deleted":
            e.ledger.path.unlink()
        elif fault == "mode_deleted":
            e.journal.path.unlink()
        elif fault == "foreign_claim":
            from hummingbot.strategy_v2.life_liquidity.risk import SpotIntent
            session = c._order_safety_manager.current_session
            reservations.reserve(SpotIntent("foreign", "BUY", D("1"), D("1"), session.session_id, session.epoch), reference_price=D("1"))
        elif fault == "operator":
            with pytest.raises(ValueError, match="OPERATOR"):
                await e.return_to_mm(operator_id="")
            assert await e.return_to_mm(operator_id="op")
        elif fault == "stop":
            c.stop()
        else:
            c.on_config_load_failure()
        runner = SimpleNamespace(controllers={"life": c}, logger=c.logger)
        assert StrategyV2Base._filter_authorized_actions(runner, actions) == []
        assert connector.sent == []
    finally:
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)


@pytest.mark.asyncio
async def test_inventory_entry_waits_for_old_swap_and_revokes_its_final_wire(tmp_path):
    from test.hummingbot.strategy_v2.life_liquidity.offline_market import install_market
    from test.hummingbot.strategy_v2.life_liquidity.test_final_quote_send import _attach_quote_planner
    from test.hummingbot.strategy_v2.life_liquidity.test_joint_recovery import report, send_close, setup_recovery
    from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital_runner import setup_shared
    from test.hummingbot.strategy_v2.life_liquidity.test_spot_session_replay import FakeTradingOkx
    from test.hummingbot.strategy_v2.life_liquidity.test_swap_executor import wire_for

    from hummingbot.core.data_type.common import PositionAction, PositionMode
    from hummingbot.strategy_v2.life_liquidity.economics import SubsidyBudgetLedger
    from hummingbot.strategy_v2.life_liquidity.fill_attribution import (
        IndependentFillObservation,
        ReconciledFillAttributor,
    )
    from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetLedger
    from hummingbot.strategy_v2.life_liquidity.order_gateway import SpotAccountReconciler

    joint, v, bundle, canceled = setup_recovery(tmp_path, values=setup_shared(
        tmp_path, collateral="100", budget_capacity=50, spot_route=FakeTradingOkx()))
    c, capital, sender, spot, swap, wal, clock, account, feed, strategy, sc, pc = v
    watchdog = asyncio.create_task(asyncio.Event().wait())
    c.order_safety_watchdog_task = watchdog
    try:
        await install_market(c, spot, exchange_ms=int(clock.wall.timestamp() * 1000))
        template = type("Template", (), {"_strategy": strategy})()
        original_config = c.config
        _attach_quote_planner(c, template, c._order_safety_wal, c._order_safety_reservations, propose=False)
        c.config = original_config  # Keep the SWAP sender's immutable strategy binding.
        c._runner_orchestrator.get_stored_executors_by_controller = lambda _: ()
        budget = SubsidyBudgetLedger(tmp_path / "exit.json", campaign_id="inventory", campaign_limit_quote=D("1"),
                                     day_limit_quote=D("1"), session_limit_quote=D("1"))
        budget.initialize_empty()
        loss = LossBudgetLedger(tmp_path / "loss.json", campaign_id="inventory", campaign_limit_quote=D("1"),
                                day_limit_quote=D("1"), session_limit_quote=D("1"))
        loss.record("anchor", D("0"), session_id=c._order_safety_manager.current_session.session_id, at_utc=clock.wall)
        attribution = ReconciledFillAttributor(tmp_path / "attribution.json", wal=c._order_safety_wal,
                                               reservations=c._order_safety_reservations, loss_budget=loss,
                                               opening_life=D("10"), opening_usdt=D("10"), opening_independent_price_usdt=D("1"),
                                               independent_value=lambda f: IndependentFillObservation(D("1"), f.fill_at_ms, f.fill_at_ms, "independent_market"),
                                               max_reference_skew_ms=1000, create=True, exit_budget=budget)
        c._order_safety_gateway.account_check = SpotAccountReconciler(spot, c._order_safety_reservations).check
        c.install_execution_loss_budget(loss, utc_clock=lambda: clock.wall)
        c.install_fill_attributor(attribution)
        book = InventoryExecutionLedger(tmp_path / "inventory.json", session_id="inventory", policy=InventoryExecutionPolicy(
            "SELL", D("1"), D("1"), clock.wall + timedelta(seconds=1), D("0.5"), D("2"), D("200"), D("0.1"), D("0.2"),
            D("0.1"), D("1"), D("0.01"), D("0.1"), D("0.1")), create=True)
        execution = InventoryExecutor(c, tmp_path / "inventory_mode.json", ledger=book, budget=budget,
                                      observation=lambda: InventoryMarketObservation(int(clock.wall.timestamp() * 1000),
                                                                                     D("0.98"), D("0.99"), D("100"), D("0"), D("5"), D("0.001"), D("0.001"), "independent_market", "inventory"),
                                      clock_ms=lambda: int(clock.wall.timestamp() * 1000), max_age_ms=1000, create=True)
        c.install_inventory_executor(execution)
        c._runner_halt_ok = c._halt_runner_orders()
        assert await joint.reconcile(), joint.reason_code
        claim = send_close(v)
        pc = pc.model_copy(update={"amount": D("0.5"), "position_action": PositionAction.CLOSE})
        wire = wire_for(pc, claim.wire_id, PositionMode.ONEWAY)
        assert not await execution.enter_inventory(operator_id="op")  # Missing sent SWAP history retains its claim.
        assert capital.claim_ids() == (claim.intent_id,)
        assert canceled == [claim.wire_id]
        with pytest.raises(PermissionError):
            swap.sent[0]["pre_send_check"](wire)
        report(v, bundle, claim, fee="0")
        assert await execution.enter_inventory(operator_id="op"), execution.reason_code
        assert capital.claim_ids() == ()
        assert execution.journal._read()["mode"] == "INVENTORY"
        with pytest.raises(PermissionError):
            swap.sent[0]["pre_send_check"](wire)
    finally:
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)


@pytest.mark.asyncio
async def test_stop_retains_account_lock_until_inventory_collection_cleanup_finishes(tmp_path):
    from unittest.mock import MagicMock
    e, c, connector, wal, reservations, gate, old, watchdog, obs, now = await setup_inventory(tmp_path)
    entered, cleanup = asyncio.Event(), asyncio.Event()
    ownership = MagicMock()
    c._order_safety_account_lock = ownership
    original = c._order_safety_gateway.reconcile

    async def pending(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            await cleanup.wait()
        return await original(*args)

    c._order_safety_gateway.reconcile = pending
    task = asyncio.create_task(e.enter_inventory(operator_id="op"))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        c.stop()
        ownership.release.assert_not_called()
        cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        ownership.release.assert_called_once()
        assert connector.sent == []
        assert not e.allows_mm()
    finally:
        cleanup.set()
        task.cancel()
        watchdog.cancel()
        await asyncio.gather(task, watchdog, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("sent", [False, True])
async def test_mm_queue_and_live_order_are_drained_before_inventory_mode(tmp_path, sent):
    from types import SimpleNamespace

    from hummingbot.strategy.strategy_v2_base import StrategyV2Base
    from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
    from hummingbot.strategy_v2.life_liquidity.spot_quotes import QuoteCosts
    e, c, connector, wal, reservations, gate, old, watchdog, obs, now = await setup_inventory(tmp_path)
    try:
        quote = c._quote_action_planner
        original = quote.snapshot()
        quote.snapshot = lambda: replace(original, costs=QuoteCosts(*(D("0"),) * 6),
                                         loss_budget_status=c.execution_loss_status)
        quote.intent_id_factory = lambda: "mm-before-inventory"
        actions = quote.propose()
        assert len(actions) == 1, quote.reason_code
        runner = SimpleNamespace(controllers={"life": c}, logger=c.logger)
        if sent:
            assert StrategyV2Base._filter_authorized_actions(runner, actions) == actions
            child = OrderExecutor(old._strategy, actions[0].executor_config)
            child.get_order_price = lambda: child.config.price
            c._runner_orchestrator.active_executors["life"].append(child)
            assert c.on_runner_create_action_dispatched(actions[0])
            child.place_open_order()
            assert not await e.enter_inventory(operator_id="op")
            assert e.journal._read()["mode"] == "DRAINING"
            exchange_report(connector, child, now, terminal=True)
        assert await e.enter_inventory(operator_id="op"), e.reason_code
        assert StrategyV2Base._filter_authorized_actions(runner, actions) == []
        assert len(e.propose("inventory-after-mm")) == 1, e.reason_code
        assert quote.action_journal.verified_get("mm-before-inventory").state == ("RECONCILED" if sent else "REJECTED")
    finally:
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)


@pytest.mark.asyncio
async def test_reconciled_own_volume_is_excluded_even_if_source_reports_zero(tmp_path):
    e, c, connector, wal, reservations, gate, old, watchdog, obs, now = await setup_inventory(tmp_path)
    try:
        assert await e.enter_inventory(operator_id="op")
        child, _ = send_child(e, c, connector, old)
        exchange_report(connector, child, now, terminal=True)
        assert await e.reconcile()
        obs["value"] = replace(obs["value"], public_volume_base=D("3.1"), known_own_volume_base=D("0"))
        # Without subtracting our reconciled 0.2 fill, participation would permit another 0.1 child.
        assert e.propose("own-volume") == []
        assert e.reason_code == "INVENTORY_CHILD_BELOW_MINIMUM"
    finally:
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)


@pytest.mark.asyncio
async def test_inventory_mode_blocks_direct_reference_epoch_transition(tmp_path):
    e, c, connector, wal, reservations, gate, old, watchdog, obs, now = await setup_inventory(tmp_path)
    try:
        manager = c._order_safety_manager
        assert await e.enter_inventory(operator_id="op")
        manager.pause("OPERATOR_PAUSE")
        parent = manager.current_session
        with pytest.raises(ValueError, match="INVENTORY_ORDERS_UNRECONCILED"):
            manager.transition_reference(anchors=parent.anchors, model_version="new-model",
                                         config_version=parent.config_version + 1, old_orders_reconciled=True)
        assert manager.current_session.epoch == parent.epoch
        assert await e.return_to_mm(operator_id="op"), e.reason_code
        changed = manager.transition_reference(anchors=parent.anchors, model_version="new-model",
                                               config_version=parent.config_version + 1, old_orders_reconciled=True)
        assert changed.epoch == parent.epoch + 1
    finally:
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)


@pytest.mark.asyncio
async def test_inventory_mode_blocks_market_successor_until_operator_return(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.session import OrderReconciliation
    e, c, connector, wal, reservations, gate, old, watchdog, obs, now = await setup_inventory(tmp_path)
    try:
        manager = c._order_safety_manager
        parent = manager.current_session
        manager._commit(replace(manager._journal, on_expiry="switch_to_market_reference",
                                successor_duration_seconds=D("30")))
        assert await e.enter_inventory(operator_id="op")
        manager.wall_clock = lambda: parent.expires_at
        c._order_safety_gateway.clock = manager.wall_clock
        now["ms"] = int(parent.expires_at.timestamp() * 1000)
        assert manager.tick(reference_ready=True, all_gates_ready=True) == "TRANSITIONING"
        receipt = OrderReconciliation(parent.session_id, parent.epoch, parent.expires_at, True, (), (), (), True)
        assert manager.tick(reference_ready=True, all_gates_ready=True, reconciliation=receipt,
                            market_reference_ready=True, market_anchor_usdt=D("1")) == "TRANSITIONING"
        assert manager.reason_code == "INVENTORY_ORDERS_UNRECONCILED"
        assert await e.return_to_mm(operator_id="op"), e.reason_code
        assert manager.tick(reference_ready=True, all_gates_ready=True, reconciliation=receipt,
                            market_reference_ready=True, market_anchor_usdt=D("1")) == "ACTIVE"
        assert manager.current_session.epoch == parent.epoch + 1
        with pytest.raises(ValueError, match="INVENTORY_RECONCILIATION_BINDING_INVALID"):
            manager.bind_inventory_reconciliation(lambda *_: True)
        c._inventory_executor = None
        assert not manager._inventory_scope_ready(parent.session_id, parent.epoch)
        c._inventory_executor = e
        e.allows_mm = lambda: (_ for _ in ()).throw(ValueError("damaged mode"))
        assert not manager._inventory_scope_ready(parent.session_id, parent.epoch)
    finally:
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)


@pytest.mark.asyncio
async def test_reconciliation_fences_queued_inventory_send_during_account_snapshot(tmp_path):
    from types import SimpleNamespace

    from hummingbot.strategy.strategy_v2_base import StrategyV2Base
    e, c, connector, wal, reservations, gate, old, watchdog, obs, now = await setup_inventory(tmp_path)
    task = None
    try:
        assert await e.enter_inventory(operator_id="op")
        actions = e.propose("queued-during-proof")
        assert len(actions) == 1
        started, release = asyncio.Event(), asyncio.Event()
        original = c._order_safety_gateway.reconcile

        async def paused(*args):
            started.set()
            await release.wait()
            return await original(*args)

        c._order_safety_gateway.reconcile = paused
        task = asyncio.create_task(e.reconcile())
        await started.wait()
        runner = SimpleNamespace(controllers={"life": c}, logger=c.logger)
        assert StrategyV2Base._filter_authorized_actions(runner, actions) == []
        assert connector.sent == []
        release.set()
        assert await task, e.reason_code
        assert e.ledger.pending_base == 0
    finally:
        watchdog.cancel()
        if task is not None and not task.done():
            task.cancel()
        await asyncio.gather(watchdog, *([task] if task is not None else []), return_exceptions=True)
