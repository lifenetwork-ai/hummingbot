"""Held-position reporting retains exact state; SQL never licenses funds."""
import asyncio
import threading
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_recorder_repository import setup

import pytest
from sqlalchemy import event

from hummingbot.core.data_type.common import TradeType
from hummingbot.model.position import Position
from hummingbot.strategy_v2.executors.executor_orchestrator import PositionHold
from hummingbot.strategy_v2.life_liquidity.storage.contracts import Backend, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.file import open_store
from hummingbot.strategy_v2.life_liquidity.storage.recorder import FileRecorderRepository
from hummingbot.strategy_v2.life_liquidity.storage.recorder_positions import HeldPosition, PositionReport


def held():
    result = PositionHold('okx', 'LIFE-USDT', TradeType.BUY)
    result.net_amount_base = D('1.123456789')
    result.avg_entry_price = D('.987654321')
    result.realized_pnl_quote = D('-.123456789')
    result.cum_fees_quote = D('-.000000001')
    result.volume_traded_quote = D('4.123456789')
    result.order_ids = {'wire-1'}
    return result


def report(binding, operation='positions-1', sequence=0, position=None):
    return PositionReport(operation, binding, 'life', sequence, 1000,
                          (HeldPosition.capture(held() if position is None else position, D('1')),))


@pytest.mark.asyncio
async def test_positions_preserve_exact_state_sql_scale_and_historical_replay(tmp_path):
    repo, store, native, owner, _ = await setup(tmp_path)
    try:
        first, cut = report(store.binding), (await store.read(store.binding)).cut
        receipt = await repo.report_positions(first, cut=cut, owner=owner)
        assert receipt.sequence == 1 and not receipt.replayed
        rows = native.get_positions_by_controller('life')
        assert len(rows) == 1 and rows[0].amount == D('1.123456')
        assert rows[0].realized_pnl_quote == D('-.123456')
        state = await repo.positions(store.binding)
        assert state.positions == first.positions and state.sequence == 1
        restored = state.positions[0].restore()
        assert restored.net_amount_base == D('1.123456789')
        assert restored.order_ids == {'wire-1'} and restored.cum_fees_quote == D('-.000000001')
        position = held()
        position.net_amount_base = D('2')
        position.order_ids.add('wire-2')
        second = report(store.binding, 'positions-2', 1, position)
        await repo.report_positions(second, cut=(await store.read(store.binding)).cut, owner=owner)
        assert (await repo.report_positions(first, cut=cut, owner=owner)).replayed
        assert (await repo.positions(store.binding)).positions == second.positions
        assert native.get_positions_by_controller('life')[0].amount == D('2')
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_unknown_position_write_cold_repairs_original_without_activation(tmp_path, monkeypatch, committed):
    repo, store, native, owner, _ = await setup(tmp_path)
    command, cut = report(store.binding), (await store.read(store.binding)).cut
    original = native.project_position_reports

    def fail(*args, **kwargs):
        if committed:
            original(*args, **kwargs)
        raise OSError('synthetic lost position reply')
    monkeypatch.setattr(native, 'project_position_reports', fail)
    with pytest.raises(StorageError, match='RECORDER_POSITIONS_UNKNOWN'):
        await repo.report_positions(command, cut=cut, owner=owner)
    assert store._uncertain
    await store.close()
    monkeypatch.setattr(native, 'project_position_reports', original)
    cold = await open_store(Backend.FILE, path=tmp_path / 'outbox.json', binding=store.binding,
                            limits=store.limits, create=False, clock=lambda: owner.expires_at)
    try:
        recovered = FileRecorderRepository(cold, native, 'life')
        pending = await recovered.pending_positions(cold.binding)
        assert len(pending) == 1 and pending[0].command == command and pending[0].original_cut == cut
        assert (await recovered.report_positions(command, cut=cut, owner=owner)).replayed
        assert (await recovered.positions(cold.binding)).positions == command.positions
        assert await recovered.pending_positions(cold.binding) == ()
        assert not cold._created_offline
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['sequence', 'cut', 'wire', 'controller', 'owner'])
async def test_position_scope_and_order_identity_conflicts_do_not_write(tmp_path, fault):
    repo, store, native, owner, _ = await setup(tmp_path)
    try:
        old_cut = (await store.read(store.binding)).cut
        first = report(store.binding)
        await repo.report_positions(first, cut=old_cut, owner=owner)
        current = (await store.read(store.binding)).cut
        next_report = report(store.binding, 'positions-2', 0 if fault == 'sequence' else 1)
        if fault == 'wire':
            next_report = replace(next_report, positions=(replace(next_report.positions[0], order_ids=()),))
        if fault == 'controller':
            next_report = replace(next_report, controller_id='foreign')
        selected_owner = replace(owner, epoch=owner.epoch + 1) if fault == 'owner' else owner
        with pytest.raises(StorageError):
            await repo.report_positions(next_report, cut=old_cut if fault == 'cut' else current, owner=selected_owner)
        assert (await store.read(store.binding)).cut == current
        assert native.get_positions_by_controller('life')[0].amount == D('1.123456')
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_foreign_or_tampered_sql_positions_are_never_adopted(tmp_path):
    repo, store, native, owner, _ = await setup(tmp_path)
    try:
        first = report(store.binding)
        await repo.report_positions(first, cut=(await store.read(store.binding)).cut, owner=owner)
        with native.sql_manager.get_new_session() as session:
            session.query(Position).one().realized_pnl_quote = D('99')
            session.commit()
        with pytest.raises(StorageError, match='RECORDER_POSITION_NATIVE_UNVERIFIED'):
            await repo.positions(store.binding)
        with pytest.raises(StorageError, match='RECORDER_POSITION_NATIVE_UNVERIFIED'):
            await repo.report_positions(report(store.binding, 'next', 1), cut=(await store.read(store.binding)).cut, owner=owner)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_deleted_position_rows_repair_from_latest_head_without_rolling_back(tmp_path):
    repo, store, native, owner, _ = await setup(tmp_path)
    try:
        first, cut = report(store.binding), (await store.read(store.binding)).cut
        await repo.report_positions(first, cut=cut, owner=owner)
        position = held()
        position.net_amount_base = D('2')
        second = report(store.binding, 'next', 1, position)
        await repo.report_positions(second, cut=(await store.read(store.binding)).cut, owner=owner)
        with native.sql_manager.get_new_session() as session:
            session.query(Position).delete()
            session.commit()
        with pytest.raises(StorageError, match='RECORDER_POSITION_NATIVE_UNVERIFIED'):
            await repo.positions(store.binding)
        assert (await repo.report_positions(first, cut=cut, owner=owner)).replayed
        assert native.get_positions_by_controller('life')[0].amount == D('2')
        assert (await repo.positions(store.binding)).positions == second.positions
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_unknown_legacy_positions_cannot_be_bootstrapped(tmp_path):
    repo, store, native, owner, _ = await setup(tmp_path)
    try:
        command = report(store.binding)
        native.store_position(command.sql_rows()[0])  # No bound durable origin.
        with pytest.raises(StorageError, match='RECORDER_POSITION_NATIVE_UNVERIFIED'):
            await repo.positions(store.binding)
        with pytest.raises(StorageError, match='RECORDER_POSITION_NATIVE_UNVERIFIED'):
            await repo.report_positions(command, cut=(await store.read(store.binding)).cut, owner=owner)
        assert 'recorder_positions' not in (await store.read(store.binding)).resources
    finally:
        await store.close()


