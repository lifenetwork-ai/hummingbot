"""O.7.3 coordinator contracts, with explicitly synthetic account/depth feeds."""

from dataclasses import replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital_runner import dispatch, setup_shared

import pytest

from hummingbot.core.data_type.common import PositionAction, PositionMode, TradeType
from hummingbot.strategy_v2.life_liquidity.hedge import HedgePolicy
from hummingbot.strategy_v2.life_liquidity.hedge_coordinator import (
    HedgeCoordinator,
    HedgeExecutionPolicy,
    HedgeMarket,
    HedgeSettlement,
    HedgeTrade,
)

D = Decimal


def setup_coordinator(tmp_path, *, route=None, mode=PositionMode.ONEWAY, budget_capacity=5, **changes):
    values = setup_shared(tmp_path, collateral="1000", route=route, mode=mode, budget_capacity=budget_capacity)
    c, cap, sender, spot, swap, wal, clock, account, capital, strategy, sc, pc = values
    c._order_safety_wal.initialize_empty()
    hp = HedgePolicy("LIFE-USDT-SWAP", D("0.1"), D("0.25"), D("2"), D("3"), 1000, 3,
                     D("1"), D("100"), D("0.001"))
    policy = HedgeExecutionPolicy(hp, D("0"), D("5"), 500, "bounded_maker_and_pause")
    policy = replace(policy, **changes)
    sender.request_budget.clock = lambda: clock.wall
    market = {"value": HedgeMarket("12345", "LIFE-USDT-SWAP", 1, int(clock.wall.timestamp() * 1000),
                                   ((D("0.999"), D("100")),), ((D("1.001"), D("100")),),
                                   D("0.001"), D("0"), D("10"), True, "okx_depth_fee")}
    settlements = {}
    coordinator = HedgeCoordinator(c, tmp_path / "hedge_coordinator.json", policy=policy,
                                   market=lambda: market["value"], settlement=lambda key: settlements.get(key), create=True)
    c.install_hedge_coordinator(coordinator)
    return coordinator, values, market, settlements


def test_actual_runner_issues_one_bounded_hedge_from_actual_holdings(tmp_path):
    h, v, market, _ = setup_coordinator(tmp_path)
    c, cap, sender, spot, swap, wal, clock, account, capital, strategy, sc, pc = v
    actions = c.determine_executor_actions()
    assert len(actions) == 1
    cfg = actions[0].executor_config
    assert cfg.side == TradeType.SELL and cfg.position_action == PositionAction.CLOSE
    assert cfg.amount == D("1")  # Close the actual long before opening a short.
    assert cfg.price == D("1.001")
    assert c.determine_executor_actions() == []  # No second hedge on another tick.
    dispatch(c, strategy, cfg, actions[0])
    assert len(swap.sent) == 1 and spot.sent == []
    assert len(cap.claim_ids()) == 1


def test_target_updates_are_exactly_once_and_persisted(tmp_path):
    h, v, market, settlements = setup_coordinator(tmp_path)
    assert h.update_target("decision-1", expected_version=0, target_base=D("11")) is True
    assert h.update_target("decision-1", expected_version=0, target_base=D("11")) is False
    with pytest.raises(ValueError):
        h.update_target("decision-1", expected_version=0, target_base=D("10"))
    with pytest.raises(ValueError):
        h.update_target("decision-2", expected_version=0, target_base=D("10"))
    assert h.propose() == []  # Actual spot 10 + actual long 1 is balanced.
    restored = HedgeCoordinator(v[0], h.journal.path, policy=h.policy, market=lambda: market["value"],
                                settlement=lambda key: settlements.get(key), create=False)
    assert restored.update_target("decision-1", expected_version=0, target_base=D("11")) is False


def send_child(h, values):
    from test.hummingbot.strategy_v2.life_liquidity.test_swap_executor import wire_for

    from hummingbot.core.data_type.common import PositionMode
    c, cap, sender, spot, swap, wal, clock, account, capital, strategy, sc, pc = values
    action = h.propose()[0]
    cfg = action.executor_config
    dispatch(c, strategy, cfg, action)
    pending = swap.sent[-1]
    wire = wire_for(cfg, pending["order_id"], PositionMode.ONEWAY)
    wire.update(px=str(cfg.price), sz=str(cfg.amount / D("0.25")))
    pending["pre_send_check"](wire)
    pending["on_ack"]("exchange-" + cfg.id)
    return cfg, pending


