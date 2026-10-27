"""Small admin adapters over the existing PostgreSQL models and control store."""
import base64
import hashlib
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlencode

from sqlalchemy import func, select, tuple_, update
from sqlalchemy.orm import Session

from hummingbot.model.admin_command import AdminCommand
from hummingbot.model.bot_state import BotState
from hummingbot.model.bot_state_store import ControlConflict
from hummingbot.model.order import Order
from hummingbot.model.order_status import OrderStatus
from hummingbot.model.trade_fill import TradeFill

MARKETS = {"spot": "okx", "perpetual": "okx_perpetual"}
TERMINAL_STATUSES = ("BuyOrderCompleted", "SellOrderCompleted", "OrderCancelled", "OrderFailure", "OrderExpired")


def _scope(kind, filters):
    return hashlib.sha256(json.dumps([kind, filters], sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _cursor(kind, filters, last):
    return base64.urlsafe_b64encode(json.dumps({"v": 1, "scope": _scope(kind, filters), "last": last},
                                               separators=(",", ":")).encode()).decode().rstrip("=")


def _cursor_values(value, kind, filters, size):
    if value is None:
        return None
    try:
        if not isinstance(value, str) or len(value) > 2048:
            raise ValueError
        decoded = json.loads(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
        last = decoded["last"]
        if (decoded["v"] != 1 or decoded["scope"] != _scope(kind, filters) or not isinstance(last, list) or len(last) != size
                or type(last[0]) is not int or not 0 <= last[0] <= 253402300799999
                or any(not isinstance(part, str) or not 1 <= len(part) <= 128 for part in last[1:])):
            raise ValueError
        return last
    except Exception:
        raise ValueError("Invalid cursor or changed history filters") from None


def _timestamp(value):
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


class IdempotencyConflict(ControlConflict):
    pass


class AdminStore:
    def __init__(self, store):
        self.store = store
        self.engine = store.engine
        self.bot_id = store.bot_id

    def command(self, idempotency_key, payload, *, catalogue, request=None, principal=None):
        if (not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 128
                or any(not 33 <= ord(character) <= 126 for character in idempotency_key)):
            raise ValueError("Idempotency-Key must contain 1–128 visible ASCII characters")
        request_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                                 allow_nan=False).encode()).hexdigest()
        with self.engine.begin() as connection:
            # One bot has one control row. This also serializes first use of a
            # retry key, so its receipt and control change always commit together.
            if not connection.execute(select(BotState.bot_id).where(
                    BotState.bot_id == self.bot_id).with_for_update()).first():
                raise ControlConflict("Bot state is unavailable")
            existing = connection.execute(select(AdminCommand).where(
                AdminCommand.bot_id == self.bot_id, AdminCommand.idempotency_key == idempotency_key)).mappings().first()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise IdempotencyConflict("Idempotency-Key is already bound to another request")
                return dict(existing)
            result = self.store.request_mm_session(
                action=payload["action"], expected_version=payload["expected_version"], catalogue=catalogue,
                request=request, current_session_id=payload.get("session_id"), connection=connection, principal=principal, source="admin_api")
            return self._insert_command(connection, {
                "id": str(uuid.uuid4()), "bot_id": self.bot_id, "idempotency_key": idempotency_key,
                "request_hash": request_hash, "action": payload["action"], "session_id": result["session_id"],
                "expected_version": payload["expected_version"], "effective_version": result["control_version"],
                "status": "accepted", "payload": payload, "created_at": func.clock_timestamp(),
                "updated_at": func.clock_timestamp(),
            })

    @staticmethod
    def _insert_command(connection, values):
        return dict(connection.execute(AdminCommand.__table__.insert().values(**values)
                                       .returning(*AdminCommand.__table__.c)).mappings().one())

    def command_by_id(self, command_id):
        with self.engine.connect() as connection:
            row = connection.execute(select(AdminCommand).where(AdminCommand.bot_id == self.bot_id,
                                                                AdminCommand.id == command_id)).mappings().first()
            return dict(row) if row else None

    def observations(self):
        from hummingbot.core.reporting import reporting_view
        row = self.store.read(publication=True)
        if row is None:
            raise ControlConflict("Bot identity is unavailable")
        return reporting_view(row)

    def daily(self, **filters):
        from hummingbot.model.admin_reporting import daily
        return daily(self.engine, **filters)

    def request_check(self, key, payload):
        from hummingbot.model.admin_reconciliation import request_check
        return request_check(self, key, payload)

    def claim_check(self, owner_uuid, generation):
        from hummingbot.model.admin_reconciliation import claim_check
        return claim_check(self, owner_uuid, generation)

    def finish_check(self, check_id, owner_uuid, generation, report):
        from hummingbot.model.admin_reconciliation import finish_check
        return finish_check(self, check_id, owner_uuid, generation, report)

    def latest_check(self):
        from hummingbot.model.admin_reconciliation import latest_check
        return latest_check(self)

    def reconciliation_scope(self, request, cutoff_ms):
        from hummingbot.model.admin_reconciliation import local_scope
        return local_scope(self, request, cutoff_ms)

    @staticmethod
    def _history_query(session, model, timestamp, market, pair, start_ms, end_ms):
        if market not in ("spot", "perpetual", "all"):
            raise ValueError("Unknown market")
        query = session.query(model).filter(model.market.in_(tuple(MARKETS.values()) if market == "all" else (MARKETS[market],)))
        if pair is not None:
            query = query.filter(model.symbol == pair)
        if start_ms is not None:
            query = query.filter(timestamp >= start_ms)
        if end_ms is not None:
            query = query.filter(timestamp < end_ms)
        if start_ms is not None and end_ms is not None and start_ms >= end_ms:
            raise ValueError("History range is empty")
        return query

    @staticmethod
    def _order_sides(session, rows):
        ids = [row.id for row in rows]
        if not ids:
            return {}
        sides = {}
        for order_id, status in session.query(OrderStatus.order_id, OrderStatus.status).filter(
                OrderStatus.order_id.in_(ids), OrderStatus.status.in_(
                    ("PendingBuy", "PendingSell", "BuyOrderCreated", "SellOrderCreated"))).distinct():
            side = "BUY" if status in ("PendingBuy", "BuyOrderCreated") else "SELL"
            sides[order_id] = side if order_id not in sides else (side if sides[order_id] == side else None)
        missing = set(ids) - set(sides)
        if missing:
            for order_id, first, last in session.query(TradeFill.order_id, func.min(TradeFill.trade_type), func.max(TradeFill.trade_type)).filter(
                    TradeFill.market.in_(tuple(MARKETS.values())), TradeFill.order_id.in_(missing)).group_by(TradeFill.order_id):
                sides[order_id] = first if first == last else None
        return sides

    @staticmethod
    def _order_view(row, sides):
        return dict(id=row.id, connector=row.market, market="spot" if row.market == "okx" else "perpetual",
                    pair=row.symbol, side=sides.get(row.id), amount=row.amount, price=row.price,
                    order_type=row.order_type, position=row.position, leverage=row.leverage, status=row.last_status,
                    creation_pending=row.creation_pending, exchange_order_id=row.exchange_order_id,
                    created_at=_timestamp(row.creation_timestamp), updated_at=_timestamp(row.last_update_timestamp))

    def orders(self, *, market="all", pair=None, start_ms=None, end_ms=None, limit=100, cursor=None, state="all"):
        if type(limit) is not int or not 1 <= limit <= 500 or state not in ("open", "closed", "all"):
            raise ValueError("Invalid order page")
        scope = dict(market=market, pair=pair, start_ms=start_ms, end_ms=end_ms, state=state)
        point = _cursor_values(cursor, "orders", scope, 2)
        with Session(self.engine) as session:
            query = self._history_query(session, Order, Order.creation_timestamp, market, pair, start_ms, end_ms)
            if state != "all":
                closed = Order.last_status.in_(TERMINAL_STATUSES)
                query = query.filter(closed if state == "closed" else ~closed)
            if point:
                query = query.filter(tuple_(Order.creation_timestamp, Order.id) < tuple(point))
            rows = query.order_by(Order.creation_timestamp.desc(), Order.id.desc()).limit(limit + 1).all()
            selected = rows[:limit]
            sides = self._order_sides(session, selected)
            next_cursor = _cursor("orders", scope, [selected[-1].creation_timestamp, selected[-1].id]) if len(rows) > limit else None
            return dict(items=[self._order_view(row, sides) for row in selected], next_cursor=next_cursor)

    def order(self, order_id, *, timeline_limit=100):
        if type(timeline_limit) is not int or not 1 <= timeline_limit <= 500:
            raise ValueError("Invalid timeline size")
        with Session(self.engine) as session:
            row = session.query(Order).filter(Order.id == order_id, Order.market.in_(tuple(MARKETS.values()))).one_or_none()
            if row is None:
                return None
            events = session.query(OrderStatus).filter(OrderStatus.order_id == order_id).order_by(
                OrderStatus.timestamp.desc(), OrderStatus.id.desc()).limit(timeline_limit + 1).all()
            return dict(order=self._order_view(row, self._order_sides(session, [row])),
                        timeline=[dict(id=item.id, timestamp=_timestamp(item.timestamp), status=item.status)
                                  for item in reversed(events[:timeline_limit])],
                        timeline_truncated=len(events) > timeline_limit,
                        fills_url="/api/v1/admin/fills?" + urlencode({"order_id": order_id}))

    def fills(self, *, market="all", pair=None, start_ms=None, end_ms=None, limit=100, cursor=None, order_id=None):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("Invalid fill page")
        scope = dict(market=market, pair=pair, start_ms=start_ms, end_ms=end_ms, order_id=order_id)
        point = _cursor_values(cursor, "fills", scope, 4)
        with Session(self.engine) as session:
            query = self._history_query(session, TradeFill, TradeFill.timestamp, market, pair, start_ms, end_ms)
            if order_id is not None:
                query = query.filter(TradeFill.order_id == order_id)
            if point:
                query = query.filter(tuple_(TradeFill.timestamp, TradeFill.market, TradeFill.order_id, TradeFill.exchange_trade_id) < tuple(point))
            rows = query.order_by(TradeFill.timestamp.desc(), TradeFill.market.desc(), TradeFill.order_id.desc(),
                                  TradeFill.exchange_trade_id.desc()).limit(limit + 1).all()
            selected = rows[:limit]
            items = []
            for row in selected:
                conversion = "recorded" if row.trade_fee_in_quote is not None else "unavailable"
                if row.trade_fee_in_quote == 0 and self._has_original_fee(row.trade_fee):
                    conversion = "legacy_ambiguous"
                items.append(dict(id=row.exchange_trade_id, order_id=row.order_id,
                                  market="spot" if row.market == "okx" else "perpetual", connector=row.market, pair=row.symbol,
                                  side=row.trade_type, price=row.price, amount=row.amount, timestamp=_timestamp(row.timestamp),
                                  leverage=row.leverage, position=row.position, fee=row.trade_fee,
                                  fee_in_quote=row.trade_fee_in_quote if conversion == "recorded" else None,
                                  quote_currency=row.quote_asset, conversion_status=conversion))
            last = selected[-1] if selected else None
            next_cursor = _cursor("fills", scope, [last.timestamp, last.market, last.order_id, last.exchange_trade_id]) if len(rows) > limit else None
            return dict(items=items, next_cursor=next_cursor)

    @staticmethod
    def _has_original_fee(fee):
        try:
            return (Decimal(str(fee.get("percent", "0"))) != 0
                    or any(Decimal(str(item["amount"])) != 0 for item in fee.get("flat_fees", [])))
        except Exception:
            return True

    @staticmethod
    def record_observation(connection, bot_id, version, status, state):
        """Persist outcome with the owner's status; acknowledgement alone is not success."""
        pending = (AdminCommand.bot_id == bot_id, AdminCommand.status.in_(("accepted", "applying")),
                   AdminCommand.action.in_(("start", "switch", "stop")))
        connection.execute(update(AdminCommand).where(*pending, AdminCommand.effective_version < version).values(
            status="superseded", error="newer_control_version", updated_at=func.clock_timestamp()))
        command = connection.execute(select(AdminCommand).where(*pending, AdminCommand.effective_version == version)).mappings().first()
        if command is None:
            return
        engine = status.get("engine") or {}
        session = state["mm_session"] or {}
        if (command["action"] == "stop" and state["desired_state"] == "stopped"
                and not status.get("running") and engine.get("strategy_running") is False
                and engine.get("clock_running") is False):
            result = "succeeded"
        elif (command["action"] in ("start", "switch") and status.get("running")
                and status.get("phase") == "running" and engine.get("strategy_running") is True
                and session.get("id") == command["session_id"] and session.get("status") == "running"):
            result = "succeeded"
        elif status.get("phase") == "halted" and status.get("error"):
            result = "failed"
        else:
            result = "applying"
        connection.execute(update(AdminCommand).where(AdminCommand.id == command["id"]).values(
            status=result, error=status.get("error") if result == "failed" else None, updated_at=func.clock_timestamp()))
