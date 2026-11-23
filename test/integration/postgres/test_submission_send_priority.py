"""Fresh native bursts measure enqueue-to-venue latency with unchanged SQL fences."""
import asyncio
import json
import time
from decimal import Decimal
from statistics import mean
from test.integration.postgres.test_ownership import owned as owned_fixture
from test.integration.postgres.test_performance import queries, report
from test.integration.postgres.test_submission_admission_fairness import native as native_fixture
from unittest.mock import patch

import pytest
from sqlalchemy import event, update

from hummingbot.core import bot_ownership
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.order_submission_guard import session_order_scope
from hummingbot.model.bot_state import BotState
from hummingbot.model.order import Order
from hummingbot.model.runtime_journal import SubmissionAttempt

owned = owned_fixture
native = native_fixture


class FIFOAdmission(asyncio.Lock):
    async def acquire(self, *, work=None):
        return await super().acquire()

    def take_work(self, maximum, compatible=None):
        return []

    def final_send(self):
        return self

    def intent_write(self):
        return self


def test_ready_send_passes_admission_waiting_on_recorder(native, monkeypatch):
    first_ready, enter_final = asyncio.Event(), asyncio.Event()
    next_waiting = asyncio.Event()
    actions = []
    authorize = native.owner.authorize_rest
    acquire = native.recorder.order_writes.acquire
    persist = native.recorder.persist_order_intent
    reply = native.transport.call.side_effect

    async def hold_first_before_final(request):
        if json.loads(request.data)['clOrdId'] == 'ready':
            first_ready.set()
            await enter_final.wait()
        return await authorize(request)

    async def observe_recorder_wait(**kwargs):
        if kwargs.get('intent_write') and asyncio.current_task().get_name() == 'next-intent':
            next_waiting.set()
        return await acquire(**kwargs)

    async def record(connector, order):
        result = await persist(connector, order)
        actions.append(('intent', order.client_order_id))
        return result

    async def venue(request):
        identity = json.loads(request.data)['clOrdId']
        assert identity in native.owner.sent_intents
        actions.append(('wire', identity))
        return await reply(request)

    async def exercise():
        def submit(identity, **kwargs):
            return asyncio.create_task(native.market._create_order(
                TradeType.BUY, identity, 'LIFE-USDT', Decimal('5'),
                OrderType.LIMIT_MAKER, Decimal('10')), **kwargs)

        with session_order_scope(native.session.id, native.session.generation):
            first = submit('ready')
            await asyncio.wait_for(first_ready.wait(), 2)
            async with native.recorder.order_writes:
                following = submit('next', name='next-intent')
                await asyncio.wait_for(next_waiting.wait(), 2)
                enter_final.set()
                await asyncio.sleep(0)
            await asyncio.gather(first, following)

    monkeypatch.setattr(native.owner, 'authorize_rest', hold_first_before_final)
    monkeypatch.setattr(native.recorder.order_writes, 'acquire', observe_recorder_wait)
    monkeypatch.setattr(native.recorder, 'persist_order_intent', record)
    native.transport.call.side_effect = venue
    native.loop.run_until_complete(asyncio.wait_for(exercise(), 5))
    assert actions == [('intent', 'ready'), ('wire', 'ready'), ('intent', 'next'), ('wire', 'next')]
    with native.manager.get_new_session() as db:
        assert {row.order_id: row.state for row in db.query(SubmissionAttempt)} == {
            'ready': 'acknowledged', 'next': 'acknowledged',
        }
    assert not native.store.read()['recovery_state'].get('halt')


