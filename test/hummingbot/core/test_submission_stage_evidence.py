"""Keep short submission phases observable without treating them as transport latency."""
import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hummingbot.connector.exchange_py_base import ExchangePyBase
from hummingbot.core import engine_diagnostics as diagnostic, order_submission_guard as gates
from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.engine_diagnostics import EngineDiagnostics


def test_completed_submission_phases_survive_sql_churn_and_publish_on_watchdog():
    now, records = [10_000_000_000], []
    tracer = EngineDiagnostics(clock=lambda: now[0], emit=records.append, capacity=8)
    tracer._started, tracer.loop_thread_id = True, threading.get_ident()
    with tracer.span('order.submit', order='never-export-order'):
        with tracer.span('order.intent', order='never-export-order'):
            now[0] += 10_000_000
    for _ in range(100):
        with tracer.span('db.sql'):
            pass
    assert not records  # No logging or sink callback on the trading path.
    tracer._beat_ns = now[0]
    tracer.watch_once()
    receipt = next(row for row in records if row['event'] == 'order_submission_stages')
    assert [s['operation'] for s in receipt['completed']] == ['order.intent', 'order.submit']
    assert receipt['dropped_since_previous'] == 0
    assert 'never-export' not in json.dumps(receipt)
    assert len(json.dumps(receipt).encode()) <= 24000
    tracer.watch_once()
    assert len([r for r in records if r['event'] == 'order_submission_stages']) == 1


def test_eight_order_burst_retains_all_five_phases_before_next_batch():
    now, records = [10_000_000_000], []
    tracer = EngineDiagnostics(clock=lambda: now[0], emit=records.append)
    tracer._started, tracer.loop_thread_id = True, threading.get_ident()
    for index in range(8):
        for operation in ('order.submit', 'order.admission', 'order.intent', 'order.place', 'order.created'):
            with tracer.span(operation, order='private-burst-' + str(index)):
                pass
    tracer._beat_ns = now[0]
    tracer.watch_once()
    batch = next(row for row in records if row['event'] == 'order_submission_stages')
    assert len(batch['completed']) == 40 and batch['dropped_since_previous'] == 0
    assert len(json.dumps(batch).encode()) <= 24000


def test_submission_batch_reports_bounded_loss_and_keeps_late_finishing_parent():
    now, records = [10_000_000_000], []
    tracer = EngineDiagnostics(clock=lambda: now[0], emit=records.append)
    tracer._started, tracer.loop_thread_id = True, threading.get_ident()
    parent = tracer.begin('order.submit', order='private-parent')
    for _ in range(100):
        with tracer.span('order.created', order='private-child'):
            pass
    tracer._beat_ns = now[0]
    tracer.watch_once()
    first = next(r for r in records if r['event'] == 'order_submission_stages')
    assert len(first['completed']) <= 64 and first['dropped_since_previous'] > 0
    now[0] += 2_000_000_000
    tracer.finish(parent)
    tracer._beat_ns = now[0]
    tracer.watch_once()
    second = [r for r in records if r['event'] == 'order_submission_stages'][-1]
    assert [s['id'] for s in second['completed']] == [parent['id']]
    assert second['dropped_since_previous'] == 0
    assert 'private-' not in json.dumps(records)


@pytest.mark.asyncio
async def test_real_watchdog_delivers_submission_batch_off_loop_without_holding_lock():
    emitted = []

    def emit(payload):
        assert tracer.lock.acquire(blocking=False)
        tracer.lock.release()
        emitted.append((threading.get_ident(), payload))

    tracer = EngineDiagnostics(emit=emit)
    assert await tracer.start()
    try:
        with tracer.span('order.intent', order='private-native'):
            pass
        async with asyncio.timeout(3):
            while not any(row['event'] == 'order_submission_stages' for _, row in emitted):
                await asyncio.sleep(.01)
        assert all(identity != tracer.loop_thread_id for identity, _ in emitted)
        assert not tracer.sink_errors
    finally:
        await tracer.close()


