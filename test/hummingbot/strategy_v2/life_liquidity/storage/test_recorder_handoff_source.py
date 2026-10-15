"""Scoped SQL source snapshots preserve reporting IDs without adopting money."""
import json
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_recorder_events import (
    capture,
    created,
    fill,
    fixture,
    publish,
)
from test.hummingbot.strategy_v2.life_liquidity.storage.test_recorder_repository import setup

import pytest

from hummingbot.core.event.events import MarketEvent
from hummingbot.model.executors import Executors
from hummingbot.model.order_status import OrderStatus
from hummingbot.model.trade_fill import TradeFill
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError

LIMITS = {'max_rows': 128, 'max_bytes': 1024 * 1024}  # Explicit offline capture capacities.


@pytest.mark.asyncio
async def test_capture_preserves_sql_primary_keys_fees_and_original_namespace(tmp_path):
    repo, store, sql, owner, source = await fixture(tmp_path)
    try:
        await publish(repo, store, owner, capture(store, source, MarketEvent.BuyOrderCreated, created()))
        await publish(repo, store, owner, capture(store, source, MarketEvent.OrderFilled, fill()))
        before = await store.read(store.binding)
        image = await repo.handoff_source(store.binding, **LIMITS)
        tables = json.loads(image.rows_json)['tables']
        assert image.cut == before.cut and image.controller_id == 'life'
        assert len(tables['Executors']) == len(tables['Order']) == len(tables['TradeFill']) == 1
        assert len(tables['OrderStatus']) == 2 and len(tables['MarketState']) == 1
        with sql.sql_manager.get_new_session() as session:
            assert [r['id'] for r in tables['OrderStatus']] == [r.id for r in session.query(OrderStatus).order_by(OrderStatus.id)]
            trade = session.query(TradeFill).one()
            for column in TradeFill.__table__.primary_key.columns:
                assert tables['TradeFill'][0][column.name] == getattr(trade, column.name)
        assert tables['TradeFill'][0]['trade_fee_in_quote'] == '-0.01'
        assert tables['Executors'][0]['custom_info']['life_storage_binding'] == store.binding.json()
        assert (await store.read(store.binding)).cut == before.cut
        assert await repo.handoff_source(store.binding, **LIMITS) == image
        tables['Executors'].clear()
        assert len(json.loads(image.rows_json)['tables']['Executors']) == 1
        assert not hasattr(image, 'send_authorized')
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['missing_executor', 'changed_executor', 'unknown_executor', 'unknown_status', 'changed_trade', 'missing_status'])
async def test_capture_rejects_missing_conflicting_or_unproven_sql_without_repair(tmp_path, fault):
    repo, store, sql, owner, source = await fixture(tmp_path)
    try:
        await publish(repo, store, owner, capture(store, source, MarketEvent.BuyOrderCreated, created()))
        await publish(repo, store, owner, capture(store, source, MarketEvent.OrderFilled, fill()))
        with sql.sql_manager.get_new_session() as session:
            if fault == 'missing_executor':
                session.query(Executors).delete()
            elif fault == 'changed_executor':
                session.query(Executors).one().net_pnl_quote = 99
            elif fault == 'unknown_executor':
                row = session.query(Executors).one()
                values = {c.name: getattr(row, c.name) for c in Executors.__table__.columns}
                session.add(Executors(**{**values, 'id': 'unproven'}))
            elif fault == 'unknown_status':
                session.add(OrderStatus(order_id='wire-1', timestamp=999, status='OrderCancelled'))
            elif fault == 'changed_trade':
                session.query(TradeFill).one().exchange_trade_id = 'changed'
            else:
                session.query(OrderStatus).delete()
            session.commit()
        before = await store.read(store.binding)
        with pytest.raises(StorageError):
            await repo.handoff_source(store.binding, **LIMITS)
        assert (await store.read(store.binding)).cut == before.cut and not store._uncertain
        if fault == 'missing_status':
            with sql.sql_manager.get_new_session() as session:
                assert session.query(OrderStatus).count() == 0
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('limits', [{'max_rows': 1, 'max_bytes': 1024 * 1024}, {'max_rows': 128, 'max_bytes': 64},
                                    {'max_rows': True, 'max_bytes': 1024}, {'max_rows': 128, 'max_bytes': 0}])
