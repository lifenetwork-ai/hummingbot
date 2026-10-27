from test.hummingbot.core.test_mm_liquidity import connector

import pytest

from hummingbot.core.mm_liquidity import LiquidityObserver, liquidity_view, own_quotes


def observation(*, empty=False, valid=True, reasons=None, at=1000):
    market = connector()
    if empty:
        market.in_flight_orders = {}
    value = dict(own_quotes(market, 'LIFE-USDT', max_orders=20), valid=valid, eligible=True,
                 connector='okx', pair='LIFE-USDT', reason_code=None)
    if reasons:
        value['proposal'] = dict(observed_at=at, reasons=reasons, proposed={'BUY': 0, 'SELL': 0})
    return value


def sample(observer, at, value, *, generation=1, owner=1, status='running', threshold=5):
    return observer.observe(dict(session_id='session', generation=generation, profile_id='sideways',
                                 started_at=1000, status=status), {'spot': value},
                            now=at, monotonic_now=at, owner_generation=owner,
                            warning_seconds=threshold)['markets']['spot']['quote_activity']


def test_working_order_fill_scope_does_not_claim_session_fill_history():
    market = connector()
    value = own_quotes(market, 'LIFE-USDT', max_orders=20)
    assert value['buy_order_count'] == value['sell_order_count'] == 1
    assert value['working_orders_with_fills'] == 1
    market.in_flight_orders['bid'].executed_amount_base = 0
    value = dict(own_quotes(market, 'LIFE-USDT', max_orders=20), valid=True, eligible=True)
    result = sample(LiquidityObserver(), 1000, value)
    assert result['state'] == 'orders_working'
    assert result['fill_observation'] == 'no_fills_on_working_orders'
    assert result['fill_observation_basis'] == 'current_working_orders'
    assert not result['warning_active']


def test_continuous_no_quote_warning_is_bounded_and_recovery_is_logged(caplog):
    caplog.set_level('INFO')
    observer = LiquidityObserver()
    empty = observation(empty=True)
    assert sample(observer, 1000, empty)['no_quote_seconds'] == 0
    assert not sample(observer, 1003, empty)['warning_active']
    warning = sample(observer, 1006, empty)
    assert warning['warning_active'] and warning['no_quote_seconds'] == 6
    sample(observer, 1009, empty)
    assert len([r for r in caplog.records if getattr(r, 'event', None) == 'mm_no_quotes_warning']) == 1
    recovered = sample(observer, 1010, observation(at=1010))
    assert recovered['state'] == 'orders_working' and not recovered['warning_active']
    assert recovered['no_quote_seconds'] == 0
    assert any(getattr(r, 'event', None) == 'mm_no_quotes_warning_cleared' for r in caplog.records)


@pytest.mark.parametrize('boundary', ['gap', 'generation', 'owner', 'restart', 'unknown', 'stopped'])
def test_absence_time_never_crosses_unknown_or_inactive_boundaries(boundary):
    observer = LiquidityObserver()
    empty = observation(empty=True)
    sample(observer, 1000, empty)
    sample(observer, 1003, empty)
    if boundary == 'restart':
        observer = LiquidityObserver(observer.frame)
    changes = {'generation': 2} if boundary == 'generation' else {'owner': 2} if boundary == 'owner' else {}
    if boundary == 'stopped':
        changes['status'] = 'stopped'
    result = sample(observer, 1010 if boundary == 'gap' else 1006,
                    observation(empty=True, valid=False) if boundary == 'unknown' else empty, **changes)
    assert result['no_quote_seconds'] in (0, None) and not result['warning_active']


def test_per_side_reasons_expire_instead_of_reusing_old_balance_denials():
    observer = LiquidityObserver()
    empty = observation(empty=True, reasons={'BUY': ['insufficient_quote_balance'], 'SELL': ['insufficient_base_balance']})
    result = sample(observer, 1000, empty)
    assert result['sides']['BUY']['reason_codes'] == ['insufficient_quote_balance']
    assert result['sides']['SELL']['reason_codes'] == ['insufficient_base_balance']
    result = sample(observer, 1006, empty)
    assert result['sides']['BUY']['reason_codes'] == ['quote_reason_unavailable']
    assert result['proposal_observed_at'] == 1000


