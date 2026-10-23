"""Offline candidate enrollment evidence; never a runtime/send permission.

Capture this only after trusted installation/provisioning. A digest taken from
an unknown database cannot bless it. Every comparison re-reads live catalogs;
no cached readiness flag, source scan replacement or DDL-race claim is made.
"""
import json

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.postgres.schema_image import CATALOG, NAMESPACES

NAMES = (*NAMESPACES, 'fill_projection_spike')


async def catalog(c):
    if not c.is_in_transaction():
        raise StorageError('PROTOTYPE_CATALOG_TRANSACTION_REQUIRED')
    result = {}
    for kind, query in CATALOG.items():
        rows = await c.fetch(query, NAMES)
        result[kind] = sorted((r['name'], json.loads(r['body'])) for r in rows)
    # Owners and login escalation are part of the trust boundary too.
    result['owners'] = [tuple(r) for r in await c.fetch('''
        SELECT 'relation',n.nspname||'.'||r.relname,pg_get_userbyid(r.relowner)
        FROM pg_class r JOIN pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname=ANY($1::text[])
        UNION ALL SELECT 'function',p.oid::regprocedure::text,pg_get_userbyid(p.proowner)
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=ANY($1::text[])
        ORDER BY 1,2''', NAMES)]
    result['roles'] = [tuple(r) for r in await c.fetch('''SELECT rolname,rolsuper,rolbypassrls,
        rolcreaterole,rolcreatedb,rolreplication FROM pg_roles
        WHERE rolname IN ('life_pg2_migrator','life_pg2_runtime','life_pg2_reporter') ORDER BY rolname''')]
    result['memberships'] = [tuple(r) for r in await c.fetch('''SELECT pg_get_userbyid(member),
        pg_get_userbyid(roleid),admin_option,inherit_option,set_option FROM pg_auth_members
        WHERE pg_get_userbyid(member) IN ('life_pg2_migrator','life_pg2_runtime','life_pg2_reporter')
        ORDER BY 1,2''')]
    return fingerprint(result)


async def verify(c, expected):
    if await catalog(c) != expected:
        raise StorageError('PROTOTYPE_CATALOG_DRIFT')
