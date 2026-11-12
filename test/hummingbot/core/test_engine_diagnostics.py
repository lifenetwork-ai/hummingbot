"""Bounded observational tracing cannot acquire authority or export raw inputs."""
import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, text

from hummingbot.core.engine_diagnostics import EngineDiagnostics, trace_runtime


def test_retention_and_error_metadata_are_bounded_without_raw_identifiers():
    emitted = []
    tracer = EngineDiagnostics(emit=emitted.append, capacity=8, max_active=2)
    for _ in range(30):
        with tracer.span('strategy.tick', entity='secret-config-id'):
            pass
    first = tracer.begin('owner.read')
    second = tracer.begin('db.sql')
    assert tracer.begin('db.sql') is None
    tracer.finish(first, error=True)
    tracer.finish(second)
    snapshot = tracer.snapshot()
    assert len(snapshot['recent']) == 8 and not snapshot['active']
    assert snapshot['operations']['strategy.tick']['completed'] == 30
    assert snapshot['operations']['owner.read']['errors'] == 1
    assert snapshot['dropped_spans'] == 1
    assert 'secret-config-id' not in json.dumps(snapshot)
    assert not emitted


@pytest.mark.asyncio
async def test_real_loop_stall_has_stage_and_sanitized_stack_then_tears_down():
    records = []
    tracer = EngineDiagnostics(emit=records.append)
    private_value = 'never-copy-this-local-or-exception'
    await tracer.start()
    try:
        await asyncio.sleep(.06)
        with tracer.span('owner.read', entity=private_value):
            time.sleep(.4)
        await asyncio.sleep(.15)
    finally:
        await tracer.close()
    stalls = [r for r in records if r['event'] == 'engine_loop_stall']
    assert stalls and stalls[0]['heartbeat_gap_ms'] >= 250
    assert any(s['operation'] == 'owner.read' for s in stalls[0]['active'])
    assert any(s['stack'] for s in stalls[0]['stacks'])
    assert any(r['event'] == 'engine_loop_recovered' for r in records)
    assert private_value not in json.dumps(records)
    assert not tracer.worker.is_alive()
    assert tracer.heartbeat_task.done()
    assert not tracer.snapshot()['active']


@pytest.mark.asyncio
async def test_decorators_preserve_return_exception_and_task_cancellation():
    tracer = EngineDiagnostics(emit=lambda _: None)
    await tracer.start()

    @trace_runtime('order.submit', order_parameter='order_id')
    async def submit(actor, order_id):
        if order_id == 'fail':
            raise ValueError('do-not-export-error-text')
        await asyncio.Event().wait()

    @trace_runtime('strategy.tick')
    def tick(actor):
        return 17

    try:
        actor = SimpleNamespace(config=SimpleNamespace(id='do-not-export-id'))
        assert tick(actor) == 17
        with pytest.raises(ValueError, match='do-not-export-error-text'):
            await submit(actor, 'fail')
        task = asyncio.create_task(submit(actor, 'private-order-id'))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not tracer.snapshot()['active']
        assert tracer.snapshot()['operations']['order.submit']['errors'] == 2
        rendered = json.dumps(tracer.snapshot())
        assert 'private-order-id' not in rendered and 'do-not-export' not in rendered
    finally:
        await tracer.close()


@pytest.mark.asyncio
async def test_sql_hooks_measure_errors_without_sql_parameters_and_are_removed():
    tracer = EngineDiagnostics(emit=lambda _: None)
    engine = create_engine('sqlite://')
    tracer.attach_engine(engine)
    assert tracer.hooks
    hooks = list(tracer.hooks)
    try:
        with engine.connect() as connection:
            assert connection.execute(text('SELECT :secret'), {'secret': 'never-log-parameter'}).scalar() == 'never-log-parameter'
            with pytest.raises(Exception):
                connection.execute(text('SELECT never_log_statement_missing_column'))
        snapshot = tracer.snapshot()
        assert snapshot['operations']['db.sql']['completed'] == 2
        assert snapshot['operations']['db.sql']['errors'] == 1
        assert not snapshot['active']
        assert 'never_log_statement' not in json.dumps(snapshot)
        assert 'never-log-parameter' not in json.dumps(snapshot)
    finally:
        await tracer.close()
        assert all(not event.contains(target, name, callback) for target, name, callback in hooks)
        engine.dispose()