def test_disabled_warning_and_stale_publication_never_publish_an_active_alarm():
    observer = LiquidityObserver()
    empty = observation(empty=True)
    sample(observer, 1000, empty)
    result = sample(observer, 1003, empty, threshold=0)
    assert not result['warning_active']
    row = dict(database_now=1004, owner_uuid='owner', heartbeat_age_s=20,
               mm_session={'id': 'session', 'generation': 1, 'status': 'running'}, status={'mm_liquidity': observer.frame})
    public = liquidity_view(row)['markets']['spot']['quote_activity']
    assert public['state'] == 'unavailable' and not public['warning_active']
    assert public['no_quote_seconds'] is None
    assert public['sides']['BUY']['state'] == 'unavailable'


def test_one_sided_quotes_are_not_a_total_absence_alarm_and_reads_do_not_mutate_checkpoint():
    observer = LiquidityObserver()
    sample(observer, 1000, observation(empty=True))
    sample(observer, 1003, observation(empty=True))
    warning = sample(observer, 1006, observation(empty=True))
    assert warning['warning_active']
    row = dict(database_now=1007, owner_uuid='owner', heartbeat_age_s=20,
               mm_session={'id': 'session', 'generation': 1, 'status': 'running'}, status={'mm_liquidity': observer.frame})
    assert not liquidity_view(row)['markets']['spot']['quote_activity']['warning_active']
    assert observer.frame['markets']['spot']['quote_activity']['warning_active']
    market = connector()
    del market.in_flight_orders['ask']
    one_side = dict(own_quotes(market, 'LIFE-USDT', max_orders=20), valid=True, eligible=True)
    result = sample(observer, 1007, one_side)
    assert result['state'] == 'one_sided' and not result['warning_active']
    assert result['sides']['BUY']['state'] == 'working'
    assert result['sides']['SELL']['order_count'] == 0


@pytest.mark.asyncio
async def test_capture_connects_actual_controller_reasons_to_fresh_liquidity_without_new_io(monkeypatch):
    from decimal import Decimal
    from test.hummingbot.strategy_v2.controllers.test_life_mm import configuration, make_controller
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    import hummingbot.core.mm_liquidity as liquidity

    controller, _ = make_controller(configuration())
    source = controller.market_data_provider.get_connector_with_fallback.return_value
    source.get_available_balance.side_effect = lambda asset: Decimal('0')
    source._user_stream_tracker.last_recv_time = 1000
    source.TICK_INTERVAL_LIMIT = 120
    now = [1000]
    monkeypatch.setattr(liquidity, 'time', SimpleNamespace(time=lambda: now[0], monotonic=lambda: now[0]))
    owner = SimpleNamespace(generation=1, store=MagicMock())
    owner.store.read.return_value = {}
    session = SimpleNamespace(id='session', generation=1, profile_id='sideways',
                              started_at=1000, status='running', expires_at=1600)
    host = SimpleNamespace(mm_session=session, owner=owner, phase='running', _mm_controllers=lambda: [controller],
                           admin_settings=SimpleNamespace(no_quote_warning_seconds=5))
    for at in (1000, 1003, 1006):
        now[0] = at
        controller.market_data_provider.time.return_value = at
        await controller.update_processed_data()
        assert not controller.create_actions_proposal()
        result = liquidity.capture_liquidity(host)
    activity = result['markets']['spot']['quote_activity']
    assert activity['warning_active'] and activity['no_quote_seconds'] == 6
    assert activity['sides']['BUY']['reason_codes'] == ['insufficient_quote_balance']
    assert activity['sides']['SELL']['reason_codes'] == ['insufficient_base_balance']
    owner.store.read.assert_called_once()
    source._api_request.assert_not_called()
