"""Causal derivative arithmetic on disposable PostgreSQL; no execution grant."""
import json
from decimal import ROUND_HALF_EVEN, Decimal as D, localcontext

import pytest


def trade(key='trade1', *, side='BUY', action='OPEN', amount='2', price='3', pnl='0', fee='-.01', at=1000, sequence=None):
    return dict(trade_id=key, intent_id='intent:' + key, side=side, position_action=action,
                quantity_base=amount, price_usdt=price, fee_currency='USDT', signed_fee=fee,
                realized_pnl_quote=pnl, at_ms=at, sequence=sequence)


def anchor(**changes):
    return {**dict(long_base='0', short_base='0', opening_long_entry='0', opening_short_entry='0', observed_at_ms=900), **changes}


async def replay(connection, trades, *, opening=None, payments=(), mode='HEDGE', upper=2000, bound=64):
    query = 'SELECT life_financial.swap_position_replay($1::jsonb,$2::jsonb,$3::jsonb,$4,$5,$6)'
    value = await connection.fetchval(query, json.dumps(opening or anchor()), json.dumps(trades), json.dumps(payments), mode, upper, bound)
    return json.loads(value)


@pytest.mark.asyncio
async def test_causal_long_open_partial_close_and_funding_keep_distinct_cash_components(database):
    rows = [trade(), trade('trade2', side='SELL', action='CLOSE', amount='1', price='2', pnl='-1', at=1100)]
    payments = [dict(bill_id='77', signed_amount_quote='-.2', at_ms=1200)]
    result = await replay(database[0], rows, payments=payments)
    assert D(result['long_base']) == 1 and D(result['long_entry']) == 3 and D(result['short_base']) == 0
    assert D(result['realized_pnl_quote']) == -1 and D(result['signed_fees_quote']) == D('-.02')
    assert D(result['paid_funding_quote']) == D('-.2') and D(result['cash_delta_quote']) == D('-1.22')
    assert D(result['execution_loss_quote']) == D('1.02')  # Funding belongs to its own budget, never execution loss.
    assert result['events'][0] == dict(event_id='JOINT:1000:TRADE:trade1', at_ms=1000, signed_amount_usdt='-0.01')
    assert result['events'][-1]['event_id'] == 'JOINT:1200:FUNDING:77'
    assert result['trading_permission'] is False


@pytest.mark.asyncio
async def test_short_close_uses_short_basis_and_does_not_net_losing_fees_against_profit(database):
    rows = [trade('short', side='SELL'), trade('close', action='CLOSE', amount='1', price='2', pnl='1', fee='-.03', at=1100)]
    result = await replay(database[0], rows)
    assert D(result['short_base']) == 1 and D(result['short_entry']) == 3
    assert D(result['cash_delta_quote']) == D('.96') and D(result['execution_loss_quote']) == D('.04')


@pytest.mark.asyncio
@pytest.mark.parametrize('sequence', [None, 1])
async def test_same_timestamp_requires_complete_unique_collector_sequence(database, sequence):
    import asyncpg
    rows = [trade(sequence=sequence), trade('trade2', amount='1', price='1', sequence=sequence)]
    with pytest.raises(asyncpg.RaiseError, match='JOINT_TRADE_ORDER_AMBIGUOUS'):
        await replay(database[0], rows)


@pytest.mark.asyncio
async def test_opaque_ids_do_not_define_order_and_input_array_order_does_not_change_projection(database):
    rows = [trade('z', sequence=1), trade('a', amount='1', price='6', sequence=2)]
    first = await replay(database[0], rows)
    assert first == await replay(database[0], list(reversed(rows)))
    assert D(first['long_base']) == 3 and D(first['long_entry']) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize('rows,mode,reason', [
    ([trade(action='CLOSE')], 'HEDGE', 'JOINT_CLOSE_PNL_INVALID'),
    ([trade(pnl='1')], 'HEDGE', 'JOINT_OPEN_PNL_INVALID'),
    ([trade(), trade('short', side='SELL', at=1100)], 'ONEWAY', 'JOINT_ONEWAY_REVERSE_INVALID'),
    ([trade(), trade('close', side='SELL', action='CLOSE', amount='1', price='4', pnl='.999', at=1100)], 'HEDGE', 'JOINT_CLOSE_PNL_INVALID'),
    ([trade(), trade()], 'HEDGE', 'JOINT_TRADE_IDENTITY_CONFLICT'),
    ([trade(at=800)], 'HEDGE', 'JOINT_TRADE_INVALID'),
    ([trade(at=2001)], 'HEDGE', 'JOINT_TRADE_INVALID'),
    ([{**trade(), 'fee_currency': 'LIFE'}], 'HEDGE', 'JOINT_TRADE_INVALID'),
    ([{**trade(), 'sequence': True}], 'HEDGE', 'JOINT_TRADE_INVALID'),
    ([{**trade(), 'quantity_base': '0.0000000000000000001'}], 'HEDGE', 'JOINT_TRADE_INVALID'),
])
async def test_ambiguous_or_unreplayable_accounting_is_rejected_without_coercion(database, rows, mode, reason):
    import asyncpg
    with pytest.raises(asyncpg.RaiseError, match=reason):
        await replay(database[0], rows, mode=mode)


