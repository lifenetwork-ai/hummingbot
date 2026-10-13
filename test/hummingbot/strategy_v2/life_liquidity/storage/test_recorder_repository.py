"""Actual file-backed MarketsRecorder; projection is not financial authority."""
import json
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import CAPACITY, create_fixture
from test.hummingbot.strategy_v2.life_liquidity.test_executor_protected_send import _setup
from test.hummingbot.strategy_v2.life_liquidity.test_recorder_cold_restart import _recorder

import pytest

from hummingbot.model.executors import Executors
from hummingbot.strategy_v2.life_liquidity.storage.contracts import Backend, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.file import open_store
from hummingbot.strategy_v2.life_liquidity.storage.recorder import ExecutorProjection, FileRecorderRepository


async def setup(tmp_path):
    _, executor, _, _, _, _ = _setup(tmp_path / 'native', recovery_account_uid='12345')
    executor.config = executor.config.model_copy(update={'timestamp': 1.0})
    _, binding, owner = create_fixture(tmp_path / 'spot')
    info = executor.executor_info.model_copy(update={'controller_id': 'life'})
    command = ExecutorProjection.from_info('projection-1', binding, info)
    store = await open_store(Backend.FILE, path=tmp_path / 'outbox.json', binding=binding,
                             limits=CAPACITY, create=True, initial={}, owner=owner, clock=lambda: owner.valid_from)
    recorder = _recorder(tmp_path / 'recorder.sqlite')
    repo = FileRecorderRepository(store, recorder, 'life')
    return repo, store, recorder, owner, command


@pytest.mark.asyncio
async def test_actual_recorder_projection_read_and_missing_row_repair(tmp_path):
    repo, store, native, owner, command = await setup(tmp_path)
    try:
        cut = (await store.read(store.binding)).cut
        receipt = await repo.project(command, cut, owner)
        assert not receipt.replayed
        rows = await repo.read(store.binding)
        assert len(rows) == 1 and rows[0].executor_id == command.executor_id
        assert json.loads(rows[0].payload_json)['custom_info']['recovery_account_uid'] == '12345'
        with native.sql_manager.get_new_session() as session:
            session.query(Executors).delete()
            session.commit()
        assert await repo.read(store.binding) == ()
        repaired = await repo.project(command, cut, owner)
        assert repaired.replayed
        assert len(await repo.read(store.binding)) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_recorder_failure_retains_pending_outbox_and_cold_repair_never_rearms(tmp_path, monkeypatch):
    repo, store, native, owner, command = await setup(tmp_path)
    cut = (await store.read(store.binding)).cut
    original = native.project_executor_info
    monkeypatch.setattr(native, 'project_executor_info', lambda info: (_ for _ in ()).throw(OSError('synthetic')))
    with pytest.raises(StorageError, match='RECORDER_PROJECTION_UNKNOWN'):
        await repo.project(command, cut, owner)
    await store.close()
    cold = await open_store(Backend.FILE, path=tmp_path / 'outbox.json', binding=store.binding,
                            limits=CAPACITY, create=False, clock=lambda: owner.expires_at)
    monkeypatch.setattr(native, 'project_executor_info', original)
    try:
        restored = FileRecorderRepository(cold, native, 'life')
        assert (await restored.project(command, cut, owner)).replayed
        assert len(await restored.read(store.binding)) == 1
        assert not cold._created_offline
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_recorder_scope_forgery_and_conflicting_existing_projection_are_denied(tmp_path):
    repo, store, native, owner, command = await setup(tmp_path)
    try:
        cut = (await store.read(store.binding)).cut
        data = json.loads(command.payload_json)
        data['custom_info']['recovery_account_uid'] = '999'
        with pytest.raises(StorageError, match='RECORDER_ACCOUNT_OR_IDENTITY_MISMATCH'):
            ExecutorProjection('foreign', store.binding, json.dumps(data))
        await repo.project(command, cut, owner)
        with native.sql_manager.get_new_session() as session:
            row = session.query(Executors).one()
            row.custom_info = {**row.custom_info, 'recovery_account_uid': '999'}
            session.commit()
        with pytest.raises(StorageError, match='RECORDER_PROJECTION_UNKNOWN'):
            await repo.project(command, cut, owner)
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['noop', 'commit_then_fail'])
async def test_pending_projection_readback_handles_missing_or_already_committed_rows(tmp_path, monkeypatch, failure):
    repo, store, native, owner, command = await setup(tmp_path)
    cut = (await store.read(store.binding)).cut
    original = native.project_executor_info

    def failed(info):
        if failure == 'commit_then_fail':
            original(info)
            raise OSError('synthetic acknowledgement loss')

    monkeypatch.setattr(native, 'project_executor_info', failed)
    with pytest.raises(StorageError, match='RECORDER_PROJECTION_UNKNOWN'):
        await repo.project(command, cut, owner)
    await store.close()
    monkeypatch.setattr(native, 'project_executor_info', original)
    cold = await open_store(Backend.FILE, path=tmp_path / 'outbox.json', binding=store.binding,
                            limits=CAPACITY, create=False, clock=lambda: owner.expires_at)
    try:
        restored = FileRecorderRepository(cold, native, 'life')
        assert (await restored.project(command, cut, owner)).replayed
        assert len(native.get_executors_by_controller('life')) == 1
        assert (await cold.read(store.binding)).resources['recorder_outbox']['projection-1']['state'] == 'APPLIED'
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_recorder_repair_does_not_roll_back_later_independent_reporting_progress(tmp_path):
    from hummingbot.strategy_v2.models.base import RunnableStatus
    repo, store, native, owner, command = await setup(tmp_path)
    try:
        cut = (await store.read(store.binding)).cut
        await repo.project(command, cut, owner)
        with native.sql_manager.get_new_session() as session:
            row = session.query(Executors).one()
            row.status = RunnableStatus.TERMINATED.value
            row.is_active = False
            row.net_pnl_quote = 42.0
            session.commit()
        assert (await repo.project(command, cut, owner)).replayed
        data = json.loads((await repo.read(store.binding))[0].payload_json)
        assert data['status'] == RunnableStatus.TERMINATED.value
        assert data['net_pnl_quote'] == '42' and not data['is_active']
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_recorder_repository_alias_and_operation_conflict_are_denied(tmp_path):
    from dataclasses import replace
    repo, store, native, owner, command = await setup(tmp_path)
    try:
        cut = (await store.read(store.binding)).cut
        await repo.project(command, cut, owner)
        data = json.loads(command.payload_json)
        data['net_pnl_quote'] = '1'
        changed = replace(command, payload_json=json.dumps(data))
        with pytest.raises(StorageError, match='RECORDER_OPERATION_CONFLICT'):
            await repo.project(changed, cut, owner)
        foreign = FileRecorderRepository(store, _recorder(tmp_path / 'other.sqlite'), 'life')
        with pytest.raises(StorageError, match='RECORDER_REPOSITORY_BINDING_MISMATCH'):
            await foreign.read(store.binding)
    finally:
        await store.close()
