"""Session safety decisions against a disposable real PostgreSQL primary."""
import asyncio
from dataclasses import replace
from datetime import timedelta
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order, spot
from uuid import uuid4

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import AtomicScope, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.session import SessionTick


def tick(owner, **changes):
    return replace(SessionTick('pause', str(owner.session_id), owner.epoch, False, False, False, 1000), **changes)


@pytest.mark.asyncio
async def test_pause_resume_atomic_state_and_permanent_original_retry(database):
    repo, cfg, owner = await spot(database)
    try:
        before = await repo.snapshot(cfg.binding)
        command = tick(owner)
        receipt = await repo.tick_session(command, cut=before.cut, owner=owner)
        assert receipt.atomic_scope is AtomicScope.ACCOUNT
        assert (receipt.state, receipt.reason_code, receipt.replayed) == ('PAUSED', 'SESSION_GATE_UNAVAILABLE', False)
        paused = await repo.snapshot(cfg.binding)
        assert paused.session.state == 'PAUSED' and not paused.recovery_required
        assert (paused.balance, paused.budget) == (before.balance, before.budget)
        with pytest.raises(StorageError):
            await repo.prepare(order(owner), cut=paused.cut, owner=owner)
        assert (await repo.tick_session(command, cut=before.cut, owner=owner)).replayed
        resume = tick(owner, operation_id='resume', reference_ready=True, all_gates_ready=True)
        assert (await repo.tick_session(resume, cut=paused.cut, owner=owner)).state == 'ACTIVE'
        assert (await repo.lookup_session_tick('pause', cfg.binding)).state == 'PAUSED'
        assert await repo.pending_session_ticks(cfg.binding) == ()
        await repo.prepare(order(owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('on_expiry,state,reason', [
    ('pause_quotes', 'EXPIRED', 'SESSION_DEADLINE_REACHED'),
    ('switch_to_market_reference', 'TRANSITIONING', 'OLD_ORDERS_UNRESOLVED'),
])
async def test_absolute_expiry_does_not_extend_owner_or_create_successor(database, on_expiry, state, reason):
    repo, cfg, owner = await spot(database, session_duration=timedelta(seconds=1), on_expiry=on_expiry)
    try:
        before = await repo.snapshot(cfg.binding)
        await asyncio.sleep(max(0, (before.session.expires_at - await database[0].fetchval('SELECT clock_timestamp()')).total_seconds()) + .01)
        result = await repo.tick_session(tick(owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert (result.state, result.reason_code) == (state, reason)
        final = await repo.snapshot(cfg.binding)
        assert final.session.state == state and final.session.expires_at == before.session.expires_at
        assert final.balance == before.balance and final.budget == before.budget
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.sessions') == 1
        assert await database[0].fetchval('SELECT owner_until FROM life_financial.accounts') == owner.expires_at
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.activation_grants') == 0
        with pytest.raises(StorageError):
            await repo.prepare(order(owner), cut=final.cut, owner=owner)
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_stale_conflicting_and_concurrent_ticks_do_not_overwrite_state(database):
    repo, cfg, owner = await spot(database)
    try:
        cut = (await repo.snapshot(cfg.binding)).cut
        results = await asyncio.gather(repo.tick_session(tick(owner), cut=cut, owner=owner),
                                       repo.tick_session(tick(owner, operation_id='other'), cut=cut, owner=owner), return_exceptions=True)
        assert sum(isinstance(value, StorageError) for value in results) == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_session_events') == 1
        success = next(value for value in results if not isinstance(value, Exception))
        with pytest.raises(StorageError, match='OPERATION_CONFLICT'):
            await repo.tick_session(tick(owner, operation_id=success.operation_id, reference_ready=True), cut=cut, owner=owner)
        assert (await repo.session(cfg.binding)).state == 'PAUSED'
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['pause', 'resume'])
@pytest.mark.parametrize('committed', [False, True])
async def test_lost_commit_readback_retains_original_session_operation(database, monkeypatch, stage, committed):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner = await spot(database)
    if stage == 'resume':
        await repo.tick_session(tick(owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    command = tick(owner, operation_id='fault', reference_ready=stage == 'resume', all_gates_ready=stage == 'resume')
    cut = (await repo.snapshot(cfg.binding)).cut
    original = repo._runtime._commit
    admin = await asyncpg.connect(database[1])

    async def lost(tx):
        pid = tx._connection.get_server_pid()
        if committed:
            await original(tx)
        await admin.fetchval('SELECT pg_terminate_backend($1)', pid)
        raise ConnectionError('synthetic lost session COMMIT reply')
    monkeypatch.setattr(repo._runtime, '_commit', lost)
    try:
        with pytest.raises(CommitUnknown):
            await repo.tick_session(command, cut=cut, owner=owner)
        assert not repo._runtime.healthy
        assert ((await repo.lookup_session_tick('fault', cfg.binding)) is not None) is committed
    finally:
        await admin.close()
        await repo.close()
    cold = PgSpotRepository(await PgRuntime.open(cfg))
    try:
        result = await cold.tick_session(command, cut=cut, owner=owner)
        assert result.replayed is committed
        assert result.state == ('ACTIVE' if stage == 'resume' else 'PAUSED')
        assert await database[0].fetchval("SELECT count(*) FROM life_financial.spot_session_events WHERE operation_id='fault'") == 1
        assert await cold.pending_session_ticks(cfg.binding) == ()
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['pause', 'resume'])
async def test_generation_change_at_commit_rolls_back_session_event_and_receipt(database, monkeypatch, stage):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import install_generation_fault
    repo, cfg, owner = await spot(database)
    await install_generation_fault(database)
    if stage == 'resume':
        await repo.tick_session(tick(owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    original = repo._runtime._commit

    async def changed(tx):
        await tx._connection.execute('SELECT life_financial.test_change_generation($1)', cfg.account_id)
        await original(tx)
    monkeypatch.setattr(repo._runtime, '_commit', changed)
    try:
        before = await repo.snapshot(cfg.binding)
        command = tick(owner, operation_id='changed', reference_ready=stage == 'resume', all_gates_ready=stage == 'resume')
        with pytest.raises(StorageError, match='EXPIRED_OR_CHANGED_AT_COMMIT'):
            await repo.tick_session(command, cut=before.cut, owner=owner)
        assert await repo.lookup_session_tick('changed', cfg.binding) is None
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', ['session_id', 'epoch', 'max_reconciliation_age_ms', 'reference_ready'])
async def test_raw_sql_null_fields_cannot_bypass_session_scope(database, bad):
    import asyncpg
    repo, cfg, owner = await spot(database)
    connection = await asyncpg.connect(cfg.dsn)
    import json
    try:
        cut = (await repo.snapshot(cfg.binding)).cut
        command = {**tick(owner).json(), bad: None}
        with pytest.raises(asyncpg.RaiseError, match='MODE_UNSUPPORTED'):
            await connection.fetchval('SELECT life_financial.spot_session_tick($1,$2::jsonb,$3::jsonb,$4,$5,$6,$7::jsonb)',
                                      cfg.account_id, json.dumps(cfg.binding.json()), json.dumps(owner.json()),
                                      cut.checkpoint.revision, cut.checkpoint.state_digest, 'f' * 64, json.dumps(command))
        assert (await repo.snapshot(cfg.binding)).cut == cut
    finally:
        await connection.close()
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_actual_process_exit_publishes_whole_session_or_nothing(database, committed):
    import json
    import sys
    from dataclasses import fields

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner = await spot(database)
    try:
        before = await repo.snapshot(cfg.binding)
        config = {field.name: getattr(cfg, field.name) for field in fields(cfg)}
        config.update(account_id=str(cfg.account_id), database_lineage=str(cfg.database_lineage), binding=cfg.binding.json())
        payload = json.dumps(dict(config=config, owner=owner.json(), cut=before.cut.json(), command=tick(owner).json(), committed=committed))
        child = r'''
import asyncio, json, os, sys
from uuid import UUID
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken, StateBinding
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.session import SessionTick
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig, PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
async def run():
    data=json.load(sys.stdin)
    config=data['config']
    config['binding']=StateBinding.from_json(config['binding'])
    for key in ('account_id','database_lineage'): config[key]=UUID(config[key])
    runtime=await PgRuntime.open(PgConfig(**config))
    original=runtime._commit
    async def die(tx):
        if data['committed']: await original(tx)
        os._exit(73)
    runtime._commit=die
    await PgSpotRepository(runtime).tick_session(SessionTick.from_json(data['command']),
        cut=FinancialCut.from_json(data['cut']),owner=OwnerToken.from_json(data['owner']))
asyncio.run(run())
'''
        process = await asyncio.create_subprocess_exec(sys.executable, '-c', child, stdin=asyncio.subprocess.PIPE,
                                                       stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            _, stderr = await asyncio.wait_for(process.communicate(payload.encode()), 15)
        except BaseException:
            process.kill()
            await process.wait()
            raise
        assert process.returncode == 73, stderr.decode()
    finally:
        await repo.close()
    cold = PgSpotRepository(await PgRuntime.open(cfg))
    try:
        assert ((await cold.lookup_session_tick('pause', cfg.binding)) is not None) is committed
        assert (await cold.session(cfg.binding)).state == ('PAUSED' if committed else 'ACTIVE')
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_session_events') == int(committed)
        assert (await cold.tick_session(tick(owner), cut=before.cut, owner=owner)).replayed is committed
        final = await cold.snapshot(cfg.binding)
        assert (final.balance, final.budget) == (before.balance, before.budget)
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_expired_resume_at_commit_rolls_back_to_paused(database, monkeypatch):
    repo, cfg, owner = await spot(database)
    try:
        await repo.tick_session(tick(owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        expires = await database[0].fetchval("SELECT clock_timestamp()+interval '1 second'")
        owner = replace(owner, expires_at=expires)
        await database[0].execute('UPDATE life_financial.accounts SET owner_until=$1', expires)
        original = repo._runtime._commit

        async def expire(tx):
            await asyncio.sleep(1.1)
            await original(tx)
        monkeypatch.setattr(repo._runtime, '_commit', expire)
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='OWNER_OR_BINDING_INVALID'):
            await repo.tick_session(tick(owner, operation_id='resume', reference_ready=True, all_gates_ready=True), cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert await repo.lookup_session_tick('resume', cfg.binding) is None
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_unverified_progress_cannot_be_repaired_by_tick_or_used_for_orders(database):
    repo, cfg, owner = await spot(database)
    try:
        await database[0].execute("UPDATE life_financial.spot_session_progress SET state='PAUSED',reason_code='SESSION_GATE_UNAVAILABLE',version=version+1")
        view = await repo.snapshot(cfg.binding)
        assert view.recovery_required
        for command in (tick(owner), tick(owner, reference_ready=True, all_gates_ready=True)):
            with pytest.raises(StorageError, match='SESSION_UNVERIFIED'):
                await repo.tick_session(command, cut=view.cut, owner=owner)
        with pytest.raises(StorageError, match='SESSION_UNVERIFIED'):
            await repo.prepare(order(owner), cut=view.cut, owner=owner)
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_no_implicit_session_policy_or_progress_bootstrap(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import backend

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    runtime, cfg, owner = await backend(database)
    repo = PgSpotRepository(runtime)
    try:
        # Financial policy exists, but the required session import does not.
        await database[0].execute("INSERT INTO life_financial.spot_policies VALUES($1,$2,0,100,1000,100,'synthetic',10,5,2,10,5,2,8)", cfg.account_id, cfg.binding.policy_hash)
        view = await repo.snapshot(cfg.binding)
        assert view.recovery_required and view.session.state == 'PAUSED'
        with pytest.raises(StorageError, match='SESSION_UNVERIFIED'):
            await repo.tick_session(tick(owner), cut=view.cut, owner=owner)
        with pytest.raises(StorageError, match='SESSION_UNVERIFIED'):
            await repo.prepare(order(owner), cut=view.cut, owner=owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_session_policies') == 0
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_file_and_postgres_primary_session_decisions_preserve_money(database, tmp_path):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import (
        BUDGET,
        CAPACITY,
        NOW,
        RISK,
        create_fixture,
        open_legacy_spot,
    )
    repo, cfg, owner = await spot(database)
    files, binding, file_owner = create_fixture(tmp_path)
    native = await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                    subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                    account_lock_root=tmp_path / 'locks', owner=file_owner, create_binding=True, clock=lambda: NOW)
    try:
        for ready in (False, True, False):
            receipts = []
            for store, scope, token in ((repo, cfg.binding, owner), (native, binding, file_owner)):
                before = await store.snapshot(scope)
                command = tick(token, operation_id=f'tick-{before.cut.checkpoint.revision}', reference_ready=ready, all_gates_ready=ready)
                receipts.append(await store.tick_session(command, cut=before.cut, owner=token))
                after = await store.snapshot(scope)
                assert (after.balance, after.budget) == (before.balance, before.budget)
            assert (receipts[0].state, receipts[0].reason_code) == (receipts[1].state, receipts[1].reason_code)
    finally:
        await native.close()
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', ['owner', 'scope', 'policy', 'cut', 'successor', 'grant'])
async def test_session_invalid_commands_have_no_effect(database, bad):
    repo, cfg, owner = await spot(database)
    try:
        before = await repo.snapshot(cfg.binding)
        command, token, cut = tick(owner), owner, before.cut
        if bad == 'owner':
            token = replace(owner, generation=uuid4())
        elif bad == 'scope':
            command = replace(command, session_id=str(uuid4()))
        elif bad == 'policy':
            command = replace(command, max_reconciliation_age_ms=999)
        elif bad == 'cut':
            cut = replace(cut, checkpoint=replace(cut.checkpoint, state_digest='f' * 64), components=(('postgres_account', 'f' * 64),))
        elif bad == 'successor':
            command = replace(command, observe_only_successor=True)
        else:
            command = replace(command, observation_grant_id='unverified')
        with pytest.raises(StorageError):
            await repo.tick_session(command, cut=cut, owner=token)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert await repo.lookup_session_tick('pause', cfg.binding) is None
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_pause_keeps_existing_holds_and_prevents_arm(database):
    repo, cfg, owner = await spot(database)
    try:
        await repo.prepare(order(owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        before = await repo.snapshot(cfg.binding)
        await repo.tick_session(tick(owner), cut=before.cut, owner=owner)
        paused = await repo.snapshot(cfg.binding)
        assert (paused.balance, paused.budget) == (before.balance, before.budget)
        with pytest.raises(StorageError):
            await repo.arm('operation1', cut=paused.cut, owner=owner)
        assert (await repo.attempt('intent1', cfg.binding)).state == 'PREPARED'
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_clock_rollback_latches_across_cold_restart(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository

    # Explicit privileged import simulates a restored future clock watermark;
    # the runtime has no server-clock override and cannot write this seed.
    repo, cfg, owner = await spot(database, session_clock_offset=timedelta(seconds=1))
    try:
        before = await repo.snapshot(cfg.binding)
        receipt = await repo.tick_session(tick(owner), cut=before.cut, owner=owner)
        assert (receipt.state, receipt.reason_code) == ('PAUSED', 'CLOCK_ROLLBACK')
    finally:
        await repo.close()
    cold = PgSpotRepository(await PgRuntime.open(cfg))
    try:
        view = await cold.snapshot(cfg.binding)
        assert view.session.rollback_latched and view.session.state == 'PAUSED'
        await asyncio.sleep(max(0, (view.session.last_seen_at - await database[0].fetchval('SELECT clock_timestamp()')).total_seconds()) + .01)
        command = tick(owner, operation_id='try-resume', reference_ready=True, all_gates_ready=True)
        assert (await cold.tick_session(command, cut=view.cut, owner=owner)).reason_code == 'CLOCK_ROLLBACK'
        with pytest.raises(StorageError):
            await cold.prepare(order(owner), cut=(await cold.snapshot(cfg.binding)).cut, owner=owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.grant_consumptions') == 0
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_halt_and_expired_owner_can_pause_but_cannot_resume(database):
    repo, cfg, owner = await spot(database)
    try:
        expired = owner.valid_from + timedelta(milliseconds=1)
        owner = replace(owner, expires_at=expired)
        await database[0].execute("UPDATE life_financial.accounts SET owner_until=$1,halted=true,mode='RECOVERY_ONLY'", expired)
        receipt = await repo.tick_session(tick(owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert receipt.state == 'PAUSED'
        with pytest.raises(StorageError):
            await repo.tick_session(tick(owner, operation_id='resume', reference_ready=True, all_gates_ready=True),
                                    cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert (await repo.session(cfg.binding)).state == 'PAUSED'
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_unknown_send_can_pause_without_releasing_holds_but_cannot_resume(database):
    repo, cfg, owner = await spot(database)
    try:
        await repo.prepare(order(owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await repo.arm('operation1', cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        before = await repo.snapshot(cfg.binding)
        await repo.tick_session(tick(owner), cut=before.cut, owner=owner)
        paused = await repo.snapshot(cfg.binding)
        assert paused.recovery_required and (paused.balance, paused.budget) == (before.balance, before.budget)
        with pytest.raises(StorageError, match='SESSION_RECOVERY_REQUIRED'):
            await repo.tick_session(tick(owner, operation_id='resume', reference_ready=True, all_gates_ready=True), cut=paused.cut, owner=owner)
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_upgrade_revokes_old_entrypoints_and_reapplication_is_empty(database, tmp_path):
    import shutil
    from pathlib import Path

    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import migrate
    root = database[1].rsplit('/', 1)[0]
    admin = await asyncpg.connect(database[1])
    name = 'life_session_upgrade_' + uuid4().hex
    await admin.execute(f'CREATE DATABASE "{name}" OWNER life_pg2_migrator')
    connection = await asyncpg.connect(root.replace('postgres@', 'life_pg2_migrator@') + '/' + name)
    try:
        directory = Path(__file__).resolve().parents[6] / 'migrations/life_liquidity'
        for path in directory.glob('*.sql'):
            if path.name < '000008':
                shutil.copyfile(path, tmp_path / path.name)
        await migrate(connection, action='up', directory=tmp_path)
        signatures = (
            'life_financial.spot_state(uuid)',
            'life_financial.spot_prepare(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
            'life_financial.spot_arm(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,life_financial.identity)',
        )
        for signature in signatures:
            await connection.execute(f'GRANT EXECUTE ON FUNCTION {signature} TO life_pg2_runtime,life_pg2_reporter')
        result = await migrate(connection, action='up')
        assert result['applied'] == sorted(path.name for path in directory.glob('*.sql') if path.name >= '000008')
        for signature in signatures:
            renamed = signature.replace('spot_state(', 'spot_state_v7(').replace('spot_prepare(', 'spot_prepare_v7(').replace('spot_arm(', 'spot_arm_v7(')
            for role in ('life_pg2_runtime', 'life_pg2_reporter'):
                assert not await connection.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, renamed)
                assert not await connection.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, signature)
        assert (await migrate(connection, action='up'))['applied'] == []
        assert await connection.fetchval('SELECT count(*) FROM life_financial.spot_session_policies') == 0
    finally:
        await connection.close()
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()
