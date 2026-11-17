from decimal import Decimal

import pytest

from hummingbot.model.overview_fees import aggregate_fees, fee_in_usdt


def fill(*, quote='USDT', base='LIFE', converted='0', fee=None, market='okx'):
    native_fee = fee or {'percent': '0', 'percent_token': None,
                         'flat_fees': [{'token': 'USDT', 'amount': converted}] if converted not in ('0', None) else []}
    return dict(quote_asset=quote, base_asset=base, market=market,
                trade_fee_in_quote=converted, trade_fee=native_fee)


def test_fee_conversion_accepts_usdt_or_base_native_fees_and_preserves_rebates():
    assert fee_in_usdt(fill(converted='0.125')) == (Decimal('0.125'), True)
    assert fee_in_usdt(fill(converted='-0.025', fee={
        'percent': '0', 'percent_token': None, 'flat_fees': [{'token': 'LIFE', 'amount': '.25'}]})) == (Decimal('-0.025'), True)


def test_third_token_quote_asset_mismatch_and_legacy_zero_are_incomplete():
    assert fee_in_usdt(fill(converted='0.05', fee={
        'percent': '0.01', 'percent_token': 'BNB', 'flat_fees': []})) == (None, False)
    assert fee_in_usdt(fill(quote='BTC', converted='0.05')) == (None, False)
    assert fee_in_usdt(fill(converted='0', fee={
        'percent': '0', 'percent_token': None, 'flat_fees': [{'token': 'USDT', 'amount': '.1'}]})) == (None, False)
    assert fee_in_usdt(fill(converted=None, fee={
        'percent': '0', 'percent_token': None, 'flat_fees': [{'token': 'BNB', 'amount': '.1'}]})) == (None, False)


def test_known_zero_requires_evidence_of_no_native_fee_and_invalid_decimal_is_unknown():
    assert fee_in_usdt(fill(converted='0')) == (Decimal('0'), True)
    assert fee_in_usdt(fill(converted='NaN')) == (None, False)


def test_aggregate_preserves_decimal_precision_market_split_and_partial_reason():
    rows = [fill(converted='0.000000000000000003'), fill(converted='-.000000000000000001', market='okx_perpetual'),
            fill(converted=None, fee={'percent': '0', 'percent_token': None, 'flat_fees': [{'token': 'BNB', 'amount': '.1'}]})]
    result = aggregate_fees(rows)
    assert result['total_usdt'] == Decimal('0.000000000000000002')
    assert result['by_market'] == {'spot': Decimal('0.000000000000000003'),
                                   'perpetual': Decimal('-0.000000000000000001')}
    assert result['history_complete'] is False
    assert result['unconverted_count'] == 1


@pytest.mark.parametrize('value', ['Infinity', '-Infinity', 'garbage', None])
def test_unusable_stored_conversion_never_enters_total(value):
    assert fee_in_usdt(fill(converted=value)) == (None, False)
