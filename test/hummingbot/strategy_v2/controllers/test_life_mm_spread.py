"""Optional spreads reuse fee conversion, NATR and the existing controller."""
from collections import deque
from decimal import Decimal
from test.hummingbot.strategy_v2.controllers.test_life_mm import configuration, make_controller
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount
from hummingbot.strategy_v2.utils.mm_spread import SpreadPolicy, SpreadPolicyConfig


def feed(rows=20, *, now=1200, width=.002):
    return SimpleNamespace(interval_in_seconds=60, _candles=deque([
        [now - (rows - i) * 60, .1, .1 + width, .1 - width, .1, 100, 0, 0, 0, 0]
        for i in range(rows)], maxlen=200))


def dynamic(**values):
    return SpreadPolicyConfig(volatility={**dict(enabled=True, length=5, target_natr='.01',
                                                 min_multiplier='.5', max_multiplier='3'), **values})


def test_dynamic_requires_explicit_fallback_and_valid_limits():
    with pytest.raises(ValueError):
        dynamic(on_unavailable='static')
    with pytest.raises(ValueError):
        dynamic(on_unavailable='wait', min_multiplier='4')
    config = dynamic(on_unavailable='static', fallback_buy_spreads=['.01'], fallback_sell_spreads=['.02'])
    assert config.volatility.fallback_buy_spreads == [Decimal('.01')]


def test_completed_candles_ignore_forming_cache_natr_and_expire(monkeypatch):
    policy = SpreadPolicy(dynamic(on_unavailable='wait'))
    source = feed()
    source._candles.append([1200, .1, 1000, 0, .1, 1, 0, 0, 0, 0])
    provider = MagicMock()
    calls = []
    original = pd.DataFrame
    # A quote/send readiness check must never reconstruct a DataFrame.
    monkeypatch.setattr('hummingbot.strategy_v2.utils.mm_spread.natr_value', lambda frame, length: calls.append(1) or Decimal('.02'))
    policy.refresh(source, 1201)
    assert policy.multiplier(1201) == 2
    policy.refresh(source, 1202)
    assert len(calls) == 1
    assert policy.multiplier(1400) is None
    assert not provider.mock_calls and original is pd.DataFrame


def test_candle_warmup_flat_shock_stale_reordered_and_recovery():
    policy = SpreadPolicy(dynamic(on_unavailable='wait'))
    policy.refresh(feed(rows=2), 1200)
    assert policy.multiplier(1200) is None
    policy.refresh(feed(width=0), 1200)
    assert policy.multiplier(1200) == Decimal('.5')
    policy.refresh(feed(now=1260, width=.08), 1260)
    assert policy.multiplier(1260) == 3
    broken = feed(now=1320)
    broken._candles[-2][0] = broken._candles[-3][0]
    policy.refresh(broken, 1320)
    assert policy.multiplier(1320) is None
    policy.refresh(feed(now=1380), 1380)
    assert policy.multiplier(1380) is not None


@pytest.mark.parametrize('token', ['LIFE', 'USDT'])
def test_fee_floor_covers_maker_roundtrip_with_original_currency_flat_fees(token):
    policy = SpreadPolicy(SpreadPolicyConfig(fee_floor=True))
    connector = MagicMock()
    connector.get_fee.return_value = AddedToCostTradeFee(percent=Decimal('.01'), percent_token=token,
                                                         flat_fees=[TokenAmount(token=token, amount=Decimal('.001'))])
    spreads = policy.compose(connector, 'LIFE-USDT', Decimal('.1'), [Decimal('.0001')], [Decimal('.0001')],
                             [Decimal('50')], [Decimal('50')], now=1200)
    for side, values in zip((TradeType.BUY, TradeType.SELL), spreads):
        price = Decimal('.1') * (1 + (-1 if side == TradeType.BUY else 1) * values[0])
        assert policy.quote_valid(connector, 'LIFE-USDT', side, price, Decimal('50') / price, Decimal('.1'))
    assert connector.get_fee.call_args.kwargs['is_maker'] is True


