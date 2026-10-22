"""Original native enrollment, configured SDK installation and fail-closed broker."""
import asyncio
import hashlib
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_managed_service import managed_service
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_ownership import managed
from test.hummingbot.strategy_v2.life_liquidity.storage.test_generation_access import snapshot
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hummingbot.core.data_type.common import TradeType
from hummingbot.core.web_assistant.connections.data_types import RESTMethod
from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorKeys
from hummingbot.strategy_v2.life_liquidity.storage.generation_access import (
    BrokerIdentity,
    GenerationBroker,
    GenerationClient,
    GenerationCredentials,
)
from hummingbot.strategy_v2.life_liquidity.storage.generation_exclusion import (
    CredentialExclusionVerifier,
    GenerationIdentity,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import PgControlRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.generation_broker import (
    PgGenerationBrokerControl,
    bind_private_broker,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.ownership import PgProcessLease, ProcessCapability
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository


def generation_policy(private):
    key = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return dict(protocol='OFFLINE_GENERATION_ACCESS_V1', public_key_sha256=hashlib.sha256(key).hexdigest(), origins=['https://www.okx.com'], max_generations=8, max_calls=4096)


def identity(cfg, registration, *, resource='secret:source:1', service='service:source'):
    return BrokerIdentity.create(GenerationIdentity(registration.owner, service, resource, 'a' * 64), registration)


async def enrollment(database):
    private = Ed25519PrivateKey.generate()
    f, cfg, owner, cap, lease, _, _, keys = await managed(database, configuration_extra={'generation_access': generation_policy(private)})
    await lease.claim('source')
    broker = GenerationBroker(cfg.binding, cfg.database_lineage, private, origins=('https://www.okx.com',), max_generations=8, max_calls=4096)
    worker = identity(cfg, cap.registration)
    broker.stage(worker.registration, GenerationCredentials('key1', 'secret1', 'pass1'))
    client = GenerationClient(worker, broker.dispatch, public_key=broker.public_key, local_ready=lambda: lease.ready, timeout_ms=1000)
    control = PgControlRepository(database[0], f, verifier=keys.verifier())
    return f, cfg, owner, cap, lease, broker, client, PgGenerationBrokerControl(control, broker)


@pytest.mark.asyncio
async def test_native_private_enrollment_is_required_before_key_delivery_and_explicit_arm(database):
    f, cfg, owner, cap, lease, broker, client, control = await enrollment(database)
    try:
        with pytest.raises(StorageError):
            await client.credentials()
        with pytest.raises(StorageError):
            await control.sync(client.identity.registration)
        receipt = await bind_private_broker(f._runtime, client, 'bind-source')
        assert receipt['trading_permission'] is False
        await control.sync(client.identity.registration)
        assert (await client.credentials()).api_key == 'key1'
        assert broker._generations[client.identity.registration.client_id]['mode'] == 'UNARMED'
        await control.sync(client.identity.registration, enable_active=True)
        assert broker._generations[client.identity.registration.client_id]['mode'] == 'ACTIVE'
        with pytest.raises(StorageError):
            await control.sync(client.identity.registration, enable_active=True)
    finally:
        await f.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['public_hash', 'second_client', 'replay', 'lost_commit', 'commit_expired'])
async def test_native_enrollment_denies_public_context_rebinding_and_unknown_commit(database, monkeypatch, fault):
    f, cfg, owner, cap, lease, broker, client, control = await enrollment(database)
    other = None
    try:
        before = await f.snapshot(cfg.binding)
        if fault == 'public_hash':
            other = PgSpotRepository(await PgRuntime.open(cfg))
            copied = ProcessCapability.create(cfg, owner)
            wrong = PgProcessLease(other._runtime, copied)
            with pytest.raises(StorageError):
                await wrong.claim('public-reclaim')
            with pytest.raises(StorageError):
                await bind_private_broker(other._runtime, client, 'bind-public')
        elif fault in ('second_client', 'replay'):
            await bind_private_broker(f._runtime, client, 'bind-once')
            different = GenerationClient(identity(cfg, cap.registration), broker.dispatch, public_key=broker.public_key, local_ready=lambda: lease.ready, timeout_ms=1000)
            with pytest.raises(StorageError):
                await bind_private_broker(f._runtime, different if fault == 'second_client' else client, 'bind-once')
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.process_broker_bindings') == 1
        else:
            original = f._runtime._commit

            async def interrupted(tx):
                if fault == 'lost_commit':
                    await original(tx)
                    raise ConnectionError('synthetic lost binding commit')
                await asyncio.sleep(1.1)
                await original(tx)
            if fault == 'commit_expired':
                # Set an explicitly bounded lease fixture before calling the command.
                # The physical-COMMIT deferred guard must reject expiry.
                async with database[0].transaction():
                    await database[0].execute('SELECT pg_sleep(3.1)')
            monkeypatch.setattr(f._runtime, '_commit', interrupted)
            with pytest.raises((StorageError, CommitUnknown)):
                await bind_private_broker(f._runtime, client, 'uncertain-bind')
            assert not lease.ready
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.process_broker_bindings') == (1 if fault == 'lost_commit' else 0)
            with pytest.raises(StorageError):
                await client.credentials()
        after = await f.snapshot(cfg.binding)
        assert (after.balance, after.budget, after.cut) == (before.balance, before.budget, before.cut)
    finally:
        if other is not None:
            await other.close()
        await f.close()


async def broker_service(database, *, omit=False, provider=None, hedge=False):
    private = Ed25519PrivateKey.generate()
    state = SimpleNamespace(private=private)

    async def factory(service):
        cfg = service.runtime.config
        worker = identity(cfg, service.process_lease.capability.registration)
        broker = GenerationBroker(cfg.binding, cfg.database_lineage, private, origins=('https://www.okx.com',), max_generations=8, max_calls=4096, provider=provider)
        if provider is not None:
            provider.register(worker.registration.identity.credential_resource, GenerationCredentials('synthetic-key', 'synthetic-secret', 'synthetic-pass'))
        broker.stage(worker.registration, GenerationCredentials('synthetic-key', 'synthetic-secret', 'synthetic-pass'))
        client = GenerationClient(worker, broker.dispatch, public_key=broker.public_key, local_ready=service._trading_ready, timeout_ms=1000)
        await bind_private_broker(service.runtime, client, 'service-bind')
        keys = OperatorKeys({'operator': b'x' * 32}, {'operator': frozenset(('ACTIVATE', 'DRAIN', 'RECOVER', 'REVOKE', 'HANDOFF'))})
        control = PgGenerationBrokerControl(PgControlRepository(database[0], service.financial, verifier=keys.verifier()), broker)
        await control.sync(worker.registration)
        state.broker, state.client, state.control = broker, client, control
        return client
    configuration = {'generation_access': generation_policy(private)}
    if provider is not None:
        configuration['credential_authority'] = CredentialExclusionVerifier('generation-provider',
                                                                            private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).policy()
    t, service, native = await managed_service(database, handoff_role=True, hedge=hedge, configuration_extra=configuration,
                                               generation_access_factory=None if omit else factory)
    return t, service, native, state


@pytest.mark.asyncio
async def test_configured_service_installs_gate_before_source_reads_and_requires_both_explicit_arms(database):
    t, service, native, state = await broker_service(database)
    try:
        assert service.process_mode == 'UNARMED'
        assert all(c._web_assistants_factory._generation_access.current is state.client for c in service.connectors.values())
        await service.arm_process()
        # Local arm is insufficient to turn on the independent broker.
        with pytest.raises(StorageError):
            await state.client.request_guard(financial_guard=True).authorize(
                snapshot('/api/v5/trade/order', RESTMethod.POST, {}, key='synthetic-key'))
        await state.control.sync(state.client.identity.registration, enable_active=True)
        await service.quote('broker-backed-quote', TradeType.BUY, 0)
        assert len(t.service_orders) == 1
        service.connectors['okx']._web_assistants_factory._generation_access._access = None
        assert not service._local_ready()
        with pytest.raises((StorageError, PermissionError)):
            await service.connectors['okx']._api_get('/api/v5/account/config', is_auth_required=True)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_configured_generation_policy_requires_explicit_factory_no_silent_fallback(database):
    with pytest.raises(StorageError, match='GENERATION_ACCESS_REQUIRED'):
        await broker_service(database, omit=True)
    assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
