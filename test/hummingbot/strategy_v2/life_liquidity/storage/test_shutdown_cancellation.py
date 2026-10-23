"""Exact broker identities remain usable after local admission is revoked."""
import asyncio
import json
from datetime import datetime, timezone
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_shared_sdk_quota import sdk_pair
from test.hummingbot.strategy_v2.life_liquidity.storage.test_generation_access import broker_fixture
from uuid import uuid4

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.cancellation_manifest import (
    CancellationIdentity,
    CancellationManifest,
)
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.generation_access import GenerationClient, GenerationCredentials
from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_sdk_quota import PgSharedSdkQuota


def manifest(cfg, identity, products=('SPOT', 'SWAP')):
    owner = identity.registration.identity.owner
    entries = tuple(CancellationIdentity('cancel:' + str(i), p, 'LIFE-USDT' + ('-SWAP' if p == 'SWAP' else ''),
                                         'owned' + str(i), str(i + 1), owner.process_id, owner.generation,
                                         owner.session_id, owner.epoch) for i, p in enumerate(products))
    return CancellationManifest(cfg.binding, cfg.database_lineage, cfg.account_id, 1, 'a' * 64,
                                datetime.now(timezone.utc), entries)


@pytest.mark.asyncio
async def test_signed_shutdown_list_is_exact_bounded_and_not_a_write_grant():
    cfg, broker, client, identity, credentials, _ = broker_fixture()
    original = manifest(cfg, identity)
    broker.pin_manifest(identity.registration, original)
    assert await client.cancellation_identities(max_cancels=2) == original.identities
    with pytest.raises(StorageError, match='BOUND'):
        await client.cancellation_identities(max_cancels=1)
    with pytest.raises(StorageError):
        await broker.dispatch(identity.call('CANCEL_MANIFEST', dict(max_cancels=2, identities=[])))
    assert broker._entry(identity.registration)['mode'] == 'UNARMED'
    broker._current = uuid4().hex
    with pytest.raises(StorageError, match='NOT_CURRENT'):
        await client.cancellation_identities(max_cancels=2)


@pytest.mark.asyncio
@pytest.mark.parametrize('product', ['SPOT', 'SWAP'])
async def test_exact_cancel_passes_stuck_collection_but_preserves_rate_budget_and_guards(product):
    cfg, broker, _, identity, _, _ = broker_fixture()
    spot, swap, session = sdk_pair()
    broker._entry(identity.registration)['credentials'] = GenerationCredentials('synthetic-key', 'synthetic-secret', 'synthetic-pass')
    client = GenerationClient(identity, broker.dispatch, public_key=broker.public_key, local_ready=lambda: False, timeout_ms=1000)
    quota = PgSharedSdkQuota.install(spot=spot, swap=swap)
    connectors = dict(okx=spot, okx_perpetual=swap)
    await client.install(connectors)
    original = manifest(cfg, identity, (product,))
    broker.pin_manifest(identity.registration, original)
    cancel = original.identities[0]
    request = session.request

    async def http(**kwargs):
        response = await request(**kwargs)
        if kwargs.get('data'):
            body = json.loads(kwargs['data'])

            async def data():
                return dict(code='0', data=[dict(sCode='0', ordId=body['ordId'], clOrdId=body['clOrdId'])])
            response.json = data
        return response

    session.request = http
    connector = spot if product == 'SPOT' else swap
    # Model an already accepted HTTP call retaining the scheduling mutex.
    await quota._collection_lock.acquire()
    try:
        before = len(quota._shared._task_logs)
        with pytest.raises(StorageError, match='SDK_BINDING_REQUIRED'):
            await client.cancel_exact(swap if product == 'SPOT' else spot, cancel)
        assert await asyncio.wait_for(client.cancel_exact(connector, cancel), .5)
        assert quota._collection_lock.locked()
        assert len(quota._shared._task_logs) == before + 1
        assert json.loads(session.sent[-1]['data']) == dict(instId=cancel.instrument, clOrdId=cancel.wire_id, ordId=cancel.exchange_order_id)
        broker._entry(identity.registration)['manifest'] = None
        sent = len(session.sent)
        with pytest.raises(StorageError, match='EXACT_CANCEL'):
            await asyncio.wait_for(client.cancel_exact(connector, cancel), .5)
        assert len(session.sent) == sent
    finally:
        quota._collection_lock.release()


@pytest.mark.asyncio
async def test_emergency_lane_is_same_task_cancel_only_and_never_erases_rate_capacity():
    import time

    from hummingbot.core.api_throttler.data_types import TaskLog

    cfg, broker, _, identity, _, _ = broker_fixture()
    spot, swap, session = sdk_pair()
    broker._entry(identity.registration)['credentials'] = GenerationCredentials('synthetic-key', 'synthetic-secret', 'synthetic-pass')
    client = GenerationClient(identity, broker.dispatch, public_key=broker.public_key, local_ready=lambda: False, timeout_ms=1000)
    quota = PgSharedSdkQuota.install(spot=spot, swap=swap)
    await client.install(dict(okx=spot, okx_perpetual=swap))
    broker.pin_manifest(identity.registration, manifest(cfg, identity, ('SPOT',)))
    entry = (await client.cancellation_identities(max_cancels=1))[0]
    before = len(session.sent)
    async with quota.emergency_cancellation(client, spot):
        for route in ('/api/v5/trade/order', '/api/v5/account/config'):
            with pytest.raises(StorageError, match='CANCEL_CONTEXT_INVALID'):
                await spot._api_post(route, data={}, is_auth_required=True)
        # ContextVar inheritance is not delegation to another task.
        with pytest.raises(StorageError, match='CANCEL_CONTEXT_INVALID'):
            await asyncio.create_task(spot._api_post('/api/v5/trade/cancel-order', data={}, is_auth_required=True))
    assert len(session.sent) == before
    limit = quota._shared._id_to_limit_map['/api/v5/trade/cancel-order']
    occupied = TaskLog(timestamp=time.time(), rate_limit=limit, weight=limit.limit)
    quota._shared._task_logs.append(occupied)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(client.cancel_exact(spot, entry), .05)
    assert occupied in quota._shared._task_logs and len(session.sent) == before
