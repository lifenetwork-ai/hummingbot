"""Generation broker request authorization, not a mocked lease verdict."""
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.storage.test_generation_exclusion import proof

import pytest

from hummingbot.core.web_assistant.connections.data_types import RESTMethod, RESTRequest
from hummingbot.core.web_assistant.connections.request_guard import RESTRequestSnapshot
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.generation_access import (
    BrokerIdentity,
    GenerationBroker,
    GenerationClient,
    GenerationCredentials,
)


def broker_fixture():
    cfg, old, cap, private, verifier, cert, now = proof()
    identity = BrokerIdentity.create(cert.target, cap.registration)
    broker = GenerationBroker(cfg.binding, cfg.database_lineage, private, origins=('https://www.okx.com',), max_generations=8, max_calls=4096)
    credentials = GenerationCredentials('synthetic-api-key', 'synthetic-secret', 'synthetic-passphrase')
    broker.stage(identity.registration, credentials)
    from uuid import uuid4
    broker.bind_head(identity.registration, uuid4(), 1)
    client = GenerationClient(identity, broker.dispatch, public_key=broker.public_key, local_ready=lambda: True, timeout_ms=1000)
    return cfg, broker, client, identity, credentials, cert


def snapshot(path='/api/v5/account/config', method=RESTMethod.GET, body=None, key='synthetic-api-key'):
    import json
    return RESTRequestSnapshot.capture(RESTRequest(method=method, url='https://www.okx.com' + path,
                                       data=None if body is None else json.dumps(body), headers={'OK-ACCESS-KEY': key}, is_auth_required=True))


@pytest.mark.asyncio
async def test_unarmed_can_get_only_its_credential_and_read_but_cannot_trade():
    cfg, broker, client, identity, credentials, cert = broker_fixture()
    assert await client.credentials() == credentials
    guard = client.request_guard(financial_guard=True)
    assert await guard.authorize(snapshot()) and guard.check(snapshot())
    assert not guard.check(snapshot())  # One original physical request only.
    for path in ('/api/v5/trade/order', '/api/v5/trade/amend-order', '/api/v5/trade/cancel-order', '/api/v5/trade/cancel-all-after'):
        with pytest.raises(StorageError):
            await client.request_guard(financial_guard=True).authorize(snapshot(path, RESTMethod.POST, {'instId': 'LIFE-USDT'}))


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['/api/v5/trade/order', '/api/v5/trade/amend-order', '/api/v5/trade/batch-orders', '/api/v5/trade/cancel-all-after', '/api/v5/users/subaccount/apikey', '/api/v5/asset/withdrawal'])
async def test_recovery_request_allowlist_denies_create_amend_caa_and_privilege_changes(path):
    cfg, broker, client, identity, credentials, cert = broker_fixture()
    broker.enable(identity.registration, mode='RECOVERY_ONLY', expires_at=identity.registration.identity.owner.expires_at)
    with pytest.raises(StorageError):
        await client.request_guard(financial_guard=True).authorize(snapshot(path, RESTMethod.POST, {'instId': 'LIFE-USDT'}))


@pytest.mark.asyncio
async def test_even_active_requests_need_original_financial_guard_and_exact_key_endpoint():
    cfg, broker, client, identity, credentials, cert = broker_fixture()
    broker.enable(identity.registration, mode='ACTIVE', expires_at=identity.registration.identity.owner.expires_at)
    with pytest.raises(StorageError):
        await client.request_guard(financial_guard=False).authorize(snapshot('/api/v5/trade/order', RESTMethod.POST, {'instId': 'LIFE-USDT'}))
    with pytest.raises(StorageError):
        await client.request_guard(financial_guard=True).authorize(snapshot(key='foreign-key'))
    with pytest.raises(StorageError):
        await client.request_guard(financial_guard=True).authorize(replace(snapshot(), url='https://foreign.example/api/v5/account/config'))
    g = client.request_guard(financial_guard=True)
    request = snapshot('/api/v5/trade/order', RESTMethod.POST, {'instId': 'LIFE-USDT'})
    assert await g.authorize(request) and g.check(request)


@pytest.mark.asyncio
async def test_other_identity_cannot_fetch_replacement_resource_or_replay_signed_calls():
    cfg, broker, client, identity, credentials, cert = broker_fixture()
    call = identity.call('FETCH', {'resource': identity.registration.identity.credential_resource})
    await broker.dispatch(call)
    with pytest.raises(StorageError):
        await broker.dispatch(call)
    call = identity.call('FETCH', {'resource': 'secret:successor:1'})
    with pytest.raises(StorageError):
        await broker.dispatch(call)
    assert credentials.secret not in repr(credentials)


@pytest.mark.asyncio
async def test_revoke_during_authorization_and_private_ws_write_deny_without_waiting_for_db():
    cfg, broker, client, identity, credentials, cert = broker_fixture()
    broker.enable(identity.registration, mode='ACTIVE', expires_at=identity.registration.identity.owner.expires_at)
    g = client.request_guard(financial_guard=True)
    request = snapshot('/api/v5/trade/order', RESTMethod.POST, {'instId': 'LIFE-USDT'})
    await g.authorize(request)
    client.stop()
    assert not g.check(request)
    with pytest.raises(StorageError):
        await client.authorize_ws({'op': 'order', 'args': []})


@pytest.mark.asyncio
async def test_parallel_signed_calls_accept_reordering_but_never_duplicate_delivery():
    cfg, broker, client, identity, credentials, cert = broker_fixture()
    first = identity.call('FETCH', {'resource': identity.registration.identity.credential_resource})
    second = identity.call('FETCH', {'resource': identity.registration.identity.credential_resource})
    assert (await broker.dispatch(second)).body['sequence'] == second.sequence
    assert (await broker.dispatch(first)).body['sequence'] == first.sequence
    with pytest.raises(StorageError):
        await broker.dispatch(first)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['signature', 'response_scope', 'timeout', 'revoked_during_reply'])
async def test_client_refuses_tampered_late_or_foreign_rpc_reply(fault):
    import asyncio

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageUnavailable, encode
    from hummingbot.strategy_v2.life_liquidity.storage.generation_access import BrokerReply
    cfg, broker, original, identity, credentials, cert = broker_fixture()

    async def rpc(call):
        reply = await broker.dispatch(call)
        if fault == 'signature':
            return BrokerReply(reply.body, b'0' * 64)
        if fault == 'response_scope':
            body = dict(reply.body, client_id='f' * 64)
            return BrokerReply(body, broker._signer.sign(encode(body)))
        if fault == 'timeout':
            await asyncio.sleep(.05)
        if fault == 'revoked_during_reply':
            client.stop()
        return reply
    client = GenerationClient(identity, rpc, public_key=broker.public_key, local_ready=lambda: True, timeout_ms=10)
    with pytest.raises((StorageError, StorageUnavailable)):
        await client.credentials()
