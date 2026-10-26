from decimal import Decimal
from test.hummingbot.strategy_v2.controllers.test_quote_guard import order
from types import SimpleNamespace

from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.core.mm_liquidity import LiquidityObserver, own_quotes


def connector():
    return SimpleNamespace(name='okx', in_flight_orders={
        'bid': order(identity='bid', price='.099', amount='10', filled='2', state=OrderState.PARTIALLY_FILLED),
        'ask': order(identity='ask', side=TradeType.SELL, price='.101', amount='20')})


def test_only_acknowledged_live_remaining_quote_is_counted():
    market = connector()
    result = own_quotes(market, 'LIFE-USDT', max_orders=20)
    assert result['bid_notional'] == Decimal('.792') and result['ask_notional'] == Decimal('2.020')
    assert result['spread_bps'] == 200 and result['two_sided']
    market.in_flight_orders['ask'].current_state = OrderState.PENDING_CANCEL
    assert not own_quotes(market, 'LIFE-USDT', max_orders=20)['two_sided']
    market.in_flight_orders['bid'].exchange_order_id = None
    assert own_quotes(market, 'LIFE-USDT', max_orders=20)['bid_notional'] == 0


def test_gaps_boundaries_and_restart_never_inflate_observed_duration():
    observer = LiquidityObserver()
    current = dict(session_id='session', generation=0, profile_id='sideways', started_at=1000)
    value = own_quotes(connector(), 'LIFE-USDT', max_orders=20)

    def sample(at, mono, eligible=True, generation=0, valid=True):
        return observer.observe({**current, 'generation': generation}, {'spot': dict(value, eligible=eligible, valid=valid)},
                                now=at, monotonic_now=mono, owner_generation=1)
    sample(1000, 0)
    result = sample(1002, 2)
    assert result['markets']['spot']['two_sided_seconds'] == 2
    result = sample(1010, 10)
    assert result['markets']['spot']['two_sided_seconds'] == 2
    result = sample(1012, 12, generation=1)
    assert result['markets']['spot']['two_sided_seconds'] == 2
    restarted = LiquidityObserver(result)
    result = restarted.observe({**current, 'generation': 1}, {'spot': dict(value, eligible=True, valid=True)},
                               now=1020, monotonic_now=20, owner_generation=2)
    assert result['markets']['spot']['two_sided_seconds'] == 2
    assert result['markets']['spot']['unknown_seconds'] >= 18


def test_stopped_or_missing_runtime_does_not_erase_history_or_credit_future_time():
    observer = LiquidityObserver()
    session = dict(session_id='session', generation=0, profile_id='sideways', started_at=1000)
    quotes = dict(own_quotes(connector(), 'LIFE-USDT', max_orders=20), valid=True, eligible=True)
    observer.observe(session, {'spot': quotes}, now=1000, monotonic_now=0, owner_generation=1)
    observer.observe(session, {'spot': quotes}, now=1002, monotonic_now=2, owner_generation=1)
    stopped = {**session, 'status': 'stopped'}
    result = observer.observe(stopped, {}, now=1003, monotonic_now=3, owner_generation=1)
    assert result['markets']['spot']['two_sided_seconds'] == 2
    result = observer.observe(stopped, {'spot': quotes}, now=1004, monotonic_now=4, owner_generation=1)
    assert result['markets']['spot']['observed_seconds'] == 2
    assert result['markets']['spot']['elapsed_seconds'] == 3


def test_invalid_cached_liquidity_is_unknown_not_an_api_failure():
    from hummingbot.core.mm_liquidity import liquidity_view
    row = dict(database_now=1000, heartbeat_age_s=0, owner_uuid='owner', mm_session={'id': 'session'},
               status={'mm_liquidity': {'session_id': 'session', 'observed_at': None, 'markets': []}})
    assert liquidity_view(row)['state'] == 'unavailable'


def test_current_session_generation_cannot_show_old_step_quotes_as_fresh():
    from hummingbot.core.mm_liquidity import liquidity_view
    observer = LiquidityObserver()
    session = dict(session_id='session', generation=0, profile_id='sideways', started_at=1000)
    value = dict(own_quotes(connector(), 'LIFE-USDT', max_orders=20), valid=True, eligible=True)
    frame = observer.observe(session, {'spot': value}, now=1000, monotonic_now=0, owner_generation=1)
    row = dict(database_now=1001, heartbeat_age_s=0, owner_uuid='owner', mm_session=dict(id='session', generation=1),
               status={'mm_liquidity': frame})
    result = liquidity_view(row)
    assert result['state'] == 'unavailable' and not result['markets']['spot']['eligible']


def test_valid_empty_book_quote_observation_is_zero_availability_not_unknown():
    observer = LiquidityObserver()
    session = dict(session_id='session', generation=0, profile_id='sideways', started_at=1000)
    market = connector()
    market.in_flight_orders = {}
    value = dict(own_quotes(market, 'LIFE-USDT', max_orders=20), valid=True, eligible=True)
    observer.observe(session, {'spot': value}, now=1000, monotonic_now=0, owner_generation=1)
    result = observer.observe(session, {'spot': value}, now=1002, monotonic_now=2, owner_generation=1)['markets']['spot']
    assert result['observed_seconds'] == 2 and result['two_sided_seconds'] == 0 and result['unknown_seconds'] == 0


def test_checkpoint_market_count_is_bounded():
    import pytest
    observer = LiquidityObserver(dict(session_id='session', markets={'unexpected': {}}))
    with pytest.raises(ValueError):
        observer.observe(dict(session_id='session', generation=0, profile_id='sideways', started_at=1000), {},
                         now=1001, monotonic_now=1, owner_generation=1)


def test_market_orders_are_not_resting_liquidity():
    from hummingbot.core.data_type.common import OrderType
    market = connector()
    immediate = order(identity='market', price='NaN', amount='100')
    immediate.order_type = OrderType.MARKET
    market.in_flight_orders['market'] = immediate
    value = own_quotes(market, 'LIFE-USDT', max_orders=20)
    assert value['bid_notional'] == Decimal('.792')
