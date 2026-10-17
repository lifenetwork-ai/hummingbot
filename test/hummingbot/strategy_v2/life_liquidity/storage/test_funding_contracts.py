"""Paid-funding wire decoding retains original units without source authority."""
from decimal import Decimal as D, localcontext
from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital import claim, policy, snapshot

import pytest

from hummingbot.strategy_v2.life_liquidity.carry_monitor import (
    CarryAccount,
    CarryBundle,
    CarryFunding,
    CarryPrice,
    CarryTerms,
    FundingPayment,
)
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.funding import ApplyFunding


def command():
    account = snapshot(pending=(claim(),))
    at = account.observed_at_ms
    bundle = CarryBundle(CarryAccount(account, ('777',), True, True),
                         CarryPrice('LIFE-USDT-SWAP', 1, at, D('1'), 'okx_mark'),
                         CarryPrice('LIFE-USDT-SWAP', 1, at, D('1'), 'okx_index'),
                         CarryFunding('12345', 'LIFE-USDT-SWAP', 1, at, D('.001'), at + 1000, at + 14401000,
                                      at - 1000, at, (FundingPayment('777', at, D('-.123456789012345678')),), True, 'okx_funding'),
                         CarryTerms('12345', 'LIFE-USDT-SWAP', 1, at, D('.001'), D('.002'), policy().tiers, True, 'okx_account_terms'))
    return ApplyFunding('funding', bundle)


def test_funding_wire_roundtrip_keeps_snapshot_claims_tiers_paid_bill_and_decimal_precision():
    original = command()
    with localcontext() as context:
        context.prec = 6
        assert ApplyFunding.from_json(original.json()) == original
        assert original.json()['bundle']['funding']['payments'][0]['signed_payment_quote'] == '-0.123456789012345678'
        assert context.prec == 6


@pytest.mark.parametrize('field,value', [('spot_cash_quote', 1), ('spot_cash_quote', True), ('spot_cash_quote', 'NaN'),
                                         ('mark_price_usdt', float('inf'))])
def test_native_funding_decoder_cannot_coerce_or_round_snapshot_money(field, value):
    data = command().json()
    data['bundle']['account']['snapshot'][field] = value
    with pytest.raises(StorageError):
        ApplyFunding.from_json(data)
