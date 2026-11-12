"""Replacement-stage diagnostics preserve native results, errors and identities."""
from test.e2e.offline import load_mm_activity as activity
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('outcome', ['ok', 'error'])
def test_terminal_trace_keeps_identity_after_callback_clears_order(outcome):
    rows = []
    recorder = activity.ActivityRecorder(rows.append)

    class Executor:
        config = SimpleNamespace(id='executor')

        def __init__(self):
            self._order = SimpleNamespace(order_id='order')

        def callback(self, tag, market, event):
            self._order = None
            if outcome == 'error':
                raise ValueError('do not serialize callback details')
            return 42

    activity.instrument_terminal(Executor, 'callback', recorder)
    if outcome == 'error':
        with pytest.raises(ValueError):
            Executor().callback(None, None, SimpleNamespace(order_id='order'))
    else:
        assert Executor().callback(None, None, SimpleNamespace(order_id='order')) == 42
    assert [r['operation'] for r in rows] == ['order.terminal', 'order.terminal']
    assert all(r['order_id'] == 'order' and r['executor_id'] == 'executor' for r in rows)
    assert rows[-1]['outcome'] == ('ValueError' if outcome == 'error' else 'returned')


def test_foreign_terminal_callback_runs_without_a_false_matching_trace():
    rows = []
    recorder = activity.ActivityRecorder(rows.append)

    class Executor:
        _order = SimpleNamespace(order_id='own')

        def callback(self, tag, market, event):
            return event.order_id

    activity.instrument_terminal(Executor, 'callback', recorder)
    assert Executor().callback(None, None, SimpleNamespace(order_id='foreign')) == 'foreign'
    assert rows == []


@pytest.mark.parametrize('outcome', ['ok', 'error'])
def test_checkpoint_trace_preserves_commit_failure_and_does_not_capture_financial_or_secret_payload(outcome):
    rows = []
    recorder = activity.ActivityRecorder(rows.append)
    positions = [object()]
    infos = [SimpleNamespace(id='executor')]
    basis = object()

    class Store:
        def checkpoint(self, actual_positions, actual_infos, *, reduction_bases):
            assert actual_positions is positions and actual_infos is infos and reduction_bases is basis
            if outcome == 'error':
                raise OSError('commit outcome unknown')
            return 17

    activity.instrument_checkpoint(Store, 'checkpoint', recorder)
    if outcome == 'error':
        with pytest.raises(OSError):
            Store().checkpoint(positions, infos, reduction_bases=basis)
    else:
        assert Store().checkpoint(positions, infos, reduction_bases=basis) == 17
    assert all(r['operation'] == 'position.checkpoint' and r['executor_ids'] == ['executor'] for r in rows)
    assert rows[-1]['outcome'] == ('OSError' if outcome == 'error' else 'returned')
    assert 'positions' not in rows[-1] and 'reduction_bases' not in rows[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['service.stop_core', 'service.prepare', 'service.start'])
async def test_profile_stage_timing_preserves_asynchronous_failure(operation):
    recorder = activity.ActivityRecorder(lambda _: None)

    class Host:
        async def step(self):
            raise OSError('original profile error')

    activity.instrument_cycle(Host, 'step', operation, recorder, asynchronous=True)
    with pytest.raises(OSError):
        await Host().step()
    assert recorder.summary()['operations'][operation]['errors'] == 1


def test_terminal_diagnostic_metadata_failure_cannot_skip_real_callback():
    rows = []
    recorder = activity.ActivityRecorder(rows.append)

    class Executor:
        _order = SimpleNamespace(order_id='order')

        @property
        def config(self):
            raise RuntimeError('diagnostic metadata unavailable')

        def callback(self, *args):
            self.called = True
            return 'native-result'

    activity.instrument_terminal(Executor, 'callback', recorder)
    actor = Executor()
    assert actor.callback(SimpleNamespace(order_id='order')) == 'native-result'
    assert actor.called
    assert recorder.metadata_errors == 1
    assert rows[-1]['outcome'] == 'returned'


@pytest.mark.asyncio
async def test_service_stage_metadata_cannot_collide_with_trace_phase():
    rows = []
    recorder = activity.ActivityRecorder(rows.append)

    class Host:
        phase = 'starting'

        async def step(self, row):
            return row

    activity.instrument_cycle(Host, 'step', 'service.prepare', recorder, asynchronous=True,
                              start_details=lambda actor: dict(phase=actor.phase))
    assert await Host().step('native-result') == 'native-result'
    assert rows[0]['phase'] == 'start' and rows[0]['service_phase'] == 'starting'
    assert rows[-1]['phase'] == 'end'
