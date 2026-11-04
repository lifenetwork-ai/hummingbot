import base64
import json
import time
from test.integration.postgres.test_service import eventually, health, start_service, terminate
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from aiohttp import BasicAuth

from hummingbot.model.bot_state_store import BotStateStore


def call(port, path, *, body=None, key=None):
    headers = {"Authorization": BasicAuth("operator", "offline-admin").encode()}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if key is not None:
        headers["Idempotency-Key"] = key
    request = Request(f"http://127.0.0.1:{port}/api/v1/admin{path}", headers=headers,
                      data=json.dumps(body).encode() if body is not None else None)
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


def test_real_process_managed_set_acknowledgement_and_restart_keep_bot_stopped(pg_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("EXCHANGE_CREDENTIALS_SOURCE", "postgres_kms")
    monkeypatch.setenv("KMS_PROVIDER", "local")
    monkeypatch.setenv("KMS_LOCAL_SECRET_KEY", base64.b64encode(b"K" * 32).decode())
    monkeypatch.setenv("OKX_TRADING_ENV", "demo")
    monkeypatch.setenv("OKX_SITE", "www")
    monkeypatch.setenv("HBOT_OFFLINE_CREDENTIAL_ACCOUNT", "offline-account")
    processes = []
    store = BotStateStore(pg_engine, "hummingbot")
    try:
        process, port, log = start_service(tmp_path / "managed-first", admin_enabled=True)
        processes.append((process, log))
        eventually(lambda: health(port, "/startup")[0] == 200)
        eventually(lambda: store.read() and store.read()["acknowledged_version"] == 0 and store.read()["owner_uuid"])
        status, receipt = call(port, "/credentials/okx/okx", key="save", body=dict(expected_version=0, expected_control_version=0,
                                                                                   credentials=dict(api_key="SERVICE-CANARY-API", secret_key="SERVICE-CANARY-SECRET", passphrase="SERVICE-CANARY-PASS")))
        assert status == 202, receipt
        eventually(lambda: call(port, "/credentials/operations/" + receipt["id"])[1].get("status") == "succeeded")
        eventually(lambda: store.read()["acknowledged_version"] == 1)
        status, saved = call(port, "/credentials/okx")
        assert status == 200 and saved["slots"][0]["status"] == "connected"
        assert store.read()["desired_state"] == "stopped" and not store.read()["status"]["running"]
        terminate(process)
        assert process.returncode == 0
        process, port, log = start_service(tmp_path / "managed-restart", admin_enabled=True, auto_migrate=False)
        processes.append((process, log))
        eventually(lambda: health(port, "/startup")[0] == 200)
        eventually(lambda: call(port, "/credentials/okx")[1].get("slots", [{}])[0].get("status") == "connected")
        assert store.read()["desired_state"] == "stopped" and not store.read()["status"]["running"]
    finally:
        for process, log in processes:
            terminate(process)
            log.seek(0)
            assert "SERVICE-CANARY" not in log.read()
            log.close()


def test_missing_managed_kek_keeps_admin_alive_without_owner_restart_loop(pg_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("EXCHANGE_CREDENTIALS_SOURCE", "postgres_kms")
    monkeypatch.setenv("KMS_PROVIDER", "local")
    monkeypatch.delenv("KMS_LOCAL_SECRET_KEY", raising=False)
    process, port, log = start_service(tmp_path / "missing-key", admin_enabled=True)
    try:
        eventually(lambda: health(port, "/startup")[0] == 200)
        store = BotStateStore(pg_engine, "hummingbot")
        first = eventually(lambda: (row if (row := store.read()) and row["status"].get("phase") == "halted" else None))
        time.sleep(2)
        assert process.poll() is None and health(port, "/live")[0] == 200
        assert call(port, "/credentials")[0] == 200
        assert store.read()["owner_generation"] == first["owner_generation"]
    finally:
        terminate(process)
        log.close()
