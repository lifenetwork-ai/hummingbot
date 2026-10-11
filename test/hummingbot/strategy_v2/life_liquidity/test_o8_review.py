"""Offline regressions for the 2026-10-11 implementation review."""
import asyncio
import time
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.o7_scenarios import publish, send, system
from test.hummingbot.strategy_v2.life_liquidity.test_carry_monitor import refresh
from test.hummingbot.strategy_v2.life_liquidity.test_hedge_coordinator import setup_coordinator
from test.hummingbot.strategy_v2.life_liquidity.test_joint_recovery import setup_recovery
from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital_runner import dispatch, setup_shared
from test.hummingbot.strategy_v2.life_liquidity.test_spot_session_replay import FakeTradingOkx

import pytest

from hummingbot.core.data_type.common import PositionAction
from hummingbot.strategy_v2.life_liquidity.joint_recovery import RecoveryTrade
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, RiskLimits, SpotIntent, _Reservation
from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalClaim


@pytest.mark.asyncio
async def test_active_runner_poll_preserves_joint_authority(tmp_path):
    async with system(tmp_path) as f:
        c = f.c
        for _ in range(3):
            f.v[6].advance(.251)
            c.on_runner_safety_tick(f.v[6].mono, c._runner_orchestrator)
            assert await c._joint_reconciliation_task, f.joint.reason_code
            assert f.joint.allocation_ready()
            assert c._order_safety_manager.state == "ACTIVE"


@pytest.mark.asyncio
async def test_healthy_refresh_does_not_revoke_still_fresh_receipt(tmp_path):
    async with system(tmp_path) as f:
        entered, release = asyncio.Event(), asyncio.Event()

        async def collect():
            entered.set()
            await release.wait()
            return f.history["value"]

        f.joint.collect = collect
        task = asyncio.create_task(f.joint.reconcile())
        try:
            await entered.wait()
            assert f.joint.allocation_ready()
            # A changed account sequence must revoke the old receipt during I/O.
            obs = f.v[8]["snapshot"]
            f.v[8]["snapshot"] = replace(obs, sequence=obs.sequence + 1)
            assert not f.joint.allocation_ready()
            refresh(f.v, f.feeds)
            f.history["value"] = replace(f.history["value"], snapshot_sequence=f.v[8]["snapshot"].sequence)
            release.set()
            assert await task, f.joint.reason_code
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("side", ["BUY", "SELL"])
@pytest.mark.parametrize("levels", [1, 3])
def test_owned_pending_maker_does_not_pause_balanced_hedge(tmp_path, side, levels):
    h, v, _, _ = setup_coordinator(tmp_path)
    h.update_target("balanced", expected_version=0, target_base=D("11"))
    assert h.allows_spot()
    from hummingbot.core.data_type.common import TradeType
    for level in range(levels):
        cfg = v[10].model_copy(update={"id": "maker-" + str(level), "amount": D(".5"),
                                       "side": TradeType[side], "level_id": str(level)})
        dispatch(v[0], v[9], cfg)
    obs = v[8]["snapshot"]
    v[8]["snapshot"] = replace(obs, sequence=obs.sequence + 1, pending=tuple(v[1].claims().values()))
    v[7]["value"] = replace(v[7]["value"], snapshot_sequence=obs.sequence + 1)
    assert v[1].check().allowed
    assert h.allows_spot(), h.reason_code


@pytest.mark.asyncio
async def test_spot_sell_fill_then_safety_tick_allows_bounded_open_hedge(tmp_path):
    async with system(tmp_path) as f:
        q = f.c._quote_action_planner
        q.max_actions_per_tick = 100
        ids = iter("quote-" + str(i) for i in range(20))
        q.intent_id_factory = lambda: next(ids)
        actions = q.propose()
        chosen = next(a for a in actions if a.executor_config.side.name == "SELL")
        for action in actions:
            if action is not chosen:
                q.on_runner_action_rejected(action)
        claim, _ = send(f, chosen)
        publish(f, claim, life="9.5", cash="10.504", long="1")
        # Reconcile through the real scheduler, rather than a manual runner halt.
        for _ in range(4):
            f.c.on_runner_safety_tick(f.v[6].mono, f.c._runner_orchestrator)
            if f.c._joint_reconciliation_task:
                assert await f.c._joint_reconciliation_task, f.joint.reason_code
            if f.c.order_safety_task:
                await f.c.order_safety_task
        q._release_reconciled_proposals()
        hedges = f.hedge.propose()
        assert len(hedges) == 1, f.hedge.reason_code
        assert hedges[0].executor_config.position_action == PositionAction.OPEN
        assert hedges[0].executor_config.amount == D(".5")
        send(f, hedges[0])