@pytest.mark.parametrize('value', [D('NaN'), D('Infinity'), D('100000000000000')])
def test_invalid_or_sql_overflow_position_is_denied_before_enqueue(value):
    position = held()
    position.net_amount_base = value
    with pytest.raises(StorageError):
        HeldPosition.capture(position, D('1'))


@pytest.mark.asyncio
async def test_position_write_cancellation_keeps_worker_owned(tmp_path, monkeypatch):
    repo, store, native, owner, _ = await setup(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = native.project_position_reports

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(native, 'project_position_reports', blocked)
    task = asyncio.create_task(repo.report_positions(report(store.binding), cut=(await store.read(store.binding)).cut, owner=owner))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert store._uncertain and len(store._pending) == 1
    finally:
        release.set()
        await store.close()


@pytest.mark.asyncio
async def test_sql_position_compare_and_write_exclude_concurrent_reporters(tmp_path):
    repo, store, native, _, _ = await setup(tmp_path)
    entered, release, second_started, second_selected = (threading.Event() for _ in range(4))
    first_thread = {'id': None}
    rows = report(store.binding).sql_rows()
    expected_id = rows[0].id  # ORM input expires on commit; retain a scalar assertion.
    competing = report(store.binding, position=held()).sql_rows()
    competing[0].id = 'competing-unqualified-id'

    def before(connection, cursor, statement, parameters, context, executemany):
        if first_thread['id'] is not None and threading.get_ident() != first_thread['id']:
            second_started.set()

    def after(connection, cursor, statement, parameters, context, executemany):
        if 'FROM "Position"' not in statement:
            return
        if first_thread['id'] is None:
            first_thread['id'] = threading.get_ident()
            entered.set()
            assert release.wait(5)
        elif threading.get_ident() != first_thread['id']:
            second_selected.set()

    engine = native.sql_manager.engine
    event.listen(engine, 'before_cursor_execute', before)
    event.listen(engine, 'after_cursor_execute', after)
    first = asyncio.create_task(asyncio.to_thread(native.project_position_reports, 'life', (), rows))
    second = None
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        second = asyncio.create_task(asyncio.to_thread(native.project_position_reports, 'life', (), competing))
        assert await asyncio.to_thread(second_started.wait, 3)
        await asyncio.sleep(.05)
        assert not second_selected.is_set()  # It must wait before reading the old set.
        release.set()
        await first
        with pytest.raises(ValueError, match='RECORDER_POSITION_SQL_CONFLICT'):
            await second
        assert [row.id for row in native.get_positions_by_controller('life')] == [expected_id]
    finally:
        release.set()
        await asyncio.gather(*(task for task in (first, second) if task is not None), return_exceptions=True)
        event.remove(engine, 'before_cursor_execute', before)
        event.remove(engine, 'after_cursor_execute', after)
        await store.close()


@pytest.mark.parametrize('sign', [1, -1])
def test_reporting_truncation_cannot_round_across_a_micro_unit_boundary(sign):
    from hummingbot.strategy_v2.life_liquidity.storage.recorder_positions import sql_value
    value = D('0.000000' + '9' * 58)
    if sign < 0:
        value = value.copy_negate()
    assert sql_value(value) == 0
