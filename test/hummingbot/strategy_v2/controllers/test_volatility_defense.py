"""Volatility defense contracts on the existing LIFE execution path."""
from decimal import Decimal
from pathlib import Path
from test.hummingbot.strategy_v2.controllers.test_life_mm import configuration, make_controller
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml

from hummingbot.client.config.mm_profiles import MMProfilesConfig
from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee
from hummingbot.strategy_v2.utils.mm_spread import SpreadPolicy, SpreadPolicyConfig


@pytest.fixture
def clock():
    """Wall time and monotonic recovery time advance independently in tests."""
    return SimpleNamespace(epoch=1200, monotonic=100)


def defense_policy(**updates):
    values = dict(enabled=True, min_multiplier='1', on_unavailable='static', length=5,
                  fallback_buy_spreads=['.01'], fallback_sell_spreads=['.01'],
                  defense=dict(reduce_at_natr='.02', stop_at_natr='.05', resume_below_natr='.015',
                               reduced_size_factor='.5', recovery_seconds=60))
    values.update(updates)
    return dict(fee_floor=True, volatility=values)


def sample(policy, monkeypatch, raw, *, epoch=1200, monotonic=100):
    from test.hummingbot.strategy_v2.controllers.test_life_mm_spread import feed
    monkeypatch.setattr('hummingbot.strategy_v2.utils.mm_spread.natr_value', lambda *args: Decimal(raw))
    policy.refresh(feed(now=epoch), epoch)
    policy.sample_defense(epoch, monotonic)


def test_raw_natr_survives_multiplier_cap_and_controls_size(monkeypatch):
    policy = SpreadPolicy(SpreadPolicyConfig(**defense_policy()))
    sample(policy, monkeypatch, '.04')
    assert policy.multiplier(1200) == 3
    assert policy.raw_natr == Decimal('.04')
    assert policy.size_factor(1200) == Decimal('.5')


def test_reduced_recovery_requires_distinct_candles_and_elapsed_time(monkeypatch):
    policy = SpreadPolicy(SpreadPolicyConfig(**defense_policy()))
    sample(policy, monkeypatch, '.02')
    sample(policy, monkeypatch, '.01', epoch=1260, monotonic=110)
    for _ in range(10):
        policy.sample_defense(1260, 1000)
        assert policy.size_factor(1260) == Decimal('.5')
    sample(policy, monkeypatch, '.01', epoch=1320, monotonic=170)
    assert policy.size_factor(1320) == 1


def test_static_fallback_uses_smaller_size_and_disabled_policy_is_unchanged():
    policy = SpreadPolicy(SpreadPolicyConfig(**defense_policy()))
    assert policy.size_factor(1200) == Decimal('.5')
    assert SpreadPolicy(SpreadPolicyConfig()).size_factor(1200) == 1


def test_extreme_pause_cannot_be_cleared_by_fallback_or_one_candle(monkeypatch):
    policy = SpreadPolicy(SpreadPolicyConfig(**defense_policy()))
    sample(policy, monkeypatch, '.05')
    assert policy.defense.state == 'paused'
    assert policy.size_factor(1200) == 0
    policy.sample_defense(2000, 1000)
    assert policy.defense.state == 'paused'
    sample(policy, monkeypatch, '.019', epoch=2060, monotonic=1100)
    assert policy.defense.state == 'paused'
    sample(policy, monkeypatch, '.019', epoch=2120, monotonic=1160)
    assert policy.defense.state == 'reduced'
    sample(policy, monkeypatch, '.01', epoch=2180, monotonic=1220)
    assert policy.defense.state == 'reduced'
    sample(policy, monkeypatch, '.01', epoch=2240, monotonic=1280)
    assert policy.defense.state == 'normal'


def test_paused_spread_composition_cannot_offer_static_fallback(monkeypatch):
    policy = SpreadPolicy(SpreadPolicyConfig(**defense_policy()))
    sample(policy, monkeypatch, '.05')
    connector = MagicMock()
    connector.get_fee.return_value = AddedToCostTradeFee(percent=Decimal('.001'))
    with pytest.raises(ValueError, match='volatility_paused'):
        policy.compose(connector, 'LIFE-USDT', Decimal('.1'), [.01], [.01], [50], [50], now=2000)


