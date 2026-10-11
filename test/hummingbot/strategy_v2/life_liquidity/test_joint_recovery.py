"""O.7.5 synthetic complete-history recovery through actual executor children."""

import asyncio
from dataclasses import replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital_runner import dispatch, setup_shared
from test.hummingbot.strategy_v2.life_liquidity.test_swap_executor import wire_for
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from controllers.generic.life_liquidity import LifeLiquidityController
from hummingbot.core.data_type.common import OrderType, PositionAction, PositionMode, TradeType
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount
from hummingbot.core.event.events import OrderCancelledEvent, OrderFilledEvent
from hummingbot.strategy_v2.life_liquidity.fill_attribution import IndependentFillObservation, ReconciledFillAttributor
from hummingbot.strategy_v2.life_liquidity.joint_recovery import (
    JointRecovery,
    RecoveryBundle,
    RecoveryOrder,
    RecoveryPayment,
    RecoveryTrade,
)
from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetLedger
from hummingbot.strategy_v2.life_liquidity.order_gateway import OkxSpotOrderGateway, SpotReservationReconciler
from hummingbot.strategy_v2.life_liquidity.protected_swap import ProtectedSwapExecutorSender
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
from hummingbot.strategy_v2.life_liquidity.session import OrderReconciliation, SessionManager, SessionStore
from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalClaim, SharedCapitalAuthority
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL

D = Decimal


def setup_recovery(tmp_path, *, values=None):
    values = values or setup_shared(tmp_path, collateral="100", budget_capacity=50)
    c, capital, sender, spot, swap, wal, clock, account, feed, strategy, sc, pc = values
    if not c._order_safety_wal.path.exists():
        c._order_safety_wal.initialize_empty()
    c._order_safety_gateway.apply_fills = SpotReservationReconciler(
        c._order_safety_wal, c._order_safety_reservations, require_fees=True).apply_fills
    now = int(clock.wall.timestamp() * 1000)
    bundle = {"value": RecoveryBundle("12345", now, now, 1, (), (), (), True, True, "okx_joint_reconciled")}

    async def collect():
        return bundle["value"]

    canceled = []

    async def cancel(claim):
        canceled.append(claim.wire_id)

    recovery = JointRecovery(tmp_path / "joint_recovery.json", controller=c, collect=collect,
                             cancel_swap=cancel, clock_ms=lambda: int(clock.wall.timestamp() * 1000),
                             max_age_ms=1000, cancel_retry_ms=100, max_cancels_per_cycle=2, poll_interval_ms=250,
                             opening_long_entry=D("1"),
                             opening_short_entry=D("1") if feed["snapshot"].short_base > 0 else D("0"), create=True)
    c.install_joint_recovery(recovery)
    return recovery, values, bundle, canceled


def send_close(values):
    c, capital, sender, spot, swap, wal, clock, account, feed, strategy, sc, pc = values
    pc = pc.model_copy(update={"amount": D("0.5"), "position_action": PositionAction.CLOSE})
    dispatch(c, strategy, pc, sender.propose(pc))
    swap.sent[0]["pre_send_check"](wire_for(pc, swap.sent[0]["order_id"], PositionMode.ONEWAY))
    return capital.claims()[pc.id]


def report(values, bundle, claim, *, terminal=True, quantity="0.25", fee="-0.001"):
    c, capital, sender, spot, swap, wal, clock, account, feed, strategy, sc, pc = values
    now = int(clock.wall.timestamp() * 1000)
    trade = RecoveryTrade("trade-1", D(quantity), D("1"), "USDT", D(fee), D("0"), now)
    proof = RecoveryOrder(claim, "exchange-1", "canceled" if terminal else "partially_filled", (trade,))
    feed["snapshot"] = replace(feed["snapshot"], sequence=2, long_base=D("1") - D(quantity),
                               spot_cash_quote=D("10") + D(fee), pending=() if terminal else (claim,))
    account["value"] = replace(account["value"], net_contracts=(D("1") - D(quantity)) / D("0.25"), snapshot_sequence=2)
    bundle["value"] = replace(bundle["value"], snapshot_sequence=2, orders=(proof,),
                              pending_wire_ids=() if terminal else (claim.wire_id,))
    return proof


