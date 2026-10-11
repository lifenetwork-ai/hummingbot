"""Complete synthetic settled history, built in bulk outside measured work.

Equivalent to paired fully filled maker buys/sells at the independent value.
Every intent has a WAL, reservation, fee, attribution, loss, capital tombstone,
action and stored-executor identity. No financial journal is omitted.
"""
from dataclasses import asdict, replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.test_carry_monitor import refresh

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.life_liquidity.action_journal import QuoteActionRecord
from hummingbot.strategy_v2.life_liquidity.joint_recovery import RecoveryOrder, RecoveryTrade, digest, encode_order
from hummingbot.strategy_v2.life_liquidity.risk import SpotIntent, _Reservation
from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalClaim, _json
from hummingbot.strategy_v2.life_liquidity.state import IntentRecord
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo


def seed_settled_history(f, count):
    assert count % 2 == 0
    c, pool, _, _, _, _, clock, _, _, _, template, _ = f.v
    current = c._order_safety_manager.current_session
    now = int(clock.wall.timestamp() * 1000)
    reservations, trades, fees, attribution, loss, actions, records, settled = {}, {}, {}, {}, {}, {}, {}, {}
    orders, stored = [], []
    for i in range(count):
        key, wire, trade_id = f"past-{i:05}", f"HBOTpast{i:024}", f"history-{i:05}"
        side = "BUY" if i % 2 == 0 else "SELL"
        claim = CapitalClaim(key, wire, "12345", current.session_id, current.epoch, current.config_version,
                             1, "SPOT", side, "OPEN", D("1"), D("1"), 1)
        orders.append(RecoveryOrder(claim, "exchange-" + key, "filled", (
            RecoveryTrade(trade_id, D("1"), D("1"), "USDT", D("0"), D("0"), now),)))
        records[key] = IntentRecord(key, wire, current.session_id, current.epoch, key, "TERMINAL",
                                    exchange_order_id="exchange-" + key, exchange_terminal_observed=True,
                                    slot_market="LIFE-USDT", slot_side=side, slot_level=0)
        reservations[key] = _Reservation(
            SpotIntent(key, side, D("1"), D("1"), current.session_id, current.epoch), D("0"), D("1"), "TERMINAL")
        trades[trade_id] = (key, D("1"), D("1"))
        fees["FEE:" + trade_id] = ("FEE", "USDT", D("0"))
        attribution[trade_id] = dict(intent_id=key, wire_id=wire, session_id=current.session_id,
                                     side=side, quantity_base="1", price_usdt="1", fee_currency="USDT", signed_fee="0",
                                     fill_at_ms=now, fee_cost_quote="0", independent_value_usdt="1", loss_quote="0")
        loss["fill:" + trade_id] = dict(session_id=current.session_id, day=clock.wall.date().isoformat(), loss_quote="0")
        actions[key] = QuoteActionRecord(key, "life", current.session_id, current.epoch, current.config_version,
                                         "LIFE-USDT", side, 0, "RECONCILED")
        settled[key] = dict(claim=_json(asdict(claim)), proof_id="0" * 64)
        config = template.model_copy(update=dict(id=key, amount=D("1"), side=TradeType[side], level_id="0"))
        stored.append(ExecutorInfo(id=key, timestamp=clock.wall.timestamp(), type=config.type, status=RunnableStatus.TERMINATED,
                                   config=config, net_pnl_pct=D("0"), net_pnl_quote=D("0"), cum_fees_quote=D("0"),
                                   filled_amount_quote=D("1"), is_active=False, is_trading=False, controller_id="life",
                                   custom_info=dict(recovery_account_uid="12345", recovery_order_ids=[wire], order_id=wire)))
    wal, ledger, attributor = c._order_safety_wal, c._order_safety_reservations, c._fill_attributor
    wal._save(records)
    wal._records = records
    ledger._commit(D("10"), D("10"), reservations, trades, fees)
    attributor._save(attribution)
    attributor._events = attribution
    budget = c._execution_loss_budget
    budget._save({**budget._events, **loss})
    budget._events.update(loss)
    c._quote_action_planner.action_journal._commit(actions)
    with pool.journal.locked() as state:
        state["settled"] = settled
        pool.journal.commit(state)
    c._runner_orchestrator.get_stored_executors_by_controller = lambda _: stored
    refresh(f.v, f.feeds)
    f.history["value"] = replace(f.history["value"], snapshot_sequence=f.v[8]["snapshot"].sequence, orders=tuple(orders))
    # This fixture represents a previously completed cut, not 10k new fills in
    # one poll. Reconciliation below must independently qualify the whole cut.
    with f.joint.journal.locked() as state:
        encoded = _json(asdict(replace(f.history["value"], orders=())))
        encoded["orders"] = [encode_order(o) for o in orders]
        proof = digest(encoded)
        state.update(sequence=f.history["value"].snapshot_sequence, bundle=encoded, proof_id=proof,
                     phase="COMMITTED", valuation=dict(nav_quote="20", adjusted_nav_quote="20", highwater_quote="20",
                                                       drawdown_bps="0", unrealized_quote="0", external_flows_quote="0"))
        f.joint.journal.commit(state)
    with pool.journal.locked() as state:
        for entry in state["settled"].values():
            entry["proof_id"] = proof
        pool.journal.commit(state)
    return stored
