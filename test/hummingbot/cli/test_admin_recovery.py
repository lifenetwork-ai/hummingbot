from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from hummingbot.cli.admin_api import ADMIN_PREFIX, AdminAPI, admin_middleware
from hummingbot.client.config.admin_config import AdminSettings
from hummingbot.core.admin_identity import Principal


@asynccontextmanager
async def client(role='admin'):
    settings = AdminSettings(enabled=True, google_auth_enabled=True, google_client_id='offline-client')
    identity = SimpleNamespace(authenticate=AsyncMock(return_value=Principal('u', 'google', role, email='boss@example.com', account_version=1)))
    host = SimpleNamespace(store=MagicMock(), settings=SimpleNamespace(bot_id='test'))
    store = MagicMock()
    store.latest.return_value = None
    store.state.read.return_value = dict(owner_uuid='owner', owner_generation=1, heartbeat_age_s=0,
                                         database_now=datetime.now(timezone.utc).timestamp(), desired_state='stopped',
                                         control_version=3, acknowledged_version=3, recovery_state=dict(halt=True,
                                                                                                        reason='Strategy initialization/reconciliation failed; inspect sanitized logs.'))
    now = datetime.now(timezone.utc)
    store.request.return_value = dict(id='op', action='recovery_check', status='accepted', created_at=now, updated_at=now,
                                      result={'stage': 'queued', 'can_recover': False, '_private': {'do_not_expose': 'private-proof'}})
    store.get.return_value = store.request.return_value
    app = web.Application(middlewares=[admin_middleware(settings, identity=identity)])
    api = AdminAPI(host, settings); api.recovery_store = lambda: store; api.register(app)
    async with TestClient(TestServer(app), headers={'Authorization': 'Bearer offline-token'}) as http:
        yield http, store


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['read', 'write'])
@pytest.mark.parametrize('method,path', [('get', '/recovery'), ('post', '/recovery/check'), ('post', '/recovery/reset'), ('get', '/recovery/operations/op')])
async def test_non_admin_cannot_read_or_execute_recovery(role, method, path):
    async with client(role) as (http, store):
        result = await getattr(http, method)(ADMIN_PREFIX + path)
        assert result.status == 403
        store.request.assert_not_called(); store.get.assert_not_called()


@pytest.mark.asyncio
async def test_admin_check_returns_safe_accepted_receipt_and_forwards_identity():
    async with client() as (http, store):
        result = await http.post(ADMIN_PREFIX + '/recovery/check', json={'expected_version': 3}, headers={'Idempotency-Key': 'check'})
        assert result.status == 202
        body = await result.json()
        assert body['stage'] == 'queued' and body['trading_state'] == 'stopped'
        assert 'private-proof' not in str(body) and '_private' not in str(body)
        assert store.request.call_args.args[0] == 'recovery_check'
        assert store.request.call_args.args[3].role == 'admin'
        status = await http.get(ADMIN_PREFIX + '/recovery/operations/op')
        assert status.status == 200


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', [{'expected_version': True}, {'expected_version': -1}, {'expected_version': 0, 'force': True},
                                     {'expected_version': 0, 'positions': []}])
async def test_reset_does_not_accept_bypass_or_financial_override_fields(payload):
    async with client() as (http, store):
        result = await http.post(ADMIN_PREFIX + '/recovery/check', json=payload, headers={'Idempotency-Key': 'check'})
        assert result.status == 422
        store.request.assert_not_called()


@pytest.mark.asyncio
async def test_missing_confirmation_or_retry_key_never_reaches_storage():
    async with client() as (http, store):
        assert (await http.post(ADMIN_PREFIX + '/recovery/reset', json={'expected_version': 0, 'check_id': 'c'},
                                headers={'Idempotency-Key': 'reset'})).status == 422
        assert (await http.post(ADMIN_PREFIX + '/recovery/check', json={'expected_version': 0})).status == 422
        store.request.assert_not_called()


@pytest.mark.asyncio
async def test_recovery_paths_and_confirmation_schema_are_documented():
    async with client() as (http, store):
        result = await http.get(ADMIN_PREFIX + '/openapi.json')
        assert result.status == 200
        body = await result.json()
        assert ADMIN_PREFIX + '/recovery/reset' in body['paths']
        schema = body['components']['schemas']['RecoveryResetRequest']
        assert set(schema['required']) == {'expected_version', 'check_id', 'confirmation_token'}


@pytest.mark.asyncio
async def test_overview_explains_check_availability_without_querying_exchange():
    async with client() as (http, store):
        result = await http.get(ADMIN_PREFIX + '/recovery')
        assert result.status == 200
        body = await result.json()
        assert body['halted'] and body['check_available'] and not body['reset_available']
        assert body['operation'] is None


@pytest.mark.asyncio
async def test_unicode_confirmation_is_validation_error_without_storage_call():
    async with client() as (http, store):
        result = await http.post(ADMIN_PREFIX + '/recovery/reset',
                                 json=dict(expected_version=3, check_id='c', confirmation_token='é' * 32),
                                 headers={'Idempotency-Key': 'reset'})
        assert result.status == 422
        store.request.assert_not_called()
