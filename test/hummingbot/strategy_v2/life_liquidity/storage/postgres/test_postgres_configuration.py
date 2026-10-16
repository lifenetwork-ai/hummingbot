"""Configuration audit does not grant activation or change financial policy."""
import json
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import spot

import pytest

from controllers.generic.life_liquidity import LifeLiquidityConfig
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.postgres.configuration import PgConfigurationRepository
from hummingbot.strategy_v2.life_liquidity.storage.recorder_configuration import ConfigurationReport


async def setup(database, *, startup_kill=False):
    template = LifeLiquidityConfig(id='controller1', storage_backend='postgres', manual_kill_switch=startup_kill)
    digest = fingerprint(template.strategy.model_dump(mode='json'))

    def imported(binding):
        config = template.model_copy(update={'storage_binding': binding})
        return {'controller': config.model_dump(mode='json')}
    financial, cfg, owner = await spot(database, config_hash=digest, configuration_body=imported)
    config = template.model_copy(update={'storage_binding': cfg.binding})
    repo = PgConfigurationRepository(financial, controller_id=config.id, max_rows=32, max_read_bytes=1048576)
    at = await database[0].fetchval('SELECT floor(extract(epoch FROM clock_timestamp())*1000)::bigint')
    command = ConfigurationReport('startup', cfg.binding, config.id, 0, 1, config.model_dump_json(), cfg.binding.policy_hash,
                                  fingerprint(config.model_dump(mode='json')), 'INITIAL', 'CONFIG_STARTUP', at)
    return financial, cfg, owner, repo, config, command


