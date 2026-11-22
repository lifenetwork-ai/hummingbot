"""Small operational-state adapter for the existing bot_state table."""
import base64
import json
import zlib
from contextlib import ExitStack, nullcontext
from functools import partial
from threading import Lock
from typing import Optional
from uuid import uuid4

from sqlalchemy import Text, bindparam, case, cast, func, literal_column, select, text, update
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from hummingbot.core.control_errors import ControlConflict
from hummingbot.core.engine_diagnostics import trace_runtime
from hummingbot.model.bot_state import BotLiquidityCheckpoint, BotState

_COVERAGE_BUCKETS = '_coverage_buckets'
_CHECKPOINT_FIELDS = frozenset((_COVERAGE_BUCKETS, '_coverage_history', '_overview_recovery'))
_CHECKPOINT_REF = '_liquidity_checkpoint_ref'
_PACKED_HISTORY = 'history_zlib_v1'
_EMPTY_JSON_OBJECT = literal_column("'{}'::jsonb", type_=JSONB)


def _coverage_changes(previous, current, signatures):
    """Send only changed seconds when the complete published basis is still present."""
    changes = {}
    for market, rows in current.items():
        old = previous.get(market, {})
        deleted = list(old.keys() - rows.keys())
        written = {second: row for second, row in rows.items()
                   if signatures[market][second] != old.get(second)}
        changes[market] = deleted, written
    if sum(len(deleted) + len(written) for deleted, written in changes.values()) < sum(map(len, current.values())):
        return changes


def _coverage_buckets(checkpoint):
    buckets = checkpoint.get(_COVERAGE_BUCKETS)
    if (isinstance(buckets, dict) and buckets.keys() <= {'spot', 'perpetual'}
            and all(isinstance(rows, dict) and all(isinstance(second, str) for second in rows)
                    for rows in buckets.values())):
        return buckets


def _pack_history(checkpoint, serialize):
    history = {key: value for key, value in checkpoint.items() if key != _COVERAGE_BUCKETS}
    if history:
        raw = serialize(history).encode('utf-8')
        packed = base64.b64encode(zlib.compress(raw, level=1))
        if len(packed) + len(_PACKED_HISTORY) + 5 < len(raw):
            return {**{key: value for key, value in checkpoint.items() if key == _COVERAGE_BUCKETS},
                    _PACKED_HISTORY: packed.decode('ascii')}
    return checkpoint


def _merge_coverage(value, changes, serialize):
    markets = []
    prior = BotLiquidityCheckpoint.payload[_COVERAGE_BUCKETS]
    for market, (deleted, written) in changes.items():
        rows = func.coalesce(prior[market], _EMPTY_JSON_OBJECT)
        if deleted:
            rows = rows.op('-')(bindparam(f'deleted_{market}', deleted, type_=ARRAY(Text)))
        if written:
            rows = rows.op('||')(cast(bindparam(f'written_{market}', serialize(written)), JSONB))
        markets.extend((market, rows))
    return func.jsonb_set(value, [_COVERAGE_BUCKETS], func.jsonb_build_object(*markets))


def _optional_statement_timeout(error):
    # These failures leave the physical session usable after SAVEPOINT rollback.
    # A lost/invalidated connection or another operational failure stays fatal.
    return (not error.connection_invalidated
            and getattr(error.orig, "pgcode", None) in {"55P03", "57014"})


def _overview_policy_projection():
    """Bounded pinned fields for reporting; never transfer the archived catalogue."""
    markets = []
    for name in ('spot', 'perpetual'):
        config = f"mm_session->'catalogue'->'markets'->'{name}'"
        guard = f"{config}->'quote_guards'"
        markets.append(
            f"'{name}', CASE WHEN jsonb_typeof({config})='object' THEN jsonb_build_object("
            f"'id', {config}->'id', 'total_amount_quote', {config}->'total_amount_quote', "
            f"'quote_guards', jsonb_build_object('enabled', {guard}->'enabled', "
            f"'mode', {guard}->'mode', 'max_deviation_bps', {guard}->'max_deviation_bps')) ELSE NULL END")
    return ("jsonb_build_object('session_id', mm_session->'id', 'generation', mm_session->'generation', "
            "'markets', jsonb_strip_nulls(jsonb_build_object(" + ', '.join(markets)
            + "))) AS overview_session_policy")


