"""Opt-in stage evidence preserves native durable admission and acknowledgements."""
import asyncio
import time
from decimal import Decimal
from test.integration.postgres.test_submission_admission_fairness import (
    native as native_fixture,
    owned as owned_fixture,
)

import pytest

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.engine_diagnostics import EngineDiagnostics
from hummingbot.core.order_submission_guard import session_order_scope
from hummingbot.model.order import Order
from hummingbot.model.runtime_journal import SubmissionAttempt

native = native_fixture
owned = owned_fixture


def test_ready_to_dispatch_p95_includes_a_quote_burst_waiting_behind_an_ack(native, monkeypatch):
    offset = 0
    tracer = EngineDiagnostics(clock=lambda: time.monotonic_ns() + offset)
    native.loop.run_until_complete(tracer.start())

    async def submit():
        nonlocal offset
        first_reply, allow_reply, ack_entered, allow_ack = (asyncio.Event() for _ in range(4))
        prepared, enter_final = asyncio.Event(), asyncio.Event()
        prepared_count = 0
        venue = native.transport.call.side_effect
        persist_ack = native.recorder._persist_open_acknowledgements
        authorize = native.owner.authorize_rest

        async def reply(request):
            if '"burst-0"' in request.data:
                first_reply.set()
                await allow_reply.wait()
            return await venue(request)

        async def hold_ack(*args):
            if not ack_entered.is_set():
                ack_entered.set()
                await allow_ack.wait()
            return await persist_ack(*args)

        async def final_authority(request):
            nonlocal prepared_count
            if '"burst-0"' not in request.data:
                prepared_count += 1
                if prepared_count == 7:
                    prepared.set()
                await enter_final.wait()
            return await authorize(request)

        async def quote(index):
            with session_order_scope(native.session.id, native.session.generation):
                await native.market._create_order(TradeType.BUY, f'burst-{index}', 'LIFE-USDT', Decimal('1'),
                                                  OrderType.LIMIT_MAKER, Decimal('10'))

        monkeypatch.setattr(native.transport.call, 'side_effect', reply)
        monkeypatch.setattr(native.recorder, '_persist_open_acknowledgements', hold_ack)
        monkeypatch.setattr(native.owner, 'authorize_rest', final_authority)
        tasks = [asyncio.create_task(quote(0))]
        try:
            async with asyncio.timeout(5):
                await first_reply.wait()
                tasks.extend(asyncio.create_task(quote(index)) for index in range(1, 8))
                await prepared.wait()
                allow_reply.set()
                await ack_entered.wait()
                enter_final.set()
                while len(native.recorder.order_writes._admissions) < 7:
                    await asyncio.sleep(0)
                assert native.transport.call.await_count == 1
                offset += 300_000_000
        finally:
            allow_reply.set()
            enter_final.set()
            allow_ack.set()
            await asyncio.gather(*tasks)

    try:
        native.loop.run_until_complete(submit())
        snapshot = tracer.snapshot()
        placed = [span for span in snapshot['submission_recent'] if span['operation'] == 'order.place']
        assert len(placed) == 8
        slow = [span for span in placed if span['final_slot_wait_ms'] >= 300]
        assert len(slow) == 7
        for span in slow:
            wait = span['final_slot_wait_evidence']
            assert wait['writers']['open_ack']['turns'] >= 1
            assert wait['writers']['open_ack']['max_overlap_ms'] >= 300
            assert not wait['truncated']
        assert snapshot['order_dispatch']['samples'] == 8
        assert snapshot['order_dispatch']['p95_ms'] == max(span['ready_to_dispatch_ms'] for span in placed)
        assert snapshot['order_dispatch']['p95_ms'] >= 300
        assert native.transport.call.await_count == 8 and not native.recorder.order_writes.locked()
        with native.manager.get_new_session() as db:
            assert db.query(Order).filter(Order.creation_pending.is_(False)).count() == 8
            assert db.query(SubmissionAttempt).filter_by(state='acknowledged').count() == 8
    finally:
        native.loop.run_until_complete(tracer.close())


@pytest.mark.parametrize('broken_sink', [False, True])
def test_native_submission_stages_preserve_owner_and_acknowledged_history(native, broken_sink):
    records = []

    def emit(payload):
        if broken_sink:
            raise OSError('do-not-export-sink-error')
        records.append(payload)

    tracer = EngineDiagnostics(emit=emit)
    identity = native.owner.owner_uuid, native.owner.generation, native.owner.backend_pid
    native.loop.run_until_complete(tracer.start())
    tracer.attach_engine(native.owner.engine)

    async def submit():
        with session_order_scope(native.session.id, native.session.generation):
            await asyncio.gather(*(native.market._create_order(
                TradeType.BUY, 'never-export-stage-' + str(i), 'LIFE-USDT', Decimal('5'),
                OrderType.LIMIT_MAKER, Decimal('10')) for i in range(2)))
        await asyncio.sleep(0)

    try:
        native.loop.run_until_complete(submit())
        # Force one bounded watchdog delivery; failure cannot alter DB/transport.
        tracer._last_submission_emitted_ns = -2_000_000_000
        tracer._beat_ns = tracer.clock()
        tracer.watch_once()
        snapshot = tracer.snapshot()
        for operation in ('order.submit', 'order.admission', 'order.place', 'order.created'):
            assert snapshot['operations'][operation]['completed'] == 2
        assert snapshot['operations']['order.intent_batch']['completed'] == 1
        assert 1 <= snapshot['operations']['order.receipt_write']['completed'] <= 2
        submits = [span for span in snapshot['submission_recent'] if span['operation'] == 'order.submit']
        assert len(submits) == 2
        assert all(0 < span['admission_ms'] <= span['duration_ms'] for span in submits)
        places = [span for span in snapshot['submission_recent'] if span['operation'] == 'order.place']
        assert len(places) == 2
        for span in places:
            assert all(0 <= span[field] <= span['duration_ms'] for field in (
                'final_authority_ms', 'wire_headers_ms', 'transport_receipt_ms', 'tracker_ack_ms', 'tracker_recorder_wait_ms'))
            assert span['tracker_recorder_wait_ms'] <= span['tracker_ack_ms']
        native.owner.check_owner()
        assert (native.owner.owner_uuid, native.owner.generation, native.owner.backend_pid) == identity
        assert native.transport.call.await_count == 2 and not native.owner.submission_tasks
        with native.manager.get_new_session() as session:
            assert session.query(Order).count() == session.query(SubmissionAttempt).count() == 2
            assert not session.query(Order).filter(Order.creation_pending.is_(True)).count()
            assert {row.state for row in session.query(SubmissionAttempt)} == {'acknowledged'}
        assert not native.store.read()['recovery_state'].get('halt')
        if broken_sink:
            assert tracer.sink_errors
        else:
            assert any(r['event'] == 'order_submission_stages' for r in records)
    finally:
        native.loop.run_until_complete(tracer.close())
