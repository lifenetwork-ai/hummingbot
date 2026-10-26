"""Reuse legacy PMM interpolation; adapt LIFE sizing rather than its executor."""
from contextlib import nullcontext
from decimal import Decimal
from test.hummingbot.strategy_v2.controllers.test_life_mm import configuration, make_controller
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy.pure_market_making.inventory_skew_calculator import (
    calculate_bid_ask_ratios_from_base_asset_ratio,
)
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.utils.inventory_skew import InventorySkewConfig, inventory_skew_ratios


@pytest.mark.parametrize('base,quote,price,target,range_', [
    ('85000', '10000', '.0036', '.03', '20000'), ('95000', '10000', '.0036', '.03', '20000'),
    ('70000', '10000', '.0036', '.03', '20000'), ('0', '100', '1', '.5', '20'),
    ('100', '0', '1', '.5', '20'), ('100', '100', '1', '.5', '20'),
    ('100', '10', '1', '.35', '200'), ('10', '100', '1', '.75', '200'),
])
def test_legacy_ratio_parity(base, quote, price, target, range_):
    inputs = tuple(Decimal(value) for value in (base, quote, price, target, range_))
    actual = inventory_skew_ratios(*inputs)
    legacy = calculate_bid_ask_ratios_from_base_asset_ratio(*(float(value) for value in inputs))
    assert actual == (Decimal(str(legacy.bid_ratio)), Decimal(str(legacy.ask_ratio)))


@pytest.mark.parametrize('index,value', [(0, '-1'), (1, 'NaN'), (2, '0'), (2, 'Infinity'), (2, '1e1000'), (4, '0'), (4, '1e-1000')])
def test_invalid_or_unrepresentable_legacy_inputs_do_not_invent_a_ratio(index, value):
    inputs = [Decimal('10'), Decimal('10'), Decimal('1'), Decimal('.5'), Decimal('2')]
    inputs[index] = Decimal(value)
    with pytest.raises(ValueError):
        inventory_skew_ratios(*inputs)


@pytest.mark.parametrize('field,value', [('target_base_pct', '-0.01'), ('target_base_pct', '1.01'),
                                         ('target_base_pct', 'NaN'), ('inventory_range_multiplier', '0')])
def test_invalid_skew_configuration_rejected(field, value):
    with pytest.raises(ValueError):
        InventorySkewConfig(**{field: value})