class BotStateStore:
    def __init__(self, engine: Engine, bot_id: str, *, checkpoint_schema_pending=False):
        self.engine = engine
        self.bot_id = bot_id
        self.checkpoint_schema_pending = checkpoint_schema_pending
        self._published_coverage = None
        self.publication_fence = Lock()

    def read(self, *, publication: bool = False) -> Optional[dict]:
        # Public reads can skip the checkpoint table. Strip inlined histories
        # too, since a database can still hold a legacy publication.
        from hummingbot.core.halt_diagnostics import publication_halt_fields
        source = 'public.bot_state b'
        columns = ("bot_id, desired_state, control_version, acknowledged_version, owner_uuid, owner_generation, "
                   "heartbeat, CASE WHEN jsonb_typeof(status->'mm_liquidity')='object' THEN "
                   f"jsonb_set(status - '{_CHECKPOINT_REF}', '{{mm_liquidity}}', (status->'mm_liquidity') - "
                   "ARRAY['_coverage_history', '_coverage_buckets', '_overview_recovery']) ELSE status END AS status, "
                   "revision, jsonb_build_object('halted', recovery_state->'halt' = 'true'::jsonb, "
                   + publication_halt_fields() +
                   "'shutdown_pending', COALESCE(recovery_state->'halt' = 'true'::jsonb "
                   "AND recovery_state->>'halt_kind'='shutdown_pending' "
                   "AND COALESCE(recovery_state->>'reason', '')='', false), "
                   "'credential_operation_pending', COALESCE(jsonb_typeof(recovery_state->'credential_operation')='object', false), "
                   "'recovery_operation_pending', COALESCE(jsonb_typeof(recovery_state->'recovery_operation')='object', false), "
                   "'pending_mutations', COALESCE(recovery_state->'pending_mutations', '[]'::jsonb) "
                   "NOT IN ('[]'::jsonb, '{}'::jsonb, 'null'::jsonb), "
                   "'automatic_recovery', recovery_state->'automatic_recovery') AS recovery_summary, "
                   "CASE WHEN jsonb_typeof(mm_session)='object' "
                   "THEN mm_session - 'catalogue' ELSE mm_session END AS mm_session, "
                   + _overview_policy_projection())
        checkpoint_join = (" LEFT JOIN public.bot_liquidity_checkpoints p ON p.bot_id=b.bot_id "
                           f"AND p.token=b.status->>'{_CHECKPOINT_REF}'")
        with self.engine.connect() as connection:
            def read_row(projection, tables):
                return connection.execute(text(f"""
                    SELECT {projection}, EXTRACT(EPOCH FROM (clock_timestamp() - b.heartbeat)) AS heartbeat_age_s,
                        EXTRACT(EPOCH FROM clock_timestamp()) AS database_now
                    FROM {tables} WHERE b.bot_id=:bot_id
                """), {"bot_id": self.bot_id}).mappings().first()

            if publication:
                row = read_row(columns, source)
            else:
                # The pending schema can only publish inline snapshots. If an
                # owner starts publishing references, the table is now visible.
                row = read_row('b.*', source) if self.checkpoint_schema_pending else None
                status = row['status'] if row else None
                if not self.checkpoint_schema_pending or (isinstance(status, dict) and status.get(_CHECKPOINT_REF)):
                    row = read_row('b.*, p.payload AS checkpoint_payload', source + checkpoint_join)
                    self.checkpoint_schema_pending = False
        if row is None:
            return None
        result = dict(row)
        checkpoint = result.pop('checkpoint_payload', None)
        if not publication and isinstance(result['status'], dict):
            status = result['status']
            status.pop(_CHECKPOINT_REF, None)
            if checkpoint:
                encoded = checkpoint.pop(_PACKED_HISTORY, None)
                if encoded is not None:
                    checkpoint.update(json.loads(zlib.decompress(base64.b64decode(encoded, validate=True))))
                status['mm_liquidity'].update(checkpoint)
        return result

    def ensure(self) -> None:
        with self.engine.begin() as connection:
            # Serialize identity creation as well as checking it; otherwise two
            # cold starts with different BOT_IDs could both observe an empty DB.
            connection.execute(text("SELECT pg_advisory_xact_lock(5215808413150596171)"))
            other = connection.execute(select(BotState.bot_id).where(BotState.bot_id != self.bot_id).limit(1)).first()
            if other:
                raise ControlConflict("This database already belongs to another BOT_ID; use its configured identity.",
                                      code="bot_identity_mismatch")
            connection.execute(insert(BotState).values(bot_id=self.bot_id).on_conflict_do_nothing())

    def control(self, *, expected_version: int, desired_state: Optional[str] = None,
                config_file_path: Optional[str] = None, config_type: Optional[str] = None) -> int:
        if not isinstance(expected_version, int) or expected_version < 0:
            raise ValueError("Control version must be a non-negative integer.")
        values = {"control_version": expected_version + 1}
        if desired_state is not None:
            if desired_state not in ("running", "stopped"):
                raise ValueError("Invalid desired state.")
            values["desired_state"] = desired_state
        if config_file_path is not None:
            if not config_file_path or config_type not in ("v1-strategy", "v2-script", "controller"):
                raise ValueError("A configuration requires a valid file identity and type.")
            values.update(config_file_path=config_file_path, config_type=config_type)
        if desired_state == "stopped" and config_file_path is None:
            # A cold CLI can spend seconds importing model dependencies. Load
            # them before locking the row needed by the owner's heartbeat.
            from hummingbot.core.mm_session import MMSession
        with self.engine.begin() as connection:
            conditions = [BotState.bot_id == self.bot_id, BotState.control_version == expected_version]
            row = connection.execute(select(BotState.mm_session, BotState.recovery_state, BotState.config_file_path).where(*conditions)
                                     .with_for_update()).mappings().first()
            if row is None:
                raise ControlConflict("Control version changed; read status and submit the intended command again.")
            if (row["recovery_state"] or {}).get("credential_operation"):
                raise ControlConflict("Wait for the connection operation to complete", code="credential_operation_in_progress")
            if (row["recovery_state"] or {}).get("recovery_operation"):
                raise ControlConflict("Wait for the recovery operation", code="recovery_operation_in_progress")
            if desired_state == 'running' and ((row['recovery_state'] or {}).get('automatic_recovery') or {}).get('phase') in (
                    'reconciling', 'bootstrapping_recovery', 'waiting_for_readiness'):
                raise ControlConflict('Wait for exchange order reconciliation', code='automatic_recovery_in_progress')
            if desired_state == "running" and config_file_path is None:
                if row["config_file_path"] is None:
                    raise ControlConflict("A deployed configuration must be selected before starting.")
            bound = (row["recovery_state"] or {}).get("strategy_checkpoints", {}).get("life_mm_profiles.yaml", {}).get("mm_drawdown_policy")
            if bound is not None and desired_state != "stopped":
                raise ControlConflict("Armed MM drawdown requires admin MM commands; policy/scope changes need operator review.")
            if any((row["recovery_state"] or {}).get(key) for key in ("spot_outflow", "listing_bootstrap")) and desired_state != "stopped":
                raise ControlConflict("Armed spot_outflow requires admin MM commands and its bound policy.")
            if ((row['recovery_state'] or {}).get('volatility_defense') or {}).get('pauses') and desired_state != 'stopped':
                raise ControlConflict('Active volatility pauses require admin MM commands and their bound YAML policy.',
                                      code='volatility_pause_policy_bound')
            if config_file_path is not None:
                # An explicit legacy YAML selection replaces the MM intent.
                # Its former session must not keep future admin starts blocked.
                values["mm_session"] = None
            elif desired_state == "stopped" and row["mm_session"]:
                session = MMSession.model_validate(row["mm_session"])
                if session.status in ("preparing", "running"):
                    # The CLI and admin API share the same stop handshake:
                    # revoke this generation now; the owner confirms cleanup.
                    session.status = "stopping"
                    session.generation += 1
                    values["mm_session"] = session.model_dump(mode="json")
            from hummingbot.model.mm_event import record_transition
            record_transition(connection, self.bot_id, row["mm_session"], values.get("mm_session", row["mm_session"]),
                              expected_version + 1, source="cli")
            result = connection.execute(update(BotState).where(*conditions).values(**values)
                                        .returning(BotState.control_version)).scalar()
            if result is None:
                raise ControlConflict("Control version changed; read status and submit the intended command again.")
            return result

    @trace_runtime('service.checkpoint_write')
    def _write_liquidity_checkpoint(self, connection, token, previous_token, payload, changes, serialize):
        value = cast(bindparam('liquidity_checkpoint_payload', payload), JSONB)
        if changes is not None:
            statement = update(BotLiquidityCheckpoint).where(
                BotLiquidityCheckpoint.bot_id == self.bot_id, BotLiquidityCheckpoint.token == previous_token,
            ).values(token=token, payload=_merge_coverage(value, changes, serialize))
        else:
            insert_checkpoint = insert(BotLiquidityCheckpoint).values(bot_id=self.bot_id, token=token, payload=value)
            statement = insert_checkpoint.on_conflict_do_update(
                index_elements=[BotLiquidityCheckpoint.bot_id],
                set_=dict(token=token, payload=insert_checkpoint.excluded.payload))
        return connection.execute(statement.returning(BotLiquidityCheckpoint.bot_id)).first() is not None

    @trace_runtime('service.publication')
    def publish(self, *, owner_uuid: str, owner_generation: int, version: int, status: dict,
                revision: Optional[str] = None, save_balances: bool = True) -> bool:
        """Atomically publish only fresh authority; yield to financial writers.

        Optional projections run before the authority row is locked. Their
        writes remain speculative until a fresh, nonwaiting fence commits the
        status and receipt together. False also means contention: keep the old
        heartbeat and let the existing observer retry on its next cycle.
        """
        from sqlalchemy.exc import DataError, IntegrityError

        from hummingbot.model.account_balance_changes import save_session_cash_baseline
        from hummingbot.model.admin_reporting import save_daily_balances
        from hummingbot.model.admin_store import AdminStore
        from hummingbot.model.mm_event import record_guards

        status.pop("daily_snapshot_error", None)
        status.pop("mm_history_error", None)
        # JSON adaptation may be expensive for a complete restart checkpoint.
        # Do it before taking the authority row lock used by financial writers
        # and Stop. Guard/error metadata is validated under the final row lock.
        custom_serializer = self.engine.dialect._json_serializer
        serialize = custom_serializer or partial(json.dumps, separators=(',', ':'), allow_nan=False)
        cached = self._published_coverage
        frame = status.get('mm_liquidity')
        checkpoint = ({key: value for key, value in frame.items() if key in _CHECKPOINT_FIELDS}
                      if isinstance(frame, dict) else {})
        token = uuid4().hex if checkpoint else None
        base_status = {key: value for key, value in status.items() if key != _CHECKPOINT_REF}
        if checkpoint:
            base_status['mm_liquidity'] = {key: value for key, value in frame.items() if key not in _CHECKPOINT_FIELDS}
            base_status[_CHECKPOINT_REF] = token
        prepared_status = serialize(base_status)
        if custom_serializer is None:
            checkpoint = _pack_history(checkpoint, serialize)
        buckets = _coverage_buckets(checkpoint) if custom_serializer is None else None
        # Detach the comparison basis from the caller's lists. JSON scalar
        # types matter, including when Python equates True and 1.
        signatures = ({market: {second: repr(row) for second, row in rows.items()} for market, rows in buckets.items()}
                      if buckets is not None else None)
        changes = _coverage_changes(cached[1], buckets, signatures) if cached is not None and signatures is not None else None
        with ExitStack() as fence_scope, self.engine.connect() as connection, connection.begin() as transaction:
            # Guard history needs its previous observation, not private telemetry
            # histories. Keep malformed legacy status behavior and the full
            # session, row lock and authority predicates; the new snapshot below
            # is still written in full for restart/checkpoint recovery.
            guard_status = case((func.jsonb_typeof(BotState.status) == 'object',
                                 func.jsonb_build_object('mm_guard_observation',
                                                         BotState.status['mm_guard_observation'])),
                                else_=BotState.status).label('status')
            authority = (
                BotState.bot_id == self.bot_id, BotState.owner_uuid == owner_uuid,
                BotState.owner_generation == owner_generation, BotState.control_version == version,
                BotState.acknowledged_version <= version)
            halt_basis = BotState.recovery_state['halt']
            reference = BotState.status[_CHECKPOINT_REF].astext
            statement = select(guard_status, BotState.mm_session, BotState.desired_state,
                               halt_basis.label('halt_basis'), reference.label('checkpoint_ref')).where(*authority)
            previous = connection.execute(statement).mappings().first()
            if previous is None or (previous['halt_basis'] is True and status.get('running') is True):
                return False

            def same_json(column, value, name):
                # Driver None represents both SQL NULL and JSON null. The
                # optimistic basis treats those like the detached Python read.
                if value is None:
                    return column.is_(None) | (column == cast(text("'null'"), JSONB))
                return column.is_not_distinct_from(cast(bindparam(name, serialize(value)), JSONB))

            # Encode comparisons before history can acquire an implicit FK lock.
            # The final fence returns no large JSON while holding authority.
            fence = select(BotState.bot_id).where(
                *authority,
                BotState.desired_state == previous['desired_state'],
                same_json(halt_basis, previous['halt_basis'], 'publication_halt_basis'),
                same_json(BotState.mm_session, previous['mm_session'], 'publication_session_basis'),
                same_json(guard_status, previous['status'], 'publication_guard_basis'))
            previous_token = previous['checkpoint_ref']
            fence = fence.where(reference.is_not_distinct_from(previous_token))
            if changes is not None and cached[0] != previous_token:
                changes = None
            # Receipt and balance SQL may wait on their own tables. Never hold
            # the row needed by the recorder's FOR SHARE while doing that work.
            AdminStore.record_observation(connection, self.bot_id, version, status, previous)
            try:
                if save_balances and not status.get("reporting_unavailable"):
                    with connection.begin_nested():
                        save_daily_balances(connection, (status.get("reporting") or {}).get("accounts", []),
                                            status.get("reporting_timezone", "Asia/Ho_Chi_Minh"))
                        save_session_cash_baseline(connection, self.bot_id, previous["mm_session"], status)
            except (ValueError, TypeError, KeyError, OverflowError, DataError, IntegrityError, OperationalError) as error:
                if isinstance(error, OperationalError) and not _optional_statement_timeout(error):
                    raise
                status["daily_snapshot_error"] = type(error).__name__

            # Write and TOAST private history on its own row before the
            # ownership fence. If the fence loses, roll back both publications.
            if checkpoint:
                payload = serialize(checkpoint if changes is None else {
                    key: value for key, value in checkpoint.items() if key != _COVERAGE_BUCKETS})
                try:
                    written = self._write_liquidity_checkpoint(connection, token, previous_token, payload, changes, serialize)
                except OperationalError as error:
                    if not _optional_statement_timeout(error):
                        raise
                    written = False
                if not written:
                    transaction.rollback()
                    return False

            # Guard history inserts may take an implicit FK KEY SHARE on
            # BotState even before the final write. Keep those after the checkpoint.
            try:
                record_guards(connection, self.bot_id, previous["mm_session"], previous["status"] or {}, status, version)
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError, DataError, IntegrityError, OperationalError) as error:
                if isinstance(error, OperationalError) and not _optional_statement_timeout(error):
                    raise
                status["mm_guard_observation"] = (previous["status"] or {}).get("mm_guard_observation")
                status["mm_history_error"] = "guard_history_unavailable"

            # SKIP LOCKED avoids joining an exclusive-lock wait queue in front
            # of compatible financial readers. Recheck both authority and the
            # optimistic guard/session basis after all speculative work.
            # Update in the same statement so the row lock does not span a
            # separate client round trip before writing the heartbeat.
            metadata = {key: status[key] for key in
                        ("mm_guard_observation", "mm_history_error", "daily_snapshot_error") if key in status}
            value = cast(bindparam("prepared_status_json", prepared_status), JSONB).op("||")(
                cast(bindparam("publication_status_metadata", serialize(metadata)), JSONB))
            target = fence.with_for_update(skip_locked=True).cte('publication_target')
            # A local recorder can obtain the financial fence first. Hold
            # this slot through COMMIT, with optional SQL already completed.
            if not self.publication_fence.acquire(blocking=False):
                transaction.rollback()
                return False
            fence_scope.callback(self.publication_fence.release)
            result = connection.execute(update(BotState).where(
                BotState.bot_id == target.c.bot_id,
            ).values(acknowledged_version=version, heartbeat=text("clock_timestamp()"), status=value,
                     revision=revision).returning(BotState.bot_id)
                .execution_options(ownership_row_writer='publication')).first()
            if result is None:
                transaction.rollback()
                return False
        # A failed/ambiguous commit cannot become the basis of a later patch.
        self._published_coverage = (token, signatures) if signatures else None
        return True

    def request_mm_session(self, *, action, expected_version, catalogue=None, request=None,
                           current_session_id=None, rng=None, connection=None, principal=None, source="control"):
        from random import SystemRandom

        from hummingbot.core.mm_session import MMSession, MMSessionRequest
        if action not in ("start", "switch", "stop") or type(expected_version) is not int or expected_version < 0:
            raise ValueError("Invalid MM action or control version")
        if action != "stop" and (catalogue is None or request is None):
            raise ValueError("Start/switch require a catalogue and session request")
        with (self.engine.begin() if connection is None else nullcontext(connection)) as connection:
            row = connection.execute(select(BotState).where(BotState.bot_id == self.bot_id).with_for_update()).mappings().first()
            if row is None or row["control_version"] != expected_version:
                raise ControlConflict("Control version changed", code="control_version_changed")
            if (row["recovery_state"] or {}).get("credential_operation"):
                raise ControlConflict("Wait for the connection operation to complete", code="credential_operation_in_progress")
            if (row["recovery_state"] or {}).get("recovery_operation"):
                raise ControlConflict("Wait for the recovery operation", code="recovery_operation_in_progress")
            automatic_phase = ((row['recovery_state'] or {}).get('automatic_recovery') or {}).get('phase')
            if (action != 'stop' and automatic_phase in ('reconciling', 'bootstrapping_recovery', 'waiting_for_readiness')
                    and not (action == 'start' and row['desired_state'] == 'stopped' and automatic_phase == 'bootstrapping_recovery')):
                raise ControlConflict('Wait for exchange order reconciliation', code='automatic_recovery_in_progress')
            current = MMSession.model_validate(row["mm_session"]) if row["mm_session"] else None
            if action == "start":
                if row["desired_state"] == "running" or (current and current.status in ("preparing", "running", "stopping")):
                    raise ControlConflict("An active strategy/session must be stopped or explicitly switched", code="session_active")
            elif current is None or current.id != current_session_id:
                raise ControlConflict("Current MM session identity changed", code="session_changed")
            if action != "stop" and (row["recovery_state"] or {}).get("halt"):
                raise ControlConflict("Recovery HALT must be resolved before starting another session", code="recovery_required")
            bound = (row["recovery_state"] or {}).get("strategy_checkpoints", {}).get("life_mm_profiles.yaml", {}).get("mm_drawdown_policy")
            if action != "stop" and bound is not None and bound != catalogue.drawdown_policy():
                raise ControlConflict("Drawdown policy/scope is already bound; operator-reviewed maintenance is required to change it", code="drawdown_policy_bound")
            bootstrap = (row["recovery_state"] or {}).get("listing_bootstrap")
            if action != "stop" and bootstrap is not None and bootstrap.get("policies") != catalogue.bootstrap_policies():
                raise ControlConflict("listing_bootstrap policy is already bound; operator-reviewed maintenance is required", code="bootstrap_policy_bound")
            outflow = (row["recovery_state"] or {}).get("spot_outflow")
            if action != 'stop':
                from hummingbot.core.volatility_defense import validate_catalogue_pauses
                validate_catalogue_pauses(row['recovery_state'] or {}, catalogue)
            if action != "stop" and outflow is not None and outflow.get("policy") != catalogue.outflow_policy():
                raise ControlConflict("spot_outflow policy is already bound; operator-reviewed maintenance is required to change it", code="outflow_policy_bound")
            if action == "stop":
                current.status = "stopping"
                current.generation += 1
                effective = current
            else:
                if action == "switch" and current.status not in ("preparing", "running"):
                    raise ControlConflict("Only an active MM session can be switched", code="session_not_active")
                effective = MMSession.create(catalogue, MMSessionRequest.model_validate(request),
                                             rng=rng or SystemRandom())
            values = {"control_version": expected_version + 1, "mm_session": effective.model_dump(mode="json"),
                      "desired_state": "stopped" if action == "stop" else "running"}
            if action != "stop":
                values.update(config_type="mm-session", config_file_path="life_mm_profiles.yaml")
            from hummingbot.model.mm_event import record_transition
            record_transition(connection, self.bot_id, row["mm_session"], values["mm_session"], expected_version + 1,
                              source=source, principal=principal)
            connection.execute(update(BotState).where(BotState.bot_id == self.bot_id).values(**values))
            return {"control_version": expected_version + 1, "session_id": effective.id, "generation": effective.generation}

    def update_mm_session(self, *, owner_uuid, owner_generation, version, session_id, generation, change, risk_change=None):
        from hummingbot.core.mm_session import MMSession
        with self.engine.begin() as connection:
            locked = select(BotState.mm_session, BotState.recovery_state).where(
                BotState.bot_id == self.bot_id, BotState.owner_uuid == owner_uuid,
                BotState.owner_generation == owner_generation, BotState.control_version == version,
            ).with_for_update().cte("locked_session").prefix_with("MATERIALIZED", dialect="postgresql")
            # Evaluate the clock only after the CTE produces the locked row.
            statement = select(locked, func.extract("epoch", func.clock_timestamp()).label("database_now")).execution_options(
                ownership_row_writer='session_update')
            row = connection.execute(statement).mappings().first()
            if row is None or not owner_uuid or not row["mm_session"]:
                raise ControlConflict("MM session owner/control version changed")
            session = MMSession.model_validate(row["mm_session"])
            if session.id != session_id or session.generation != generation:
                raise ControlConflict("MM session or random step changed")
            now = float(row["database_now"])
            change(session, now)
            if (row["recovery_state"] or {}).get("halt") and session.status in ("preparing", "running"):
                raise ControlConflict("Recovery HALT blocks session activation or a new draw")
            values = {"mm_session": session.model_dump(mode="json")}
            if risk_change is not None:
                from copy import deepcopy
                recovery = deepcopy(row["recovery_state"] or {})
                risk_change(recovery, session, now)
                values["recovery_state"] = recovery
            if session.status in ("stopping", "stopped", "expired"):
                values["desired_state"] = "stopped"
            from hummingbot.model.mm_event import record_transition
            record_transition(connection, self.bot_id, row["mm_session"], values["mm_session"], version)
            connection.execute(update(BotState).where(BotState.bot_id == self.bot_id).values(**values))
            return session
