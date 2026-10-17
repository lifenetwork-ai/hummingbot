"""Joint accounting owns its arithmetic context; callers cannot change basis."""
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal as D, localcontext
from types import SimpleNamespace

import pytest

from hummingbot.strategy_v2.life_liquidity.joint_recovery import JointRecovery, RecoveryTrade


def projection():
    replay = object.__new__(JointRecovery)
    replay._binding = dict(opening_long_entry='0', opening_short_entry='0')
    replay.controller = SimpleNamespace(_carry_monitor=None, _fill_attributor=None)
    replay.reservations = SimpleNamespace(cashflow_events={}, joint_cash_events={})
    replay._repository_application = None
    state = dict(anchor=dict(spot_life_base='0', spot_cash_quote='1000', long_base='0', short_base='0', observed_at_ms=900),
                 opening_cashflows={}, opening_joint={}, bundle=None, highwater='1000')
    claim = SimpleNamespace(product='SWAP', side='BUY', position_action='OPEN')
    trades = dict(first=(claim, RecoveryTrade('first', D('2'), D('1'), 'USDT', D('-.01'), D('0'), 1000)),
                  second=(claim, RecoveryTrade('second', D('1'), D('2'), 'USDT', D('-.01'), D('0'), 1100)))
    obs = SimpleNamespace(position_mode='HEDGE', spot_life_base=D('0'), spot_cash_quote=D('999.98'),
                          long_base=D('3'), short_base=D('0'), mark_price_usdt=D('2'), index_price_usdt=D('1'))
    return replay._financial_replay(SimpleNamespace(payments=(), observed_at_ms=2000), state, obs, trades)


@pytest.mark.parametrize('precision,rounding', [(6, ROUND_DOWN), (28, ROUND_DOWN), (80, ROUND_HALF_EVEN)])
def test_file_joint_replay_owns_decimal28_half_even_and_restores_the_caller(precision, rounding):
    with localcontext() as context:
        context.prec, context.rounding = 28, ROUND_HALF_EVEN
        expected = projection()
    with localcontext() as context:
        context.prec, context.rounding = precision, rounding
        assert projection() == expected
        assert context.prec == precision and context.rounding == rounding
