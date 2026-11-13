"""Startup history must not repeatedly assign Pydantic reports per ledger row."""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator
from hummingbot.strategy_v2.models.executors import CloseType
from hummingbot.strategy_v2.models.executors_info import PerformanceReport


def setup(monkeypatch, rows):
    item = object.__new__(ExecutorOrchestrator)
    item.strategy = SimpleNamespace(config=SimpleNamespace(mm_drawdown_scope=None), controllers={'a': None, 'b': None})
    item.cached_performance = {'a': PerformanceReport(realized_pnl_quote=Decimal('.123'), volume_traded=Decimal('1')),
                               'b': PerformanceReport()}
    item.active_executors, item.positions_held = {}, {'a': [], 'b': []}
    item.initial_positions_by_controller = {}
    closed = []

    def history(controller_ids):
        assert controller_ids == ['a', 'b']
        try:
            yield from rows
        finally:
            closed.append(True)

    recorder = SimpleNamespace(iter_executor_performance=history, get_all_positions=lambda **kw: [])
    monkeypatch.setattr(MarketsRecorder, 'get_instance', lambda: recorder)
    return item, closed


def row(controller='a', *, pnl='.000000000000000123', volume='1.000000000000001', close=CloseType.EARLY_STOP):
    return SimpleNamespace(controller_id=controller, net_pnl_quote=Decimal(pnl), filled_amount_quote=Decimal(volume), close_type=close)


def test_startup_report_assignments_scale_with_controllers_not_history(monkeypatch):
    rows = [row('a' if i % 2 else 'b', close=CloseType.POSITION_HOLD if i % 7 == 0 else CloseType.EARLY_STOP)
            for i in range(2000)]
    rows.append(row('foreign', pnl='NaN'))  # Out-of-scope rows do not supply report authority.
    item, closed = setup(monkeypatch, rows)
    reference = object.__new__(ExecutorOrchestrator)
    reference.cached_performance = {key: value.model_copy(deep=True) for key, value in item.cached_performance.items()}
    for record in rows[:-1]:
        reference._update_cached_performance(record.controller_id, record)
    original, writes = PerformanceReport.__setattr__, []

    def assign(report, name, value):
        writes.append(name)
        return original(report, name, value)

    monkeypatch.setattr(PerformanceReport, '__setattr__', assign)
    item._initialize_cached_performance()
    assert item.cached_performance == reference.cached_performance
    assert len(writes) <= 6, f'Historical startup repeatedly assigns report models: {len(writes)}'
    assert closed == [True]


@pytest.mark.parametrize('invalid', ['NaN', 'Infinity', 'close_type'])
def test_startup_bad_history_closes_reader_without_publishing_partial_totals(monkeypatch, invalid):
    bad = row(pnl=invalid if invalid != 'close_type' else '0')
    if invalid == 'close_type':
        bad.close_type = 999999
    item, closed = setup(monkeypatch, [row(pnl='5'), bad])
    before = {key: value.model_copy(deep=True) for key, value in item.cached_performance.items()}
    with pytest.raises(ValueError):
        item._initialize_cached_performance()
    assert item.cached_performance == before
    assert closed == [True]


def test_startup_preserves_decimal_application_order_and_all_close_type_counts(monkeypatch):
    rows = [row(pnl='1e27'), row(pnl='-1e27'), row(pnl='.0001', close=None),
            row('b', pnl='3', close=CloseType.POSITION_HOLD)]
    item, closed = setup(monkeypatch, rows)
    expected = object.__new__(ExecutorOrchestrator)
    expected.cached_performance = {key: value.model_copy(deep=True) for key, value in item.cached_performance.items()}
    for record in rows:
        expected._update_cached_performance(record.controller_id, record)
    item._initialize_cached_performance()
    assert item.cached_performance == expected.cached_performance
    assert item.cached_performance['b'].close_type_counts == {CloseType.POSITION_HOLD: 1}
    assert closed == [True]
