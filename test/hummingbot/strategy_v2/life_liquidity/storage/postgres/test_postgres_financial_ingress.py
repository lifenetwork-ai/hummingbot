"""Original received financial values persist before accounting, without grants."""
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_capital import capital_store, flow
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import ack, filled

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, StorageUnavailable
from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_ingress import PgFinancialIngress


async def setup(database, kind='fill'):
    repo, cfg, owner, at, ms = await capital_store(database)
    await repo.prepare(order(owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    await repo.arm('operation1', cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    observed = await repo.observation_cut(cfg.binding)
    await repo.acknowledge(ack(owner, observed.observed_at), cut=observed.cut, owner=owner)
    command = filled(owner, observed.observed_at) if kind == 'fill' else flow(at, ms)
    return repo, cfg, owner, command, PgFinancialIngress(repo)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['fill', 'cashflow'])
async def test_received_values_change_no_money_then_apply_exactly_once_without_releasing_holds(database, kind):
    repo, cfg, owner, command, intake = await setup(database, kind)
    try:
        before = await repo.snapshot(cfg.binding)
        received = await intake.capture(command, owner=owner)
        captured = await repo.snapshot(cfg.binding)
        assert received.command == command and not received.replayed and not received.trading_permission
        assert received.capture_sequence > 0 and received.applied_operation_id is None
        assert captured.balance == before.balance and captured.budget == before.budget
        assert captured.cut.checkpoint.revision == before.cut.checkpoint.revision and captured.cut != before.cut
        assert not before.recovery_required and captured.recovery_required
        result = await intake.capture_and_apply(command, owner=owner, max_rows=64, max_bytes=1048576)
        assert result.state == 'APPLIED'
        after = await repo.snapshot(cfg.binding)
        replay = await intake.capture_and_apply(command, owner=owner, max_rows=64, max_bytes=1048576)
        assert replay.replayed and (await repo.snapshot(cfg.binding)).cut == after.cut
        assert after.balance.reserved_quote_usdt > 0 and not after.recovery_required
        assert await intake.pending(cfg.binding, max_rows=64, max_bytes=1048576) == ()
        assert (await intake.lookup(received.ingress_id)).applied_operation_id == command.operation_id
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['fill', 'cashflow'])
async def test_financial_application_failure_retains_original_chosen_values_and_denies_allocation(database, monkeypatch, kind):
    import json

    import asyncpg
    repo, cfg, owner, command, intake = await setup(database, kind)
    c = await asyncpg.connect(cfg.dsn)

    async def failed(*args, **kwargs):
        raise StorageUnavailable('PG_SYNTHETIC_FINANCIAL_APPLICATION_FAILURE')
    monkeypatch.setattr(repo, 'apply_fills' if kind == 'fill' else 'apply_cashflow', failed)
    try:
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='PG_SYNTHETIC_FINANCIAL_APPLICATION_FAILURE'):
            await intake.capture_and_apply(command, owner=owner, max_rows=64, max_bytes=1048576)
        pending = await intake.pending(cfg.binding, max_rows=64, max_bytes=1048576)
        assert len(pending) == 1 and pending[0].command == command and pending[0].owner == owner
        assert not repo._runtime.healthy and (await repo.snapshot(cfg.binding)).balance == before.balance
        with pytest.raises(asyncpg.RaiseError, match='LIFE_FINANCIAL_INGRESS_RECOVERY_REQUIRED'):
            await c.fetchval('SELECT life_financial.allocate_balance($1,$2::jsonb,$3::jsonb,$4,$5,$6,$7,$8)', cfg.account_id,
                             json.dumps(cfg.binding.json()), json.dumps(owner.json()), before.cut.checkpoint.revision,
                             'allocation-after-pending-intake', 'f' * 64, 'USDT', 1)
    finally:
        await c.close()
        await repo.close()


