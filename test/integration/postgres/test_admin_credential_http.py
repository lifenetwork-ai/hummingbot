from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import BasicAuth, web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import func, update

from hummingbot.cli.admin_api import ADMIN_ACCESS_KEY, ADMIN_PREFIX, AdminAPI, admin_middleware
from hummingbot.client.config.admin_config import AdminSettings
from hummingbot.connector.credential_adapters import CredentialAdapterError, CredentialAdapters, OKXCredentialAdapter
from hummingbot.model.admin_command import AdminCommand
from hummingbot.model.bot_state import BotState
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.credential_store import CredentialStore
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres

AUTH = {"Authorization": BasicAuth("operator", "offline-password").encode()}
BODY = dict(expected_version=0, expected_control_version=0,
            credentials=dict(api_key="HTTP-CANARY-API", secret_key="HTTP-CANARY-SECRET", passphrase="HTTP-CANARY-PASS"))


@asynccontextmanager
async def client_for(pg_engine, monkeypatch, *, mode="postgres_kms"):
    import base64
    monkeypatch.setenv("EXCHANGE_CREDENTIALS_SOURCE", mode)
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("KMS_PROVIDER", "local")
    monkeypatch.setenv("KMS_LOCAL_SECRET_KEY", base64.b64encode(b"K" * 32).decode())
    monkeypatch.setenv("OKX_TRADING_ENV", "demo")
    monkeypatch.setenv("OKX_SITE", "www")
    bootstrap_postgres(pg_engine, auto_migrate=True)
    state = BotStateStore(pg_engine, "hummingbot")
    state.ensure()
    settings = AdminSettings(enabled=True, username="operator", password="offline-password", basic_auth_enabled=True)
    app = web.Application(middlewares=[admin_middleware(settings)])
    api = AdminAPI(SimpleNamespace(store=state), settings)
    adapter = OKXCredentialAdapter()
    adapter.preflight = AsyncMock(return_value="www:account")
    api._credential_adapters_override = CredentialAdapters([adapter], enabled_connectors={"okx", "okx_perpetual"})
    api.register(app)
    async with TestClient(TestServer(app)) as client:
        yield client, state, adapter


@pytest.mark.asyncio
async def test_secret_save_has_typed_receipt_no_plaintext_and_no_auto_start(pg_engine, monkeypatch):
    async with client_for(pg_engine, monkeypatch) as (client, state, adapter):
        response = await client.post(ADMIN_PREFIX + "/credentials/okx/okx", headers={**AUTH, "Idempotency-Key": "save"}, json=BODY)
        assert response.status == 202, await response.text()
        receipt = await response.json()
        assert receipt["action"] == "credentials_set" and receipt["session_id"] is None
        assert receipt["result"]["stage"] == "waiting_for_owner"
        retry = await client.post(ADMIN_PREFIX + "/credentials/okx/okx", headers={**AUTH, "Idempotency-Key": "save"},
                                  json={**BODY, "credentials": {**BODY["credentials"], "secret_key": "different-canary"}})
        assert await retry.json() == receipt and adapter.preflight.await_count == 1
        assert state.read()["desired_state"] == "stopped"
        read = await client.get(ADMIN_PREFIX + "/credentials/operations/" + receipt["id"], headers=AUTH)
        assert read.status == 200 and (await read.json())["id"] == receipt["id"]
        assert (await client.get(ADMIN_PREFIX + "/commands/" + receipt["id"], headers=AUTH)).status == 404
        status = await (await client.get(ADMIN_PREFIX + "/credentials/okx", headers=AUTH)).json()
        slot = next(row for row in status["slots"] if row["connector"] == "okx")
        assert slot["configured"] and slot["status"] == "checking"
        assert "HTTP-CANARY" not in str(receipt) + str(status)
        audit = await (await client.get(ADMIN_PREFIX + "/audit", headers=AUTH)).json()
        assert any(event["operation_id"] == receipt["id"] and event["connector"] == "okx" for event in audit["items"])
        row = CredentialStore(state).load("okx", "okx", "demo")
        assert row["version"] == 1 and "HTTP-CANARY" not in str(row)


