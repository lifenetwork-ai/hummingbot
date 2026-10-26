from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.core.data_type.common import TradeType
from hummingbot.model.position import Position
from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator, PositionHold
from hummingbot.strategy_v2.models.executors_info import PerformanceReport

SCOPE = {"life_spot": SimpleNamespace(connector_name="okx", trading_pair="LIFE-USDT"),
         "life_perpetual": SimpleNamespace(connector_name="okx_perpetual", trading_pair="LIFE-USDT")}


def orchestrator(*, mm=True, price="110", book_time=1000):
    provider = MagicMock()
    provider.time.return_value = 1000
    provider.get_price_by_type.return_value = Decimal(price)
    provider.get_order_book.return_value = SimpleNamespace(snapshot_uid=book_time * 1000, last_diff_uid=0)
    strategy = SimpleNamespace(config=SimpleNamespace(mm_drawdown_scope=SCOPE if mm else None,
                                                      mm_drawdown_max_data_age_seconds=Decimal("10")),
                               controllers={"life_spot": object()}, markets={"okx": {"LIFE-USDT"}},
                               connectors={"okx": object()}, market_data_provider=provider, current_timestamp=1000)
    item = ExecutorOrchestrator.__new__(ExecutorOrchestrator)
    item.strategy = strategy
    item.cached_performance = {key: PerformanceReport() for key in SCOPE}
    item.active_executors = {key: [] for key in SCOPE}
    item.positions_held = {key: [] for key in SCOPE}
    item.initial_positions_by_controller = {}
    return item


def held():
    position = PositionHold("okx_perpetual", "LIFE-USDT", TradeType.BUY)
    position.net_amount_base = Decimal("2")
    position.avg_entry_price = Decimal("100")
    position.realized_pnl_quote = Decimal("3")
    position.cum_fees_quote = Decimal("1")
    return position


def test_retained_disabled_market_and_held_fees_remain_in_the_existing_mm_report():
    item = orchestrator()
    item.positions_held["life_perpetual"] = [held()]
    report = item.generate_performance_report("life_perpetual")
    assert report.realized_pnl_quote == Decimal("2")
    assert report.unrealized_pnl_quote == Decimal("20")
    assert report.global_pnl_quote == Decimal("22")


@pytest.mark.parametrize("price,book_time", [("NaN", 1000), ("0", 1000), ("110", 980)])
def test_missing_or_stale_held_valuation_is_not_zero_pnl(price, book_time):
    item = orchestrator(price=price, book_time=book_time)
    item.positions_held["life_perpetual"] = [held()]
    with pytest.raises(ValueError):
        item.generate_performance_report("life_perpetual")


def test_legacy_report_keeps_its_existing_disabled_market_behavior():
    item = orchestrator(mm=False)
    item.positions_held["life_perpetual"] = [held()]
    assert item.generate_performance_report("life_perpetual").global_pnl_quote == 0


def test_net_fee_basis_does_not_jump_when_active_exposure_becomes_a_position_hold():
    item = orchestrator()
    config = SimpleNamespace(connector_name="okx_perpetual", trading_pair="LIFE-USDT")
    active = SimpleNamespace(config=config, executor_info=SimpleNamespace(is_done=False, net_pnl_quote=Decimal("19"),
                                                                          filled_amount_quote=Decimal("200"), config=config))
    item.active_executors["life_perpetual"] = [active]
    before = item.generate_performance_report("life_perpetual").global_pnl_quote
    position = held()
    position.realized_pnl_quote = Decimal("0")
    item.active_executors["life_perpetual"] = []
    item.positions_held["life_perpetual"] = [position]
    assert item.generate_performance_report("life_perpetual").global_pnl_quote == before == Decimal("19")


def test_mm_initialization_loads_full_bound_scope_not_only_enabled_controllers():
    item = orchestrator()
    db_position = Position(id="retained", controller_id="life_perpetual", connector_name="okx_perpetual",
                           trading_pair="LIFE-USDT", side="BUY", amount=Decimal("2"), breakeven_price=Decimal("100"),
                           realized_pnl_quote=Decimal("3"), cum_fees_quote=Decimal("1"), volume_traded_quote=Decimal("200"))
    recorder = MagicMock()
    recorder.iter_executor_performance.return_value = (row for row in [])
    recorder.get_all_positions.return_value = [db_position]
    recorder.has_uncovered_positions.return_value = False
    item.cached_performance, item.active_executors, item.positions_held = {}, {}, {}
    with patch.object(MarketsRecorder, "get_instance", return_value=recorder):
        item._initialize_cached_performance()
    assert set(item.positions_held) == set(SCOPE)
    assert item.positions_held["life_perpetual"][0].net_amount_base == Decimal("2")
    assert set(recorder.iter_executor_performance.call_args.args[0]) == set(SCOPE)


def test_storage_retains_disabled_market_exposure():
    item = orchestrator()
    item.positions_held["life_perpetual"] = [held()]
    with patch.object(MarketsRecorder, "get_instance") as recorder:
        item.store_all_positions()
    recorder.return_value.update_or_store_position.assert_called_once()
    saved = recorder.return_value.update_or_store_position.call_args.args[0]
    assert saved.amount == Decimal("2") and saved.connector_name == "okx_perpetual"


def test_flat_held_history_has_known_realized_pnl_without_requiring_a_market_price():
    item = orchestrator(price="NaN", book_time=0)
    position = held()
    position.net_amount_base = Decimal("0")
    item.positions_held["life_perpetual"] = [position]
    assert item.generate_performance_report("life_perpetual").global_pnl_quote == Decimal("2")