@pytest.mark.asyncio
async def test_lost_ack_partial_close_releases_only_after_full_account_and_fill_proof(tmp_path):
    recovery, v, bundle, _ = setup_recovery(tmp_path)
    assert await recovery.reconcile()
    claim = send_close(v)
    assert v[5].get(claim.intent_id).state == "SEND_UNKNOWN"
    proof = report(v, bundle, claim, terminal=False)
    assert await recovery.reconcile()
    assert v[5].get(claim.intent_id).state == "ACKED"
    assert v[1].claim_ids() == (claim.intent_id,)
    assert not recovery.successor_ready(claim.session_id, claim.epoch)
    assert v[0]._order_safety_reservations.usdt_balance == D("9.999")
    # A cancel ACK is not terminal. The next complete historical report is.
    v[8]["snapshot"] = replace(v[8]["snapshot"], sequence=3, pending=())
    v[7]["value"] = replace(v[7]["value"], snapshot_sequence=3)
    bundle["value"] = replace(bundle["value"], snapshot_sequence=3,
                              orders=(replace(proof, state="canceled"),), pending_wire_ids=())
    assert await recovery.reconcile()
    assert v[5].get(claim.intent_id).state == "TERMINAL"
    assert v[1].claim_ids() == ()
    assert v[8]["snapshot"].long_base == D("0.75")  # Remaining position is never reported closed.
    assert recovery.successor_ready(claim.session_id, claim.epoch)
    assert await recovery.reconcile()  # Immutable fill identity is applied once.
    assert v[0]._order_safety_reservations.usdt_balance == D("9.999")
    assert not v[1].reserve(claim).allowed  # Settled intent IDs cannot allocate again.


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["uid", "scope", "runner", "history", "pending", "algo", "cash", "position", "fee", "missing", "exchange", "duplicate"])
async def test_unqualified_terminal_evidence_keeps_all_capital_and_blocks_both_routes(tmp_path, fault):
    recovery, v, bundle, _ = setup_recovery(tmp_path)
    assert await recovery.reconcile()
    claim = send_close(v)
    proof = report(v, bundle, claim)
    if fault in ("uid", "scope", "runner", "history", "pending", "algo", "missing"):
        edits = {"uid": {"account_uid": "999"}, "scope": {"scope_complete": False},
                 "runner": {"runner_scope_complete": False}, "history": {"coverage_from_ms": bundle["value"].observed_at_ms + 1},
                 "pending": {"pending_wire_ids": ("foreign",)}, "algo": {"algo_wire_ids": ("algo",)},
                 "missing": {"orders": ()}}
        bundle["value"] = replace(bundle["value"], **edits[fault])
    elif fault in ("cash", "position"):
        v[8]["snapshot"] = replace(v[8]["snapshot"], **({"spot_cash_quote": D("10")} if fault == "cash" else {"long_base": D("1")}))
    elif fault == "fee":
        bundle["value"] = replace(bundle["value"], orders=(replace(proof, trades=(replace(proof.trades[0], fee_currency="BTC"),)),))
    elif fault == "exchange":
        v[5].acknowledge(claim.intent_id, "other-exchange-id")
    else:
        bundle["value"] = replace(bundle["value"], orders=(proof, proof))
    assert not await recovery.reconcile()
    assert v[1].claim_ids() == (claim.intent_id,)
    assert not recovery.allocation_ready()
    assert not recovery.successor_ready(claim.session_id, claim.epoch)
    assert not v[1].reserve(replace(claim, intent_id="new", wire_id="new")).allowed


@pytest.mark.asyncio
async def test_cancel_attempt_is_durable_bounded_and_ack_never_releases(tmp_path):
    recovery, v, bundle, canceled = setup_recovery(tmp_path)
    assert await recovery.reconcile()
    claim = send_close(v)
    await recovery.cancel_working_swaps()
    await recovery.cancel_working_swaps()
    record = v[5].get(claim.intent_id)
    assert record.cancel_requested and record.cancel_attempts == 1
    assert canceled == [claim.wire_id]
    assert v[1].claim_ids() == (claim.intent_id,)
    assert record.state == "SEND_UNKNOWN"


@pytest.mark.asyncio
async def test_disconnect_requires_fresh_proof_and_explicit_operator_rearm(tmp_path):
    recovery, v, bundle, _ = setup_recovery(tmp_path)
    assert await recovery.reconcile()
    claim = send_close(v)
    report(v, bundle, claim)

    async def disconnected():
        raise ConnectionError("offline disconnect")

    collector = recovery.collect
    recovery.collect = disconnected
    assert not await recovery.reconcile()
    recovery.collect = collector
    assert await recovery.reconcile()
    assert not recovery.allocation_ready()
    recovery.manual_rearm(operator_id="offline-operator")
    assert recovery.allocation_ready()


def cold_restore(recovery, v):
    old, capital, sender, spot, swap, wal, clock, account, feed, strategy, sc, pc = v
    c = LifeLiquidityController(old.config, MagicMock(), MagicMock())
    sw = IntentWAL(old._order_safety_wal.path)
    ledger = ReservationLedger.restore(old._order_safety_reservations.path, limits=old._order_safety_reservations.limits)
    manager = SessionManager(SessionStore(old._order_safety_manager.store.path), wall_clock=lambda: clock.wall,
                             monotonic_clock=lambda: clock.mono, max_reconciliation_age_ms=1000)
    adapter = SpotReservationReconciler(sw, ledger, require_fees=True)
    gateway = OkxSpotOrderGateway(spot, sw, trading_pair="LIFE-USDT", clock=lambda: clock.wall,
                                  apply_fills=adapter.apply_fills, confirm_terminal=adapter.confirm_terminal,
                                  on_cancel_requested=adapter.request_cancel, on_unknown=adapter.mark_unknown,
                                  account_check=lambda: True)
    c.install_order_safety(manager, gateway, sw, reservations=ledger)
    cap = SharedCapitalAuthority(capital.journal.path, policy=capital.policy, observation=capital.observation,
                                 clock_ms=capital.clock_ms, create=False)
    c.install_shared_capital_authority(cap, recover=True)
    restored = ProtectedSwapExecutorSender(
        c, swap, IntentWAL(wal.path), sender.contract, sender.journal.path,
        account_observation=sender.account_observation, authorize_reservation=sender.authorize_reservation,
        risk_epoch=sender.risk_epoch, clock_ms=sender.clock_ms, max_age_ms=sender.max_age_ms,
        account_mode=sender.account_mode, request_budget=sender.request_budget, create=False)
    c.install_protected_swap_sender(restored)
    if old._hedge_coordinator is not None:
        from hummingbot.strategy_v2.life_liquidity.hedge_coordinator import HedgeCoordinator
        h = old._hedge_coordinator
        coordinator = HedgeCoordinator(c, h.journal.path, policy=h.policy, market=h.market,
                                       settlement=h.settlement, create=False)
        c.install_hedge_coordinator(coordinator)
    joint = JointRecovery(recovery.journal.path, controller=c, collect=recovery.collect, cancel_swap=recovery.cancel_swap,
                          clock_ms=recovery.clock_ms, max_age_ms=1000, cancel_retry_ms=100, max_cancels_per_cycle=2, poll_interval_ms=250,
                          opening_long_entry=D("1"), opening_short_entry=D("0"), create=False)
    c.install_joint_recovery(joint)
    strategy = SimpleNamespace(connectors={"okx": spot, "okx_perpetual": swap}, controllers={"life": c})
    c.trading_permissions_ready = lambda: True
    c.allow_create_executor_actions = lambda: c._joint_recovery_ready()
    return joint, (c, cap, restored, spot, swap, restored.wal, clock, account, feed, strategy, sc, pc)


