"""Retained releases share account-wide terminal proofs within one audit only."""
import asyncio
from datetime import timedelta
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_cycle import cancellation_feed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_trading import configured
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_release import setup

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import migrate


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [104], indirect=True)
async def test_release_audit_checks_terminal_history_once_and_rechecks_next_statement(database):
    c = database[0]
    # Capture the actual predecessor audit at schema 104, then upgrade before
    # constructing the current service (which requires later ownership tables).
    # This compares both algorithms on identical current retained releases;
    # it is not a claim that the current service runs on an old schema.
    original = await c.fetchval("SELECT pg_get_functiondef('life_financial.shared_release_verified(uuid)'::regprocedure)")
    await c.execute(original.replace('life_financial.shared_release_verified(', 'life_financial.test_release_reference('))
    await migrate(c, action='up')
    t, service = await configured(
        database, provisional_quotes=True, maker_cycle=True,
        model_change=lambda b: b.update(maker_schedule=dict(
            schedule_id='synthetic-release-audit', levels=[0], refresh_interval_ms=1, max_cycles=1),
            quote_schedule_timeout_ms=60000),
        spot_overrides=dict(owner_duration=timedelta(seconds=300), session_duration=timedelta(seconds=360)))
    stream, _ = await cancellation_feed(t)
    try:
        await service.start_makers()
        assert service.makers.completed_cycles == 1
        assert await c.fetchval('SELECT count(*) FROM life_financial.shared_claim_releases') == 2
    finally:
        try:
            await service.close()
        finally:
            stream.cancel()
            await asyncio.gather(stream, return_exceptions=True)
    await c.execute('INSERT INTO life_financial.access_bindings VALUES(current_user,$1,true)', t.cfg.account_id)
    await c.execute('CREATE SEQUENCE life_financial.test_terminal_count MINVALUE 0 START 0')
    definition = await c.fetchval("SELECT pg_get_functiondef('life_financial.spot_lifecycle_verified(uuid)'::regprocedure)")
    source = await c.fetchval("SELECT prosrc FROM pg_proc WHERE oid='life_financial.spot_lifecycle_verified(uuid)'::regprocedure")
    await c.execute(definition.replace('life_financial.spot_lifecycle_verified(', 'life_financial.test_full_lifecycle('))
    await c.execute(definition.replace('LANGUAGE sql', 'LANGUAGE plpgsql').replace(source,
                    "BEGIN PERFORM nextval('life_financial.test_terminal_count'); RETURN life_financial.test_full_lifecycle(p_account); END;"))

    async def checked(name):
        await c.execute("SELECT setval('life_financial.test_terminal_count',0,false)")
        verdict = await c.fetchval('SELECT life_financial.' + name + '($1)', t.cfg.account_id)
        count = await c.fetchval('SELECT CASE WHEN is_called THEN last_value+1 ELSE 0 END FROM life_financial.test_terminal_count')
        return verdict, count

    assert await checked('test_release_reference') == (True, 2)
    assert await checked('shared_release_verified') == (True, 1)
    assert await checked('shared_release_verified') == (True, 1)
    # Damage retained truth without changing account version. A fresh audit
    # must detect it even after two successful calls on the same connection.
    faults = (
        ('reservations', "state='HELD',remaining=least(amount,1)", "intent_id=(SELECT min(intent_id) FROM life_financial.shared_claim_releases)"),
        ('spot_terminal_facts', "command=jsonb_set(command,'{facts,cumulative_base_life}','\"1\"'::jsonb)", 'true'),
        ('shared_claim_releases', "basis=basis||'{\"carry_operation_id\":\"missing\"}'::jsonb", 'true'),
        ('operations', "receipt=receipt||'{\"released\":false}'::jsonb", "command->>'kind'='SHARED_RELEASE'"),
    )
    for table, change, condition in faults:
        tx = c.transaction()
        await tx.start()
        try:
            await c.execute('ALTER TABLE life_financial.' + table + ' DISABLE TRIGGER USER')
            assert await c.execute('UPDATE life_financial.' + table + ' SET ' + change + ' WHERE ' + condition) != 'UPDATE 0'
            await c.execute('ALTER TABLE life_financial.' + table + ' ENABLE TRIGGER USER')
            assert (await checked('test_release_reference'))[0] is False
            assert (await checked('shared_release_verified'))[0] is False
        finally:
            await tx.rollback()
        assert await checked('shared_release_verified') == (True, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize('released', [False, True])
async def test_swap_release_audit_checks_only_the_product_with_retained_releases(database, released):
    c = database[0]
    values, repo, command, *_ = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        if released:
            await repo.release(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
    finally:
        await financial.close()
    await c.execute('INSERT INTO life_financial.access_bindings VALUES(current_user,$1,true)', cfg.account_id)
    # Keep the original standalone verifier intact in a test-only copy; count
    # actual full evaluations rather than substituting a true verdict.
    definition = await c.fetchval("SELECT pg_get_functiondef('life_financial.swap_terminal_verified(uuid)'::regprocedure)")
    source = await c.fetchval("SELECT prosrc FROM pg_proc WHERE oid='life_financial.swap_terminal_verified(uuid)'::regprocedure")
    await c.execute(definition.replace('life_financial.swap_terminal_verified(', 'life_financial.test_full_swap_terminal('))
    await c.execute('CREATE SEQUENCE life_financial.test_swap_terminal_count MINVALUE 0 START 0')
    await c.execute(definition.replace(source,
                    "BEGIN PERFORM nextval('life_financial.test_swap_terminal_count'); RETURN life_financial.test_full_swap_terminal(p_account); END;"))
    for _ in range(2):
        await c.execute("SELECT setval('life_financial.test_swap_terminal_count',0,false)")
        assert await c.fetchval('SELECT life_financial.shared_release_verified($1)', cfg.account_id) is True
        assert await c.fetchval('SELECT CASE WHEN is_called THEN last_value+1 ELSE 0 END FROM life_financial.test_swap_terminal_count') == int(released)