def controller(*, base='1000', cash='100', equity='1000', enabled=True, **updates):
    config = configuration(total_amount_quote=Decimal('100'), inventory_skew=dict(
        enabled=enabled, target_base_pct='.5', inventory_range_multiplier='1'), **updates)
    item, prices = make_controller(config)
    connector = item.market_data_provider.get_connector_with_fallback.return_value
    connector.name = 'okx'
    connector.account_balance_observations = {asset: dict(cash_balance=Decimal(amount), observed_at=1000)
                                              for asset, amount in [('LIFE', base), ('USDT', cash)]}
    connector.get_balance.side_effect = lambda asset: Decimal(base if asset == 'LIFE' else equity)
    connector.get_available_balance.side_effect = lambda asset: Decimal(base if asset == 'LIFE' else cash)
    connector.quantize_order_price.side_effect = lambda pair, price: price.quantize(Decimal('.000001'))
    connector.quantize_order_amount.side_effect = lambda pair, amount: (amount // Decimal('.001')) * Decimal('.001')
    connector.trading_rules['LIFE-USDT'] = TradingRule('LIFE-USDT', min_order_size=Decimal('.001'), min_notional_size=Decimal('.001'))
    from test.hummingbot.strategy_v2.controllers.test_quote_guard import book
    target = book(bids=[(.099, 100000)], asks=[(.101, 100000)], timestamp=1000)
    connector._order_tracker = SimpleNamespace(cached_orders={}, lost_orders={})
    connector.order_books['LIFE-USDT'] = target
    item.market_data_provider.get_order_book.side_effect = lambda name, pair: target
    item.update_reference()
    return item, connector, prices


def sizes(item):
    return {side: sum((action.executor_config.amount * action.executor_config.entry_price
                      for action in item.create_actions_proposal() if action.executor_config.side == side), Decimal('0'))
            for side in (TradeType.BUY, TradeType.SELL)}


def test_ratio_uses_observed_cash_not_equity_and_keeps_total_proposal_budget():
    item, _, _ = controller()
    status = item.processed_data['inventory_skew']
    assert status['state'] == 'active' and status['base_pct'] == Decimal('.5')
    assert status['buy_multiplier'] == status['sell_multiplier'] == 1
    values = sizes(item)
    assert sum(values.values()) <= 100
    assert abs(values[TradeType.BUY] - values[TradeType.SELL]) < Decimal('.001')


@pytest.mark.parametrize('base,cash,larger', [('100', '100', TradeType.BUY), ('2000', '10', TradeType.SELL)])
def test_under_and_overweight_bias_size_without_changing_reference_or_order_type(base, cash, larger):
    item, _, _ = controller(base=base, cash=cash)
    values = sizes(item)
    other = TradeType.SELL if larger == TradeType.BUY else TradeType.BUY
    assert values[larger] > values[other] and sum(values.values()) <= 100
    assert item.processed_data['reference_price'] == Decimal('.1')
    assert all(a.executor_config.triple_barrier_config.open_order_type.name == 'LIMIT_MAKER' for a in item.create_actions_proposal())


@pytest.mark.parametrize('failure', ['missing_cash', 'empty', 'calculation', 'unqualified_price'])
def test_optional_skew_unavailable_is_neutral_and_recovers_on_same_controller(failure):
    item, connector, _ = controller(base='100', cash='100')
    original = {key: dict(value) for key, value in connector.account_balance_observations.items()}
    if failure == 'missing_cash':
        connector.account_balance_observations['USDT']['cash_balance'] = None
    if failure == 'empty':
        for value in connector.account_balance_observations.values():
            value['cash_balance'] = Decimal('0')
    context = nullcontext()
    if failure == 'unqualified_price':
        context = patch.object(item, '_inventory_valuation_price', side_effect=ValueError('no outside valuation'))
    elif failure == 'calculation':
        context = patch('hummingbot.strategy_v2.utils.inventory_skew.calculate_bid_ask_ratios_from_base_asset_ratio',
                        side_effect=RuntimeError('optional'))
    with context:
        item.update_reference()
    assert item.processed_data['ready']
    status = item.processed_data['inventory_skew']
    assert status['reason_code'] == 'inventory_skew_unavailable'
    assert status['buy_multiplier'] == status['sell_multiplier'] == 1
    connector.account_balance_observations = original
    item.update_reference()
    assert item.processed_data['inventory_skew']['state'] == 'active'


def test_mandatory_balance_or_provider_failure_still_blocks_orders():
    item, connector, _ = controller()
    connector.get_balance.side_effect = lambda asset: Decimal('NaN')
    item.update_reference()
    assert not item.processed_data['ready'] and not item.create_actions_proposal()
    item, _, _ = controller()
    item.market_data_provider.ready = False
    assert not item.create_actions_proposal()


def test_skew_cannot_reallocate_blocked_buy_or_ignore_partial_active_quote_budget(monkeypatch):
    item, _, _ = controller(base='100', cash='100', runtime_config_managed=True)
    import hummingbot.core.order_submission_guard as gates
    monkeypatch.setattr(gates, 'postgres_requested', lambda: True)
    monkeypatch.setattr(gates, '_guard', SimpleNamespace(spot_outflow=SimpleNamespace(preview=lambda *args: False)))
    assert all(a.executor_config.side == TradeType.SELL for a in item.create_actions_proposal())
    pending = SimpleNamespace(is_active=True, is_trading=True, status=RunnableStatus.RUNNING,
                              custom_info={'level_id': 'buy_0'},
                              config=SimpleNamespace(side=TradeType.BUY, amount=Decimal('900'), entry_price=Decimal('.1')))
    item.executors_info = [pending]
    actions = item.create_actions_proposal()
    assert Decimal('90') + sum((a.executor_config.amount * a.executor_config.entry_price for a in actions), Decimal('0')) <= 100


def test_disabled_path_parity_and_perpetual_rejects_enabling_spot_policy():
    item, _, _ = controller(enabled=False)
    plain, _ = make_controller(configuration(total_amount_quote=Decimal('100')))
    plain.update_reference()
    for level in plain.get_not_active_levels_ids([]):
        assert item.get_price_and_amount(level) == plain.get_price_and_amount(level)
    with pytest.raises(ValueError, match='spot'):
        configuration(connector_name='okx_perpetual', inventory_skew=dict(enabled=True))


def test_asymmetric_level_allocations_are_scaled_down_together_and_minimum_levels_skipped():
    item, connector, _ = controller(base='100', cash='1000', buy_amounts_pct=[Decimal('9')], sell_amounts_pct=[Decimal('1')])
    assert item.processed_data['inventory_skew']['budget_scale'] < 1
    values = sizes(item)
    assert sum(values.values()) <= Decimal('100')
    connector.trading_rules['LIFE-USDT'] = TradingRule('LIFE-USDT', min_order_size=Decimal('100000'))
    assert not item.create_actions_proposal()


def test_one_interpolation_per_observation_and_decimal_final_sizes_do_not_round_up():
    item, connector, _ = controller(base='100', cash='100')
    with patch('controllers.market_making.life_mm.inventory_skew_ratios', wraps=inventory_skew_ratios) as calculation:
        item.update_reference()
        for _ in range(20):
            item.create_actions_proposal()
        assert calculation.call_count == 1
    connector.quantize_order_amount.side_effect = lambda pair, amount: amount + Decimal('.001')
    assert not item.create_actions_proposal()


def test_life_range_is_sum_of_all_unskewed_base_levels_at_actual_proposed_prices():
    item, _, _ = controller(base='85000', cash='10000', buy_spreads=[.01, .02], sell_spreads=[.01, .02])
    baseline = [item._baseline_price_and_amount(level) for level in item.get_not_active_levels_ids([])]
    expected = inventory_skew_ratios(Decimal('85000'), Decimal('10000'), Decimal('.1'), Decimal('.5'),
                                     sum((amount for _, amount in baseline), Decimal('0')))
    status = item.processed_data['inventory_skew']
    assert (status['buy_multiplier'], status['sell_multiplier']) == expected


def test_enabled_skew_never_uses_model_anchor_as_the_inventory_valuation():
    item, _, prices = controller(base='1000', cash='100')
    prices['LIFE-USDT'] = Decimal('.2')
    from hummingbot.core.data_type.order_book_row import OrderBookRow
    connector = item.market_data_provider.get_connector_with_fallback('okx')
    connector.order_books['LIFE-USDT'].apply_snapshot([OrderBookRow(.198, 100000, 1000)], [OrderBookRow(.202, 100000, 1000)], 1000)
    item.update_reference()
    assert item.processed_data['reference_price'] == Decimal('.1')
    assert item.processed_data['inventory_skew']['valuation_price'] == Decimal('.2')
    assert item.processed_data['inventory_skew']['base_pct'] == Decimal('2') / Decimal('3')


def test_inventory_status_uses_existing_authenticated_market_readiness_schema():
    from hummingbot.cli.admin_models import InventorySkewStatus
    item, _, _ = controller()
    status = InventorySkewStatus.model_validate(item.processed_data['inventory_skew']).model_dump(mode='json')
    assert status['scope'] == 'account_cash' and status['base_pct'] == '0.5'
    assert status['balance_observed_at'].endswith('Z')


@pytest.mark.parametrize('stamp', [1, 1100, float('nan')])
def test_known_stale_account_balances_block_mandatory_admission_and_recover(stamp):
    item, connector, _ = controller()
    connector.account_balance_observations['USDT']['observed_at'] = stamp
    item.update_reference()
    assert not item.processed_data['ready']
    assert item.processed_data['reason'] == 'account_balance_unavailable'
    assert not item.create_actions_proposal()
    connector.account_balance_observations['USDT']['observed_at'] = 1000
    item.update_reference()
    assert item.processed_data['ready'] and item.processed_data['inventory_skew']['state'] == 'active'


@pytest.mark.asyncio
async def test_both_market_send_readiness_waits_for_known_stale_spot_account_then_recovers():
    from test.hummingbot.cli.test_mm_service import setup_host
    host, controllers, provider = await setup_host()
    spot = next(item for item in controllers.values() if item.config.connector_name == 'okx')
    spot.config.inventory_skew = InventorySkewConfig(enabled=True)
    connector = provider.get_connector_with_fallback('okx')
    connector.account_balance_observations = {
        'LIFE': dict(cash_balance=Decimal('1000'), observed_at=1000),
        'USDT': dict(cash_balance=Decimal('1000'), observed_at=1),
    }
    assert not host.mm_runtime_ready() and host.mm_readiness_reason == 'account_balance_unavailable'
    connector.account_balance_observations['USDT']['observed_at'] = 1000
    assert host.mm_runtime_ready()


def test_own_only_quotes_are_not_an_independent_inventory_valuation():
    from test.hummingbot.strategy_v2.controllers.test_quote_guard import book, order
    item, connector, _ = controller()
    connector.in_flight_orders = {
        'bid': order(identity='bid', price='.099', amount='10'),
        'ask': order(identity='ask', side=TradeType.SELL, price='.101', amount='10'),
    }
    connector._order_tracker = SimpleNamespace(cached_orders={}, lost_orders={})
    target = book(bids=[(.099, 10)], asks=[(.101, 10)], timestamp=1000)
    item.market_data_provider.get_order_book.side_effect = lambda *args: target
    item.update_reference()
    assert item.processed_data['ready']
    assert item.processed_data['inventory_skew']['state'] == 'neutral'
    assert item.processed_data['inventory_skew']['valuation_price'] is None


@pytest.mark.parametrize('optional_metadata', [None, 'unavailable', {}])
def test_malformed_optional_cash_snapshot_cannot_fault_the_controller(optional_metadata):
    item, connector, _ = controller()
    connector.account_balance_observations['USDT'] = optional_metadata
    item.update_reference()
    assert item.processed_data['ready'] and not item.processed_data.get('internal_error')
    assert item.processed_data['inventory_skew']['reason_code'] == 'inventory_skew_unavailable'
    connector.account_balance_observations['USDT'] = dict(cash_balance=Decimal('100'), observed_at=1000)
    item.update_reference()
    assert item.processed_data['inventory_skew']['state'] == 'active'
