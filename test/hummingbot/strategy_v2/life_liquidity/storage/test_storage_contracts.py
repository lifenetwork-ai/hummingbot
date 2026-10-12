"""Backend contracts, initially qualified against the existing PolicyState primitive."""
import asyncio
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import UUID

import pytest
import pytest_asyncio

from hummingbot.strategy_v2.life_liquidity.policy_state import PolicyState
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    Backend,
    CommitUnknown,
    OwnerMode,
    OwnerToken,
    StateBinding,
    StorageConflict,
    StorageError,
    StorageUnavailable,
    UnsupportedBackend,
)
from hummingbot.strategy_v2.life_liquidity.storage.file import FileLimits, open_store

UTC = timezone.utc
NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)


def binding():
    return StateBinding(environment='OFFLINE_SIMULATION', exchange='okx', account_uid='12345',
                        risk_pool='usdt', lineage=UUID(int=1), schema_version=1,
                        policy_hash='a' * 64, config_hash='b' * 64)


def owner():
    return OwnerToken(process_id=UUID(int=2), generation=UUID(int=3), epoch=1,
                      session_id=UUID(int=4), valid_from=NOW, expires_at=NOW + timedelta(hours=1))


def limits():
    # Explicit synthetic test capacity; never an implicit live threshold.
    return FileLimits(max_pending=2, max_checkpoint_bytes=1024 * 1024, max_operations=16)


@pytest_asyncio.fixture
async def store(tmp_path):
    repository = await open_store(Backend.FILE, path=tmp_path / 'contract.json', binding=binding(),
                                  limits=limits(), create=True, initial={'cash': {'usdt': '100.00'}},
                                  owner=owner(), clock=lambda: NOW)
    try:
        yield repository
    finally:
        await repository.close()


@pytest.mark.asyncio
async def test_snapshot_is_deeply_immutable_and_cut_is_not_a_permit(store):
    snapshot = await store.read(binding())
    assert snapshot.cut.revision == 0 and snapshot.cut.event_version == 0
    assert snapshot.owner_mode is OwnerMode.ACTIVE
    with pytest.raises(TypeError):
        snapshot.resources['cash']['usdt'] = '999'
    with pytest.raises(FrozenInstanceError):
        snapshot.cut.revision = 10
    assert not hasattr(snapshot.cut, 'authorized')


@pytest.mark.asyncio
async def test_atomic_multiple_resource_publication_and_idempotent_operation(store):
    snapshot = await store.read(binding())
    work = store.unit_of_work(snapshot.cut, owner())
    work.put('cash', {'usdt': '90.00'})
    work.put('reservation:order1', {'usdt': '10.00', 'state': 'PREPARED'})
    receipt = await work.commit('operation1')
    assert receipt.revision == 1 and not receipt.replayed
    new = await store.read(binding())
    assert new.resources['cash']['usdt'] == '90.00'
    assert new.resources['reservation:order1']['usdt'] == '10.00'
    assert new.cut.revision == new.cut.event_version == 1
    retry = store.unit_of_work(snapshot.cut, owner())
    retry.put('cash', {'usdt': '90.00'})
    retry.put('reservation:order1', {'usdt': '10.00', 'state': 'PREPARED'})
    assert (await retry.commit('operation1')).replayed
    assert (await store.read(binding())).cut == new.cut


@pytest.mark.asyncio
async def test_changed_duplicate_operation_and_stale_cut_cannot_write(store):
    first = await store.read(binding())
    work = store.unit_of_work(first.cut, owner())
    work.put('cash', {'usdt': '90'})
    await work.commit('operation1')
    for operation_id in ['operation1', 'operation2']:
        stale = store.unit_of_work(first.cut, owner())
        stale.put('cash', {'usdt': '80'})
        with pytest.raises(StorageConflict):
            await stale.commit(operation_id)
    assert (await store.read(binding())).resources['cash']['usdt'] == '90'


