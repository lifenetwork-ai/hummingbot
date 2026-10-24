"""Shared environment bootstrap; independent of connector discovery and client YAML."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

from dotenv import load_dotenv
from sqlalchemy.engine import URL, make_url


class RuntimeConfigurationError(ValueError):
    """A deliberately redacted configuration error safe for CLI/service logs."""


def load_environment() -> None:
    """Load only the selected local file, preserving even empty process values."""
    explicit = os.environ.get("HBOT_ENV_FILE")
    if "K_SERVICE" in os.environ:
        if explicit is not None:
            raise RuntimeConfigurationError("HBOT_ENV_FILE is not supported on Cloud Run; inject environment settings.")
        return
    path = Path(explicit).expanduser() if explicit else Path(__file__).resolve().parents[3] / ".env"
    if explicit is not None and (not explicit or not path.is_file()):
        raise RuntimeConfigurationError("The explicitly selected HBOT_ENV_FILE does not exist.")
    if path.is_file():
        load_dotenv(path, override=False, interpolate=False)


def parse_boolean(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in ("true", "false"):
        raise RuntimeConfigurationError("ENABLE_AUTO_MIGRATION must be true or false.")
    return normalized == "true"


@dataclass(frozen=True)
class RuntimeSettings:
    # Neither repr nor exception text may expose connection credentials/query parameters.
    database_url: URL = field(repr=False)
    enable_auto_migration: bool = True
    bot_id: str = "hummingbot"
    port: int = 8080

    @classmethod
    def from_environment(cls, environ: Optional[Mapping[str, str]] = None) -> "RuntimeSettings":
        values = os.environ if environ is None else environ
        raw = values.get("DATABASE_URL", "")
        try:
            url = make_url(raw)
            if url.drivername == "postgresql":
                url = url.set(drivername="postgresql+psycopg2")
            if url.drivername != "postgresql+psycopg2" or not url.database:
                raise ValueError
            # Accessing port validates it; URL parsing preserves percent-escaped credentials.
            url.port
        except Exception:
            raise RuntimeConfigurationError("DATABASE_URL must be a valid postgresql+psycopg2 URL with a database.") from None
        try:
            port = int(values.get("PORT", "8080"))
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            raise RuntimeConfigurationError("PORT must be an integer between 1 and 65535.") from None
        bot_id = values.get("BOT_ID", "hummingbot")
        if not bot_id.strip() or len(bot_id) > 128:
            raise RuntimeConfigurationError("BOT_ID must contain 1–128 characters.")
        return cls(url, parse_boolean(values.get("ENABLE_AUTO_MIGRATION", "true")), bot_id, port)


def postgres_requested() -> bool:
    """Presence, including an empty value, selects PG and must never fall back to SQLite."""
    return "DATABASE_URL" in os.environ or "K_SERVICE" in os.environ
