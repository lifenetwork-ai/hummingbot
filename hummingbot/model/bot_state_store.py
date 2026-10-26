"""Small operational-state adapter for the existing bot_state table."""
from contextlib import nullcontext
from typing import Optional

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine

from hummingbot.model.bot_state import BotState


class ControlConflict(RuntimeError):
    pass


class BotStateStore:
    def __init__(self, engine: Engine, bot_id: str):
        self.engine = engine
        self.bot_id = bot_id

    def read(self) -> Optional[dict]:
        with self.engine.connect() as connection:
            row = connection.execute(text("""
                SELECT *, EXTRACT(EPOCH FROM (clock_timestamp() - heartbeat)) AS heartbeat_age_s,
                    EXTRACT(EPOCH FROM clock_timestamp()) AS database_now
                FROM public.bot_state WHERE bot_id=:bot_id
            """), {"bot_id": self.bot_id}).mappings().first()
            return dict(row) if row else None

    def ensure(self) -> None:
        with self.engine.begin() as connection:
            # Serialize identity creation as well as checking it; otherwise two
            # cold starts with different BOT_IDs could both observe an empty DB.
            connection.execute(text("SELECT pg_advisory_xact_lock(5215808413150596171)"))
            other = connection.execute(select(BotState.bot_id).where(BotState.bot_id != self.bot_id).limit(1)).first()
            if other:
                raise ControlConflict("This database already belongs to another BOT_ID; use its configured identity.")
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
            if desired_state == "running" and config_file_path is None:
                conditions.append(BotState.config_file_path.isnot(None))
            row = connection.execute(select(BotState.mm_session).where(*conditions)
                                     .with_for_update()).mappings().first()
            if row is None:
                raise ControlConflict("Control version changed; read status and submit the intended command again.")
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
            result = connection.execute(update(BotState).where(*conditions).values(**values)
                                        .returning(BotState.control_version)).scalar()
            if result is None:
                raise ControlConflict("Control version changed; read status and submit the intended command again.")
            return result

    def publish(self, *, owner_uuid: str, owner_generation: int, version: int, status: dict,
                revision: Optional[str] = None, save_balances: bool = True) -> bool:
        """An old process/command cannot acknowledge newer control intent."""
        status.pop("daily_snapshot_error", None)
        with self.engine.begin() as connection:
            result = connection.execute(update(BotState).where(
                BotState.bot_id == self.bot_id, BotState.owner_uuid == owner_uuid,
                BotState.owner_generation == owner_generation, BotState.control_version == version,
                BotState.acknowledged_version <= version,
            ).values(acknowledged_version=version, heartbeat=text("clock_timestamp()"), status=status,
                     revision=revision).returning(BotState.mm_session, BotState.desired_state)).mappings().first()
            if result is None:
                return False
            from hummingbot.model.admin_store import AdminStore
            AdminStore.record_observation(connection, self.bot_id, version, status, result)
            from sqlalchemy.exc import DataError, IntegrityError

            from hummingbot.model.admin_reporting import save_daily_balances
            try:
                # A malformed optional observation must not roll back ownership,
                # heartbeat or a truthful command outcome. Connection/commit
                # failures remain fatal and are deliberately not caught here.
                if save_balances and not status.get("reporting_unavailable"):
                    with connection.begin_nested():
                        save_daily_balances(connection, (status.get("reporting") or {}).get("accounts", []),
                                            status.get("reporting_timezone", "Asia/Ho_Chi_Minh"))
            except (ValueError, TypeError, KeyError, OverflowError, DataError, IntegrityError) as error:
                status["daily_snapshot_error"] = type(error).__name__
                connection.execute(update(BotState).where(BotState.bot_id == self.bot_id).values(status=status))
            return True

    def request_mm_session(self, *, action, expected_version, catalogue=None, request=None,
                           current_session_id=None, rng=None, connection=None):
        from random import SystemRandom

        from hummingbot.core.mm_session import MMSession, MMSessionRequest
        if action not in ("start", "switch", "stop") or type(expected_version) is not int or expected_version < 0:
            raise ValueError("Invalid MM action or control version")
        if action != "stop" and (catalogue is None or request is None):
            raise ValueError("Start/switch require a catalogue and session request")
        with (self.engine.begin() if connection is None else nullcontext(connection)) as connection:
            row = connection.execute(select(BotState).where(BotState.bot_id == self.bot_id).with_for_update()).mappings().first()
            if row is None or row["control_version"] != expected_version:
                raise ControlConflict("Control version changed")
            current = MMSession.model_validate(row["mm_session"]) if row["mm_session"] else None
            if action == "start":
                if row["desired_state"] == "running" or (current and current.status in ("preparing", "running", "stopping")):
                    raise ControlConflict("An active strategy/session must be stopped or explicitly switched")
            elif current is None or current.id != current_session_id:
                raise ControlConflict("Current MM session identity changed")
            if action != "stop" and (row["recovery_state"] or {}).get("halt"):
                raise ControlConflict("Recovery HALT must be resolved before starting another session")
            if action == "stop":
                current.status = "stopping"
                current.generation += 1
                effective = current
            else:
                if action == "switch" and current.status not in ("preparing", "running"):
                    raise ControlConflict("Only an active MM session can be switched")
                effective = MMSession.create(catalogue, MMSessionRequest.model_validate(request),
                                             rng=rng or SystemRandom())
            values = {"control_version": expected_version + 1, "mm_session": effective.model_dump(mode="json"),
                      "desired_state": "stopped" if action == "stop" else "running"}
            if action != "stop":
                values.update(config_type="mm-session", config_file_path="life_mm_profiles.yaml")
            connection.execute(update(BotState).where(BotState.bot_id == self.bot_id).values(**values))
            return {"control_version": expected_version + 1, "session_id": effective.id, "generation": effective.generation}

    def update_mm_session(self, *, owner_uuid, owner_generation, version, session_id, generation, change):
        from hummingbot.core.mm_session import MMSession
        with self.engine.begin() as connection:
            row = connection.execute(select(BotState).where(
                BotState.bot_id == self.bot_id, BotState.owner_uuid == owner_uuid,
                BotState.owner_generation == owner_generation, BotState.control_version == version,
            ).with_for_update()).mappings().first()
            if row is None or not owner_uuid or not row["mm_session"]:
                raise ControlConflict("MM session owner/control version changed")
            session = MMSession.model_validate(row["mm_session"])
            if session.id != session_id or session.generation != generation:
                raise ControlConflict("MM session or random step changed")
            now = float(connection.execute(text("SELECT EXTRACT(EPOCH FROM clock_timestamp())")).scalar())
            change(session, now)
            if (row["recovery_state"] or {}).get("halt") and session.status in ("preparing", "running"):
                raise ControlConflict("Recovery HALT blocks session activation or a new draw")
            values = {"mm_session": session.model_dump(mode="json")}
            if session.status in ("stopping", "stopped", "expired"):
                values["desired_state"] = "stopped"
            connection.execute(update(BotState).where(BotState.bot_id == self.bot_id).values(**values))
            return session