def test_unknown_fee_currency_blocks_and_rebates_do_not_narrow_spreads():
    policy = SpreadPolicy(SpreadPolicyConfig(fee_floor=True))
    connector = MagicMock()
    connector.get_fee.return_value = AddedToCostTradeFee(percent=Decimal('.001'), percent_token='OTHER')
    with pytest.raises(ValueError, match='fee_basis_unavailable'):
        policy.compose(connector, 'LIFE-USDT', Decimal('.1'), [Decimal('.01')], [Decimal('.01')], [50], [50], now=1)
    connector.get_fee.return_value = AddedToCostTradeFee(percent=Decimal('-.01'))
    assert policy.compose(connector, 'LIFE-USDT', Decimal('.1'), [Decimal('.01')], [Decimal('.02')], [50], [50], now=1) == ([Decimal('.01')], [Decimal('.02')])


@pytest.mark.asyncio
async def test_controller_fallback_fee_floor_and_dynamic_recovery_keep_anchor_and_send_cache():
    config = configuration(spread_policy=dict(fee_floor=True, volatility=dict(
        enabled=True, length=5, target_natr='.01', on_unavailable='static',
        fallback_buy_spreads=['.0001'], fallback_sell_spreads=['.0001'])))
    controller, _ = make_controller(config)
    connector = controller.market_data_provider.get_connector_with_fallback.return_value
    connector.get_fee.return_value = AddedToCostTradeFee(percent=Decimal('.002'))
    source = feed(rows=2, now=960)
    controller.market_data_provider.candles_feeds = {'okx_LIFE-USDT_1m': source}
    controller.market_data_provider._generate_candle_feed_key.return_value = 'okx_LIFE-USDT_1m'
    await controller.update_processed_data()
    assert controller.processed_data['ready']
    assert controller.processed_data['spread_policy']['state'] == 'static_fallback'
    assert controller.processed_data['reference_price'] == Decimal('.1')
    assert controller.get_price_and_amount('sell_0')[0] > Decimal('.1002')
    source._candles = feed(now=960)._candles
    await controller.update_processed_data()
    assert controller.processed_data['spread_policy']['state'] == 'dynamic'
    for _ in range(10):
        controller.update_reference()
    assert controller.processed_data['spread_policy']['state'] == 'dynamic'
    assert controller.processed_data['reference_price'] == Decimal('.1')
    controller.market_data_provider.time.return_value = 1500
    controller.update_reference()
    assert controller.processed_data['spread_policy']['state'] == 'static_fallback'


@pytest.mark.asyncio
async def test_wait_policy_recovers_without_restart_and_rejects_final_fee_rounding():
    controller, _ = make_controller(configuration(spread_policy=dict(fee_floor=True, volatility=dict(
        enabled=True, length=5, target_natr='.01', on_unavailable='wait'))))
    connector = controller.market_data_provider.get_connector_with_fallback.return_value
    connector.get_fee.return_value = AddedToCostTradeFee(percent=Decimal('.002'))
    source = feed(rows=2, now=960)
    controller.market_data_provider.candles_feeds = {'okx_LIFE-USDT_1m': source}
    controller.market_data_provider._generate_candle_feed_key.return_value = 'okx_LIFE-USDT_1m'
    await controller.update_processed_data()
    assert not controller.processed_data['ready']
    assert controller.processed_data['reason'] == 'candles_unavailable'
    source._candles = feed(now=960)._candles
    await controller.update_processed_data()
    assert controller.processed_data['ready']
    assert not controller.spread_quote_valid(TradeType.SELL, Decimal('.10001'), Decimal('10'))


