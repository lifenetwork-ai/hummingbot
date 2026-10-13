"""Reporting progress is ordered, repairable and independent of money authority."""
import json
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_recorder_repository import setup

import pytest

from hummingbot.model.executors import Executors
from hummingbot.strategy_v2.life_liquidity.storage.contracts import Backend, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.file import open_store
from hummingbot.strategy_v2.life_liquidity.storage.recorder import FileRecorderRepository
from hummingbot.strategy_v2.life_liquidity.storage.recorder_progress import ExecutorProgress
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo


def progress(projection, sequence=0, **updates):
    info = ExecutorInfo.model_validate_json(projection.payload_json)
    info = info.model_copy(update={'status': RunnableStatus.TERMINATED, 'is_active': False,
                                   'net_pnl_quote': D('.1'), 'filled_amount_quote': D('2'),
                                   'custom_info': {**info.custom_info, 'recovery_order_ids': ['wire-1']}, **updates})
    return ExecutorProgress.from_info('progress-' + str(sequence), projection.binding, sequence, info)


@pytest.mark.asyncio
async def test_actual_sqlite_progress_and_old_projection_repair_do_not_rollback_reporting(tmp_path):
    repo, store, _, owner, initial = await setup(tmp_path)
    try:
        original_cut = (await store.read(store.binding)).cut
        await repo.project(initial, original_cut, owner)
        command = progress(initial)
        cut = (await store.read(store.binding)).cut
        receipt = await repo.update_progress(command, cut=cut, owner=owner)
        assert receipt.sequence == 1 and not receipt.replayed
        row = json.loads((await repo.read(store.binding))[0].payload_json)
        assert row['status'] == RunnableStatus.TERMINATED.value and row['custom_info']['recovery_order_ids'] == ['wire-1']
        assert float(row['net_pnl_quote']) == .1
        assert (await repo.update_progress(command, cut=cut, owner=owner)).replayed
        assert (await repo.project(initial, original_cut, owner)).replayed
        assert json.loads((await repo.read(store.binding))[0].payload_json) == row
        assert await repo.pending_progress(store.binding) == ()
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['sequence', 'status', 'config', 'wires', 'uid', 'cut'])
async def test_stale_or_conflicting_reporting_cannot_mutate_projection(tmp_path, fault):
    repo, store, _, owner, initial = await setup(tmp_path)
    try:
        stale = (await store.read(store.binding)).cut
        await repo.project(initial, stale, owner)
        first = progress(initial)
        await repo.update_progress(first, cut=(await store.read(store.binding)).cut, owner=owner)
        before = (await store.read(store.binding)).cut
        info = ExecutorInfo.model_validate_json(first.payload_json)
        if fault == 'status':
            info.status = RunnableStatus.RUNNING
        elif fault == 'config':
            info.config = info.config.model_copy(update={'amount': D('99')})
        elif fault == 'wires':
            info.custom_info['recovery_order_ids'] = []
        elif fault == 'uid':
            info.custom_info['recovery_account_uid'] = '999'
        with pytest.raises(StorageError):
            command = ExecutorProgress.from_info('next', store.binding, 0 if fault == 'sequence' else 1, info)
            await repo.update_progress(command, cut=stale if fault == 'cut' else before, owner=owner)
        assert (await store.read(store.binding)).cut == before
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_unknown_sql_reply_keeps_outbox_and_cold_exact_repair_is_idempotent(tmp_path, monkeypatch, committed):
    repo, store, native, owner, initial = await setup(tmp_path)
    await repo.project(initial, (await store.read(store.binding)).cut, owner)
    command, cut = progress(initial), (await store.read(store.binding)).cut
    original = native.update_executor_info

    def fail(info):
        if committed:
            original(info)
        raise OSError('synthetic reporting reply loss')
    monkeypatch.setattr(native, 'update_executor_info', fail)
    with pytest.raises(StorageError, match='RECORDER_PROGRESS_UNKNOWN'):
        await repo.update_progress(command, cut=cut, owner=owner)
    await store.close()
    monkeypatch.setattr(native, 'update_executor_info', original)
    cold = await open_store(Backend.FILE, path=tmp_path / 'outbox.json', binding=store.binding,
                            limits=store.limits, create=False, clock=lambda: owner.expires_at)
    try:
        restored = FileRecorderRepository(cold, native, 'life')
        pending = await restored.pending_progress(store.binding)
        assert len(pending) == 1 and pending[0].command == command and pending[0].original_cut == cut
        assert (await restored.update_progress(command, cut=cut, owner=owner)).replayed
        assert await restored.pending_progress(store.binding) == ()
        row = json.loads((await restored.read(store.binding))[0].payload_json)
        assert float(row['net_pnl_quote']) == .1 and row['status'] == RunnableStatus.TERMINATED.value
        assert not cold._created_offline
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_historical_progress_repair_never_replaces_later_reporting_version(tmp_path):
    repo, store, _, owner, initial = await setup(tmp_path)
    try:
        await repo.project(initial, (await store.read(store.binding)).cut, owner)
        first, first_cut = progress(initial), (await store.read(store.binding)).cut
        await repo.update_progress(first, cut=first_cut, owner=owner)
        second = progress(initial, 1, net_pnl_quote=D('-2'), filled_amount_quote=D('3'))
        await repo.update_progress(second, cut=(await store.read(store.binding)).cut, owner=owner)
        after = (await store.read(store.binding)).cut
        assert (await repo.update_progress(first, cut=first_cut, owner=owner)).replayed
        assert (await store.read(store.binding)).cut == after
        assert json.loads((await repo.read(store.binding))[0].payload_json)['net_pnl_quote'] == '-2'
        with pytest.raises(StorageError):
            await repo.update_progress(replace(first, payload_json=second.payload_json), cut=first_cut, owner=owner)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_missing_sql_row_is_rebuilt_from_latest_progress_not_initial_snapshot(tmp_path):
    repo, store, native, owner, initial = await setup(tmp_path)
    try:
        original_cut = (await store.read(store.binding)).cut
        await repo.project(initial, original_cut, owner)
        await repo.update_progress(progress(initial), cut=(await store.read(store.binding)).cut, owner=owner)
        before = json.loads((await repo.read(store.binding))[0].payload_json)
        with native.sql_manager.get_new_session() as session:
            session.query(Executors).delete()
            session.commit()
        assert (await repo.project(initial, original_cut, owner)).replayed
        assert json.loads((await repo.read(store.binding))[0].payload_json) == before
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('net_pnl_quote', D('NaN')), ('filled_amount_quote', D('-1')),
                                         ('cum_fees_quote', D('Infinity')), ('timestamp', float('inf'))])
async def test_invalid_report_numbers_are_rejected_before_sql(tmp_path, field, value):
    repo, store, _, owner, initial = await setup(tmp_path)
    try:
        before = (await store.read(store.binding)).cut
        info = ExecutorInfo.model_validate_json(initial.payload_json).model_copy(update={field: value})
        with pytest.raises(StorageError):
            ExecutorProgress.from_info('invalid', store.binding, 0, info)
        assert (await store.read(store.binding)).cut == before and await repo.read(store.binding) == ()
    finally:
        await store.close()