@pytest.mark.asyncio
async def test_native_admission_and_place_are_separate_and_place_does_not_confirm_creation(monkeypatch):
    now = [10_000_000_000]
    tracer = EngineDiagnostics(clock=lambda: now[0], emit=lambda _: None)
    monkeypatch.setattr(diagnostic, '_ACTIVE', tracer)
    owner = BotOwnership(None, 'test')
    monkeypatch.setattr(gates, 'postgres_requested', lambda: True)
    monkeypatch.setattr(gates, '_guard', owner)
    owner.before_order = Mock()
    order = SimpleNamespace(client_order_id='private-native', trading_pair='BTC-USDT', amount=1,
                            trade_type=TradeType.BUY, order_type=OrderType.LIMIT_MAKER, price=100)
    await owner.submission_admission.acquire()
    task = asyncio.create_task(owner.admit_order(None, order))
    await asyncio.sleep(0)
    now[0] += 125_000_000
    owner.submission_admission.release()
    await task

    async def place(**kwargs):
        now[0] += 40_000_000
        return '123', 12

    actor = SimpleNamespace(_place_order=place, _order_tracker=Mock())
    assert await ExchangePyBase._place_order_and_process_update(actor, order) == '123'
    spans = tracer.snapshot()['submission_recent']
    assert [(r['operation'], r['duration_ms']) for r in spans] == [
        ('order.admission', 125), ('order.place', 40)]
    actor._order_tracker.process_order_update.assert_called_once()
    assert 'order.created' not in tracer.snapshot()['operations']
    assert not owner.submission_admission.locked()


@pytest.mark.asyncio
async def test_cancelled_native_admission_keeps_original_cancellation_and_never_calls_writer(monkeypatch):
    tracer = EngineDiagnostics(emit=lambda _: None)
    monkeypatch.setattr(diagnostic, '_ACTIVE', tracer)
    owner = BotOwnership(None, 'test')
    monkeypatch.setattr(gates, 'postgres_requested', lambda: True)
    monkeypatch.setattr(gates, '_guard', owner)
    owner.before_order = Mock()
    await owner.submission_admission.acquire()
    task = asyncio.create_task(owner.admit_order(None, SimpleNamespace(client_order_id='private-cancel')))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    owner.submission_admission.release()
    await asyncio.sleep(0)
    owner.before_order.assert_not_called()
    assert not owner.submission_tasks and not tracer.snapshot()['active']
    assert tracer.snapshot()['operations']['order.admission']['errors'] == 1


def test_suppressed_warning_keeps_an_info_stack_sample_after_five_seconds():
    now, records = [10_000_000_000], []
    tracer = EngineDiagnostics(clock=lambda: now[0], emit=records.append)
    tracer._started, tracer.loop_thread_id, tracer._beat_ns = True, threading.get_ident(), now[0] - 300_000_000
    tracer.watch_once()
    tracer._beat_ns = now[0]
    tracer.watch_once()
    now[0] += 6_000_000_000
    tracer._beat_ns = now[0] - 300_000_000
    tracer.watch_once()
    samples = [r for r in records if r['event'] == 'engine_loop_sample']
    assert len(samples) == 1 and samples[0]['stacks']
    tracer.watch_once()
    assert len([r for r in records if r['event'] == 'engine_loop_sample']) == 1
    tracer._beat_ns = now[0]
    tracer.watch_once()
    assert sum(r['event'] == 'engine_loop_stall' for r in records) == 1
    assert sum(r['event'] == 'engine_loop_sample_recovered' for r in records) == 1


def test_new_observations_are_info_and_sink_failure_does_not_lose_retryable_batch(caplog):
    import logging

    from hummingbot.logger.service_logging import ServiceFormatter

    with caplog.at_level(logging.INFO):
        for event in ('order_submission_stages', 'engine_loop_sample', 'engine_loop_sample_recovered'):
            EngineDiagnostics._log(dict(event=event))
    rows = [json.loads(ServiceFormatter('json', {}).format(r)) for r in caplog.records]
    assert rows and all(r['severity'] == 'INFO' for r in rows)
    emitted = []

    def fail(_):
        raise OSError('never-export-sink-error')

    now = [10_000_000_000]
    tracer = EngineDiagnostics(clock=lambda: now[0], emit=fail)
    tracer._started, tracer.loop_thread_id = True, threading.get_ident()
    with tracer.span('order.submit', order='private'):
        pass
    tracer._beat_ns = now[0]
    tracer.watch_once()
    tracer.emit = emitted.append
    now[0] += 2_000_000_000
    tracer._beat_ns = now[0]
    tracer.watch_once()
    assert any(r['event'] == 'order_submission_stages' and r['completed'] for r in emitted)
    assert tracer.sink_errors
