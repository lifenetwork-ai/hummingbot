"""Actual SDK/auth/RESTConnection and WSAssistant final generation boundaries."""
import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from test.hummingbot.core.web_assistant.connections.test_request_guard import Response
from test.hummingbot.strategy_v2.life_liquidity.storage.offline_generation_provider import OfflineGenerationProvider
from test.hummingbot.strategy_v2.life_liquidity.storage.test_generation_access import broker_fixture
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.core.api_throttler.async_throttler import AsyncThrottler
from hummingbot.core.api_throttler.data_types import RateLimit
from hummingbot.core.web_assistant.connections.data_types import RESTMethod, WSJSONRequest
from hummingbot.core.web_assistant.connections.rest_connection import RESTConnection
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.generation_access import (
    BrokerIdentity,
    GenerationClient,
    GenerationCredentials,
)


def sdk(credentials, product, session):
    connector = (OkxExchange(credentials.api_key, credentials.secret, credentials.passphrase, trading_pairs=[], trading_required=False)
                 if product == 'SPOT' else OkxPerpetualDerivative(okx_perpetual_api_key=credentials.api_key,
                                                                  okx_perpetual_secret_key=credentials.secret, okx_perpetual_passphrase=credentials.passphrase, trading_pairs=[], trading_required=False))
    connector._api_request_url = AsyncMock(side_effect=lambda path_url, **_: 'https://www.okx.com' + path_url)
    connector._web_assistants_factory = WebAssistantsFactory(throttler=AsyncThrottler(rate_limits=[RateLimit(prefix + p, 100, 1) for prefix in ('', 'GET-', 'POST-') for p in ['test', '/api/v5/account/config'] + ['/api/v5/trade/' + p for p in ('order', 'amend-order', 'cancel-order', 'batch-orders', 'cancel-batch-orders', 'cancel-all-after', 'mass-cancel')]]), auth=connector._auth,
                                                             connections_factory=SimpleNamespace(get_rest_connection=AsyncMock(side_effect=lambda: RESTConnection(session)),
                                                                                                 get_ws_connection=AsyncMock(return_value=SimpleNamespace(send=AsyncMock()))))
    return connector


