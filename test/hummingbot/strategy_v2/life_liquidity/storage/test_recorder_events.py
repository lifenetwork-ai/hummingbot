"""Actual SQLite event reporting is independent of financial observations."""
import json
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_recorder_repository import setup

import pytest

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount
from hummingbot.core.event.events import (
    BuyOrderCreatedEvent,
    FundingPaymentCompletedEvent,
    MarketEvent,
    OrderCancelledEvent,
    OrderFilledEvent,
)
from hummingbot.model.funding_payment import FundingPayment
from hummingbot.model.order import Order
from hummingbot.model.order_status import OrderStatus
from hummingbot.model.trade_fill import TradeFill
from hummingbot.strategy_v2.life_liquidity.storage.contracts import Backend, StorageError, encode, thaw
from hummingbot.strategy_v2.life_liquidity.storage.file import open_store
from hummingbot.strategy_v2.life_liquidity.storage.recorder import ExecutorProjection, FileRecorderRepository
from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import EventProjection, EventSource
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo


async def fixture(tmp_path):
    repo, store, native, owner, projection = await setup(tmp_path)
    info = ExecutorInfo.model_validate_json(projection.payload_json)
    info.custom_info['recovery_order_ids'] = ['wire-1']
    projection = ExecutorProjection.from_info(projection.operation_id, store.binding, info)
    await repo.project(projection, (await store.read(store.binding)).cut, owner)
    source = EventSource('life', native.config_file_path, native.strategy_name, 'okx')
    return repo, store, native, owner, source


def created():
    return BuyOrderCreatedEvent(2, OrderType.LIMIT_MAKER, 'LIFE-USDT', D('2'), D('1'), 'wire-1', 1, 'exchange-1')


def fill():
    return OrderFilledEvent(3, 'wire-1', 'LIFE-USDT', TradeType.BUY, OrderType.LIMIT_MAKER, D('1'), D('1'),
                            AddedToCostTradeFee(flat_fees=[TokenAmount('USDT', D('-.01'))]), 'trade-1', 'exchange-1')


def capture(store, source, tag, event, tracking=None):
    return EventProjection.capture(store.binding, source, tag.value, event, {} if tracking is None else tracking)


async def publish(repo, store, owner, command):
    return await repo.project_event(command, cut=(await store.read(store.binding)).cut, owner=owner)


