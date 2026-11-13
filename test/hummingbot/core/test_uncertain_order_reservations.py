"""Unknown executions never become free cash or permission for another POST."""
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, DeductedFromReturnsTradeFee, TokenAmount
from hummingbot.core.order_submission_guard import MarketReadinessBlocked
from hummingbot.core.uncertain_order_reservations import authorize_cash_sell, buy_fee_envelope, cohort


def attempt(identity='unknown'):
    return {'order_id': identity, 'state': 'uncertain', 'context': {
        'environment': {'mode': 'demo'}, 'config_file_path': 'mm.yml', 'bindings': [{'account_id': 'same'}],
        'reservation': {'cash_buy_fee': {'version': 1, 'percent': '.001', 'percent_token': 'BTC',
                                         'base_hold': '.1', 'quote_hold': '10'}},
        'executor': {'connector_name': 'okx', 'trading_pair': 'BTC-USDT', 'type': 'position_executor', 'amount': '.1'},
        'order': {'client_order_id': identity, 'trading_pair': 'BTC-USDT', 'trade_type': 'BUY', 'position': 'NIL',
                  'order_type': 'LIMIT_MAKER', 'amount': '.1', 'price': '100'}}}


def reserve(items):
    return cohort(items, bindings=[{'account_id': 'same'}], environment={'mode': 'demo'},
                  config_file_path='mm.yml', max_quote='50')


def native(amount='.2', identity='new'):
    return SimpleNamespace(name='okx', client_order_id=identity, trading_pair='BTC-USDT', amount=D(amount),
                           price=D('100'), trade_type=TradeType.SELL, order_type=OrderType.LIMIT_MAKER, is_done=False,
                           _cash_sell_reserved_base=D(amount))


def connector(cash='.5'):
    return SimpleNamespace(name='okx', account_balance_observations={'BTC': {'observed_at': 100, 'cash_balance': D(cash)}},
                           get_available_balance=lambda _: D(cash), in_flight_orders={},
                           get_fee=Mock(return_value=DeductedFromReturnsTradeFee(percent=D('.001'))))


def test_all_unknowns_reserve_full_quote_and_unproved_acquired_base():
    holds, quote = reserve([attempt('one'), attempt('two')])
    assert holds == {'BTC': D('.2')} and quote == D('20')
    exchange = connector()
    authorize_cash_sell(exchange, native(), holds, now=101, max_age=5)
    with pytest.raises(MarketReadinessBlocked, match='uncertain_base_reserved'):
        authorize_cash_sell(exchange, native('.4'), holds, now=101, max_age=5)


def test_reservation_uses_immutable_native_units_after_proposal_quantization():
    row = attempt()
    row['context']['executor']['amount'] = '.100000009'
    assert reserve([row]) == ({'BTC': D('.1')}, D('10'))
    row['context']['executor']['amount'] = '.099'
    with pytest.raises(MarketReadinessBlocked):
        reserve([row])


@pytest.mark.parametrize('field,value', [('state', 'prepared'), ('state', 'rejected')])
def test_only_uncertain_cohort_is_eligible(field, value):
    row = attempt()
    row[field] = value
    with pytest.raises(MarketReadinessBlocked):
        reserve([row])


@pytest.mark.parametrize('change', ['duplicate', 'count', 'notional', 'binding', 'environment', 'pair', 'perpetual', 'sell', 'nan'])
def test_unbounded_or_foreign_cohort_cannot_relax_admission(change):
    row = attempt()
    items = [row]
    if change == 'duplicate':
        items *= 2
    elif change == 'count':
        items = [attempt(str(i)) for i in range(65)]
    elif change == 'notional':
        items = [attempt(str(i)) for i in range(6)]
    elif change == 'binding':
        row['context']['bindings'] = []
    elif change == 'environment':
        row['context']['environment'] = {'mode': 'live'}
    elif change == 'pair':
        row['context']['order']['trading_pair'] = 'BTC-ETH'
    elif change == 'perpetual':
        row['context']['executor']['connector_name'] = 'okx_perpetual'
    elif change == 'sell':
        row['context']['order']['trade_type'] = 'SELL'
    else:
        row['context']['order']['amount'] = 'NaN'
    with pytest.raises(MarketReadinessBlocked):
        reserve(items)


def test_resident_pending_sell_and_base_fee_cannot_spend_reserved_cash_twice():
    exchange = connector()
    exchange.in_flight_orders['resident'] = native('.2', 'resident')
    exchange.get_fee.return_value = AddedToCostTradeFee(percent=D('.01'))
    holds, _ = reserve([attempt()])
    with pytest.raises(MarketReadinessBlocked, match='uncertain_base_reserved'):
        authorize_cash_sell(exchange, native(), holds, now=101, max_age=5)
    authorize_cash_sell(exchange, native('.1'), holds, now=101, max_age=5)