def set_positions(values, *, long="0", short="0", spot=None):
    c, cap, sender, route, swap, wal, clock, account, capital, strategy, sc, pc = values
    obs = capital["snapshot"]
    updates = {"sequence": obs.sequence + 1, "long_base": D(long), "short_base": D(short),
               "observed_at_ms": int(clock.wall.timestamp() * 1000)}
    if spot is not None:
        updates["spot_life_base"] = D(spot)
    capital["snapshot"] = replace(obs, **updates)
    account["value"] = replace(account["value"], net_contracts=(D(long) - D(short)) / D("0.25"),
                               snapshot_sequence=obs.sequence + 1, observed_at_ms=updates["observed_at_ms"])


def proof_for(values, cfg, *, quantity="0", terminal=True, trade_id="trade-1"):
    obs = values[8]["snapshot"]
    wal = values[5].get(cfg.id)
    amount = D(quantity)
    trades = () if amount == 0 else (HedgeTrade(trade_id, amount, cfg.price, D("0.0001")),)
    return HedgeSettlement("12345", cfg.id, wal.client_order_id, wal.exchange_order_id,
                           obs.sequence, amount, trades, terminal, True, "okx_reconciled")


def test_partial_open_requires_complete_terminal_proof_before_bounded_residual_retry(tmp_path):
    h, v, market, settlements = setup_coordinator(tmp_path)
    set_positions(v)  # No long to close: hedge actual spot 10 with a short.
    cfg, pending = send_child(h, v)
    assert cfg.amount == D("2") and cfg.position_action == PositionAction.OPEN
    set_positions(v, short="0.5")
    settlements[cfg.id] = proof_for(v, cfg, quantity="0.5", terminal=False)
    assert h.reconcile(cfg.id)
    assert h.propose() == [] and not h.allows_spot()
    v[5].mark_terminal(cfg.id, "exchange-" + cfg.id)
    settlements[cfg.id] = replace(settlements[cfg.id], terminal=True)
    assert h.reconcile(cfg.id)
    assert h.reconcile(cfg.id)  # Idempotent duplicate cannot apply a second fill.
    child = h.propose()[0].executor_config
    assert child.amount == D("2")  # Bounded actual residual, never the original order's missing ACK.
    assert len(v[1].claim_ids()) == 2  # No early capital refund on terminal evidence.


def test_cancelled_zero_fill_attempts_exhaust_retry_cap_without_automatic_retry(tmp_path):
    h, v, market, settlements = setup_coordinator(tmp_path)
    set_positions(v)
    for attempt in range(3):
        if attempt:
            v[6].advance(1)
            set_positions(v)
            market["value"] = replace(market["value"], sequence=attempt + 1,
                                      observed_at_ms=int(v[6].wall.timestamp() * 1000))
        cfg, pending = send_child(h, v)
        assert h.propose() == []  # ACK and timeout do not permit replacement.
        v[5].mark_terminal(cfg.id, "exchange-" + cfg.id)
        settlements[cfg.id] = proof_for(v, cfg)
        assert h.reconcile(cfg.id)
    assert h.propose() == [] and h.reason_code == "HEDGE_RETRY_CAP"
    assert not h.allows_spot() and len(v[1].claim_ids()) == 3


@pytest.mark.parametrize("fault", ["uid", "future", "stale", "source", "scope", "crossed", "depth",
                                   "fee", "edge", "funding", "sequence", "nan", "position", "disk"])
