"""Retire a dispatched action only before any financial acceptance."""
import json
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_action_preparation import bridge, command
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_actions import transition

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.actions import AbandonQuoteAction, ClaimQuoteActions
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


async def abandonment(database, cfg, row, **changes):
    observed = await transition(database, cfg, row, 'REJECTED', operation='retire', **changes)
    return AbandonQuoteAction('retire', row, observed.facts)


@pytest.mark.asyncio
async def test_explicit_retirement_blocks_later_financial_acceptance_and_reuses_only_slot(database):
    repo, cfg, owner, row = await bridge(database)
    try:
        before = await repo.snapshot(cfg.binding)
        cmd = await abandonment(database, cfg, row)
        result = await repo.abandon_quote_action(cmd, cut=before.cut, owner=owner)
        assert result.states == (('intent1', 'REJECTED'),)
        assert (await repo.abandon_quote_action(cmd, cut=before.cut, owner=owner)).replayed
        assert (await repo.lookup_quote_action('retire', cfg.binding)).states == result.states
        after = await repo.snapshot(cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        assert not after.recovery_required and await repo.attempt('intent1', cfg.binding) is None
        with pytest.raises(StorageError, match='ACTION_NOT_DISPATCHED_OR_CHANGED'):
            await repo.prepare_quote_action(await command(database, cfg, owner, row), cut=after.cut, owner=owner)
        fresh = replace(row, intent_id='new1', payload_json=json.dumps({**json.loads(row.payload_json), 'id': 'new1'}))
        with pytest.raises(StorageError, match='ACTION_IDENTITY_USED'):
            await repo.claim_quote_actions(ClaimQuoteActions('reuse_old', (row,)), cut=after.cut, owner=owner)
        await repo.claim_quote_actions(ClaimQuoteActions('newbatch', (fresh,)), cut=after.cut, owner=owner)
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('arm', [False, True])
async def test_financially_accepted_or_unknown_attempt_cannot_be_abandoned(database, arm):
    repo, cfg, owner, row = await bridge(database)
    try:
        await repo.prepare_quote_action(await command(database, cfg, owner, row), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        if arm:
            await repo.arm('operation1', cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='ACTION_STATE_OR_IDENTITY_CONFLICT'):
            await repo.abandon_quote_action(await abandonment(database, cfg, row), cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert (await repo.quote_actions(cfg.binding))[0].state == 'PREPARED'
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_runner_snapshot_before_dispatch_does_not_prove_retirement(database):
    repo, cfg, owner, row = await bridge(database, claim=False)
    try:
        await repo.claim_quote_actions(ClaimQuoteActions('claim', (row,)), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        early = await abandonment(database, cfg, row)
        await repo.transition_quote_action(await transition(database, cfg, row, 'DISPATCHED'), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        cut = (await repo.snapshot(cfg.binding)).cut
        with pytest.raises(StorageError, match='ACTION_RUNNER_FACTS_BEFORE_DISPATCH'):
            await repo.abandon_quote_action(early, cut=cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == cut
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('changes,reason', [({'scope_complete': False}, 'RUNNER_FACTS_INVALID'),
                                            ({'executor_ids': ('intent1',)}, 'RUNNER_SCOPE_CONFLICT'),
                                            ({'controller_id': 'other'}, 'RUNNER_FACTS_INVALID')])
async def test_unqualified_retirement_keeps_claim(database, changes, reason):
    repo, cfg, owner, row = await bridge(database)
    try:
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match=reason):
            await repo.abandon_quote_action(await abandonment(database, cfg, row, **changes), cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('first', ['prepare', 'abandon'])
async def test_account_lock_serializes_both_prepare_retirement_race_orders(database, monkeypatch, first):
    import asyncio
    repo, cfg, owner, row = await bridge(database)
    ready, release = asyncio.Event(), asyncio.Event()
    original = repo._runtime._commit
    seen = False

    async def held(tx):
        nonlocal seen
        if not seen:
            seen = True
            ready.set()
            await release.wait()
        await original(tx)
    monkeypatch.setattr(repo._runtime, '_commit', held)
    try:
        prep = await command(database, cfg, owner, row)
        retire = await abandonment(database, cfg, row)
        before = await repo.snapshot(cfg.binding)
        calls = {'prepare': lambda: repo.prepare_quote_action(prep, cut=before.cut, owner=owner),
                 'abandon': lambda: repo.abandon_quote_action(retire, cut=before.cut, owner=owner)}
        one = asyncio.create_task(calls[first]())
        await asyncio.wait_for(ready.wait(), 2)
        two = asyncio.create_task(calls['abandon' if first == 'prepare' else 'prepare']())
        release.set()
        results = await asyncio.gather(one, two, return_exceptions=True)
        assert not isinstance(results[0], BaseException) and isinstance(results[1], StorageError)
        final = await repo.snapshot(cfg.binding)
        if first == 'prepare':
            assert (await repo.quote_actions(cfg.binding))[0].state == 'PREPARED'
            assert await repo.attempt('intent1', cfg.binding) is not None
            assert final.balance.reserved_quote_usdt > 0
            with pytest.raises(StorageError, match='ACTION_STATE_OR_IDENTITY_CONFLICT'):
                await repo.abandon_quote_action(retire, cut=final.cut, owner=owner)
        else:
            assert (await repo.quote_actions(cfg.binding))[0].state == 'REJECTED'
            assert await repo.attempt('intent1', cfg.binding) is None
            assert (final.balance, final.budget) == (before.balance, before.budget)
            with pytest.raises(StorageError, match='ACTION_NOT_DISPATCHED_OR_CHANGED'):
                await repo.prepare_quote_action(prep, cut=final.cut, owner=owner)
    finally:
        release.set()
        await repo.close()


@pytest.mark.asyncio
async def test_late_retirement_is_not_activation_or_financial_release(database):
    repo, cfg, owner, row = await bridge(database)
    try:
        expired = await database[0].fetchval("SELECT clock_timestamp()-interval '1 millisecond'")
        await database[0].execute("UPDATE life_financial.accounts SET owner_until=$1,halted=true,mode='RECOVERY_ONLY'", expired)
        owner = replace(owner, expires_at=expired)
        before = await repo.snapshot(cfg.binding)
        await repo.abandon_quote_action(await abandonment(database, cfg, row), cut=before.cut, owner=owner)
        after = await repo.snapshot(cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        assert after.session.state == 'PAUSED' and await database[0].fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_changed_generation_at_commit_keeps_retirement_uncommitted(database, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import install_generation_fault
    repo, cfg, owner, row = await bridge(database)
    await install_generation_fault(database)
    original = repo._runtime._commit

    async def changed(tx):
        await tx._connection.execute('SELECT life_financial.test_change_generation($1)', cfg.account_id)
        await original(tx)
    monkeypatch.setattr(repo._runtime, '_commit', changed)
    try:
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='EXPIRED_OR_CHANGED_AT_COMMIT'):
            await repo.abandon_quote_action(await abandonment(database, cfg, row), cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert (await repo.quote_actions(cfg.binding))[0].state == 'DISPATCHED'
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_retirement_facts_expiring_at_commit_do_not_free_slot(database, monkeypatch):
    import asyncio
    repo, cfg, owner, row = await bridge(database, action_fact_age_ms=100)
    original = repo._runtime._commit

    async def delayed(tx):
        await asyncio.sleep(.12)
        await original(tx)
    monkeypatch.setattr(repo._runtime, '_commit', delayed)
    try:
        cmd = await abandonment(database, cfg, row)
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='ACTION_FACTS_STALE_AT_COMMIT'):
            await repo.abandon_quote_action(cmd, cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert await repo.lookup_quote_action('retire', cfg.binding) is None
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_actual_backend_termination_cold_readback_keeps_retirement_identity(database, monkeypatch, committed):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner, row = await bridge(database)
    cmd = await abandonment(database, cfg, row)
    before = await repo.snapshot(cfg.binding)
    original = repo._runtime._commit
    admin = await asyncpg.connect(database[1])

    async def lost(tx):
        pid = tx._connection.get_server_pid()
        if committed:
            await original(tx)
        assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid)
        raise ConnectionError('synthetic lost retirement COMMIT reply')
    monkeypatch.setattr(repo._runtime, '_commit', lost)
    try:
        with pytest.raises(CommitUnknown):
            await repo.abandon_quote_action(cmd, cut=before.cut, owner=owner)
    finally:
        await admin.close()
        await repo.close()
    cold = PgSpotRepository(await PgRuntime.open(cfg))
    try:
        assert ((await cold.lookup_quote_action('retire', cfg.binding)) is not None) is committed
        assert (await cold.quote_actions(cfg.binding))[0].state == ('REJECTED' if committed else 'DISPATCHED')
        assert (await cold.abandon_quote_action(cmd, cut=before.cut, owner=owner)).replayed is committed
        after = await cold.snapshot(cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        assert await cold.attempt('intent1', cfg.binding) is None
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['ordinary_transition', 'wrong_reason', 'missing_reason', 'null_scope'])
async def test_raw_sql_cannot_treat_dispatch_as_ordinary_rejection(database, case):
    import asyncpg
    repo, cfg, owner, row = await bridge(database)
    c = await asyncpg.connect(cfg.dsn)
    try:
        cmd = (await abandonment(database, cfg, row)).json()
        kind = 'SPOT_ACTION_ABANDON'
        if case == 'ordinary_transition':
            kind = 'SPOT_ACTION_TRANSITION'
            cmd.pop('reason_code')
        elif case == 'wrong_reason':
            cmd['reason_code'] = 'TIMEOUT'
        elif case == 'missing_reason':
            cmd.pop('reason_code')
        else:
            cmd['facts']['scope_complete'] = None
        cut = (await repo.snapshot(cfg.binding)).cut
        with pytest.raises(asyncpg.RaiseError, match='ACTION_(COMMAND|RUNNER_FACTS)_INVALID'):
            await c.fetchval('SELECT life_financial.spot_action_command($1,$2::jsonb,$3::jsonb,$4,$5,$6,$7,$8::jsonb)',
                             cfg.account_id, json.dumps(cfg.binding.json()), json.dumps(owner.json()), cut.checkpoint.revision,
                             cut.checkpoint.state_digest, 'f' * 64, kind, json.dumps(cmd))
        assert (await repo.snapshot(cfg.binding)).cut == cut
        assert await repo.lookup_quote_action('retire', cfg.binding) is None
    finally:
        await c.close()
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_actual_process_exit_resolves_original_retirement_without_financial_attempt(database, committed):
    import asyncio
    import sys
    from dataclasses import fields
    from datetime import datetime

    from hummingbot.strategy_v2.life_liquidity.storage.actions import RunnerActionFacts, SpotQuoteAction
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner, row = await bridge(database)
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
from hummingbot.strategy_v2.life_liquidity.storage.actions import SpotQuoteAction,RunnerActionFacts,AbandonQuoteAction
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig,PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
async def run():
    data=json.load(sys.stdin);cfg=data['config'];cfg['binding']=StateBinding.from_json(cfg['binding'])
    for key in ('account_id','database_lineage'):cfg[key]=UUID(cfg[key])
    repo=PgSpotRepository(await PgRuntime.open(PgConfig(**cfg)))
    row=SpotQuoteAction(**data['action']);facts=RunnerActionFacts(cfg['binding'],datetime.now(timezone.utc),row.controller_id,(),True)
    command=AbandonQuoteAction('retire',row,facts);print(json.dumps(command.json()),flush=True)
    original=repo._runtime._commit
    async def die(tx):
        if data['committed']:await original(tx)
        os._exit(73)
    repo._runtime._commit=die
    await repo.abandon_quote_action(command,cut=FinancialCut.from_json(data['cut']),owner=OwnerToken.from_json(data['owner']))
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
        assert ((await cold.lookup_quote_action('retire', cfg.binding)) is not None) is committed
        assert (await cold.quote_actions(cfg.binding))[0].state == ('REJECTED' if committed else 'DISPATCHED')
        f = raw['facts']
        facts = RunnerActionFacts(cfg.binding, datetime.fromisoformat(f['observed_at']), f['controller_id'], tuple(f['executor_ids']), f['scope_complete'])
        cmd = AbandonQuoteAction(raw['operation_id'], SpotQuoteAction(**raw['action']), facts)
        assert (await cold.abandon_quote_action(cmd, cut=before.cut, owner=owner)).replayed is committed
        assert await cold.attempt('intent1', cfg.binding) is None
        after = await cold.snapshot(cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
    finally:
        await cold.close()