@pytest.mark.asyncio
async def test_stuck_swap_cancel_cannot_delay_spot_cancel(tmp_path):
    joint, v, _, _ = setup_recovery(tmp_path, values=setup_shared(
        tmp_path, collateral="100", budget_capacity=500, spot_route=FakeTradingOkx()))
    c, _, sender, spot, _, _, _, _, _, strategy, sc, pc = v
    assert await joint.reconcile()
    dispatch(c, strategy, sc)
    dispatch(c, strategy, pc, sender.propose(pc))
    entered = asyncio.Event()

    async def blocked(_):
        entered.set()
        await asyncio.Event().wait()

    joint.cancel_swap = blocked
    c._order_safety_manager.pause("REVIEW_PAUSE")
    task = asyncio.create_task(c._cancel_and_reconcile_orders())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.sleep(.05)
        assert spot.cancels  # Must start while SWAP transport is still blocked.
        assert c._order_safety_wal.get(sc.id).cancel_requested
        assert sc.id in v[1].claim_ids() and pc.id in v[1].claim_ids()
        await asyncio.wait_for(task, 2.5)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("close_id", ["9", "09", "opaque-close"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("close_first", [False, True])
def test_same_millisecond_causal_close_open_history(tmp_path, close_id, reverse, close_first):
    recovery, v, bundle, _ = setup_recovery(tmp_path)
    with recovery.journal.locked() as state:
        ms = state["anchor"]["observed_at_ms"]
        close = CapitalClaim("close", "closewire", "12345", "s", 1, 1, 1,
                             "SWAP", "SELL", "CLOSE", D(".25"), D("2"), 1)
        opening = replace(close, intent_id="open", wire_id="openwire", side="BUY", position_action="OPEN")
        tc = RecoveryTrade(close_id, D(".25"), D("2"), "USDT", D("0"), D(".25"), ms, sequence=1)
        to = replace(tc, trade_id="10", realized_pnl_quote=D("0"), sequence=2)
        tc = replace(tc, sequence=1 if close_first else 2, realized_pnl_quote=D(".25") if close_first else D(".2"))
        to = replace(to, sequence=2 if close_first else 1)
        obs = replace(v[8]["snapshot"], spot_cash_quote=D("10.25") if close_first else D("10.2"), mark_price_usdt=D("2"), long_base=D("1"))
        _, valuation = recovery._financial_replay(bundle["value"], state, obs,
                                                  dict(reversed([(("SWAP", close_id), (close, tc)), (("SWAP", "10"), (opening, to))])) if reverse else
                                                  {("SWAP", close_id): (close, tc), ("SWAP", "10"): (opening, to)})
        assert D(valuation["nav_quote"]) == D("21")
        assert D(valuation["unrealized_quote"]) == (D(".75") if close_first else D(".8"))
        with pytest.raises(ValueError, match="JOINT_TRADE_ORDER_AMBIGUOUS"):
            recovery._financial_replay(bundle["value"], state, obs, {
                ("SWAP", "9"): (close, replace(tc, sequence=None)),
                ("SWAP", "10"): (opening, replace(to, sequence=None))})


def test_ten_thousand_terminal_reservations_restore_within_offline_budget(tmp_path):
    # Local CPU/filesystem budget, not a live latency guarantee.
    n = 10000
    limits = RiskLimits(D("0"), D("100000"), D("100000"), D("100000"))
    path = tmp_path / "ledger.json"
    ledger = ReservationLedger(life_balance=D(n), usdt_balance=D("100000"), limits=limits, path=path)
    reservations = {str(i): _Reservation(SpotIntent(str(i), "BUY", D("1"), D("1"), "s", 1),
                                         D("0"), D("1"), "TERMINAL") for i in range(n)}
    trades = {str(i): (str(i), D("1"), D("1")) for i in range(n)}
    ledger._commit(D(n), D("100000"), reservations, trades)
    start = time.perf_counter()
    restored = ReservationLedger.restore(path, limits=limits)
    elapsed = time.perf_counter() - start
    assert restored.life_balance == D(n) and len(restored._trades) == n
    assert elapsed < 1.0, f"offline restore exceeded 1000 ms: {elapsed:.3f}s"


@pytest.mark.asyncio
@pytest.mark.parametrize("product", ["SPOT", "SWAP"])
@pytest.mark.parametrize("fault", ["blocked", "error"])
async def test_cancel_isolation_in_both_directions(tmp_path, product, fault):
    joint, v, _, canceled = setup_recovery(tmp_path, values=setup_shared(
        tmp_path, collateral="100", budget_capacity=500, spot_route=FakeTradingOkx()))
    c, pool, sender, spot, _, _, _, _, _, strategy, sc, pc = v
    assert await joint.reconcile()
    dispatch(c, strategy, sc)
    dispatch(c, strategy, pc, sender.propose(pc))
    entered = asyncio.Event()

    async def failing(*_):
        entered.set()
        if fault == "error":
            raise ConnectionError("synthetic transport failure")
        await asyncio.Event().wait()

    if product == "SWAP":
        joint.cancel_swap = failing
    else:
        spot.cancel_by_client_id = failing
    c._order_safety_manager.pause("ISOLATION")
    started = time.perf_counter()
    task = asyncio.create_task(c._cancel_and_reconcile_orders())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        while not (spot.cancels if product == "SWAP" else canceled):
            assert time.perf_counter() - started < .25
            await asyncio.sleep(.001)
        assert time.perf_counter() - started < .25  # Frozen synthetic healthy-product deadline.
        await asyncio.wait_for(task, 2.5)
        assert set(pool.claim_ids()) == {sc.id, pc.id}
        assert c._order_safety_wal.get(sc.id).state != "TERMINAL"
        assert sender.wal.get(pc.id).state != "TERMINAL"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["scope", "risk_epoch", "invalid_risk_epoch", "config", "collector", "age"])
async def test_refresh_receipt_revocation(tmp_path, fault):
    async with system(tmp_path) as f:
        assert f.joint.allocation_ready()
        if fault == "scope":
            f.c._runner_scope_invalid = True
        elif fault == "risk_epoch":
            f.joint.sender.risk_epoch = lambda: 999
        elif fault == "invalid_risk_epoch":
            f.joint.sender.risk_epoch = lambda: True
        elif fault == "config":
            manager = f.c._order_safety_manager
            manager.update_duration("5h", config_version=2)
        elif fault == "age":
            f.v[6].advance(2)
        else:
            async def failed():
                raise ConnectionError("synthetic collector failure")
            f.joint.collect = failed
            assert not await f.joint.reconcile()
        assert not f.joint.allocation_ready()


@pytest.mark.parametrize("fault", ["disk", "memory", "symlink"])
def test_verified_ledger_cache_does_not_hide_corruption(tmp_path, fault):
    _, v, _, _ = setup_coordinator(tmp_path)
    ledger = v[0]._order_safety_reservations
    first = ledger.verified_disk()
    assert ledger.verified_disk() is first
    if fault == "disk":
        ledger.path.write_text("corrupted")
        with pytest.raises((ValueError, KeyError)):
            ledger.verified_disk()
    elif fault == "memory":
        ledger.life_balance += D("1")
        assert not v[0]._hedge_coordinator.allows_spot()
    else:
        target = tmp_path / "target"
        ledger.path.rename(target)
        ledger.path.symlink_to(target)
        with pytest.raises(ValueError):
            ledger.verified_disk()


@pytest.mark.asyncio
@pytest.mark.parametrize("fills", [100, 500])
async def test_many_fills_qualification_restart_and_page_order(tmp_path, fills):
    from test.hummingbot.strategy_v2.life_liquidity.test_joint_recovery import cold_restore

    from hummingbot.strategy_v2.life_liquidity.joint_recovery import RecoveryOrder
    joint, v, history, _ = setup_recovery(tmp_path)
    assert await joint.reconcile()
    c, pool, _, _, _, _, _, account, feed, strategy, sc, _ = v
    cfg = sc.model_copy(update={"amount": D(".5")})
    dispatch(c, strategy, cfg)
    claim = pool.claims()[cfg.id]
    ms = history["value"].observed_at_ms
    trades = tuple(RecoveryTrade(str(i), D(".5") / fills, D("1"), "USDT", D("0"), D("0"), ms,
                                 sequence=i) for i in range(fills))
    order = RecoveryOrder(claim, "exchange-scale", "filled", trades)
    feed["snapshot"] = replace(feed["snapshot"], sequence=2, spot_life_base=D("10.5"), spot_cash_quote=D("9.5"))
    account["value"] = replace(account["value"], snapshot_sequence=2)
    history["value"] = replace(history["value"], snapshot_sequence=2, orders=(order,))
    start = time.perf_counter()
    assert await joint.reconcile(), joint.reason_code
    assert time.perf_counter() - start < 2.0  # Offline qualification + durable settlement, with CI headroom.
    expected = joint.measure()
    # Reversed page arrival with the same causal sequence is the same history.
    history["value"] = replace(history["value"], orders=(replace(order, trades=tuple(reversed(trades))),))
    assert await joint.reconcile(), joint.reason_code
    restored, restored_values = cold_restore(joint, v)
    assert await restored.reconcile(), restored.reason_code
    assert restored.measure() == expected
    assert pool.claim_ids() == ()
    assert len(restored_values[0]._order_safety_reservations._trades) == fills
    mutated = list(trades)
    mutated[0] = replace(mutated[0], sequence=999)
    history["value"] = replace(history["value"], orders=(replace(order, trades=tuple(mutated)),))
    assert not await restored.reconcile()
    assert not restored.allocation_ready()


@pytest.mark.parametrize("fault", ["unknown", "wire", "unaffordable", "wal"])
def test_pending_maker_risk_and_identity_fail_closed(tmp_path, fault):
    h, v, _, _ = setup_coordinator(tmp_path)
    h.update_target("balanced", expected_version=0, target_base=D("11"))
    cfg = v[10].model_copy(update={"amount": D(".5")})
    dispatch(v[0], v[9], cfg)
    owned = v[1].claims()[cfg.id]
    pending = replace(owned, intent_id="foreign") if fault == "unknown" else (
        replace(owned, wire_id="foreign-wire") if fault == "wire" else owned)
    obs = v[8]["snapshot"]
    v[8]["snapshot"] = replace(obs, sequence=obs.sequence + 1, pending=(pending,),
                               collateral_quote=D("0") if fault == "unaffordable" else obs.collateral_quote)
    v[7]["value"] = replace(v[7]["value"], snapshot_sequence=obs.sequence + 1)
    if fault == "wal":
        v[0]._order_safety_wal.path.unlink()
    assert not h.allows_spot()
    assert cfg.id in v[1].retained_claim_ids()


@pytest.mark.asyncio
@pytest.mark.parametrize("close_first", [False, True])
@pytest.mark.parametrize("ambiguous", [False, True])
async def test_tied_swap_history_release_and_cold_restart(tmp_path, close_first, ambiguous):
    from test.hummingbot.strategy_v2.life_liquidity.test_joint_recovery import cold_restore

    from hummingbot.core.data_type.common import TradeType
    from hummingbot.strategy_v2.life_liquidity.joint_recovery import RecoveryOrder

    joint, v, history, _ = setup_recovery(tmp_path)
    c, capital, sender, _, _, _, _, account, feed, strategy, _, pc = v
    assert await joint.reconcile()
    configs = [pc.model_copy(update={"id": "tie-close", "amount": D(".25"), "price": D("2"),
                                     "side": TradeType.SELL, "position_action": PositionAction.CLOSE}),
               pc.model_copy(update={"id": "tie-open", "amount": D(".25"), "price": D("2"),
                                     "side": TradeType.BUY, "position_action": PositionAction.OPEN})]
    for cfg in configs:
        dispatch(c, strategy, cfg, sender.propose(cfg))
    claims = capital.claims()
    ms = history["value"].observed_at_ms
    pnl = D(".25") if close_first else D(".2")
    trades = [RecoveryTrade("9", D(".25"), D("2"), "USDT", D("0"), pnl, ms,
                            sequence=1 if close_first else 2),
              RecoveryTrade("10", D(".25"), D("2"), "USDT", D("0"), D("0"), ms,
                            sequence=2 if close_first else 1)]
    orders = tuple(RecoveryOrder(claims[cfg.id], "exchange-" + cfg.id, "filled", (trade,))
                   for cfg, trade in zip(configs, trades))
    feed["snapshot"] = replace(feed["snapshot"], sequence=2, spot_cash_quote=D("10") + pnl,
                               mark_price_usdt=D("2"), index_price_usdt=D("2"))
    account["value"] = replace(account["value"], snapshot_sequence=2)
    history["value"] = replace(history["value"], snapshot_sequence=2, orders=tuple(reversed(orders)))
    if ambiguous:
        history["value"] = replace(history["value"], orders=tuple(
            replace(order, trades=(replace(order.trades[0], sequence=None),)) for order in orders))
        assert not await joint.reconcile()
        assert joint.reason_code == "JOINT_TRADE_ORDER_AMBIGUOUS"
        assert set(capital.claim_ids()) == set(claims)
        history["value"] = replace(history["value"], orders=orders)
    assert await joint.reconcile(), joint.reason_code
    assert not capital.claim_ids()
    assert joint.measure().nav_quote == D("31")
    restored, rv = cold_restore(joint, v)
    history["value"] = replace(history["value"], orders=tuple(reversed(orders)))
    assert await restored.reconcile(), restored.reason_code
    assert restored.measure() == joint.measure()
    assert rv[0]._order_safety_reservations.usdt_balance == D("10") + pnl
    assert not rv[1].claim_ids()


@pytest.mark.parametrize("fault", ["memory", "same_stat", "stale_writer", "invalid_record"])
def test_action_cache_keeps_independent_verified_image(tmp_path, fault):
    import os

    from hummingbot.strategy_v2.life_liquidity.action_journal import QuoteActionJournal, QuoteActionRecord

    path = tmp_path / "actions.json"
    journal = QuoteActionJournal(path, account_uid="12345")
    journal.initialize_empty()
    record = QuoteActionRecord("intent", "life", "s", 1, 1, "LIFE-USDT", "BUY", 0)
    journal.claim_batch([record])
    journal = QuoteActionJournal(path, account_uid="12345")
    assert journal.verified_get("intent") == record  # Warm a restored writer too.
    if fault == "memory":
        journal._records["intent"] = replace(record, side="SELL")
        with pytest.raises(ValueError):
            journal.verified_records()
    elif fault == "same_stat":
        original = path.stat()
        path.write_bytes(path.read_bytes().replace(b'"BUY"', b'"BAD"'))
        os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
        with pytest.raises(ValueError):
            journal.verified_records()
    elif fault == "invalid_record":
        with pytest.raises(ValueError):
            journal._commit({"intent": replace(record, side="INVALID")})
        assert journal.verified_get("intent") == record
    else:
        other = QuoteActionJournal(path, account_uid="12345")
        other.transition("intent", expected="PROPOSED", state="REJECTED")
        with pytest.raises(ValueError):
            journal.transition("intent", expected="PROPOSED", state="DISPATCHED")


@pytest.mark.parametrize("sequences,reason", [((None, None), "JOINT_TRADE_ORDER_AMBIGUOUS"),
                                              ((1, 1), "JOINT_TRADE_ORDER_AMBIGUOUS"),
                                              ((True, 2), "JOINT_TRADE_SEQUENCE_INVALID"),
                                              ((-1, 2), "JOINT_TRADE_SEQUENCE_INVALID")])
def test_same_direction_swap_ties_also_require_valid_causal_evidence(tmp_path, sequences, reason):
    joint, v, history, _ = setup_recovery(tmp_path)
    with joint.journal.locked() as state:
        ms = state["anchor"]["observed_at_ms"]
        claim = CapitalClaim("open", "wire", "12345", "s", 1, 1, 1,
                             "SWAP", "BUY", "OPEN", D(".5"), D("1"), 1)
        trades = {("SWAP", str(i)): (claim, RecoveryTrade(str(i), D(".25"), D("1"), "USDT", D("0"),
                                                          D("0"), ms, sequence=sequence))
                  for i, sequence in enumerate(sequences)}
        with pytest.raises(ValueError, match=reason):
            joint._financial_replay(history["value"], state, replace(v[8]["snapshot"], long_base=D("1.5")), trades)


@pytest.mark.asyncio
@pytest.mark.parametrize("owned", [False, True])
async def test_active_unarmed_spot_child_requires_a_captured_proposal(tmp_path, owned):
    from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor

    async with system(tmp_path) as f:
        config = (f.c._quote_action_planner.propose()[0].executor_config if owned else
                  f.v[10].model_copy(update={"id": "foreign-unarmed", "amount": D("1")}))
        child = OrderExecutor(f.v[9], config)
        f.c._runner_orchestrator.active_executors["life"].append(child)
        assert await f.joint.reconcile() is owned


@pytest.mark.asyncio
async def test_inflight_joint_collection_cannot_block_either_product_cancel(tmp_path):
    joint, v, _, canceled = setup_recovery(tmp_path, values=setup_shared(
        tmp_path, collateral="100", budget_capacity=500, spot_route=FakeTradingOkx()))
    c, pool, sender, spot, _, _, _, _, _, strategy, sc, pc = v
    assert await joint.reconcile()
    dispatch(c, strategy, sc)
    dispatch(c, strategy, pc, sender.propose(pc))
    entered = asyncio.Event()

    async def blocked_collect():
        entered.set()
        await asyncio.Event().wait()

    joint.collect = blocked_collect
    collecting = asyncio.create_task(joint.reconcile())
    await entered.wait()
    c._order_safety_manager.pause("COLLECTION_STUCK")
    task = asyncio.create_task(c._cancel_and_reconcile_orders())
    try:
        await asyncio.sleep(.05)
        assert spot.cancels
        assert canceled  # SWAP cancel must not queue behind the collector mutex.
        assert set(pool.claim_ids()) == {sc.id, pc.id}
    finally:
        collecting.cancel()
        task.cancel()
        await asyncio.gather(collecting, task, return_exceptions=True)
