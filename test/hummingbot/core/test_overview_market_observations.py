from decimal import Decimal
from types import SimpleNamespace

import pytest

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.core.overview_market_observations import capture_overview_markets


class Entry:
    def __init__(self, price, amount):
        self.price = float(price)
        self.amount = float(amount)


class Book:
    def __init__(self, bids, asks, *, at=100):
        self.bids = [Entry(*row) for row in bids]
        self.asks = [Entry(*row) for row in asks]
        self.snapshot_uid = at
        self.last_diff_uid = at

    def bid_entries(self):
        return iter(self.bids)

    def ask_entries(self):
        return iter(self.asks)


class Connector:
    def __init__(self, name, books):
        self.name = name
        self.order_books = books
        self.in_flight_orders = {}
        self.trading_rules = {}


class Provider:
    def __init__(self, connectors):
        self.connectors = connectors
        self._non_trading_connectors = {}
        self.created = 0

    def get_connector_with_fallback(self, name):
        self.created += 1
        raise AssertionError('collector must not create a connector')


def config(market, *, references=True, **changes):
    values = dict(
        id=f'{market}-controller',
        connector_name='okx' if market == 'spot' else 'okx_perpetual',
        trading_pair='LIFE-USDT',
        reference_connector='okx',
        references=[SimpleNamespace(pair='BTC-USDT')] if references else [],
        max_data_age_seconds=Decimal('10'),
        quote_guards=SimpleNamespace(max_book_levels=20, max_own_orders=20),
        max_base_inventory=Decimal('600') if market == 'spot' else None,
        max_long_base=Decimal('800') if market == 'perpetual' else None,
        max_short_base=Decimal('700') if market == 'perpetual' else None,
        max_gross_base=Decimal('1200') if market == 'perpetual' else None,
        mm_session_id='session-a',
        mm_generation=4,
    )
    values.update(changes)
    return SimpleNamespace(**values)


def sample_book(at=100):
    return Book([(Decimal('99.99'), 10), (Decimal('99.95'), 10), (Decimal('99.9'), 20)],
                [(Decimal('100.01'), 10), (Decimal('100.05'), 10), (Decimal('100.1'), 20)], at=at)


def setup_host(*, spot_book=None, perp_book=None, reference_book=None, include_perp=True):
    spot = Connector('okx', {'LIFE-USDT': spot_book or sample_book(), 'BTC-USDT': reference_book or Book([(199, 10)], [(201, 10)])})
    perpetual = Connector('okx_perpetual', {'LIFE-USDT': perp_book or sample_book(at=100_000)})
    provider = Provider({'okx': spot, 'okx_perpetual': perpetual})
    controllers = [SimpleNamespace(config=config('spot'), market_data_provider=provider)]
    if include_perp:
        controllers.append(SimpleNamespace(config=config('perpetual'), market_data_provider=provider))
    session = SimpleNamespace(
        id='session-a', generation=4, started_at=90,
        anchors={
            'spot-controller': {'sources': {'BTC-USDT': '200'}},
            'perpetual-controller': {'sources': {'BTC-USDT': '200'}},
        },
    )
    host = SimpleNamespace(mm_session=session, _mm_controllers=lambda: controllers)
    return host, provider, controllers