def test_changed_evidence_revokes_dispatched_hedge_and_retains_every_claim(tmp_path, fault):
    from test.hummingbot.strategy_v2.life_liquidity.test_swap_executor import wire_for

    from hummingbot.core.data_type.common import PositionMode
    h, v, market, settlements = setup_coordinator(tmp_path)
    cfg = h.propose()[0].executor_config
    dispatch(v[0], v[9], cfg)
    pending = v[4].sent[-1]
    m = market["value"]
    changes = {
        "uid": {"account_uid": "999"}, "future": {"observed_at_ms": m.observed_at_ms + 1},
        "stale": {"observed_at_ms": m.observed_at_ms - 501}, "source": {"source": "benchmark"},
        "scope": {"scope_complete": False}, "crossed": {"bids": ((D("2"), D("1")),)},
        "depth": {"bids": ((D("0.999"), D("0.1")),)}, "fee": {"fee_rate": D("0.1")},
        "edge": {"available_edge_quote": D("0")}, "funding": {"funding_cost_quote": D("2")},
        "sequence": {"sequence": 0}, "nan": {"fee_rate": D("NaN")},
    }
    if fault == "disk":
        h.journal.path.unlink()
    elif fault == "position":
        set_positions(v, long="0", short="12")  # Hedge direction would increase exposure.
    else:
        market["value"] = replace(m, **({"sequence": 2} | changes[fault]))
    wire = wire_for(cfg, pending["order_id"], PositionMode.ONEWAY)
    wire.update(px=str(cfg.price), sz=str(cfg.amount / D("0.25")))
    with pytest.raises(PermissionError):
        pending["pre_send_check"](wire)
    assert v[5].get(cfg.id).state == "SEND_UNKNOWN"
    assert v[1].retained_claim_ids() == (cfg.id,)


@pytest.mark.parametrize("fault", ["uid", "wire", "sequence", "scope", "source", "sum", "duplicate",
                                   "price", "fee", "position", "unknown", "oversize"])
def test_invalid_settlement_cannot_enable_replacement_or_release_capital(tmp_path, fault):
    h, v, market, settlements = setup_coordinator(tmp_path)
    cfg, _ = send_child(h, v)
    set_positions(v, long="0.5")
    v[5].mark_terminal(cfg.id, "exchange-" + cfg.id)
    proof = proof_for(v, cfg, quantity="0.5")
    changes = {"uid": {"account_uid": "999"}, "wire": {"wire_id": "bad"}, "sequence": {"snapshot_sequence": 1},
               "scope": {"scope_complete": False}, "source": {"source": "runner_callback"},
               "sum": {"cumulative_base": D("0.6")}, "duplicate": {"trades": proof.trades * 2},
               "price": {"trades": (replace(proof.trades[0], price_usdt=D("0.01")),)},
               "fee": {"trades": (replace(proof.trades[0], fee_quote=D("-1")),)},
               "unknown": {"exchange_order_id": "missing"}, "oversize": {"cumulative_base": D("2")}}
    if fault == "position":
        set_positions(v, long="0.4")
        proof = replace(proof, snapshot_sequence=v[8]["snapshot"].sequence)
    else:
        proof = replace(proof, **changes[fault])
    settlements[cfg.id] = proof
    assert not h.reconcile(cfg.id)
    assert h.propose() == [] and not h.allows_spot()
    assert v[1].claim_ids() == (cfg.id,)


def test_urgent_deadline_overrides_deadband_without_relaxing_depth_or_cost(tmp_path):
    hp = HedgePolicy("LIFE-USDT-SWAP", D("0.5"), D("0.5"), D("2"), D("3"), 1000, 3,
                     D("1"), D("100"), D("0.001"))
    h, v, market, settlements = setup_coordinator(tmp_path, hedge=hp)
    h.update_target("small-residual", expected_version=0, target_base=D("10.75"))
    assert h.propose() == []
    v[6].advance(1)
    set_positions(v, long="1")
    market["value"] = replace(market["value"], sequence=2, observed_at_ms=int(v[6].wall.timestamp() * 1000))
    actions = h.propose()
    assert len(actions) == 1 and actions[0].executor_config.amount == D("0.25")
    assert h.reason_code == "HEDGE_URGENT_BOUNDED_MAKER" and not h.allows_spot()


def test_balanced_position_never_creates_zero_size_urgent_order(tmp_path):
    h, v, market, _ = setup_coordinator(tmp_path)
    h.update_target("balanced", expected_version=0, target_base=D("11"))
    assert h.propose() == [] and h.allows_spot()


def test_shared_campaign_cost_budget_blocks_successive_hedges(tmp_path):
    h, v, market, settlements = setup_coordinator(tmp_path, cumulative_cost_budget_quote=D("0.001"))
    assert h.propose() == [] and h.reason_code == "HEDGE_CAMPAIGN_COST_LIMIT"
    assert v[1].claim_ids() == ()


