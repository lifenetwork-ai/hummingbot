"""Fill replay compares fee values without rewriting original JSON payloads."""
from copy import deepcopy
from decimal import Decimal

import pytest

from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, DeductedFromReturnsTradeFee, TokenAmount


@pytest.mark.parametrize('kind', [AddedToCostTradeFee, DeductedFromReturnsTradeFee])
@pytest.mark.parametrize('amount', ['0.20', '-0.20'])
def test_fee_models_compare_exact_decimals_and_preserve_original_payload(kind, amount):
    original = kind(percent=Decimal('0.0010'), flat_fees=[TokenAmount('USDT', Decimal(amount))]).to_json()
    replay = kind(percent=Decimal('0.001'), flat_fees=[TokenAmount('USDT', Decimal(amount + '00'))]).to_json()
    before = deepcopy(original)
    assert MarketsRecorder._same_trade_fee(original, replay)
    assert original == before


@pytest.mark.parametrize('change', ['kind', 'token', 'sign', 'amount', 'percent', 'percent_token', 'order'])
def test_fee_replay_cannot_hide_changed_economic_fields(change):
    original = AddedToCostTradeFee(flat_fees=[TokenAmount('USDT', Decimal('.2')),
                                              TokenAmount('BTC', Decimal('.0001'))]).to_json()
    replay = deepcopy(original)
    if change == 'kind':
        replay['fee_type'] = DeductedFromReturnsTradeFee.type_descriptor_for_json()
    elif change == 'token':
        replay['flat_fees'][0]['token'] = 'LIFE'
    elif change == 'sign':
        replay['flat_fees'][0]['amount'] = '-0.2'
    elif change == 'amount':
        replay['flat_fees'][0]['amount'] = '0.20001'
    elif change == 'percent':
        replay['percent'] = '0.01'
    elif change == 'percent_token':
        replay['percent_token'] = 'USDT'
    else:
        replay['flat_fees'].reverse()
    assert not MarketsRecorder._same_trade_fee(original, replay)


@pytest.mark.parametrize('change', ['extra', 'nested_extra', 'missing', 'nan', 'infinity', 'bad_type'])
def test_nonmatching_unknown_or_nonfinite_shapes_remain_rejected(change):
    original = AddedToCostTradeFee(flat_fees=[TokenAmount('USDT', Decimal('.2'))]).to_json()
    replay = deepcopy(original)
    if change == 'extra':
        replay['extension'] = 'unexpected'
    elif change == 'nested_extra':
        replay['flat_fees'][0]['extension'] = 'unexpected'
    elif change == 'missing':
        replay.pop('percent_token')
    elif change == 'nan':
        replay['percent'] = 'NaN'
    elif change == 'infinity':
        replay['flat_fees'][0]['amount'] = 'Infinity'
    else:
        replay = None
    assert not MarketsRecorder._same_trade_fee(original, replay)


def test_identical_extended_payload_keeps_existing_strict_behavior():
    original = AddedToCostTradeFee().to_json()
    original['extension'] = 'unchanged'
    assert MarketsRecorder._same_trade_fee(original, deepcopy(original))