def test_recovery_rejects_backwards_replayed_candles(monkeypatch):
    policy = SpreadPolicy(SpreadPolicyConfig(**defense_policy()))
    sample(policy, monkeypatch, '.02')
    sample(policy, monkeypatch, '.01', epoch=1320, monotonic=110)
    sample(policy, monkeypatch, '.01', epoch=1260, monotonic=200)
    assert policy.size_factor(1260) == Decimal('.5')


@pytest.mark.asyncio
async def test_pause_sends_position_preserving_cancellation_before_checkpoint(monkeypatch, clock):
    from unittest.mock import AsyncMock
    controller = defended_controller(monkeypatch, clock)
    events = []
    controller.bind_volatility_defense(paused=False, checkpoint=lambda paused: events.append(('checkpoint', paused)) or True)
    controller.send_actions = AsyncMock(side_effect=lambda actions: events.append(('cancel', actions)))
    controller.executors_info = [SimpleNamespace(id='working', is_active=True)]
    monkeypatch.setattr('hummingbot.strategy_v2.utils.mm_spread.natr_value', lambda *args: Decimal('.05'))
    await controller.control_task()
    assert events[0][0] == 'cancel'
    assert events[0][1][0].keep_position
    assert events[1] == ('checkpoint', True)
    assert controller.processed_data['reason'] == 'volatility_paused'
    assert not controller.processed_data['quote_guard']['can_admit']
    assert not controller.processed_data['ready']


def test_restored_pause_requires_fresh_recovery_before_pending_clear(monkeypatch, clock):
    controller = defended_controller(monkeypatch, clock)
    controller.bind_volatility_defense(paused=True, checkpoint=lambda paused: False)
    monkeypatch.setattr('hummingbot.strategy_v2.utils.mm_spread.natr_value', lambda *args: Decimal('.01'))
    controller.update_reference(sample_guard=True)
    assert controller.processed_data['reason'] == 'volatility_paused'
    from test.hummingbot.strategy_v2.controllers.test_life_mm_spread import feed
    clock.epoch, clock.monotonic = 1260, 160
    controller.market_data_provider.candles_feeds['feed'] = feed(now=clock.epoch)
    controller.update_reference(sample_guard=True)
    assert controller.processed_data['reason'] == 'volatility_checkpoint_pending'
    assert not controller.sync_volatility_checkpoint()
    assert controller._volatility_saved_pause


def test_defense_status_is_typed_english_and_polling_does_not_sample(monkeypatch, clock, caplog):
    import logging

    from hummingbot.cli.admin_models import SpreadPolicyStatus
    controller = defended_controller(monkeypatch, clock)
    monkeypatch.setattr('hummingbot.strategy_v2.utils.mm_spread.natr_value', lambda *args: Decimal('.025'))
    with caplog.at_level(logging.DEBUG):
        controller.update_reference(sample_guard=True)
        for _ in range(10):
            controller.update_reference()
    status = SpreadPolicyStatus.model_validate(controller.processed_data['spread_policy']).model_dump(mode='json')
    assert status['raw_natr'] == '0.025'
    assert status['defense']['label'] == 'Trading with smaller orders'
    assert status['defense']['effective_allocation_quote'] == '100.0'
    assert status['defense']['evaluated_at'].endswith('Z')
    assert controller._spread_policy.defense.good_samples == 0
    assert len([row for row in caplog.records if getattr(row, 'event', None) == 'mm_volatility_defense_changed']) == 1
    from test.hummingbot.strategy_v2.controllers.test_life_mm_spread import feed
    monkeypatch.setattr('hummingbot.strategy_v2.utils.mm_spread.natr_value', lambda *args: Decimal('.01'))
    with caplog.at_level(logging.DEBUG):
        for clock.epoch, clock.monotonic in [(1260, 110), (1320, 170)]:
            controller.market_data_provider.candles_feeds['feed'] = feed(now=clock.epoch)
            controller.update_reference(sample_guard=True)
    changes = [row for row in caplog.records if getattr(row, 'event', None) == 'mm_volatility_defense_changed']
    assert len(changes) == 2
    assert changes[-1].levelno == logging.INFO


