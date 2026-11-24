"""Bounded transition history on existing control transactions, not another engine."""
import hashlib
import json
import math
import re

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Sequence,
    Text,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB, insert

from hummingbot.model import HummingbotBase


class MMEvent(HummingbotBase):
    __tablename__ = "mm_events"
    __table_args__ = (UniqueConstraint("event_key"),
                      Index("idx_mm_events_bot_id_event_type_id", "bot_id", "event_type", "id"),
                      Index("idx_mm_events_bot_id_session_id_id", "bot_id", "session_id", "id"),
                      Index("idx_mm_events_bot_id_session_id_event_type_id", "bot_id", "session_id", "event_type", "id"),
                      Index("idx_mm_events_bot_id_session_id_source_id", "bot_id", "session_id", "source", "id"))
    id = Column(BigInteger, Sequence("seq_mm_events_id", metadata=HummingbotBase.metadata), primary_key=True, nullable=False)
    bot_id = Column(Text, ForeignKey("bot_state.bot_id"), nullable=False)
    session_id = Column(Text, nullable=False)
    event_key = Column(Text, nullable=False)
    event_type = Column(Text, nullable=False)
    source = Column(Text, nullable=False)
    principal = Column(Text)
    generation = Column(BigInteger, nullable=False)
    control_version = Column(BigInteger, nullable=False)
    old_profile = Column(Text)
    new_profile = Column(Text)
    payload = Column(JSONB().with_variant(JSON(), "sqlite"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)


def append_event(connection, *, bot_id, session, event_type, version, source, principal=None,
                 previous=None, payload=None, identity=None):
    value = payload or {}
    maximum = 1024 * 1024 if event_type == "session_created" else 8192
    if len(json.dumps(value, separators=(",", ":"), allow_nan=False).encode()) > maximum:
        raise ValueError("MM history payload exceeds its bounded size")
    key = hashlib.sha256(json.dumps([bot_id, session["id"], event_type, identity if identity is not None else
                                     [session["generation"], version]], separators=(",", ":")).encode()).hexdigest()
    connection.execute(insert(MMEvent).values(bot_id=bot_id, session_id=session["id"], event_key=key,
                                              event_type=event_type, source=source, principal=principal, generation=session["generation"],
                                              control_version=version, old_profile=previous.get("profile_id") if previous else None,
                                              new_profile=session["profile_id"], payload=value, created_at=func.clock_timestamp())
                       .on_conflict_do_nothing(index_elements=[MMEvent.event_key]))


def record_transition(connection, bot_id, previous, current, version, *, source="scheduler", principal=None):
    if previous == current:
        return
    if current is None:
        if previous:
            append_event(connection, bot_id=bot_id, session=previous, previous=previous, event_type="session_replaced",
                         version=version, source=source, principal=principal)
        return
    if previous is None or previous["id"] != current["id"]:
        if previous and previous["status"] in ("preparing", "running", "stopping"):
            append_event(connection, bot_id=bot_id, session=previous, previous=previous, event_type="session_switched",
                         version=version, source=source, principal=principal, payload=dict(next_session_id=current["id"]))
        append_event(connection, bot_id=bot_id, session=current, event_type="session_created", version=version,
                     source=source, principal=principal, identity="configuration",
                     payload=dict(configuration={key: current[key] for key in ("catalogue", "request", "config_hash")},
                                  previous_session_id=previous["id"] if previous else None))
        return
    kind = None
    if current["status"] in ("stopping", "stopped", "expired") and current["status"] != previous["status"]:
        kind = {"stopping": "stop_requested", "stopped": "session_stopped", "expired": "session_expired"}[current["status"]]
    elif current["step_index"] != previous["step_index"]:
        kind = "step_changed"
    elif current["status"] == "running" and previous["status"] != "running":
        kind = "step_started"
    if kind:
        payload = {key: current[key] for key in ("status", "step_index", "started_at", "expires_at", "step_expires_at", "anchors")}
        identity = ([current["generation"], version, current["step_index"]]
                    if kind == "step_changed" and current["generation"] == previous["generation"] else None)
        append_event(connection, bot_id=bot_id, session=current, previous=previous, event_type=kind, version=version,
                     source=source, principal=principal, payload=payload, identity=identity)


def _guard_diagnostics(guard):
    evidence = {}
    detail = guard.get('detail')
    if isinstance(detail, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,63}', detail):
        evidence['detail'] = detail
    for field in ('book_observed_at', 'evaluated_at', 'recovery_started_at'):
        timestamp = guard.get(field)
        if type(timestamp) in (int, float) and 0 < timestamp and math.isfinite(timestamp):
            evidence[field] = timestamp  # Unix seconds, from the same observation.
    if 'book_observed_at' in evidence and 'evaluated_at' in evidence:
        evidence['book_age_seconds'] = round(evidence['evaluated_at'] - evidence['book_observed_at'], 3)
    samples = guard.get('recovery_valid_samples')
    if type(samples) is int and 0 <= samples <= 2:
        evidence['recovery_valid_samples'] = samples
    return evidence


def record_guards(connection, bot_id, session, previous, status, version):
    """Only confirmed changes are events; no per-tick book snapshots are stored."""
    old = previous.get("mm_guard_observation") or {}
    status["mm_guard_observation"] = old
    observed = status.get("mm_session") or {}
    if not session or (observed.get("id"), observed.get("generation")) != (session["id"], session["generation"]):
        return
    signature = {}
    observations = {}
    if status.get("phase") is not None:
        signature["all"] = dict(phase=status["phase"],
                                runtime_reason=(status.get("availability") or {}).get("reason_code"),
                                drawdown_reason=(status.get("mm_drawdown") or {}).get("reason_code"),
                                error=status.get("error"))
    for market in status.get("mm_markets", []):
        name = market.get("market")
        if name not in ("spot", "perpetual") or type(market.get("ready")) is not bool:
            continue
        guard = market.get("quote_guard") or {}
        observations[name] = guard
        signature[name] = dict(ready=market["ready"], reason=market.get("reason"),
                               guard_reason=guard.get("reason_code"), state=(guard.get("bootstrap") or {}).get("state"),
                               runtime_reason=(status.get("availability") or {}).get("reason_code"),
                               drawdown_reason=(status.get("mm_drawdown") or {}).get("reason_code"),
                               inventory_reason=(market.get("inventory_skew") or {}).get("reason_code"),
                               spread_state=(market.get("spread_policy") or {}).get("state"),
                               defense_state=((market.get('spread_policy') or {}).get('defense') or {}).get('state'),
                               outflow_buy_allowed=(status.get("spot_outflow") or {}).get("buy_allowed") if name == "spot" else None)
    sequence = old.get("sequence", 0)
    status["mm_guard_observation"] = old
    if not signature:
        return
    key = [session["id"], session["generation"]]
    prior = old.get("markets", {}) if old.get("identity") == key else {}
    if prior == signature:
        return
    sequence += 1
    with connection.begin_nested():
        for market, value in signature.items():
            if prior.get(market) != value:
                payload = dict(market=market, previous=prior.get(market), current=value)
                # Evidence can vary with every book update. Keep it out of the
                # signature and history cursor so only transitions write rows.
                if market in observations and (evidence := _guard_diagnostics(observations[market])):
                    payload['diagnostics'] = evidence
                append_event(connection, bot_id=bot_id, session=session, event_type="guard_changed", version=version,
                             source="observer", payload=payload,
                             identity=[session["generation"], sequence, market])
    status["mm_guard_observation"] = dict(identity=key, sequence=sequence, markets=signature)


def session_events(store, session_id, *, limit=100, cursor=None, event_type=None, source=None):
    from hummingbot.model.admin_store import _cursor, _cursor_values
    if (not isinstance(session_id, str) or not 1 <= len(session_id) <= 128 or type(limit) is not int or not 1 <= limit <= 500
            or event_type is not None and (not isinstance(event_type, str) or len(event_type) > 64)
            or source is not None and source not in ("admin_api", "scheduler", "observer", "cli", "control")):
        raise ValueError("Invalid session event filters")
    scope = dict(bot_id=store.bot_id, session_id=session_id, event_type=event_type, source=source)
    point = _cursor_values(cursor, "mm_events", scope, 1)
    conditions = [MMEvent.bot_id == store.bot_id, MMEvent.session_id == session_id]
    with store.engine.connect() as connection:
        complete = connection.execute(select(MMEvent.id).where(*conditions, MMEvent.event_type == "session_created").limit(1)).first() is not None
        query = select(MMEvent).where(*conditions)
        if event_type is not None:
            query = query.where(MMEvent.event_type == event_type)
        if source is not None:
            query = query.where(MMEvent.source == source)
        if point:
            query = query.where(MMEvent.id < point[0])
        rows = connection.execute(query.order_by(MMEvent.id.desc()).limit(limit + 1)).mappings().all()
    selected = [dict(row) for row in rows[:limit]]
    return dict(session_id=session_id, history_complete=complete, coverage="recorded_transitions_since_migration",
                items=selected, next_cursor=_cursor("mm_events", scope, [selected[-1]["id"]]) if len(rows) > limit else None)