@pytest.mark.asyncio
@pytest.mark.parametrize("cut", ["before_apply", "cash", "wal", "capital", "completion", "clean"])
async def test_cold_restart_replays_cross_journal_cutpoints_without_resend_or_double_fee(tmp_path, monkeypatch, cut):
    recovery, v, bundle, _ = setup_recovery(tmp_path)
    assert await recovery.reconcile()
    claim = send_close(v)
    report(v, bundle, claim)
    if cut != "clean":
        target, name = {"before_apply": (recovery, "_apply"),
                        "cash": (v[0]._order_safety_reservations, "apply_joint_snapshot"),
                        "wal": (v[5], "mark_terminal"), "capital": (v[1], "settle"),
                        "completion": (recovery.journal, "commit")}[cut]
        original = getattr(target, name)

        def interrupted(*args, **kwargs):
            if cut == "completion" and args[0]["phase"] != "COMMITTED":
                return original(*args, **kwargs)
            if cut in ("cash", "wal", "capital"):
                original(*args, **kwargs)
            raise OSError("synthetic crash")

        with monkeypatch.context() as patch:
            patch.setattr(target, name, interrupted)
            assert not await recovery.reconcile()
            assert not recovery.allocation_ready()
    else:
        assert await recovery.reconcile()
    joint, fresh = cold_restore(recovery, v)
    assert not joint.allocation_ready()
    assert fresh[2]._permits == {} and not fresh[2]._runtime_enabled
    assert await joint.reconcile(), joint.reason_code
    assert fresh[1].claim_ids() == () and fresh[5].get(claim.intent_id).state == "TERMINAL"
    assert fresh[0]._order_safety_reservations.usdt_balance == D("9.999")
    assert not fresh[2].authorizes_config(v[11])
    assert len(fresh[4].sent) == 1
    joint.manual_rearm(operator_id="operator")
    assert joint.allocation_ready()
    assert not fresh[2].authorizes_config(v[11])
    assert not fresh[1].reserve(claim).allowed
    assert LifeLiquidityController.trading_permissions_ready(fresh[0]) is False


