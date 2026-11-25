"""Keep short submission phases observable without treating them as transport latency."""
import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hummingbot.connector.client_order_tracker import ClientOrderTracker
from hummingbot.connector.exchange_py_base import ExchangePyBase
from hummingbot.core import engine_diagnostics as diagnostic, order_submission_guard as gates
from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.core.engine_diagnostics import EngineDiagnostics
from hummingbot.core.web_assistant.connections.data_types import RESTMethod


@pytest.mark.asyncio
async def test_admission_timing_accumulates_waits_without_charging_the_held_lock(monkeypatch):
    now = [1_000_000_000]
    tracer = EngineDiagnostics(clock=lambda: now[0])
    monkeypatch.setattr(diagnostic, '_ACTIVE', tracer)
    lock = asyncio.Lock()

    async def acquire():
        async with diagnostic.timed_submission_lock(lock, 'admission_slot_wait_ms'):
            now[0] += 100_000_000

    with tracer.span('order.admission', order='fixture'):
        await lock.acquire()
        waiter = asyncio.create_task(acquire())
        await asyncio.sleep(0)
        now[0] += 12_000_000
        lock.release()
        await waiter
        for _ in range(2):
            with diagnostic.submission_timing('ownership_backoff_ms'):
                now[0] += 500_000_000

    phase, = tracer.snapshot()['submission_recent']
    assert phase['admission_slot_wait_ms'] == 12
    assert phase['ownership_backoff_ms'] == 1000
    assert phase['duration_ms'] == 1112


def test_completed_submission_phases_survive_sql_churn_and_publish_on_watchdog():
    now, records = [10_000_000_000], []
    tracer = EngineDiagnostics(clock=lambda: now[0], emit=records.append, capacity=8)
    tracer._started, tracer.loop_thread_id = True, threading.get_ident()
    with tracer.span('order.submit', order='never-export-order'):
        with tracer.span('order.intent_batch'):
            now[0] += 10_000_000
    with tracer.span('order.created', order='never-export-order'):
        pass
    for _ in range(100):
        with tracer.span('db.sql'):
            pass
    assert not records  # No logging or sink callback on the trading path.
    tracer._beat_ns = now[0]
    tracer.watch_once()
    receipt, = records
    assert receipt['event'] == 'order_submission_stages'
    assert [s['operation'] for s in receipt['completed']] == ['order.submit', 'order.created']
    assert {operation: stats['completed'] for operation, stats in receipt['operations'].items()} == {
        'order.submit': 1, 'order.intent_batch': 1, 'order.created': 1,
    }
    assert receipt['dropped_since_previous'] == 0
    assert 'never-export' not in json.dumps(receipt)
    assert len(json.dumps(receipt).encode()) <= 24000
    tracer.watch_once()
    assert len([r for r in records if r['event'] == 'order_submission_stages']) == 1


def test_spot_sell_milestones_keep_scope_and_record_ws_evidence_before_http(monkeypatch):
    now, records = [10_000_000_000], []
    tracer = EngineDiagnostics(clock=lambda: now[0], emit=records.append)
    tracer._started, tracer.loop_thread_id = True, threading.get_ident()
    monkeypatch.setattr(diagnostic, '_ACTIVE', tracer)
    config = dict(type='position_executor', connector_name='okx', use_configured_entry_price=True,
                  mm_session_id='private-session', mm_generation=1, controller_id='private-controller',
                  trading_pair='BTC-USDT', level_id='sell_0')
    order = SimpleNamespace(client_order_id='private-replacement', exchange_order_id='private-native-id',
                            amount=1, executed_amount_base=0)
    request = SimpleNamespace(throttler_limit_id='/api/v5/trade/order', method=RESTMethod.POST, is_auth_required=True,
                              data=json.dumps(dict(clOrdId=order.client_order_id, side='sell', ordType='post_only')))
    executor = SimpleNamespace(config=config, _completed_entry_fill_timestamp=lambda: 1)

    diagnostic.observe_spot_quote_fill(executor, 'private-filled')
    diagnostic.observe_spot_quote_open(SimpleNamespace(name='okx'), order)  # Unobserved transport.
    diagnostic.observe_spot_quote_handoff(config, 'account_wait_at_ns', 'different-order')
    now[0] += 10_000_000
    diagnostic.observe_spot_quote_handoff(dict(config, mm_generation=2), 'first_proposal_at_ns')
    with gates.session_order_scope('private-session', 2, executor=dict(config, mm_generation=2)):
        unrelated = diagnostic.observe_spot_quote_dispatch(request)
    assert unrelated[1]['replaces'] is None
    diagnostic.observe_spot_quote_handoff(config, 'first_proposal_at_ns')
    order.client_order_id = 'private-same-level'
    request.data = json.dumps(dict(clOrdId=order.client_order_id, side='sell', ordType='post_only'))
    with gates.session_order_scope('private-session', 1, executor=config):
        dispatched = diagnostic.observe_spot_quote_dispatch(request)

    assert not records
    now[0] += 12_000_000
    diagnostic.observe_spot_quote_open(SimpleNamespace(name='okx_perpetual'), order)
    diagnostic.observe_spot_quote_open(SimpleNamespace(name='okx'), SimpleNamespace(**{**vars(order), 'executed_amount_base': 1}))
    diagnostic.observe_spot_quote_open(SimpleNamespace(name='okx'), order)
    diagnostic.observe_spot_quote_open(SimpleNamespace(name='okx'), order)
    now[0] += 15_000_000
    diagnostic.observe_spot_quote_response(dispatched)
    tracer._beat_ns = now[0]
    tracer.watch_once()

    batch, = records
    fill, other_send, send, opened, response = batch['completed']
    assert batch['dropped_since_previous'] == 0
    assert other_send['lane'] != fill['lane'] == send['lane'] == opened['lane'] == response['lane']
    assert send['replaces'] == opened['replaces'] == response['replaces'] == fill['order']
    assert send['order'] == opened['order'] == response['order']
    assert opened['at_ns'] - send['at_ns'] == 12_000_000
    assert response['at_ns'] > opened['at_ns']
    assert opened['fill_at_ns'] == fill['at_ns']
    assert opened['sent_at_ns'] == send['at_ns']
    assert opened['http_response_at_ns'] is None
    assert opened['first_proposal_at_ns'] == opened['sent_at_ns']
    assert 'account_wait_at_ns' not in opened
    assert response['fill_at_ns'] == fill['at_ns']
    assert response['sent_at_ns'] == send['at_ns']
    assert 'private-' not in json.dumps(batch)