@pytest.mark.asyncio
async def test_rollback_and_input_mutation_have_no_durable_effect(store):
    snapshot = await store.read(binding())
    mutable = {'items': ['original']}
    work = store.unit_of_work(snapshot.cut, owner())
    work.put('notes', mutable)
    mutable['items'].append('changed')
    await work.commit('operation1')
    assert (await store.read(binding())).resources['notes']['items'] == ('original',)
    fresh = await store.read(binding())
    rolled_back = store.unit_of_work(fresh.cut, owner())
    rolled_back.put('cash', {'usdt': '0'})
    rolled_back.rollback()
    with pytest.raises(StorageError):
        await rolled_back.commit('operation2')
    assert await store.lookup_operation('operation2', binding()) is None
    assert (await store.read(binding())).cut == fresh.cut


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [
    ('environment', 'DEMO'), ('account_uid', '67890'), ('risk_pool', 'other'),
    ('lineage', UUID(int=9)), ('schema_version', 2), ('policy_hash', 'c' * 64), ('config_hash', 'd' * 64),
])
async def test_foreign_binding_denies_read_write_and_readback(store, field, value):
    foreign = replace(binding(), **{field: value})
    with pytest.raises(StorageError):
        await store.read(foreign)
    with pytest.raises(StorageError):
        await store.lookup_operation('operation1', foreign)
    snapshot = await store.read(binding())
    with pytest.raises(StorageError):
        store.unit_of_work(replace(snapshot.cut, binding=foreign), owner())


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [
    ('process_id', UUID(int=8)), ('generation', UUID(int=8)), ('epoch', 2), ('session_id', UUID(int=8)),
    ('expires_at', NOW + timedelta(hours=2)),
])
async def test_foreign_owner_denies_mutation(store, field, value):
    snapshot = await store.read(binding())
    work = store.unit_of_work(snapshot.cut, replace(owner(), **{field: value}))
    work.put('cash', {'usdt': '0'})
    with pytest.raises(StorageError):
        await work.commit('operation1')
    assert (await store.read(binding())).cut == snapshot.cut


@pytest.mark.asyncio
async def test_expiry_after_staging_rejects_commit_and_retains_data(tmp_path):
    time = [NOW]
    repo = await open_store(Backend.FILE, path=tmp_path / 'expiry.json', binding=binding(), limits=limits(),
                            create=True, initial={'cash': '100'}, owner=owner(), clock=lambda: time[0])
    try:
        snapshot = await repo.read(binding())
        work = repo.unit_of_work(snapshot.cut, owner())
        work.put('cash', '0')
        time[0] = owner().expires_at
        with pytest.raises(StorageError):
            await work.commit('expired')
        assert (await repo.read(binding())).cut == snapshot.cut
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_missing_state_never_auto_creates_and_pg_has_no_file_fallback(tmp_path):
    path = tmp_path / 'missing.json'
    with pytest.raises(StorageUnavailable):
        await open_store(Backend.FILE, path=path, binding=binding(), limits=limits())
    assert not path.exists()
    with pytest.raises(UnsupportedBackend):
        await open_store(Backend.POSTGRES, path=path, binding=binding(), limits=limits())
    assert not path.exists()


@pytest.mark.asyncio
async def test_cold_restore_preserves_operation_identity_and_cut(tmp_path, store):
    snapshot = await store.read(binding())
    work = store.unit_of_work(snapshot.cut, owner())
    work.put('cash', {'usdt': '90'})
    await work.commit('operation1')
    expected = await store.read(binding())
    await store.close()
    restored = await open_store(Backend.FILE, path=tmp_path / 'contract.json', binding=binding(),
                                limits=limits(), clock=lambda: NOW)
    try:
        assert (await restored.read(binding())).cut == expected.cut
        receipt = await restored.lookup_operation('operation1', binding())
        assert receipt.revision == 1
        retry = restored.unit_of_work(snapshot.cut, owner())
        retry.put('cash', {'usdt': '90'})
        assert (await retry.commit('operation1')).replayed
    finally:
        await restored.close()


