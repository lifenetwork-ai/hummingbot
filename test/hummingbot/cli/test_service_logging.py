"""Cloud console logs must retain severity before logger-level filtering."""
import io
import json
import logging
import os
import subprocess
import sys
import warnings
from datetime import datetime
from types import SimpleNamespace

import pytest

from hummingbot.cli.service import ServiceHost
from hummingbot.cli.service_availability import ServiceAvailability
from hummingbot.logger import HummingbotLogger
from hummingbot.logger.service_logging import ServiceFormatter


@pytest.mark.parametrize('style', ['json', 'text'])
def test_security_correlation_survives_console_format_without_exporting_arbitrary_extras(style):
    record = logging.LogRecord('security', logging.ERROR, __file__, 1, 'Audit unavailable', (), None)
    record.request_id, record.actor_id = 'request-123', 'account-456'
    record.authorization = 'Bearer must-not-export'
    record.credentials = {'secret': 'must-not-export'}
    formatted = ServiceFormatter(style, {}).format(record)
    assert 'request-123' in formatted and 'account-456' in formatted
    assert 'must-not-export' not in formatted
    if style == 'json':
        row = json.loads(formatted)
        assert row['request_id'] == 'request-123' and row['actor_id'] == 'account-456'


@pytest.mark.asyncio
async def test_failed_denial_audit_retains_safe_actor_request_and_explicit_persistence_reason(caplog):
    from unittest.mock import AsyncMock

    from hummingbot.cli.admin_access_api import AdminAccessAPI
    api = SimpleNamespace(access_store=lambda: SimpleNamespace(record_denial=object()),
                          run_database=AsyncMock(side_effect=RuntimeError('private-database-input')))
    with caplog.at_level(logging.WARNING):
        await AdminAccessAPI.audit_denial(api, actor=SimpleNamespace(id='account-456'), reason_code='forbidden',
                                          request_identity='request-123', mutation=True)
    record = next(row for row in caplog.records if getattr(row, 'event', None) == 'admin_audit_unavailable')
    result = json.loads(ServiceFormatter('json', {}).format(record))
    assert result['actor_id'] == 'account-456' and result['request_id'] == 'request-123'
    assert result['reason_code'] == 'audit_persistence_unavailable' and result['severity'] == 'ERROR'
    assert 'private-database-input' not in json.dumps(result)


@pytest.fixture
def console(monkeypatch):
    stream = io.StringIO()
    root = logging.getLogger()
    old_handlers, old_level = root.handlers[:], root.level
    old_network = getattr(HummingbotLogger, "network_log_level", None)
    old_sql_level = logging.getLogger("sqlalchemy").level
    old_warning_handler, old_warning_capture = warnings.showwarning, logging._warnings_showwarning
    root.handlers = [logging.StreamHandler(stream)]
    root.setLevel(logging.INFO)
    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    host = SimpleNamespace(settings=SimpleNamespace(bot_id="test-bot"),
                           owner=SimpleNamespace(config_file_path="mm.yml", owner_uuid="owner", generation=3),
                           revision="revision-1", mm_session=SimpleNamespace(id="session-1"))
    logger = HummingbotLogger("service-log-test")
    logger.parent = root
    try:
        yield host, logger, stream
    finally:
        for handler in root.handlers:
            handler.close()
        root.handlers = old_handlers
        root.setLevel(old_level)
        HummingbotLogger.network_log_level = old_network
        logging.getLogger("sqlalchemy").setLevel(old_sql_level)
        warnings.showwarning, logging._warnings_showwarning = old_warning_handler, old_warning_capture


