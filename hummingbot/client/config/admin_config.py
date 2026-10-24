"""Admin HTTP settings loaded through the existing dotenv/environment bootstrap."""
import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from hummingbot.client.config.defaults import DEFAULT_ENV_CONFIG
from hummingbot.client.config.runtime_config import RuntimeConfigurationError, parse_boolean


@dataclass(frozen=True)
class AdminSettings:
    enabled: bool = False
    username: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    cors_origins: tuple = ("*",)
    reporting_timezone: str = "Asia/Ho_Chi_Minh"

    @classmethod
    def from_environment(cls, environ=None):
        values = os.environ if environ is None else environ
        enabled = parse_boolean(values.get("ADMIN_API_ENABLED", DEFAULT_ENV_CONFIG["ADMIN_API_ENABLED"]), "ADMIN_API_ENABLED")
        username, password = values.get("ADMIN_API_USERNAME", ""), values.get("ADMIN_API_PASSWORD", "")
        if enabled and (not username.strip() or not password.strip() or ":" in username
                        or any(character in username + password for character in "\r\n")):
            raise RuntimeConfigurationError("Enabled admin API requires valid ADMIN_API_USERNAME and ADMIN_API_PASSWORD.")
        origins = []
        for origin in values.get("ADMIN_CORS_ORIGINS", DEFAULT_ENV_CONFIG["ADMIN_CORS_ORIGINS"]).split(","):
            if not origin.strip():
                continue
            if origin.strip() == "*":
                origins.append("*")
                continue
            try:
                parsed = urlsplit(origin.strip())
                valid = (parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username
                         and parsed.path in ("", "/") and not parsed.query and not parsed.fragment and "*" not in origin)
                parsed.port
            except ValueError:
                valid = False
            if not valid:
                raise RuntimeConfigurationError("ADMIN_CORS_ORIGINS must contain HTTP(S) origins or *.")
            origins.append(origin.strip().rstrip("/"))
        timezone = values.get("REPORTING_TIMEZONE", DEFAULT_ENV_CONFIG["REPORTING_TIMEZONE"])
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise RuntimeConfigurationError("REPORTING_TIMEZONE must name a supported IANA timezone.") from None
        return cls(enabled, username, password, ("*",) if "*" in origins else tuple(dict.fromkeys(origins)), timezone)