def test_quote_milestones_bound_pending_state_and_do_not_link_stale_fills():
    now = [10_000_000_000]
    tracer = EngineDiagnostics(clock=lambda: now[0])
    tracer.record_quote_marker('quote.sell_fill', 'old-fill', lane='stale-lane')
    now[0] += 121_000_000_000
    assert tracer.record_quote_marker('quote.sell_send', 'new-order', lane='stale-lane')['replaces'] is None
    for index in range(80):
        lane = f'lane-{index}'
        tracer.record_quote_marker('quote.sell_fill', f'fill-{index}', lane=lane)
        tracer.record_quote_handoff(lane, 'first_proposal_at_ns', None, executor=f'executor-{index}')
        tracer.record_quote_marker('quote.sell_send', f'order-{index}', lane=lane)
    assert len(tracer._quote_fills) == len(tracer._quote_sends) == len(tracer.snapshot()['submission_recent']) == 64
    assert len(tracer._quote_attempts) == 64
    assert tracer.record_quote_marker('quote.sell_open', 'order-0') is None
    assert tracer.record_quote_marker('quote.sell_open', 'order-79')['replaces'] == 'fill-79'


@pytest.mark.parametrize('reprice', [False, True])
@pytest.mark.parametrize('slow', [False, True])
def test_spot_sell_warning_joins_executor_stages_and_keeps_only_slow_replacements(monkeypatch, reprice, slow):
    now, records = [10_000_000_000], []
    tracer = EngineDiagnostics(clock=lambda: now[0], emit=records.append)
    tracer._started, tracer.loop_thread_id = True, threading.get_ident()
    monkeypatch.setattr(diagnostic, '_ACTIVE', tracer)
    config = dict(id='private-first', type='position_executor', connector_name='okx', use_configured_entry_price=True,
                  mm_session_id='private-session', mm_generation=1, controller_id='private-controller',
                  trading_pair='BTC-USDT', level_id='sell_0')
    executor = SimpleNamespace(config=config, _completed_entry_fill_timestamp=lambda: 1)
    diagnostic.observe_spot_quote_fill(executor, 'private-original')
    diagnostic.observe_spot_quote_handoff(config, 'first_proposal_at_ns')
    first_proposal = now[0]
    if reprice:
        now[0] += 1_000_000
        diagnostic.observe_spot_quote_executor(config, 'created')
        with gates.session_order_scope('private-session', 1, executor=config):
            diagnostic.observe_spot_quote_submit('private-rejected-order')
        diagnostic.observe_spot_quote_executor(config, 'rejected', 'private-rejected-order', reason='QuoteRefreshRequired')
        now[0] += 1_400_000_000 if slow else 40_000_000
        config = dict(config, id='private-replacement')
        diagnostic.observe_spot_quote_handoff(config, 'first_proposal_at_ns')
    proposal = now[0]
    now[0] += 1_300_000_000 if slow and not reprice else 3_000_000
    diagnostic.observe_spot_quote_executor(config, 'created')
    order = SimpleNamespace(client_order_id='private-worked', exchange_order_id='private-native',
                            amount=1, executed_amount_base=0)
    request = SimpleNamespace(throttler_limit_id='/api/v5/trade/order', method=RESTMethod.POST, is_auth_required=True,
                              data=json.dumps(dict(clOrdId=order.client_order_id, side='sell', ordType='post_only')))
    with gates.session_order_scope('private-session', 1, executor=config):
        diagnostic.observe_spot_quote_submit(order.client_order_id)
        submitted = now[0]
        diagnostic.observe_spot_quote_dispatch(request)
    now[0] += 40_000_000
    diagnostic.observe_spot_quote_open(SimpleNamespace(name='okx'), order)
    for _ in range(65):
        with tracer.span('order.intent', order='private-other-order'):
            pass

    assert not records
    tracer._beat_ns = now[0]
    tracer.watch_once()
    tracer.watch_once()
    warnings = [row for row in records if row['event'] == 'spot_sell_replacement_slow']
    assert len(warnings) == int(slow)
    if slow:
        warning, = warnings
        case, = warning['replacements']
        assert case['first_proposal_at_ns'] == first_proposal
        assert case['replacement_proposal_at_ns'] == proposal
        assert case['replacement_created_at_ns'] == submitted
        assert case['replacement_submit_at_ns'] == submitted
        assert case['first_proposal_executor'] == diagnostic._identity('private-first')
        assert case['replacement_executor'] == diagnostic._identity(config['id'])
        assert case['proposal_count'] == case['submit_count'] == 1 + int(reprice)
        if reprice:
            assert case['last_rejection']['reason_code'] == 'quote_refresh_required'
            assert case['last_rejection']['executor'] == case['first_proposal_executor']
        else:
            assert 'last_rejection' not in case
        assert 'private-' not in json.dumps(warning)