@pytest.mark.parametrize('change', ['stale', 'future', 'nan_cash', 'buy', 'perpetual', 'flat_fee', 'third_fee', 'fee_one'])
def test_missing_balance_or_unsupported_order_fee_fails_closed(change):
    exchange, order = connector(), native()
    if change == 'stale':
        exchange.account_balance_observations['BTC']['observed_at'] = 90
    elif change == 'future':
        exchange.account_balance_observations['BTC']['observed_at'] = 102
    elif change == 'nan_cash':
        exchange.account_balance_observations['BTC']['cash_balance'] = D('NaN')
    elif change == 'buy':
        order.trade_type = TradeType.BUY
    elif change == 'perpetual':
        exchange.name = 'okx_perpetual'
    elif change == 'flat_fee':
        exchange.get_fee.return_value = AddedToCostTradeFee(flat_fees=[TokenAmount('BTC', D('1'))])
    elif change == 'third_fee':
        exchange.get_fee.return_value = AddedToCostTradeFee(percent=D('.01'), percent_token='ETH')
    else:
        exchange.get_fee.return_value = AddedToCostTradeFee(percent=D('1'))
    with pytest.raises(MarketReadinessBlocked):
        authorize_cash_sell(exchange, order, reserve([attempt()])[0], now=101, max_age=5)


@pytest.mark.parametrize('percent,token,base_hold,quote_hold', [
    ('.01', 'USDT', '.1', '10.1'), ('-.01', 'BTC', '.101', '10'),
    ('-.01', 'USDT', '.1', '10'), ('.01', 'BTC', '.1', '10')])
def test_original_fee_envelope_includes_debits_and_possible_base_rebate(percent, token, base_hold, quote_hold):
    exchange, order = connector(), native('.1')
    order.trade_type = TradeType.BUY
    exchange.get_fee.return_value = AddedToCostTradeFee(percent=D(percent), percent_token=token)
    envelope = buy_fee_envelope(exchange, order)
    row = attempt()
    row['context']['reservation']['cash_buy_fee'] = envelope
    assert reserve([row]) == ({'BTC': D(base_hold)}, D(quote_hold))
    exchange.get_fee.return_value = AddedToCostTradeFee(percent=D('.9'))
    assert reserve([row]) == ({'BTC': D(base_hold)}, D(quote_hold))


@pytest.mark.parametrize('change', ['missing', 'invalid', 'tampered', 'third_token'])
def test_legacy_or_unverifiable_original_fee_never_grants_admission(change):
    row = attempt()
    envelope = row['context']['reservation']['cash_buy_fee']
    if change == 'missing':
        row['context']['reservation'].clear()
    elif change == 'invalid':
        envelope['percent'] = 'NaN'
    elif change == 'tampered':
        envelope['base_hold'] = '.01'
    else:
        envelope['percent_token'] = 'ETH'
    with pytest.raises(MarketReadinessBlocked, match='uncertain_fee_unavailable'):
        reserve([row])


def test_terminal_sell_debit_remains_reserved_until_balance_covers_it():
    exchange, resident = connector('.35'), native('.2', 'resident')
    resident.is_done = True
    resident.last_update_timestamp = 101
    exchange._order_tracker = SimpleNamespace(all_fillable_orders={'resident': resident})
    with pytest.raises(MarketReadinessBlocked, match='uncertain_base_reserved'):
        authorize_cash_sell(exchange, native('.1'), {'BTC': D('.1')}, now=102, max_age=5)
    exchange.account_balance_observations['BTC']['observed_at'] = 102
    authorize_cash_sell(exchange, native('.1'), {'BTC': D('.1')}, now=102, max_age=5)


def test_original_resident_sell_base_fee_does_not_shrink_when_current_fee_changes():
    exchange, resident = connector('.405'), native('.2', 'resident')
    resident._cash_sell_reserved_base = D('.21')
    exchange.in_flight_orders['resident'] = resident
    with pytest.raises(MarketReadinessBlocked, match='uncertain_base_reserved'):
        authorize_cash_sell(exchange, native('.1'), {'BTC': D('.1')}, now=101, max_age=5)
    del resident._cash_sell_reserved_base
    with pytest.raises(MarketReadinessBlocked, match='uncertain_fee_unavailable'):
        authorize_cash_sell(exchange, native('.1'), {'BTC': D('.1')}, now=101, max_age=5)


def test_proven_unsent_rejection_is_not_a_possible_cash_debit():
    exchange, rejected = connector('.35'), native('.2', 'rejected')
    rejected.is_done, rejected.last_update_timestamp = True, 101
    rejected.exchange_order_id, rejected.executed_amount_base, rejected.order_fills = None, D(0), {}
    del rejected._cash_sell_reserved_base
    exchange._order_tracker = SimpleNamespace(all_fillable_orders={'rejected': rejected})
    authorize_cash_sell(exchange, native('.1'), {'BTC': D('.1')}, now=102, max_age=5,
                        proven_unsent={'rejected': rejected})
    for unproved in ({}, {'rejected': native('.2', 'rejected')}):
        with pytest.raises(MarketReadinessBlocked):
            authorize_cash_sell(exchange, native('.1'), {'BTC': D('.1')}, now=102, max_age=5, proven_unsent=unproved)
    for field, value in (('exchange_order_id', 'late-ack'), ('executed_amount_base', D('.01'))):
        previous = getattr(rejected, field)
        setattr(rejected, field, value)
        with pytest.raises(MarketReadinessBlocked):
            authorize_cash_sell(exchange, native('.1'), {'BTC': D('.1')}, now=102, max_age=5,
                                proven_unsent={'rejected': rejected})
        setattr(rejected, field, previous)