@pytest.mark.asyncio
async def test_uncertain_publish_never_reports_rollback_or_allows_more_writes(tmp_path, store):
    snapshot = await store.read(binding())
    work = store.unit_of_work(snapshot.cut, owner())
    work.put('cash', {'usdt': '90'})
    original = PolicyState.commit

    def lost_reply(self, state):
        original(self, state)
        raise OSError('synthetic loss after durable publication')

    with patch.object(PolicyState, 'commit', lost_reply):
        with pytest.raises(CommitUnknown):
            await work.commit('operation1')
    with pytest.raises(StorageUnavailable):
        await store.read(binding())
    await store.close()
    restored = await open_store(Backend.FILE, path=tmp_path / 'contract.json', binding=binding(),
                                limits=limits(), clock=lambda: NOW)
    try:
        assert (await restored.lookup_operation('operation1', binding())).revision == 1
        assert (await restored.read(binding())).resources['cash']['usdt'] == '90'
    finally:
        await restored.close()


@pytest.mark.asyncio
async def test_external_writer_and_corruption_rejected(tmp_path, store):
    path = tmp_path / 'contract.json'
    raw = path.read_bytes()
    # Valid JSON changed outside the current writer still invalidates its verified memory.
    path.write_bytes(raw.replace(b'100.00', b'900.00'))
    with pytest.raises(StorageUnavailable):
        await store.read(binding())


@pytest.mark.parametrize('field,value', [
    ('account_uid', 'bad'), ('schema_version', True), ('policy_hash', 'short'),
    ('environment', 'unknown'), ('lineage', 'not-uuid'), ('risk_pool', ''),
])
def test_invalid_binding_rejected(field, value):
    with pytest.raises(StorageError):
        replace(binding(), **{field: value})


@pytest.mark.asyncio
async def test_caller_cancellation_keeps_admitted_worker_capacity_until_done(store):
    import threading

    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original = type(store)._read

    def paused(self):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(3)
        return original(self)

    with patch.object(type(store), '_read', paused):
        first = asyncio.create_task(store.read(binding()))
        await entered.wait()
        second = asyncio.create_task(store.read(binding()))
        await asyncio.sleep(0)
        first.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await first
            with pytest.raises(StorageUnavailable, match='BACKPRESSURE'):
                await store.read(binding())
        finally:
            release.set()
            await second
    assert (await store.read(binding())).cut.revision == 0


@pytest.mark.asyncio
async def test_cancelled_commit_can_finish_and_requires_operation_readback(tmp_path, store):
    import threading

    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original = PolicyState.commit

    def paused(self, state):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(3)
        return original(self, state)

    snapshot = await store.read(binding())
    work = store.unit_of_work(snapshot.cut, owner())
    work.put('cash', {'usdt': '90'})
    with patch.object(PolicyState, 'commit', paused):
        task = asyncio.create_task(work.commit('operation1'))
        await entered.wait()
        task.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await task
            with pytest.raises(StorageError):
                work.rollback()
            with pytest.raises(StorageUnavailable):
                await store.read(binding())
        finally:
            release.set()
            await store.close()
    restored = await open_store(Backend.FILE, path=tmp_path / 'contract.json', binding=binding(),
                                limits=limits(), clock=lambda: NOW)
    try:
        assert (await restored.lookup_operation('operation1', binding())).revision == 1
        assert (await restored.read(binding())).resources['cash']['usdt'] == '90'
    finally:
        await restored.close()


