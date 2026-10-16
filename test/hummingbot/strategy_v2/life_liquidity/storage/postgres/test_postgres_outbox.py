"""Durable reporting delivery never dispatches an exchange operation."""
import json
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_quote_transport import transport

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.recorder import PgRecorderRepository
from hummingbot.strategy_v2.life_liquidity.storage.recorder import ExecutorProjection
from hummingbot.strategy_v2.life_liquidity.storage.recorder_progress import ExecutorProgress
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo


async def setup(database):
    t = await transport(database)
    repo = PgRecorderRepository(t.repo, controller_id=t.row.controller_id, max_rows=32, max_read_bytes=1048576)
    info = ExecutorInfo(id=t.row.intent_id, timestamp=t.create.executor_config.timestamp, type='order_executor',
                        controller_id=t.row.controller_id, config=t.create.executor_config.model_copy(deep=True),
                        status=RunnableStatus.NOT_STARTED, net_pnl_pct=D('0'), net_pnl_quote=D('0'), cum_fees_quote=D('0'),
                        filled_amount_quote=D('0'), is_active=False, is_trading=False,
                        custom_info={'recovery_account_uid': t.cfg.binding.account_uid, 'recovery_order_ids': []})
    command = ExecutorProjection.from_info('report-initial', t.cfg.binding, info)
    return t, repo, info, command


