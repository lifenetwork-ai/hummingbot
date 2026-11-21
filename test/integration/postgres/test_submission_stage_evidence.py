"""Opt-in stage evidence preserves native durable admission and acknowledgements."""
import asyncio
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
        places = [span for span in snapshot['submission_recent'] if span['operation'] == 'order.place']
        assert len(places) == 2
        for span in places:
            assert all(0 <= span[field] <= span['duration_ms'] for field in (
                'final_authority_ms', 'wire_headers_ms', 'transport_receipt_ms', 'tracker_ack_ms'))
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
