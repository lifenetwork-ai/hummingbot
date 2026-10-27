"""Console-only service logging using Cloud Logging's structured fields."""
import json
import logging
import os
import sys
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit

from hummingbot.client.config.defaults import DEFAULT_ENV_CONFIG
from hummingbot.client.config.runtime_config import RuntimeConfigurationError
from hummingbot.logger import HummingbotLogger


class ServiceFormatter(logging.Formatter):
    def __init__(self, style, values, host=None):
        super().__init__()
        self.style = style
        self.host = host
        # Redact known configured credentials, including decoded DSN passwords.
        # This cannot identify arbitrary secrets invented inside upstream messages.
        secrets = {value for key, value in values.items() if value and any(
            token in key.upper() for token in ("PASSWORD", "PASSPHRASE", "SECRET", "API_KEY", "DATABASE_URL"))}
        try:
            password = urlsplit(values.get("DATABASE_URL", "")).password
            if password:
                secrets.update((password, unquote(password)))
        except ValueError:
            pass
        self.secrets = sorted((value for value in secrets if len(value) >= 4), key=len, reverse=True)

    def redact(self, value):
        for secret in self.secrets:
            value = value.replace(secret, "[REDACTED]")
        return value

    def format(self, record):
        # EVENT/METRIC numeric levels are admitted only with DEBUG. NETWORK is
        # promoted at its call site, before logger-level filtering can drop it.
        severity = ("CRITICAL" if record.levelno >= logging.CRITICAL else
                    "ERROR" if record.levelno >= logging.ERROR else
                    "WARNING" if record.levelno >= logging.WARNING else
                    "INFO" if record.levelno >= logging.INFO else "DEBUG")
        host = self.host
        owner = getattr(host, "owner", None)
        payload = dict(
            timestamp=datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            severity=severity, message=record.getMessage(), logger=record.name,
            bot_id=getattr(getattr(host, "settings", None), "bot_id", os.environ.get("BOT_ID", DEFAULT_ENV_CONFIG["BOT_ID"])),
            config=getattr(owner, "config_file_path", None) or "-",
            owner_uuid=getattr(owner, "owner_uuid", None) or "-",
            owner_generation=getattr(owner, "generation", None) or 0,
            revision=getattr(host, "revision", None) or os.environ.get("K_REVISION") or os.environ.get("COMMIT_SHA") or "local")
        for name in ("event", "reason_code", "scope", "session_id", "command_id", "error_type"):
            value = getattr(record, name, None)
            if isinstance(value, (str, int, bool)):
                payload[name] = value
        session_id = getattr(getattr(host, "mm_session", None), "id", None)
        if isinstance(session_id, str) and "session_id" not in payload:
            payload["session_id"] = session_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        if self.style == "json":
            # Redact before JSON escaping, including secrets containing newlines.
            payload = {key: self.redact(value) if isinstance(value, str) else value for key, value in payload.items()}
            return json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        text = (f"{payload['timestamp']} {severity} {record.name} bot={payload['bot_id']} "
                f"config={payload['config']} owner={payload['owner_uuid']} generation={payload['owner_generation']} "
                f"revision={payload['revision']} {payload['message']}")
        if "exception" in payload:
            text += "\n" + payload["exception"]
        if "stack" in payload:
            text += "\n" + payload["stack"]
        return self.redact(text)


def configure_service_logging(host=None, *, environ=None, reset=False, level_override=None):
    values = os.environ if environ is None else environ
    level = (level_override or values.get("LOG_LEVEL", DEFAULT_ENV_CONFIG["LOG_LEVEL"])).strip().upper()
    style = values.get("LOG_FORMAT", DEFAULT_ENV_CONFIG["LOG_FORMAT"]).strip().lower()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise RuntimeConfigurationError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR or CRITICAL.")
    if style not in ("json", "text"):
        raise RuntimeConfigurationError("LOG_FORMAT must be json or text.")
    root = logging.getLogger()
    if reset or not root.handlers:
        logging.basicConfig(stream=sys.stdout, force=True)
    root.setLevel(level)
    logging.raiseExceptions = False
    # SQLAlchemy uses effective logger levels to decide whether to render SQL
    # and bound values even when engine.echo=False. Keep those out of DEBUG too.
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
    logging.captureWarnings(True)
    HummingbotLogger.network_log_level = logging.WARNING
    formatter = ServiceFormatter(style, values, host)
    for handler in root.handlers:
        # Propagating child loggers bypass the root logger's threshold.
        handler.setLevel(level)
        handler.setFormatter(formatter)