@pytest.mark.asyncio
async def test_stage_delivery_and_cold_read_are_atomic_idempotent_and_money_neutral(database):
    t, repo, info, command = await setup(database)
    try:
        before = await t.repo.snapshot(t.cfg.binding)
        staged = await repo.stage(command, cut=before.cut, owner=t.owner)
        assert staged.sequence == 0 and not staged.trading_permission
        assert len(await repo.pending(t.cfg.binding)) == 1
        pending_cut = (await t.repo.snapshot(t.cfg.binding)).cut
        assert (await t.repo.snapshot(t.cfg.binding)).recovery_required
        with pytest.raises(StorageError):
            await t.repo.arm('operation1', cut=pending_cut, owner=t.owner)
        assert (await t.repo.snapshot(t.cfg.binding)).cut == pending_cut
        assert await t.repo.balance(t.cfg.binding) == before.balance
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."Executors"') == 0
        applied = await repo.apply(command.operation_id, cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
        assert applied.state == 'APPLIED' and not applied.trading_permission
        assert await repo.pending(t.cfg.binding) == ()
        assert not (await t.repo.snapshot(t.cfg.binding)).recovery_required
        assert await t.repo.balance(t.cfg.binding) == before.balance
        assert (await repo.stage(command, cut=before.cut, owner=t.owner)).replayed
        assert (await repo.apply(command.operation_id, cut=before.cut, owner=t.owner)).replayed
        cold = PgRecorderRepository(t.repo, controller_id=t.row.controller_id, max_rows=32, max_read_bytes=1048576)
        rows = await cold.read(t.cfg.binding)
        assert len(rows) == 1 and json.loads(rows[0].payload_json)['id'] == info.id
        assert not t.session.sent
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_ordered_progress_and_old_delivery_do_not_regress_current_reporting(database):
    t, repo, info, initial = await setup(database)
    try:
        await repo.project(initial, owner=t.owner)
        info.status, info.is_active, info.filled_amount_quote = RunnableStatus.TERMINATED, False, D('2')
        info.custom_info['recovery_order_ids'] = ['wire1']
        update = ExecutorProgress.from_info('report-progress', t.cfg.binding, 0, info)
        first = await repo.project(update, owner=t.owner)
        assert first.sequence == 1
        await repo.project(initial, owner=t.owner)
        row = (await repo.read(t.cfg.binding))[0]
        assert json.loads(row.payload_json)['status'] == 4
        assert await database[0].fetchval('SELECT status FROM life_recorder."Executors" WHERE id=$1', info.id) == 4
        assert await repo.pending(t.cfg.binding) == ()
        assert not t.session.sent
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['sequence', 'config', 'uid', 'regression', 'wire', 'owner', 'binding'])
async def test_invalid_reporting_cannot_change_heads_or_money(database, fault):
    t, repo, info, initial = await setup(database)
    try:
        await repo.project(initial, owner=t.owner)
        info.status, info.filled_amount_quote = RunnableStatus.RUNNING, D('1')
        info.custom_info['recovery_order_ids'] = ['wire1']
        await repo.project(ExecutorProgress.from_info('one', t.cfg.binding, 0, info), owner=t.owner)
        before = await t.repo.snapshot(t.cfg.binding)
        owner, binding, expected = t.owner, t.cfg.binding, 1
        if fault == 'sequence':
            expected = 0
        elif fault == 'config':
            info.config.amount = D('9')
        elif fault == 'uid':
            info.custom_info['recovery_account_uid'] = 'foreign'
        elif fault == 'regression':
            info.status = RunnableStatus.NOT_STARTED
        elif fault == 'wire':
            info.custom_info['recovery_order_ids'] = []
        elif fault == 'owner':
            owner = replace(owner, epoch=2)
        else:
            binding = replace(binding, account_uid='999')
        with pytest.raises(StorageError):
            command = ExecutorProgress.from_info('bad', binding, expected, info)
            await repo.stage(command, cut=before.cut, owner=owner)
        assert (await t.repo.snapshot(t.cfg.binding)).cut == before.cut
        assert await t.repo.balance(t.cfg.binding) == before.balance
        assert len(await repo.read(t.cfg.binding)) == 1
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_pending_reporting_blocks_risk_but_does_not_block_received_ack(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import ack
    t, repo, info, initial = await setup(database)
    try:
        await t.repo.arm('operation1', cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
        await repo.stage(initial, cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
        observed = await t.repo.acknowledge(ack(t.owner, datetime.now(timezone.utc)), cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
        assert observed.state == 'ACKED'
        assert (await t.repo.snapshot(t.cfg.binding)).recovery_required
        await repo.apply(initial.operation_id, cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
        assert not (await t.repo.snapshot(t.cfg.binding)).recovery_required
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['stage', 'apply'])
async def test_lost_committed_reply_is_recovered_without_delivery_duplication(database, monkeypatch, phase):
    t, repo, info, initial = await setup(database)
    try:
        if phase == 'apply':
            await repo.stage(initial, cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
        original = t.repo._runtime._commit

        async def lost(tx):
            await original(tx)
            raise OSError('synthetic lost commit reply')
        monkeypatch.setattr(t.repo._runtime, '_commit', lost)
        cut = (await t.repo.snapshot(t.cfg.binding)).cut
        with pytest.raises(StorageError, match='PG_COMMIT_UNKNOWN'):
            if phase == 'stage':
                await repo.stage(initial, cut=cut, owner=t.owner)
            else:
                await repo.apply(initial.operation_id, cut=cut, owner=t.owner)
        monkeypatch.setattr(t.repo._runtime, '_commit', original)
        result = await repo.project(initial, owner=t.owner)
        assert result.state == 'APPLIED'
        assert len(await repo.read(t.cfg.binding)) == 1
        assert await repo.pending(t.cfg.binding) == ()
        assert not t.session.sent
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_pending_read_barrier_and_ordered_delivery_are_enforced(database):
    t, repo, info, initial = await setup(database)
    try:
        await repo.stage(initial, cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
        info.status = RunnableStatus.RUNNING
        progress = ExecutorProgress.from_info('next', t.cfg.binding, 0, info)
        await repo.stage(progress, cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
        with pytest.raises(StorageError, match='PG_RECORDER_DELIVERY_PENDING'):
            await repo.read(t.cfg.binding)
        cut = (await t.repo.snapshot(t.cfg.binding)).cut
        with pytest.raises(StorageError, match='PG_RECORDER_CUT_OR_SEQUENCE_INVALID'):
            await repo.apply(progress.operation_id, cut=cut, owner=t.owner)
        assert (await t.repo.snapshot(t.cfg.binding)).cut == cut
        await repo.apply(initial.operation_id, cut=cut, owner=t.owner)
        assert (await t.repo.snapshot(t.cfg.binding)).recovery_required
        await repo.apply(progress.operation_id, cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
        assert json.loads((await repo.read(t.cfg.binding))[0].payload_json)['status'] == 2
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['legacy', 'outbox', 'receipt', 'operation'])
async def test_cold_corruption_diagnosis_blocks_reports_and_new_risk(database, target):
    t, repo, info, initial = await setup(database)
    try:
        await repo.project(initial, owner=t.owner)
        if target == 'legacy':
            await database[0].execute('UPDATE life_recorder."Executors" SET filled_amount_quote=9 WHERE id=$1', info.id)
        else:
            table = {'outbox': 'recorder_outbox', 'receipt': 'projection_receipts', 'operation': 'operations'}[target]
            # Privileged test-only corruption. Runtime has no DML/bypass grants.
            await database[0].execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
            if target == 'outbox':
                await database[0].execute("UPDATE life_financial.recorder_outbox SET source_hash=repeat('e',64)")
            elif target == 'receipt':
                await database[0].execute("UPDATE life_financial.projection_receipts SET applied_hash=repeat('e',64)")
            else:
                await database[0].execute("UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{sequence}','999') WHERE operation_id=$1", initial.operation_id)
            await database[0].execute(f'ALTER TABLE life_financial.{table} ENABLE TRIGGER USER')
        before = await t.repo.balance(t.cfg.binding)
        cold = PgRecorderRepository(t.repo, controller_id=t.row.controller_id, max_rows=32, max_read_bytes=1048576)
        with pytest.raises(StorageError, match='PG_RECORDER_UNVERIFIED'):
            await cold.read(t.cfg.binding)
        state = await t.repo.snapshot(t.cfg.binding)
        assert state.recovery_required
        with pytest.raises(StorageError):
            await t.repo.arm('operation1', cut=state.cut, owner=t.owner)
        assert await t.repo.balance(t.cfg.binding) == before
        assert not t.session.sent
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_capacity_denies_instead_of_returning_partial_pending_scope(database):
    t, repo, info, initial = await setup(database)
    try:
        await repo.stage(initial, cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
        info.status = RunnableStatus.RUNNING
        await repo.stage(ExecutorProgress.from_info('next', t.cfg.binding, 0, info),
                         cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
        bounded = PgRecorderRepository(t.repo, controller_id=t.row.controller_id, max_rows=1, max_read_bytes=1048576)
        with pytest.raises(StorageError, match='PG_RECORDER_CAPACITY'):
            await bounded.pending(t.cfg.binding)
        assert len(await repo.pending(t.cfg.binding)) == 2
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_existing_foreign_legacy_target_is_never_adopted(database):
    t, repo, info, initial = await setup(database)
    try:
        await repo.stage(initial, cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
        await database[0].execute('''INSERT INTO life_recorder."Executors"
            SELECT * FROM jsonb_populate_record(NULL::life_recorder."Executors",$1::jsonb)''', initial.payload_json)
        cut = (await t.repo.snapshot(t.cfg.binding)).cut
        with pytest.raises(StorageError, match='PG_RECORDER_FOREIGN_TARGET'):
            await repo.apply(initial.operation_id, cut=cut, owner=t.owner)
        assert (await t.repo.snapshot(t.cfg.binding)).cut == cut
        assert len(await repo.pending(t.cfg.binding)) == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.projection_receipts') == 0
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['stage', 'apply'])
async def test_generation_change_at_commit_rolls_back_entire_reporting_operation(database, monkeypatch, phase):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import install_generation_fault
    t, repo, info, initial = await setup(database)
    await install_generation_fault(database)
    try:
        if phase == 'apply':
            await repo.stage(initial, cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
        before = await t.repo.snapshot(t.cfg.binding)
        original = t.repo._runtime._commit

        async def changed(tx):
            await tx._connection.execute('SELECT life_financial.test_change_generation($1)', t.cfg.account_id)
            await original(tx)
        monkeypatch.setattr(t.repo._runtime, '_commit', changed)
        with pytest.raises(StorageError, match='OWNER_EXPIRED_OR_CHANGED_AT_COMMIT'):
            if phase == 'stage':
                await repo.stage(initial, cut=before.cut, owner=t.owner)
            else:
                await repo.apply(initial.operation_id, cut=before.cut, owner=t.owner)
        assert (await t.repo.snapshot(t.cfg.binding)).cut == before.cut
        assert await t.repo.balance(t.cfg.binding) == before.balance
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."Executors"') == 0
        assert len(await repo.pending(t.cfg.binding)) == (1 if phase == 'apply' else 0)
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['stage', 'apply'])
@pytest.mark.parametrize('committed', [False, True])
async def test_actual_backend_termination_before_after_commit_and_cold_recovery(database, monkeypatch, phase, committed):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    t, repo, info, initial = await setup(database)
    if phase == 'apply':
        await repo.stage(initial, cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
    before = await t.repo.snapshot(t.cfg.binding)
    original = t.repo._runtime._commit
    admin = await asyncpg.connect(database[1])

    async def lost(tx):
        pid = tx._connection.get_server_pid()
        if committed:
            await original(tx)
        assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid)
        raise ConnectionError('synthetic reporting COMMIT reply loss')
    monkeypatch.setattr(t.repo._runtime, '_commit', lost)
    try:
        with pytest.raises(CommitUnknown):
            if phase == 'stage':
                await repo.stage(initial, cut=before.cut, owner=t.owner)
            else:
                await repo.apply(initial.operation_id, cut=before.cut, owner=t.owner)
    finally:
        await admin.close()
        await t.close()
    financial = PgSpotRepository(await PgRuntime.open(t.cfg))
    cold = PgRecorderRepository(financial, controller_id=t.row.controller_id, max_rows=32, max_read_bytes=1048576)
    try:
        expected_pending = committed if phase == 'stage' else not committed
        assert len(await cold.pending(t.cfg.binding)) == int(expected_pending)
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."Executors"') == int(phase == 'apply' and committed)
        await cold.project(initial, owner=t.owner)
        assert len(await cold.read(t.cfg.binding)) == 1
        assert await cold.pending(t.cfg.binding) == ()
        assert await financial.balance(t.cfg.binding) == before.balance
        assert not t.session.sent
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['stage', 'apply'])
@pytest.mark.parametrize('committed', [False, True])
async def test_actual_process_exit_before_after_reporting_commit(database, phase, committed):
    import asyncio
    import sys
    from dataclasses import fields

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    t, repo, info, initial = await setup(database)
    if phase == 'apply':
        await repo.stage(initial, cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
    before = await t.repo.snapshot(t.cfg.binding)
    cfg = {field.name: getattr(t.cfg, field.name) for field in fields(t.cfg)}
    cfg.update(account_id=str(t.cfg.account_id), database_lineage=str(t.cfg.database_lineage), binding=t.cfg.binding.json())
    payload = json.dumps(dict(config=cfg, owner=t.owner.json(), command=initial.json(), cut=before.cut.json(), phase=phase, committed=committed))
    child = r'''
import asyncio,json,os,sys
from uuid import UUID
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken,StateBinding
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.recorder import ExecutorProjection
from hummingbot.strategy_v2.life_liquidity.storage.postgres.recorder import PgRecorderRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig,PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
async def run():
    data=json.load(sys.stdin);cfg=data['config'];cfg['binding']=StateBinding.from_json(cfg['binding'])
    for key in ('account_id','database_lineage'):cfg[key]=UUID(cfg[key])
    financial=PgSpotRepository(await PgRuntime.open(PgConfig(**cfg)))
    repo=PgRecorderRepository(financial,controller_id='controller1',max_rows=32, max_read_bytes=1048576)
    value=data['command'];command=ExecutorProjection(value['operation_id'],cfg['binding'],value['payload_json'])
    original=financial._runtime._commit
    async def die(tx):
        if data['committed']:await original(tx)
        os._exit(73)
    financial._runtime._commit=die
    kwargs=dict(cut=FinancialCut.from_json(data['cut']),owner=OwnerToken.from_json(data['owner']))
    if data['phase']=='stage':await repo.stage(command,**kwargs)
    else:await repo.apply(command.operation_id,**kwargs)
asyncio.run(run())
'''
    try:
        process = await asyncio.create_subprocess_exec(sys.executable, '-c', child, stdin=asyncio.subprocess.PIPE,
                                                       stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            _, stderr = await asyncio.wait_for(process.communicate(payload.encode()), 15)
        except BaseException:
            process.kill()
            await process.wait()
            raise
        assert process.returncode == 73, stderr.decode()
    finally:
        await t.close()
    financial = PgSpotRepository(await PgRuntime.open(t.cfg))
    cold = PgRecorderRepository(financial, controller_id=t.row.controller_id, max_rows=32, max_read_bytes=1048576)
    try:
        assert len(await cold.pending(t.cfg.binding)) == int(committed if phase == 'stage' else not committed)
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."Executors"') == int(phase == 'apply' and committed)
        await cold.project(initial, owner=t.owner)
        assert len(await cold.read(t.cfg.binding)) == 1
        assert await cold.pending(t.cfg.binding) == ()
        assert await financial.balance(t.cfg.binding) == before.balance
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['subtype', 'payload', 'status', 'binding', 'progress', 'cut'])
async def test_raw_sql_malformed_commands_cannot_bypass_validation(database, field):
    import asyncpg
    t, repo, info, initial = await setup(database)
    c = await asyncpg.connect(t.cfg.dsn)
    try:
        command, subtype = initial.json(), 'INITIAL'
        payload = json.loads(command['payload_json'])
        before = await t.repo.snapshot(t.cfg.binding)
        if field == 'subtype':
            subtype = None
        elif field == 'payload':
            payload.pop('status')
        elif field == 'status':
            payload['status'] = None
        elif field == 'binding':
            payload['custom_info']['life_storage_binding'] = None
        elif field == 'progress':
            subtype, command['expected_sequence'] = 'PROGRESS', None
        command['payload_json'] = json.dumps(payload)
        with pytest.raises(asyncpg.RaiseError, match='RECORDER_'):
            await c.fetchval('SELECT life_financial.recorder_stage_executor($1,$2::jsonb,$3::jsonb,$4,$5,$6,$7,$8::jsonb)',
                             t.cfg.account_id, json.dumps(t.cfg.binding.json()), json.dumps(t.owner.json()),
                             before.cut.checkpoint.revision, None if field == 'cut' else before.cut.checkpoint.state_digest,
                             'f' * 64, subtype, json.dumps(command))
        assert (await t.repo.snapshot(t.cfg.binding)).cut == before.cut
        assert await repo.pending(t.cfg.binding) == ()
    finally:
        await c.close()
        await t.close()


@pytest.mark.asyncio
async def test_read_only_role_can_read_scoped_reports_but_not_write_or_call_private_proofs(database):
    from uuid import uuid4

    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    t, repo, info, initial = await setup(database)
    reporting = None
    try:
        await repo.project(initial, owner=t.owner)
        cfg = replace(t.cfg, dsn=t.cfg.dsn.replace('life_pg2_runtime@', 'life_pg2_reporter@'),
                      principal='life_pg2_reporter', write=False)
        reporting = PgSpotRepository(await PgRuntime.open(cfg))
        reader = PgRecorderRepository(reporting, controller_id=t.row.controller_id, max_rows=32, max_read_bytes=1048576)
        assert len(await reader.read(t.cfg.binding)) == 1
        with pytest.raises(StorageError, match='PG_SPOT_COMMAND_INVALID'):
            await reader.stage(initial, cut=(await reporting.snapshot(t.cfg.binding)).cut, owner=t.owner)
        c = await asyncpg.connect(cfg.dsn)
        try:
            for signature in ('recorder_stage_executor(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)',
                              'recorder_apply_executor(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.identity)',
                              'recorder_executor_integrity(uuid)', 'spot_state_v15(uuid)'):
                assert not await c.fetchval('SELECT has_function_privilege(current_user,$1,\'EXECUTE\')', 'life_financial.' + signature)
            with pytest.raises(asyncpg.RaiseError, match='ACCOUNT_SCOPE_INVALID'):
                await c.fetchval('SELECT life_financial.recorder_executor_verified($1)', uuid4())
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.execute("UPDATE life_financial.recorder_outbox SET sequence=99")
        finally:
            await c.close()
    finally:
        if reporting is not None:
            await reporting.close()
        await t.close()


@pytest.mark.asyncio
async def test_original_owner_may_deliver_late_reporting_after_halt_without_new_risk(database):
    t, repo, info, initial = await setup(database)
    try:
        await repo.stage(initial, cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
        await database[0].execute('UPDATE life_financial.accounts SET halted=true WHERE account_id=$1', t.cfg.account_id)
        result = await repo.apply(initial.operation_id, cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
        assert result.state == 'APPLIED' and not result.trading_permission
        assert len(await repo.read(t.cfg.binding)) == 1
        with pytest.raises(StorageError):
            await t.repo.arm('operation1', cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
        assert not t.session.sent
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('applied', [False, True])
async def test_read_result_byte_capacity_is_checked_before_transfer(database, applied):
    t, repo, info, initial = await setup(database)
    try:
        await repo.stage(initial, cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
        if applied:
            await repo.apply(initial.operation_id, cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
        bounded = PgRecorderRepository(t.repo, controller_id=t.row.controller_id, max_rows=32, max_read_bytes=8)
        before = await t.repo.snapshot(t.cfg.binding)
        with pytest.raises(StorageError, match='PG_RECORDER_BYTE_CAPACITY'):
            if applied:
                await bounded.read(t.cfg.binding)
            else:
                await bounded.pending(t.cfg.binding)
        assert (await t.repo.snapshot(t.cfg.binding)).cut == before.cut
        assert await t.repo.balance(t.cfg.binding) == before.balance
    finally:
        await t.close()
