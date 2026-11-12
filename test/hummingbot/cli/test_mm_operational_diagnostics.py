"""Expected data outages are operational states; unexpected errors retain traces."""
import logging
from test.hummingbot.strategy_v2.executors.test_report_snapshots import ChangingExecutor, become_held, orchestrator
from types import SimpleNamespace
from unittest.mock import MagicMock

from hummingbot.connector.exchange_py_base import ExchangePyBase
from hummingbot.core.data_type.common import TradeType
from hummingbot.core.event.events import MarketOrderFailureEvent, OrderType
from hummingbot.core.operational_warnings import OperationalWarnings
from hummingbot.core.order_submission_guard import QuoteRefreshRequired
from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.executors.position_executor.position_executor import PositionExecutor


def test_valuation_outage_warns_once_preserves_unready_and_reports_recovery(caplog):
    item = ChangingExecutor('held')
    become_held(item)
    reports, price = orchestrator([item])
    price.stamp = 980000
    logger = logging.getLogger('test.mm.valuation')
    strategy = SimpleNamespace(executor_orchestrator=reports, controllers={}, logger=lambda: logger,
                               _managed_report_handle=None, _managed_report_pending={})
    with caplog.at_level(logging.INFO):
        StrategyV2Base.update_executors_info(strategy)
        StrategyV2Base.update_executors_info(strategy)
        assert strategy.controller_reports_error is not None
        assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
        price.stamp = 1000000
        StrategyV2Base.update_executors_info(strategy)
    assert strategy.controller_reports_error is None
    assert any('recover' in r.getMessage().lower() for r in caplog.records)


def test_quote_refresh_is_not_warned_again_by_executor(caplog):
    logger = logging.getLogger('test.mm.executor')
    executor = SimpleNamespace(logger=lambda: logger, _current_retries=0, _max_retries=10)
    event = MarketOrderFailureEvent(timestamp=1000, order_id='local-only', order_type=OrderType.LIMIT_MAKER,
                                    error_type='QuoteRefreshRequired', error_message='spread_policy_quote_rejected')
    with caplog.at_level(logging.DEBUG):
        PositionExecutor._log_order_failure(executor, 'Open', event, reprice=True)
    assert caplog.records and all(r.levelno < logging.WARNING for r in caplog.records)


def test_connector_coalesces_expected_quote_refresh_but_keeps_both_outcomes(caplog):
    logger = logging.getLogger('test.mm.connector')
    connector = SimpleNamespace(name_cap='Okx', logger=lambda: logger, _update_order_after_failure=MagicMock())
    with caplog.at_level(logging.DEBUG):
        for identity in ['first', 'second']:
            ExchangePyBase._on_order_failure(connector, identity, 'BTC-USDT', 1, TradeType.SELL,
                                             OrderType.LIMIT_MAKER, 100, QuoteRefreshRequired('spread_policy_quote_rejected'))
    assert connector._update_order_after_failure.call_count == 2
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


def test_summary_interval_recovery_and_bounded_retention():
    clock = [100.0]
    warnings = OperationalWarnings(maximum=2, clock=lambda: clock[0])
    assert warnings.observe('a') == (True, dict(total=1, suppressed=0, seconds=0))
    assert warnings.observe('a')[0] is False
    clock[0] += 30
    assert warnings.observe('a') == (True, dict(total=3, suppressed=1, seconds=30))
    assert warnings.recover('a') == dict(total=3, suppressed=0, seconds=30)
    assert warnings.recover('a') is None
    for key in range(100):
        warnings.observe(key)
    assert list(warnings.states) == [98, 99]


def test_unexpected_report_error_retains_error_trace(caplog):
    reports = MagicMock()
    reports.get_all_reports.side_effect = RuntimeError('unexpected_report_failure')
    strategy = SimpleNamespace(executor_orchestrator=reports, controllers={},
                               _managed_report_handle=None, _managed_report_pending={},
                               logger=lambda: logging.getLogger('test.mm.unexpected'))
    with caplog.at_level(logging.ERROR):
        StrategyV2Base.update_executors_info(strategy)
    assert strategy.controller_reports_error == 'RuntimeError'
    assert any(r.levelno == logging.ERROR and r.exc_info for r in caplog.records)
