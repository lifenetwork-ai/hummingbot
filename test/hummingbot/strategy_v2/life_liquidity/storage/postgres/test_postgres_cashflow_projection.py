"""Raw cashflow projection planning, scope and repaired-origin equivalence."""
import json
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import backend
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_cashflow_repair import setup

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair import PgJointRepairRepository


def nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from nodes(child)


@pytest.mark.asyncio
async def test_cashflow_projection_is_planner_visible_and_stays_private(database):
    runtime, cfg, _ = await backend(database)
    c = database[0]
    try:
        plan = json.loads(await c.fetchval('''EXPLAIN(VERBOSE,FORMAT JSON)
            SELECT * FROM life_financial.cashflow_effective($1) WHERE bill_id=$2''', cfg.account_id, '101'))
        assert not any(n.get('Function Name') == 'cashflow_effective' for n in nodes(plan))
        assert {'spot_cashflow_facts', 'spot_cashflow_applications'} <= {n.get('Relation Name') for n in nodes(plan)}
        for role in ('public', 'life_pg2_runtime', 'life_pg2_reporter'):
            assert not await c.fetchval("SELECT has_function_privilege($1,'life_financial.cashflow_effective(uuid)','EXECUTE')", role)
        assert not await c.fetchval("SELECT prosecdef FROM pg_proc WHERE oid='life_financial.cashflow_effective(uuid)'::regprocedure")
        assert await c.fetchval("SELECT proconfig IS NULL FROM pg_proc WHERE oid='life_financial.cashflow_effective(uuid)'::regprocedure")
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('currency', ['LIFE', 'USDT'])
async def test_inline_cashflow_projection_matches_original_before_and_after_signed_repair(database, currency):
    values, _, _, command, facts, cut, signed, importer, _ = await setup(database, currency=currency)
    financial, cfg, owner = values[:3]
    c = database[0]
    try:
        # Independent predecessor query; this raw projection never verifies a
        # financial verdict, books money or substitutes for native recovery.
        original = '''SELECT f.* FROM life_financial.spot_cashflow_facts f
            WHERE f.account_id=$1 AND NOT EXISTS(SELECT 1 FROM life_financial.spot_cashflow_applications a
                WHERE a.account_id=f.account_id AND a.bill_id=f.bill_id)
            UNION ALL SELECT account_id,bill_id,command,'APPLIED'::text,NULL::life_financial.identity,source_operation_id,source_hash
                FROM life_financial.spot_cashflow_applications WHERE account_id=$1'''

        async def equal():
            expected = await c.fetch('SELECT to_jsonb(t) AS row FROM (' + original + ') t ORDER BY bill_id', cfg.account_id)
            actual = await c.fetch('SELECT to_jsonb(t) AS row FROM life_financial.cashflow_effective($1) t ORDER BY bill_id', cfg.account_id)
            assert actual == expected and actual
            return [json.loads(r['row']) for r in actual]
        before = await equal()
        assert any(v['state'] == 'RECOVERY_REQUIRED' for v in before)
        await importer.approve(command, approval=signed, cut=cut, facts=facts)
        await PgJointRepairRepository(importer.batch).resolve(command, cut=cut, owner=owner)
        after = await equal()
        assert all(v['state'] == 'APPLIED' for v in after)
        money = await financial.balance(cfg.binding)
        await c.execute('CREATE SCHEMA untrusted_projection')
        await c.execute('''CREATE FUNCTION untrusted_projection.false_uuid_equal(uuid,uuid) RETURNS boolean
            LANGUAGE sql IMMUTABLE AS 'SELECT false' ''')
        await c.execute('''CREATE OPERATOR untrusted_projection.= (LEFTARG=uuid,RIGHTARG=uuid,
            PROCEDURE=untrusted_projection.false_uuid_equal)''')
        await c.execute('SET search_path=untrusted_projection,pg_temp,pg_catalog')
        # Unlike the predecessor security-definer helper, the inlinable query
        # has no SET clause. Its relations, casts and operators must be pinned.
        actual = await c.fetch('SELECT to_jsonb(t) AS row FROM life_financial.cashflow_effective($1) t ORDER BY bill_id /* fresh untrusted-path plan */', cfg.account_id)
        assert [json.loads(r['row']) for r in actual] == after
        await c.execute('RESET search_path')
        assert await financial.balance(cfg.binding) == money
        assert not await c.fetchval("SELECT EXISTS(SELECT 1 FROM life_financial.cashflow_effective('00000000-0000-0000-0000-000000000000'))")
    finally:
        await c.execute('RESET search_path')
        await financial.close()
