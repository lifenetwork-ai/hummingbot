import asyncio
import gc
import json
import weakref
from test.e2e.offline import load_mm_activity as activity

import pytest


class Clock:
    now = 1_000_000_000

    def __call__(self):
        return self.now


class Actor:
    name = 'okx'


@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.asyncio
async def test_cycle_measures_attempt_gap_and_completion_without_changing_result(asynchronous):
    clock, rows = Clock(), []
    recorder = activity.ActivityRecorder(rows.append, clock=clock, limit=4)

    class Runner:
        def tick(self, stamp):
            clock.now += 20_000_000
            return stamp

        async def cycle(self, stamp):
            return self.tick(stamp)
    method = 'cycle' if asynchronous else 'tick'
    operation = 'controller.cycle' if asynchronous else 'strategy.tick'
    activity.instrument_cycle(Runner, method, operation, recorder, asynchronous=asynchronous)
    runner = Runner()
    for start in [1_000_000_000, 4_000_000_000]:
        clock.now = start
        result = getattr(runner, method)('original')
        assert (await result if asynchronous else result) == 'original'
    starts = [r for r in rows if r['phase'] == 'start']
    assert starts[0]['previous_start_ns'] is None
    assert starts[1]['gap_ms'] == 3000
    assert len({r['instance'] for r in rows}) == 1
    summary = recorder.summary()
    assert summary['operations'][operation]['max_duration_ms'] == 20
    assert summary['operations'][operation]['max_start_gap_ms'] == 3000
    assert summary['operations'][operation]['completed'] == 2
    assert len(summary['recent']) == 4
    reference = weakref.ref(runner)
    del runner
    gc.collect()
    assert reference() is None and recorder.summary()['live_instances'] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['ok', 'false', 'error', 'cancel'])
async def test_order_timing_preserves_return_exception_and_cancellation(outcome):
    clock, rows = Clock(), []
    recorder = activity.ActivityRecorder(rows.append, clock=clock)

    class Connector(Actor):
        async def cancel(self, order_id, tracked_order):
            clock.now += 80_000_000
            if outcome == 'error':
                raise ValueError('original')
            if outcome == 'cancel':
                raise asyncio.CancelledError
            return outcome == 'ok'
    activity.instrument_order(Connector, 'cancel', 'order.cancel', recorder)
    connector = Connector()
    if outcome in ('error', 'cancel'):
        with pytest.raises(ValueError if outcome == 'error' else asyncio.CancelledError):
            await connector.cancel('one', object())
    else:
        assert await connector.cancel(order_id='one', tracked_order=object()) is (outcome == 'ok')
    event = rows[-1]
    assert event['duration_ms'] == 80 and event['order_id'] == 'one'
    assert event['connector'] == 'okx'
    assert event['outcome'] == {'ok': 'returned', 'false': 'returned_false', 'error': 'ValueError', 'cancel': 'CancelledError'}[outcome]
    assert 'tracked_order' not in json.dumps(rows)


def test_bounded_events_do_not_retain_actors_and_sink_failure_cannot_break_work():
    clock = Clock()

    def broken(_):
        raise OSError('disk unavailable')
    recorder = activity.ActivityRecorder(broken, clock=clock, limit=3)
    actor = Actor()
    for _ in range(20):
        token = recorder.start(actor, 'strategy.tick')
        recorder.finish(token)
        clock.now += 1
    summary = recorder.summary()
    assert len(summary['recent']) == 3
    assert summary['events_evicted'] == 37 and summary['sink_errors'] == 40
    assert summary['operations']['strategy.tick']['completed'] == 20
    with pytest.raises(ValueError):
        recorder.start(actor, 'unbounded-operation-key')


def test_venue_live_age_advances_without_an_engine_tick_and_terminal_is_separate():
    clock = Clock()
    quotes = activity.VenueQuotes(clock=clock, limit=2)
    order = dict(clOrdId='one', instId='LIFE-USDT', side='buy')
    quotes.accept(order)
    clock.now += 2_800_000_000
    first = quotes.snapshot()
    assert first['orders'][0]['age_ms'] == 2800 and first['at_ns'] == clock.now
    received = clock.now
    clock.now += 50_000_000
    quotes.terminal('one', 'canceled', request_ns=received)
    last = quotes.snapshot()
    assert last['orders'] == []
    assert last['terminals'][0]['request_to_terminal_ms'] == 50
    assert last['terminals'][0]['state'] == 'canceled'
    assert last['terminals'][0]['order_id'] == 'one'


def test_venue_tracking_and_terminal_history_are_bounded_and_loss_is_explicit():
    clock = Clock()
    quotes = activity.VenueQuotes(clock=clock, limit=2)
    for name in ['one', 'two', 'overflow']:
        quotes.accept(dict(clOrdId=name, instId='LIFE-USDT', side='buy'))
    assert len(quotes.snapshot()['orders']) == 2
    assert quotes.snapshot()['untracked_acceptances'] == 1
    for name in ['one', 'two', 'overflow']:
        quotes.terminal(name, 'filled')
    assert len(quotes.snapshot()['terminals']) == 2
    assert quotes.snapshot()['terminal_events_evicted'] == 1
    assert quotes.snapshot()['tracking_complete'] is False


@pytest.mark.asyncio
async def test_venue_sampler_records_request_bounds_errors_and_clock_alignment():
    clock, rows = Clock(), []
    replies = [dict(at_ns=1_100_000_000, orders=[], terminals=[], tracking_complete=True), OSError('offline')]

    async def read():
        value = replies.pop(0)
        clock.now += 200_000_000
        if isinstance(value, Exception):
            raise value
        return value
    sampler = activity.VenueSampler(read, rows.append, clock=clock)
    await sampler.sample()
    await sampler.sample()
    assert rows[0]['kind'] == 'venue_quote_sample' and rows[0]['clock_aligned'] is True
    assert rows[0]['request_start_ns'] == 1_000_000_000 and rows[0]['response_end_ns'] == 1_200_000_000
    assert rows[1]['kind'] == 'venue_quote_error'
    assert sampler.summary()['samples'] == 1 and sampler.summary()['errors'] == 1


@pytest.mark.asyncio
async def test_bad_venue_clock_is_not_accepted_as_continuity_evidence():
    async def read():
        return dict(at_ns=0, orders=[], terminals=[], tracking_complete=True)
    sampler = activity.VenueSampler(read, lambda _: None, clock=Clock())
    await sampler.sample()
    assert sampler.summary()['clock_errors'] == 1


def test_failed_readiness_observation_cannot_mask_successful_trading_call():
    recorder = activity.ActivityRecorder(lambda _: None)

    class Runner:
        def tick(self):
            return 42

    def bad_metadata(_):
        raise AttributeError('readiness unavailable')
    activity.instrument_cycle(Runner, 'tick', 'strategy.tick', recorder, details=bad_metadata)
    assert Runner().tick() == 42
    assert recorder.summary()['metadata_errors'] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('value', [{}, {'at_ns': True}, {'at_ns': 1, 'orders': None}])
async def test_malformed_venue_reply_is_explicit_error_instead_of_dead_sampler(value):
    async def read():
        return value
    sampler = activity.VenueSampler(read, lambda _: None)
    await sampler.sample()
    assert sampler.summary()['errors'] == 1