@pytest.mark.asyncio
async def test_capacity_exhaustion_does_not_prune_operations_or_partially_write(tmp_path):
    tiny = replace(limits(), max_operations=1)
    repo = await open_store(Backend.FILE, path=tmp_path / 'capacity.json', binding=binding(), limits=tiny,
                            create=True, initial={'cash': '100'}, owner=owner(), clock=lambda: NOW)
    try:
        initial = await repo.read(binding())
        first = repo.unit_of_work(initial.cut, owner())
        first.put('cash', '90')
        await first.commit('operation1')
        snapshot = await repo.read(binding())
        second = repo.unit_of_work(snapshot.cut, owner())
        second.put('cash', '80')
        with pytest.raises(StorageUnavailable, match='CAPACITY'):
            await second.commit('operation2')
        assert (await repo.read(binding())).cut == snapshot.cut
        assert (await repo.lookup_operation('operation1', binding())).revision == 1
        retry = repo.unit_of_work(initial.cut, owner())
        retry.put('cash', '90')
        assert (await retry.commit('operation1')).replayed
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_active_token_not_valid_before_start_or_after_clock_rollback(tmp_path):
    time = [NOW + timedelta(minutes=5)]
    repo = await open_store(Backend.FILE, path=tmp_path / 'clock.json', binding=binding(), limits=limits(),
                            create=True, initial={'cash': '100'}, owner=owner(), clock=lambda: time[0])
    try:
        first = repo.unit_of_work((await repo.read(binding())).cut, owner())
        first.put('cash', '90')
        await first.commit('operation1')
        for at in [NOW, NOW - timedelta(seconds=1)]:
            snapshot = await repo.read(binding())
            work = repo.unit_of_work(snapshot.cut, owner())
            work.put('cash', '0')
            time[0] = at
            with pytest.raises(StorageError):
                await work.commit('operation2')
            assert (await repo.read(binding())).cut == snapshot.cut
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_unarmed_creation_is_readable_but_cannot_mutate(tmp_path):
    repo = await open_store(Backend.FILE, path=tmp_path / 'unarmed.json', binding=binding(), limits=limits(),
                            create=True, initial={'cash': '100'}, clock=lambda: NOW)
    try:
        snapshot = await repo.read(binding())
        assert snapshot.owner_mode is OwnerMode.UNARMED
        assert (await repo.current_owner(binding())).token is None
        work = repo.unit_of_work(snapshot.cut, owner())
        work.put('cash', '0')
        with pytest.raises(StorageError):
            await work.commit('operation1')
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('environment', ['DEMO', 'PRODUCTION'])
async def test_create_cannot_bootstrap_live_or_demo_state(tmp_path, environment):
    with pytest.raises(StorageError, match='OFFLINE_ONLY'):
        await open_store(Backend.FILE, path=tmp_path / 'no-bootstrap.json',
                         binding=replace(binding(), environment=environment), limits=limits(),
                         create=True, initial={'cash': '100'}, owner=owner())
    assert not (tmp_path / 'no-bootstrap.json').exists()


@pytest.mark.asyncio
async def test_invalid_payloads_and_closed_store_cannot_publish(store):
    from decimal import Decimal

    cut = (await store.read(binding())).cut
    for value in [1.2, float('nan'), Decimal('1.0'), {'nested': object()}]:
        with pytest.raises(StorageError):
            store.unit_of_work(cut, owner()).put('cash', value)
    with pytest.raises(AttributeError):
        store.binding = replace(binding(), account_uid='9')
    await store.close()
    with pytest.raises(StorageUnavailable):
        await store.read(binding())


@pytest.mark.asyncio
async def test_two_uows_from_one_cut_have_one_publication(store):
    cut = (await store.read(binding())).cut
    first, second = store.unit_of_work(cut, owner()), store.unit_of_work(cut, owner())
    first.put('cash', '90')
    second.put('cash', '80')
    results = await asyncio.gather(first.commit('one'), second.commit('two'), return_exceptions=True)
    assert sum(isinstance(result, StorageConflict) for result in results) == 1
    assert (await store.read(binding())).cut.revision == 1
    receipts = [await store.lookup_operation(key, binding()) for key in ('one', 'two')]
    assert sum(receipt is not None for receipt in receipts) == 1