def test_external_swap_action_cannot_bypass_installed_coordinator(tmp_path):
    h, v, _, _ = setup_coordinator(tmp_path)
    with pytest.raises(PermissionError):
        v[2].propose(v[11])
    assert v[1].claim_ids() == ()


def test_restore_and_stale_coordinator_do_not_restore_send_authority(tmp_path):
    h, v, market, settlements = setup_coordinator(tmp_path)
    stale = HedgeCoordinator(v[0], h.journal.path, policy=h.policy, market=lambda: market["value"],
                             settlement=lambda key: settlements.get(key), create=False)
    cfg = h.propose()[0].executor_config
    assert all(hasattr(a, "executor_id") for a in stale.propose()) and not stale.authorizes(cfg, issued=True)
    restored = HedgeCoordinator(v[0], h.journal.path, policy=h.policy, market=lambda: market["value"],
                                settlement=lambda key: settlements.get(key), create=False)
    assert all(hasattr(a, "executor_id") for a in restored.propose()) and not restored.authorizes(cfg, issued=True)
    with pytest.raises(ValueError):
        h.update_target("change-during-order", expected_version=0, target_base=D("10"))


def test_pending_spot_quote_is_cancelled_before_hedge_and_queued_spot_is_revoked(tmp_path):
    h, v, _, _ = setup_coordinator(tmp_path)
    c, cap, sender, spot, swap, wal, clock, account, capital, strategy, sc, pc = v
    # The real spot gate denies it once the coordinator requires a hedge.
    with pytest.raises(PermissionError):
        dispatch(c, strategy, sc)
    assert spot.sent == []
    # Pending old quote cancellation is coordinated without submitting a hedge.
    c._order_safety_wal.begin("old-quote", client_order_id="old-wire", session_id="old", epoch=1,
                              reservation_id="old-quote", slot_market="LIFE-USDT", slot_side="BUY", slot_level=1)
    stops = c.determine_executor_actions()
    assert len(stops) == 1 and stops[0].executor_id == "old-quote"
    assert not swap.sent


def test_spot_partial_fill_changes_actual_hedge_size_and_revokes_queued_quote(tmp_path):
    h, v, market, _ = setup_coordinator(tmp_path)
    c, cap, sender, spot, swap, wal, clock, account, capital, strategy, sc, pc = v
    h.update_target("initial-target", expected_version=0, target_base=D("11"))
    dispatch(c, strategy, sc)
    queued = spot.sent[0]
    ledger = c._order_safety_reservations
    assert ledger.record_fill(sc.id, "reconciled-spot-fill", D("1"), D("1"))
    capital["snapshot"] = replace(capital["snapshot"], sequence=2, spot_life_base=D("11"), spot_cash_quote=D("9"))
    account["value"] = replace(account["value"], snapshot_sequence=2)
    with pytest.raises(PermissionError):
        queued["pre_send_check"]({"clOrdId": queued["order_id"], "instId": "LIFE-USDT", "side": "buy",
                                  "ordType": "post_only", "tdMode": "cash", "px": "1", "sz": "6"})
    actions = c.determine_executor_actions()
    assert len(actions) == 1 and actions[0].executor_id == sc.id
    c._order_safety_wal.mark_terminal(sc.id, "spot-exchange")
    ledger.confirm_terminal(sc.id, cumulative_filled=D("1"), fills_reconciled=True, exchange_state="CANCELED")
    actions = c.determine_executor_actions()
    assert len(actions) == 1 and actions[0].executor_config.amount == D("1")
    assert h.update_target("initial-target", expected_version=0, target_base=D("11")) is False


def test_full_close_is_reconciled_from_actual_positions_without_refunding_capital(tmp_path):
    h, v, market, settlements = setup_coordinator(tmp_path)
    cfg, _ = send_child(h, v)
    set_positions(v)
    v[5].mark_terminal(cfg.id, "exchange-" + cfg.id)
    settlements[cfg.id] = proof_for(v, cfg, quantity="1")
    assert h.reconcile(cfg.id)
    assert v[1].claim_ids() == (cfg.id,)
    # The old full close hold cannot finance a successor; O.7.5 must settle it.
    assert h.propose() == [] and not h.allows_spot()