def records(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_json_severity_utc_context_and_single_line(console, monkeypatch):
    host, logger, stream = console
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    ServiceHost.configure_logging(host)
    for level in (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL):
        logger.log(level, "message\nsecond line", extra={"event": "test_event", "reason_code": "test_reason"})
    values = records(stream)
    assert [r["severity"] for r in values] == ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    assert all(r["message"] == "message\nsecond line" for r in values)
    assert all(r["bot_id"] == "test-bot" and r["owner_generation"] == 3 for r in values)
    assert values[0]["revision"] == "revision-1"
    assert values[0]["session_id"] == "session-1"
    assert values[0]["event"] == "test_event" and values[0]["reason_code"] == "test_reason"
    assert values[0]["timestamp"].endswith("Z")
    assert datetime.fromisoformat(values[0]["timestamp"].replace("Z", "+00:00")).utcoffset().total_seconds() == 0


@pytest.mark.parametrize("threshold,expected", [("INFO", ["INFO", "WARNING", "ERROR"]),
                                                ("WARNING", ["WARNING", "ERROR"]), ("ERROR", ["ERROR"])])
def test_level_threshold_and_network_admission(console, monkeypatch, threshold, expected):
    host, logger, stream = console
    monkeypatch.setenv("LOG_LEVEL", threshold)
    ServiceHost.configure_logging(host)
    logger.debug("details")
    logger.info("normal")
    logger.network("connection unavailable")
    logger.error("operator required")
    assert [r["severity"] for r in records(stream)] == expected


def test_explicit_child_debug_level_cannot_bypass_service_threshold(console):
    host, logger, stream = console
    logger.setLevel(logging.DEBUG)
    ServiceHost.configure_logging(host)
    logger.debug("details hidden at INFO")
    logger.info("visible")
    assert [r["severity"] for r in records(stream)] == ["INFO"]


def test_incident_escalation_logs_error_immediately_without_retry_spam(console, monkeypatch):
    host, _, stream = console
    ServiceHost.configure_logging(host)
    monkeypatch.setattr("hummingbot.cli.service_availability.time.monotonic", lambda: 10)
    availability = ServiceAvailability()
    availability.fail("reporting", "unavailable", ValueError("private exception"))
    availability.fail("reporting", "unavailable", operator=True)
    availability.fail("reporting", "unavailable", operator=True)
    availability.clear("reporting")
    values = records(stream)
    assert [r["severity"] for r in values] == ["WARNING", "ERROR", "INFO"]
    assert values[1]["reason_code"] == "unavailable"
    assert "private exception" not in stream.getvalue()


def test_secret_values_redacted_in_message_context_and_traceback(console, monkeypatch):
    host, logger, stream = console
    secret = "configured-private-value"
    monkeypatch.setenv("ADMIN_API_PASSWORD", secret)
    monkeypatch.setenv("DATABASE_URL", "postgresql://operator:db%40secret@localhost/db")
    host.settings.bot_id = secret
    ServiceHost.configure_logging(host)
    try:
        raise ValueError(f"failure {secret} db@secret")
    except ValueError:
        logger.exception("request failed: %s", secret)
    value = records(stream)[0]
    assert secret not in stream.getvalue() and "db@secret" not in stream.getvalue()
    assert value["severity"] == "ERROR" and "ValueError" in value["exception"]
    assert "[REDACTED]" in value["message"]


def test_reconfigure_keeps_one_record_and_refreshes_owner(console):
    host, logger, stream = console
    ServiceHost.configure_logging(host)
    ServiceHost.configure_logging(host)
    host.owner.generation = 4
    logger.info("updated")
    assert len(records(stream)) == 1 and records(stream)[0]["owner_generation"] == 4


def test_text_format_uses_same_levels_and_redaction(console, monkeypatch):
    host, logger, stream = console
    monkeypatch.setenv("LOG_FORMAT", "text")
    monkeypatch.setenv("ADMIN_API_PASSWORD", "private-password")
    ServiceHost.configure_logging(host)
    logger.network("failure private-password")
    assert "WARNING" in stream.getvalue() and "private-password" not in stream.getvalue()
    assert "bot=test-bot" in stream.getvalue()


@pytest.mark.parametrize("env", [{"LOG_LEVEL": "bad-private-value"}, {"LOG_FORMAT": "bad-private-value"}])
def test_invalid_logging_settings_are_rejected_without_echoing_input(console, monkeypatch, env):
    from hummingbot.client.config.runtime_config import RuntimeConfigurationError
    host, _, _ = console
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeConfigurationError) as error:
        ServiceHost.configure_logging(host)
    assert "bad-private-value" not in str(error.value)


def test_console_bootstrap_honors_env_and_custom_levels(console, monkeypatch):
    from hummingbot import init_logging
    from hummingbot.logger.struct_logger import EVENT_LOG_LEVEL, METRICS_LOG_LEVEL
    _, _, stream = console
    monkeypatch.setattr("sys.stdout", stream)
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    logger = logging.getLogger("service-bootstrap-test")
    logger.setLevel(logging.ERROR)
    init_logging("unused.yml", None, console_only=True)
    logger.log(EVENT_LOG_LEVEL, "event detail")
    logger.log(METRICS_LOG_LEVEL, "metric detail")
    logger.network("network failure")
    assert [r["severity"] for r in records(stream)] == ["DEBUG", "DEBUG", "WARNING"]