@pytest.mark.asyncio
async def test_expiry_while_waiting_for_worker_prevents_publication(tmp_path):
    import threading

    time = [NOW]
    repo = await open_store(Backend.FILE, path=tmp_path / 'queued-expiry.json', binding=binding(), limits=limits(),
                            create=True, initial={'cash': '100'}, owner=owner(), clock=lambda: time[0])
    snapshot = await repo.read(binding())
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original = type(repo)._read

    def paused(self):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(3)
        return original(self)

    try:
        with patch.object(type(repo), '_read', paused):
            read = asyncio.create_task(repo.read(binding()))
            await entered.wait()
            work = repo.unit_of_work(snapshot.cut, owner())
            work.put('cash', '0')
            commit = asyncio.create_task(work.commit('expired'))
            await asyncio.sleep(0)
            time[0] = owner().expires_at
            release.set()
            await read
            with pytest.raises(StorageError):
                await commit
        assert (await repo.read(binding())).cut == snapshot.cut
        assert await repo.lookup_operation('expired', binding()) is None
    finally:
        release.set()
        await repo.close()


@pytest.mark.asyncio
async def test_oversized_publication_preserves_previous_checkpoint(store):
    snapshot = await store.read(binding())
    work = store.unit_of_work(snapshot.cut, owner())
    work.put('oversized', 'x' * limits().max_checkpoint_bytes)
    with pytest.raises(StorageUnavailable, match='CAPACITY'):
        await work.commit('oversized')
    assert (await store.read(binding())).cut == snapshot.cut


@pytest.mark.asyncio
async def test_foreign_policy_restore_and_symlink_denied(tmp_path, store):
    path = tmp_path / 'contract.json'
    with pytest.raises(StorageUnavailable):
        await open_store(Backend.FILE, path=path, binding=replace(binding(), config_hash='e' * 64),
                         limits=limits(), clock=lambda: NOW)
    link = tmp_path / 'link.json'
    link.symlink_to(path)
    with pytest.raises(StorageUnavailable):
        await open_store(Backend.FILE, path=link, binding=binding(), limits=limits())


@pytest.mark.parametrize('field,value', [
    ('valid_from', NOW.replace(tzinfo=None)), ('expires_at', NOW.replace(tzinfo=None)),
    ('valid_from', NOW + timedelta(hours=2)), ('epoch', True),
])
def test_invalid_owner_time_and_epoch_rejected(field, value):
    with pytest.raises(StorageError):
        replace(owner(), **{field: value})


@pytest.mark.asyncio
async def test_non_active_modes_reject_writes_on_cold_restore(tmp_path):
    import json

    for mode in [OwnerMode.DRAINING, OwnerMode.RECOVERY_ONLY]:
        path = tmp_path / f'{mode.value}.json'
        repo = await open_store(Backend.FILE, path=path, binding=binding(), limits=limits(),
                                create=True, initial={'cash': '100'}, owner=owner(), clock=lambda: NOW)
        await repo.close()
        # Test-only cold fixture construction. Runtime does not expose grant issuance.
        data = json.loads(path.read_text())
        data['state']['ownership']['mode'] = mode.value
        path.write_text(json.dumps(data, sort_keys=True, separators=(',', ':')))
        restored = await open_store(Backend.FILE, path=path, binding=binding(), limits=limits(), clock=lambda: NOW)
        try:
            cut = (await restored.read(binding())).cut
            work = restored.unit_of_work(cut, owner())
            work.put('cash', '0')
            with pytest.raises(StorageError):
                await work.commit('denied')
            assert (await restored.read(binding())).cut == cut
        finally:
            await restored.close()


@pytest.mark.asyncio
async def test_cancelled_close_still_refuses_new_work_and_drains_admitted_read(store):
    import threading

    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original = type(store)._read

    def paused(self):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(3)
        return original(self)

    with patch.object(type(store), '_read', paused):
        read = asyncio.create_task(store.read(binding()))
        await entered.wait()
        close = asyncio.create_task(store.close())
        await asyncio.sleep(0)
        close.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await close
            with pytest.raises(StorageUnavailable):
                await store.read(binding())
        finally:
            release.set()
            await read
            await store.close()
