"""Generated deterministic digests retain every original structural check."""
from datetime import timedelta
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.restore_capacity_cases import MODEL, seed_completed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_history_flow import calls, instrument
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_restore_matrix import source
from unittest.mock import patch

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.life_liquidity.storage.contracts import fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.postgres import runtime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.birth import PgQuoteBirthRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import migrate
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_intent import PgSpotQuoteIntent


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [129], indirect=True)
async def test_birth_digest_upgrade_avoids_reencoding_old_bodies_but_still_rejects_corruption(database, request):
    load = runtime.load_migrations
    with patch.object(runtime, 'load_migrations', lambda path: tuple(m for m in load(path) if m.version <= 129)):
        values, _, _ = await source(database, managed_options=dict(lease_ms=10000, owner_duration_ms=300000,
                                                                   spot_options=dict(session_duration=timedelta(minutes=10))),
                                    spot_quote_producer=MODEL)
        f, cfg, owner, _, lease, *_ = values
        try:
            await seed_completed(database, f, cfg, owner, lease, 4, {})
            births = PgQuoteBirthRepository(f)
            intent = PgSpotQuoteIntent('unconsumed', 'controller1', owner, 1, TradeType.BUY, 0, Decimal(1), Decimal(1))
            ticket = await births.issue('unconsumed', intent, model_hash=fingerprint(MODEL),
                                        expected_version=(await f.snapshot(cfg.binding)).cut.checkpoint.revision)
            await births.retire('retire-unconsumed', ticket.ticket_id, cut=(await f.snapshot(cfg.binding)).cut, owner=owner)
        finally:
            await f.close()
    c = database[0]
    await c.execute('CREATE SCHEMA fingerprint_reference')
    for name in ('quote_birth_before_retirement_verified', 'quote_birth_verified'):
        definition = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', f'life_financial.{name}(uuid)')
        definition = definition.replace('FUNCTION life_financial.' + name, 'FUNCTION fingerprint_reference.' + name, 1)
        if name == 'quote_birth_verified':
            definition = definition.replace('life_financial.quote_birth_before_retirement_verified(',
                                            'fingerprint_reference.quote_birth_before_retirement_verified(')
        await c.execute(definition)
    before = await c.fetch('SELECT * FROM life_financial.balances ORDER BY asset')
    await migrate(c, action='up')
    sequence = await instrument(c, 'canonical_json_v1', 'jsonb')
    counts = []
    for schema in ('life_financial', 'fingerprint_reference'):
        await calls(c, sequence, reset=True)
        assert await c.fetchval(f'SELECT {schema}.quote_birth_verified($1)', cfg.account_id)
        counts.append(await calls(c, sequence))
    request.node.user_properties.append(('birth_canonical_calls', str(counts)))
    assert counts[0] < counts[1], counts
    assert await c.fetch('SELECT * FROM life_financial.balances ORDER BY asset') == before
    for table, change, rehash in (
        ('quote_births', "body=jsonb_set(body,'{owner,process_id}',to_jsonb(gen_random_uuid()::text)) WHERE ticket_id='birth:capacity-0'", False),
        ('quote_births', "body=jsonb_set(body,'{owner,process_id}',to_jsonb(gen_random_uuid()::text)) WHERE ticket_id='birth:capacity-0'", True),
        ('quote_birth_retirements', "body=jsonb_set(body,'{receipt,retired}','false'::jsonb)", False),
        ('quote_birth_retirements', "body=jsonb_set(body,'{receipt,retired}','false'::jsonb)", True),
    ):
        tx = c.transaction()
        await tx.start()
        try:
            await c.execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
            await c.execute(f'UPDATE life_financial.{table} SET {change}')
            if rehash:
                await c.execute(f'UPDATE life_financial.{table} SET body_hash=life_financial.command_fingerprint_v1(body)')
            assert not await c.fetchval('SELECT fingerprint_reference.quote_birth_verified($1)', cfg.account_id)
            assert not await c.fetchval('SELECT life_financial.quote_birth_verified($1)', cfg.account_id)
        finally:
            await tx.rollback()
