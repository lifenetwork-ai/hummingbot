"""Quote publication bookkeeping on a disposable primary; no runner or HTTP sends."""
import asyncio
from dataclasses import replace
from datetime import timedelta
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order, spot

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.actions import (
    ClaimQuoteActions,
    RunnerActionFacts,
    SpotQuoteAction,
    TransitionQuoteAction,
)
from hummingbot.strategy_v2.life_liquidity.storage.contracts import AtomicScope, StorageError, StorageUnavailable


def action(owner, **changes):
    return replace(SpotQuoteAction('intent1', 'controller1', str(owner.session_id), owner.epoch, 1,
                                   'LIFE-USDT', 'BUY', 0, '{"captured_executor":"synthetic1"}'), **changes)


async def setup(database, *, policy=True, max_fact_age_ms=1000, **settings):
    repo, cfg, owner = await spot(database, **settings)
    if policy:
        await database[0].execute('INSERT INTO life_financial.spot_action_policies VALUES($1,$2,\'controller1\',8,65536,$3)',
                                  cfg.account_id, cfg.binding.policy_hash, max_fact_age_ms)
    return repo, cfg, owner


async def transition(database, cfg, row, state, *, expected='PROPOSED', operation='transition', **changes):
    facts = RunnerActionFacts(cfg.binding, await database[0].fetchval('SELECT clock_timestamp()'), row.controller_id,
                              (row.intent_id,) if state == 'DISPATCHED' else (), True)
    return TransitionQuoteAction(operation, row, expected, state, replace(facts, **changes))