@pytest.mark.asyncio
async def test_environment_source_cannot_mutate_and_routing_cannot_be_overridden(pg_engine, monkeypatch):
    async with client_for(pg_engine, monkeypatch, mode="environment") as (client, state, adapter):
        response = await client.post(ADMIN_PREFIX + "/credentials/okx/okx", headers={**AUTH, "Idempotency-Key": "save"}, json=BODY)
        assert response.status == 409 and (await response.json())["code"] == "credentials_managed_externally"
        assert adapter.preflight.await_count == 0 and not CredentialStore(state).metadata()
    async with client_for(pg_engine, monkeypatch) as (client, state, adapter):
        bad = {**BODY, "credentials": {**BODY["credentials"], "okx_registration_sub_domain": "app"}}
        response = await client.post(ADMIN_PREFIX + "/credentials/okx/okx", headers={**AUTH, "Idempotency-Key": "save"}, json=bad)
        assert response.status == 422 and "HTTP-CANARY" not in await response.text()
        assert not CredentialStore(state).metadata()


@pytest.mark.asyncio
async def test_candidate_unavailability_is_not_reported_as_bad_key_and_writes_nothing(pg_engine, monkeypatch):
    async with client_for(pg_engine, monkeypatch) as (client, state, adapter):
        adapter.preflight.side_effect = CredentialAdapterError("exchange_unavailable", status=503)
        response = await client.post(ADMIN_PREFIX + "/credentials/okx/okx", headers={**AUTH, "Idempotency-Key": "save"}, json=BODY)
        assert response.status == 503 and (await response.json())["code"] == "exchange_unavailable"
        assert not CredentialStore(state).metadata() and state.read()["control_version"] == 0


@pytest.mark.asyncio
async def test_rejected_replacement_preserves_saved_version_and_stopped_bot(pg_engine, monkeypatch):
    async with client_for(pg_engine, monkeypatch) as (client, state, adapter):
        response = await client.post(ADMIN_PREFIX + "/credentials/okx/okx", headers={**AUTH, "Idempotency-Key": "first"}, json=BODY)
        receipt = await response.json()
        with pg_engine.begin() as connection:
            connection.execute(update(BotState).values(owner_uuid="owner", owner_generation=1))
        saved = CredentialStore(state)
        assert saved.finish_set(receipt["id"], "owner", 1, versions={"okx": 1})
        with pg_engine.begin() as connection:
            connection.execute(update(BotState).values(acknowledged_version=1))
        original = saved.load("okx", "okx", "demo")
        adapter.preflight.side_effect = CredentialAdapterError("exchange_credentials_rejected")
        response = await client.post(ADMIN_PREFIX + "/credentials/okx/okx", headers={**AUTH, "Idempotency-Key": "rejected"},
                                     json={**BODY, "expected_version": 1, "expected_control_version": 1})
        assert response.status == 422 and (await response.json())["code"] == "exchange_credentials_rejected"
        assert saved.load("okx", "okx", "demo") == original
        assert state.read()["desired_state"] == "stopped" and state.read()["control_version"] == 1


@pytest.mark.asyncio
async def test_storage_receipts_and_audit_do_not_hardcode_okx(pg_engine, monkeypatch):
    async with client_for(pg_engine, monkeypatch) as (client, state, adapter):
        class OtherAdapter:
            exchange, connectors, environment = "other", ("other_spot",), "demo"
            config = staticmethod(lambda connector, values: None)
            preflight = AsyncMock(return_value="other-subaccount")

            def descriptor(self):
                return dict(exchange=self.exchange, environment=self.environment, cleanup_mode="simulated",
                            fields=[dict(name=name, label=name, secret=True, required=True) for name in BODY["credentials"]])

        api = client.app[ADMIN_ACCESS_KEY]
        api._credential_adapters_override = CredentialAdapters([OtherAdapter()], enabled_connectors={"other_spot"})
        status = await client.get(ADMIN_PREFIX + "/credentials", headers=AUTH)
        assert (await status.json())["exchanges"][0]["exchange"] == "other"
        response = await client.post(ADMIN_PREFIX + "/credentials/other/other_spot", headers={**AUTH, "Idempotency-Key": "save-other"}, json=BODY)
        assert response.status == 202, await response.text()
        receipt = await response.json()
        assert receipt["payload"]["exchange"] == "other" and receipt["payload"]["connector"] == "other_spot"
        row = CredentialStore(state).load("other", "other_spot", "demo")
        assert row["account_id"] == "other-subaccount" and "HTTP-CANARY" not in str(row)
        events = (await (await client.get(ADMIN_PREFIX + "/audit", headers=AUTH)).json())["items"]
        assert any(event["exchange"] == "other" and event["operation_id"] == receipt["id"] for event in events)