def test_failed_quote_validation_logs_only_final_defense_decision(monkeypatch, clock, caplog):
    import logging
    controller = defended_controller(monkeypatch, clock)
    controller.quote_price_valid = MagicMock(return_value=False)
    monkeypatch.setattr('hummingbot.strategy_v2.utils.mm_spread.natr_value', lambda *args: Decimal('.025'))
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            controller.update_reference(sample_guard=True)
    changes = [row for row in caplog.records if getattr(row, 'event', None) == 'mm_volatility_defense_changed']
    assert len(changes) == 1
    assert controller.processed_data['spread_policy']['defense']['state'] == 'waiting'


@pytest.mark.parametrize('updates', [dict(min_multiplier='.5'), dict(enabled=False),
                                     dict(defense=dict(reduce_at_natr='.02', stop_at_natr='.01',
                                                       resume_below_natr='.015', reduced_size_factor='.5', recovery_seconds=60))])
def test_defense_requires_consistent_volatility_settings(updates):
    with pytest.raises(ValueError):
        SpreadPolicyConfig(**defense_policy(**updates))


def test_defense_rejects_tight_fallback_and_unguarded_price():
    with pytest.raises(ValueError, match='fallback'):
        configuration(quote_guards=dict(enabled=True, max_deviation_bps='100', resume_deviation_bps='50'),
                      spread_policy=defense_policy(fallback_buy_spreads=['.001']))
    with pytest.raises(ValueError, match='guards'):
        configuration(spread_policy=defense_policy())


def defended_controller(monkeypatch, clock, **updates):
    from test.hummingbot.strategy_v2.controllers.test_life_mm_spread import feed

    from hummingbot.strategy_v2.utils.quote_guard import MarketObservation
    values = dict(runtime_config_managed=True, mm_session_id='session', mm_generation=0,
                  total_amount_quote='200', spread_policy=defense_policy(),
                  quote_guards=dict(enabled=True, max_deviation_bps='200', resume_deviation_bps='100'))
    values.update(updates)
    controller, _ = make_controller(configuration(**values))
    controller.bind_volatility_defense(paused=False, checkpoint=lambda paused: True)
    controller._session_admitted = True
    controller._evaluate_guard = MagicMock(return_value=dict(ready=True, can_admit=True))
    monkeypatch.setattr('controllers.market_making.life_mm.observe_market',
                        lambda *args, **kwargs: MarketObservation(Decimal('.1'), clock.epoch, clock.epoch))
    monkeypatch.setattr('controllers.market_making.life_mm.time.monotonic', lambda: clock.monotonic)
    provider = controller.market_data_provider
    provider.time.side_effect = lambda: clock.epoch
    provider._generate_candle_feed_key.return_value = 'feed'
    provider.candles_feeds = {'feed': feed(now=clock.epoch)}
    from test.hummingbot.strategy_v2.controllers.test_quote_guard import book
    provider.get_order_book.side_effect = lambda *args: book(bids=[(.09, 100000)], asks=[(.11, 100000)], timestamp=clock.epoch)
    connector = provider.get_connector_with_fallback.return_value
    connector.name = 'okx'
    connector.get_fee.return_value = AddedToCostTradeFee(percent=Decimal('.001'))
    connector.trading_rules['LIFE-USDT'] = SimpleNamespace(min_order_size=Decimal('.001'), min_notional_size=Decimal('0'))
    connector.quantize_order_price.side_effect = lambda pair, price: price
    connector.quantize_order_amount.side_effect = lambda pair, amount: amount
    return controller


