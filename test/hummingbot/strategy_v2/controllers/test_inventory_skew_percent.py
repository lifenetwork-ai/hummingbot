"""Percentage bands reuse upstream sizing and preserve archived multiplier policy."""
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from test.hummingbot.strategy_v2.controllers.test_inventory_skew import controller, sizes
from test.hummingbot.strategy_v2.controllers.test_life_mm import configuration, make_controller
from types import SimpleNamespace

import pytest

from hummingbot.cli.admin_models import InventorySkewStatus
from hummingbot.client.config.mm_profiles import MMProfilesConfig
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.utils.inventory_skew import InventorySkewConfig


def percent_controller(pct='.5019', budget='100', **updates):
    # 100,000 quote cash value; a 100 quote proposal must not define the band.
    pct = Decimal(pct)
    item, connector, prices = controller(base=str(pct * 1000000), cash=str((1 - pct) * 100000),
                                         max_base_inventory='1000000', **updates)
    item.config.total_amount_quote = Decimal(budget)
    item.config.inventory_skew = InventorySkewConfig(enabled=True, target_base_pct='.5', inventory_range_pct='.02')
    item.update_reference()
    return item, connector, prices


@pytest.mark.parametrize('pct,buy,sell', [
    ('.47', '2', '0'), ('.48', '2', '0'), ('.49', '1.5', '.5'), ('.5', '1', '1'),
    ('.5019', '.905', '1.095'), ('.51', '.5', '1.5'), ('.52', '0', '2'), ('.53', '0', '2'),
])
def test_percent_band_matches_target_and_one_sided_boundaries(pct, buy, sell):
    item, _, _ = percent_controller(pct)
    status = item.processed_data['inventory_skew']
    assert abs(status['buy_multiplier'] - Decimal(buy)) < Decimal('1e-12')
    assert abs(status['sell_multiplier'] - Decimal(sell)) < Decimal('1e-12')
    actions = item.create_actions_proposal()
    assert actions
    expected_sides = {side for side, value in ((TradeType.BUY, buy), (TradeType.SELL, sell)) if Decimal(value) > 0}
    assert {action.executor_config.side for action in actions} == expected_sides


@pytest.mark.parametrize('budget', ['20', '100', '1000'])
def test_percent_band_is_independent_of_quote_budget_and_uses_existing_sizing(budget):
    item, _, _ = percent_controller(budget=budget)
    status = item.processed_data['inventory_skew']
    assert status['buy_multiplier'] == pytest.approx(Decimal('.905'), abs=Decimal('1e-12'))
    values = sizes(item)
    assert all(value > 0 for value in values.values())
    assert sum(values.values()) <= Decimal(budget)
    assert values[TradeType.SELL] > values[TradeType.BUY]


def test_observed_demo_inventory_no_longer_suppresses_buy_in_percent_mode():
    item, connector, _ = percent_controller()
    connector.account_balance_observations['LIFE']['cash_balance'] = Decimal('368393.01059750526')
    connector.account_balance_observations['USDT']['cash_balance'] = Decimal('36560.724422436106')
    item.update_reference()
    assert 0 < item.processed_data['inventory_skew']['buy_multiplier'] < 1
    item.config.inventory_skew = InventorySkewConfig(enabled=True)
    item.update_reference()
    assert item.processed_data['inventory_skew']['buy_multiplier'] == 0


