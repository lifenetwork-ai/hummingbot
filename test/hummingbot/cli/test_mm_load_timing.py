from collections import defaultdict
from test.e2e.offline.load_trace import TraceRecorder, instrument_sync

import pytest


def test_sync_stage_preserves_results_exceptions_and_measures_thread_cpu():
    class Stage:
        def execute(self, value):
            if isinstance(value, Exception):
                raise value
            return value * 2
    records, samples, totals = [], defaultdict(list), defaultdict(int)
    instrument_sync(Stage, 'execute', 'stage_ms', samples, totals, TraceRecorder(records.append, slow_ms=0))
    assert Stage().execute(3) == 6
    error = ValueError('not included in trace')
    with pytest.raises(ValueError) as caught:
        Stage().execute(error)
    assert caught.value is error
    assert totals['stage_ms_count'] == 2
    assert len(samples['stage_cpu_ms']) == 2
    assert records[-1]['spans'][0]['error'] == 'ValueError'
    assert 'not included' not in str(records)


def test_inherited_classmethod_keeps_concrete_subclass_and_arguments():
    class Parent:
        @classmethod
        def build(cls, value, *, option):
            return cls, value, option

    class Child(Parent):
        pass
    samples, totals = defaultdict(list), defaultdict(int)
    instrument_sync(Parent, 'build', 'validation_ms', samples, totals, None)
    assert Child.build(7, option='x') == (Child, 7, 'x')
    assert len(samples['validation_cpu_ms']) == 1
