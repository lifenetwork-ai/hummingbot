"""Explicit hot/history capacity; original identities and money stay retained."""
from datetime import timedelta
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.restore_capacity_cases import MODEL, seed_completed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_restore_matrix import source

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.postgres.birth import PgQuoteBirthRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_intent import PgSpotQuoteIntent


async def fixture(database, *, tickets=4, bytes_limit=2**20, declare=True):
    retention = dict(mode='SETTLED_NATIVE_V1', max_retained_tickets=tickets, max_retained_body_bytes=bytes_limit)
    model = {**MODEL, 'birth_retention': retention} if declare else MODEL
    values, _, _ = await source(database, managed_options=dict(lease_ms=10000, owner_duration_ms=300000,
                                                               spot_options=dict(session_duration=timedelta(minutes=10))),
                                spot_quote_producer=model)
    return values, model, retention


@pytest.mark.asyncio
async def test_settled_ticket_history_exceeds_hot_capacity_without_erasing_any_original_fact(database, request, tmp_path):
    values, model, policy = await fixture(database)
    f, cfg, owner, _, lease, *_ = values
    try:
        import json
        definitions = await database[0].fetch("""SELECT oid::regprocedure::text AS signature,pg_get_functiondef(oid) AS definition,
            encode(sha256(convert_to(prosrc,'UTF8')),'hex') AS sha256 FROM pg_proc
            WHERE pronamespace='life_financial'::regnamespace AND proname IN (
                'quote_birth_before_retirement_verified','quote_birth_issue','spot_body','quote_birth_verified')""")
        artifact = tmp_path / 'retention_predecessors.json'
        artifact.write_text(json.dumps([dict(r) for r in definitions]))
        request.node.user_properties.append(('retention_predecessors', str(artifact)))
        await seed_completed(database, f, cfg, owner, lease, 4, {}, model=model, hot_tickets=2, retention=policy)
        c = database[0]
        snapshot = await f.snapshot(cfg.binding)
        assert not snapshot.recovery_required
        assert (snapshot.balance.total_base_life, snapshot.balance.total_quote_usdt) == (5, 10)
        assert snapshot.budget.campaign_execution_loss_usdt == snapshot.budget.campaign_subsidy_committed_usdt == 0
        for table in ('quote_births', 'quote_birth_consumptions', 'send_attempts', 'spot_inventory_members', 'fills'):
            assert await c.fetchval(f'SELECT count(*) FROM life_financial.{table}') == 4, table
        assert await c.fetchval('SELECT count(*) FROM life_financial.spot_inventory_settlements') == 2
        assert await c.fetchval('SELECT count(*) FROM life_recorder."Executors"') == 4
        before = await c.fetch('SELECT * FROM life_financial.quote_births ORDER BY ticket_id')
        historical = await PgQuoteBirthRepository(f).read('birth:capacity-0')
        assert historical.historical and not historical.trading_permission
        assert await c.fetch('SELECT * FROM life_financial.quote_births ORDER BY ticket_id') == before
        from decimal import Decimal
        duplicate = PgSpotQuoteIntent('capacity-0', 'controller1', owner, 1, TradeType.BUY, 0, Decimal(1), Decimal(1))
        with pytest.raises(StorageError, match='PG_BIRTH_IDENTITY_USED'):
            await PgQuoteBirthRepository(f).issue('reuse-old-intent', duplicate, model_hash=fingerprint(model),
                                                  expected_version=snapshot.cut.checkpoint.revision)
        for role in ('life_pg2_runtime', 'life_pg2_reporter'):
            for privilege in ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE'):
                assert not await c.fetchval("SELECT has_table_privilege($1,'life_financial.quote_birth_retention_policies',$2)", role, privilege)
            for signature in ('life_financial.quote_birth_cold_tickets(uuid)',
                              'life_financial.quote_birth_capacity_valid(uuid,bigint,bigint,bigint)'):
                assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, signature)
        for table, change in (
            ('quote_births', "body_hash=repeat('f',64) WHERE ticket_id='birth:capacity-0'"),
            ('quote_birth_retention_policies', 'max_retained_tickets=2'),
        ):
            tx = c.transaction()
            await tx.start()
            try:
                await c.execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
                await c.execute(f'UPDATE life_financial.{table} SET {change}')
                assert not await c.fetchval('SELECT life_financial.quote_birth_verified($1)', cfg.account_id), table
            finally:
                await tx.rollback()
    finally:
        await f.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['legacy', 'active', 'history', 'bytes', 'undeclared', 'unprovisioned'])
async def test_each_explicit_capacity_and_configuration_guard_retains_the_original_rows(database, mode):
    from decimal import Decimal
    values, model, policy = await fixture(database, tickets=2 if mode == 'history' else 4,
                                          bytes_limit=1 if mode == 'bytes' else 2**20,
                                          declare=mode not in ('legacy', 'undeclared'))
    f, cfg, owner, _, lease, *_ = values
    hot = 1 if mode == 'active' else 2
    count = 2 if mode in ('legacy', 'history') else 1 if mode == 'active' else 0
    try:
        await seed_completed(database, f, cfg, owner, lease, count, {}, model=model, hot_tickets=hot,
                             retention=None if mode in ('legacy', 'unprovisioned') else policy)
        before = await f.snapshot(cfg.binding)
        intent = PgSpotQuoteIntent('retention-next', 'controller1', owner, 1, TradeType.BUY, 0, Decimal(1), Decimal(1))
        with pytest.raises(StorageError, match='PG_BIRTH_CAPACITY|PG_BIRTH_UNVERIFIED'):
            await PgQuoteBirthRepository(f).issue('retention-next', intent, model_hash=fingerprint(model),
                                                  expected_version=before.cut.checkpoint.revision)
        after = await f.snapshot(cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.fills') == count
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.quote_births') == count
    finally:
        await f.close()
