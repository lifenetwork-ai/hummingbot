"""Durable observational check receipts; no trading control transition."""
import hashlib
import json
import uuid
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from hummingbot.model.admin_command import AdminCommand
from hummingbot.model.bot_state import BotState
from hummingbot.model.bot_state_store import ControlConflict
from hummingbot.model.order import Order
from hummingbot.model.trade_fill import TradeFill


def _owner(row, owner_uuid, generation):
    return row["owner_uuid"] == owner_uuid and row["owner_generation"] == generation


def _state(connection, bot_id):
    return connection.execute(select(BotState, func.clock_timestamp().label("database_now")).where(
        BotState.bot_id == bot_id).with_for_update()).mappings().one()


def request_check(store, key, payload):
    if not isinstance(key, str) or not 1 <= len(key) <= 128 or any(not 33 <= ord(c) <= 126 for c in key):
        raise ValueError("Idempotency-Key is required")
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    with store.engine.begin() as connection:
        state = _state(connection, store.bot_id)
        prior = connection.execute(select(AdminCommand).where(AdminCommand.bot_id == store.bot_id,
                                                              AdminCommand.idempotency_key == key)).mappings().first()
        if prior:
            if prior["action"] != "reconciliation_check" or prior["request_hash"] != digest:
                raise ControlConflict("Retry identity conflict")
            return dict(prior)
        latest = connection.execute(select(AdminCommand).where(AdminCommand.bot_id == store.bot_id,
                                                               AdminCommand.action == "reconciliation_check").order_by(AdminCommand.created_at.desc()).limit(1)).mappings().first()
        if latest and (latest["status"] in ("accepted", "applying") or
                       state["database_now"] - latest["created_at"] < timedelta(seconds=30)):
            raise ControlConflict("One bounded check at a time, at most one new check per thirty seconds")
        available = state["owner_uuid"] and state["heartbeat"] and timedelta(0) <= state["database_now"] - state["heartbeat"] <= timedelta(seconds=5)
        values = dict(id=str(uuid.uuid4()), bot_id=store.bot_id, idempotency_key=key, request_hash=digest,
                      action="reconciliation_check", session_id=(state["mm_session"] or {}).get("id"),
                      expected_version=state["control_version"], effective_version=state["control_version"],
                      status="accepted" if available else "failed", payload=payload,
                      result=None if available else dict(state="unavailable", markets=[], started_at=None, finished_at=state["database_now"].isoformat()),
                      error=None if available else "owner_unavailable", created_at=state["database_now"], updated_at=state["database_now"])
        return store._insert_command(connection, values)


def claim_check(store, owner_uuid, generation):
    with store.engine.begin() as connection:
        state = _state(connection, store.bot_id)
        if not _owner(state, owner_uuid, generation):
            return None
        row = connection.execute(select(AdminCommand).where(AdminCommand.bot_id == store.bot_id,
                                                            AdminCommand.action == "reconciliation_check", AdminCommand.status.in_(("accepted", "applying")))
                                 .order_by(AdminCommand.created_at).limit(1)).mappings().first()
        if row is None:
            return None
        result = dict(state="checking", markets=[], started_at=state["database_now"].isoformat(), finished_at=None,
                      lease=dict(owner_uuid=owner_uuid, generation=generation))
        return dict(connection.execute(update(AdminCommand).where(AdminCommand.id == row["id"]).values(
            status="applying", result=result, updated_at=state["database_now"]).returning(*AdminCommand.__table__.c)).mappings().one())


def finish_check(store, check_id, owner_uuid, generation, report):
    with store.engine.begin() as connection:
        state = _state(connection, store.bot_id)
        if not _owner(state, owner_uuid, generation):
            return False
        result = connection.execute(update(AdminCommand).where(AdminCommand.bot_id == store.bot_id,
                                                               AdminCommand.id == check_id, AdminCommand.action == "reconciliation_check", AdminCommand.status == "applying",
                                                               AdminCommand.result["lease"]["owner_uuid"].astext == owner_uuid,
                                                               AdminCommand.result["lease"]["generation"].astext == str(generation)).values(
            status="succeeded", result=report, updated_at=state["database_now"]))
        return result.rowcount == 1


def latest_check(store):
    with store.engine.connect() as connection:
        row = connection.execute(select(AdminCommand).where(AdminCommand.bot_id == store.bot_id,
                                                            AdminCommand.action == "reconciliation_check").order_by(AdminCommand.created_at.desc(), AdminCommand.id.desc()).limit(1)).mappings().first()
        return dict(row) if row else None


def local_scope(store, request, cutoff_ms):
    from hummingbot.model.admin_store import MARKETS, TERMINAL_STATUSES
    names = list(MARKETS.values()) if request.market == "all" else [MARKETS[request.market]]
    with store.engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection, connection.begin(), Session(connection) as session:
        state = session.query(BotState).filter(BotState.bot_id == store.bot_id).one()
        query = session.query(Order).filter(Order.market.in_(names),
                                            (Order.last_update_timestamp >= cutoff_ms) | ~Order.last_status.in_(TERMINAL_STATUSES))
        if request.pair:
            query = query.filter(Order.symbol == request.pair)
        orders = query.order_by(Order.last_update_timestamp.desc(), Order.id).limit(request.max_orders + 1).all()
        truncated = len(orders) > request.max_orders
        orders = orders[:request.max_orders]
        sides = store._order_sides(session, orders)
        fills = session.query(TradeFill).filter(TradeFill.market.in_(names), TradeFill.order_id.in_([row.id for row in orders]))
        rows = fills.order_by(TradeFill.market, TradeFill.order_id, TradeFill.exchange_trade_id).limit(1001).all()
        truncated = truncated or len(rows) > 1000
        result = dict(orders=[store._order_view(row, sides) for row in orders], fills=[dict(
            connector=row.market, order_id=row.order_id, id=row.exchange_trade_id, amount=str(row.amount), price=str(row.price),
            fee=row.trade_fee, timestamp=row.timestamp) for row in rows[:1000]], truncated=truncated,
            control_version=state.control_version, session_generation=(state.mm_session or {}).get("generation"),
            # Funding coverage is observational: its background checkpoint must
            # not invalidate an otherwise stable orders/fills/exposure check.
            # Keep HALT and every other recovery change in the fingerprint.
            recovery_state={key: value for key, value in (state.recovery_state or {}).items()
                            if key != "funding_sync"})
        result["fingerprint"] = hashlib.sha256(json.dumps(result, sort_keys=True, default=str).encode()).hexdigest()
        return result
