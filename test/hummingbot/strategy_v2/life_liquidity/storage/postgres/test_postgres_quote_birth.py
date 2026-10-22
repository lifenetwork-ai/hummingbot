"""One native birth, one atomic preparation, and no reconstructed send grant."""
import asyncio
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_action_preparation import command
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_actions import setup
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order

import asyncpg
import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.life_liquidity.storage.action_preparation import PrepareQuoteAction, QuoteAdmissionFacts
from hummingbot.strategy_v2.life_liquidity.storage.actions import RunnerActionFacts
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.postgres.birth import PgQuoteBirthRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.diagnostics import _current_trace
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_intent import PgSpotQuoteIntent

MODEL = dict(synthetic='birth-contract-v1')


async def born(database):
    repo, cfg, owner = await setup(database, configuration_body=lambda binding: dict(spot_quote_producer=MODEL))
    c = database[0]
    await c.execute('INSERT INTO life_financial.quote_birth_policies VALUES($1,$2,16,1048576)', cfg.account_id, cfg.binding.policy_hash)
    await c.execute('''INSERT INTO life_financial.spot_quote_admission_policies VALUES(
        $1,$2,'liquidity_service',0,0,.01,.01,.01,1000,1000,1000,1000)''', cfg.account_id, cfg.binding.policy_hash)
    births = PgQuoteBirthRepository(repo)
    intent = PgSpotQuoteIntent('intent1', 'controller1', owner, 1, TradeType.BUY, 0, D('2'), D('1'))
    return repo, cfg, owner, births, intent