def test_reduction_scales_budget_and_rejects_stale_level_terms(monkeypatch, clock):
    controller = defended_controller(monkeypatch, clock)
    monkeypatch.setattr('hummingbot.strategy_v2.utils.mm_spread.natr_value', lambda *args: Decimal('.025'))
    controller.update_reference(sample_guard=True)
    assert controller.processed_data['ready']
    assert controller._quote_budget() == 100
    price, amount = controller.get_price_and_amount('buy_0')
    assert price * amount == pytest.approx(Decimal('50'))
    assert controller.spread_quote_valid(TradeType.BUY, price, amount, level_id='buy_0')
    assert not controller.spread_quote_valid(TradeType.BUY, price, amount * 2, level_id='buy_0')
    assert not controller.spread_quote_valid(TradeType.BUY, price, amount)


@pytest.mark.parametrize('bootstrap,skew', [(False, False), (True, False), (False, True), (True, True)])
def test_reduced_allocation_composes_with_bootstrap_and_skew(monkeypatch, clock, bootstrap, skew):
    updates = dict(inventory_skew=dict(enabled=skew))
    if bootstrap:
        updates['quote_guards'] = dict(enabled=True, mode='bootstrap_then_market',
                                       max_deviation_bps='200', resume_deviation_bps='100',
                                       bootstrap=dict(duration_seconds=60, max_anchor_move_bps='900', max_quote='40',
                                                      min_external_depth_quote='100', max_market_spread_bps='200', qualification_seconds='1'))
    controller = defended_controller(monkeypatch, clock, **updates)
    connector = controller.market_data_provider.get_connector_with_fallback.return_value
    connector.account_balance_observations = {
        asset: dict(cash_balance=value, observed_at=clock.epoch) for asset, value in [('LIFE', '1000'), ('USDT', '150')]}
    controller._evaluate_guard.return_value = dict(ready=True, can_admit=True, market_mid=Decimal('.1'))
    controller.update_reference(sample_guard=True)
    assert controller.processed_data['ready']
    assert controller._quote_budget() == (20 if bootstrap else 100)
    amounts = [controller.get_price_and_amount(level) for level in ['buy_0', 'sell_0']]
    assert sum(price * amount for price, amount in amounts) <= controller._quote_budget()
    if skew:
        assert controller.processed_data['inventory_skew']['state'] == 'active'


def test_each_level_uses_its_own_spread_and_stable_quotes_are_not_cancelled(monkeypatch, clock):
    controller = defended_controller(monkeypatch, clock, buy_spreads=[.01, .02], sell_spreads=[.01, .02],
                                     spread_policy=defense_policy(fallback_buy_spreads=['.01', '.02'], fallback_sell_spreads=['.01', '.02']))
    controller.update_reference(sample_guard=True)
    price, amount = controller.get_price_and_amount('buy_1')
    assert not controller.spread_quote_valid(TradeType.BUY, Decimal('.099'), amount / 2, level_id='buy_1')
    from hummingbot.strategy_v2.models.base import RunnableStatus
    controller.executors_info = [SimpleNamespace(id='ok', is_active=True, status=RunnableStatus.RUNNING,
                                                 config=SimpleNamespace(level_id='buy_1', side=TradeType.BUY,
                                                                        amount=amount, entry_price=price))]
    for _ in range(10):
        controller.update_reference()
        assert controller._defense_refresh_actions() == []


def test_reduction_cancels_oversized_quotes_once_without_replacing_pending(monkeypatch, clock):
    from hummingbot.strategy_v2.models.base import RunnableStatus
    controller = defended_controller(monkeypatch, clock)
    controller.update_reference(sample_guard=True)  # Static fallback is reduced too.
    price, amount = controller.get_price_and_amount('buy_0')
    executor = SimpleNamespace(id='old', is_active=True, status=RunnableStatus.RUNNING,
                               config=SimpleNamespace(level_id='buy_0', side=TradeType.BUY,
                                                      amount=amount * 2, entry_price=price))
    controller.executors_info = [executor]
    actions = controller._defense_refresh_actions()
    assert len(actions) == 1 and actions[0].keep_position
    assert controller._defense_refresh_actions() == []
    executor.status, executor.is_active = RunnableStatus.SHUTTING_DOWN, False
    assert controller.create_actions_proposal() == []


