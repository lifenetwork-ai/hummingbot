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


def _cursor_values(value, kind, filters, size, *, max_first=253402300799999999 // 1000):
    if value is None:
        return None
    try:
        if not isinstance(value, str) or len(value) > 2048:
            raise ValueError
        decoded = json.loads(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
        last = decoded["last"]
        if (decoded["v"] != 1 or decoded["scope"] != _scope(kind, filters) or not isinstance(last, list) or len(last) != size
                or type(last[0]) is not int or not 0 <= last[0] <= max_first
                or any(not isinstance(part, str) or not 1 <= len(part) <= 128 for part in last[1:])):
            raise ValueError
        return last
    except Exception:
        raise ValueError("Invalid cursor or changed history filters") from None


def _timestamp(value):
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


class IdempotencyConflict(ControlConflict):
    code = "idempotency_key_reused"


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
            expected_hash = payload.get("expected_catalogue_hash")
            if payload["action"] != "stop" and expected_hash is not None and expected_hash != catalogue.catalogue_hash():
                raise ControlConflict("Deployed catalogue changed", code="catalogue_changed")
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

    def commands_page(self, **filters):
        from hummingbot.model.admin_history import commands_page
        return commands_page(self, **filters)

    def sessions_page(self, **filters):
        from hummingbot.model.admin_history import sessions_page
        return sessions_page(self, **filters)

    def check_by_id(self, check_id):
        row = self.command_by_id(check_id)
        return row if row and row["action"] == "reconciliation_check" else None

    def session_configuration(self, session_id):
        from hummingbot.client.config.mm_profiles import MMProfilesConfig
        from hummingbot.core.mm_session import MMSession, MMSessionRequest
        from hummingbot.model.mm_event import MMEvent
        with self.engine.connect() as connection:
            current = connection.execute(select(BotState.mm_session).where(BotState.bot_id == self.bot_id)).scalar()
            if current and current.get("id") == session_id:
                raw, profile, source = current, current["profile_id"], "current_session"
            else:
                event = connection.execute(select(MMEvent.payload, MMEvent.new_profile).where(
                    MMEvent.bot_id == self.bot_id, MMEvent.session_id == session_id,
                    MMEvent.event_type == "session_created").order_by(MMEvent.id).limit(1)).first()
                if event is None:
                    return None
                raw, profile, source = event.payload["configuration"], event.new_profile, "session_created_event"
        catalogue = MMProfilesConfig.model_validate(MMSession.restore_legacy_random_pool(raw)["catalogue"])
        request = MMSessionRequest.model_validate(raw["request"])
        legacy = []
        if request.references is not None:
            legacy.append("references")
        if request.selection == "random":
            if request.profile_ids is not None and set(request.profile_ids) != set(catalogue.random.profile_ids):
                legacy.append("profile_ids")
            if request.step_duration_seconds is not None and request.step_duration_seconds != catalogue.random.step_duration_seconds:
                legacy.append("step_duration_seconds")
        return dict(session_id=session_id, source=source, profile_id=profile,
                    profile_context="current" if source == "current_session" else "at_creation",
                    config_hash=raw["config_hash"], catalogue_hash=catalogue.catalogue_hash(),
                    catalogue=catalogue.model_dump(mode="json"), request=request.model_dump(mode="json"), legacy_overrides=legacy)

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
    def _order_sides(session, rows, fill_stats=None):
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
        if missing and fill_stats is not None:
            for order_id in missing:
                stats = fill_stats.get(order_id)
                if stats:
                    sides[order_id] = stats["first_side"] if stats["first_side"] == stats["last_side"] else None
        elif missing:
            for order_id, first, last in session.query(TradeFill.order_id, func.min(TradeFill.trade_type), func.max(TradeFill.trade_type)).filter(
                    TradeFill.market.in_(tuple(MARKETS.values())), TradeFill.order_id.in_(missing)).group_by(TradeFill.order_id):
                sides[order_id] = first if first == last else None
        return sides

    @staticmethod
    def _order_view(row, sides, fill_stats=None):
        result = dict(id=row.id, connector=row.market, market="spot" if row.market == "okx" else "perpetual",
                      pair=row.symbol, side=sides.get(row.id), amount=row.amount, price=row.price,
                      order_type=row.order_type, position=row.position, leverage=row.leverage, status=row.last_status,
                      creation_pending=row.creation_pending, exchange_order_id=row.exchange_order_id,
                      created_at=_timestamp(row.creation_timestamp), updated_at=_timestamp(row.last_update_timestamp))
        if fill_stats is not None:
            stats = fill_stats.get(row.id, {})
            filled = stats.get("amount", Decimal("0"))
            inconsistent = not filled.is_finite() or filled < 0 or filled > row.amount or stats.get("min_amount", 0) < 0
            provisional = bool(row.creation_pending)
            result.update(recorded_filled_amount=filled if filled.is_finite() else None,
                          recorded_remaining_amount=None if inconsistent or provisional else row.amount - filled,
                          progress_basis="inconsistent_recording" if inconsistent else "provisional_order" if provisional else "recorded_fills_only")
        return result

    @staticmethod
    def _fill_stats(session, rows):
        if not rows:
            return {}
        result = session.query(TradeFill.order_id, func.sum(TradeFill.amount), func.min(TradeFill.amount),
                               func.min(TradeFill.trade_type), func.max(TradeFill.trade_type)).filter(
            tuple_(TradeFill.market, TradeFill.order_id).in_([(row.market, row.id) for row in rows])).group_by(TradeFill.order_id)
        return {identity: dict(amount=amount, min_amount=minimum, first_side=first, last_side=last)
                for identity, amount, minimum, first, last in result}

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
            # These pages only need scalar values. Entity hydration creates an
            # identity-map/attribute graph for every row without using relations.
            rows = query.with_entities(*Order.__table__.c).order_by(
                Order.creation_timestamp.desc(), Order.id.desc()).limit(limit + 1).all()
            selected = rows[:limit]
            stats = self._fill_stats(session, selected)
            sides = self._order_sides(session, selected, stats)
        # All values are materialized; release the transaction/pooled connection
        # before page formatting. Read-only scalar rows cannot lazy-load.
        next_cursor = _cursor("orders", scope, [selected[-1].creation_timestamp, selected[-1].id]) if len(rows) > limit else None
        return dict(items=[self._order_view(row, sides, stats) for row in selected], next_cursor=next_cursor)

    def order(self, order_id, *, timeline_limit=100):
        if type(timeline_limit) is not int or not 1 <= timeline_limit <= 500:
            raise ValueError("Invalid timeline size")
        with Session(self.engine) as session:
            row = session.query(Order).filter(Order.id == order_id, Order.market.in_(tuple(MARKETS.values()))).one_or_none()
            if row is None:
                return None
            events = session.query(OrderStatus).filter(OrderStatus.order_id == order_id).order_by(
                OrderStatus.timestamp.desc(), OrderStatus.id.desc()).limit(timeline_limit + 1).all()
            stats = self._fill_stats(session, [row])
            return dict(order=self._order_view(row, self._order_sides(session, [row], stats), stats),
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
            rows = query.with_entities(*TradeFill.__table__.c).order_by(
                TradeFill.timestamp.desc(), TradeFill.market.desc(), TradeFill.order_id.desc(),
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
