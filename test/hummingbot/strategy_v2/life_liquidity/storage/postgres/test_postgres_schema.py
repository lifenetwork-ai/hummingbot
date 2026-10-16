"""Real constraints reject rounding, nonfinite money and cross-account data."""
from decimal import Decimal

import pytest

asyncpg = pytest.importorskip('asyncpg')


@pytest.mark.asyncio
async def test_migrations_install_empty_typed_families_and_replay(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import migrate
    c, _, _ = database
    tables = set(await c.fetch('SELECT tablename FROM pg_tables WHERE schemaname=\'life_financial\''))
    names = {row['tablename'] for row in tables}
    assert {'accounts', 'sessions', 'balances', 'reservations', 'send_attempts', 'operations', 'financial_events',
            'fills', 'fees', 'cashflows', 'budget_buckets', 'capital_claims', 'hedge_plans', 'inventory_campaigns',
            'joint_receipts', 'recorder_outbox', 'import_runs', 'audit_checkpoints'} <= names
    assert await c.fetchval('SELECT count(*) FROM life_financial.accounts') == 0
    assert (await migrate(c, action='up'))['applied'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('value', ['NaN', 'Infinity', '-Infinity', '-.01', '1e38', '0.0000000000000000001'])
async def test_money_domain_does_not_round_or_accept_nonfinite_or_out_of_range(database, value):
    c, _, _ = database
    with pytest.raises(asyncpg.CheckViolationError):
        await c.fetchval('SELECT $1::numeric::life_financial.amount', Decimal(value))


@pytest.mark.asyncio
async def test_exact_decimal_and_signed_fee_domains(database):
    c, _, _ = database
    value = Decimal('123456789012345678.123456789012345678')
    assert await c.fetchval('SELECT $1::numeric::life_financial.amount', value) == value
    assert await c.fetchval('SELECT $1::numeric::life_financial.signed_amount', Decimal('-.000000000000000001')) == Decimal('-.000000000000000001')


@pytest.mark.asyncio
async def test_runtime_and_reporter_roles_cannot_bypass_commands_schema_or_history(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import seed
    _, _, _, _, dsn = await seed(database)
    for role in ('life_pg2_runtime', 'life_pg2_reporter'):
        c = await asyncpg.connect(dsn.replace('life_pg2_runtime@', role + '@'))
        try:
            for sql in ("CREATE TABLE life_financial.unsafe(n int)", "DELETE FROM life_financial.operations",
                        "UPDATE life_financial.balances SET total=99,version=version+1",
                        "TRUNCATE life_financial.financial_events", "INSERT INTO life_financial.access_bindings VALUES(current_user,gen_random_uuid(),true)"):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await c.execute(sql)
            assert await c.fetchval('SELECT count(*) FROM life_financial.accounts') == 1
            if role == 'life_pg2_reporter':
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await c.execute("SELECT life_financial.allocate_balance(NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL)")
        finally:
            await c.close()


@pytest.mark.asyncio
async def test_unmapped_principal_cannot_read_another_account_or_allocate(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import seed
    account, _, _, _, dsn = await seed(database)
    c = await asyncpg.connect(dsn)
    try:
        await database[0].execute('DELETE FROM life_financial.access_bindings WHERE principal=$1', 'life_pg2_runtime')
        assert await c.fetchval('SELECT count(*) FROM life_financial.accounts') == 0
        assert await c.fetchval('SELECT count(*) FROM life_financial.balances') == 0
        with pytest.raises(asyncpg.RaiseError):
            await c.fetchval("SELECT life_financial.allocate_balance($1,NULL,NULL,0,'foreign',$2,'USDT',1)", account, 'a' * 64)
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_session_deadlines_primary_identity_and_row_versions_are_enforced(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import seed
    account, _, _, _, _ = await seed(database)
    c = database[0]
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
        await c.execute("UPDATE life_financial.sessions SET expires_at=expires_at+interval '1 hour'")
    with pytest.raises(asyncpg.SerializationError):
        await c.execute('UPDATE life_financial.balances SET reserved=1 WHERE account_id=$1', account)
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
        await c.execute("UPDATE life_financial.balances SET asset='OTHER',version=version+1 WHERE asset='USDT'")
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await c.execute("INSERT INTO life_financial.cashflows VALUES($1,'unapproved',1,1,$2)", account, 'a' * 64)
    with pytest.raises(asyncpg.CheckViolationError):
        await c.execute("UPDATE life_financial.balances SET reserved=11,version=version+1 WHERE asset='USDT'")


@pytest.mark.asyncio
async def test_wire_and_financial_identity_tombstones_and_attempt_transitions(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import backend

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import AllocateBalance
    store, cfg, owner = await backend(database)
    try:
        await store.allocate(AllocateBalance('allocated', 0, 'USDT', Decimal('1')), owner)
        c = database[0]
        await c.execute('''INSERT INTO life_financial.send_attempts(account_id,intent_id,operation_id,wire_id,product,side,session_id,
            process_id,generation,epoch,payload,payload_hash,quantity,price,state)
            VALUES($1,'intent1','allocated','wire1','SPOT','BUY',$2,$3,$4,1,'x',encode(sha256('x'::bytea),'hex'),1,1,'PREPARED')''',
                        cfg.account_id, owner.session_id, owner.process_id, owner.generation)
        with pytest.raises(asyncpg.RaiseError):
            await c.execute("UPDATE life_financial.send_attempts SET state='TERMINAL',version=1")
        await c.execute("UPDATE life_financial.send_attempts SET state='ABORTED_BEFORE_SEND',version=1")
        for sql in ("DELETE FROM life_financial.send_attempts", "UPDATE life_financial.operations SET receipt='{}'",
                    "UPDATE life_financial.send_attempts SET state='PREPARED',version=2"):
            with pytest.raises(asyncpg.PostgresError):
                await c.execute(sql)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_cost_counters_campaign_deadline_and_terminal_product_states_cannot_reset(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import seed
    account, _, _, _, _ = await seed(database)
    c = database[0]
    await c.execute("INSERT INTO life_financial.budget_buckets VALUES($1,'LOSS','CAMPAIGN','campaign',10,1,2,0)", account)
    with pytest.raises(asyncpg.RaiseError):
        await c.execute('UPDATE life_financial.budget_buckets SET incurred=1,version=1')
    # Actual irreversible losses remain recordable beyond a budget limit.
    await c.execute('UPDATE life_financial.budget_buckets SET incurred=11,version=1')
    await c.execute("INSERT INTO life_financial.inventory_campaigns VALUES($1,'inventory',clock_timestamp()+interval '1 hour',1,10,0)", account)
    with pytest.raises(asyncpg.RaiseError):
        await c.execute("UPDATE life_financial.inventory_campaigns SET deadline=deadline+interval '1 hour',version=1")
    await c.execute("INSERT INTO life_financial.joint_receipts VALUES($1,'joint',$2,'APPLYING',0)", account, 'a' * 64)
    await c.execute("UPDATE life_financial.joint_receipts SET state='APPLIED',version=1")
    with pytest.raises(asyncpg.RaiseError):
        await c.execute("UPDATE life_financial.joint_receipts SET state='APPLYING',version=2")


@pytest.mark.asyncio
async def test_cross_account_session_and_zero_identity_are_rejected(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import seed
    from uuid import UUID
    account, _, _, owner, _ = await seed(database)
    c = database[0]
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await c.execute("INSERT INTO life_financial.quote_actions VALUES($1,'foreign',$2,'BUY',0,'PROPOSED',0)", UUID(int=123), owner.session_id)
    with pytest.raises(asyncpg.CheckViolationError):
        await c.execute("INSERT INTO life_financial.quote_actions VALUES($1,'zero',$2,'BUY',0,'PROPOSED',0)", account, UUID(int=0))


@pytest.mark.asyncio
@pytest.mark.parametrize('family', ['fills', 'capital_claims'])
async def test_financial_rows_cannot_attach_swap_product_to_spot_attempt(database, family):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import backend

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import AllocateBalance
    store, cfg, owner = await backend(database)
    try:
        await store.allocate(AllocateBalance('origin', 0, 'USDT', Decimal('1')), owner)
        c = database[0]
        await c.execute('''INSERT INTO life_financial.send_attempts(account_id,intent_id,operation_id,wire_id,product,side,session_id,
            process_id,generation,epoch,payload,payload_hash,quantity,price,state)
            VALUES($1,'intent','origin','wire','SPOT','BUY',$2,$3,$4,1,'x',encode(sha256('x'::bytea),'hex'),1,1,'PREPARED')''',
                        cfg.account_id, owner.session_id, owner.process_id, owner.generation)
        sql = ("INSERT INTO life_financial.fills VALUES($1,'SWAP','trade','wire',1,1,1,clock_timestamp(),clock_timestamp(),$2)"
               if family == 'fills' else
               "INSERT INTO life_financial.capital_claims VALUES($1,'claim','intent','SWAP',1,1,1,'HELD',0)")
        args = (cfg.account_id, 'a' * 64) if family == 'fills' else (cfg.account_id,)
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await c.execute(sql, *args)
        # The exact same command with its original product is valid.
        await c.execute(sql.replace("'SWAP'", "'SPOT'"), *args)
    finally:
        await store.close()