@pytest.mark.asyncio
async def test_spot_only_receipt_cannot_activate_successor_until_swap_settles(tmp_path):
    recovery, v, bundle, _ = setup_recovery(tmp_path)
    manager = v[0]._order_safety_manager
    # Explicitly choose continue-market before any order exists.
    record = manager._journal.primary
    manager._commit(replace(manager._journal, on_expiry="continue_market", successor_duration_seconds=D("30")))
    assert await recovery.reconcile()
    claim = send_close(v)
    v[6].advance(10)
    assert manager.tick(reference_ready=True, all_gates_ready=True) == "TRANSITIONING"
    now = int(v[6].wall.timestamp() * 1000)
    v[8]["snapshot"] = replace(v[8]["snapshot"], observed_at_ms=now, sequence=2, pending=(claim,))
    v[7]["value"] = replace(v[7]["value"], observed_at_ms=now, snapshot_sequence=2)
    bundle["value"] = replace(bundle["value"], observed_at_ms=now, snapshot_sequence=2,
                              orders=(RecoveryOrder(claim, "exchange-1", "live", ()),), pending_wire_ids=(claim.wire_id,))
    assert await recovery.reconcile()
    receipt = OrderReconciliation(record.session_id, record.epoch, v[6].wall, True, (), (), (), True)
    assert manager.tick(reference_ready=True, all_gates_ready=True, reconciliation=receipt,
                        market_reference_ready=True, market_anchor_usdt=D("1")) == "TRANSITIONING"
    assert manager.reason_code == "JOINT_ORDERS_UNRECONCILED"
    v[8]["snapshot"] = replace(v[8]["snapshot"], sequence=3, pending=())
    v[7]["value"] = replace(v[7]["value"], snapshot_sequence=3)
    bundle["value"] = replace(bundle["value"], snapshot_sequence=3, pending_wire_ids=(),
                              orders=(RecoveryOrder(claim, "exchange-1", "canceled", ()),))
    assert await recovery.reconcile(), recovery.reason_code
    assert manager.tick(reference_ready=True, all_gates_ready=True, reconciliation=receipt,
                        market_reference_ready=True, market_anchor_usdt=D("1")) == "ACTIVE"
    assert manager.current_session.epoch == record.epoch + 1
    assert manager.current_session.started_at == record.expires_at


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["capital", "wal", "actions"])
async def test_proven_unsent_allocation_gaps_abort_without_exchange_send(tmp_path, monkeypatch, stage):
    recovery, v, bundle, _ = setup_recovery(tmp_path)
    assert await recovery.reconcile()
    if stage == "actions":
        v[2].propose(v[11])
    else:
        target, attr = (v[5], "begin") if stage == "capital" else (v[2].journal, "commit")
        original = getattr(target, attr)

        def crash(*args, **kwargs):
            if stage == "wal" and not args[0]["actions"]:
                return original(*args, **kwargs)
            raise OSError("crash")

        with monkeypatch.context() as patch:
            patch.setattr(target, attr, crash)
            with pytest.raises(OSError):
                v[2].propose(v[11])
    claim = v[1].claims()[v[11].id]
    v[8]["snapshot"] = replace(v[8]["snapshot"], sequence=2)
    v[7]["value"] = replace(v[7]["value"], snapshot_sequence=2)
    bundle["value"] = replace(bundle["value"], snapshot_sequence=2,
                              orders=(RecoveryOrder(claim, None, "unsent", ()),))
    assert await recovery.reconcile(), recovery.reason_code
    assert v[1].claim_ids() == () and v[4].sent == []
    if stage != "capital":
        assert v[5].get(claim.intent_id).state == "ABORTED_BEFORE_SEND"


@pytest.mark.asyncio
async def test_missing_sent_order_is_never_proof_of_absence(tmp_path):
    recovery, v, bundle, _ = setup_recovery(tmp_path)
    assert await recovery.reconcile()
    claim = send_close(v)
    v[8]["snapshot"] = replace(v[8]["snapshot"], sequence=2)
    v[7]["value"] = replace(v[7]["value"], snapshot_sequence=2)
    bundle["value"] = replace(bundle["value"], snapshot_sequence=2,
                              orders=(RecoveryOrder(claim, None, "unsent", ()),))
    assert not await recovery.reconcile()
    assert v[1].claim_ids() == (claim.intent_id,)


def install_attribution(tmp_path, v):
    c = v[0]
    sid = c._order_safety_manager.current_session.session_id
    budget = LossBudgetLedger(tmp_path / "loss.json", campaign_id="life", campaign_limit_quote=D("10"),
                              day_limit_quote=D("10"), session_limit_quote=D("10"))
    budget.record("anchor", D("0"), session_id=sid, at_utc=v[6].wall)
    attribution = ReconciledFillAttributor(
        tmp_path / "attribution.json", wal=c._order_safety_wal, reservations=c._order_safety_reservations,
        loss_budget=budget, opening_life=D("10"), opening_usdt=D("10"), opening_independent_price_usdt=D("1"),
        independent_value=lambda f: IndependentFillObservation(D("1"), f.fill_at_ms, f.fill_at_ms, "independent_market"),
        max_reference_skew_ms=1000, create=True)
    c.install_execution_loss_budget(budget, utc_clock=lambda: v[6].wall)
    c.install_fill_attributor(attribution)
    return attribution, budget