def test_optional_candle_ownership_shares_feed_without_blocking_static_fallback():
    from unittest.mock import patch

    from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
    from hummingbot.data_feed.market_data_provider import MarketDataProvider
    provider = MarketDataProvider({})
    config = CandlesConfig(connector='okx', trading_pair='LIFE-USDT')
    candle = MagicMock(ready=False, max_records=500)
    with patch('hummingbot.data_feed.market_data_provider.CandlesFactory.get_candle', return_value=candle):
        first, second = object(), object()
        provider.acquire_candle_feed(config, first)
        provider.acquire_candle_feed(config, second)
        assert provider.ready  # Explicit static/wait policy is controller-local.
        candle.start.assert_called_once()
        provider.release_candle_feed(config, first)
        candle.stop.assert_not_called()
        provider.release_candle_feed(config, second)
        candle.stop.assert_called_once()
        assert not provider.candles_feeds


def test_existing_unmanaged_candle_feed_retains_original_readiness_and_lifecycle():
    from unittest.mock import patch

    from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
    from hummingbot.data_feed.market_data_provider import MarketDataProvider
    provider = MarketDataProvider({})
    config = CandlesConfig(connector='okx', trading_pair='LIFE-USDT')
    candle = MagicMock(ready=False, max_records=500)
    with patch('hummingbot.data_feed.market_data_provider.CandlesFactory.get_candle', return_value=candle):
        provider.initialize_candles_feed(config)
        owner = object()
        provider.acquire_candle_feed(config, owner)
        assert not provider.ready
        provider.release_candle_feed(config, owner)
        candle.stop.assert_not_called()
        assert provider.candles_feeds


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '-1'])
def test_invalid_indicator_selects_wait_and_recovers(monkeypatch, value):
    policy = SpreadPolicy(dynamic(on_unavailable='wait'))
    monkeypatch.setattr('hummingbot.strategy_v2.utils.mm_spread.natr_value', lambda *args: Decimal(value))
    policy.refresh(feed(), 1200)
    assert policy.multiplier(1200) is None
    monkeypatch.setattr('hummingbot.strategy_v2.utils.mm_spread.natr_value', lambda *args: Decimal('.01'))
    policy.refresh(feed(), 1200)
    assert policy.multiplier(1200) == 1


@pytest.mark.asyncio
async def test_spread_enabled_uses_original_configured_entry_path_and_unknown_fee_recovers():
    controller, _ = make_controller(configuration(spread_policy=dict(fee_floor=True)))
    connector = controller.market_data_provider.get_connector_with_fallback.return_value
    connector.get_fee.return_value = AddedToCostTradeFee(percent=Decimal('.001'), percent_token='OTHER')
    await controller.update_processed_data()
    assert not controller.processed_data['ready'] and controller.processed_data['reason'] == 'fee_basis_unavailable'
    connector.get_fee.return_value = AddedToCostTradeFee(percent=Decimal('.001'))
    await controller.update_processed_data()
    price, amount = controller.get_price_and_amount('buy_0')
    connector.quantize_order_price.side_effect = lambda pair, value: value
    connector.quantize_order_amount.side_effect = lambda pair, value: value
    connector.trading_rules['LIFE-USDT'] = SimpleNamespace(min_order_size=Decimal('.001'), min_notional_size=Decimal('0'))
    executor = controller.get_executor_config('buy_0', price, amount)
    assert executor.use_configured_entry_price
    assert controller.spread_quote_valid(TradeType.BUY, price, amount)


@pytest.mark.asyncio
async def test_sell_tick_rounding_cannot_narrow_the_composed_spread():
    from decimal import ROUND_DOWN
    controller, _ = make_controller(configuration(spread_policy=dict(fee_floor=True), buy_spreads=[.00001], sell_spreads=[.00001]))
    connector = controller.market_data_provider.get_connector_with_fallback.return_value
    connector.get_fee.return_value = AddedToCostTradeFee(percent=Decimal('.002'))
    connector.quantize_order_price.side_effect = lambda pair, price: price.quantize(Decimal('.0001'), rounding=ROUND_DOWN)
    connector.get_order_price_quantum.return_value = Decimal('.0001')
    connector.quantize_order_amount.side_effect = lambda pair, value: value
    connector.trading_rules['LIFE-USDT'] = SimpleNamespace(min_order_size=Decimal('.001'), min_notional_size=Decimal('0'))
    await controller.update_processed_data()
    price, amount = controller.get_price_and_amount('sell_0')
    executor = controller.get_executor_config('sell_0', price, amount)
    assert executor is not None
    assert executor.entry_price == Decimal('.1003')
    assert executor.entry_price * executor.amount <= price * amount
    assert controller.spread_quote_valid(TradeType.SELL, executor.entry_price, executor.amount)


