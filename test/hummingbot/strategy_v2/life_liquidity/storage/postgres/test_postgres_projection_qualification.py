"""Promotion prerequisites over native commands, not an optimized runtime."""
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres import projection_contract
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_fill_projection import install, ready
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import filled

import asyncpg
import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


@pytest.mark.asyncio
async def test_projection_zero_fee_buffers_require_only_the_original_principal(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order, spot
    c = database[0]
    await install(c, economics=True)
    repo, cfg, owner = await spot(database)
    try:
        await repo.prepare(order(owner, fee_buffer_base_life=D(0), fee_buffer_quote_usdt=D(0)),
                           cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert await c.fetchval('SELECT count(*) FROM life_financial.reservations') == 1
        assert await c.fetchval('SELECT count(*) FROM fill_projection_spike.hold_proof') == 1
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('hold', ['principal', 'fee:LIFE', 'fee:USDT'])
async def test_projection_requires_original_nonzero_hold_even_if_native_verifier_is_faulty(database, hold):
    repo, cfg, owner = await ready(database, economics=True)
    c = database[0]
    try:
        # Previously the hold and its witness could both be removed. The
        # immutable order basis must independently require their existence.
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            async with c.transaction():
                await c.execute('ALTER TABLE life_financial.reservations DISABLE TRIGGER USER')
                await c.execute('DELETE FROM fill_projection_spike.hold_proof WHERE reservation_id=$1', 'operation1:' + hold)
                await c.execute('DELETE FROM life_financial.reservations WHERE reservation_id=$1', 'operation1:' + hold)
        assert await c.fetchval('SELECT count(*) FROM life_financial.reservations') == 3
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('table', ['fills', 'fees'])
async def test_projection_rejects_unwitnessed_raw_spot_rows_without_user_triggers(database, table):
    repo, cfg, owner = await ready(database, economics=True)
    c = database[0]
    try:
        await repo.apply_fills(filled(owner, await c.fetchval('SELECT clock_timestamp()')),
                               cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            async with c.transaction():
                await c.execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
                if table == 'fills':
                    await c.execute('''INSERT INTO life_financial.fills(account_id,product,trade_id,wire_id,quantity,price,
                        independent_price,fill_at,value_at,source_hash)
                        SELECT account_id,product,'orphan',wire_id,quantity,price,independent_price,fill_at,value_at,source_hash
                        FROM life_financial.fills''')
                else:
                    await c.execute('''INSERT INTO life_financial.fees(account_id,payment_id,product,trade_id,asset,signed_fee)
                        SELECT account_id,'extra-fee',product,trade_id,asset,signed_fee FROM life_financial.fees''')
        assert await c.fetchval(f'SELECT count(*) FROM life_financial.{table}') == 1
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['constraint', 'not_valid', 'trigger', 'internal_trigger', 'function', 'acl', 'role'])
async def test_projection_enrollment_detects_changed_enforcement_and_privileges(database, fault):
    c = database[0]
    await install(c, economics=True)
    async with c.transaction(isolation='repeatable_read', readonly=True):
        expected = await projection_contract.catalog(c)
        await projection_contract.verify(c, expected)
    changes = {
        'constraint': 'ALTER TABLE life_financial.financial_events DROP CONSTRAINT prototype_money_complete',
        'not_valid': '''ALTER TABLE life_financial.financial_events DROP CONSTRAINT prototype_money_complete;
            ALTER TABLE life_financial.financial_events ADD CONSTRAINT prototype_money_complete
            FOREIGN KEY(account_id,event_id) REFERENCES fill_projection_spike.money(account_id,event_id)
            DEFERRABLE INITIALLY DEFERRED NOT VALID''',
        'trigger': 'ALTER TABLE life_financial.financial_events DISABLE TRIGGER prototype_money',
        'internal_trigger': 'ALTER TABLE life_financial.financial_events DISABLE TRIGGER ALL',
        'function': '''CREATE OR REPLACE FUNCTION fill_projection_spike.record_money() RETURNS trigger
            LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN RETURN NULL; END; $$''',
        'acl': 'GRANT SELECT ON fill_projection_spike.money TO life_pg2_runtime',
        'role': 'GRANT life_pg2_migrator TO life_pg2_runtime',
    }
    admin = None
    try:
        if fault in ('role', 'internal_trigger'):
            admin = await asyncpg.connect(database[1])
            await admin.execute(changes[fault])
        else:
            await c.execute(changes[fault])
        async with c.transaction(isolation='repeatable_read', readonly=True):
            with pytest.raises(StorageError, match='^PROTOTYPE_CATALOG_DRIFT$'):
                await projection_contract.verify(c, expected)
    finally:
        if admin is not None:
            if fault == 'role':
                await admin.execute('REVOKE life_pg2_migrator FROM life_pg2_runtime')
            await admin.close()


@pytest.mark.asyncio
async def test_projection_installer_refuses_existing_history_atomically(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order, spot
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import ack
    repo, cfg, owner = await spot(database)
    c = database[0]
    try:
        await repo.prepare(order(owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await repo.arm('operation1', cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await repo.acknowledge(ack(owner, await c.fetchval('SELECT clock_timestamp()')),
                               cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(asyncpg.RaiseError, match='PROTOTYPE_REQUIRES_EMPTY_EVENTS'):
            await install(c, economics=True)
        assert await c.fetchval("SELECT to_regnamespace('fill_projection_spike')") is None
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_projection_late_enrollment_failure_leaves_no_partial_base_schema(database, monkeypatch, tmp_path):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres import test_postgres_fill_projection as prototype
    broken = tmp_path / 'broken.sql'
    broken.write_text("-- Intentional failed final component.\nBEGIN;\nDO $$ BEGIN RAISE EXCEPTION 'TEST_ENROLLMENT_FAILURE'; END; $$;\nCOMMIT;")
    monkeypatch.setattr(prototype, 'COMPLETENESS', broken)
    c = database[0]
    with pytest.raises(asyncpg.RaiseError, match='TEST_ENROLLMENT_FAILURE'):
        await install(c, economics=True)
    assert await c.fetchval("SELECT to_regnamespace('fill_projection_spike')") is None
    assert not await c.fetchval("SELECT EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid='life_financial.operations'::regclass AND attname='prototype_receipt_hash')")
    assert not await c.fetchval("SELECT EXISTS(SELECT 1 FROM pg_constraint WHERE conname='prototype_money_complete')")


@pytest.mark.asyncio
@pytest.mark.parametrize('capital', [False, True])
async def test_projection_native_body_is_exactly_equal_after_removing_test_metadata(database, capital):
    repo, cfg, owner = await ready(database, economics=True)
    c = database[0]
    try:
        if capital:
            await c.execute("INSERT INTO life_financial.spot_capital_policies VALUES($1,$2,5,10,1,$3,'100',4096)",
                            cfg.account_id, cfg.binding.policy_hash, owner.valid_from)
        # Explicit privileged test-reader scope; do not weaken can_access.
        await c.execute('INSERT INTO life_financial.access_bindings VALUES(current_user,$1,false)', cfg.account_id)
        await repo.apply_fills(filled(owner, await c.fetchval('SELECT clock_timestamp()')),
                               cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        original_cut = (await repo.snapshot(cfg.binding)).cut
        enrolled = await c.fetchval('SELECT life_financial.spot_body($1)::text', cfg.account_id)
        # Same original rows, same account and clock-independent body. Remove
        # only experimental enforcement, then compare the unchanged native
        # calculation. No fields are stripped/normalized from either body.
        async with c.transaction():
            await c.execute('DROP SCHEMA fill_projection_spike CASCADE')
            for table, column in [('operations', 'prototype_receipt_hash'), ('spot_preparations', 'prototype_request_hash'),
                                  ('fills', 'prototype_spot_trade'), ('fees', 'prototype_spot_payment')]:
                await c.execute(f'ALTER TABLE life_financial.{table} DROP COLUMN {column} CASCADE')
            assert await c.fetchval('SELECT life_financial.spot_body($1)::text', cfg.account_id) == enrolled
        assert (await repo.snapshot(cfg.binding)).cut == original_cut
    finally:
        await repo.close()
