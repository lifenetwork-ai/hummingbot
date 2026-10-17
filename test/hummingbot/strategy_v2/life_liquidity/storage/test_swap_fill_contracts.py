"""Native fill command boundary checks without a PostgreSQL dependency."""
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal as D, localcontext

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_fills import FillSwap, SwapFillObservation


def command():
    at = datetime(2026, 10, 17, tzinfo=timezone.utc)
    ms = int(at.timestamp() * 1000)
    row = SwapFillObservation('trade', D('1'), D('2'), 'USDT', D('-.001'), ms, D('2'), ms, ms,
                              'independent_market', D('0'), None)
    return FillSwap('fills', 'prepare', 'intent', 'Wire1', 'LIFE-USDT', 'SELL', 'session', 1,
                    '12345', D('1'), (row,), at)


def test_swap_fill_serialization_preserves_signed_pnl_fee_sequence_and_original_snapshot():
    value = command()
    assert FillSwap.from_json(value.json()) == value
    assert value.json()['fills'][0]['signed_fee'] == '-0.001'
    assert value.json()['fills'][0]['realized_pnl_usdt'] == '0'


@pytest.mark.parametrize('changes', [dict(sequence=True), dict(sequence=-1), dict(fee_currency='LIFE'),
                                     dict(trade_id='bad:trade'), dict(realized_pnl_usdt=D('NaN'))])
def test_derivative_fill_identity_and_contract_units_cannot_be_coerced(changes):
    with pytest.raises(StorageError):
        replace(command().fills[0], **changes)


def test_cumulative_snapshot_comparison_does_not_round_under_the_callers_decimal_context():
    original = command()
    rows = (replace(original.fills[0], quantity_base_life=D('1.123456789')),
            replace(original.fills[0], trade_id='second', quantity_base_life=D('.123456789')))
    with localcontext() as context:
        context.prec = 6
        value = replace(original, cumulative_base_life=D('1.246913578'), fills=rows)
        assert FillSwap.from_json(value.json()) == value
        assert context.prec == 6
        with pytest.raises(StorageError, match='SNAPSHOT_INVALID'):
            replace(original, cumulative_base_life=D('1.24691'), fills=rows)