@pytest.mark.parametrize('trial,fifo', [(i, fifo) for i in range(3) for fifo in (True, False)])
def test_fresh_twelve_order_enqueue_to_acceptance(native, trial, fifo, monkeypatch):
    # Compare scheduling alone, with intent batching disabled for both modes.
    monkeypatch.setattr(bot_ownership, 'MAX_INTENT_BATCH', 1)
    if fifo:
        native.recorder.order_writes = FIFOAdmission()
    original_record, original_reply = native.recorder.persist_order_intent, native.transport.call.side_effect
    accepted, admissions, probes = [], [], []
    origin = [0.0]

    async def record(connector, order):
        cancelled = await original_record(connector, order)
        admissions.append(order.client_order_id)
        if len(admissions) == 1:
            queued = time.perf_counter()
            asyncio.get_running_loop().call_soon(
                lambda: probes.append(dict(admissions=len(admissions), wait_ms=(time.perf_counter() - queued) * 1000)))
        return cancelled

    async def venue(request):
        accepted.append(dict(order=json.loads(request.data)['clOrdId'],
                             elapsed_ms=(time.perf_counter() - origin[0]) * 1000))
        return await original_reply(request)

    def roundtrip(*args):
        time.sleep(.002)

    async def burst():
        origin[0] = time.perf_counter()
        with session_order_scope(native.session.id, native.session.generation):
            tasks = [asyncio.create_task(native.market._create_order(
                TradeType.BUY, 'priority-' + str(i), 'LIFE-USDT', Decimal('5'),
                OrderType.LIMIT_MAKER, Decimal('10'))) for i in range(12)]
        await asyncio.gather(*tasks)
        await asyncio.sleep(0)

    event.listen(native.engine, 'after_cursor_execute', roundtrip)
    try:
        with patch.object(native.recorder, 'persist_order_intent', side_effect=record), \
                patch.object(native.transport.call, 'side_effect', venue), queries(native.engine) as statements:
            native.loop.run_until_complete(burst())
    finally:
        event.remove(native.engine, 'after_cursor_execute', roundtrip)
    assert len(admissions) == len(accepted) == native.transport.call.await_count == 12
    assert probes[0]['admissions'] == 1
    assert not native.owner.submission_tasks and not native.owner.transport_tasks
    assert not native.owner.submission_admission.locked()
    with native.manager.get_new_session() as db:
        assert db.query(Order).count() == db.query(SubmissionAttempt).count() == 12
        assert not db.query(Order).filter(Order.creation_pending.is_(True)).count()
        assert {row.state for row in db.query(SubmissionAttempt)} == {'acknowledged'}
        assert all(row.payload_hash and row.receipt['http_status'] == 200 for row in db.query(SubmissionAttempt))
    assert not native.store.read()['recovery_state'].get('halt')
    values = [row['elapsed_ms'] for row in accepted]
    report(f'send_priority_{trial}_{"fifo" if fifo else "priority"}', dict(
        offered_orders=12, sql_statements=len(statements), injected_roundtrip_seconds=.002,
        first_accept_ms=min(values), mean_accept_ms=mean(values), last_accept_ms=max(values),
        accepts=accepted, callback=probes[0],
        scope='Fresh equal ledgers; native connector and real PG; not HTTP/exchange performance qualification'))


@pytest.mark.parametrize('change', ['stop', 'version'])
def test_control_callback_after_first_prioritized_send_fences_queued_orders(native, change):
    original = native.owner._record_send_attempt
    sent, revoked = [], []

    def revoke():
        if change == 'stop':
            native.store.control(expected_version=native.owner.version, desired_state='stopped')
        else:
            # Simulate the version advance from a separate MM control writer.
            # Legacy start is intentionally unavailable with armed spot_outflow.
            with native.engine.begin() as connection:
                changed = connection.execute(update(BotState).where(
                    BotState.bot_id == native.store.bot_id, BotState.control_version == native.owner.version
                ).values(control_version=native.owner.version + 1))
                assert changed.rowcount == 1
        revoked.append(change)

    def record(identity, *args):
        original(identity, *args)
        sent.append(identity)
        if len(sent) == 1:
            asyncio.get_running_loop().call_soon(revoke)

    async def burst():
        with session_order_scope(native.session.id, native.session.generation):
            tasks = [asyncio.create_task(native.market._create_order(
                TradeType.BUY, 'revoked-' + str(i), 'LIFE-USDT', Decimal('5'),
                OrderType.LIMIT_MAKER, Decimal('10'))) for i in range(12)]
        await asyncio.gather(*tasks)
        await asyncio.sleep(0)

    with patch.object(native.owner, '_record_send_attempt', side_effect=record):
        native.loop.run_until_complete(burst())
    assert revoked == [change]
    assert len(sent) == native.transport.call.await_count == 1
    assert not native.owner.submission_tasks and not native.owner.transport_tasks
    assert not native.owner.submission_admission.locked()
    with native.manager.get_new_session() as db:
        assert db.query(SubmissionAttempt).filter(SubmissionAttempt.state == 'acknowledged').count() == 1
        assert not db.query(Order).filter(Order.creation_pending.is_(True)).count()
    assert not native.store.read()['recovery_state'].get('halt')
