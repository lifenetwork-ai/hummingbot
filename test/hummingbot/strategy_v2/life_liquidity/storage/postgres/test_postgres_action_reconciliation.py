"""Release only the quote slot after proven financial closure; no money refund."""
import json
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_action_preparation import bridge, command
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_actions import transition
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_lifecycle import lifecycle, terminal
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import ack, filled

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.actions import ClaimQuoteActions
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


async def closed(database, *, stage='abort', side='BUY', action_fact_age_ms=1000):
    repo, cfg, owner, row = await bridge(database, side=side, action_fact_age_ms=action_fact_age_ms)
    await database[0].execute("INSERT INTO life_financial.spot_capital_policies VALUES($1,$2,5,10,1,$3,'100',4096)",
                              cfg.account_id, cfg.binding.policy_hash, owner.valid_from)
    await database[0].execute('INSERT INTO life_financial.spot_fill_policies VALUES($1,$2,1000,4096)', cfg.account_id, cfg.binding.policy_hash)
    await repo.prepare_quote_action(await command(database, cfg, owner, row), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    if stage == 'abort':
        await repo.apply_lifecycle(lifecycle(), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    elif stage != 'prepared':
        await repo.arm('operation1', cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        at = await database[0].fetchval('SELECT clock_timestamp()')
        fills = ()
        if stage == 'filled':
            cmd = filled(owner, at, side=side)
            await repo.apply_fills(cmd, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
            fills = cmd.fills
        if stage != 'unknown':
            await repo.acknowledge(ack(owner, at, side=side), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
            await repo.confirm_terminal(await terminal(repo, cfg, owner, side=side, fills=fills),
                                        cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    return repo, cfg, owner, row


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['abort', 'zero', 'filled'])
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
async def test_closed_order_action_reconciliation_releases_only_slot(database, stage, side):
    repo, cfg, owner, row = await closed(database, stage=stage, side=side)
    try:
        before = await repo.snapshot(cfg.binding)
        assert (await repo.quote_actions(cfg.binding))[0].state == 'PREPARED'
        with pytest.raises(StorageError, match='CLAIM_BOUND_PREPARATION_REQUIRED'):
            await repo.prepare(order(owner, side=side, operation_id='next', intent_id='next1', client_order_id='wire2'),
                               cut=before.cut, owner=owner)
        cmd = await transition(database, cfg, row, 'RECONCILED', expected='PREPARED', operation='close_action')
        result = await repo.transition_quote_action(cmd, cut=before.cut, owner=owner)
        assert result.states == (('intent1', 'RECONCILED'),)
        after = await repo.snapshot(cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        assert (await repo.transition_quote_action(cmd, cut=before.cut, owner=owner)).replayed
        assert (await repo.lookup_quote_action('close_action', cfg.binding)).states == result.states
        with pytest.raises(StorageError, match='CLAIM_BOUND_PREPARATION_REQUIRED'):
            await repo.prepare(order(owner, side=side, operation_id='old', client_order_id='wire2'), cut=after.cut, owner=owner)
        fresh = replace(row, intent_id='next1', payload_json=json.dumps({**json.loads(row.payload_json), 'id': 'next1'}))
        await repo.claim_quote_actions(ClaimQuoteActions('newbatch', (fresh,)), cut=after.cut, owner=owner)
        assert {r.action.intent_id: r.state for r in await repo.quote_actions(cfg.binding)} == {'intent1': 'RECONCILED', 'next1': 'PROPOSED'}
        assert (await repo.balance(cfg.binding)) == before.balance
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['prepared', 'unknown'])
async def test_unclosed_financial_attempt_cannot_free_action_slot(database, stage):
    repo, cfg, owner, row = await closed(database, stage=stage)
    try:
        before = await repo.snapshot(cfg.binding)
        cmd = await transition(database, cfg, row, 'RECONCILED', expected='PREPARED', operation='close_action')
        with pytest.raises(StorageError, match='ACTION_RECONCILIATION_UNPROVEN'):
            await repo.transition_quote_action(cmd, cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert (await repo.quote_actions(cfg.binding))[0].state == 'PREPARED'
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_live_runner_executor_prevents_slot_release_after_financial_close(database):
    repo, cfg, owner, row = await closed(database)
    try:
        before = await repo.snapshot(cfg.binding)
        cmd = await transition(database, cfg, row, 'RECONCILED', expected='PREPARED', operation='close_action', executor_ids=('intent1',))
        with pytest.raises(StorageError, match='ACTION_RUNNER_SCOPE_CONFLICT'):
            await repo.transition_quote_action(cmd, cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_runner_scope_captured_before_financial_closure_is_not_reusable(database):
    repo, cfg, owner, row = await closed(database, stage='prepared')
    try:
        at = await database[0].fetchval('SELECT clock_timestamp()')
        await repo.apply_lifecycle(lifecycle(), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        cmd = await transition(database, cfg, row, 'RECONCILED', expected='PREPARED', operation='close_action', observed_at=at)
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='ACTION_RUNNER_FACTS_BEFORE_CLOSURE'):
            await repo.transition_quote_action(cmd, cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['generation', 'process_id'])
async def test_new_writer_cannot_retire_previous_writer_action_without_handoff(database, field):
    from uuid import uuid4
    repo, cfg, owner, row = await closed(database)
    try:
        changed = replace(owner, **{field: uuid4()})
        column = 'owner_generation' if field == 'generation' else 'owner_process'
        await database[0].execute(f'UPDATE life_financial.accounts SET {column}=$1 WHERE account_id=$2', getattr(changed, field), cfg.account_id)
        cmd = await transition(database, cfg, row, 'RECONCILED', expected='PREPARED', operation='close_action')
        cut = (await repo.snapshot(cfg.binding)).cut
        with pytest.raises(StorageError, match='ACTION_ORIGINAL_WRITER_REQUIRED'):
            await repo.transition_quote_action(cmd, cut=cut, owner=changed)
        assert (await repo.quote_actions(cfg.binding))[0].state == 'PREPARED'
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_late_original_writer_slot_cleanup_does_not_reactivate_halted_account(database):
    repo, cfg, owner, row = await closed(database)
    try:
        expired = await database[0].fetchval("SELECT clock_timestamp()-interval '1 millisecond'")
        await database[0].execute("UPDATE life_financial.accounts SET owner_until=$1,halted=true,mode='RECOVERY_ONLY'", expired)
        owner = replace(owner, expires_at=expired)
        before = await repo.snapshot(cfg.binding)
        await repo.transition_quote_action(await transition(database, cfg, row, 'RECONCILED', expected='PREPARED', operation='close_action'),
                                           cut=before.cut, owner=owner)
        after = await repo.snapshot(cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget and after.session.state == 'PAUSED'
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        with pytest.raises(StorageError):
            await repo.prepare(order(owner, operation_id='next', intent_id='next1', client_order_id='wire2'), cut=after.cut, owner=owner)
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('corrupt', ['open_reservation', 'fabricated_reconciled'])
async def test_unproven_closed_action_or_remaining_hold_is_not_silently_accepted(database, corrupt):
    repo, cfg, owner, row = await closed(database)
    try:
        if corrupt == 'open_reservation':
            # Privileged corruption under the declared DB-owner trust model.
            await database[0].execute("INSERT INTO life_financial.reservations VALUES($1,'untracked','operation1','intent1','USDT',1,1,'HELD',0)", cfg.account_id)
            cmd = await transition(database, cfg, row, 'RECONCILED', expected='PREPARED', operation='close_action')
            with pytest.raises(StorageError, match='ACTION_RECONCILIATION_UNPROVEN'):
                await repo.transition_quote_action(cmd, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
            assert (await repo.quote_actions(cfg.binding))[0].state == 'PREPARED'
        else:
            await database[0].execute("UPDATE life_financial.quote_actions SET state='RECONCILED',version=version+1")
            assert (await repo.snapshot(cfg.binding)).recovery_required
            with pytest.raises(StorageError):
                await repo.prepare(order(owner, operation_id='next', intent_id='next1', client_order_id='wire2'),
                                   cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_same_operation_concurrency_is_one_slot_transition_without_refund(database):
    import asyncio
    repo, cfg, owner, row = await closed(database, stage='filled')
    try:
        cmd = await transition(database, cfg, row, 'RECONCILED', expected='PREPARED', operation='close_action')
        before = await repo.snapshot(cfg.binding)
        results = await asyncio.gather(repo.transition_quote_action(cmd, cut=before.cut, owner=owner),
                                       repo.transition_quote_action(cmd, cut=before.cut, owner=owner))
        assert sorted(result.replayed for result in results) == [False, True]
        assert await database[0].fetchval("SELECT count(*) FROM life_financial.spot_action_events WHERE operation_id='close_action'") == 1
        after = await repo.snapshot(cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        with pytest.raises(StorageError, match='OPERATION_CONFLICT'):
            await repo.transition_quote_action(replace(cmd, facts=replace(cmd.facts, executor_ids=('unrelated',))),
                                               cut=before.cut, owner=owner)
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_generation_changed_at_commit_rolls_back_slot_event_and_receipt(database, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import install_generation_fault
    repo, cfg, owner, row = await closed(database)
    await install_generation_fault(database)
    original = repo._runtime._commit

    async def changed(tx):
        await tx._connection.execute('SELECT life_financial.test_change_generation($1)', cfg.account_id)
        await original(tx)
    monkeypatch.setattr(repo._runtime, '_commit', changed)
    try:
        cmd = await transition(database, cfg, row, 'RECONCILED', expected='PREPARED', operation='close_action')
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='EXPIRED_OR_CHANGED_AT_COMMIT'):
            await repo.transition_quote_action(cmd, cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert (await repo.quote_actions(cfg.binding))[0].state == 'PREPARED'
        assert await repo.lookup_quote_action('close_action', cfg.binding) is None
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_actual_backend_termination_cold_retry_never_releases_money_twice(database, monkeypatch, committed):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner, row = await closed(database, stage='filled')
    cmd = await transition(database, cfg, row, 'RECONCILED', expected='PREPARED', operation='close_action')
    before = await repo.snapshot(cfg.binding)
    original = repo._runtime._commit
    admin = await asyncpg.connect(database[1])

    async def lost(tx):
        pid = tx._connection.get_server_pid()
        if committed:
            await original(tx)
        assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid)
        raise ConnectionError('synthetic lost action closure reply')
    monkeypatch.setattr(repo._runtime, '_commit', lost)
    try:
        with pytest.raises(CommitUnknown):
            await repo.transition_quote_action(cmd, cut=before.cut, owner=owner)
    finally:
        await admin.close()
        await repo.close()
    cold = PgSpotRepository(await PgRuntime.open(cfg))
    try:
        assert ((await cold.lookup_quote_action('close_action', cfg.binding)) is not None) is committed
        assert (await cold.quote_actions(cfg.binding))[0].state == ('RECONCILED' if committed else 'PREPARED')
        assert (await cold.transition_quote_action(cmd, cut=before.cut, owner=owner)).replayed is committed
        final = await cold.snapshot(cfg.binding)
        assert (final.balance, final.budget) == (before.balance, before.budget)
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_runner_facts_expiring_at_commit_keep_slot_claimed(database, monkeypatch):
    import asyncio
    repo, cfg, owner, row = await closed(database, action_fact_age_ms=100)
    original = repo._runtime._commit

    async def delayed(tx):
        await asyncio.sleep(.12)
        await original(tx)
    monkeypatch.setattr(repo._runtime, '_commit', delayed)
    try:
        cmd = await transition(database, cfg, row, 'RECONCILED', expected='PREPARED', operation='close_action')
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='ACTION_FACTS_STALE_AT_COMMIT'):
            await repo.transition_quote_action(cmd, cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert (await repo.quote_actions(cfg.binding))[0].state == 'PREPARED'
        assert await repo.lookup_quote_action('close_action', cfg.binding) is None
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_actual_process_exit_keeps_slot_tombstone_and_spending_atomic(database, committed):
    import asyncio
    import sys
    from dataclasses import fields
    from datetime import datetime

    from hummingbot.strategy_v2.life_liquidity.storage.actions import (
        RunnerActionFacts,
        SpotQuoteAction,
        TransitionQuoteAction,
    )
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner, row = await closed(database, stage='filled')
    before = await repo.snapshot(cfg.binding)
    config = {field.name: getattr(cfg, field.name) for field in fields(cfg)}
    config.update(account_id=str(cfg.account_id), database_lineage=str(cfg.database_lineage), binding=cfg.binding.json())
    payload = json.dumps(dict(config=config, owner=owner.json(), action=row.json(), cut=before.cut.json(), committed=committed))
    child = r'''
import asyncio,json,os,sys
from datetime import datetime,timezone
from uuid import UUID
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken,StateBinding
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.actions import SpotQuoteAction,RunnerActionFacts,TransitionQuoteAction
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig,PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
async def run():
    data=json.load(sys.stdin);cfg=data['config'];cfg['binding']=StateBinding.from_json(cfg['binding'])
    for key in ('account_id','database_lineage'):cfg[key]=UUID(cfg[key])
    repo=PgSpotRepository(await PgRuntime.open(PgConfig(**cfg)))
    row=SpotQuoteAction(**data['action']);facts=RunnerActionFacts(cfg['binding'],datetime.now(timezone.utc),row.controller_id,(),True)
    command=TransitionQuoteAction('close_action',row,'PREPARED','RECONCILED',facts)
    print(json.dumps(command.json()),flush=True)
    original=repo._runtime._commit
    async def die(tx):
        if data['committed']:await original(tx)
        os._exit(73)
    repo._runtime._commit=die
    await repo.transition_quote_action(command,cut=FinancialCut.from_json(data['cut']),owner=OwnerToken.from_json(data['owner']))
asyncio.run(run())
'''
    try:
        process = await asyncio.create_subprocess_exec(sys.executable, '-c', child, stdin=asyncio.subprocess.PIPE,
                                                       stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(payload.encode()), 15)
        except BaseException:
            process.kill()
            await process.wait()
            raise
        assert process.returncode == 73, stderr.decode()
        raw = json.loads(stdout)
    finally:
        await repo.close()
    cold = PgSpotRepository(await PgRuntime.open(cfg))
    try:
        assert ((await cold.lookup_quote_action('close_action', cfg.binding)) is not None) is committed
        assert (await cold.quote_actions(cfg.binding))[0].state == ('RECONCILED' if committed else 'PREPARED')
        f = raw['facts']
        facts = RunnerActionFacts(cfg.binding, datetime.fromisoformat(f['observed_at']), f['controller_id'], tuple(f['executor_ids']), f['scope_complete'])
        cmd = TransitionQuoteAction(raw['operation_id'], SpotQuoteAction(**raw['action']), raw['expected_state'], raw['state'], facts)
        assert (await cold.transition_quote_action(cmd, cut=before.cut, owner=owner)).replayed is committed
        after = await cold.snapshot(cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
    finally:
        await cold.close()
