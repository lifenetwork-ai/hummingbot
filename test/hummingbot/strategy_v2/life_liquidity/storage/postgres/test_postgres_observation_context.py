"""One bounded primary financial context for provider reconciliation."""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_capital import capital_store, flow
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import ack, filled

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.financial import RecordedSpotFill
from hummingbot.strategy_v2.life_liquidity.storage.postgres.observation_context import PgSpotObservationReader
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository


@pytest.mark.asyncio
async def test_context_preserves_original_commands_fill_prices_signed_fees_and_approved_cashflows(database):
    repo, cfg, owner, at, ms = await capital_store(database)
    try:
        reader = PgSpotObservationReader(repo, max_rows=64, max_bytes=1048576)
        initial = await reader.observation_context(cfg.binding)
        assert initial.commands == initial.fills == initial.cashflows == initial.pending == ()
        assert initial.policy.opening_base_life == 5 and initial.policy.opening_quote_usdt == 10
        assert initial.policy.approved_cashflows[0].signed_amount == 2
        command = order(owner)
        await repo.prepare(command, cut=initial.cut, owner=owner)
        await repo.arm(command.operation_id, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        observed = await repo.observation_cut(cfg.binding)
        fill = filled(owner, observed.observed_at)
        await repo.apply_fills(fill, cut=observed.cut, owner=owner)
        await repo.acknowledge(ack(owner, observed.observed_at), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        cashflow = flow(at, ms)
        await repo.apply_cashflow(cashflow, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        context = await reader.observation_context(cfg.binding)
        assert context.commands == (command,)
        assert context.fills == (RecordedSpotFill(command.operation_id, command.intent_id, fill.exchange_order_id, fill.fills[0]),)
        assert context.cashflows == (cashflow,)
        assert context.pending == context.recovery_batches == ()
        assert context.cut == (await repo.snapshot(cfg.binding)).cut
        assert context.fill_policy.max_reference_skew_ms == 1000
        assert context.fills[0].fill.signed_fee == D('-.02')
        readonly = PgSpotRepository(await PgRuntime.open(replace(cfg, dsn=cfg.dsn.replace('life_pg2_runtime@', 'life_pg2_reporter@'),
                                                                 principal='life_pg2_reporter', write=False)))
        try:
            assert await PgSpotObservationReader(readonly, max_rows=64, max_bytes=1048576).observation_context(cfg.binding) == context
        finally:
            await readonly.close()
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('bound', ['rows', 'bytes'])
async def test_context_enforces_explicit_read_bounds_instead_of_returning_partial_scope(database, bound):
    repo, cfg, owner, at, ms = await capital_store(database)
    try:
        reader = PgSpotObservationReader(repo, max_rows=1 if bound == 'rows' else 64, max_bytes=8 if bound == 'bytes' else 1048576)
        with pytest.raises(StorageError, match='PG_SPOT_CONTEXT_READ_BOUND'):
            await reader.observation_context(cfg.binding)
        assert not (await repo.snapshot(cfg.binding)).recovery_required
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_context_does_not_silently_hide_pending_cashflow_or_reconstruct_its_original_value(database):
    repo, cfg, owner, at, ms = await capital_store(database, (('101', 'USDT', '-11'),))
    try:
        result = await repo.apply_cashflow(flow(at, ms, signed_amount=D('-11')), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert result.state == 'RECOVERY_REQUIRED'
        with pytest.raises(StorageError, match='PG_SPOT_CONTEXT_UNVERIFIED'):
            await PgSpotObservationReader(repo, max_rows=64, max_bytes=1048576).observation_context(cfg.binding)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_cashflow_facts') == 1
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_context_never_coerces_provider_money_through_binary_float(database):
    repo, cfg, owner, at, ms = await capital_store(database, (('101', 'USDT', '2.000000000000000001'),))
    try:
        context = await PgSpotObservationReader(repo, max_rows=64, max_bytes=1048576).observation_context(cfg.binding)
        assert context.policy.approved_cashflows[0].signed_amount == D('2.000000000000000001')
        assert isinstance(context.policy.approved_cashflows[0].signed_amount, D)
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_context_and_cut_use_one_statement_snapshot_during_concurrent_cashflow_commit(database):
    import asyncio
    repo, cfg, owner, at, ms = await capital_store(database)
    writer = PgSpotRepository(await PgRuntime.open(cfg))
    reader = PgSpotObservationReader(repo, max_rows=64, max_bytes=1048576)
    admin = database[0]
    task = None
    lock = 7400322
    try:
        before = await reader.observation_context(cfg.binding)
        await admin.execute('''ALTER FUNCTION life_financial.spot_observation_context(uuid,integer,integer)
            RENAME TO test_context_original;
            REVOKE ALL ON FUNCTION life_financial.test_context_original(uuid,integer,integer) FROM PUBLIC,life_pg2_runtime,life_pg2_reporter;
            CREATE FUNCTION life_financial.spot_observation_context(p_account uuid,p_rows integer,p_bytes integer) RETURNS jsonb
                LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $f$
                BEGIN PERFORM pg_advisory_xact_lock(7400322);
                    RETURN life_financial.test_context_original(p_account,p_rows,p_bytes); END; $f$;
            REVOKE ALL ON FUNCTION life_financial.spot_observation_context(uuid,integer,integer) FROM PUBLIC;
            GRANT EXECUTE ON FUNCTION life_financial.spot_observation_context(uuid,integer,integer) TO life_pg2_runtime,life_pg2_reporter''')
        await admin.execute('SELECT pg_advisory_lock($1)', lock)
        task = asyncio.create_task(reader.observation_context(cfg.binding))

        async def blocked():
            while not await admin.fetchval('SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype=\'advisory\' AND objid=$1 AND NOT granted)', lock):
                await asyncio.sleep(.01)
        await asyncio.wait_for(blocked(), 2)
        await writer.apply_cashflow(flow(at, ms), cut=(await writer.snapshot(cfg.binding)).cut, owner=owner)
        await admin.execute('SELECT pg_advisory_unlock($1)', lock)
        observed = await asyncio.wait_for(task, 2)
        assert observed == before
        after = await reader.observation_context(cfg.binding)
        assert after.cut != before.cut and after.cashflows == (flow(at, ms),)
        assert (await writer.balance(cfg.binding)).total_quote_usdt == 12
    finally:
        await admin.execute('SELECT pg_advisory_unlock_all()')
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await writer.close()
        await repo.close()


@pytest.mark.asyncio
async def test_native_context_reader_denies_foreign_scope_and_corrupt_original_provenance(database):
    from uuid import uuid4

    import asyncpg
    repo, cfg, owner, at, ms = await capital_store(database)
    c = await asyncpg.connect(cfg.dsn)
    try:
        with pytest.raises(asyncpg.RaiseError, match='LIFE_ACCOUNT_SCOPE_INVALID'):
            await c.fetchval('SELECT life_financial.spot_observation_context($1,$2,$3)', uuid4(), 64, 1048576)
        await repo.prepare(order(owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await database[0].execute('ALTER TABLE life_financial.spot_preparations DISABLE TRIGGER USER')
        await database[0].execute('UPDATE life_financial.spot_preparations SET fee_quote=fee_quote+1')
        await database[0].execute('ALTER TABLE life_financial.spot_preparations ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='PG_SPOT_CONTEXT_UNVERIFIED'):
            await PgSpotObservationReader(repo, max_rows=64, max_bytes=1048576).observation_context(cfg.binding)
    finally:
        await c.close()
        await repo.close()


@pytest.mark.asyncio
async def test_runtime_pins_utc_for_primary_json_timestamps_despite_inherited_role_timezone(database):
    from datetime import timedelta

    import asyncpg
    repo, cfg, owner, at, ms = await capital_store(database)
    await repo.close()
    admin = await asyncpg.connect(database[1])
    cold = None
    try:
        await admin.execute("ALTER ROLE life_pg2_runtime SET timezone='Asia/Ho_Chi_Minh'")
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        context = await PgSpotObservationReader(cold, max_rows=64, max_bytes=1048576).observation_context(cfg.binding)
        assert context.policy.opened_at.utcoffset() == timedelta(0)
        assert (await cold.snapshot(cfg.binding)).session.started_at.utcoffset() == timedelta(0)
    finally:
        await admin.execute('ALTER ROLE life_pg2_runtime RESET timezone')
        if cold is not None:
            await cold.close()
        await admin.close()


@pytest.mark.asyncio
async def test_primary_connection_timezone_mutation_is_rejected_before_use(database):
    repo, cfg, owner, at, ms = await capital_store(database)
    try:
        async with repo._runtime._pools[True].acquire() as c:
            await c.execute("SET timezone='Asia/Ho_Chi_Minh'")
            with pytest.raises(StorageError, match='PG_PRIMARY_CONTEXT_INVALID'):
                await repo._runtime._validate_connection(c)
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_context_retains_unapplied_raw_ack_with_original_owner_and_primary_timestamp(database, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_ack_ingress import pending_ack

    from hummingbot.strategy_v2.life_liquidity.storage.pending import PendingSpotCommand
    t, received = await pending_ack(database, monkeypatch)
    try:
        # Explicit privileged synthetic policy import; received ACK diagnostics
        # do not create opening-capital or independent-value policies.
        await database[0].execute('INSERT INTO life_financial.spot_capital_policies VALUES($1,$2,5,10,1,$3,\'100\',4096)',
                                  t.cfg.account_id, t.cfg.binding.policy_hash, t.owner.valid_from)
        await database[0].execute('INSERT INTO life_financial.spot_fill_policies VALUES($1,$2,1000,4096)',
                                  t.cfg.account_id, t.cfg.binding.policy_hash)
        context = await PgSpotObservationReader(t.repo, max_rows=64, max_bytes=1048576).observation_context(t.cfg.binding)
        assert context.pending == (PendingSpotCommand('ACK', received.command, received.owner),)
        assert context.commands == (t.preparation.prepare,)
        assert context.cut == (await t.repo.snapshot(t.cfg.binding)).cut
        assert len(t.session.sent) == 1
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_missing_retired_preparation_cannot_disappear_from_complete_context_or_current_cut(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_lifecycle import lifecycle
    repo, cfg, owner, at, ms = await capital_store(database, ())
    try:
        await repo.prepare(order(owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await repo.apply_lifecycle(lifecycle(), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert not (await repo.snapshot(cfg.binding)).recovery_required
        await database[0].execute('ALTER TABLE life_financial.spot_preparations DISABLE TRIGGER USER')
        await database[0].execute('DELETE FROM life_financial.spot_preparations')
        await database[0].execute('ALTER TABLE life_financial.spot_preparations ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='PG_SPOT_CONTEXT_UNVERIFIED'):
            await PgSpotObservationReader(repo, max_rows=64, max_bytes=1048576).observation_context(cfg.binding)
        view = await repo.snapshot(cfg.binding)
        assert view.recovery_required
        with pytest.raises(StorageError, match='PG_SPOT_RECOVERY_REQUIRED'):
            await repo.prepare(order(owner, operation_id='new-operation', intent_id='new-intent', client_order_id='wire2'),
                               cut=view.cut, owner=owner)
        assert await database[0].fetchval("SELECT count(*) FROM life_financial.send_attempts WHERE intent_id='new-intent'") == 0
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_pending_fill_requires_resolution_instead_of_vanishing_from_context(database):
    repo, cfg, owner, at, ms = await capital_store(database, ())
    try:
        await repo.prepare(order(owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await repo.arm('operation1', cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        observed = await repo.observation_cut(cfg.binding)
        original = filled(owner, observed.observed_at)
        command = replace(original, fills=(replace(original.fills[0], signed_fee=D('-100')),))
        receipt = await repo.apply_fills(command, cut=observed.cut, owner=owner)
        assert receipt.state == 'RECOVERY_REQUIRED'
        with pytest.raises(StorageError, match='PG_SPOT_CONTEXT_PENDING_RESOLUTION_REQUIRED'):
            await PgSpotObservationReader(repo, max_rows=64, max_bytes=1048576).observation_context(cfg.binding)
        assert (await repo.balance(cfg.binding)).total_quote_usdt == 10
        assert (await repo.balance(cfg.binding)).reserved_quote_usdt > 0
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_context_snapshot_arithmetic_is_independent_of_ambient_decimal_precision(database):
    from decimal import localcontext
    repo, cfg, owner, at, ms = await capital_store(database, (('101', 'USDT', '2.000000000000000001'),))
    try:
        reader = PgSpotObservationReader(repo, max_rows=64, max_bytes=1048576)
        exact = await reader.observation_context(cfg.binding)
        with localcontext() as precision:
            precision.prec = 2
            assert await reader.observation_context(cfg.binding) == exact
    finally:
        await repo.close()
