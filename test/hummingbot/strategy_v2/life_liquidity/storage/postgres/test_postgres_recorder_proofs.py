"""Joined reads retain the predecessor's complete recorder integrity decisions."""
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_events import event, setup
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_recorder_application import (
    close,
    setup as executor_setup,
)

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.postgres import runtime as runtime_module
from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import migrate
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import StoreExecutorAction


async def reference(c, name):
    await c.execute('CREATE SCHEMA recorder_reference')
    definition = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', 'life_financial.' + name + '(uuid)')
    await c.execute(definition.replace('FUNCTION life_financial.' + name, 'FUNCTION recorder_reference.original'))


async def compare(c, name, account, expected):
    row = await c.fetchrow('SELECT recorder_reference.original($1) AS previous,life_financial.' + name + '($1) AS current', account)
    assert row['previous'] is expected and row['current'] is expected


async def damage(c, table, changes):
    await c.execute('ALTER TABLE ' + table + ' DISABLE TRIGGER USER')
    assert await c.execute('UPDATE ' + table + ' SET ' + changes) != 'UPDATE 0'
    await c.execute('ALTER TABLE ' + table + ' ENABLE TRIGGER USER')


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [94], indirect=True)
@pytest.mark.parametrize('fault', ['none', 'pending', 'stage', 'apply', 'receipt', 'hash', 'effect', 'head', 'native'])
async def test_event_upgrade_matches_predecessor_on_applied_pending_and_corrupt_history(database, monkeypatch, fault):
    c = database[0]
    load = runtime_module.load_migrations
    with monkeypatch.context() as patch:
        patch.setattr(runtime_module, 'load_migrations', lambda path: tuple(m for m in load(path) if m.version <= 94))
        t, repo, source, at = await setup(database)
        try:
            await repo.project(event(t.cfg.binding, source, at), owner=t.owner)
            await repo.project(event(t.cfg.binding, source, at + 1, 'OrderCancelled'), owner=t.owner)
            if fault == 'pending':
                await repo.stage(event(t.cfg.binding, source, at + 2, 'OrderFilled'), cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
            before = await c.fetch('SELECT * FROM life_financial.balances ORDER BY account_id,asset')
        finally:
            await t.close()
    name = 'recorder_event_integrity'
    assert await c.fetchval('SELECT life_financial.' + name + '($1)', t.cfg.account_id)
    await reference(c, name)
    assert (await migrate(c, action='up'))['applied'] == ['000095_recorder_verification_reads.sql', '000096_recorder_history_lookups.sql', '000097_atomic_metadata_dispatch.sql', '000098_native_quote_capture.sql', '000099_provider_collection.sql']
    await compare(c, name, t.cfg.account_id, True)
    if fault not in ('none', 'pending'):
        table, changes = {
            'stage': ('life_financial.operations', "receipt='{}'::jsonb WHERE command->>'kind'='RECORDER_EVENT_STAGE'"),
            'apply': ('life_financial.operations', "command=command||'{\"kind\":\"WRONG\"}'::jsonb WHERE command->>'kind'='RECORDER_EVENT_APPLY'"),
            'receipt': ('life_financial.projection_receipts', "applied_hash=repeat('0',64)"),
            'hash': ('life_financial.recorder_outbox', "source_hash=repeat('0',64) WHERE stream LIKE 'event:%'"),
            'effect': ('life_financial.recorder_projection_effects', "after_row=after_row||'{\"synthetic\":true}'::jsonb"),
            'head': ('life_financial.recorder_projection_heads', 'version=99'),
            'native': ('life_recorder."Order"', 'amount=amount+1'),
        }[fault]
        await damage(c, table, changes)
        await compare(c, name, t.cfg.account_id, False)
    # Verification and migration never write account inventory rows.
    assert await c.fetch('SELECT * FROM life_financial.balances ORDER BY account_id,asset') == before
    assert (await migrate(c, action='up'))['applied'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [94], indirect=True)
@pytest.mark.parametrize('fault', ['none', 'stage', 'hash', 'receipt', 'transition', 'native'])
async def test_executor_upgrade_matches_predecessor_on_real_initial_and_progress_stream(database, monkeypatch, fault):
    c = database[0]
    load = runtime_module.load_migrations
    with monkeypatch.context() as patch:
        patch.setattr(runtime_module, 'load_migrations', lambda path: tuple(m for m in load(path) if m.version <= 94))
        s = await executor_setup(database, patch)
        try:
            assert await s.app.before_dispatch(s.t.create)
            s.runner.execute_action(s.t.create)
            await s.app.drain()
            child, = s.runner.active_executors['controller1']
            child._status = RunnableStatus.TERMINATED
            s.runner.store_executor(StoreExecutorAction(controller_id='controller1', executor_id=child.config.id))
            await s.runner.drain_repository_reporting()
            assert len(await s.app.stored_infos()) == 1
        finally:
            await close(s)
    name = 'recorder_executor_only_integrity'
    assert await c.fetchval("SELECT count(*) FROM life_financial.recorder_outbox WHERE stream LIKE 'executor:%'") == 3
    assert await c.fetchval('SELECT life_financial.' + name + '($1)', s.cfg.account_id)
    await reference(c, name)
    assert (await migrate(c, action='up'))['applied'] == ['000095_recorder_verification_reads.sql', '000096_recorder_history_lookups.sql', '000097_atomic_metadata_dispatch.sql', '000098_native_quote_capture.sql', '000099_provider_collection.sql']
    await compare(c, name, s.cfg.account_id, True)
    if fault != 'none':
        table, changes = {
            'stage': ('life_financial.operations', "receipt='{}'::jsonb WHERE command->>'kind'='RECORDER_STAGE'"),
            'hash': ('life_financial.recorder_outbox', "source_hash=repeat('0',64) WHERE stream LIKE 'executor:%'"),
            'receipt': ('life_financial.projection_receipts', "applied_hash=repeat('0',64)"),
            'transition': ('life_financial.recorder_outbox', "body=jsonb_set(body,'{command,expected_sequence}','99'::jsonb) WHERE sequence=2 AND stream LIKE 'executor:%'"),
            'native': ('life_recorder."Executors"', "status='1'"),
        }[fault]
        await damage(c, table, changes)
        await compare(c, name, s.cfg.account_id, False)
    assert (await migrate(c, action='up'))['applied'] == []