def test_captures_bounded_own_depth_full_book_slippage_and_session_references():
    host, provider, controllers = setup_host(include_perp=False)
    order = SimpleNamespace(
        trading_pair='LIFE-USDT', order_type=OrderType.LIMIT_MAKER, trade_type=TradeType.BUY,
        current_state=OrderState.OPEN, exchange_order_id='ack-1', price=Decimal('99.99'),
        amount=Decimal('2'), executed_amount_base=Decimal('0'), client_order_id='bid-1', last_update_timestamp=90,
    )
    host_order_connector = provider.connectors['okx']
    host_order_connector.in_flight_orders['bid-1'] = order
    host_order_connector.in_flight_orders['ask-1'] = SimpleNamespace(
        trading_pair='LIFE-USDT', order_type=OrderType.LIMIT_MAKER, trade_type=TradeType.SELL,
        current_state=OrderState.OPEN, exchange_order_id='ack-2', price=Decimal('100.01'),
        amount=Decimal('3'), executed_amount_base=Decimal('0'), client_order_id='ask-1', last_update_timestamp=90,
    )

    result = capture_overview_markets(host, now=100)

    market = result['markets'][0]
    assert market['depth']['value']['basis'] == 'own_working_orders'
    assert market['depth']['value']['levels'] == [
        {'offset_bps': 25, 'bid_usdt': '199.98', 'ask_usdt': '300.03'},
        {'offset_bps': 50, 'bid_usdt': '199.98', 'ask_usdt': '300.03'},
        {'offset_bps': 100, 'bid_usdt': '199.98', 'ask_usdt': '300.03'},
    ]
    buy = market['slippage_estimate']['value']['estimates'][0]
    sell = market['slippage_estimate']['value']['estimates'][3]
    assert buy['notional_usdt'] == '100' and buy['sufficient_depth']
    assert Decimal(buy['expected_slippage_bps']) > 0
    assert sell['notional_usdt'] == '100' and sell['sufficient_depth']
    assert Decimal(sell['expected_slippage_bps']) > 0
    assert result['references']['value'][0]['anchor_price'] == '200'
    assert Decimal(result['references']['value'][0]['last_price']) == Decimal('200')
    assert result['references']['value'][0]['anchor_at'] == '1970-01-01T00:01:30Z'
    assert result['inventory_limits']['value'][0]['inventory_limit'] == '600'
    assert result['inventory_limits']['value'][0]['long_limit'] is None
    assert Decimal(result['valuation_prices']['value'][0]['price_usdt']) == Decimal('100')
    assert market['coverage']['value'] is None
    assert market['post_fill_recovery']['value'] is None
    assert provider.created == 0
    assert host_order_connector.in_flight_orders['bid-1'] is order


def test_pending_and_unacknowledged_own_orders_are_not_counted_as_resting_depth():
    host, provider, _ = setup_host(include_perp=False)
    connector = provider.connectors['okx']
    connector.in_flight_orders['pending'] = SimpleNamespace(
        trading_pair='LIFE-USDT', order_type=OrderType.LIMIT_MAKER, trade_type=TradeType.BUY,
        current_state=OrderState.PENDING_CREATE, exchange_order_id=None, price=Decimal('99.99'),
        amount=Decimal('500'), executed_amount_base=Decimal('0'), client_order_id='pending', last_update_timestamp=90,
    )

    result = capture_overview_markets(host, now=100)

    level = result['markets'][0]['depth']['value']['levels'][0]
    assert level['bid_usdt'] == '0'
    assert connector.in_flight_orders['pending'].amount == Decimal('500')


@pytest.mark.parametrize('bad_book,reason', [
    (Book([], [(100, 10)]), 'empty_or_filtered_depth'),
    (Book([(100, 10)], [(99, 10)]), 'crossed_book'),
    (Book([(100, 10)], [(101, 10)], at=80), 'stale_book'),
    (Book([(100, 10)], [(101, 10)], at=102), 'stale_book'),
    (Book([(float('nan'), 10)], [(101, 10)]), 'invalid_observation'),
])
def test_invalid_stale_future_or_empty_market_book_is_unavailable(bad_book, reason):
    host, _, _ = setup_host(spot_book=bad_book, include_perp=False)

    result = capture_overview_markets(host, now=100)

    assert result['markets'][0]['depth']['value'] is None
    assert result['markets'][0]['depth']['unavailable_reason'] == reason
    assert result['markets'][0]['slippage_estimate']['value'] is None


def test_insufficient_exchange_depth_keeps_slippage_null_per_notional():
    sparse = Book([(99, .5)], [(101, .5)])
    host, _, _ = setup_host(spot_book=sparse, include_perp=False)

    result = capture_overview_markets(host, now=100)

    estimates = result['markets'][0]['slippage_estimate']['value']['estimates']
    assert [item['sufficient_depth'] for item in estimates] == [False, False, False, False, False, False]
    assert all(item['expected_slippage_bps'] is None for item in estimates)


def test_market_failure_isolated_and_reference_anchor_requires_current_generation():
    host, _, controllers = setup_host(perp_book=Book([], [(1, 10)], at=100_000))
    controllers[0].config.mm_generation = 3

    result = capture_overview_markets(host, now=100)

    assert result['markets'][0]['depth']['value'] is not None
    assert result['markets'][1]['depth']['value'] is None
    assert result['references']['value'] is None
    assert result['references']['unavailable_reason'] == 'reference_anchor_unavailable'
    assert result['inventory_limits']['value'][1]['long_limit'] == '800'
    assert result['inventory_limits']['value'][1]['short_limit'] == '700'
    assert result['inventory_limits']['value'][1]['gross_limit'] == '1200'