@pytest.mark.asyncio
async def test_nonterminating_weighted_entry_matches_declared_decimal28_half_even(database):
    rows = [trade(amount='2', price='1'), trade('second', amount='1', price='2', at=1100)]
    result = await replay(database[0], rows)
    with localcontext() as context:
        context.prec, context.rounding = 28, ROUND_HALF_EVEN
        assert D(result['long_entry']) == D(4) / D(3)
    assert D(result['long_base']) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize('numerator,denominator', [
    ('1', '3'), ('-1', '3'), ('0.00000000000000000000000000012345678901234567890123456785', '1'),
    ('12345678901234567890123456785', '1'), ('12345678901234567890123456795', '1'),
    ('99999999999999999999999999995', '1'), ('1e-38', '1e37'), ('999999999999999999', '.000000000000000001'),
])
async def test_sql_decimal28_rounding_uses_integer_remainder_without_division_or_log_guess(database, numerator, denominator):
    result = await database[0].fetchval('SELECT life_financial.joint_decimal28($1::numeric,$2::numeric)', D(numerator), D(denominator))
    with localcontext() as context:
        context.prec, context.rounding = 28, ROUND_HALF_EVEN
        assert result == D(numerator) / D(denominator)


@pytest.mark.asyncio
@pytest.mark.parametrize('payments', [
    [dict(bill_id='1', signed_amount_quote='1', at_ms=800)],
    [dict(bill_id='1', signed_amount_quote='1', at_ms=2001)],
    [dict(bill_id='1', signed_amount_quote='NaN', at_ms=1000)],
    [dict(bill_id='1', signed_amount_quote='1', at_ms=1000)] * 2,
])
async def test_funding_identities_and_coverage_are_strict(database, payments):
    import asyncpg
    with pytest.raises(asyncpg.RaiseError, match='JOINT_FUNDING_INVALID'):
        await replay(database[0], [], payments=payments)


@pytest.mark.asyncio
async def test_work_bound_is_explicit_and_schema_functions_do_not_bootstrap_money(database):
    import asyncpg
    with pytest.raises(asyncpg.RaiseError, match='JOINT_REPLAY_BOUND'):
        await replay(database[0], [trade(), trade('two', at=1100)], bound=1)
    result = await replay(database[0], [])
    assert D(result['cash_delta_quote']) == 0
    assert await database[0].fetchval('SELECT count(*) FROM life_financial.accounts') == 0
    assert not await database[0].fetchval("SELECT has_function_privilege('public','life_financial.swap_position_replay(jsonb,jsonb,jsonb,text,bigint,integer)','EXECUTE')")


@pytest.mark.asyncio
@pytest.mark.parametrize('at,reason', [('no-clock', 'JOINT_FUNDING_INVALID'), (True, 'JOINT_FUNDING_INVALID')])
async def test_funding_bad_clock_is_classified_before_chronological_cast(database, at, reason):
    import asyncpg
    with pytest.raises(asyncpg.RaiseError, match=reason):
        await replay(database[0], [], payments=[dict(bill_id='77', signed_amount_quote='1', at_ms=at)])


@pytest.mark.asyncio
async def test_native_basis_and_cash_events_match_the_existing_file_joint_projection(database):
    from test.hummingbot.strategy_v2.life_liquidity.test_joint_replay_precision import projection
    result = await replay(database[0], [trade('first', amount='2', price='1'), trade('second', amount='1', price='2', at=1100)])
    with localcontext() as context:
        context.prec, context.rounding = 28, ROUND_HALF_EVEN
        events, valuation = projection()
        assert {v['event_id']: D(v['signed_amount_usdt']) for v in result['events']} == events
        assert D(result['long_base']) * (D('2') - D(result['long_entry'])) == D(valuation['unrealized_quote'])


@pytest.mark.asyncio
async def test_sql_rounding_matches_independent_decimal_for_varied_signs_scales_and_carries(database):
    import random
    generator = random.Random(731)
    for _ in range(200):
        numerator = D(generator.randrange(-10**37, 10**37)).scaleb(-generator.randrange(0, 39))
        denominator = D(generator.randrange(1, 10**37)).scaleb(-generator.randrange(0, 39))
        actual = await database[0].fetchval('SELECT life_financial.joint_decimal28($1::numeric,$2::numeric)', numerator, denominator)
        with localcontext() as context:
            context.prec, context.rounding = 28, ROUND_HALF_EVEN
            assert actual == numerator / denominator