@pytest.mark.asyncio
async def test_amount_rounding_cannot_bypass_flat_fee_coverage():
    controller, _ = make_controller(configuration(spread_policy=dict(fee_floor=True)))
    connector = controller.market_data_provider.get_connector_with_fallback.return_value
    connector.get_fee.return_value = AddedToCostTradeFee(flat_fees=[TokenAmount(token='USDT', amount=Decimal('.1'))])
    connector.quantize_order_price.side_effect = lambda pair, value: value
    connector.quantize_order_amount.return_value = Decimal('.001')
    connector.trading_rules['LIFE-USDT'] = SimpleNamespace(min_order_size=Decimal('.001'), min_notional_size=Decimal('0'))
    await controller.update_processed_data()
    price, amount = controller.get_price_and_amount('sell_0')
    assert controller.get_executor_config('sell_0', price, amount) is None


def test_perpetual_fee_uses_existing_required_open_position_signature():
    from unittest.mock import create_autospec

    from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
    from hummingbot.core.data_type.common import PositionAction
    connector = create_autospec(OkxPerpetualDerivative, instance=True)
    connector.name = 'okx_perpetual'
    connector.get_fee.return_value = AddedToCostTradeFee(percent=Decimal('.001'))
    policy = SpreadPolicy(SpreadPolicyConfig(fee_floor=True))
    values = policy.compose(connector, 'LIFE-USDT', Decimal('.1'), [.01], [.01], [50], [50], now=1)
    assert values == ([Decimal('.01')], [Decimal('.01')])
    assert connector.get_fee.call_args.kwargs['position_action'] == PositionAction.OPEN


@pytest.mark.parametrize('side,price', [(TradeType.BUY, '.09999'), (TradeType.SELL, '.10001')])
def test_one_leg_cannot_borrow_the_other_legs_fee_coverage(side, price):
    connector = MagicMock()
    connector.get_fee.return_value = AddedToCostTradeFee(percent=Decimal('.01'))
    policy = SpreadPolicy(SpreadPolicyConfig(fee_floor=True))
    assert not policy.quote_valid(connector, 'LIFE-USDT', side, Decimal(price), Decimal('100'), Decimal('.1'))


@pytest.mark.asyncio
async def test_dynamic_shock_still_obeys_absolute_quote_bounds_and_recovers():
    controller, _ = make_controller(configuration(max_price='.1012', spread_policy=dict(volatility=dict(
        enabled=True, length=5, target_natr='.01', on_unavailable='wait'))))
    provider = controller.market_data_provider
    source = feed(now=960, width=.08)
    provider.candles_feeds = {'okx_LIFE-USDT_1m': source}
    provider._generate_candle_feed_key.return_value = 'okx_LIFE-USDT_1m'
    await controller.update_processed_data()
    assert not controller.processed_data['ready'] and controller.processed_data['reason'] == 'quote_out_of_bounds'
    source._candles = feed(now=1020, width=.0001)._candles
    provider.time.return_value = 1021
    provider.get_order_book.side_effect = lambda *args: SimpleNamespace(snapshot_uid=1021, last_diff_uid=1021)
    await controller.update_processed_data()
    assert controller.processed_data['ready']
    assert controller.processed_data['reference_price'] == Decimal('.1')


def test_candle_gap_recovers_once_required_contiguous_tail_returns():
    policy = SpreadPolicy(dynamic(on_unavailable='wait'))
    source = feed(now=1200)
    policy.refresh(source, 1200)
    # Keep old history, miss two intervals, then receive six completed candles.
    tail = feed(rows=6, now=1680)
    source._candles.extend(tail._candles)
    policy.refresh(source, 1680)
    assert policy.multiplier(1680) is not None