def test_btc_demo_snapshot_proposes_both_sides_with_realistic_lots_inside_budget():
    from test.hummingbot.strategy_v2.controllers.test_quote_guard import book
    item, prices = make_controller(configuration(
        trading_pair='BTC-USDT', min_price='1', max_price='1000000', initial_anchor='63816.75',
        total_amount_quote='100', max_base_inventory='2', buy_spreads=[.001, .002], sell_spreads=[.001, .002],
        inventory_skew=dict(enabled=True, inventory_range_pct='.02')))
    prices['BTC-USDT'] = Decimal('63816.75')
    connector = item.market_data_provider.get_connector_with_fallback.return_value
    base, cash = Decimal('.577266956712'), Decimal('36560.724422436106')
    connector.account_balance_observations = {
        'BTC': dict(cash_balance=base, observed_at=1000), 'USDT': dict(cash_balance=cash, observed_at=1000)}
    connector.get_balance.side_effect = lambda asset: base if asset == 'BTC' else cash
    connector.get_available_balance.side_effect = connector.get_balance.side_effect
    connector.quantize_order_price.side_effect = lambda pair, price: price.quantize(Decimal('.01'))
    connector.quantize_order_amount.side_effect = lambda pair, amount: (amount // Decimal('.00000001')) * Decimal('.00000001')
    connector.trading_rules['BTC-USDT'] = TradingRule('BTC-USDT', min_order_size=Decimal('.00001'), min_notional_size=Decimal('5'))
    connector._order_tracker = SimpleNamespace(cached_orders={}, lost_orders={})
    target = book(bids=[(63816.5, 10)], asks=[(63817, 10)], timestamp=1000)
    connector.order_books['BTC-USDT'] = target
    item.market_data_provider.get_order_book.side_effect = lambda *args: target
    item.update_reference()
    actions = item.create_actions_proposal()
    assert [a.executor_config.side for a in actions].count(TradeType.BUY) == 2
    assert [a.executor_config.side for a in actions].count(TradeType.SELL) == 2
    notionals = [a.executor_config.amount * a.executor_config.entry_price for a in actions]
    assert all(value >= 5 for value in notionals) and sum(notionals) <= 100
    item.config.inventory_skew = InventorySkewConfig(enabled=True)
    item.update_reference()
    assert item.processed_data['inventory_skew']['buy_multiplier'] == 0
    assert all(a.executor_config.side == TradeType.SELL for a in item.create_actions_proposal())


def btc_edge_controller(pct, *, fee_floor=True, **updates):
    from test.hummingbot.strategy_v2.controllers.test_quote_guard import book
    item, prices = make_controller(configuration(
        trading_pair='BTC-USDT', min_price='1', max_price='1000000', initial_anchor='63816.75',
        total_amount_quote='100', max_base_inventory='2', buy_spreads=[.001, .002], sell_spreads=[.001, .002],
        inventory_skew=dict(enabled=True, inventory_range_pct='.02'), spread_policy=dict(fee_floor=fee_floor), **updates))
    prices['BTC-USDT'] = Decimal('63816.75')
    connector = item.market_data_provider.get_connector_with_fallback.return_value
    base = Decimal(pct) * 100000 / prices['BTC-USDT']
    cash = (1 - Decimal(pct)) * 100000
    connector.account_balance_observations = {
        'BTC': dict(cash_balance=base, observed_at=1000), 'USDT': dict(cash_balance=cash, observed_at=1000)}
    connector.get_balance.side_effect = lambda asset: base if asset == 'BTC' else cash
    connector.get_available_balance.side_effect = connector.get_balance.side_effect
    connector.get_fee.return_value = AddedToCostTradeFee(percent=Decimal('.0008'))
    lot = Decimal('.00000001')
    connector.quantize_order_price.side_effect = lambda trading_pair, price: (price // Decimal('.1')) * Decimal('.1')
    connector.get_order_price_quantum.return_value = Decimal('.1')
    connector.quantize_order_amount.side_effect = lambda trading_pair, amount: (amount // lot) * lot
    connector.trading_rules['BTC-USDT'] = TradingRule('BTC-USDT', min_order_size=Decimal('.00002'),
                                                      min_price_increment=Decimal('.1'), min_base_amount_increment=lot)
    connector._order_tracker = SimpleNamespace(cached_orders={}, lost_orders={})
    target = book(bids=[(63816.5, 10)], asks=[(63817, 10)], timestamp=1000)
    connector.order_books['BTC-USDT'] = target
    item.market_data_provider.get_order_book.side_effect = lambda *args: target
    item.update_reference()
    return item, connector


@pytest.mark.parametrize('pct, borrows_from_buy', [('.4802', True), ('.4809', False)])
def test_btc_edge_keeps_one_exchange_minimum_sell_within_allocation(pct, borrows_from_buy):
    item, _ = btc_edge_controller(pct)
    buy_multiplier, sell_multiplier, scale = item._inventory_factors
    original_buy = sum(price * amount * buy_multiplier * scale for price, amount in (
        item._baseline_price_and_amount(level) for level in ('buy_0', 'buy_1')))
    original_sell = sum(price * amount * sell_multiplier * scale for price, amount in (
        item._baseline_price_and_amount(level) for level in ('sell_0', 'sell_1')))
    actions = [action.executor_config for action in item.create_actions_proposal()]
    sell, = [config for config in actions if config.side == TradeType.SELL]
    deficit = max(Decimal(0), sell.entry_price * sell.amount - original_sell)
    proposed_buy = sum(price * amount for price, amount in (
        item.get_price_and_amount(level) for level in ('buy_0', 'buy_1')))
    assert (deficit > 0) is borrows_from_buy
    assert abs(proposed_buy - (original_buy - deficit)) < Decimal('1e-20')
    assert sell.amount == Decimal('.00002')
    assert len([config for config in actions if config.side == TradeType.BUY]) == 2
    assert sum(config.amount * config.entry_price for config in actions) <= 100
    assert sell.use_configured_entry_price
    queued = sell.model_copy(update={'controller_id': item.config.id})
    assert item.prepare_entry_order(queued) == queued


def test_btc_minimum_sell_uses_a_fixed_passive_price_without_a_fee_floor():
    item, _ = btc_edge_controller('.4802', fee_floor=False)
    sell, = [a.executor_config for a in item.create_actions_proposal() if a.executor_config.side == TradeType.SELL]
    assert sell.entry_price >= max(Decimal('63817'), item._baseline_price_and_amount(sell.level_id)[0])
    assert sell.use_configured_entry_price
    queued = sell.model_copy(update={'controller_id': item.config.id})
    assert item.prepare_entry_order(queued) == queued


def test_btc_at_lower_inventory_band_does_not_restore_sell():
    item, _ = btc_edge_controller('.48')
    assert all(action.executor_config.side == TradeType.BUY for action in item.create_actions_proposal())
    assert 'inventory_skew_sell_suppressed' in item.quote_diagnostics['reasons']['SELL']


@pytest.mark.parametrize('reserved,expected', [('98', True), ('99', False)])
def test_btc_minimum_sell_respects_existing_buy_reservations(reserved, expected):
    item, _ = btc_edge_controller('.4802')
    active_buy = SimpleNamespace(is_active=True, is_trading=False, status=RunnableStatus.RUNNING,
                                 custom_info={'level_id': 'buy_0'}, config=SimpleNamespace(
                                     side=TradeType.BUY, amount=Decimal(reserved) / 64000, entry_price=Decimal(64000)))
    item.executors_info = [active_buy]
    actions = [action.executor_config for action in item.create_actions_proposal()]
    assert any(config.side == TradeType.SELL for config in actions) is expected
    assert Decimal(reserved) + sum(config.amount * config.entry_price for config in actions) <= 100
    if not expected:
        assert 'allocation_limit' in item.quote_diagnostics['reasons']['SELL']


def test_btc_minimum_sell_never_exceeds_available_base():
    item, connector = btc_edge_controller('.4802')
    available_quote = connector.get_available_balance('USDT')
    connector.get_available_balance.side_effect = lambda asset: Decimal('.00001999') if asset == 'BTC' else available_quote
    assert all(action.executor_config.side == TradeType.BUY for action in item.create_actions_proposal())
    assert 'insufficient_base_balance' in item.quote_diagnostics['reasons']['SELL']


@pytest.mark.parametrize('change', ['maker_price', 'inventory_boundary'])
def test_btc_queued_minimum_sell_revalidates_original_budget_and_inventory(change):
    from hummingbot.core.data_type.common import PriceType

    item, connector = btc_edge_controller('.4802')
    sell, = [a.executor_config for a in item.create_actions_proposal() if a.executor_config.side == TradeType.SELL]
    queued = sell.model_copy(update={'controller_id': item.config.id})
    if change == 'maker_price':
        item.market_data_provider.get_price_by_type.side_effect = lambda name, pair, kind: (
            sell.entry_price + Decimal('1') if kind == PriceType.BestAsk else Decimal('63816.75'))
    else:
        connector.account_balance_observations['BTC']['cash_balance'] *= Decimal('.99')
    assert item.prepare_entry_order(queued) is None


def test_btc_edge_floor_cannot_turn_an_unfunded_level_into_a_sell():
    item, connector = btc_edge_controller('.4802')
    connector.trading_rules['BTC-USDT'] = TradingRule(
        'BTC-USDT', min_order_size=Decimal('.001'), min_base_amount_increment=Decimal('.00000001'))
    item.update_reference()
    assert not any(a.executor_config.side == TradeType.SELL for a in item.create_actions_proposal())
    assert 'inventory_skew_sell_below_minimum' in item.quote_diagnostics['reasons']['SELL']


@pytest.mark.parametrize('value', ['0', '-.01', '.50001', '2', 'NaN', 'Infinity'])
def test_percent_range_must_be_a_finite_fraction_of_cash_value(value):
    with pytest.raises(ValueError):
        InventorySkewConfig(inventory_range_pct=value)


@pytest.mark.parametrize('target', ['0', '.01', '.99', '1'])
def test_percent_band_must_fit_either_side_of_target(target):
    with pytest.raises(ValueError):
        InventorySkewConfig(target_base_pct=target, inventory_range_pct='.02')


def test_modes_cannot_be_combined_and_percent_configuration_round_trips():
    with pytest.raises(ValueError, match='inventory_range'):
        InventorySkewConfig(inventory_range_pct='.02', inventory_range_multiplier='1')
    config = InventorySkewConfig(enabled=True, inventory_range_pct='.02')
    archived = config.model_dump(mode='json')
    assert 'inventory_range_multiplier' not in archived
    assert InventorySkewConfig.model_validate(archived) == config
    legacy = InventorySkewConfig().model_dump(mode='json')
    assert legacy == dict(enabled=False, target_base_pct='0.5', inventory_range_multiplier='1')
    schema = InventorySkewConfig.model_json_schema(mode='serialization')
    assert all('default' not in schema['properties'][name] for name in ('inventory_range_pct', 'inventory_range_multiplier'))


def test_percent_status_describes_effective_band_without_claiming_readiness():
    item, connector, _ = percent_controller()
    status = InventorySkewStatus.model_validate(item.processed_data['inventory_skew']).model_dump(mode='json')
    assert status['range_mode'] == 'portfolio_pct'
    assert Decimal(status['target_base_pct']) == Decimal('.5')
    assert Decimal(status['inventory_range_pct']) == Decimal('.02')
    connector.account_balance_observations['USDT']['cash_balance'] = None
    item.update_reference()
    status = item.processed_data['inventory_skew']
    assert status['state'] == 'neutral' and status['inventory_range_pct'] is None


def test_percent_skew_retains_minimum_lot_balance_and_hard_cap_checks():
    item, connector, _ = percent_controller()
    connector.trading_rules['LIFE-USDT'] = TradingRule('LIFE-USDT', min_order_size=Decimal('1000000'))
    assert not item.create_actions_proposal()
    item, connector, _ = percent_controller()
    item.config.max_base_inventory = Decimal('1')
    assert not item.create_actions_proposal()
    connector.get_balance.side_effect = lambda asset: Decimal('NaN')
    item.update_reference()
    assert not item.processed_data['ready'] and not item.create_actions_proposal()


@pytest.mark.parametrize('active_quote,has_room', [('40', True), ('90', False)])
def test_percent_skew_keeps_outflow_fence_and_partial_active_budget(monkeypatch, active_quote, has_room):
    item, _, _ = percent_controller(runtime_config_managed=True)
    import hummingbot.core.order_submission_guard as gates
    monkeypatch.setattr(gates, 'postgres_requested', lambda: True)
    monkeypatch.setattr(gates, '_guard', SimpleNamespace(spot_outflow=SimpleNamespace(preview=lambda *args, **kwargs: None)))
    funded_sells = item.create_actions_proposal()
    assert funded_sells and all(a.executor_config.side == TradeType.SELL for a in funded_sells)
    pending = SimpleNamespace(is_active=True, is_trading=True, status=RunnableStatus.RUNNING,
                              custom_info={'level_id': 'buy_0'},
                              config=SimpleNamespace(side=TradeType.BUY, amount=Decimal(active_quote) * 10, entry_price=Decimal('.1')))
    item.executors_info = [pending]
    actions = item.create_actions_proposal()
    assert bool(actions) is has_room
    assert Decimal(active_quote) + sum((a.executor_config.amount * a.executor_config.entry_price for a in actions), Decimal('0')) <= 100


def test_percent_band_uses_current_cash_value_and_remains_spot_only():
    item, connector, _ = percent_controller()
    initial = item.processed_data['inventory_skew']['buy_multiplier']
    for observation in connector.account_balance_observations.values():
        observation['cash_balance'] *= 10
    item.update_reference()
    assert abs(item.processed_data['inventory_skew']['buy_multiplier'] - initial) < Decimal('1e-12')
    with pytest.raises(ValueError, match='spot'):
        configuration(connector_name='okx_perpetual', inventory_skew=dict(enabled=True, inventory_range_pct='.02'))


@pytest.mark.asyncio
async def test_archived_legacy_catalogue_keeps_hash_and_percent_snapshot_round_trips():
    path = Path(__file__).resolve().parents[4] / 'deploy/cloudrun/conf/life_mm_profiles.yaml'
    original = await MMProfilesConfig.load(path)
    archived = original.model_dump(mode='json')
    for market in archived['markets'].values():
        market['inventory_skew'] = dict(enabled=False, target_base_pct='0.5', inventory_range_multiplier='1')
    old_hash = hashlib.sha256(json.dumps(archived, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    restored = MMProfilesConfig.model_validate(archived)
    assert restored.catalogue_hash() == old_hash
    restored.markets['spot'].inventory_skew = InventorySkewConfig(enabled=True, inventory_range_pct='.02')
    percent = MMProfilesConfig.model_validate(restored.model_dump(mode='json'))
    assert percent.catalogue_hash() == restored.catalogue_hash() != old_hash
    assert MMProfilesConfig.model_validate(archived).catalogue_hash() == old_hash


@pytest.mark.asyncio
async def test_demo_selects_percent_band_for_spot_only():
    path = Path(__file__).resolve().parents[4] / 'deploy/cloudrun/demo/life_mm_profiles.yaml'
    catalogue = await MMProfilesConfig.load(path)
    assert catalogue.markets['spot'].inventory_skew.inventory_range_pct == Decimal('.02')
    assert not catalogue.markets['perpetual'].inventory_skew.enabled
