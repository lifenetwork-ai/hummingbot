"""Credential-free ABI/runtime checks for the slim deployment image."""
import importlib
import importlib.util
import io
import json
import logging
import os
import shutil
import ssl
import subprocess
import sys
import warnings
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import talib
from numba import njit


def check_service_logging():
    """Exercise packaged logging and the real entry point without any network."""
    from sqlalchemy import create_engine, text

    from hummingbot.cli.service_availability import ServiceAvailability
    from hummingbot.logger import HummingbotLogger
    from hummingbot.logger.service_logging import configure_service_logging

    root = logging.getLogger()
    previous_handlers, previous_level = root.handlers[:], root.level
    previous_network_level = HummingbotLogger.network_log_level
    previous_sql_level = logging.getLogger("sqlalchemy").level
    previous_warning_handler, previous_warning_capture = warnings.showwarning, logging._warnings_showwarning
    stream = io.StringIO()
    root.handlers = [logging.StreamHandler(stream)]
    host = SimpleNamespace(settings=SimpleNamespace(bot_id="logging-smoke"), revision="test-revision",
                           owner=SimpleNamespace(owner_uuid="test-owner", generation=7, config_file_path="test.yml"),
                           mm_session=SimpleNamespace(id="test-session"))
    try:
        configure_service_logging(host, environ={"LOG_LEVEL": "DEBUG", "LOG_FORMAT": "json",
                                                 "ADMIN_API_PASSWORD": "smoke-private-value"})
        logger = HummingbotLogger("runtime-logging-smoke")
        logger.parent = root
        for level in (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL):
            logger.log(level, "multiline\nsmoke-private-value")
        logger.network("connection unavailable")
        availability = ServiceAvailability()
        availability.fail("test", "test_failure", operator=True)
        availability.clear("test")
        engine = create_engine("sqlite://")
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT :value"), {"value": "smoke-private-row"})
        finally:
            engine.dispose()
        with warnings.catch_warnings():
            warnings.simplefilter("always")
            warnings.warn("multiline warning\nsmoke-private-value", UserWarning)
        values = [json.loads(line) for line in stream.getvalue().splitlines()]
        assert [value["severity"] for value in values] == [
            "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "WARNING", "ERROR", "INFO", "WARNING"]
        assert all(value["timestamp"].endswith("Z") and value["owner_generation"] == 7
                   and value["session_id"] == "test-session" for value in values)
        assert values[-1]["logger"] == "py.warnings"
        assert "smoke-private-value" not in stream.getvalue() and "smoke-private-row" not in stream.getvalue()
    finally:
        for handler in root.handlers:
            handler.close()
        root.handlers = previous_handlers
        root.setLevel(previous_level)
        HummingbotLogger.network_log_level = previous_network_level
        logging.getLogger("sqlalchemy").setLevel(previous_sql_level)
        warnings.showwarning, logging._warnings_showwarning = previous_warning_handler, previous_warning_capture
    env = {key: value for key, value in os.environ.items() if key not in ("ENV_FILE", "HBOT_ENV_FILE")}
    env.update(K_SERVICE="logging-smoke", STORAGE_BACKEND="postgres", DATABASE_URL="invalid-private-dsn",
               LOG_LEVEL="INFO", LOG_FORMAT="json")
    result = subprocess.run([sys.executable, "-m", "hummingbot.cli.service"], env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 1 and not result.stderr
    startup = [json.loads(line) for line in result.stdout.splitlines()]
    assert [value["severity"] for value in startup] == ["ERROR", "INFO"]
    assert startup[0]["event"] == "service_failed" and startup[-1]["event"] == "service_exit"
    assert "invalid-private-dsn" not in result.stdout
    return {"passed": True, "all_severities": True, "network_warning": True,
            "utc_context": True, "redaction": True, "startup_failure_json": True,
            "python_warning_json": True, "sql_parameters_suppressed": True}


def main():
    sys.path.insert(0, str(Path.cwd()))
    from hummingbot.client.settings import AllConnectorSettings
    from hummingbot.model.db_migration.postgres.runner import SCRIPTS

    for executable in ("gcc", "g++", "rustc", "cargo", "conda"):
        assert shutil.which(executable) is None, f"Build tool shipped in runtime: {executable}"
    for module in ("Cython", "pytest", "conda_build"):
        spec = importlib.util.find_spec(module)
        assert spec is None or spec.origin is None, f"Build/test module shipped in runtime: {module}"
    for module in ("hummingbot.core.clock", "hummingbot.core.data_type.order_book",
                   "hummingbot.strategy.pure_market_making.pure_market_making",
                   "hummingbot.connector.exchange.okx.okx_exchange",
                   "hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative",
                   "hummingbot.strategy_v2.executors.executor_orchestrator", "pandas_ta", "psycopg2"):
        importlib.import_module(module)
    connectors = AllConnectorSettings.get_connector_settings()
    assert {"okx", "okx_perpetual"} <= connectors.keys()
    # Numba needs LLVM at runtime even though Rust/GCC are build-only.
    assert njit(lambda number: number + 1)(2) == 3
    assert talib.SMA(np.array([1., 2., 3.]), timeperiod=2)[-1] == 2.5
    ssl.create_default_context()
    trust = ssl.get_default_verify_paths()
    assert trust.cafile and Path(trust.cafile).is_file()
    assert len(list(SCRIPTS.glob("*.sql"))) == 17
    assert (SCRIPTS / "017_add_session_discovery_index.sql").is_file()
    assert (SCRIPTS / "016_create_mm_events.sql").is_file()
    assert (SCRIPTS.parent / "manual_scripts/001_normalize_legacy_names.sql").is_file()
    cli_version = subprocess.check_output(["hbot", "--version"], text=True, timeout=20).strip()
    assert cli_version
    report = json.loads(Path("cloudrun-runtime-packages.json").read_text())
    assert report["environment_bytes_after"] < report["environment_bytes_before"]
    logging_smoke = check_service_logging()
    print(json.dumps({"passed": True, "numba_jit": True, "ta_lib": True, "tls_trust": True,
                      "connector_count": len(connectors), "sql_scripts": len(list(SCRIPTS.glob("*.sql"))),
                      "build_tools_absent": True, "cli_version": cli_version,
                      "environment_bytes": report["environment_bytes_after"], "service_logging": logging_smoke}))


if __name__ == "__main__":
    main()