def test_slow_replacement_warnings_batch_and_bound_retention():
    now, records = [10_000_000_000], []
    tracer = EngineDiagnostics(clock=lambda: now[0], emit=records.append)
    tracer._started = True

    def replace(index):
        lane, executor, order = f'lane-{index}', f'candidate-{index}', f'quote-{index}'
        original = f'filled-{index}'
        tracer.record_quote_marker('quote.sell_fill', original, lane=lane)
        tracer.record_quote_handoff(lane, 'first_proposal_at_ns', None, executor=executor)
        now[0] += 1_000_000_000
        tracer.record_quote_attempt(executor, 'submitted', order)
        tracer.record_quote_marker('quote.sell_send', order, lane=lane, executor=executor)
        tracer.record_quote_marker('quote.sell_open', order)

    def warnings():
        tracer._beat_ns = now[0]
        tracer.watch_once()
        return [row for row in records if row['event'] == 'spot_sell_replacement_slow']

    replace(0)
    first, = warnings()
    for index in range(1, 11):
        replace(index)
    assert warnings() == [first]
    now[0] = first['at_ns'] + 30_000_000_000
    _, next_batch = warnings()
    assert next_batch['dropped'] == 2
    assert [row['order'] for row in next_batch['replacements']] == [f'quote-{i}' for i in range(3, 11)]


@pytest.mark.parametrize('side', ['buy', 'sell'])
@pytest.mark.parametrize('waiting_at', ['proposal', 'open'])
def test_spot_replacement_reports_an_unfinished_gap_and_then_end_to_end_time(monkeypatch, side, waiting_at):
    now, records = [10_000_000_000], []
    tracer = EngineDiagnostics(clock=lambda: now[0], emit=records.append)
    tracer._started = True
    monkeypatch.setattr(diagnostic, '_ACTIVE', tracer)
    config = dict(type='position_executor', connector_name='okx', use_configured_entry_price=True,
                  id='candidate', mm_session_id='session', mm_generation=1, controller_id='controller',
                  trading_pair='BTC-USDT', level_id=f'{side}_0')
    executor = SimpleNamespace(config=config, _completed_entry_fill_timestamp=lambda: 1)
    order = SimpleNamespace(client_order_id='replacement', exchange_order_id='native',
                            trade_type=TradeType[side.upper()], amount=1, executed_amount_base=0,
                            build_order_created_message=lambda: 'order created')
    request = SimpleNamespace(throttler_limit_id='/api/v5/trade/order', method=RESTMethod.POST, is_auth_required=True,
                              data=json.dumps(dict(clOrdId=order.client_order_id, side=side, ordType='post_only')))

    def dispatch():
        diagnostic.observe_spot_quote_handoff(config, 'first_proposal_at_ns')
        with gates.session_order_scope('session', 1, executor=config):
            assert diagnostic.observe_spot_quote_dispatch(request) is not None

    def publish():
        tracer._beat_ns = now[0]
        tracer.watch_once()
        return [r for r in records if r['event'] == f'spot_{side}_replacement_slow']

    diagnostic.observe_spot_quote_fill(executor, 'filled')
    if waiting_at == 'open':
        dispatch()
    now[0] += 6_000_000_000
    pending, = publish()
    item, = pending['pending']
    assert item['waiting_for'] == waiting_at and item['age_ms'] == 6000
    assert not pending['replacements']
    if waiting_at == 'proposal':
        dispatch()
    ClientOrderTracker(SimpleNamespace(name='okx'))._note_creation(order, OrderState.OPEN)
    now[0] += 30_000_000_000
    _, completed = publish()
    sample, = completed['replacements']
    assert completed['pending'] == []
    assert sample['fill_to_open_ms'] == 6000
    summary = tracer.snapshot()['quote_replacement'][side]
    assert summary['completed'] == summary['samples'] == 1 and summary['max_ms'] == 6000


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
