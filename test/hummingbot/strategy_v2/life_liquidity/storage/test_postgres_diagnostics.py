"""Deterministic physical-boundary diagnostics without any database or .env."""
import asyncio
from collections import deque
from dataclasses import FrozenInstanceError, replace
from test.hummingbot.strategy_v2.life_liquidity.storage.test_postgres_runtime_config import configuration

import asyncpg
import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime


class Connection:
    def __init__(self):
        self.fault = None
        self.commit_fault = None
        self.guard_fault = None
        self.rollback_fault = None
        self.replayed = False

    def get_server_pid(self):
        return 321

    def transaction(self, **kwargs):
        return self

    async def start(self):
        pass

    async def execute(self, sql, *args):
        if 'guard_account' in sql and self.guard_fault:
            raise self.guard_fault

    async def fetchval(self, *args):
        if self.fault:
            raise self.fault
        return {'replayed': self.replayed, 'version': 1}

    async def commit(self):
        if self.commit_fault:
            raise self.commit_fault

    def is_closed(self):
        return False

    def is_in_transaction(self):
        return True

    async def rollback(self):
        if self.rollback_fault:
            raise self.rollback_fault

    def terminate(self):
        pass


class Pool:
    def __init__(self, connection):
        self.connection = connection
        self.acquire_fault = self.release_fault = None

    def acquire(self, **kwargs):
        return self

    async def __aenter__(self):
        if self.acquire_fault:
            raise self.acquire_fault
        return self.connection

    async def __aexit__(self, *args):
        if self.release_fault:
            raise self.release_fault


def runtime():
    store = PgRuntime(configuration())
    connection = Connection()
    store._pools = {True: Pool(connection), False: Pool(connection)}
    store._healthy = True

    async def validate(c):
        pass
    store._validate_connection = validate
    return store, connection


async def transaction(store):
    class Owner:
        def json(self):
            return {}
    return await store._transaction('SELECT life_financial.test_command($1)', ('secret-payload',), Owner(), lambda r: r)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault_phase', ['acquire', 'validate', 'execute', 'guard', 'commit', 'parse', 'release'])
async def test_failure_retains_original_phase_without_sensitive_error_text(fault_phase):
    store, c = runtime()
    error = asyncpg.QueryCanceledError('password=private-test-password secret-payload')
    if fault_phase == 'acquire':
        store._pools[False].acquire_fault = error
    elif fault_phase == 'release':
        store._pools[False].release_fault = error
    elif fault_phase == 'validate':
        async def invalid(connection):
            raise error
        store._validate_connection = invalid
    elif fault_phase == 'parse':
        c.replayed = True

        def bad_parse(receipt):
            raise error
        with pytest.raises(CommitUnknown):
            await store._transaction('SELECT life_financial.test_command($1)', ('secret-payload',), None, bad_parse, untrusted_ingress=False)
        # A replay skips the owner guard in this parse-only fixture.
    else:
        setattr(c, {'execute': 'fault', 'guard': 'guard_fault', 'commit': 'commit_fault'}[fault_phase], error)
    if fault_phase != 'parse':
        if fault_phase == 'release':
            assert (await transaction(store))['version'] == 1
        else:
            with pytest.raises(StorageError):
                await transaction(store)
    sample = store.diagnostics[-1]
    assert sample.error_phase == fault_phase and sample.sqlstate == '57014'
    assert sample.error_kind == 'postgres'
    assert sample.backend_pid == (None if fault_phase == 'acquire' else 321)
    assert sample.elapsed_ms >= 0 and all(ms >= 0 for _, ms in sample.phases_ms)
    assert abs(sum(ms for _, ms in sample.phases_ms) - sample.elapsed_ms) < .001
    assert sample.commit_confirmed == (fault_phase in ('parse', 'release'))
    assert sample.commit_attempted == (fault_phase in ('commit', 'parse', 'release'))
    assert not store.healthy
    assert all(secret not in repr(sample) for secret in ('secret-payload', 'private-test-password', 'password='))


@pytest.mark.asyncio
async def test_unknown_commit_rollback_error_cannot_replace_original_failure():
    store, c = runtime()
    c.commit_fault = ConnectionError('private-test-password')
    c.rollback_fault = asyncpg.ConnectionDoesNotExistError('private-test-password')
    with pytest.raises(CommitUnknown):
        await transaction(store)
    sample = store.diagnostics[-1]
    assert sample.error_phase == 'commit' and sample.commit_attempted and not sample.commit_confirmed
    assert sample.sqlstate is None


@pytest.mark.asyncio
async def test_cancelled_waiter_retains_capacity_and_separate_concurrent_trace():
    store, c = runtime()
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(connection):
        entered.set()
        await release.wait()
        return 'confirmed'

    task = asyncio.create_task(store._admit(blocked, safety=False, operation='blocked'))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store._counts[False] == 1 and not store.diagnostics

    async def fast(connection):
        return 'read'
    assert await store._admit(fast, safety=True, operation='read') == 'read'
    release.set()
    await asyncio.gather(*tuple(store._tasks))
    samples = {sample.operation: sample for sample in store.diagnostics}
    assert samples['blocked'].caller_cancelled and samples['blocked'].outcome == 'success'
    assert not samples['read'].caller_cancelled
    assert store._counts == {False: 0, True: 0} and not store.healthy


@pytest.mark.asyncio
async def test_samples_are_bounded_immutable_and_backpressure_is_visible():
    store, c = runtime()
    store._counts[False] = store.config.max_pending
    with pytest.raises(StorageError, match='PG_BACKPRESSURE'):
        await transaction(store)
    assert store.diagnostics[-1].outcome == 'backpressure'
    store._counts[False] = 0

    async def read(connection):
        return 1
    for _ in range(140):
        assert await store._admit(read, safety=True, operation='password=secret') == 1
    assert isinstance(store._diagnostics, deque) and len(store.diagnostics) == 128
    assert all(sample.operation == 'command' for sample in store.diagnostics)
    with pytest.raises(FrozenInstanceError):
        store.diagnostics[-1].outcome = 'modified'


@pytest.mark.asyncio
@pytest.mark.parametrize('during_commit', [False, True])
async def test_outer_physical_deadline_retains_failure_phase_and_uncertainty(during_commit):
    store, c = runtime()
    store.config = replace(store.config, transaction_timeout_ms=30)

    async def blocked(*args):
        await asyncio.Event().wait()
    if during_commit:
        store._commit = blocked
    else:
        c.fetchval = blocked
    with pytest.raises(CommitUnknown if during_commit else StorageError):
        await transaction(store)
    sample = store.diagnostics[-1]
    assert sample.error_kind == 'timeout' and sample.error_phase == ('commit' if during_commit else 'execute')
    assert sample.commit_attempted is during_commit and not sample.commit_confirmed
    assert store._counts == {False: 0, True: 0} and not store.healthy
