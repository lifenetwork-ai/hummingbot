"""The native tick cannot enter generic synchronous recorder or reload paths."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.service import PgServiceRunner


def runner(*, ready=True, trading_ready=True):
    report = NS(id='original')
    executor = NS(executor_info=report)
    service = NS(_original=Mock(), revoke=Mock(), _revoked=False, _quote_task=None,
                 hedge=NS(_busy=False), config=NS(id='main'), makers=None,
                 _owned_executors={'original': executor},
                 reporting=NS(enqueue=Mock(), _trading_admission=NS(owns_report=lambda info: info is report)),
                 start_makers=Mock(), _trading_ready=Mock(return_value=trading_ready))
    return NS(service=service, _is_stop_triggered=False, market_data_provider=NS(ready=ready),
              executor_orchestrator=NS(active_executors={'main': [executor]})), executor


def test_native_tick_reports_only_original_executor_without_sync_recorder_reads():
    r, executor = runner()
    PgServiceRunner.on_tick(r)
    r.service.reporting.enqueue.assert_called_once_with(executor.executor_info)
    r.service.revoke.assert_not_called()


@pytest.mark.parametrize('blocked', ['quote', 'hedge', 'provider', 'revoked', 'stopped'])
def test_tick_does_not_report_or_start_loop_inside_financial_source_or_revoked_window(blocked):
    r, _ = runner()
    r.service.makers = NS(task=None)
    if blocked == 'quote':
        r.service._quote_task = object()
    elif blocked == 'hedge':
        r.service.hedge._busy = True
    elif blocked == 'provider':
        r.market_data_provider.ready = False
    elif blocked == 'revoked':
        r.service._revoked = True
    else:
        r._is_stop_triggered = True
    PgServiceRunner.on_tick(r)
    r.service.reporting.enqueue.assert_not_called()
    r.service.start_makers.assert_not_called()


def test_tick_starts_only_explicit_schedule_and_never_restarts_a_completed_task():
    r, _ = runner()
    r.service.makers = NS(task=None)
    PgServiceRunner.on_tick(r)
    r.service.start_makers.assert_called_once()
    r.service.makers.task = object()
    PgServiceRunner.on_tick(r)
    r.service.start_makers.assert_called_once()


def test_tick_can_report_while_unarmed_but_cannot_start_the_maker_schedule():
    r, executor = runner(trading_ready=False)
    r.service.makers = NS(task=None)
    PgServiceRunner.on_tick(r)
    r.service.reporting.enqueue.assert_called_once_with(executor.executor_info)
    r.service._trading_ready.assert_called_once_with()
    r.service.start_makers.assert_not_called()
    r.service.revoke.assert_not_called()


@pytest.mark.parametrize('fault', ['executor', 'report', 'enqueue'])
def test_foreign_executor_or_reporting_failure_revokes_without_starting_makers(fault):
    r, _ = runner()
    r.service.makers = NS(task=None)
    if fault == 'executor':
        r.service._owned_executors = {}
    elif fault == 'report':
        r.service.reporting._trading_admission.owns_report = lambda info: False
    else:
        r.service.reporting.enqueue.side_effect = StorageError('unknown report')
    with pytest.raises(StorageError):
        PgServiceRunner.on_tick(r)
    r.service.revoke.assert_called_once()
    r.service.start_makers.assert_not_called()