@pytest.mark.asyncio
async def test_connected_requires_fresh_matching_owner_evidence_not_only_saved_credentials(pg_engine, monkeypatch):
    import time
    async with client_for(pg_engine, monkeypatch) as (client, state, adapter):
        response = await client.post(ADMIN_PREFIX + "/credentials/okx/okx", headers={**AUTH, "Idempotency-Key": "save"}, json=BODY)
        receipt = await response.json()
        with pg_engine.begin() as connection:
            connection.execute(update(BotState).values(owner_uuid="owner", owner_generation=1, heartbeat=func.clock_timestamp()))
        assert CredentialStore(state).finish_set(receipt["id"], "owner", 1, versions={"okx": 1})
        for version, owner, age, expected in ((1, "old", 0, "unavailable"), (1, "owner", 30, "unavailable"),
                                              (2, "owner", 0, "unavailable"), (1, "owner", 0, "connected")):
            with pg_engine.begin() as connection:
                connection.execute(update(BotState).values(heartbeat=func.clock_timestamp(), status=dict(credential_versions={"okx": version},
                                                                                                         credential_owner=dict(owner_uuid=owner, generation=1), availability=dict(observed_at=time.time() - age))))
            status = await (await client.get(ADMIN_PREFIX + "/credentials/okx", headers=AUTH)).json()
            observed = state.read()
            assert status["slots"][0]["status"] == expected, {
                "owner": observed["owner_uuid"], "generation": observed["owner_generation"],
                "age": observed["heartbeat_age_s"], "database_now": observed["database_now"], "status": observed["status"]}
        with pg_engine.begin() as connection:
            connection.execute(update(AdminCommand).where(AdminCommand.id == receipt["id"]).values(
                status="failed", error="kms_unavailable"))
        status = await (await client.get(ADMIN_PREFIX + "/credentials/okx", headers=AUTH)).json()
        assert status["slots"][0]["status"] == "connected"  # A historical receipt cannot override fresh recovered evidence.


@pytest.mark.asyncio
async def test_disconnect_and_repair_are_typed_versioned_and_never_erase_early(pg_engine, monkeypatch):
    async with client_for(pg_engine, monkeypatch) as (client, state, adapter):
        first = await client.post(ADMIN_PREFIX + '/credentials/okx/okx', headers={**AUTH, 'Idempotency-Key': 'save'}, json=BODY)
        saved = await first.json()
        with pg_engine.begin() as connection:
            connection.execute(update(BotState).values(owner_uuid='owner', owner_generation=1))
        store = CredentialStore(state)
        assert store.finish_set(saved['id'], 'owner', 1, versions={'okx': 1})
        response = await client.post(ADMIN_PREFIX + '/credentials/okx/okx/revoke', headers={**AUTH, 'Idempotency-Key': 'remove'},
                                     json=dict(expected_version=1, expected_control_version=1))
        assert response.status == 202, await response.text()
        root = await response.json()
        assert root['result']['stage'] == 'waiting_for_owner'
        status = await (await client.get(ADMIN_PREFIX + '/credentials/okx', headers=AUTH)).json()
        assert status['slots'][0]['status'] == 'disconnecting'
        assert store.load('okx', 'okx', 'demo')['envelope'] is not None
        store.progress_disconnect(root['id'], 'owner', 1, stage='action_required', reason_code='exchange_credentials_rejected', operator_action_required=True)
        response = await client.post(ADMIN_PREFIX + '/credentials/okx/okx/cleanup-credentials', headers={**AUTH, 'Idempotency-Key': 'repair'},
                                     json=dict(operation_id=root['id'], expected_version=1, expected_control_version=2, expected_operation_version=1,
                                               credentials=BODY['credentials']))
        assert response.status == 202, await response.text()
        child = await response.json()
        assert child['result']['cleanup_operation_id'] == root['id']
        assert store.load('okx', 'okx', 'demo')['state'] == 'revoking'
        proof = dict(orders_terminal_verified=True, positions_flat_verified=True, ledger_confirmed=True, consumers_detached=True,
                     observed_at=float(state.read()['database_now']), owner_generation=1)
        store.progress_disconnect(root['id'], 'owner', 1, stage='detaching', evidence=proof)
        assert store.finish_disconnect(root['id'], 'owner', 1, evidence=proof)
        operation = await client.get(ADMIN_PREFIX + '/credentials/operations/' + child['id'], headers=AUTH)
        assert operation.status == 200, await operation.text()
        assert (await operation.json())['status'] == 'succeeded'
        status = await (await client.get(ADMIN_PREFIX + '/credentials/okx', headers=AUTH)).json()
        assert status['slots'][0]['status'] == 'disconnected' and not status['slots'][0]['configured']
        assert 'HTTP-CANARY' not in str(child) + str(status)