def test_tick_polls_terminal_proof_and_retry_is_for_current_actual_residual(tmp_path):
    h, v, market, settlements = setup_coordinator(tmp_path)
    set_positions(v)
    cfg, _ = send_child(h, v)
    set_positions(v, short="0.5")
    v[5].mark_terminal(cfg.id, "exchange-" + cfg.id)
    settlements[cfg.id] = proof_for(v, cfg, quantity="0.5")
    actions = v[0].determine_executor_actions()
    assert len(actions) == 1 and actions[0].executor_config.id != cfg.id


def test_reconciliation_conflict_latches_across_restart_and_cancels_working_hedge(tmp_path):
    h, v, market, settlements = setup_coordinator(tmp_path)
    cfg, _ = send_child(h, v)
    settlements[cfg.id] = proof_for(v, cfg, terminal=False)
    assert h.reconcile(cfg.id)
    settlements[cfg.id] = replace(settlements[cfg.id], account_uid="999")
    assert not h.reconcile(cfg.id)
    assert not h.authorizes(cfg, issued=True) and not h.allows_spot()
    stops = h.propose()
    assert len(stops) == 1 and stops[0].executor_id == cfg.id
    restored = HedgeCoordinator(v[0], h.journal.path, policy=h.policy, market=lambda: market["value"],
                                settlement=lambda key: settlements.get(key), create=False)
    assert not restored.allows_spot()


def test_deteriorating_cost_cancels_working_hedge_without_release_or_replacement(tmp_path):
    h, v, market, settlements = setup_coordinator(tmp_path)
    cfg, _ = send_child(h, v)
    market["value"] = replace(market["value"], sequence=2, funding_cost_quote=D("10"))
    stops = h.propose()
    assert len(stops) == 1 and stops[0].executor_id == cfg.id
    assert v[1].claim_ids() == (cfg.id,)


def test_two_instances_cannot_allocate_conflicting_hedges(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    h, v, market, settlements = setup_coordinator(tmp_path)
    second = HedgeCoordinator(v[0], h.journal.path, policy=h.policy, market=lambda: market["value"],
                              settlement=lambda key: settlements.get(key), create=False)
    # Test competing same-host active owners; normal restore never enables this.
    second._runtime_enabled = True
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda c: c.propose(), (h, second)))
    creates = [a for result in results for a in result if hasattr(a, "executor_config")]
    # A noninstalled instance may win ownership and then be denied at the sender.
    # Either interleaving must preserve one owner and at most one capital claim.
    import json
    assert len(json.loads(h.journal.path.read_text())["state"]["orders"]) == 1
    assert len(creates) <= 1 and len(v[1].claim_ids()) <= 1


def test_issuer_checkpoint_failure_never_returns_action_or_permits_retry(tmp_path, monkeypatch):
    h, v, market, settlements = setup_coordinator(tmp_path)
    commit = v[2].journal.commit

    def fail(state):
        if state["actions"]:
            raise OSError("injected action journal failure")
        return commit(state)
    monkeypatch.setattr(v[2].journal, "commit", fail)
    assert not any(hasattr(a, "executor_config") for a in h.propose())
    assert not any(hasattr(a, "executor_config") for a in h.propose())
    assert len(v[1].claim_ids()) == 1
    assert not v[4].sent


@pytest.mark.parametrize("side", [TradeType.BUY, TradeType.SELL])
def test_hedge_mode_closes_the_correct_existing_side_before_opening(tmp_path, side):
    from test.hummingbot.strategy_v2.life_liquidity.test_swap_executor import wire_for
    h, v, market, settlements = setup_coordinator(tmp_path, mode=PositionMode.HEDGE)
    if side == TradeType.BUY:
        h.update_target("buy-close-target", expected_version=0, target_base=D("12"))
    action = h.propose()[0]
    cfg = action.executor_config
    assert cfg.side == side and cfg.position_action == PositionAction.CLOSE and cfg.amount == D("1")
    dispatch(v[0], v[9], cfg, action)
    sent = v[4].sent[-1]
    wire = wire_for(cfg, sent["order_id"], PositionMode.HEDGE)
    wire.update(px=str(cfg.price), sz="4")
    sent["pre_send_check"](wire)
    sent["on_ack"]("exchange-" + cfg.id)
    assert wire["posSide"] == ("short" if side == TradeType.BUY else "long")
    obs = v[8]["snapshot"]
    v[8]["snapshot"] = replace(obs, sequence=2, long_base=D("1") if side == TradeType.BUY else D("0.5"),
                               short_base=D("0.5") if side == TradeType.BUY else D("1"))
    v[7]["value"] = replace(v[7]["value"], snapshot_sequence=2,
                            long_contracts=v[8]["snapshot"].long_base / D("0.25"),
                            short_contracts=v[8]["snapshot"].short_base / D("0.25"))
    settlements[cfg.id] = proof_for(v, cfg, quantity="0.5", terminal=False)
    assert h.reconcile(cfg.id)