@pytest.mark.asyncio
async def test_both_executor_products_settle_into_one_wallet_and_economic_ledger(tmp_path):
    recovery, v, bundle, _ = setup_recovery(tmp_path)
    attribution, budget = install_attribution(tmp_path, v)
    assert await recovery.reconcile()
    swap_claim = send_close(v)
    dispatch(v[0], v[9], v[10])
    spot_claim = v[1].claims()[v[10].id]
    spot_sent = v[3].sent[0]
    spot_sent["pre_send_check"]({"clOrdId": spot_claim.wire_id, "instId": "LIFE-USDT", "tdMode": "cash",
                                 "ordType": "post_only", "side": "buy", "px": "1", "sz": "6"})
    swap_proof = report(v, bundle, swap_claim)
    now = recovery.clock_ms()
    spot_proof = RecoveryOrder(spot_claim, "spot-exchange", "canceled", (
        RecoveryTrade("spot-trade", D("1"), D("1"), "USDT", D("-0.001"), D("0"), now),))
    v[8]["snapshot"] = replace(v[8]["snapshot"], spot_life_base=D("11"), spot_cash_quote=D("8.998"))
    bundle["value"] = replace(bundle["value"], orders=(swap_proof, spot_proof))
    assert await recovery.reconcile(), recovery.reason_code
    assert v[1].claim_ids() == () and attribution.ready()
    assert attribution.capital().usdt_balance == D("8.998")
    assert attribution.capital().life_balance == D("11")
    assert v[0]._order_safety_reservations.cashflow_events == {}  # Fees/PnL are not transfers.
    assert recovery.measure().nav_quote == D("19.998")
    assert recovery.measure().drawdown_bps == D("1")
    status = budget.verified_status(session_id=swap_claim.session_id, at_utc=v[6].wall)
    assert status.campaign_loss_quote == D("0.002")
    assert await recovery.reconcile()
    assert budget.verified_status(session_id=swap_claim.session_id, at_utc=v[6].wall) == status


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", [False, True])
async def test_real_runner_swap_fill_and_cancel_hints_need_complete_exchange_history(tmp_path, conflict):
    recovery, v, bundle, _ = setup_recovery(tmp_path)
    assert await recovery.reconcile()
    claim = send_close(v)
    event = OrderFilledEvent(v[6].wall.timestamp(), claim.wire_id, "LIFE-USDT", TradeType.SELL,
                             OrderType.LIMIT_MAKER, D("1"), D("0.25"),
                             AddedToCostTradeFee(flat_fees=[TokenAmount("USDT", D("0.001"))]),
                             exchange_trade_id="trade-1", exchange_order_id="exchange-1")
    v[0].on_runner_order_filled(event)
    v[0].on_runner_order_canceled(OrderCancelledEvent(v[6].wall.timestamp(), claim.wire_id, "exchange-1"))
    assert not recovery.allocation_ready() and not v[0]._runner_scope_invalid
    proof = report(v, bundle, claim)
    if conflict:
        bundle["value"] = replace(bundle["value"], orders=(replace(proof, trades=(replace(proof.trades[0], trade_id="other"),)),))
    assert (await recovery.reconcile()) is (not conflict), recovery.reason_code
    assert (v[1].claim_ids() == ()) is (not conflict)


@pytest.mark.asyncio
async def test_joint_funding_cash_nav_and_carry_budget_are_not_counted_twice(tmp_path):
    from test.hummingbot.strategy_v2.life_liquidity.test_carry_monitor import refresh, setup_carry

    from hummingbot.strategy_v2.life_liquidity.carry_monitor import FundingPayment
    recovery, v, bundle, _ = setup_recovery(tmp_path)
    attribution, _ = install_attribution(tmp_path, v)
    monitor, _, feeds = setup_carry(tmp_path, values=v)
    assert await recovery.reconcile(), recovery.reason_code
    now = recovery.clock_ms()
    payments = (FundingPayment("101", now, D("-0.1")), FundingPayment("102", now, D("0.04")))
    refresh(v, feeds, payments=payments, spot_cash_quote=D("9.94"), collateral_quote=D("99.94"))
    bundle["value"] = replace(bundle["value"], snapshot_sequence=2, payments=tuple(
        RecoveryPayment(p.bill_id, p.signed_payment_quote, p.settled_at_ms) for p in payments))
    assert await recovery.reconcile(), recovery.reason_code
    assert v[0]._order_safety_reservations.usdt_balance == D("9.94") and attribution.ready()
    assert recovery.measure().nav_quote == D("19.94")
    decision = monitor.check()
    assert decision.funding_debits_quote == D("0.1")
    assert decision.settled_payment_quote == D("-0.06")
    assert v[8]["snapshot"].collateral_quote == D("99.94")  # No second debit to collateral.
    assert await recovery.reconcile()
    assert recovery.measure().adjusted_nav_quote == D("19.94")


@pytest.mark.asyncio
async def test_realized_close_pnl_and_remaining_unrealized_pnl_are_distinct(tmp_path):
    recovery, v, bundle, _ = setup_recovery(tmp_path)
    _, loss = install_attribution(tmp_path, v)
    assert await recovery.reconcile()
    # Favorable execution: maker SELL close above its original limit.
    claim = send_close(v)
    proof = report(v, bundle, claim)
    trade = replace(proof.trades[0], price_usdt=D("1.1"), realized_pnl_quote=D("0.025"))
    bundle["value"] = replace(bundle["value"], orders=(replace(proof, trades=(trade,)),))
    v[8]["snapshot"] = replace(v[8]["snapshot"], spot_cash_quote=D("10.024"), mark_price_usdt=D("1.1"))
    assert await recovery.reconcile(), recovery.reason_code
    assert recovery.measure().nav_quote == D("20.099")  # Cash10.024 + LIFE10 + remaining UPL.075.
    assert loss.verified_status(session_id=claim.session_id, at_utc=v[6].wall).campaign_loss_quote == D("0.001")


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["stale", "clock", "mutation", "missing", "policy", "trades", "pnl"])
async def test_persisted_scope_and_fill_history_cannot_be_replayed_or_changed(tmp_path, fault):
    recovery, v, bundle, _ = setup_recovery(tmp_path)
    assert await recovery.reconcile()
    claim = send_close(v)
    proof = report(v, bundle, claim)
    assert await recovery.reconcile()
    if fault == "stale":
        v[6].advance(2)
    elif fault == "clock":
        v[6].advance(-1)
    elif fault == "missing":
        recovery.journal.path.unlink()
    elif fault == "policy":
        recovery._binding["max_age_ms"] = 999
    else:
        orders = (replace(proof, trades=()),) if fault == "trades" else (replace(
            proof, trades=(replace(proof.trades[0], realized_pnl_quote=D("1")),)),)
        if fault == "mutation":
            bundle["value"] = replace(bundle["value"], runner_scope_complete=False)
        else:
            v[8]["snapshot"] = replace(v[8]["snapshot"], sequence=3)
            v[7]["value"] = replace(v[7]["value"], snapshot_sequence=3)
            bundle["value"] = replace(bundle["value"], snapshot_sequence=3, orders=orders)
    assert not await recovery.reconcile()
    assert not recovery.allocation_ready() and recovery.measure() is None


