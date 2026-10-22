"""Two OS processes cannot share private broker identity or replacement secrets."""
import asyncio
import hashlib
import multiprocessing
import pickle
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from test.hummingbot.strategy_v2.life_liquidity.storage.generation_worker import run
from test.hummingbot.strategy_v2.life_liquidity.storage.offline_generation_provider import OfflineGenerationProvider
from test.hummingbot.strategy_v2.life_liquidity.storage.test_generation_exclusion import proof
from uuid import uuid4

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.generation_access import GenerationBroker, GenerationCredentials
from hummingbot.strategy_v2.life_liquidity.storage.postgres.ownership import ProcessCapability


async def receive(pipe):
    assert await asyncio.to_thread(pipe.poll, 10), 'bounded process fixture timeout'
    return pipe.recv()


@pytest.mark.asyncio
@pytest.mark.parametrize('revision', ['same', 'new'])
async def test_separate_worker_private_identities_and_replacement_secret_exclusion(revision):
    cfg, source_owner, cap, signer, verifier, certificate, now = proof()
    first = certificate.target
    new_owner = replace(first.owner, process_id=uuid4(), generation=uuid4())
    second = replace(first, owner=new_owner, service_identity='service:second', credential_resource='secret:second:1',
                     image_digest=first.image_digest if revision == 'same' else 'f' * 64)
    second_cap = ProcessCapability.create(cfg, new_owner)
    provider = OfflineGenerationProvider()
    broker = GenerationBroker(cfg.binding, cfg.database_lineage, signer, origins=('https://www.okx.com',), max_generations=8, max_calls=100, provider=provider)
    context = multiprocessing.get_context('spawn')
    processes, pipes, registrations = [], [], []

    async def fetch(index, resource, *, denied=False):
        pipes[index].send(('FETCH', dict(resource=resource)))
        call = await receive(pipes[index])
        try:
            reply = await broker.dispatch(call)
        except StorageError:
            reply = None
        pipes[index].send(reply)
        response = await receive(pipes[index])
        assert response[0] == ('denied' if denied else 'fetched')
        return response
    try:
        for public, registration in ((first, cap.registration), (second, second_cap.registration)):
            parent, child = context.Pipe()
            process = context.Process(target=run, args=(child, public, registration))
            process.start()
            child.close()
            processes.append(process)
            pipes.append(parent)
            registrations.append(await receive(parent))
        credentials = [GenerationCredentials('source-key', 'source-secret', 'source-pass'), GenerationCredentials('target-key', 'target-secret', 'target-pass')]
        for reg, secret in zip(registrations, credentials):
            broker.stage(reg, secret)
            provider.register(reg.identity.credential_resource, secret)
        broker.bind_head(registrations[0], uuid4(), 1)
        broker.enable(registrations[0], mode='ACTIVE', expires_at=first.owner.expires_at)
        assert (await fetch(0, first.credential_resource))[1] == hashlib.sha256(credentials[0].api_key.encode()).hexdigest()
        await fetch(1, second.credential_resource, denied=True)
        await fetch(0, second.credential_resource, denied=True)
        await broker.begin_exclusion(registrations[0])
        with pytest.raises(StorageError):
            await broker.certify(*registrations, expires_at=datetime.now(timezone.utc) + timedelta(seconds=5))
        provider.effect(first.credential_resource)
        certificate = await broker.certify(*registrations, expires_at=datetime.now(timezone.utc) + timedelta(seconds=5))
        broker.admit(registrations[1], certificate, recovery=True)
        broker.bind_head(registrations[1], uuid4(), 2)
        await fetch(0, second.credential_resource, denied=True)
        await fetch(0, first.credential_resource, denied=True)
        assert (await fetch(1, second.credential_resource))[1] == hashlib.sha256(credentials[1].api_key.encode()).hexdigest()
        pipes[0].send(('ARM', {}))
        assert (await receive(pipes[0]))[0] == 'local-denied'
        with pytest.raises(StorageError):
            pickle.dumps(broker)
        assert all(p.is_alive() for p in processes)
    finally:
        for pipe in pipes:
            pipe.send(None)
        for process in processes:
            await asyncio.to_thread(process.join, 5)
            if process.is_alive():
                process.terminate()
                await asyncio.to_thread(process.join, 5)
            assert process.exitcode == 0
        for pipe in pipes:
            pipe.close()
