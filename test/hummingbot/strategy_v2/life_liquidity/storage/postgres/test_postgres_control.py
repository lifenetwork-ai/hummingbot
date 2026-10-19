"""Signed native control changes; synthetic operator and disposable PostgreSQL."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order, spot
from uuid import uuid4

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerMode, OwnerToken, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorKeys, OwnerTransition
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts


async def setup(database, *, successor_duration_ms=60000, control_max_records=32, control_max_bytes=1048576, **spot_changes):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import PgControlRepository
    keys = OperatorKeys({'operator': b'x' * 32}, {'operator': frozenset(('ACTIVATE', 'DRAIN', 'REVOKE', 'RECOVER', 'OBSERVE_SESSION'))})
    financial, cfg, owner = await spot(database, configuration_body=lambda _: {'operator_verifier': keys.verifier().policy()}, **spot_changes)
    c = database[0]
    await c.execute('INSERT INTO life_financial.access_bindings VALUES(current_user,$1,true)', cfg.account_id)
    await c.execute('INSERT INTO life_financial.control_policies VALUES($1,$2,1000,$3,$4,$5)', cfg.account_id, cfg.binding.policy_hash, control_max_records, control_max_bytes, successor_duration_ms)
    control = PgControlRepository(c, financial, verifier=keys.verifier())
    return financial, cfg, owner, keys, control


async def evidence(database, financial, cfg):
    view = await financial.snapshot(cfg.binding)
    now = await database[0].fetchval('SELECT clock_timestamp()')
    trades = tuple(row['trade_id'] for row in await database[0].fetch('SELECT DISTINCT trade_id FROM life_financial.fills WHERE account_id=$1 ORDER BY trade_id', cfg.account_id))
    bills = tuple(row['bill_id'] for row in await database[0].fetch('SELECT bill_id FROM life_financial.cashflows WHERE account_id=$1 ORDER BY bill_id', cfg.account_id))
    facts = SpotFinancialRecoveryFacts(cfg.binding, now, view.balance.total_base_life, view.balance.total_quote_usdt,
                                       D('0'), D('0'), trades, bills, (), (), True, True, True)
    return view.cut, facts


async def signed(database, financial, cfg, keys, command):
    cut, facts = await evidence(database, financial, cfg)
    approval = keys.sign('operator', cfg.binding, command.role, command.json(), cut, facts,
                         issued_at=facts.observed_at - timedelta(seconds=1), expires_at=facts.observed_at + timedelta(seconds=5))
    return dict(approval=approval, cut=cut, facts=facts)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', [OwnerMode.DRAINING, OwnerMode.RECOVERY_ONLY, OwnerMode.UNARMED])
async def test_signed_control_revokes_risk_preserves_money_and_cold_readback_never_activates(database, mode):
    financial, cfg, owner, keys, control = await setup(database)
    try:
        before = await financial.snapshot(cfg.binding)
        command = OwnerTransition('revoke', mode, None if mode is OwnerMode.UNARMED else owner)
        arguments = await signed(database, financial, cfg, keys, command)
        receipt = await control.transition_owner(command, **arguments)
        assert receipt.mode is mode and not receipt.replayed and not receipt.trading_permission
        assert (await control.transition_owner(command, **arguments)).replayed
        assert (await control.lookup('revoke')).replayed
        after = await financial.snapshot(cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
        # Revocation retains the original observation writer/session, even when
        # logical control is UNARMED. It cannot orphan late financial facts.
        assert after.session.session_id == str(owner.session_id)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.activation_grants') == 0
        with pytest.raises(StorageError):
            await financial.prepare(order(owner), cut=after.cut, owner=owner)
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('trailing_zero', [False, True])
async def test_new_signed_activation_consumes_one_generation_grant_and_never_refunds_or_renews_on_replay(database, trailing_zero):
    financial, cfg, owner, keys, control = await setup(database)
    try:
        before = await financial.snapshot(cfg.binding)
        drain = OwnerTransition('drain', OwnerMode.DRAINING, owner)
        await control.transition_owner(drain, **await signed(database, financial, cfg, keys, drain))
        now = await database[0].fetchval('SELECT clock_timestamp()')
        replacement = OwnerToken(uuid4(), uuid4(), owner.epoch, owner.session_id, now, now + timedelta(seconds=10))
        if trailing_zero:
            replacement = replace(replacement, expires_at=replacement.expires_at.replace(microsecond=100000))
        command = OwnerTransition('activate', OwnerMode.ACTIVE, replacement)
        arguments = await signed(database, financial, cfg, keys, command)
        receipt = await control.transition_owner(command, **arguments)
        assert not receipt.trading_permission and receipt.token == replacement and not receipt.replayed
        assert (await control.transition_owner(command, **arguments)).replayed
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.activation_grants') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.grant_consumptions') == 1
        after = await financial.snapshot(cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
        with pytest.raises(StorageError):
            await financial.prepare(order(owner), cut=after.cut, owner=owner)
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        try:
            await cold.prepare(order(replacement), cut=after.cut, owner=replacement)
        finally:
            await cold.close()
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['signature', 'role', 'facts', 'stale', 'policy', 'old-generation', 'halt', 'incomplete', 'foreign-wire', 'liability'])
async def test_unqualified_activation_changes_no_ownership_grant_money_or_budget(database, fault):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import PgControlRepository
    financial, cfg, owner, keys, control = await setup(database)
    try:
        now = await database[0].fetchval('SELECT clock_timestamp()')
        token = OwnerToken(uuid4(), uuid4(), owner.epoch, owner.session_id, now, now + timedelta(seconds=10))
        if fault == 'old-generation':
            token = replace(token, generation=owner.generation)
        if fault == 'halt':
            await database[0].execute('UPDATE life_financial.accounts SET halted=true WHERE account_id=$1', cfg.account_id)
        command = OwnerTransition('activate', OwnerMode.ACTIVE, token)
        args = await signed(database, financial, cfg, keys, command)
        if fault == 'signature':
            args['approval'] = replace(args['approval'], signature='0' * 128)
        elif fault == 'role':
            args['approval'] = replace(args['approval'], role='DRAIN')
        elif fault in ('facts', 'stale', 'incomplete', 'foreign-wire', 'liability'):
            changes = {'facts': dict(account_quote_usdt=D('11')), 'stale': dict(observed_at=now - timedelta(seconds=2)),
                       'incomplete': dict(bill_scope_complete=False), 'foreign-wire': dict(runner_wire_ids=('foreign',)),
                       'liability': dict(liability_quote_usdt=D('.01'))}[fault]
            args['facts'] = replace(args['facts'], **changes)
            args['approval'] = keys.sign('operator', cfg.binding, command.role, command.json(), args['cut'], args['facts'],
                                         issued_at=now - timedelta(seconds=1), expires_at=now + timedelta(seconds=5))
        elif fault == 'policy':
            other = OperatorKeys({'operator': b'y' * 32}, keys.roles)
            control = PgControlRepository(database[0], financial, verifier=other.verifier())
        before = await database[0].fetchval('SELECT to_jsonb(a) FROM life_financial.accounts a')
        with pytest.raises(StorageError):
            await control.transition_owner(command, **args)
        assert await database[0].fetchval('SELECT to_jsonb(a) FROM life_financial.accounts a') == before
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.control_records') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.activation_grants') == 0
        assert not financial._runtime.healthy
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_runtime_cannot_write_control_or_self_activate(database):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import PgControlRepository
    financial, cfg, owner, keys, _ = await setup(database)
    c = await asyncpg.connect(cfg.dsn)
    try:
        command = OwnerTransition('drain', OwnerMode.DRAINING, owner)
        arguments = await signed(database, financial, cfg, keys, command)
        with pytest.raises(StorageError, match='PG_CONTROL_OWNER_REQUIRED'):
            await PgControlRepository(c, financial, verifier=keys.verifier()).transition_owner(command, **arguments)
        for table in ('control_policies', 'control_records', 'activation_grants', 'grant_consumptions'):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.execute('DELETE FROM life_financial.' + table)
        for signature in ('control_verified(uuid)', 'control_commit_check(uuid,text)', 'control_facts_qualified(uuid,jsonb)'):
            assert not await c.fetchval("SELECT has_function_privilege(current_user,$1,'EXECUTE')", 'life_financial.' + signature)
    finally:
        await c.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_lost_control_commit_recovers_original_signature_and_receipt_without_new_grant(database, monkeypatch, committed):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import PgControlRepository
    financial, cfg, owner, keys, control = await setup(database)
    admin = await asyncpg.connect(database[1])
    cold_c = None
    try:
        command = OwnerTransition('drain', OwnerMode.DRAINING, owner)
        args = await signed(database, financial, cfg, keys, command)
        original = control._commit

        async def lost(tx):
            pid = tx._connection.get_server_pid()
            if committed:
                await original(tx)
            assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid)
            raise ConnectionError('synthetic lost control COMMIT reply')
        monkeypatch.setattr(control, '_commit', lost)
        with pytest.raises(CommitUnknown, match='PG_CONTROL_COMMIT_UNKNOWN'):
            await control.transition_owner(command, **args)
        cold_c = await asyncpg.connect(database[2])
        cold = PgControlRepository(cold_c, financial, verifier=keys.verifier())
        assert (await cold.lookup('drain') is not None) is committed
        receipt = await cold.transition_owner(command, **args)
        assert receipt.replayed is committed
        assert await cold_c.fetchval('SELECT count(*) FROM life_financial.control_records') == 1
        assert await cold_c.fetchval('SELECT count(*) FROM life_financial.activation_grants') == 0
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
    finally:
        if cold_c is not None:
            await cold_c.close()
        await admin.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['generation', 'approval-expiry'])
async def test_control_rechecks_original_state_and_signature_deadline_at_physical_commit(database, monkeypatch, fault):
    financial, cfg, owner, keys, control = await setup(database)
    try:
        command = OwnerTransition('drain', OwnerMode.DRAINING, owner)
        args = await signed(database, financial, cfg, keys, command)
        if fault == 'approval-expiry':
            args['approval'] = keys.sign('operator', cfg.binding, command.role, command.json(), args['cut'], args['facts'],
                                         issued_at =args['facts'].observed_at - timedelta(seconds=1),
                                         expires_at=args['facts'].observed_at + timedelta(milliseconds=350))
        original = control._commit
        reached = False

        async def changed(tx):
            nonlocal reached
            reached = True
            if fault == 'generation':
                await tx._connection.execute('UPDATE life_financial.accounts SET owner_generation=$1 WHERE account_id=$2', uuid4(), cfg.account_id)
            else:
                await tx._connection.execute('SELECT pg_sleep(greatest(0,extract(epoch FROM ($1::timestamptz-clock_timestamp())))+.02)', args['approval'].expires_at)
            await original(tx)
        monkeypatch.setattr(control, '_commit', changed)
        before = await database[0].fetchval('SELECT to_jsonb(a) FROM life_financial.accounts a')
        with pytest.raises(StorageError, match='CONTROL_EXPIRED_OR_CHANGED_AT_COMMIT'):
            await control.transition_owner(command, **args)
        assert reached
        assert await database[0].fetchval('SELECT to_jsonb(a) FROM life_financial.accounts a') == before
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.control_records') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_cancelled_caller_keeps_control_connection_owned_until_whole_effect_finishes(database, monkeypatch, committed):
    import asyncio
    financial, cfg, owner, keys, control = await setup(database)
    finish, entered = asyncio.Event(), asyncio.Event()
    try:
        command = OwnerTransition('drain', OwnerMode.DRAINING, owner)
        args = await signed(database, financial, cfg, keys, command)
        original = control._commit

        async def wait(tx):
            if committed:
                await original(tx)
            entered.set()
            await finish.wait()
            if not committed:
                await original(tx)
        monkeypatch.setattr(control, '_commit', wait)
        task = asyncio.create_task(control.transition_owner(command, **args))
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert control._tasks and control._mutex.locked() and not financial._runtime.healthy
        with pytest.raises(StorageError, match='PG_CONTROL_BACKPRESSURE'):
            await control.lookup('drain')
        finish.set()
        await control.close()
        assert (await control.lookup('drain')).replayed
        assert (await control.transition_owner(command, **args)).replayed
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.control_records') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.activation_grants') == 0
    finally:
        finish.set()
        await control.close()
        await financial.close()


@pytest.mark.asyncio
async def test_revoke_retains_original_held_order_and_allows_qualified_late_ack_and_fill_without_activation(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import filled

    from hummingbot.strategy_v2.life_liquidity.storage.financial import AckSpot
    financial, cfg, owner, keys, control = await setup(database)
    try:
        await database[0].execute('INSERT INTO life_financial.spot_fill_policies VALUES($1,$2,1000,4096)', cfg.account_id, cfg.binding.policy_hash)
        await financial.prepare(order(owner), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        await financial.arm('operation1', cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        before = await financial.snapshot(cfg.binding)
        command = OwnerTransition('revoke', OwnerMode.UNARMED, None)
        await control.transition_owner(command, **await signed(database, financial, cfg, keys, command))
        assert (await financial.snapshot(cfg.binding)).budget == before.budget
        now = await database[0].fetchval('SELECT clock_timestamp()')
        ack = AckSpot('lateack', 'operation1', 'intent1', 'wire1', 'LIFE-USDT', 'BUY', str(owner.session_id), owner.epoch, '123456789', now)
        await financial.acknowledge(ack, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        await financial.apply_fills(filled(owner, now), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        after = await financial.snapshot(cfg.binding)
        assert after.balance.total_base_life == D('6') and after.balance.total_quote_usdt == D('8.98')
        assert await database[0].fetchval('SELECT mode FROM life_financial.accounts') == 'RECOVERY_ONLY'
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.activation_grants') == 0
        assert (await control.lookup('revoke')).mode is OwnerMode.UNARMED
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['record', 'operation', 'grant'])
async def test_cold_control_history_corruption_denies_next_command_and_preserves_money(database, fault):
    financial, cfg, owner, keys, control = await setup(database)
    try:
        now = await database[0].fetchval('SELECT clock_timestamp()')
        token = replace(owner, generation=uuid4(), process_id=uuid4(), valid_from=now)
        command = OwnerTransition('activate', OwnerMode.ACTIVE, token)
        await control.transition_owner(command, **await signed(database, financial, cfg, keys, command))
        c = database[0]
        table = {'record': 'control_records', 'operation': 'operations', 'grant': 'activation_grants'}[fault]
        await c.execute('ALTER TABLE life_financial.' + table + ' DISABLE TRIGGER USER')
        if fault == 'record':
            await c.execute("UPDATE life_financial.control_records SET body=jsonb_set(body,'{signature}',to_jsonb(repeat('0',128)))")
        elif fault == 'operation':
            await c.execute("UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{mode}','\"DRAINING\"') WHERE operation_id='activate'")
        else:
            await c.execute('UPDATE life_financial.activation_grants SET signature=$1', b'wrong-original-signature')
        await c.execute('ALTER TABLE life_financial.' + table + ' ENABLE TRIGGER USER')
        money = await c.fetch('SELECT * FROM life_financial.balances ORDER BY asset')
        assert (await financial.snapshot(cfg.binding)).recovery_required
        with pytest.raises(StorageError, match='PG_CONTROL_UNVERIFIED'):
            await control.lookup('activate')
        with pytest.raises(StorageError):
            await financial.prepare(order(token), cut=(await financial.snapshot(cfg.binding)).cut, owner=token)
        assert money == await c.fetch('SELECT * FROM life_financial.balances ORDER BY asset')
        assert await c.fetchval('SELECT count(*) FROM life_financial.control_records') == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('bound', ['records', 'bytes'])
async def test_control_capacity_rejects_atomically_without_rewriting_original_history(database, bound):
    financial, cfg, owner, keys, control = await setup(database, control_max_records=1 if bound == 'records' else 32,
                                                       control_max_bytes=1048576 if bound == 'records' else 1)
    try:
        first = OwnerTransition('first', OwnerMode.DRAINING, owner)
        if bound == 'records':
            await control.transition_owner(first, **await signed(database, financial, cfg, keys, first))
            first = OwnerTransition('second', OwnerMode.RECOVERY_ONLY, owner)
        before = await database[0].fetchval('SELECT to_jsonb(a) FROM life_financial.accounts a')
        with pytest.raises(StorageError, match='PG_CONTROL_BOUND'):
            await control.transition_owner(first, **await signed(database, financial, cfg, keys, first))
        assert before == await database[0].fetchval('SELECT to_jsonb(a) FROM life_financial.accounts a')
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.control_records') == (1 if bound == 'records' else 0)
        if bound == 'records':
            assert (await control.lookup('first')).replayed
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_control_process_exit_keeps_exact_original_receipt_and_no_new_permission(database, committed):
    import asyncio
    import json
    import sys
    from dataclasses import fields

    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import PgControlRepository
    financial, cfg, owner, keys, _ = await setup(database)
    config = {f.name: getattr(cfg, f.name) for f in fields(cfg)}
    config.update(account_id=str(cfg.account_id), database_lineage=str(cfg.database_lineage), binding=cfg.binding.json())
    # Signing/primary capture happens after child imports, under the unchanged
    # source-age limit. Only synthetic disposable DSNs cross stdin.
    child = r'''
import asyncio,json,os,sys,asyncpg
from uuid import UUID
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken,StateBinding,OwnerMode
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OwnerTransition,OperatorKeys
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig,PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import PgControlRepository
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_control import signed
async def run():
    data=json.load(sys.stdin);cfg=data['config'];cfg['binding']=StateBinding.from_json(cfg['binding'])
    for k in ('account_id','database_lineage'):cfg[k]=UUID(cfg[k])
    cfg=PgConfig(**cfg);repo=PgSpotRepository(await PgRuntime.open(cfg))
    c=await asyncpg.connect(data['owner_dsn']);owner=OwnerToken.from_json(data['owner'])
    keys=OperatorKeys({'operator':b'x'*32},{'operator':frozenset(('ACTIVATE','DRAIN','REVOKE','RECOVER','OBSERVE_SESSION'))})
    control=PgControlRepository(c,repo,verifier=keys.verifier());cmd=OwnerTransition('child_drain',OwnerMode.DRAINING,owner)
    args=await signed((c,),repo,cfg,keys,cmd);original=control._commit
    async def die(tx):
        if data['committed']:await original(tx)
        os._exit(73)
    control._commit=die
    await control.transition_owner(cmd,**args)
asyncio.run(run())
'''
    c = None
    try:
        payload = json.dumps(dict(config=config, owner=owner.json(), owner_dsn=database[2], committed=committed)).encode()
        process = await asyncio.create_subprocess_exec(sys.executable, '-c', child, stdin=asyncio.subprocess.PIPE,
                                                       stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            _, stderr = await asyncio.wait_for(process.communicate(payload), 15)
        except BaseException:
            process.kill()
            await process.wait()
            raise
        assert process.returncode == 73, stderr.decode()
        c = await asyncpg.connect(database[2])
        cold = PgControlRepository(c, financial, verifier=keys.verifier())
        receipt = await cold.lookup('child_drain')
        assert (receipt is not None) is committed
        if committed:
            assert receipt.replayed and not receipt.trading_permission and receipt.mode is OwnerMode.DRAINING
        assert await c.fetchval('SELECT mode FROM life_financial.accounts') == ('DRAINING' if committed else 'ACTIVE')
        assert await c.fetchval('SELECT count(*) FROM life_financial.activation_grants') == 0
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
    finally:
        if c is not None:
            await c.close()
        await financial.close()