def test_capital_release_requires_the_bound_durable_joint_authority(tmp_path):
    recovery, v, _, _ = setup_recovery(tmp_path)
    claim = CapitalClaim("bad", "bad", "12345", "s", 1, 1, 1, "SWAP", "SELL", "OPEN", D("1"), D("1"), 1)
    with pytest.raises(ValueError, match="UNPROVEN"):
        v[1].settle(claim, "0" * 64, authority=recovery)
    assert not recovery.authorizes_order(RecoveryOrder(claim, None, "unsent", ()), "0" * 64)


@pytest.mark.asyncio
@pytest.mark.parametrize("breach", [False, True])
async def test_cold_hedge_recovery_preserves_targets_retries_costs_and_refuses_cost_breach(tmp_path, breach):
    from test.hummingbot.strategy_v2.life_liquidity.test_hedge_coordinator import setup_coordinator
    h, v, market, _ = setup_coordinator(tmp_path, budget_capacity=50)
    recovery, v, bundle, _ = setup_recovery(tmp_path, values=v)
    assert await recovery.reconcile(), recovery.reason_code
    action = h.propose()[0]
    cfg = action.executor_config
    dispatch(v[0], v[9], cfg, action)
    claim = v[1].claims()[cfg.id]
    wire = wire_for(cfg, claim.wire_id, PositionMode.ONEWAY)
    wire.update(px=str(cfg.price), sz=str(cfg.amount / D("0.25")))
    v[4].sent[-1]["pre_send_check"](wire)
    assert v[5].get(cfg.id).state == "SEND_UNKNOWN"
    fee = D("-1") if breach else D("-0.0001")
    now = recovery.clock_ms()
    trade = RecoveryTrade("hedge-trade", D("0.5"), cfg.price, "USDT", fee, D("0.0005"), now)
    v[8]["snapshot"] = replace(v[8]["snapshot"], sequence=2, long_base=D("0.5"),
                               spot_cash_quote=D("10.0005") + fee)
    v[7]["value"] = replace(v[7]["value"], snapshot_sequence=2, net_contracts=D("2"))
    bundle["value"] = replace(bundle["value"], snapshot_sequence=2,
                              orders=(RecoveryOrder(claim, "hedge-exchange", "canceled", (trade,)),))
    old = h.journal._read()
    joint, fresh = cold_restore(recovery, v)
    assert await joint.reconcile(), joint.reason_code
    recovered = fresh[0]._hedge_coordinator
    saved = recovered.journal._read()
    assert saved["target"] == old["target"] and saved["target_version"] == old["target_version"]
    assert saved["attempts"] == old["attempts"] and saved["cost_hold"] == old["cost_hold"]
    assert saved["orders"][cfg.id]["status"] == "TERMINAL"
    assert fresh[1].claim_ids() == () and len(v[4].sent) == 1
    assert recovered.propose() == []
    if breach:
        assert saved["fault"] == "HEDGE_REALIZED_COST_BREACH"
        with pytest.raises(ValueError, match="HEDGE_JOINT_REARM_REQUIRED"):
            joint.manual_rearm(operator_id="operator")
        assert not joint.allocation_ready()
    else:
        joint.manual_rearm(operator_id="operator")
        actions = recovered.propose()
        assert len(actions) == 1 and actions[0].executor_config.id != cfg.id
        assert actions[0].executor_config.amount == D("0.5")  # Only the actual remaining long.


@pytest.mark.asyncio
async def test_hedge_preparation_crash_before_capital_is_terminal_without_refunding_attempt(tmp_path, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.test_hedge_coordinator import setup_coordinator
    h, v, _, _ = setup_coordinator(tmp_path, budget_capacity=50)
    recovery, v, bundle, _ = setup_recovery(tmp_path, values=v)
    assert await recovery.reconcile()
    with monkeypatch.context() as patch:
        patch.setattr(v[2], "propose", lambda _: (_ for _ in ()).throw(OSError("before allocation")))
        assert h.propose() == []
    before = h.journal._read()
    assert before["attempts"] == 1 and next(iter(before["orders"].values()))["status"] == "PREPARING"
    assert await recovery.reconcile(), recovery.reason_code
    after = h.journal._read()
    assert after["attempts"] == 1 and after["cost_hold"] == before["cost_hold"]
    assert next(iter(after["orders"].values()))["status"] == "TERMINAL"
    assert v[4].sent == [] and v[1].claim_ids() == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,side,action", [(PositionMode.HEDGE, TradeType.BUY, PositionAction.CLOSE),
                                              (PositionMode.HEDGE, TradeType.SELL, PositionAction.OPEN),
                                              (PositionMode.ONEWAY, TradeType.BUY, PositionAction.OPEN)])
