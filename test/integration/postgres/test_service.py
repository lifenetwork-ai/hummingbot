"""Real service processes and HTTP/PG lifecycle; full trading image E2E is separate."""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import pytest

from hummingbot.model.bot_state_store import BotStateStore


def free_port():
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


def eventually(check, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    pytest.fail("Service lifecycle condition did not complete before its bounded deadline.")


def health(port, path):
    try:
        with urlopen(f"http://127.0.0.1:{port}{path}", timeout=1) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)
    except (URLError, TimeoutError):
        return None, None


def start_service(tmp_path, *, auto_migrate=True, admin_enabled=False):
    tmp_path.mkdir()
    conf = tmp_path / "conf"
    for name in ("strategies", "scripts", "controllers", "connectors"):
        (conf / name).mkdir(parents=True, exist_ok=True)
    (conf / "conf_client.yml").write_text("anonymized_metrics_mode: anonymized_metrics_disabled\n")
    for path in conf.rglob("*"):
        path.chmod(0o555 if path.is_dir() else 0o444)
    conf.chmod(0o555)
    port = free_port()
    env = {**os.environ, "DATABASE_URL": os.environ["HBOT_TEST_POSTGRES_URL"], "K_SERVICE": "offline-test",
           "K_REVISION": tmp_path.name, "BOT_ID": "hummingbot", "PORT": str(port),
           "ENABLE_AUTO_MIGRATION": str(auto_migrate).lower(), "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONPATH": str(Path(__file__).resolve().parents[3])}
    env.update(ADMIN_API_ENABLED=str(admin_enabled).lower(), ADMIN_API_USERNAME="operator", ADMIN_API_PASSWORD="offline-admin")
    for key in list(env):
        if key == "HBOT_ENV_FILE" or key.startswith("HBOT_OKX"):
            env.pop(key)
    # Override configuration input locations only. Run the real bootstrap,
    # migrations, schema checks, advisory owner, observer and signal handlers.
    program = """
import pathlib, socket, sys
from hummingbot.client import settings
conf = pathlib.Path(sys.argv[1])
settings.CONF_DIR_PATH = conf
settings.CLIENT_CONFIG_PATH = conf / 'conf_client.yml'
settings.TRADE_FEES_CONFIG_PATH = conf / 'conf_fee_overrides.yml'
settings.GATEWAY_SSL_CONF_FILE = conf / 'ssl.yml'
for constant, name in [('STRATEGIES_CONF_DIR_PATH', 'strategies'), ('SCRIPT_STRATEGY_CONF_DIR_PATH', 'scripts'),
                       ('CONTROLLERS_CONF_DIR_PATH', 'controllers'), ('CONNECTORS_CONF_DIR_PATH', 'connectors')]:
    setattr(settings, constant, conf / name)
original = socket.socket.connect
def offline(self, address):
    if isinstance(address, tuple) and address[0] not in ('127.0.0.1', '::1'):
        raise RuntimeError('External network is disabled')
    return original(self, address)
socket.socket.connect = offline
from hummingbot.cli import service_processes
from hummingbot.cli.service import main
# Repeat the test's isolated config paths and network prohibition in both real
# children. Keep the shipping supervisor; no test wrapper owns recovery policy.
service_processes.child_command = lambda role: [sys.executable, '-c', sys.orig_argv[2], str(conf), '--role', role]
main(sys.argv[2:])
"""
    log = (tmp_path / "captured-test.log").open("w+")
    process = subprocess.Popen([sys.executable, "-c", program, str(conf)], cwd=tmp_path, env=env,
                               stdout=log, stderr=subprocess.STDOUT)
    return process, port, log


def terminate(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=9.5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
            pytest.fail("Service did not exit within the process shutdown budget.")


def test_stopped_service_bootstrap_standby_handover_and_durable_control(pg_engine, tmp_path):
    processes = []
    try:
        first, port, log = start_service(tmp_path / "revision-one")
        processes.append((first, log))
        eventually(lambda: health(port, "/startup")[0] == 200)
        store = BotStateStore(pg_engine, "hummingbot")
        owner = eventually(lambda: (row if (row := store.read()) and row["owner_uuid"] else None))
        assert owner["desired_state"] == "stopped" and not owner["status"]["running"]
        assert health(port, "/live")[0] == 200 and health(port, "/ready")[0] == 503
        second, second_port, second_log = start_service(tmp_path / "revision-two", auto_migrate=False)
        processes.append((second, second_log))
        eventually(lambda: health(second_port, "/startup")[0] == 200)
        eventually(lambda: health(second_port, "/startup")[1] == {"state": "standby"})
        assert store.read()["owner_uuid"] == owner["owner_uuid"]
        started = time.monotonic()
        terminate(first)
        assert first.returncode == 0 and time.monotonic() - started < 9
        replacement = eventually(lambda: (row if (row := store.read())["owner_generation"] > owner["owner_generation"] else None))
        assert replacement["owner_uuid"] != owner["owner_uuid"]
        version = store.control(expected_version=0, desired_state="stopped")
        eventually(lambda: store.read()["acknowledged_version"] == version)
        terminate(second)
        assert second.returncode == 0
        row = store.read()
        assert row["desired_state"] == "stopped" and row["control_version"] == version
        assert row["owner_uuid"] is None
        for path in tmp_path.iterdir():
            assert sorted(item.name for item in path.iterdir()) == ["captured-test.log", "conf"]
    finally:
        for process, log in processes:
            terminate(process)
            log.close()


def test_service_auto_migration_false_refuses_empty_schema_without_creating_objects(pg_engine, tmp_path):
    process, port, log = start_service(tmp_path / "invalid-schema", auto_migrate=False)
    try:
        process.wait(timeout=25)
        assert process.returncode != 0
        from sqlalchemy import inspect
        assert inspect(pg_engine).get_table_names() == []
        assert health(port, "/startup")[0] != 200
    finally:
        terminate(process)
        log.close()