async def test_capture_capacities_are_explicit_and_cannot_truncate_a_source(tmp_path, limits):
    repo, store, sql, owner, command = await setup(tmp_path)
    try:
        await repo.project(command, (await store.read(store.binding)).cut, owner)
        if limits['max_rows'] == 1 and type(limits['max_rows']) is int:
            with sql.sql_manager.get_new_session() as session:
                row = session.query(Executors).one()
                values = {c.name: getattr(row, c.name) for c in Executors.__table__.columns}
                session.add(Executors(**{**values, 'id': 'extra'}))
                session.commit()
        before = await store.read(store.binding)
        with pytest.raises(StorageError):
            await repo.handoff_source(store.binding, **limits)
        assert (await store.read(store.binding)).cut == before.cut
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_unrelated_controller_rows_are_excluded_and_foreign_request_denied(tmp_path):
    repo, store, sql, owner, command = await setup(tmp_path)
    try:
        await repo.project(command, (await store.read(store.binding)).cut, owner)
        with sql.sql_manager.get_new_session() as session:
            row = session.query(Executors).one()
            values = {c.name: getattr(row, c.name) for c in Executors.__table__.columns}
            session.add(Executors(**{**values, 'id': 'other', 'controller_id': 'another-controller'}))
            session.commit()
        image = await repo.handoff_source(store.binding, **LIMITS)
        assert len(json.loads(image.rows_json)['tables']['Executors']) == 1
        with pytest.raises(StorageError, match='BINDING_MISMATCH'):
            await repo.handoff_source(replace(store.binding, risk_pool='different'), **LIMITS)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_progress_and_exact_held_position_history_agree_with_scoped_sql(tmp_path):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_recorder_positions import report
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_recorder_progress import progress
    repo, store, sql, owner, initial = await setup(tmp_path)
    try:
        await repo.project(initial, (await store.read(store.binding)).cut, owner)
        update = progress(initial)
        await repo.update_progress(update, cut=(await store.read(store.binding)).cut, owner=owner)
        await repo.report_positions(report(store.binding), cut=(await store.read(store.binding)).cut, owner=owner)
        image = await repo.handoff_source(store.binding, **LIMITS)
        tables = json.loads(image.rows_json)['tables']
        assert tables['Executors'][0]['custom_info']['recovery_order_ids'] == ['wire-1']
        assert float(tables['Executors'][0]['net_pnl_quote']) == .1
        assert tables['Position'][0]['amount'] == '1.123456'
        assert (await repo.positions(store.binding)).positions[0].net_amount_base == D('1.123456789')
        with sql.sql_manager.get_new_session() as session:
            session.query(Executors).one().cum_fees_quote = 9
            session.commit()
        with pytest.raises(StorageError, match='EXECUTOR_UNVERIFIED'):
            await repo.handoff_source(store.binding, **LIMITS)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_one_read_transaction_captures_wal_mode_sql_and_funding_without_dml(tmp_path):
    from sqlalchemy import event

    from hummingbot.core.event.events import FundingPaymentCompletedEvent
    from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import EventSource
    repo, store, sql, owner, source = await fixture(tmp_path)
    engine = sql.sql_manager.engine
    listener = None
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql('PRAGMA journal_mode=WAL').scalar() == 'wal'
        funding_source = EventSource('life', source.config_file_path, source.strategy_name, 'okx_perpetual')
        funding = FundingPaymentCompletedEvent(1700000000000, 'okx_perpetual', 'LIFE-USDT', D('-2'), D('.0001'))
        await publish(repo, store, owner, capture(store, funding_source, MarketEvent.FundingPaymentCompleted, funding))
        commands = []

        def audit(conn, cursor, statement, parameters, context, executemany):
            first = statement.lstrip().split()[0].upper()
            assert first in ('SELECT', 'PRAGMA', 'BEGIN'), statement
            commands.append(first)
        listener = audit
        event.listen(engine, 'before_cursor_execute', listener)
        image = await repo.handoff_source(store.binding, **LIMITS)
        value = json.loads(image.rows_json)
        assert commands.count('BEGIN') == 1
        assert value['tables']['FundingPayment'][0]['amount'] == '-2.0'
        assert value['schema']['OrderStatus'][0]['name'] == 'id'
    finally:
        if listener is not None:
            event.remove(engine, 'before_cursor_execute', listener)
        await store.close()


@pytest.mark.asyncio
async def test_unknown_sql_column_is_not_silently_omitted_from_source(tmp_path):
    repo, store, sql, owner, command = await setup(tmp_path)
    try:
        await repo.project(command, (await store.read(store.binding)).cut, owner)
        with sql.sql_manager.engine.begin() as connection:
            connection.exec_driver_sql('ALTER TABLE Executors ADD COLUMN unsupported_origin TEXT')
        with pytest.raises(StorageError, match='SCHEMA_UNSUPPORTED'):
            await repo.handoff_source(store.binding, **LIMITS)
    finally:
        await store.close()