@pytest.mark.parametrize("level", ["INFO", "DEBUG"])
def test_bootstrap_does_not_enable_sql_queries_or_parameter_logging(console, monkeypatch, level):
    from sqlalchemy import create_engine, text

    from hummingbot import init_logging
    _, _, stream = console
    monkeypatch.setattr("sys.stdout", stream)
    monkeypatch.setenv("LOG_LEVEL", level)
    engine = create_engine("sqlite://")
    try:
        init_logging("unused.yml", None, console_only=True)
        with engine.connect() as connection:
            connection.execute(text("SELECT :value"), {"value": "private-row-value"})
        logging.getLogger("sqlalchemy.engine.Engine").warning("pool health warning")
        values = records(stream)
        assert len(values) == 1 and values[0]["severity"] == "WARNING"
        assert values[0]["message"] == "pool health warning"
        assert "private-row-value" not in stream.getvalue()
    finally:
        engine.dispose()


def test_python_warning_uses_single_json_warning(console):
    host, _, stream = console
    ServiceHost.configure_logging(host)
    with warnings.catch_warnings():
        warnings.simplefilter("always")
        warnings.warn("serializer warning\nsecond line", UserWarning)
    values = records(stream)
    assert len(values) == 1 and values[0]["severity"] == "WARNING"
    assert values[0]["logger"] == "py.warnings" and "serializer warning" in values[0]["message"]


def test_legacy_yaml_logging_retains_original_network_level(console, monkeypatch, tmp_path):
    from hummingbot import init_logging
    host, _, _ = console
    ServiceHost.configure_logging(host)
    (tmp_path / "conf").mkdir()
    (tmp_path / "conf" / "logs.yml").write_text("version: 1\n")
    monkeypatch.setattr("hummingbot.prefix_path", lambda: str(tmp_path))
    monkeypatch.setattr("logging.config.dictConfig", lambda config: None)
    init_logging("logs.yml", None)
    assert HummingbotLogger.network_log_level is None


@pytest.mark.parametrize("level", ["INFO", "invalid-private-input"])
def test_startup_configuration_failure_emits_json_without_secret_inputs(level):
    # Fail before creating the database owner or performing any exchange I/O.
    env = {key: value for key, value in os.environ.items() if key not in ("ENV_FILE", "HBOT_ENV_FILE")}
    env.update(K_SERVICE="logging-test", STORAGE_BACKEND="postgres", DATABASE_URL="invalid-private-dsn",
               LOG_LEVEL=level, LOG_FORMAT="json")
    result = subprocess.run([sys.executable, "-m", "hummingbot.cli.service"], env=env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 78 and not result.stderr
    values = [json.loads(line) for line in result.stdout.splitlines()]
    assert [value["severity"] for value in values] == ["ERROR", "INFO"]
    assert values[0]["event"] == "service_failed" and values[-1]["event"] == "service_exit"
    assert "invalid-private" not in result.stdout


def test_identity_bootstrap_failure_emits_specific_safe_reason_and_permanent_exit():
    program = """
import asyncio
from dataclasses import replace
from hummingbot.cli.service import ServiceHost, run_service
from hummingbot.client.config.runtime_config import RuntimeSettings
from hummingbot.core.control_errors import ControlConflict
async def fail_bootstrap(self):
    raise ControlConflict('private-database-password-must-not-leak', code='bot_identity_mismatch')
ServiceHost.bootstrap = fail_bootstrap
settings = replace(RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test'}), port=0)
raise SystemExit(asyncio.run(run_service(settings=settings, bind_host='127.0.0.1')))
"""
    env = {key: value for key, value in os.environ.items() if key not in ('ENV_FILE', 'HBOT_ENV_FILE')}
    env.update(K_SERVICE='logging-test', STORAGE_BACKEND='postgres', ADMIN_API_ENABLED='false',
               ADMIN_BASIC_AUTH_ENABLED='false', ADMIN_GOOGLE_AUTH_ENABLED='false', LOG_LEVEL='INFO', LOG_FORMAT='json')
    result = subprocess.run([sys.executable, '-c', program], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 78 and not result.stderr
    values = [json.loads(line) for line in result.stdout.splitlines()]
    failures = [row for row in values if row.get('event') == 'service_failed']
    assert len(failures) == 1
    assert failures[0]['reason_code'] == 'bot_identity_mismatch'
    assert failures[0]['error_type'] == 'ControlConflict'
    assert failures[0]['severity'] == 'ERROR'
    assert 'private-database-password' not in result.stdout