@pytest.mark.asyncio
async def test_actual_order_fill_status_are_deduplicated_and_keep_signed_reporting_fee(tmp_path):
    repo, store, native, owner, source = await fixture(tmp_path)
    try:
        command = capture(store, source, MarketEvent.BuyOrderCreated, created())
        cut = (await store.read(store.binding)).cut
        await repo.project_event(command, cut=cut, owner=owner)
        await publish(repo, store, owner, capture(store, source, MarketEvent.OrderFilled, fill()))
        await publish(repo, store, owner, capture(store, source, MarketEvent.OrderCancelled, OrderCancelledEvent(4, 'wire-1')))
        assert (await repo.project_event(command, cut=cut, owner=owner)).replayed
        with native.sql_manager.get_new_session() as session:
            assert session.query(Order).one().last_status == 'OrderCancelled'
            assert session.query(OrderStatus).count() == 3
            trade = session.query(TradeFill).one()
            assert trade.trade_fee_in_quote == D('-.01')
            assert trade.exchange_trade_id == 'trade-1'
        # No reservation, budget or exchange-state transition is created by SQL.
        assert 'spot_operations' not in (await store.read(store.binding)).resources
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_fill_before_create_and_late_create_keep_later_order_status(tmp_path):
    repo, store, native, owner, source = await fixture(tmp_path)
    try:
        await publish(repo, store, owner, capture(store, source, MarketEvent.OrderFilled, fill()))
        await publish(repo, store, owner, capture(store, source, MarketEvent.BuyOrderCreated, created()))
        with native.sql_manager.get_new_session() as session:
            order = session.query(Order).one()
            assert order.last_status == 'OrderFilled' and order.last_update_timestamp == 3000
            assert session.query(TradeFill).count() == 1
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_unknown_event_reply_exact_cold_repair_never_duplicates_or_rearms(tmp_path, monkeypatch, committed):
    repo, store, native, owner, source = await fixture(tmp_path)
    command = capture(store, source, MarketEvent.OrderFilled, fill())
    cut = (await store.read(store.binding)).cut
    original = native.project_market_event

    def fail(effect):
        if committed:
            original(effect)
        raise OSError('synthetic lost SQL reply')
    monkeypatch.setattr(native, 'project_market_event', fail)
    with pytest.raises(StorageError, match='RECORDER_EVENT_UNKNOWN'):
        await repo.project_event(command, cut=cut, owner=owner)
    await store.close()
    monkeypatch.setattr(native, 'project_market_event', original)
    cold = await open_store(Backend.FILE, path=tmp_path / 'outbox.json', binding=store.binding,
                            limits=store.limits, create=False, clock=lambda: owner.expires_at)
    try:
        recovered = FileRecorderRepository(cold, native, 'life')
        pending = await recovered.pending_events(cold.binding)
        assert len(pending) == 1 and pending[0].command == command and pending[0].original_cut == cut
        assert (await recovered.project_event(command, cut=cut, owner=owner)).replayed
        with native.sql_manager.get_new_session() as session:
            assert session.query(TradeFill).count() == 1 and session.query(OrderStatus).count() == 1
        assert await recovered.pending_events(cold.binding) == () and not cold._created_offline
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['wire', 'source', 'amount', 'cut', 'owner'])
async def test_foreign_conflicting_or_stale_events_are_denied_before_sql(tmp_path, fault):
    repo, store, native, owner, source = await fixture(tmp_path)
    try:
        stale = (await store.read(store.binding)).cut
        command = capture(store, source, MarketEvent.OrderFilled, fill())
        await publish(repo, store, owner, command)
        current = (await store.read(store.binding)).cut
        evt = fill()
        if fault == 'wire':
            evt = evt._replace(order_id='foreign')
        if fault == 'source':
            source = replace(source, config_file_path='foreign')
        if fault == 'amount':
            evt = evt._replace(amount=D('2'))
        if fault in ('cut', 'owner'):
            evt = evt._replace(exchange_trade_id='next-trade')
        selected = replace(owner, epoch=owner.epoch + 1) if fault == 'owner' else owner
        with pytest.raises(StorageError):
            await repo.project_event(capture(store, source, MarketEvent.OrderFilled, evt),
                                     cut=stale if fault == 'cut' else current, owner=selected)
        assert (await store.read(store.binding)).cut == current
        with native.sql_manager.get_new_session() as session:
            assert session.query(TradeFill).count() == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_native_noop_cannot_publish_event_receipt(tmp_path, monkeypatch):
    repo, store, native, owner, source = await fixture(tmp_path)
    monkeypatch.setattr(native, 'project_market_event', lambda effect: None)
    try:
        with pytest.raises(StorageError, match='RECORDER_EVENT_UNKNOWN'):
            await publish(repo, store, owner, capture(store, source, MarketEvent.OrderFilled, fill()))
        assert store._uncertain
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_okx_funding_millisecond_timestamp_and_signed_payment_are_reported_once(tmp_path):
    repo, store, native, owner, source = await fixture(tmp_path)
    source = replace(source, market='okx_perpetual')
    evt = FundingPaymentCompletedEvent(1700000000000, 'okx_perpetual', 'LIFE-USDT', D('-2'), D('.0001'))
    try:
        command = capture(store, source, MarketEvent.FundingPaymentCompleted, evt)
        assert json.loads(command.payload_json)['timestamp_ms'] == evt.timestamp
        cut = (await store.read(store.binding)).cut
        await repo.project_event(command, cut=cut, owner=owner)
        assert (await repo.project_event(command, cut=cut, owner=owner)).replayed
        with native.sql_manager.get_new_session() as session:
            payment = session.query(FundingPayment).one()
            assert payment.timestamp == evt.timestamp and payment.amount == -2
        assert (await repo.connector_history(source, store.binding)).trade_ids == ()
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['effect', 'partial_sql'])
async def test_pending_event_cannot_repair_forged_effects_or_mixed_sql_outcomes(tmp_path, monkeypatch, fault):
    repo, store, native, owner, source = await fixture(tmp_path)
    command = capture(store, source, MarketEvent.OrderFilled, fill())
    cut = (await store.read(store.binding)).cut
    original = native.project_market_event

    def fail(effect):
        if fault == 'partial_sql':
            original(effect)
            with native.sql_manager.get_new_session() as session:
                session.query(OrderStatus).delete()
                session.commit()
        raise OSError('synthetic interruption')
    monkeypatch.setattr(native, 'project_market_event', fail)
    with pytest.raises(StorageError, match='RECORDER_EVENT_UNKNOWN'):
        await repo.project_event(command, cut=cut, owner=owner)
    await store.close()
    monkeypatch.setattr(native, 'project_market_event', original)
    cold = await open_store(Backend.FILE, path=tmp_path / 'outbox.json', binding=store.binding,
                            limits=store.limits, create=False, clock=lambda: owner.expires_at)
    try:
        recovered = FileRecorderRepository(cold, native, 'life')
        if fault == 'effect':
            def corrupt():
                with cold._state.locked() as state:
                    rows = thaw(state['resources']['recorder_events'])
                    effects = json.loads(rows[command.event_id]['effect_json'])
                    effects[1]['after'] = effects[1]['after'].replace('wire-1', 'foreign-wire')
                    rows[command.event_id]['effect_json'] = json.dumps(effects)
                    state['resources']['recorder_events'] = json.loads(encode(rows))
                    cold._state.commit(state)
            await cold._call(corrupt)  # Deliberately checksum-valid metadata mutation.
        with pytest.raises(StorageError, match='RECORDER_EVENT_EFFECT_UNVERIFIED|RECORDER_EVENT_UNKNOWN'):
            await recovered.project_event(command, cut=cut, owner=owner)
        with native.sql_manager.get_new_session() as session:
            assert session.query(OrderStatus).count() == 0
            assert session.query(TradeFill).count() == (1 if fault == 'partial_sql' else 0)
    finally:
        await cold.close()