def test_no_session_returns_explicit_unknown_observations():
    host, _, _ = setup_host(include_perp=False)
    host.mm_session = None

    result = capture_overview_markets(host, now=100)

    assert result['session_id'] is None
    assert result['markets'] == []
    assert result['references']['value'] is None
    assert result['references']['unavailable_reason'] == 'session_unavailable'
    assert result['valuation_prices']['value'] is None


def _own_order(side, price, *, identity):
    return SimpleNamespace(
        trading_pair='LIFE-USDT', order_type=OrderType.LIMIT_MAKER, trade_type=side,
        current_state=OrderState.OPEN, exchange_order_id=f'ack-{identity}', price=Decimal(price),
        amount=Decimal('1'), executed_amount_base=Decimal('0'), client_order_id=identity,
        last_update_timestamp=90,
    )


def test_own_buy_above_upper_offset_is_excluded_from_depth_ladder():
    spot = Book([(101.2, 1), (99, 10)], [(102, 10)])
    host, provider, _ = setup_host(spot_book=spot, include_perp=False)
    provider.connectors['okx'].in_flight_orders['high-buy'] = _own_order(TradeType.BUY, '101.2', identity='high-buy')

    result = capture_overview_markets(host, now=100)

    levels = result['markets'][0]['depth']['value']['levels']
    assert levels[0]['bid_usdt'] == '0'
    assert levels[1]['bid_usdt'] == '0'
    assert levels[2]['bid_usdt'] == '101.2'


def test_own_sell_below_lower_offset_is_excluded_from_depth_ladder():
    spot = Book([(99, 10)], [(99.8, 1), (102, 10)])
    host, provider, _ = setup_host(spot_book=spot, include_perp=False)
    provider.connectors['okx'].in_flight_orders['low-sell'] = _own_order(TradeType.SELL, '99.8', identity='low-sell')

    result = capture_overview_markets(host, now=100)

    levels = result['markets'][0]['depth']['value']['levels']
    assert levels[0]['ask_usdt'] == '0'
    assert levels[1]['ask_usdt'] == '0'
    assert levels[2]['ask_usdt'] == '99.8'


def test_perpetual_only_controller_uses_cached_spot_life_reference_for_valuation():
    host, provider, controllers = setup_host(include_perp=True)
    host.mm_session.anchors = {'perpetual-controller': {'sources': {'BTC-USDT': '200'}}}
    host._mm_controllers = lambda: [controllers[1]]
    provider.connectors['okx'].order_books['LIFE-USDT'] = Book([(99, 10)], [(101, 10)])
    controllers[1].config.references = [SimpleNamespace(pair='BTC-USDT')]

    result = capture_overview_markets(host, now=100)

    assert result['valuation_prices']['value'] == [{
        'asset': 'LIFE', 'pair': 'LIFE-USDT', 'connector': 'okx',
        'price_usdt': '100.0', 'observed_at': '1970-01-01T00:01:40Z',
    }]


def test_slippage_never_treats_known_own_orders_as_executable_external_depth():
    host, provider, _ = setup_host(spot_book=Book([(99, 100)], [(101, 1000), (102, .5)]), include_perp=False)
    own = _own_order(TradeType.SELL, '101', identity='own-ask')
    own.amount = Decimal('999')
    provider.connectors['okx'].in_flight_orders['own-ask'] = own
    result = capture_overview_markets(host, now=100)
    estimates = result['markets'][0]['slippage_estimate']['value']['estimates']
    large_buy = next(item for item in estimates if item['side'] == 'BUY' and item['notional_usdt'] == '1000')
    assert large_buy['sufficient_depth'] is False
    assert large_buy['expected_slippage_bps'] is None


def test_shared_reference_valuation_is_deduplicated_and_retains_actual_book_time():
    host, _, _ = setup_host(spot_book=sample_book(at=97))
    result = capture_overview_markets(host, now=100)
    assert len(result['valuation_prices']['value']) == 1
    assert result['valuation_prices']['value'][0]['observed_at'] == '1970-01-01T00:01:37Z'
    assert result['markets'][0]['slippage_estimate']['observed_at'] == '1970-01-01T00:01:37Z'
