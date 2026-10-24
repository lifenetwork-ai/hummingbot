"""Shared environment bootstrap; independent of connector discovery and client YAML."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

from dotenv import load_dotenv
from sqlalchemy.engine import URL, make_url

from hummingbot.client.config.defaults import DEFAULT_ENV_CONFIG


class RuntimeConfigurationError(ValueError):
    """A deliberately redacted configuration error safe for CLI/service logs."""


def load_environment() -> None:
    """Load only the selected local file, preserving even empty process values."""
    # ENV_FILE follows the shared backend convention; retain the old name for
    # existing local installations. Presence, including empty, takes precedence.
    variable = "ENV_FILE" if "ENV_FILE" in os.environ else "HBOT_ENV_FILE"
    explicit = os.environ.get(variable)
    if "K_SERVICE" in os.environ:
        if explicit is not None:
            raise RuntimeConfigurationError(f"{variable} is not supported on Cloud Run; inject environment settings.")
        return
    path = Path(explicit).expanduser() if explicit else Path(__file__).resolve().parents[3] / DEFAULT_ENV_CONFIG["ENV_FILE"]
    if explicit is not None and (not explicit or not path.is_file()):
        raise RuntimeConfigurationError(f"The explicitly selected {variable} does not exist.")
    if path.is_file():
        load_dotenv(path, override=False, interpolate=False)


def parse_boolean(value: str, variable: str = "ENABLE_AUTO_MIGRATION") -> bool:
    normalized = value.strip().lower()
    if normalized not in ("true", "false"):
        raise RuntimeConfigurationError(f"{variable} must be true or false.")
    return normalized == "true"


@dataclass(frozen=True)
class RuntimeSettings:
    # Neither repr nor exception text may expose connection credentials/query parameters.
    database_url: URL = field(repr=False)
    enable_auto_migration: bool = DEFAULT_ENV_CONFIG["ENABLE_AUTO_MIGRATION"] == "true"
    bot_id: str = DEFAULT_ENV_CONFIG["BOT_ID"]
    port: int = int(DEFAULT_ENV_CONFIG["PORT"])

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
            port = int(values.get("PORT", DEFAULT_ENV_CONFIG["PORT"]))
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            raise RuntimeConfigurationError("PORT must be an integer between 1 and 65535.") from None
        bot_id = values.get("BOT_ID", DEFAULT_ENV_CONFIG["BOT_ID"])
        if not bot_id.strip() or len(bot_id) > 128:
            raise RuntimeConfigurationError("BOT_ID must contain 1–128 characters.")
        return cls(url, parse_boolean(values.get("ENABLE_AUTO_MIGRATION", DEFAULT_ENV_CONFIG["ENABLE_AUTO_MIGRATION"])),
                   bot_id, port)


def storage_backend(environ: Optional[Mapping[str, str]] = None) -> str:
    """Select the process storage backend without connecting or reading dotenv."""
    values = os.environ if environ is None else environ
    explicit = values.get("STORAGE_BACKEND")
    if explicit is None:
        # Preserve existing deployments, including invalid/empty DSNs: selecting
        # PG must fail validation rather than silently create local files.
        selected = "postgres" if "DATABASE_URL" in values or "K_SERVICE" in values else DEFAULT_ENV_CONFIG["STORAGE_BACKEND"]
    else:
        selected = explicit.strip().lower()
    if selected not in ("postgres", "file"):
        raise RuntimeConfigurationError("STORAGE_BACKEND must be postgres or file.")
    if "K_SERVICE" in values and selected != "postgres":
        raise RuntimeConfigurationError("Cloud Run requires STORAGE_BACKEND=postgres.")
    return selected


def postgres_requested() -> bool:
    """Explicit selection wins locally; omitted mode preserves legacy detection."""
    return storage_backend() == "postgres"