@pytest.mark.asyncio
async def test_atomic_batch_exact_bytes_readback_and_no_financial_authority(database):
    repo, cfg, owner = await setup(database)
    try:
        before = await repo.snapshot(cfg.binding)
        rows = (action(owner), action(owner, intent_id='sell1', side='SELL'))
        command = ClaimQuoteActions('claim', rows)
        result = await repo.claim_quote_actions(command, cut=before.cut, owner=owner)
        assert result.atomic_scope is AtomicScope.ACCOUNT and not result.replayed
        assert result.states == (('intent1', 'PROPOSED'), ('sell1', 'PROPOSED'))
        saved = await repo.quote_actions(cfg.binding)
        assert tuple(row.action for row in saved) == rows
        assert (await repo.claim_quote_actions(command, cut=before.cut, owner=owner)).replayed
        assert (await repo.lookup_quote_action('claim', cfg.binding)).states == result.states
        current = await repo.snapshot(cfg.binding)
        assert (current.balance, current.budget) == (before.balance, before.budget)
        assert current.recovery_required
        with pytest.raises(StorageError):
            await repo.prepare(order(owner), cut=current.cut, owner=owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.reservations') == 0
        assert await database[0].fetchval("SELECT receipt->>'trading_permission' FROM life_financial.operations WHERE operation_id='claim'") == 'false'
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_rejected_unaccepted_action_releases_only_slot_and_keeps_identity(database):
    repo, cfg, owner = await setup(database)
    try:
        row = action(owner)
        await repo.claim_quote_actions(ClaimQuoteActions('claim', (row,)), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        command = await transition(database, cfg, row, 'REJECTED')
        cut = (await repo.snapshot(cfg.binding)).cut
        assert (await repo.transition_quote_action(command, cut=cut, owner=owner)).states == (('intent1', 'REJECTED'),)
        assert (await repo.transition_quote_action(command, cut=cut, owner=owner)).replayed
        assert not (await repo.snapshot(cfg.binding)).recovery_required
        with pytest.raises(StorageError):
            await repo.claim_quote_actions(ClaimQuoteActions('reuse', (row,)), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await repo.claim_quote_actions(ClaimQuoteActions('new', (action(owner, intent_id='new1'),)),
                                       cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_dispatch_is_bookkeeping_and_crash_does_not_free_claim(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner = await setup(database)
    row = action(owner)
    await repo.claim_quote_actions(ClaimQuoteActions('claim', (row,)), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    command = await transition(database, cfg, row, 'DISPATCHED')
    cut = (await repo.snapshot(cfg.binding)).cut
    await repo.transition_quote_action(command, cut=cut, owner=owner)
    await repo.close()
    cold = PgSpotRepository(await PgRuntime.open(cfg))
    try:
        assert (await cold.quote_actions(cfg.binding))[0].state == 'DISPATCHED'
        assert (await cold.transition_quote_action(command, cut=cut, owner=owner)).replayed
        with pytest.raises(StorageError):
            await cold.transition_quote_action(await transition(database, cfg, row, 'REJECTED'),
                                               cut=(await cold.snapshot(cfg.binding)).cut, owner=owner)
        with pytest.raises(StorageError):
            await cold.transition_quote_action(await transition(database, cfg, row, 'RECONCILED', expected='DISPATCHED'),
                                               cut=(await cold.snapshot(cfg.binding)).cut, owner=owner)
        assert (await cold.quote_actions(cfg.binding))[0].state == 'DISPATCHED'
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [dict(scope_complete=False), dict(controller_id='other'), dict(executor_ids=('intent1',))])
async def test_unqualified_rejection_retains_slot(database, changes):
    repo, cfg, owner = await setup(database)
    try:
        row = action(owner)
        await repo.claim_quote_actions(ClaimQuoteActions('claim', (row,)), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        with pytest.raises(StorageError):
            await repo.transition_quote_action(await transition(database, cfg, row, 'REJECTED', **changes),
                                               cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert (await repo.quote_actions(cfg.binding))[0].state == 'PROPOSED'
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_concurrent_claims_and_conflicting_payload_cannot_duplicate_slots(database):
    repo, cfg, owner = await setup(database)
    try:
        cut = (await repo.snapshot(cfg.binding)).cut
        one, two = ClaimQuoteActions('one', (action(owner),)), ClaimQuoteActions('two', (action(owner, intent_id='two'),))
        results = await asyncio.gather(repo.claim_quote_actions(one, cut=cut, owner=owner),
                                       repo.claim_quote_actions(two, cut=cut, owner=owner), return_exceptions=True)
        assert sum(isinstance(value, StorageError) for value in results) == 1
        winner = one if not isinstance(results[0], Exception) else two
        with pytest.raises(StorageError, match='OPERATION_CONFLICT'):
            await repo.claim_quote_actions(replace(winner, actions=(replace(winner.actions[0], payload_json='{ "changed":true }'),)),
                                           cut=cut, owner=owner)
        assert len(await repo.quote_actions(cfg.binding)) == 1
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [dict(controller_id='other'), dict(epoch=2), dict(config_version=2)])
async def test_changed_scope_denies_entire_batch(database, changes):
    repo, cfg, owner = await setup(database)
    try:
        cut = (await repo.snapshot(cfg.binding)).cut
        with pytest.raises(StorageError):
            await repo.claim_quote_actions(ClaimQuoteActions('claim', (action(owner), action(owner, intent_id='bad', side='SELL', **changes))),
                                           cut=cut, owner=owner)
        assert await repo.quote_actions(cfg.binding) == ()
        assert (await repo.snapshot(cfg.binding)).cut == cut
        assert await repo.lookup_quote_action('claim', cfg.binding) is None
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['missing_policy', 'active_order', 'accepted_tombstone'])
async def test_absent_import_or_existing_financial_order_denies_claim(database, case):
    repo, cfg, owner = await setup(database, policy=case != 'missing_policy')
    try:
        if case != 'missing_policy':
            await repo.prepare(order(owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
            if case == 'accepted_tombstone':
                from hummingbot.strategy_v2.life_liquidity.storage.spot_lifecycle import SpotLifecycleCommand
                command = SpotLifecycleCommand('abort', 'operation1', 'intent1', 'wire1', 'ABORT_PREPARED')
                await repo.apply_lifecycle(command, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError):
            await repo.claim_quote_actions(ClaimQuoteActions('claim', (action(owner),)), cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert await repo.quote_actions(cfg.binding) == ()
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['DISPATCHED', 'REJECTED'])
@pytest.mark.parametrize('offset', [-2, 1])
async def test_stale_or_future_runner_scope_retains_action(database, state, offset):
    repo, cfg, owner = await setup(database)
    try:
        row = action(owner)
        await repo.claim_quote_actions(ClaimQuoteActions('claim', (row,)), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        observed = await database[0].fetchval('SELECT clock_timestamp()') + timedelta(seconds=offset)
        command = await transition(database, cfg, row, state, observed_at=observed)
        with pytest.raises(StorageError, match='FACTS_STALE'):
            await repo.transition_quote_action(command, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert (await repo.quote_actions(cfg.binding))[0].state == 'PROPOSED'
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_exact_payload_at_dispatch_and_full_runner_scope_required(database):
    repo, cfg, owner = await setup(database)
    try:
        row = action(owner)
        await repo.claim_quote_actions(ClaimQuoteActions('claim', (row,)), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        for command in (await transition(database, cfg, replace(row, payload_json='{ "captured_executor":"synthetic1" }'), 'DISPATCHED'),
                        await transition(database, cfg, row, 'DISPATCHED', executor_ids=())):
            with pytest.raises(StorageError):
                await repo.transition_quote_action(command, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert (await repo.quote_actions(cfg.binding))[0].state == 'PROPOSED'
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_late_rejection_never_reactivates_expired_owner(database):
    repo, cfg, owner = await setup(database)
    try:
        row = action(owner)
        await repo.claim_quote_actions(ClaimQuoteActions('claim', (row,)), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        expired = await database[0].fetchval("SELECT clock_timestamp()-interval '1 millisecond'")
        await database[0].execute("UPDATE life_financial.accounts SET owner_until=$1,halted=true,mode='RECOVERY_ONLY'", expired)
        owner = replace(owner, expires_at=expired)
        with pytest.raises(StorageError):
            await repo.transition_quote_action(await transition(database, cfg, row, 'DISPATCHED'), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await repo.transition_quote_action(await transition(database, cfg, row, 'REJECTED'), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert (await repo.quote_actions(cfg.binding))[0].state == 'REJECTED'
        assert (await repo.snapshot(cfg.binding)).session.state == 'PAUSED'
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['claim', 'dispatch', 'reject'])
async def test_physical_generation_change_at_commit_rolls_back_action_and_receipt(database, monkeypatch, stage):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import install_generation_fault
    repo, cfg, owner = await setup(database)
    await install_generation_fault(database)
    row = action(owner)
    if stage != 'claim':
        await repo.claim_quote_actions(ClaimQuoteActions('claim', (row,)), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    command = (ClaimQuoteActions('fault', (row,)) if stage == 'claim' else
               await transition(database, cfg, row, 'DISPATCHED' if stage == 'dispatch' else 'REJECTED', operation='fault'))
    method = repo.claim_quote_actions if stage == 'claim' else repo.transition_quote_action
    original = repo._runtime._commit

    async def change(tx):
        await tx._connection.execute('SELECT life_financial.test_change_generation($1)', cfg.account_id)
        await original(tx)
    monkeypatch.setattr(repo._runtime, '_commit', change)
    try:
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='EXPIRED_OR_CHANGED_AT_COMMIT'):
            await method(command, cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert await repo.lookup_quote_action('fault', cfg.binding) is None
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['DISPATCHED', 'REJECTED'])
async def test_runner_facts_expiring_at_commit_roll_back_transition(database, monkeypatch, state):
    repo, cfg, owner = await setup(database, max_fact_age_ms=100)
    row = action(owner)
    await repo.claim_quote_actions(ClaimQuoteActions('claim', (row,)), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    original = repo._runtime._commit

    async def delayed(tx):
        await asyncio.sleep(.12)
        await original(tx)
    monkeypatch.setattr(repo._runtime, '_commit', delayed)
    try:
        command = await transition(database, cfg, row, state)
        cut = (await repo.snapshot(cfg.binding)).cut
        with pytest.raises(StorageError, match='FACTS_STALE_AT_COMMIT'):
            await repo.transition_quote_action(command, cut=cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == cut
        assert (await repo.quote_actions(cfg.binding))[0].state == 'PROPOSED'
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['claim', 'dispatch', 'reject'])
@pytest.mark.parametrize('committed', [False, True])
async def test_actual_backend_termination_and_cold_original_readback(database, monkeypatch, stage, committed):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner = await setup(database)
    row = action(owner)
    if stage != 'claim':
        await repo.claim_quote_actions(ClaimQuoteActions('claim', (row,)), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    command = (ClaimQuoteActions('fault', (row,)) if stage == 'claim' else
               await transition(database, cfg, row, 'DISPATCHED' if stage == 'dispatch' else 'REJECTED', operation='fault'))
    method = repo.claim_quote_actions if stage == 'claim' else repo.transition_quote_action
    cut = (await repo.snapshot(cfg.binding)).cut
    original = repo._runtime._commit
    admin = await asyncpg.connect(database[1])

    async def lost(tx):
        pid = tx._connection.get_server_pid()
        if committed:
            await original(tx)
        assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid)
        raise ConnectionError('synthetic lost action COMMIT reply')
    monkeypatch.setattr(repo._runtime, '_commit', lost)
    try:
        with pytest.raises(CommitUnknown):
            await method(command, cut=cut, owner=owner)
        assert not repo._runtime.healthy
    finally:
        await admin.close()
        await repo.close()
    cold = PgSpotRepository(await PgRuntime.open(cfg))
    try:
        saved = await cold.lookup_quote_action('fault', cfg.binding)
        assert (saved is not None) is committed
        # Original bytes and operation identity, never external executor replay.
        method = cold.claim_quote_actions if stage == 'claim' else cold.transition_quote_action
        result = await method(command, cut=cut, owner=owner)
        assert result.replayed is committed
        assert (await cold.quote_actions(cfg.binding))[0].state == {'claim': 'PROPOSED', 'dispatch': 'DISPATCHED', 'reject': 'REJECTED'}[stage]
        assert await database[0].fetchval("SELECT count(*) FROM life_financial.operations WHERE operation_id='fault'") == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_native_file_journal_state_slot_and_identity_parity(database, tmp_path):
    from hummingbot.strategy_v2.life_liquidity.action_journal import QuoteActionJournal, QuoteActionRecord
    repo, cfg, owner = await setup(database)
    native = QuoteActionJournal(tmp_path / 'actions.json', account_uid=cfg.binding.account_uid)
    native.initialize_empty()
    rows = (action(owner), action(owner, intent_id='sell1', side='SELL'))
    native.claim_batch([QuoteActionRecord(**{key: value for key, value in row.json().items() if key != 'payload_json'}) for row in rows])
    try:
        await repo.claim_quote_actions(ClaimQuoteActions('claim', rows), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        for row, state in zip(rows, ('REJECTED', 'DISPATCHED')):
            native.transition(row.intent_id, expected='PROPOSED', state=state)
            await repo.transition_quote_action(await transition(database, cfg, row, state, operation=row.intent_id + ':transition'),
                                               cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        saved = await repo.quote_actions(cfg.binding)
        for row in saved:
            assert native.verified_get(row.action.intent_id).__dict__ == {
                **{key: value for key, value in row.action.json().items() if key != 'payload_json'}, 'state': row.state}
        assert {row.intent_id for row in native.active_records()} == {row.action.intent_id for row in saved if row.state == 'DISPATCHED'}
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_runtime_and_reporter_permissions_and_fabricated_closed_action(database):
    import asyncpg
    repo, cfg, owner = await setup(database)
    runtime = await asyncpg.connect(cfg.dsn)
    reporter = await asyncpg.connect(cfg.dsn.replace('life_pg2_runtime@', 'life_pg2_reporter@'))
    try:
        row = action(owner)
        await repo.claim_quote_actions(ClaimQuoteActions('claim', (row,)), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert len(await reporter.fetch('SELECT * FROM life_financial.spot_action_events')) == 1
        for statement in ('DELETE FROM life_financial.quote_actions', 'UPDATE life_financial.spot_action_descriptors SET descriptor=\'{}\'',
                          'SELECT life_financial.spot_actions_verified($1)'):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await runtime.execute(statement, *([cfg.account_id] if '$1' in statement else []))
        assert not await reporter.fetchval("SELECT has_function_privilege(current_user,'life_financial.spot_action_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)','EXECUTE')")
        with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError, match='IMMUTABLE'):
            await database[0].execute("UPDATE life_financial.spot_action_events SET descriptor='{}'")
        # A privileged projection mutation cannot silently free the pending slot.
        await database[0].execute("UPDATE life_financial.quote_actions SET state='REJECTED',version=version+1")
        assert (await repo.snapshot(cfg.binding)).recovery_required
        with pytest.raises(StorageUnavailable, match='PG_DATABASE_UNAVAILABLE'):
            await repo.quote_actions(cfg.binding)
        with pytest.raises(StorageError):
            await repo.prepare(order(owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    finally:
        await runtime.close()
        await reporter.close()
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['claim', 'dispatch', 'reject'])
@pytest.mark.parametrize('committed', [False, True])
async def test_actual_process_exit_publishes_whole_action_or_nothing(database, stage, committed):
    import json
    import sys
    from dataclasses import fields

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner = await setup(database)
    row = action(owner)
    if stage != 'claim':
        await repo.claim_quote_actions(ClaimQuoteActions('claim', (row,)), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    before = await repo.snapshot(cfg.binding)
    config = {field.name: getattr(cfg, field.name) for field in fields(cfg)}
    config.update(account_id=str(cfg.account_id), database_lineage=str(cfg.database_lineage), binding=cfg.binding.json())
    payload = json.dumps(dict(config=config, owner=owner.json(), cut=before.cut.json(), action=row.json(), stage=stage, committed=committed))
    child = r'''
import asyncio, json, os, sys
from datetime import datetime, timezone
from uuid import UUID
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken, StateBinding
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.actions import ClaimQuoteActions, SpotQuoteAction, TransitionQuoteAction, RunnerActionFacts
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig, PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
async def run():
    data=json.load(sys.stdin)
    config=data['config']
    config['binding']=StateBinding.from_json(config['binding'])
    for key in ('account_id','database_lineage'): config[key]=UUID(config[key])
    runtime=await PgRuntime.open(PgConfig(**config))
    repo=PgSpotRepository(runtime)
    row=SpotQuoteAction(**data['action'])
    if data['stage']=='claim': command=ClaimQuoteActions('fault',(row,))
    else:
        state='DISPATCHED' if data['stage']=='dispatch' else 'REJECTED'
        facts=RunnerActionFacts(config['binding'],datetime.now(timezone.utc),row.controller_id,(row.intent_id,) if state=='DISPATCHED' else (),True)
        command=TransitionQuoteAction('fault',row,'PROPOSED',state,facts)
    print(json.dumps(command.json()),flush=True)
    original=runtime._commit
    async def die(tx):
        if data['committed']: await original(tx)
        os._exit(73)
    runtime._commit=die
    method=repo.claim_quote_actions if data['stage']=='claim' else repo.transition_quote_action
    await method(command,cut=FinancialCut.from_json(data['cut']),owner=OwnerToken.from_json(data['owner']))
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
        saved = await cold.lookup_quote_action('fault', cfg.binding)
        assert (saved is not None) is committed
        if stage == 'claim':
            command = ClaimQuoteActions(raw['operation_id'], tuple(SpotQuoteAction(**value) for value in raw['actions']))
        else:
            from datetime import datetime
            facts = raw['facts']
            facts = RunnerActionFacts(cfg.binding, datetime.fromisoformat(facts['observed_at']), facts['controller_id'],
                                      tuple(facts['executor_ids']), facts['scope_complete'])
            command = TransitionQuoteAction(raw['operation_id'], SpotQuoteAction(**raw['action']), raw['expected_state'], raw['state'], facts)
        method = cold.claim_quote_actions if stage == 'claim' else cold.transition_quote_action
        result = await method(command, cut=before.cut, owner=owner)
        assert result.replayed is committed
        final = await cold.snapshot(cfg.binding)
        assert (final.balance, final.budget) == (before.balance, before.budget)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['null_kind', 'null_action', 'null_level', 'bool_epoch', 'duplicate_slot', 'extra_field', 'too_many', 'bad_payload', 'null_scope'])
async def test_raw_sql_cannot_bypass_typed_action_contract(database, case):
    import json

    import asyncpg
    repo, cfg, owner = await setup(database)
    connection = await asyncpg.connect(cfg.dsn)
    try:
        cut = (await repo.snapshot(cfg.binding)).cut
        command = ClaimQuoteActions('raw', (action(owner),)).json()
        kind = 'SPOT_ACTION_CLAIM'
        if case == 'null_kind':
            kind = None
        elif case == 'null_action':
            command['actions'] = [None]
        elif case == 'null_level':
            command['actions'][0]['level'] = None
        elif case == 'bool_epoch':
            command['actions'][0]['epoch'] = True
        elif case == 'duplicate_slot':
            command['actions'].append({**command['actions'][0], 'intent_id': 'other'})
        elif case == 'extra_field':
            command['actions'][0]['authority'] = True
        elif case == 'too_many':
            command['actions'] = [{**command['actions'][0], 'intent_id': 'id' + str(i), 'level': i} for i in range(9)]
        elif case == 'bad_payload':
            command['actions'][0]['payload_json'] = '[]'
        elif case == 'null_scope':
            row = action(owner)
            await repo.claim_quote_actions(ClaimQuoteActions('claim', (row,)), cut=cut, owner=owner)
            cut = (await repo.snapshot(cfg.binding)).cut
            command = (await transition(database, cfg, row, 'REJECTED', operation='raw')).json()
            command['facts']['scope_complete'] = None
            kind = 'SPOT_ACTION_TRANSITION'
        with pytest.raises(asyncpg.PostgresError):
            await connection.fetchval('SELECT life_financial.spot_action_command($1,$2::jsonb,$3::jsonb,$4,$5,$6,$7,$8::jsonb)',
                                      cfg.account_id, json.dumps(cfg.binding.json()), json.dumps(owner.json()), cut.checkpoint.revision,
                                      cut.checkpoint.state_digest, 'f' * 64, kind, json.dumps(command))
        assert (await repo.snapshot(cfg.binding)).cut == cut
        assert await repo.lookup_quote_action('raw', cfg.binding) is None
    finally:
        await connection.close()
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('changed', ['process_id', 'generation'])
@pytest.mark.parametrize('state', ['DISPATCHED', 'REJECTED'])
async def test_unqualified_successor_cannot_dispatch_or_free_original_writer_claim(database, changed, state):
    from uuid import uuid4
    repo, cfg, owner = await setup(database)
    try:
        row = action(owner)
        await repo.claim_quote_actions(ClaimQuoteActions('claim', (row,)), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        successor = replace(owner, **{changed: uuid4()})
        column = 'owner_process' if changed == 'process_id' else 'owner_generation'
        # Privileged fixture simulates changed ownership, without a qualified
        # action handoff. Runtime still has no UPDATE or generation bypass.
        await database[0].execute(f'UPDATE life_financial.accounts SET {column}=$1 WHERE account_id=$2',
                                  getattr(successor, changed), cfg.account_id)
        cut = (await repo.snapshot(cfg.binding)).cut
        with pytest.raises(StorageError, match='ACTION_ORIGINAL_WRITER_REQUIRED'):
            await repo.transition_quote_action(await transition(database, cfg, row, state), cut=cut, owner=successor)
        assert (await repo.quote_actions(cfg.binding))[0].state == 'PROPOSED'
        assert (await repo.snapshot(cfg.binding)).cut == cut
        assert await repo.lookup_quote_action('transition', cfg.binding) is None
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_extended_lease_does_not_extend_original_action_dispatch_authority(database):
    repo, cfg, owner = await setup(database)
    try:
        row = action(owner)
        await repo.claim_quote_actions(ClaimQuoteActions('claim', (row,)), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        extended = replace(owner, expires_at=owner.expires_at + timedelta(seconds=10))
        await database[0].execute('UPDATE life_financial.accounts SET owner_until=$1 WHERE account_id=$2', extended.expires_at, cfg.account_id)
        cut = (await repo.snapshot(cfg.binding)).cut
        with pytest.raises(StorageError, match='ACTION_ORIGINAL_WRITER_REQUIRED'):
            await repo.transition_quote_action(await transition(database, cfg, row, 'DISPATCHED'), cut=cut, owner=extended)
        assert (await repo.quote_actions(cfg.binding))[0].state == 'PROPOSED'
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_runner_snapshot_from_before_claim_cannot_reject_published_action(database):
    repo, cfg, owner = await setup(database)
    try:
        row = action(owner)
        captured = await database[0].fetchval('SELECT clock_timestamp()')
        await repo.claim_quote_actions(ClaimQuoteActions('claim', (row,)), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        command = await transition(database, cfg, row, 'REJECTED', observed_at=captured)
        cut = (await repo.snapshot(cfg.binding)).cut
        with pytest.raises(StorageError, match='ACTION_RUNNER_FACTS_BEFORE_CLAIM'):
            await repo.transition_quote_action(command, cut=cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == cut
        assert (await repo.quote_actions(cfg.binding))[0].state == 'PROPOSED'
    finally:
        await repo.close()