async def test_product_side_and_position_mode_replay_preserves_gross_legs(tmp_path, mode, side, action):
    values = setup_shared(tmp_path, collateral="100", budget_capacity=50, mode=mode)
    recovery, v, bundle, _ = setup_recovery(tmp_path, values=values)
    assert await recovery.reconcile()
    cfg = v[11].model_copy(update={"amount": D("0.5"), "side": side, "position_action": action})
    dispatch(v[0], v[9], cfg, v[2].propose(cfg))
    claim = v[1].claims()[cfg.id]
    wire = wire_for(cfg, claim.wire_id, mode)
    v[4].sent[-1]["pre_send_check"](wire)
    long = D("1.5") if mode == PositionMode.ONEWAY else D("1")
    short = D("0") if mode == PositionMode.ONEWAY else D("0.5") if action == PositionAction.CLOSE else D("1.5")
    v[8]["snapshot"] = replace(v[8]["snapshot"], sequence=2, long_base=long, short_base=short, spot_cash_quote=D("9.999"))
    v[7]["value"] = replace(v[7]["value"], snapshot_sequence=2,
                            net_contracts=long / D("0.25") if mode == PositionMode.ONEWAY else D("0"),
                            long_contracts=long / D("0.25") if mode == PositionMode.HEDGE else D("0"),
                            short_contracts=short / D("0.25") if mode == PositionMode.HEDGE else D("0"))
    t = RecoveryTrade("mode-trade", D("0.5"), D("1"), "USDT", D("-0.001"), D("0"), recovery.clock_ms())
    bundle["value"] = replace(bundle["value"], snapshot_sequence=2,
                              orders=(RecoveryOrder(claim, "mode-exchange", "filled", (t,)),))
    assert await recovery.reconcile(), recovery.reason_code
    assert v[1].claim_ids() == () and v[5].get(cfg.id).state == "TERMINAL"
    assert (v[8]["snapshot"].long_base, v[8]["snapshot"].short_base) == (long, short)


@pytest.mark.asyncio
async def test_cancel_transport_failure_fences_allocation_and_attempt_survives_restart(tmp_path):
    recovery, v, _, _ = setup_recovery(tmp_path)
    assert await recovery.reconcile()
    claim = send_close(v)

    async def disconnected(_):
        raise ConnectionError("lost cancel ACK")

    recovery.cancel_swap = disconnected
    with pytest.raises(ConnectionError):
        await recovery.cancel_working_swaps()
    assert not recovery.allocation_ready()
    fresh, restored = cold_restore(recovery, v)
    assert restored[5].get(claim.intent_id).cancel_attempts == 1
    await fresh.cancel_working_swaps()  # Retry interval has not elapsed.
    assert restored[5].get(claim.intent_id).cancel_attempts == 1
    assert restored[1].claim_ids() == (claim.intent_id,)


@pytest.mark.asyncio
async def test_reference_epoch_transition_and_runtime_halt_cannot_bypass_joint_recovery(tmp_path):
    recovery, v, _, _ = setup_recovery(tmp_path)
    assert await recovery.reconcile()
    claim = send_close(v)
    manager = v[0]._order_safety_manager
    manager.pause("test")
    with pytest.raises(ValueError, match="JOINT_ORDERS_UNRECONCILED"):
        manager.transition_reference(anchors={"LIFE-USDT": D("1")}, model_version="new", config_version=2,
                                     old_orders_reconciled=True)
    with pytest.raises(ValueError, match="JOINT_REARM_PROOF_REQUIRED"):
        recovery.manual_rearm(operator_id="operator")
    assert not recovery.allocation_ready()
    assert v[1].claim_ids() == (claim.intent_id,)


@pytest.mark.asyncio
async def test_canceling_inflight_collection_never_grants_permission(tmp_path):
    recovery, v, _, _ = setup_recovery(tmp_path)
    assert await recovery.reconcile()
    entered = asyncio.Event()

    async def pending():
        entered.set()
        await asyncio.Event().wait()

    recovery.collect = pending
    task = asyncio.create_task(recovery.reconcile())
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not recovery.allocation_ready()
    assert recovery.reason_code == "JOINT_RECONCILIATION_INTERRUPTED"