@pytest.mark.asyncio
async def test_quote_level_scope_survives_task_creation_and_resets():
    import asyncio

    from hummingbot.core.order_submission_guard import current_quote_level, current_session_scope, session_order_scope

    async def observe():
        await asyncio.sleep(0)
        return current_session_scope(), current_quote_level()
    with session_order_scope('session', 2, level_id='buy_2'):
        task = asyncio.create_task(observe())
        with session_order_scope('other', 0):
            assert current_quote_level() is None
        assert current_quote_level() == 'buy_2'
    assert current_quote_level() is None
    assert await task == (('session', 2), 'buy_2')


@pytest.mark.parametrize('connector', ['okx', 'okx_perpetual'])
def test_three_weighted_levels_use_one_market_budget(connector):
    controller, _ = make_controller(configuration(
        connector_name=connector, total_amount_quote='200',
        buy_spreads=[.004, .009, .018], sell_spreads=[.004, .009, .018],
        buy_amounts_pct=[50, 30, 20], sell_amounts_pct=[50, 30, 20]))
    controller.processed_data = {'reference_price': Decimal('.1'), 'spread_multiplier': Decimal('1')}
    notionals = []
    for side, prices in [('buy', ['.0996', '.0991', '.0982']), ('sell', ['.1004', '.1009', '.1018'])]:
        for level, (expected_price, allocation) in enumerate(zip(prices, [50, 30, 20])):
            price, amount = controller.get_price_and_amount(f'{side}_{level}')
            assert price == Decimal(expected_price)
            assert price * amount == pytest.approx(Decimal(allocation))
            notionals.append(price * amount)
    assert sum(notionals) == pytest.approx(Decimal('200'))


@pytest.mark.parametrize('mode,count', [('spot', 1), ('perpetual', 1), ('both', 2)])
def test_current_catalogue_keeps_two_equal_levels_and_market_selection(mode, count):
    catalogue = MMProfilesConfig.model_validate(yaml.safe_load(Path('deploy/cloudrun/conf/life_mm_profiles.yaml').read_text()))
    configs = catalogue.controller_configs('sideways', mode, None)
    assert len(configs) == count
    for config in configs:
        for side in (TradeType.BUY, TradeType.SELL):
            spreads, amounts = config.get_spreads_and_amounts_in_quote(side)
            assert spreads == [.01, .02]
            assert amounts == [Decimal('25'), Decimal('25')]
        assert not config.spread_policy.enabled


@pytest.mark.parametrize('raw', ['0', '.02', '.08', None])
def test_existing_spread_controls_preserve_every_base_level(monkeypatch, raw):
    from test.hummingbot.strategy_v2.controllers.test_life_mm_spread import feed
    base = [Decimal('.004'), Decimal('.009'), Decimal('.018')]
    policy = SpreadPolicy(SpreadPolicyConfig(fee_floor=True, volatility=dict(
        enabled=True, min_multiplier='1', on_unavailable='static',
        fallback_buy_spreads=base, fallback_sell_spreads=base)))
    if raw is not None:
        monkeypatch.setattr('hummingbot.strategy_v2.utils.mm_spread.natr_value', lambda *args: Decimal(raw))
        policy.refresh(feed(), 1200)
    connector = MagicMock()
    connector.get_fee.return_value = AddedToCostTradeFee(percent=Decimal('.001'))
    for values in policy.compose(connector, 'LIFE-USDT', Decimal('.1'), base, base, [50, 30, 20], [50, 30, 20], now=1200):
        assert all(actual >= minimum for actual, minimum in zip(values, base))
        assert values == sorted(values)


def test_legacy_low_multiplier_and_tighter_static_fallback_stay_readable():
    config = configuration(spread_policy=dict(volatility=dict(
        enabled=True, min_multiplier='.5', on_unavailable='static',
        fallback_buy_spreads=['.001'], fallback_sell_spreads=['.001'])))
    restored = type(config).model_validate_json(config.model_dump_json())
    assert restored.spread_policy.volatility.min_multiplier == Decimal('.5')
    assert restored.spread_policy.volatility.fallback_buy_spreads == [Decimal('.001')]
    assert 'defense' not in restored.spread_policy.volatility.model_dump(mode='json')