def test_warning_budget_and_snapshot_size_stay_bounded():
    clock, records = [1_000_000_000], []
    tracer = EngineDiagnostics(emit=records.append, clock=lambda: clock[0])
    tracer.loop_thread_id = threading.get_ident()
    tracer._started = True
    tracer._beat_ns = 0
    for _ in range(100):
        with tracer.span('strategy.tick', entity='private'):
            pass
    tracer.watch_once()
    clock[0] += 1_000_000_000
    tracer.watch_once()
    tracer._beat_ns = clock[0]
    tracer.watch_once()
    clock[0] += 1_000_000_000
    tracer.watch_once()
    assert [r['event'] for r in records] == ['engine_loop_stall', 'engine_loop_recovered']
    assert all(len(json.dumps(r).encode()) <= 24000 for r in records)


@pytest.mark.asyncio
async def test_broken_sink_does_not_change_business_result_or_cleanup():
    def broken(_):
        raise OSError('private-sink-error')

    tracer = EngineDiagnostics(emit=broken)
    await tracer.start()
    try:
        await asyncio.sleep(.06)
        with tracer.span('strategy.tick'):
            time.sleep(.35)
        await asyncio.sleep(.1)
        assert tracer.sink_errors
    finally:
        await tracer.close()
    assert not tracer.worker.is_alive()


def test_disabled_decorator_does_not_inspect_actor_or_arguments():
    class Private:
        def __getattribute__(self, name):
            raise AssertionError('Disabled diagnostics inspected business state')

    @trace_runtime('strategy.tick')
    def tick(actor):
        return actor

    actor = Private()
    assert tick(actor) is actor


def test_slow_network_stage_is_visible_even_when_the_engine_heartbeat_is_healthy():
    clock, records = [1_000_000_000], []
    tracer = EngineDiagnostics(emit=records.append, clock=lambda: clock[0])
    tracer.loop_thread_id, tracer._started = threading.get_ident(), True
    token = tracer.begin('order.cancel', order='private-native-order')
    clock[0] += 2_000_000_000
    tracer.finish(token)
    tracer._beat_ns = clock[0]
    tracer.watch_once()
    assert [r['event'] for r in records] == ['engine_stage_slow']
    assert records[0]['slow'][0]['duration_ms'] == 2000
    assert not records[0]['active']
    assert 'private-native-order' not in json.dumps(records)
    tracer.watch_once()
    assert len(records) == 1


@pytest.mark.asyncio
async def test_internal_instrumentation_failure_cannot_mask_a_return_or_original_exception(monkeypatch):
    tracer = EngineDiagnostics(emit=lambda _: None)
    await tracer.start()

    def broken(*args, **kwargs):
        raise MemoryError('private-diagnostic-error')

    @trace_runtime('strategy.tick')
    def business(actor, fail=False):
        if fail:
            raise ValueError('original-business-error')
        return 23

    try:
        monkeypatch.setattr(tracer, 'finish', broken)
        assert business(None) == 23
        with pytest.raises(ValueError, match='original-business-error'):
            business(None, fail=True)
    finally:
        await tracer.close()


@pytest.mark.asyncio
async def test_watchdog_race_is_labelled_instead_of_claiming_a_blocking_frame(monkeypatch):
    now, records = [1_000_000_000], []
    tracer = EngineDiagnostics(emit=records.append, clock=lambda: now[0])
    tracer.loop_thread_id, tracer._started, tracer._beat_ns = threading.get_ident(), True, 0

    def advanced(_):
        tracer._beat_ns = now[0]
        return []

    monkeypatch.setattr(tracer, '_stacks', advanced)
    tracer.watch_once()
    assert records[0]['heartbeat_advanced_during_capture'] is True
    assert records[0]['heartbeat_gap_ms'] == 1000


@pytest.mark.asyncio
async def test_incomplete_teardown_leaves_only_inert_weak_sql_callbacks():
    import gc
    import weakref

    engine = create_engine('sqlite://')
    tracer = EngineDiagnostics(emit=lambda _: None)
    tracer.attach_engine(engine)
    reference = weakref.ref(tracer)
    await tracer.close(detach=False)
    del tracer
    gc.collect()
    assert reference() is None
    with engine.connect() as connection:
        assert connection.execute(text('SELECT 31')).scalar() == 31
    engine.dispose()


def test_diagnostic_is_a_warning_with_the_existing_service_log_format(caplog):
    import logging

    from hummingbot.logger.service_logging import ServiceFormatter

    with caplog.at_level(logging.WARNING):
        EngineDiagnostics._log(dict(event='engine_stage_slow', heartbeat_gap_ms=10, slow=[]))
    record = next(r for r in caplog.records if getattr(r, 'event', None) == 'engine_stage_slow')
    rendered = json.loads(ServiceFormatter('json', {}).format(record))
    assert rendered['severity'] == 'WARNING' and rendered['scope'] == 'runtime'
    assert json.loads(rendered['message'].split('Engine diagnostic ', 1)[1])['event'] == 'engine_stage_slow'