@pytest.mark.asyncio
async def test_newer_complete_financial_receipt_can_cover_older_received_snapshot_without_repricing(database):
    repo, cfg, owner, first, intake = await setup(database)
    try:
        old = await intake.capture(first, owner=owner)
        next_fill = replace(first.fills[0], trade_id='trade2')
        newer = replace(first, operation_id='fill2', cumulative_base_life=first.cumulative_base_life * 2, fills=(*first.fills, next_fill))
        await intake.capture_and_apply(newer, owner=owner, max_rows=64, max_bytes=1048576)
        with pytest.raises(StorageError, match='PG_FILL_CUMULATIVE_INVALID'):
            await repo.apply_fills(first, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        marked = await intake.mark_applied(old, financial_operation_id=newer.operation_id, owner=owner)
        assert marked.applied_operation_id == newer.operation_id and not marked.trading_permission
        assert await intake.pending(cfg.binding, max_rows=64, max_bytes=1048576) == ()
        assert (await repo.balance(cfg.binding)).total_base_life == 7
        before_replay = await repo.snapshot(cfg.binding)
        replay = await intake.capture_and_apply(first, owner=owner, max_rows=64, max_bytes=1048576)
        assert replay.replayed and replay.operation_id == newer.operation_id
        assert (await repo.snapshot(cfg.binding)).cut == before_replay.cut
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_changed_pending_reference_value_cannot_replace_the_first_captured_choice(database):
    from decimal import Decimal
    repo, cfg, owner, first, intake = await setup(database)
    try:
        await intake.capture(first, owner=owner)
        changed = replace(first, operation_id='revalued', fills=(replace(first.fills[0], independent_price_usdt=Decimal('.8')),))
        with pytest.raises(StorageError, match='PG_FINANCIAL_INGRESS_VALUE_CONFLICT'):
            await intake.capture_and_apply(changed, owner=owner, max_rows=64, max_bytes=1048576)
        pending = await intake.pending(cfg.binding, max_rows=64, max_bytes=1048576)
        assert len(pending) == 2 and pending[0].command == first
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.fills') == 0
        assert (await repo.balance(cfg.binding)).total_quote_usdt == 10
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_missing_unapplied_intake_is_detected_even_when_no_financial_operation_exists(database):
    repo, cfg, owner, command, intake = await setup(database)
    try:
        await intake.capture(command, owner=owner)
        await database[0].execute('ALTER TABLE life_financial.spot_financial_ingress DISABLE TRIGGER USER')
        await database[0].execute('DELETE FROM life_financial.spot_financial_ingress')
        await database[0].execute('ALTER TABLE life_financial.spot_financial_ingress ENABLE TRIGGER USER')
        assert (await repo.snapshot(cfg.binding)).recovery_required
        with pytest.raises(StorageError, match='PG_FINANCIAL_INGRESS_UNVERIFIED'):
            await intake.pending(cfg.binding, max_rows=64, max_bytes=1048576)
        assert (await repo.balance(cfg.binding)).total_quote_usdt == 10
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['fill', 'cashflow'])
async def test_context_preserves_the_pending_original_financial_command_and_owner(database, kind):
    from hummingbot.strategy_v2.life_liquidity.storage.pending import PendingSpotCommand
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.observation_context import PgSpotObservationReader
    repo, cfg, owner, command, intake = await setup(database, kind)
    try:
        await intake.capture(command, owner=owner)
        context = await PgSpotObservationReader(repo, max_rows=64, max_bytes=1048576).observation_context(cfg.binding)
        assert context.pending == (PendingSpotCommand('FILL' if kind == 'fill' else 'CASHFLOW', command, owner),)
        assert context.cut == (await repo.snapshot(cfg.binding)).cut
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_context_does_not_adopt_an_untrusted_pending_binding_as_the_current_binding(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.observation_context import PgSpotObservationReader
    repo, cfg, owner, command, intake = await setup(database)
    try:
        body = {'kind': 'SPOT_FILL', 'binding': replace(cfg.binding, policy_hash='c' * 64).json(),
                'owner': owner.json(), 'command': command.json()}
        await repo._runtime._transaction('SELECT life_financial.spot_capture_financial_ingress($1,$2)', (cfg.account_id, body),
                                         owner, intake._receipt, observation=True, untrusted_ingress=True)
        with pytest.raises(StorageError, match='PG_SPOT_CONTEXT_PENDING_ORIGIN_UNQUALIFIED'):
            await PgSpotObservationReader(repo, max_rows=64, max_bytes=1048576).observation_context(cfg.binding)
        assert (await repo.balance(cfg.binding)).total_quote_usdt == 10
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['fill', 'cashflow'])
@pytest.mark.parametrize('phase', ['capture', 'apply', 'delivery'])
@pytest.mark.parametrize('committed', [False, True])
@pytest.mark.parametrize('fault', ['backend', 'process'])
async def test_real_commit_faults_preserve_original_values_then_recover_without_double_accounting(database, monkeypatch, kind, phase, committed, fault):
    import hashlib
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_ingress_process import (
        exit_financial_ingress,
    )

    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner, command, intake = await setup(database, kind)
    admin = await asyncpg.connect(database[1])
    cold = None
    received = None
    tag = 'SPOT_FILL' if kind == 'fill' else 'SPOT_CASHFLOW'
    ingress_id = 'rawfinancial:' + hashlib.sha256((tag + ':' + command.operation_id).encode()).hexdigest()
    original_commit = repo._runtime._commit
    try:
        if phase != 'capture':
            received = await intake.capture(command, owner=owner)
        if phase == 'delivery':
            await getattr(repo, 'apply_fills' if kind == 'fill' else 'apply_cashflow')(
                command, cut=(await repo.observation_cut(cfg.binding)).cut, owner=owner)
        if fault == 'process':
            await exit_financial_ingress(cfg, command, owner, ingress_id, kind, phase, committed)
        else:
            async def lost(tx):
                pid = tx._connection.get_server_pid()
                if committed:
                    await original_commit(tx)
                assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid)
                raise ConnectionError('synthetic financial intake COMMIT reply loss')
            monkeypatch.setattr(repo._runtime, '_commit', lost)
            with pytest.raises(CommitUnknown, match='PG_COMMIT_UNKNOWN'):
                if phase == 'capture':
                    await intake.capture(command, owner=owner)
                elif phase == 'apply':
                    await getattr(repo, 'apply_fills' if kind == 'fill' else 'apply_cashflow')(
                        command, cut=(await repo.observation_cut(cfg.binding)).cut, owner=owner)
                else:
                    await intake.mark_applied(received, financial_operation_id=command.operation_id, owner=owner)
        applied = phase == 'delivery' or phase == 'apply' and committed
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_financial_ingress') == (int(committed) if phase == 'capture' else 1)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_financial_ingress_receipts') == (int(committed) if phase == 'delivery' else 0)
        table = 'fills' if kind == 'fill' else 'cashflows'
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.' + table) == int(applied)
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        recovery = PgFinancialIngress(cold)
        original = await recovery.lookup(ingress_id)
        if original is not None:
            assert original.command == command and original.owner == owner and not original.trading_permission
        await recovery.capture_and_apply(command, owner=owner, max_rows=64, max_bytes=1048576)
        before_replay = await cold.snapshot(cfg.binding)
        await recovery.capture_and_apply(command, owner=owner, max_rows=64, max_bytes=1048576)
        assert (await cold.snapshot(cfg.binding)).cut == before_replay.cut
        assert await recovery.pending(cfg.binding, max_rows=64, max_bytes=1048576) == ()
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.' + table) == 1
        assert (await cold.balance(cfg.binding)).reserved_quote_usdt > 0
        progress = await database[0].fetchrow('SELECT * FROM life_financial.spot_ingress_progress')
        assert progress['financial_captured'] == progress['financial_delivered'] == 1 and progress['version'] == 2
    finally:
        if cold is not None:
            await cold.close()
        await admin.close()
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('fee_currency', ['LIFE', 'USDT'])
@pytest.mark.parametrize('signed_fee', ['-.02', '.01'])
async def test_intake_preserves_sides_and_signed_fee_units_without_refunding_earlier_costs(database, side, fee_currency, signed_fee):
    from decimal import Decimal
    repo, cfg, owner, at, ms = await capital_store(database, ())
    try:
        await repo.prepare(order(owner, side=side), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await repo.arm('operation1', cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        observed = await repo.observation_cut(cfg.binding)
        await repo.acknowledge(ack(owner, observed.observed_at, side=side), cut=observed.cut, owner=owner)
        base = filled(owner, observed.observed_at, side=side)
        command = replace(base, fills=(replace(base.fills[0], fee_currency=fee_currency, signed_fee=Decimal(signed_fee)),))
        result = await PgFinancialIngress(repo).capture_and_apply(command, owner=owner, max_rows=64, max_bytes=1048576)
        assert result.state == 'APPLIED'
        value = await database[0].fetchval('SELECT observation FROM life_financial.spot_fill_facts')
        import json
        assert json.loads(value) == command.fills[0].json()
        assert (await repo.balance(cfg.binding)).reserved_base_life > 0
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_untrusted_intake_survives_unrelated_financial_corruption_but_cannot_apply_it(database):
    repo, cfg, owner, command, intake = await setup(database)
    try:
        await database[0].execute('ALTER TABLE life_financial.spot_preparations DISABLE TRIGGER USER')
        await database[0].execute('UPDATE life_financial.spot_preparations SET fee_quote=fee_quote+1')
        await database[0].execute('ALTER TABLE life_financial.spot_preparations ENABLE TRIGGER USER')
        received = await intake.capture(command, owner=owner)
        assert (await intake.lookup(received.ingress_id)).command == command
        with pytest.raises(StorageError, match='PG_ACCOUNTING_PROJECTION_UNVERIFIED'):
            await intake.capture_and_apply(command, owner=owner, max_rows=64, max_bytes=1048576)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.fills') == 0
        assert len(await intake.pending(cfg.binding, max_rows=64, max_bytes=1048576)) == 1
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_readonly_intake_is_scoped_bounded_and_cannot_call_private_progress_writes(database):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner, command, intake = await setup(database)
    readonly = None
    c = None
    try:
        received = await intake.capture(command, owner=owner)
        readonly = PgSpotRepository(await PgRuntime.open(replace(cfg, dsn=cfg.dsn.replace('life_pg2_runtime@', 'life_pg2_reporter@'),
                                                                 principal='life_pg2_reporter', write=False)))
        reader = PgFinancialIngress(readonly)
        assert (await reader.lookup(received.ingress_id)).command == command
        with pytest.raises(StorageError, match='PG_FINANCIAL_INGRESS_READ_BOUND$'):
            await reader.pending(cfg.binding, max_rows=64, max_bytes=8)
        with pytest.raises(StorageError, match='PG_FINANCIAL_INGRESS_WRITE_REQUIRED'):
            await reader.capture(command, owner=owner)
        with pytest.raises(StorageError, match='PG_FINANCIAL_INGRESS_WRITE_REQUIRED'):
            await reader.mark_applied(received, financial_operation_id=command.operation_id, owner=owner)
        c = await asyncpg.connect(cfg.dsn)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await c.fetchval("SELECT life_financial.spot_ingress_increment($1,'FINANCIAL','CAPTURE')", cfg.account_id)
        from uuid import uuid4
        with pytest.raises(asyncpg.RaiseError, match='LIFE_ACCOUNT_SCOPE_INVALID'):
            await c.fetchval('SELECT life_financial.spot_read_financial_ingress($1,$2)', uuid4(), received.ingress_id)
    finally:
        if c is not None:
            await c.close()
        if readonly is not None:
            await readonly.close()
        await repo.close()


@pytest.mark.asyncio
async def test_intake_metadata_hash_is_independent_of_auditor_connection_timezone(database):
    repo, cfg, owner, command, intake = await setup(database)
    try:
        await intake.capture(command, owner=owner)
        await database[0].execute("SET timezone='Asia/Ho_Chi_Minh'")
        assert await database[0].fetchval('SELECT life_financial.spot_financial_ingress_verified($1)', cfg.account_id)
    finally:
        await database[0].execute("SET timezone='UTC'")
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['body', 'capture_sequence', 'captured_at', 'delivery'])
async def test_changed_intake_or_delivery_provenance_denies_cold_readback(database, fault):
    repo, cfg, owner, command, intake = await setup(database)
    try:
        received = await intake.capture(command, owner=owner)
        if fault == 'delivery':
            await intake.capture_and_apply(command, owner=owner, max_rows=64, max_bytes=1048576)
            table = 'spot_financial_ingress_receipts'
            sql = "UPDATE life_financial.spot_financial_ingress_receipts SET financial_operation_id='ack1'"
        else:
            table = 'spot_financial_ingress'
            sql = {'body': "UPDATE life_financial.spot_financial_ingress SET body=jsonb_set(body,'{command,exchange_order_id}','\"999\"')",
                   'capture_sequence': 'UPDATE life_financial.spot_financial_ingress SET capture_sequence=capture_sequence+1',
                   'captured_at': "UPDATE life_financial.spot_financial_ingress SET captured_at=captured_at+interval '1 second'"}[fault]
        await database[0].execute('ALTER TABLE life_financial.' + table + ' DISABLE TRIGGER USER')
        await database[0].execute(sql)
        await database[0].execute('ALTER TABLE life_financial.' + table + ' ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='PG_FINANCIAL_INGRESS_UNVERIFIED'):
            await intake.lookup(received.ingress_id)
        assert (await repo.snapshot(cfg.binding)).recovery_required
        assert (await repo.balance(cfg.binding)).reserved_quote_usdt > 0
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_missing_ack_capture_after_financial_ack_does_not_disappear_from_recovery(database, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_quote_transport import transport
    t = await transport(database)

    async def failed(*args, **kwargs):
        raise StorageUnavailable('PG_SYNTHETIC_ACK_DELIVERY_FAILURE')
    monkeypatch.setattr(t.guard._ingress, 'mark_applied', failed)
    try:
        with pytest.raises(StorageError, match='PG_SYNTHETIC_ACK_DELIVERY_FAILURE'):
            await t.place()
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts') == 'ACKED'
        await database[0].execute('ALTER TABLE life_financial.spot_ack_ingress DISABLE TRIGGER USER')
        await database[0].execute('DELETE FROM life_financial.spot_ack_ingress')
        await database[0].execute('ALTER TABLE life_financial.spot_ack_ingress ENABLE TRIGGER USER')
        assert (await t.repo.snapshot(t.cfg.binding)).recovery_required
        assert (await t.repo.balance(t.cfg.binding)).reserved_quote_usdt > 0
        assert len(t.session.sent) == 1
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['fill', 'cashflow'])
async def test_financial_pending_is_retained_without_claiming_delivery_or_inventing_liability(database, kind):
    from decimal import Decimal
    if kind == 'fill':
        repo, cfg, owner, command, intake = await setup(database)
        command = replace(command, fills=(replace(command.fills[0], signed_fee=Decimal('-100')),))
    else:
        repo, cfg, owner, at, ms = await capital_store(database, (('101', 'USDT', '-11'),))
        command = flow(at, ms, signed_amount=Decimal('-11'))
        intake = PgFinancialIngress(repo)
    try:
        before = await repo.balance(cfg.binding)
        receipt = await intake.capture_and_apply(command, owner=owner, max_rows=64, max_bytes=1048576)
        assert receipt.state == 'RECOVERY_REQUIRED' and not repo._runtime.healthy
        pending = await intake.pending(cfg.binding, max_rows=64, max_bytes=1048576)
        assert len(pending) == 1 and pending[0].command == command
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_financial_ingress_receipts') == 0
        assert await repo.balance(cfg.binding) == before
    finally:
        await repo.close()
