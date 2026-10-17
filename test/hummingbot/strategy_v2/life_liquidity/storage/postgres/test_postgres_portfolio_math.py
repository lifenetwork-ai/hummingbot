"""File joint NAV arithmetic over native settled cash and replayed leg basis."""
from decimal import ROUND_HALF_EVEN, Decimal as D, localcontext

import pytest


async def measure(connection, *, cash='10', life='5', long='0', short='0', long_entry='0', short_entry='0',
                  mark='1', index='1', flows='0', highwater='15'):
    value = await connection.fetchval('SELECT life_financial.portfolio_measure($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)',
                                      *map(D, (cash, life, long, short, long_entry, short_entry, mark, index, flows, highwater)))
    import json
    return json.loads(value)


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [
    {}, dict(long='2', long_entry='1', mark='1.2'), dict(short='2', short_entry='1', mark='.8'),
    dict(long='2', short='3', long_entry='1.1', short_entry='1.2', mark='1.05', index='.95'),
    dict(cash='11', flows='1'), dict(cash='9', flows='-1'),
    dict(cash='9.9', highwater='15'), dict(cash='10.2', highwater='15'),
    dict(long='3', long_entry='1.333333333333333333333333333', mark='2', index='1'),
])
async def test_portfolio_math_matches_file_joint_cash_flow_adjusted_highwater_without_adding_collateral(database, changes):
    fields = dict(cash='10', life='5', long='0', short='0', long_entry='0', short_entry='0', mark='1', index='1', flows='0', highwater='15')
    fields.update(changes)
    result = await measure(database[0], **fields)
    with localcontext() as context:
        context.prec, context.rounding = 28, ROUND_HALF_EVEN
        v = {k: D(value) for k, value in fields.items()}
        unrealized = v['long'] * (v['mark'] - v['long_entry']) + v['short'] * (v['short_entry'] - v['mark'])
        nav = v['cash'] + v['life'] * v['index'] + unrealized
        adjusted = nav - v['flows']
        highwater = max(v['highwater'], adjusted)
        expected = dict(nav_quote=nav, adjusted_nav_quote=adjusted, highwater_quote=highwater,
                        drawdown_bps=max(D('0'), highwater - adjusted) / highwater * D('10000'),
                        unrealized_quote=unrealized, external_flows_quote=v['flows'])
        assert {key: D(result[key]) for key in expected} == expected
        assert result['trading_permission'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [
    dict(cash='-1'), dict(life='-1'), dict(long='1', long_entry='0'),
    dict(short='1', short_entry='0'), dict(mark='0'), dict(index='NaN'), dict(highwater='0'),
])
async def test_unsupported_portfolio_inputs_fail_instead_of_creating_income_or_margin(database, changes):
    import asyncpg
    with pytest.raises(asyncpg.RaiseError, match='LIFE_PORTFOLIO_VALUE_INVALID'):
        await measure(database[0], **changes)


@pytest.mark.asyncio
async def test_joint_nav_arithmetic_is_private_and_cannot_create_or_rewrite_a_financial_fact(database):
    result = await measure(database[0])
    assert D(result['nav_quote']) == 15
    assert await database[0].fetchval('SELECT count(*) FROM life_financial.accounts') == 0
    assert await database[0].fetchval('SELECT count(*) FROM life_financial.financial_events') == 0
    assert not await database[0].fetchval("SELECT has_function_privilege('public','life_financial.portfolio_measure(numeric,numeric,numeric,numeric,numeric,numeric,numeric,numeric,numeric,numeric)','EXECUTE')")
