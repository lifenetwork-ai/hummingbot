"""Audit operation-family lookups on original configured native history."""
import json
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_unused_release import stranded

import pytest


def nodes(node):
    yield node
    for child in node.get('Plans', ()):
        yield from nodes(child)


@pytest.mark.asyncio
async def test_recorder_audit_family_lookups_do_not_read_unrelated_command_json(database, monkeypatch):
    t, service, _ = await stranded(database, monkeypatch)
    try:
        c = database[0]
        for kind in ('RECORDER_APPLY', 'RECORDER_EVENT_APPLY', 'RECORDER_CONFIG_STAGE'):
            query = '''SELECT operation_id FROM life_financial.operations
                WHERE account_id=$1 AND command->>'kind'=$2 ORDER BY operation_id'''
            expected = await c.fetch(query, t.cfg.account_id, kind)
            plan = json.loads(await c.fetchval('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) ' + query,
                                               t.cfg.account_id, kind))[0]['Plan']
            scans = [n for n in nodes(plan) if n.get('Relation Name') == 'operations']
            assert scans and all('Index' in n['Node Type'] for n in scans), (kind, scans)
            assert all(n.get('Rows Removed by Filter', 0) == 0 for n in scans), (kind, scans)
            async with c.transaction():
                await c.execute('SET LOCAL enable_indexscan=off; SET LOCAL enable_bitmapscan=off')
                assert await c.fetch(query, t.cfg.account_id, kind) == expected
    finally:
        await service.close()