@pytest.mark.asyncio
async def test_joint_polling_is_due_on_explicit_interval_and_survives_quote_expiry(tmp_path):
    recovery, v, bundle, _ = setup_recovery(tmp_path)
    assert recovery.reconciliation_due()
    assert await recovery.reconcile()
    assert not recovery.reconciliation_due()
    v[6].advance(11)
    now = recovery.clock_ms()
    v[8]["snapshot"] = replace(v[8]["snapshot"], sequence=2, observed_at_ms=now)
    v[7]["value"] = replace(v[7]["value"], snapshot_sequence=2, observed_at_ms=now)
    bundle["value"] = replace(bundle["value"], snapshot_sequence=2, observed_at_ms=now)
    assert recovery.reconciliation_due()
    v[0].on_safety_tick(v[6].mono)
    await asyncio.wait_for(v[0]._joint_reconciliation_task, 2)
    assert v[0]._order_safety_manager.state == "EXPIRED"
    assert recovery.journal._read()["sequence"] == 2
    task = v[0].order_safety_task
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_verified_sale_proceeds_before_funding_debit_apply_as_one_wallet_checkpoint(tmp_path):
    from test.hummingbot.strategy_v2.life_liquidity.test_carry_monitor import refresh, setup_carry

    from hummingbot.strategy_v2.life_liquidity.carry_monitor import FundingPayment
    recovery, v, bundle, _ = setup_recovery(tmp_path)
    attribution, _ = install_attribution(tmp_path, v)
    monitor, _, feeds = setup_carry(tmp_path, values=v)
    assert await recovery.reconcile()
    cfg = v[10].model_copy(update={"side": TradeType.SELL})
    dispatch(v[0], v[9], cfg)
    claim = v[1].claims()[cfg.id]
    v[3].sent[-1]["pre_send_check"]({"clOrdId": claim.wire_id, "instId": "LIFE-USDT", "tdMode": "cash",
                                    "ordType": "post_only", "side": "sell", "px": "1", "sz": "6"})
    trade_at = recovery.clock_ms()
    v[6].advance(0.001)
    now = recovery.clock_ms()
    payment = FundingPayment("103", now, D("-11"))
    refresh(v, feeds, payments=(payment,), spot_cash_quote=D("5"), spot_life_base=D("4"), collateral_quote=D("89"))
    bundle["value"] = replace(bundle["value"], snapshot_sequence=2, observed_at_ms=now,
                              orders=(RecoveryOrder(claim, "sale-exchange", "filled", (
                                  RecoveryTrade("sale", D("6"), D("1"), "USDT", D("0"), D("0"), trade_at),)),),
                              payments=(RecoveryPayment("103", D("-11"), now),))
    assert await recovery.reconcile(), recovery.reason_code
    assert attribution.ready() and attribution.capital().usdt_balance == D("5")
    assert v[1].claim_ids() == ()
    assert not monitor.check().allowed  # True settlement can coexist with an exhausted funding budget.
    assert await recovery.reconcile()
    assert v[0]._order_safety_reservations.usdt_balance == D("5")


@pytest.mark.asyncio
async def test_failed_freshness_check_persists_clock_advance_and_cannot_be_rolled_back(tmp_path):
    recovery, v, _, _ = setup_recovery(tmp_path)
    assert await recovery.reconcile()
    v[6].advance(2)
    assert not recovery.allocation_ready()
    v[6].advance(-2)
    assert not recovery.allocation_ready()
    assert not await recovery.reconcile()
    assert recovery.reason_code == "JOINT_CLOCK_ROLLBACK"


@pytest.mark.asyncio
async def test_runner_scope_covers_swap_children_and_cold_recorder_gap(tmp_path):
    from hummingbot.strategy_v2.models.base import RunnableStatus
    recovery, v, bundle, _ = setup_recovery(tmp_path)
    assert await recovery.reconcile()
    claim = send_close(v)
    c = v[0]
    c._runner_halt_ok = True
    c._runner_executors = lambda: ()
    c._runner_orchestrator = SimpleNamespace(get_stored_executors_by_controller=lambda _: [])
    assert recovery.runner_provenance_complete(v[5].all_records())
    assert c._runner_executor_scope_complete()
    cfg = v[11].model_copy(update={"amount": D("0.5"), "position_action": PositionAction.CLOSE})
    assert c._runner_product_config_valid(cfg)
    assert not c._runner_product_config_valid(cfg.model_copy(update={"amount": D("1")}))
    report(v, bundle, claim)
    assert await recovery.reconcile(), recovery.reason_code
    # A foreign live child cannot be hidden behind the provider's scope flag.
    c._runner_executors = lambda: (SimpleNamespace(status=RunnableStatus.TERMINATED),)
    assert not c._runner_executor_scope_complete()
    assert not await recovery.reconcile()


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_first", ["joint_only", "joint", "orders"])
async def test_stop_keeps_account_ownership_until_both_reconciliation_tasks_quiesce(tmp_path, finish_first):
    recovery, v, _, _ = setup_recovery(tmp_path)
    c = v[0]
    entered, joint_cleanup, orders_cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def collect():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            await joint_cleanup.wait()

    recovery.collect = collect
    c._joint_reconciliation_task = asyncio.create_task(recovery.reconcile())
    await asyncio.wait_for(entered.wait(), 2)
    ownership = MagicMock()
    c._order_safety_account_lock = ownership
    if finish_first != "joint_only":
        orders_entered = asyncio.Event()

        async def orders():
            orders_entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await orders_cleanup.wait()

        c.order_safety_task = asyncio.create_task(orders())
        c.order_safety_task.add_done_callback(c._on_order_safety_done)
        await asyncio.wait_for(orders_entered.wait(), 2)
    tasks = [t for t in (c._joint_reconciliation_task, c.order_safety_task) if t is not None]
    try:
        c.stop()
        ownership.release.assert_not_called()
        first = c.order_safety_task if finish_first == "orders" else c._joint_reconciliation_task
        (orders_cleanup if finish_first == "orders" else joint_cleanup).set()
        await asyncio.wait_for(asyncio.gather(first, return_exceptions=True), 2)
        if finish_first != "joint_only":
            ownership.release.assert_not_called()
        joint_cleanup.set()
        orders_cleanup.set()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 2)
        ownership.release.assert_called_once()
        assert c._order_safety_account_lock is None
        assert not recovery.allocation_ready()
    finally:
        joint_cleanup.set()
        orders_cleanup.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