@pytest.mark.parametrize("fault", ["target", "version", "cost", "attempts", "clock", "quantity"])
def test_corrupt_coordinator_checkpoint_cannot_restore_or_place_orders(tmp_path, fault):
    import json
    h, v, market, settlements = setup_coordinator(tmp_path)
    cfg = h.propose()[0].executor_config
    data = json.loads(h.journal.path.read_text())
    state = data["state"]
    if fault == "target":
        state["target"] = "1"
    elif fault == "version":
        state["target_version"] = 2
    elif fault == "cost":
        state["cost_hold"] = "0"
    elif fault == "attempts":
        state["attempts"] = 0
    elif fault == "clock":
        state["since_ms"] = state["last_ms"] + 1
    else:
        state["orders"][cfg.id]["filled"] = "100"
    h.journal.path.write_text(json.dumps(data))
    assert not h.authorizes(cfg, issued=True) and not h.allows_spot()
    with pytest.raises(ValueError):
        HedgeCoordinator(v[0], h.journal.path, policy=h.policy, market=lambda: market["value"],
                         settlement=lambda key: settlements.get(key), create=False)


def test_urgent_subminimum_residual_pauses_without_rounding_quantity_up(tmp_path):
    h, v, market, settlements = setup_coordinator(tmp_path)
    h.update_target("dust-target", expected_version=0, target_base=D("10.9"))
    assert h.propose() == []
    v[6].advance(1)
    set_positions(v, long="1")
    market["value"] = replace(market["value"], sequence=2, observed_at_ms=int(v[6].wall.timestamp() * 1000))
    assert h.propose() == [] and h.reason_code == "HEDGE_DUST_OR_BATCH_LIMIT"
    assert not h.allows_spot() and v[1].claim_ids() == ()


def test_changed_policy_or_missing_spot_ledger_revokes_queued_hedge(tmp_path):
    h, v, market, settlements = setup_coordinator(tmp_path)
    cfg = h.propose()[0].executor_config
    h.policy = replace(h.policy, cumulative_cost_budget_quote=D("100000"))
    assert not h.authorizes(cfg, issued=True)
    h.policy = replace(h.policy, cumulative_cost_budget_quote=D("5"))
    v[0]._order_safety_reservations.path.unlink()
    assert not h.authorizes(cfg, issued=True)


def test_default_production_switch_still_forbids_coordinator_actions(tmp_path):
    h, v, _, _ = setup_coordinator(tmp_path)
    del v[0].trading_permissions_ready
    assert not any(hasattr(a, "executor_config") for a in v[0].determine_executor_actions())
    assert v[4].sent == [] and v[1].claim_ids() == ()


def test_cost_refusal_checkpoints_newer_market_sequence_and_forbids_cheap_replay(tmp_path):
    h, v, market, _ = setup_coordinator(tmp_path)
    cfg = h.propose()[0].executor_config
    old = market["value"]
    market["value"] = replace(old, sequence=2, fee_rate=D("0.1"))
    assert not h.authorizes(cfg, issued=True)
    market["value"] = old
    assert not h.authorizes(cfg, issued=True)


def test_unhedged_deadline_keeps_running_during_unqualified_market_data(tmp_path):
    h, v, market, _ = setup_coordinator(tmp_path)
    h.update_target("dust-with-data-gap", expected_version=0, target_base=D("10.9"))
    old = market["value"]
    market["value"] = replace(old, scope_complete=False)
    assert h.propose() == []
    v[6].advance(1)
    set_positions(v, long="1")
    market["value"] = replace(old, sequence=2, observed_at_ms=int(v[6].wall.timestamp() * 1000))
    assert h.propose() == []
    assert h.reason_code == "HEDGE_DUST_OR_BATCH_LIMIT" and not h.allows_spot()