@pytest.mark.asyncio
async def test_initial_configuration_atomic_delivery_replay_and_cold_readback(database):
    financial, cfg, owner, repo, config, command = await setup(database)
    try:
        before = await financial.snapshot(cfg.binding)
        assert (await repo.configuration(cfg.binding)).sequence == 0
        await repo.stage(command, cut=before.cut, owner=owner)
        assert len(await repo.pending(cfg.binding)) == 1
        assert (await financial.snapshot(cfg.binding)).recovery_required
        with pytest.raises(StorageError, match='PG_CONFIG_DELIVERY_PENDING'):
            await repo.configuration(cfg.binding)
        result = await repo.apply(command.operation_id, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert result.sequence == 1 and not result.trading_permission
        assert (await repo.stage(command, cut=before.cut, owner=owner)).replayed
        assert (await repo.apply(command.operation_id, cut=before.cut, owner=owner)).replayed
        cold = PgConfigurationRepository(financial, controller_id=config.id, max_rows=32, max_read_bytes=1048576)
        state = await cold.configuration(cfg.binding)
        assert state.sequence == state.active_revision == 1
        assert json.loads(state.controller_json) == json.loads(config.model_dump_json())
        assert not state.rejection_latched and not state.kill_latched
        assert await repo.pending(cfg.binding) == ()
        assert await financial.balance(cfg.binding) == before.balance
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."Controllers"') == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['REJECTED', 'APPLIED'])
async def test_rejected_or_killed_configuration_latches_halt_and_never_resumes(database, outcome):
    financial, cfg, owner, repo, config, command = await setup(database)
    try:
        await repo.project(command, owner=owner)
        if outcome == 'APPLIED':
            config = config.model_copy(update={'manual_kill_switch': True})
        updated = replace(command, operation_id='update', expected_sequence=1, active_revision=1 if outcome == 'REJECTED' else 2,
                          controller_json=config.model_dump_json(), outcome=outcome,
                          reason_code='CONFIG_VALIDATION_FAILED' if outcome == 'REJECTED' else 'CONFIG_UPDATE_APPLIED')
        await repo.project(updated, owner=owner)
        state = await repo.configuration(cfg.binding)
        assert state.rejection_latched is (outcome == 'REJECTED') and state.kill_latched is (outcome == 'APPLIED')
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        # A later audit report cannot undo either financial safety latch.
        config = config.model_copy(update={'manual_kill_switch': False})
        follow = replace(updated, operation_id='follow', expected_sequence=2, controller_json=config.model_dump_json(),
                         active_revision=3 if outcome == 'APPLIED' else 1,
                         outcome='APPLIED' if outcome == 'APPLIED' else 'UNCHANGED',
                         reason_code='CONFIG_UPDATE_APPLIED' if outcome == 'APPLIED' else 'CONFIG_UNCHANGED')
        await repo.project(follow, owner=owner)
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        assert (await repo.configuration(cfg.binding)).rejection_latched is (outcome == 'REJECTED')
        assert not (await repo.project(command, owner=owner)).trading_permission
        assert (await repo.configuration(cfg.binding)).sequence == 3
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['sequence', 'revision', 'time', 'policy', 'config', 'backend'])
async def test_invalid_configuration_does_not_change_any_head_or_money(database, fault):
    financial, cfg, owner, repo, config, command = await setup(database)
    try:
        await repo.project(command, owner=owner)
        next_command = replace(command, operation_id='bad', expected_sequence=1, outcome='UNCHANGED', reason_code='CONFIG_UNCHANGED')
        if fault == 'sequence':
            next_command = replace(next_command, expected_sequence=0)
        elif fault == 'revision':
            next_command = replace(next_command, active_revision=9)
        elif fault == 'time':
            next_command = replace(next_command, timestamp_ms=command.timestamp_ms - 1)
        elif fault == 'policy':
            next_command = replace(next_command, policy_hash='e' * 64)
        elif fault == 'config':
            next_command = replace(next_command, controller_json=config.model_copy(update={'total_amount_quote': D('4')}).model_dump_json())
        else:
            next_command = replace(next_command, controller_json=config.model_copy(update={'storage_backend': 'file'}).model_dump_json())
        before = await financial.snapshot(cfg.binding)
        with pytest.raises(StorageError):
            await repo.stage(next_command, cut=before.cut, owner=owner)
        assert (await financial.snapshot(cfg.binding)).cut == before.cut
        assert await financial.balance(cfg.binding) == before.balance
        assert (await repo.configuration(cfg.binding)).sequence == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_configuration_and_executor_streams_coexist_and_late_ack_remains_available(database):
    from datetime import datetime, timezone
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_quote_transport import transport
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import ack

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.recorder import PgRecorderRepository
    from hummingbot.strategy_v2.life_liquidity.storage.recorder import ExecutorProjection
    from hummingbot.strategy_v2.models.base import RunnableStatus
    from hummingbot.strategy_v2.models.executors_info import ExecutorInfo
    template = LifeLiquidityConfig(id='controller1', storage_backend='postgres')
    digest = fingerprint(template.strategy.model_dump(mode='json'))
    t = await transport(database, config_hash=digest, configuration_body=lambda binding: {
        'controller': template.model_copy(update={'storage_binding': binding}).model_dump(mode='json')})
    repo = PgConfigurationRepository(t.repo, controller_id=t.row.controller_id, max_rows=32, max_read_bytes=1048576)
    executors = PgRecorderRepository(t.repo, controller_id=t.row.controller_id, max_rows=32, max_read_bytes=1048576)
    try:
        config = template.model_copy(update={'storage_binding': t.cfg.binding})
        at = int(datetime.now(timezone.utc).timestamp() * 1000)
        command = ConfigurationReport('config-start', t.cfg.binding, config.id, 0, 1, config.model_dump_json(), t.cfg.binding.policy_hash,
                                      digest, 'INITIAL', 'CONFIG_STARTUP', at)
        await repo.project(command, owner=t.owner)
        info = ExecutorInfo(id=t.row.intent_id, timestamp=t.create.executor_config.timestamp, type='order_executor',
                            controller_id=t.row.controller_id, config=t.create.executor_config.model_copy(deep=True),
                            status=RunnableStatus.NOT_STARTED, net_pnl_pct=D('0'), net_pnl_quote=D('0'), cum_fees_quote=D('0'),
                            filled_amount_quote=D('0'), is_active=False, is_trading=False,
                            custom_info={'recovery_account_uid': t.cfg.binding.account_uid, 'recovery_order_ids': []})
        await executors.project(ExecutorProjection.from_info('report-start', t.cfg.binding, info), owner=t.owner)
        assert len(await executors.read(t.cfg.binding)) == 1
        assert (await repo.configuration(t.cfg.binding)).sequence == 1
        await t.repo.arm('operation1', cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
        updated = replace(command, operation_id='config-same', expected_sequence=1, outcome='UNCHANGED', reason_code='CONFIG_UNCHANGED')
        await repo.stage(updated, cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
        before = await t.repo.balance(t.cfg.binding)
        observed = await t.repo.acknowledge(ack(t.owner, datetime.now(timezone.utc)), cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
        assert observed.state == 'ACKED' and await t.repo.balance(t.cfg.binding) == before
        await repo.apply(updated.operation_id, cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
        assert not (await t.repo.snapshot(t.cfg.binding)).recovery_required
        assert (await repo.configuration(t.cfg.binding)).sequence == 2
        assert len(await executors.read(t.cfg.binding)) == 1
        assert not t.session.sent
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['stage', 'apply'])
@pytest.mark.parametrize('committed', [False, True])
async def test_real_backend_termination_at_configuration_commit_and_original_repair(database, monkeypatch, phase, committed):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    financial, cfg, owner, repo, config, command = await setup(database)
    if phase == 'apply':
        await repo.stage(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
    before = await financial.snapshot(cfg.binding)
    original = financial._runtime._commit
    admin = await asyncpg.connect(database[1])

    async def lost(tx):
        pid = tx._connection.get_server_pid()
        if committed:
            await original(tx)
        assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid)
        raise ConnectionError('synthetic lost configuration COMMIT reply')
    monkeypatch.setattr(financial._runtime, '_commit', lost)
    try:
        with pytest.raises(CommitUnknown):
            if phase == 'stage':
                await repo.stage(command, cut=before.cut, owner=owner)
            else:
                await repo.apply(command.operation_id, cut=before.cut, owner=owner)
    finally:
        await admin.close()
        await financial.close()
    cold_financial = PgSpotRepository(await PgRuntime.open(cfg))
    cold = PgConfigurationRepository(cold_financial, controller_id=config.id, max_rows=32, max_read_bytes=1048576)
    try:
        assert len(await cold.pending(cfg.binding)) == int(committed if phase == 'stage' else not committed)
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."Controllers"') == int(phase == 'apply' and committed)
        await cold.project(command, owner=owner)
        assert (await cold.configuration(cfg.binding)).sequence == 1
        assert await cold_financial.balance(cfg.binding) == before.balance
        assert await cold.pending(cfg.binding) == ()
    finally:
        await cold_financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['stage', 'apply'])
async def test_generation_change_at_physical_commit_rolls_back_configuration(database, monkeypatch, phase):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import install_generation_fault
    financial, cfg, owner, repo, config, command = await setup(database)
    await install_generation_fault(database)
    try:
        if phase == 'apply':
            await repo.stage(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        before = await financial.snapshot(cfg.binding)
        original = financial._runtime._commit

        async def changed(tx):
            await tx._connection.execute('SELECT life_financial.test_change_generation($1)', cfg.account_id)
            await original(tx)
        monkeypatch.setattr(financial._runtime, '_commit', changed)
        with pytest.raises(StorageError, match='OWNER_EXPIRED_OR_CHANGED_AT_COMMIT'):
            if phase == 'stage':
                await repo.stage(command, cut=before.cut, owner=owner)
            else:
                await repo.apply(command.operation_id, cut=before.cut, owner=owner)
        assert (await financial.snapshot(cfg.binding)).cut == before.cut
        assert await financial.balance(cfg.binding) == before.balance
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."Controllers"') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['legacy', 'outbox', 'receipt', 'operation'])
async def test_configuration_corruption_denies_cold_readback_and_financial_writes(database, fault):
    financial, cfg, owner, repo, config, command = await setup(database)
    try:
        await repo.project(command, owner=owner)
        if fault == 'legacy':
            await database[0].execute('UPDATE life_recorder."Controllers" SET type=\'foreign\'')
        else:
            table = {'outbox': 'recorder_outbox', 'receipt': 'projection_receipts', 'operation': 'operations'}[fault]
            await database[0].execute(f'ALTER TABLE life_financial.{table} DISABLE TRIGGER USER')
            if fault == 'outbox':
                await database[0].execute("UPDATE life_financial.recorder_outbox SET source_hash=repeat('e',64)")
            elif fault == 'receipt':
                await database[0].execute("UPDATE life_financial.projection_receipts SET applied_hash=repeat('e',64)")
            else:
                await database[0].execute("UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{sequence}','999') WHERE operation_id='startup'")
            await database[0].execute(f'ALTER TABLE life_financial.{table} ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='PG_RECORDER_UNVERIFIED'):
            await repo.configuration(cfg.binding)
        assert (await financial.snapshot(cfg.binding)).recovery_required
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['missing', 'null', 'backend', 'reason', 'binding', 'registered'])
async def test_raw_sql_configuration_rejects_malformed_or_unregistered_terms(database, fault):
    import asyncpg
    financial, cfg, owner, repo, config, command = await setup(database)
    c = await asyncpg.connect(cfg.dsn)
    try:
        value = command.json()
        if fault == 'missing':
            value.pop('outcome')
        elif fault == 'null':
            value['outcome'] = None
        elif fault == 'backend':
            value['controller_json'] = config.model_copy(update={'storage_backend': 'file'}).model_dump_json()
        elif fault == 'reason':
            value['reason_code'] = 'CONFIG_UNCHANGED'
        elif fault == 'binding':
            value['binding'] = None
        else:
            value['controller_json'] = config.model_copy(update={'total_amount_quote': D('3')}).model_dump_json()
        before = await financial.snapshot(cfg.binding)
        with pytest.raises(asyncpg.RaiseError, match='CONFIG_'):
            await c.fetchval('SELECT life_financial.recorder_config_stage($1,$2::jsonb,$3::jsonb,$4,$5,$6,$7::jsonb)',
                             cfg.account_id, json.dumps(cfg.binding.json()), json.dumps(owner.json()), before.cut.checkpoint.revision,
                             before.cut.checkpoint.state_digest, 'f' * 64, json.dumps(value))
        assert (await financial.snapshot(cfg.binding)).cut == before.cut
    finally:
        await c.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['stage', 'apply'])
@pytest.mark.parametrize('committed', [False, True])
async def test_real_process_exit_before_after_configuration_commit(database, phase, committed):
    import asyncio
    import sys
    from dataclasses import fields

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    financial, cfg, owner, repo, config, command = await setup(database)
    if phase == 'apply':
        await repo.stage(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
    before = await financial.snapshot(cfg.binding)
    values = {f.name: getattr(cfg, f.name) for f in fields(cfg)}
    values.update(account_id=str(cfg.account_id), database_lineage=str(cfg.database_lineage), binding=cfg.binding.json())
    data = json.dumps(dict(config=values, command=command.json(), owner=owner.json(), cut=before.cut.json(), phase=phase, committed=committed))
    child = r'''
import asyncio,json,os,sys
from uuid import UUID
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken,StateBinding
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.recorder_configuration import ConfigurationReport
from hummingbot.strategy_v2.life_liquidity.storage.postgres.configuration import PgConfigurationRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig,PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
async def run():
    data=json.load(sys.stdin);cfg=data['config'];cfg['binding']=StateBinding.from_json(cfg['binding'])
    for key in ('account_id','database_lineage'):cfg[key]=UUID(cfg[key])
    financial=PgSpotRepository(await PgRuntime.open(PgConfig(**cfg)))
    repo=PgConfigurationRepository(financial,controller_id='controller1',max_rows=32,max_read_bytes=1048576)
    command=ConfigurationReport.from_json(data['command']);original=financial._runtime._commit
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
            _, stderr = await asyncio.wait_for(process.communicate(data.encode()), 15)
        except BaseException:
            process.kill()
            await process.wait()
            raise
        assert process.returncode == 73, stderr.decode()
    finally:
        await financial.close()
    cold_financial = PgSpotRepository(await PgRuntime.open(cfg))
    cold = PgConfigurationRepository(cold_financial, controller_id=config.id, max_rows=32, max_read_bytes=1048576)
    try:
        assert len(await cold.pending(cfg.binding)) == int(committed if phase == 'stage' else not committed)
        assert await database[0].fetchval('SELECT count(*) FROM life_recorder."Controllers"') == int(phase == 'apply' and committed)
        await cold.project(command, owner=owner)
        assert (await cold.configuration(cfg.binding)).sequence == 1
        assert await cold_financial.balance(cfg.binding) == before.balance
    finally:
        await cold_financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('limit', ['rows', 'bytes'])
async def test_configuration_history_capacity_is_not_silently_truncated(database, limit):
    financial, cfg, owner, repo, config, command = await setup(database)
    try:
        await repo.project(command, owner=owner)
        follow = replace(command, operation_id='same', expected_sequence=1, outcome='UNCHANGED', reason_code='CONFIG_UNCHANGED')
        await repo.project(follow, owner=owner)
        bounded = PgConfigurationRepository(financial, controller_id=config.id, max_rows=1 if limit == 'rows' else 32,
                                            max_read_bytes=8 if limit == 'bytes' else 1048576)
        with pytest.raises(StorageError, match='PG_RECORDER_(BYTE_)?CAPACITY'):
            await bounded.configuration(cfg.binding)
        assert (await repo.configuration(cfg.binding)).sequence == 2
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_reporting_role_reads_configuration_but_cannot_write(database):
    from uuid import uuid4

    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    financial, cfg, owner, repo, config, command = await setup(database)
    reporting = None
    try:
        await repo.project(command, owner=owner)
        read_cfg = replace(cfg, dsn=cfg.dsn.replace('life_pg2_runtime@', 'life_pg2_reporter@'), principal='life_pg2_reporter', write=False)
        reporting = PgSpotRepository(await PgRuntime.open(read_cfg))
        reader = PgConfigurationRepository(reporting, controller_id=config.id, max_rows=32, max_read_bytes=1048576)
        assert (await reader.configuration(cfg.binding)).sequence == 1
        with pytest.raises(StorageError, match='PG_SPOT_COMMAND_INVALID'):
            await reader.stage(command, cut=(await reporting.snapshot(cfg.binding)).cut, owner=owner)
        c = await asyncpg.connect(read_cfg.dsn)
        try:
            for signature in ('recorder_config_stage(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
                              'recorder_config_apply(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.identity)',
                              'recorder_config_integrity(uuid)', 'recorder_executor_only_integrity(uuid)'):
                assert not await c.fetchval('SELECT has_function_privilege(current_user,$1,\'EXECUTE\')', 'life_financial.' + signature)
            with pytest.raises(asyncpg.RaiseError, match='ACCOUNT_SCOPE_INVALID'):
                await c.fetchval('SELECT life_financial.recorder_executor_verified($1)', uuid4())
        finally:
            await c.close()
    finally:
        if reporting is not None:
            await reporting.close()
        await financial.close()


@pytest.mark.asyncio
async def test_imported_startup_kill_cannot_be_silently_cleared(database):
    import asyncpg
    financial, cfg, owner, repo, config, command = await setup(database, startup_kill=True)
    try:
        before = await financial.snapshot(cfg.binding)
        disabled = replace(command, controller_json=config.model_copy(update={'manual_kill_switch': False}).model_dump_json())
        with pytest.raises(StorageError, match='PG_CONFIG_INITIAL_INVALID'):
            await repo.stage(disabled, cut=before.cut, owner=owner)
        assert (await financial.snapshot(cfg.binding)).cut == before.cut
        await repo.project(command, owner=owner)
        assert (await repo.configuration(cfg.binding)).kill_latched
        with pytest.raises(asyncpg.RaiseError, match='RECORDER_PROJECTION_UNVERIFIED'):
            await database[0].execute('UPDATE life_financial.accounts SET halted=false WHERE account_id=$1', cfg.account_id)
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await financial.close()
