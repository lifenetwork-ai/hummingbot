"""Small operational-state adapter for the existing bot_state table."""
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
                SELECT *, EXTRACT(EPOCH FROM (clock_timestamp() - heartbeat)) AS heartbeat_age_s
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
        with self.engine.begin() as connection:
            conditions = [BotState.bot_id == self.bot_id, BotState.control_version == expected_version]
            if desired_state == "running" and config_file_path is None:
                conditions.append(BotState.config_file_path.isnot(None))
            result = connection.execute(update(BotState).where(*conditions).values(**values)
                                        .returning(BotState.control_version)).scalar()
            if result is None:
                raise ControlConflict("Control version changed; read status and submit the intended command again.")
            return result

    def publish(self, *, owner_uuid: str, owner_generation: int, version: int, status: dict,
                revision: Optional[str] = None) -> bool:
        """An old process/command cannot acknowledge newer control intent."""
        with self.engine.begin() as connection:
            result = connection.execute(update(BotState).where(
                BotState.bot_id == self.bot_id, BotState.owner_uuid == owner_uuid,
                BotState.owner_generation == owner_generation, BotState.control_version == version,
                BotState.acknowledged_version <= version,
            ).values(acknowledged_version=version, heartbeat=text("clock_timestamp()"), status=status,
                     revision=revision))
            return result.rowcount == 1