@pytest.mark.asyncio
@pytest.mark.parametrize('product', ['SPOT', 'SWAP'])
async def test_actual_sdk_all_write_routes_deny_recovery_even_for_cached_assistants(product):
    cfg, broker, client, identity, credentials, cert = broker_fixture()
    requests = []

    async def http(**kw):
        requests.append(kw)
        r = Response()

        async def body():
            return dict(code='0', data=[dict(uid='12345')])
        r.json = body
        return r
    connector = sdk(credentials, product, SimpleNamespace(request=http))
    old = await connector._web_assistants_factory.get_rest_assistant()
    broker.enable(identity.registration, mode='RECOVERY_ONLY', expires_at=identity.registration.identity.owner.expires_at)
    await client.install({connector.name: connector})
    assert len(requests) == 1  # Authenticated UID qualification precedes any cancel.
    requests.clear()
    for route in ('order', 'amend-order', 'cancel-order', 'batch-orders', 'cancel-batch-orders', 'cancel-all-after', 'mass-cancel'):
        with pytest.raises(StorageError):
            await connector._api_post('/api/v5/trade/' + route, data=dict(instId='LIFE-USDT'), is_auth_required=True)
    with pytest.raises(StorageError):
        await old.execute_request('https://www.okx.com/api/v5/trade/order', throttler_limit_id='test',
                                  method=RESTMethod.POST,
                                  data=dict(instId='LIFE-USDT'), is_auth_required=True)
    assert not requests
    ws = await connector._web_assistants_factory.get_ws_assistant()
    for op in ('order', 'cancel-order', 'amend-order', 'batch-orders', 'cancel-all-after'):
        with pytest.raises(StorageError):
            await ws.send(WSJSONRequest(payload=dict(op=op, args=[])))
    assert not ws._connection.send.called
    await connector._api_get('/api/v5/account/config', is_auth_required=True)
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('barrier', ['before_accept', 'after_accept'])
async def test_provider_revocation_delay_and_accepted_writes_gate_successor_delivery(barrier):
    from uuid import uuid4

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.ownership import ProcessCapability
    cfg, broker, client, identity, credentials, cert = broker_fixture()
    provider = OfflineGenerationProvider()
    broker.provider = provider
    provider.register(identity.registration.identity.credential_resource, credentials)
    broker.enable(identity.registration, mode='ACTIVE', expires_at=identity.registration.identity.owner.expires_at)
    entered, release = asyncio.Event(), asyncio.Event()

    async def pause(resource, request):
        entered.set()
        await release.wait()
    setattr(provider, barrier, pause)
    orders = []

    async def http(**kw):
        orders.append(json.loads(kw['data']))
        r = Response()

        async def body():
            return dict(code='0', data=[dict(ordId='1', clOrdId='old', sCode='0')])
        r.json = body
        return r
    connector = sdk(credentials, 'SPOT', SimpleNamespace(request=provider.wrap(http)))
    # Actual cached-key counterexample: an old raw SDK may already have passed
    # its final application/DB check. Provider state must exclude its key.
    task = asyncio.create_task(connector._api_post('/api/v5/trade/order', data=dict(instId='LIFE-USDT', clOrdId='old'), is_auth_required=True))
    await asyncio.wait_for(entered.wait(), 2)
    target_owner = replace(identity.registration.identity.owner, process_id=uuid4(), generation=uuid4())
    cap = ProcessCapability.create(cfg, target_owner)
    target = BrokerIdentity.create(replace(identity.registration.identity, owner=target_owner, service_identity='service:replacement', credential_resource='secret:replacement:1'), cap.registration)
    new_credentials = GenerationCredentials('new-key', 'new-secret', 'new-pass')
    broker.stage(target.registration, new_credentials)
    provider.register(target.registration.identity.credential_resource, new_credentials)
    target_client = GenerationClient(target, broker.dispatch, public_key=broker.public_key, local_ready=lambda: False, timeout_ms=1000)
    try:
        with pytest.raises(StorageError):
            await target_client.credentials()
        await broker.begin_exclusion(identity.registration)
        with pytest.raises(StorageError):
            await broker.certify(identity.registration, target.registration, expires_at=datetime.now(timezone.utc) + timedelta(seconds=5))
        provider.effect(identity.registration.identity.credential_resource, ws=False, caa=False)
        with pytest.raises(StorageError):
            await broker.certify(identity.registration, target.registration, expires_at=datetime.now(timezone.utc) + timedelta(seconds=5))
        provider.effect(identity.registration.identity.credential_resource)
        if barrier == 'after_accept':
            with pytest.raises(StorageError):
                await broker.certify(identity.registration, target.registration, expires_at=datetime.now(timezone.utc) + timedelta(seconds=5))
            release.set()
            await task
        else:
            certificate = await broker.certify(identity.registration, target.registration, expires_at=datetime.now(timezone.utc) + timedelta(seconds=5))
            release.set()
            with pytest.raises(StorageError, match='PROVIDER_REST_REVOKED'):
                await task
        certificate = await broker.certify(identity.registration, target.registration, expires_at=datetime.now(timezone.utc) + timedelta(seconds=5)) if barrier == 'after_accept' else certificate
        broker.admit(target.registration, certificate, recovery=True)
        broker.bind_head(target.registration, uuid4(), 2)  # Explicit trusted control, native binding qualified separately.
        assert await target_client.credentials() == new_credentials
        with pytest.raises(StorageError):
            await client.credentials()
        with pytest.raises(StorageError, match='WS_REVOKED'):
            provider.check_ws(credentials.api_key, 'order')
        assert bool(orders) == (barrier == 'after_accept')
        assert certificate.inflight_reconciled and certificate.caa_disabled
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('uid', ['foreign', None])
async def test_installed_sdk_rejects_foreign_or_missing_authenticated_account_uid(uid):
    cfg, broker, client, identity, credentials, cert = broker_fixture()

    async def http(**kw):
        response = Response()

        async def data():
            return dict(code='0', data=[dict(uid=uid)])
        response.json = data
        return response
    connector = sdk(credentials, 'SPOT', SimpleNamespace(request=http))
    with pytest.raises(StorageError, match='AUTHENTICATED_UID_MISMATCH'):
        await client.install({'okx': connector})
    with pytest.raises(StorageError):
        await client.credentials()
