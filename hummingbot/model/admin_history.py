"""Bounded operator history projections over existing receipts and transitions."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, tuple_

from hummingbot.model.admin_command import AdminCommand
from hummingbot.model.admin_store import _cursor, _cursor_values
from hummingbot.model.bot_state import BotState
from hummingbot.model.mm_event import MMEvent

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
COMMAND_ACTIONS = ("start", "switch", "stop")
COMMAND_STATES = ("accepted", "applying", "succeeded", "failed", "superseded")
TRANSITIONS = {"session_created": "preparing", "step_started": "running", "step_changed": "preparing",
               "stop_requested": "stopping", "session_stopped": "stopped", "session_expired": "expired",
               "session_switched": "replaced", "session_replaced": "replaced"}


def _limit(limit):
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("Invalid page size")


def commands_page(store, *, limit=100, cursor=None, action=None, status=None, session_id=None):
    _limit(limit)
    if action is not None and action not in COMMAND_ACTIONS or status is not None and status not in COMMAND_STATES:
        raise ValueError("Invalid command filters")
    scope = dict(bot_id=store.bot_id, action=action, status=status, session_id=session_id)
    point = _cursor_values(cursor, "admin_commands", scope, 2, max_first=253402300799999999)
    query = select(AdminCommand).where(AdminCommand.bot_id == store.bot_id, AdminCommand.action.in_(COMMAND_ACTIONS))
    for field, value in ((AdminCommand.action, action), (AdminCommand.status, status), (AdminCommand.session_id, session_id)):
        if value is not None:
            query = query.where(field == value)
    if point:
        query = query.where(tuple_(AdminCommand.created_at, AdminCommand.id) < (EPOCH + timedelta(microseconds=point[0]), point[1]))
    with store.engine.connect() as connection:
        rows = connection.execute(query.order_by(AdminCommand.created_at.desc(), AdminCommand.id.desc()).limit(limit + 1)).mappings().all()
    selected = [dict(row) for row in rows[:limit]]
    after = None
    if len(rows) > limit:
        delta = selected[-1]["created_at"] - EPOCH
        micros = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
        after = _cursor("admin_commands", scope, [micros, selected[-1]["id"]])
    return dict(items=selected, next_cursor=after)


def sessions_page(store, *, limit=100, cursor=None):
    _limit(limit)
    scope = dict(bot_id=store.bot_id)
    point = _cursor_values(cursor, "session_list", scope, 1)
    query = select(MMEvent.id, MMEvent.session_id, MMEvent.created_at, MMEvent.new_profile,
                   MMEvent.payload["configuration"]["request"].label("request"),
                   MMEvent.payload["configuration"]["config_hash"].astext.label("config_hash")).where(
        MMEvent.bot_id == store.bot_id, MMEvent.event_type == "session_created")
    if point:
        query = query.where(MMEvent.id < point[0])
    with store.engine.connect() as connection:
        rows = connection.execute(query.order_by(MMEvent.id.desc()).limit(limit + 1)).mappings().all()
        selected = rows[:limit]
        current = connection.execute(select(BotState.mm_session["id"].astext.label("id"),
                                            BotState.mm_session["status"].astext.label("status"),
                                            BotState.mm_session["profile_id"].astext.label("profile_id")).where(
            BotState.bot_id == store.bot_id)).mappings().first() or {}
        latest = connection.execute(select(MMEvent.session_id, MMEvent.event_type, MMEvent.created_at, MMEvent.new_profile).where(
            MMEvent.bot_id == store.bot_id, MMEvent.session_id.in_([row["session_id"] for row in selected]),
            MMEvent.event_type.in_(tuple(TRANSITIONS))).distinct(MMEvent.session_id).order_by(MMEvent.session_id, MMEvent.id.desc())).mappings().all() if selected else []
    by_id = {row["session_id"]: row for row in latest}
    items = []
    for row in selected:
        identity = row["session_id"]
        last = by_id.get(identity, row)
        is_current = identity == current.get("id")
        items.append(dict(id=identity, created_at=row["created_at"], last_event_at=last["created_at"],
                          request=row["request"], config_hash=row["config_hash"], is_current=is_current,
                          profile_id=current.get("profile_id") if is_current else last["new_profile"],
                          status=current.get("status") if is_current else TRANSITIONS.get(last.get("event_type"), "unknown"),
                          state_source="current_session" if is_current else "last_recorded_transition",
                          configuration_url=f"/api/v1/admin/sessions/{identity}/configuration",
                          events_url=f"/api/v1/admin/sessions/{identity}/events"))
    return dict(items=items, next_cursor=_cursor("session_list", scope, [selected[-1]["id"]]) if len(rows) > limit else None,
                current_session_id=current.get("id"), coverage="recorded_session_creations")