async def prepare_birth(database, repo, cfg, owner, births, intent, index):
    ticket = await births.issue('birth:' + str(index), intent, model_hash=fingerprint(MODEL),
                                expected_version=(await repo.snapshot(cfg.binding)).cut.checkpoint.revision)
    facts = RunnerActionFacts(cfg.binding, await database[0].fetchval('SELECT clock_timestamp()'),
                              'controller1', tuple(row.action.intent_id for row in await repo.quote_actions(cfg.binding)) + (intent.intent_id,), True)
    at = await database[0].fetchval('SELECT clock_timestamp()')
    admission = QuoteAdmissionFacts(cfg.binding, at, at, at, at, D('1'), D('1'), D('.99'), D('1.01'),
                                    D('.01'), D('0'), D('0'), D('0'), D('0'), False, 'independent_market')
    prep = PrepareQuoteAction(ticket.action, order(owner, operation_id='prepare:' + str(index),
                              intent_id=intent.intent_id, client_order_id='wire' + str(index), quote_level=intent.level,
                              quantity_base=intent.quantity_base, subsidy_quote_usdt=intent.quantity_base * D('.01')), admission)
    await births.prepare(ticket, prep, facts, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    return ticket


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['none', 'session', 'configuration', 'operation', 'event', 'dispatch'])
async def test_retained_birth_history_checks_every_joined_origin_and_repeated_model(database, damage):
    repo, cfg, owner, births, intent = await born(database)
    c = database[0]
    try:
        for index in range(3):
            await prepare_birth(database, repo, cfg, owner, births, replace(intent, intent_id='retained:' + str(index), level=index), index)
        assert await c.fetchval('SELECT life_financial.quote_birth_verified($1)', cfg.account_id) is True
        # Change the oldest entry, not the current head. Repeated configuration
        # or joined lookup must not hide missing/changed retained provenance.
        if damage == 'session':
            await c.execute('ALTER TABLE life_financial.sessions DISABLE TRIGGER USER')
            await c.execute("UPDATE life_financial.sessions SET started_at=started_at+interval '1 millisecond' WHERE account_id=$1", cfg.account_id)
            await c.execute('ALTER TABLE life_financial.sessions ENABLE TRIGGER USER')
        elif damage == 'configuration':
            await c.execute('ALTER TABLE life_financial.configurations DISABLE TRIGGER USER')
            await c.execute("UPDATE life_financial.configurations SET body=jsonb_set(body,'{spot_quote_producer}', '{}'::jsonb) WHERE account_id=$1", cfg.account_id)
            await c.execute('ALTER TABLE life_financial.configurations ENABLE TRIGGER USER')
        elif damage == 'operation':
            await c.execute('ALTER TABLE life_financial.operations DISABLE TRIGGER USER')
            await c.execute("UPDATE life_financial.operations SET command=command||'{\"compound\":null}'::jsonb WHERE account_id=$1 AND operation_id='prepare:0'", cfg.account_id)
            await c.execute('ALTER TABLE life_financial.operations ENABLE TRIGGER USER')
        elif damage == 'event':
            await c.execute('ALTER TABLE life_financial.spot_action_events DISABLE TRIGGER USER')
            await c.execute("DELETE FROM life_financial.spot_action_events WHERE account_id=$1 AND action_id='retained:0'", cfg.account_id)
            await c.execute('ALTER TABLE life_financial.spot_action_events ENABLE TRIGGER USER')
        elif damage == 'dispatch':
            await c.execute('ALTER TABLE life_financial.quote_birth_consumptions DISABLE TRIGGER USER')
            await c.execute("UPDATE life_financial.quote_birth_consumptions SET dispatch=dispatch||'{\"scope_complete\":null}'::jsonb WHERE account_id=$1 AND ticket_id='birth:0'", cfg.account_id)
            await c.execute('ALTER TABLE life_financial.quote_birth_consumptions ENABLE TRIGGER USER')
        assert await c.fetchval('SELECT life_financial.quote_birth_verified($1)', cfg.account_id) is (damage == 'none')
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [1, 4, 8])
async def test_birth_join_plan_materializes_each_referenced_configuration_once(database, count):
    import json
    from statistics import median
    from time import perf_counter
    repo, cfg, owner, births, intent = await born(database)
    c = database[0]
    try:
        for index in range(count):
            await prepare_birth(database, repo, cfg, owner, births,
                                replace(intent, intent_id='scale:' + str(index), level=index, quantity_base=D('.1')), index)
        # Explain the actual row-selection SQL used by the verifier, rather
        # than a hand-written approximation or a whole-function Result node.
        # Migration 106 wraps the unchanged joined audit with retirement
        # validation. Explain its original delegated selection; the timings
        # below still execute the complete public verifier including retirement.
        definition = await c.fetchval('''SELECT prosrc FROM pg_proc WHERE oid=COALESCE(
            to_regprocedure('life_financial.quote_birth_before_retirement_verified(uuid)'),
            to_regprocedure('life_financial.quote_birth_verified(uuid)'))''')
        query = definition.split('FOR r IN\n', 1)[1].split(' LOOP\n', 1)[0].replace('p_account', '$1')
        plan = json.loads(await c.fetchval('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' + query, cfg.account_id))[0]

        def nodes(node):
            yield node
            for child in node.get('Plans', ()):
                yield from nodes(child)
        model, = [n for n in nodes(plan['Plan']) if n.get('Subplan Name') == 'CTE models']
        assert model['Actual Rows'] == model['Actual Loops'] == 1
        assert plan['Plan']['Actual Rows'] == count
        timings = []
        for _ in range(3):
            started = perf_counter()
            assert await c.fetchval('SELECT life_financial.quote_birth_verified($1)', cfg.account_id) is True
            timings.append((perf_counter() - started) * 1000)
        print('birth_join_growth=' + json.dumps(dict(tickets=count, configurations=1,
              materialized_model_rows=model['Actual Rows'], median_read_ms=round(median(timings), 3))))
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_birth_is_immutable_nonmonetary_and_cold_readback_cannot_reissue(database):
    repo, cfg, owner, births, intent = await born(database)
    try:
        before = await repo.snapshot(cfg.binding)
        ticket = await births.issue('birth1', intent, model_hash=fingerprint(MODEL), expected_version=before.cut.checkpoint.revision)
        assert not ticket.trading_permission and not ticket.historical
        assert ticket.action.intent_id == intent.intent_id
        assert (await repo.snapshot(cfg.binding)).balance == before.balance
        assert (await repo.snapshot(cfg.binding)).budget == before.budget
        assert not await repo.quote_actions(cfg.binding)
        assert await repo.attempt(intent.intent_id, cfg.binding) is None
        assert await database[0].fetchval('SELECT version FROM life_financial.accounts WHERE account_id=$1', cfg.account_id) == before.cut.checkpoint.revision
        await asyncio.sleep(.02)
        cold = PgQuoteBirthRepository(repo)
        stored = await cold.read('birth1')
        assert stored.historical and stored.action == ticket.action and stored.born_at_ms == ticket.born_at_ms
        with pytest.raises(StorageError, match='BIRTH_IDENTITY_USED'):
            await cold.issue('different-birth', intent, model_hash=fingerprint(MODEL), expected_version=before.cut.checkpoint.revision)
        with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError, match='IMMUTABLE'):
            await database[0].execute("UPDATE life_financial.quote_births SET body=body||'{\"extra\":true}'::jsonb")
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_atomic_birth_preparation_has_one_version_original_financial_readers_and_holds(database):
    repo, cfg, owner, births, intent = await born(database)
    try:
        ticket = await births.issue('birth1', intent, model_hash=fingerprint(MODEL), expected_version=(await repo.snapshot(cfg.binding)).cut.checkpoint.revision)
        facts = RunnerActionFacts(cfg.binding, await database[0].fetchval('SELECT clock_timestamp()'), 'controller1', ('intent1',), True)
        prep = await command(database, cfg, owner, ticket.action)
        before = await repo.snapshot(cfg.binding)
        receipt = await births.prepare(ticket, prep, facts, cut=before.cut, owner=owner)
        assert receipt.state == 'PREPARED' and not receipt.replayed
        after = await repo.snapshot(cfg.binding)
        assert after.cut.checkpoint.revision == before.cut.checkpoint.revision + 1
        assert not after.recovery_required
        assert (await repo.quote_actions(cfg.binding))[0].action == ticket.action
        assert (await repo.quote_actions(cfg.binding))[0].state == 'PREPARED'
        assert after.balance.reserved_quote_usdt == D('2.2') and after.balance.reserved_base_life == D('.1')
        assert after.budget.session_subsidy_committed_usdt == D('.02')
        assert (await repo.lookup_prepare(prep.prepare.operation_id, cfg.binding)).intent_id == intent.intent_id
        assert await database[0].fetchval("SELECT count(*) FROM life_financial.operations WHERE command->>'kind' LIKE 'SPOT_ACTION_%'") == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.quote_birth_consumptions') == 1
        with pytest.raises(StorageError, match='BIRTH_ORIGINAL_PREPARATION_REQUIRED'):
            await births.prepare(await births.read('birth1'), prep, facts, cut=before.cut, owner=owner)
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['foreign_model', 'foreign_owner', 'partial_scope', 'before_birth', 'changed_action', 'stale'])
async def test_invalid_birth_or_provisional_scope_cannot_reserve_money(database, fault):
    from datetime import timedelta
    repo, cfg, owner, births, intent = await born(database)
    try:
        before = await repo.snapshot(cfg.binding)
        if fault in ('foreign_model', 'foreign_owner'):
            with pytest.raises(StorageError):
                await births.issue('birth1', replace(intent, owner=replace(owner, epoch=2)) if fault == 'foreign_owner' else intent,
                                   model_hash='0' * 64 if fault == 'foreign_model' else fingerprint(MODEL), expected_version=before.cut.checkpoint.revision)
        else:
            ticket = await births.issue('birth1', intent, model_hash=fingerprint(MODEL), expected_version=before.cut.checkpoint.revision)
            at = await database[0].fetchval('SELECT clock_timestamp()')
            facts = RunnerActionFacts(cfg.binding, at, 'controller1', ('intent1',), fault != 'partial_scope')
            if fault == 'before_birth':
                facts = replace(facts, observed_at=at - timedelta(seconds=2))
            prep = await command(database, cfg, owner, ticket.action)
            if fault == 'changed_action':
                prep = replace(prep, action=replace(prep.action, level=1), prepare=replace(prep.prepare, quote_level=1))
            if fault == 'stale':
                await asyncio.sleep(1.05)
            with pytest.raises(StorageError):
                await births.prepare(ticket, prep, facts, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert await repo.attempt('intent1', cfg.binding) is None
        assert (await repo.snapshot(cfg.binding)).balance == before.balance
        assert not await repo.quote_actions(cfg.binding)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.reservations') == 0
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['birth', 'prepare'])
@pytest.mark.parametrize('committed', [False, True])
async def test_birth_and_atomic_prepare_physical_commit_loss_preserve_identity_and_holds(database, monkeypatch, phase, committed):
    repo, cfg, owner, births, intent = await born(database)
    original = PgRuntime._commit
    seen = []
    target = 'quote_birth_issue' if phase == 'birth' else 'quote_birth_prepare'
    admin = await asyncpg.connect(database[1])

    async def lost(runtime, tx):
        trace = _current_trace.get()
        if trace.operation != target:
            return await original(runtime, tx)
        assert not seen
        if committed:
            await original(runtime, tx)
        assert await admin.fetchval('SELECT pg_terminate_backend($1)', trace.backend_pid)
        seen.append(True)
        raise ConnectionError('synthetic lost compound response')
    try:
        ticket = None
        if phase == 'prepare':
            ticket = await births.issue('birth1', intent, model_hash=fingerprint(MODEL), expected_version=(await repo.snapshot(cfg.binding)).cut.checkpoint.revision)
            facts = RunnerActionFacts(cfg.binding, await database[0].fetchval('SELECT clock_timestamp()'), 'controller1', ('intent1',), True)
            prep = await command(database, cfg, owner, ticket.action)
        cut = (await repo.snapshot(cfg.binding)).cut
        monkeypatch.setattr(PgRuntime, '_commit', lost)
        with pytest.raises(StorageError):
            if phase == 'birth':
                await births.issue('birth1', intent, model_hash=fingerprint(MODEL), expected_version=cut.checkpoint.revision)
            else:
                await births.prepare(ticket, prep, facts, cut=cut, owner=owner)
        assert seen == [True] and not repo._runtime.healthy
        assert await admin.fetchval('SELECT count(*) FROM life_financial.quote_births') == (committed if phase == 'birth' else 1)
        assert await admin.fetchval('SELECT count(*) FROM life_financial.send_attempts') == (phase == 'prepare' and committed)
        assert await admin.fetchval('SELECT count(*) FROM life_financial.quote_actions') == (phase == 'prepare' and committed)
        reserved = await admin.fetchval('SELECT sum(reserved) FROM life_financial.balances')
        assert reserved == (D('2.3') if phase == 'prepare' and committed else 0)
    finally:
        await admin.close()
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['hash', 'binding', 'model', 'payload'])
async def test_same_version_birth_corruption_is_rechecked_even_if_its_hash_is_recomputed(database, damage):
    import json
    repo, cfg, owner, births, intent = await born(database)
    c = database[0]
    try:
        ticket = await births.issue('birth1', intent, model_hash=fingerprint(MODEL), expected_version=(await repo.snapshot(cfg.binding)).cut.checkpoint.revision)
        body = json.loads(ticket.body_json)
        if damage == 'binding':
            body['binding']['account_uid'] = 'foreign'
        elif damage == 'model':
            body['model_hash'] = '0' * 64
        elif damage == 'payload':
            for key in ('template', 'action'):
                value = json.loads(body[key]['payload_json'])
                value['connector_name'] = 'foreign'
                body[key]['payload_json'] = json.dumps(value, separators=(',', ':'))
        await c.execute('ALTER TABLE life_financial.quote_births DISABLE TRIGGER USER')
        await c.execute('UPDATE life_financial.quote_births SET body=$1::jsonb,body_hash=$2 WHERE account_id=$3',
                        json.dumps(body), '0' * 64 if damage == 'hash' else fingerprint(body), cfg.account_id)
        await c.execute('ALTER TABLE life_financial.quote_births ENABLE TRIGGER USER')
        assert await c.fetchval('SELECT life_financial.quote_birth_verified($1)', cfg.account_id) is False
        with pytest.raises(StorageError, match='BIRTH_UNVERIFIED'):
            await births.read('birth1')
        assert await repo.attempt('intent1', cfg.binding) is None
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', ['null_time', 'null_member', 'duplicate_member', 'numeric_complete'])
async def test_atomic_birth_sql_rejects_malformed_scope_without_relying_on_python_dto(database, bad):
    import json
    repo, cfg, owner, births, intent = await born(database)
    c = database[0]
    try:
        ticket = await births.issue('birth1', intent, model_hash=fingerprint(MODEL), expected_version=(await repo.snapshot(cfg.binding)).cut.checkpoint.revision)
        prep = await command(database, cfg, owner, ticket.action)
        at = await c.fetchval('SELECT clock_timestamp()')
        facts = RunnerActionFacts(cfg.binding, at, 'controller1', ('intent1',), True).json()
        if bad == 'null_time':
            facts['observed_at'] = None
        elif bad == 'null_member':
            facts['executor_ids'] = ['intent1', None]
        elif bad == 'duplicate_member':
            facts['executor_ids'] = ['intent1', 'intent1']
        else:
            facts['scope_complete'] = 1
        request = dict(ticket_id=ticket.ticket_id, **prep.json(), dispatch=facts)
        cut = (await repo.snapshot(cfg.binding)).cut.checkpoint
        identity = fingerprint(dict(kind='QUOTE_BIRTH_PREPARE', request=request, binding=cfg.binding.json(), owner=owner.json(),
                                    version=cut.revision, cut=cut.state_digest))
        await c.execute('INSERT INTO life_financial.access_bindings VALUES(current_user,$1,true)', cfg.account_id)
        with pytest.raises(asyncpg.RaiseError, match='BIRTH_RUNNER_SCOPE_INVALID'):
            await c.fetchval('SELECT life_financial.quote_birth_prepare($1,$2::jsonb,$3::jsonb,$4,$5,$6,$7::jsonb)',
                             cfg.account_id, json.dumps(cfg.binding.json()), json.dumps(owner.json()), cut.revision,
                             cut.state_digest, identity, json.dumps(request))
        assert await repo.attempt('intent1', cfg.binding) is None
        assert await c.fetchval('SELECT count(*) FROM life_financial.quote_birth_consumptions') == 0
    finally:
        await repo.close()
