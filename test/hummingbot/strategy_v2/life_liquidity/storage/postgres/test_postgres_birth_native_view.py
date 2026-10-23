"""Birth preparation shares one fresh pre-write proof, never a saved verdict."""
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.restore_capacity_cases import seed_completed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_action_preparation import command
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_history_flow import calls, instrument
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_quote_birth import MODEL, born
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_restore_matrix import source

import asyncpg
import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.life_liquidity.storage.actions import RunnerActionFacts
from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.postgres import runtime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.birth import PgQuoteBirthRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import migrate
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runner_journal import PgQuoteRunnerJournal
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_intent import PgSpotQuoteIntent

PREPARE = 'quote_birth_prepare(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'
CORE = 'spot_prepare_action_core(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb,jsonb)'
PRIVATE = 'spot_prepare_birth_from_view(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb,jsonb,jsonb)'


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['missing', 'version', 'boolean_version', 'digest'])
async def test_birth_native_view_malformed_receipt_never_registers_original_ticket(database, monkeypatch, damage):
    repo, cfg, owner, births, intent = await born(database)
    original = PgRuntime._transaction

    async def modified(store, query, args, token, parse, **options):
        def altered(value):
            if 'quote_birth_issue(' in query:
                value = dict(value)
                if damage == 'missing':
                    del value['preparation_cut']
                else:
                    proposal = dict(value['preparation_cut'])
                    proposal['digest' if damage == 'digest' else 'version'] = {'version': 1, 'boolean_version': False, 'digest': 'untrusted'}[damage]
                    value['preparation_cut'] = proposal
            return parse(value)
        return await original(store, query, args, token, altered, **options)

    try:
        before = await repo.snapshot(cfg.binding)
        monkeypatch.setattr(PgRuntime, '_transaction', modified)
        # Parse happens after physical COMMIT. Preserve the existing unknown-
        # outcome fence rather than pretending malformed readback rolled back.
        with pytest.raises(CommitUnknown, match='PG_COMMIT_UNKNOWN'):
            await births.issue('invalid-reply', intent, model_hash=fingerprint(MODEL), expected_version=before.cut.checkpoint.revision)
        assert not births._issued
        assert not repo._runtime.healthy
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.quote_births') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.reservations') == 0
        with pytest.raises(StorageError, match='BIRTH_IDENTITY_USED'):
            await births.issue('invalid-reply', intent, model_hash=fingerprint(MODEL), expected_version=before.cut.checkpoint.revision)
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [133], indirect=True)
async def test_birth_native_view_control_proof_is_shared_only_inside_one_state_read(database, monkeypatch, request):
    load = runtime.load_migrations
    with monkeypatch.context() as patch:
        patch.setattr(runtime, 'load_migrations', lambda path: tuple(m for m in load(path) if m.version <= 133))
        values, _, _ = await source(database, managed_options=dict(owner_duration_ms=300000,
                                                                   spot_options=dict(session_duration=timedelta(minutes=10))),
                                    spot_quote_producer={'synthetic_retained_history': True})
        repo, cfg = values[:2]
        await repo.close()
    c = database[0]
    await c.execute('CREATE SCHEMA control_view_reference')
    definition = await c.fetchval("SELECT pg_get_functiondef('life_financial.spot_state(uuid)'::regprocedure)")
    await c.execute(definition.replace('FUNCTION life_financial.spot_state(', 'FUNCTION control_view_reference.original(', 1))
    request.node.user_properties.append(('control_view_predecessor', await c.fetchval('''SELECT
        encode(sha256(convert_to(prosrc,'UTF8')),'hex') FROM pg_proc WHERE oid='life_financial.control_verified(uuid)'::regprocedure''')))
    await migrate(c, action='up')
    sequence = await instrument(c, 'control_verified')
    for corrupt in (False, True):
        if corrupt:
            await c.execute('ALTER TABLE life_financial.control_records DISABLE TRIGGER USER')
            assert await c.execute('''UPDATE life_financial.control_records SET source_hash=repeat('f',64)
                WHERE account_id=$1 AND sequence=(SELECT min(sequence) FROM life_financial.control_records WHERE account_id=$1)''', cfg.account_id) != 'UPDATE 0'
            await c.execute('ALTER TABLE life_financial.control_records ENABLE TRIGGER USER')
        results, counts = [], []
        for function in ('life_financial.spot_state', 'control_view_reference.original'):
            await calls(c, sequence, reset=True)
            results.append(json.loads(await c.fetchval(f"SELECT ({function}($1)-'observed_at')::text", cfg.account_id)))
            counts.append(await calls(c, sequence))
        assert results[0] == results[1]
        assert results[0]['control_verified'] is not corrupt
        request.node.user_properties.append(('control_view_counts_' + str(corrupt), json.dumps(counts)))
        assert counts[0] == 1 and counts[1] > 1
        await calls(c, sequence, reset=True)
        await c.fetchval('SELECT life_financial.spot_state($1)', cfg.account_id)
        await c.fetchval('SELECT life_financial.spot_state($1)', cfg.account_id)
        assert await calls(c, sequence) == 2  # Never reuse a verdict between statements.


@pytest.mark.asyncio
async def test_birth_native_view_runner_consumes_original_proposal_without_snapshot_refresh(database, monkeypatch):
    repo, cfg, owner, births, intent = await born(database)

    async def facts():
        return RunnerActionFacts(cfg.binding, await database[0].fetchval('SELECT clock_timestamp()'),
                                 'controller1', (intent.intent_id,), True)

    app = PgQuoteRunnerJournal(repo, owner=owner, controller_id='controller1', config_version=1,
                               runner_facts=facts, local_ready=lambda: True,
                               clock=lambda: datetime.now(timezone.utc), max_actions=8, birth_repository=births)
    try:
        original = await app.preflight_publication('native-birth')
        action = await app.capture_provisional('native-birth', intent, model_hash=fingerprint(MODEL), _publication=original)
        assert await app.async_on_runner_create_action_dispatched(action)
        prep = await command(database, cfg, owner, app._entry(action).row)

        async def refresh(*args, **kwargs):
            raise AssertionError('original provisional preparation must not refresh its snapshot')
        monkeypatch.setattr(repo, 'snapshot', refresh)
        assert (await app.prepare_quote_action(action, prep)).state == 'PREPARED'
        assert app._entry(action).accepted
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [0, 4])
async def test_birth_native_view_cut_preserves_settled_retired_and_sorted_history(database, count):
    model = {'synthetic_retained_history': True}
    values, _, _ = await source(database, managed_options=dict(owner_duration_ms=300000,
                                                               spot_options=dict(session_duration=timedelta(minutes=10))),
                                spot_quote_producer=model)
    repo, cfg, owner, _, lease, *_ = values
    try:
        await seed_completed(database, repo, cfg, owner, lease, count, {}, hot_tickets=16)
        births = PgQuoteBirthRepository(repo)
        initial = await repo.snapshot(cfg.binding)
        for key in ('Z', 'A'):
            ticket = await births.issue('ticket-' + key, PgSpotQuoteIntent('intent-' + key, 'controller1', owner, 1,
                                                                           TradeType.BUY, 0, D(1), D(1)),
                                        model_hash=fingerprint(model), expected_version=initial.cut.checkpoint.revision)
            actual = await repo.snapshot(cfg.binding)
            assert ticket.preparation_cut == actual.cut
            assert (actual.balance, actual.budget) == (initial.balance, initial.budget)
            await births.retire('retire-' + key, ticket.ticket_id, cut=actual.cut, owner=owner)
            assert (await births.read(ticket.ticket_id)).preparation_cut is None
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_birth_native_view_receipt_cut_matches_fresh_postbirth_state(database, request):
    preflight = {}
    repo, cfg, owner, births, ticket, prep, facts, cut, args = await original_request(database, preflight=preflight)
    c = database[0]
    try:
        request.node.user_properties.append(('birth_issue_predecessor', await c.fetchval('''SELECT
            encode(sha256(convert_to(prosrc,'UTF8')),'hex') FROM pg_proc
            WHERE oid='life_financial.quote_birth_issue(uuid,jsonb,jsonb,bigint,jsonb)'::regprocedure''')))
        current = json.loads(await c.fetchval("SELECT (life_financial.spot_state($1)-'digest'-'observed_at')::text", cfg.account_id))
        projected = dict(preflight)
        projected['quote_births'] = {
            **preflight['quote_births'], 'pending': True,
            'hashes': [await c.fetchval('SELECT body_hash FROM life_financial.quote_births WHERE account_id=$1', cfg.account_id)]}
        projected['recovery_required'] = True
        differences = {k: (projected.get(k), current.get(k)) for k in projected.keys() | current.keys() if projected.get(k) != current.get(k)}
        request.node.user_properties.append(('birth_projection_differences', json.dumps(differences)))
        assert not differences, differences
        assert ticket.preparation_cut == cut
        assert not ticket.trading_permission
        assert (await births.read(ticket.ticket_id)).preparation_cut is None
        result = await births.prepare(ticket, prep, facts, cut=ticket.preparation_cut, owner=owner)
        assert result.state == 'PREPARED' and not result.replayed
    finally:
        await repo.close()


async def original_request(database, *, preflight=None):
    repo, cfg, owner, births, intent = await born(database)
    c = database[0]
    await c.execute('INSERT INTO life_financial.access_bindings VALUES(current_user,$1,true)', cfg.account_id)
    before = await repo.snapshot(cfg.binding)
    if preflight is not None:
        preflight.update(json.loads(await c.fetchval("SELECT (life_financial.spot_state($1)-'digest'-'observed_at')::text", cfg.account_id)))
    ticket = await births.issue('birth1', intent, model_hash=fingerprint(MODEL), expected_version=before.cut.checkpoint.revision)
    cut = (await repo.snapshot(cfg.binding)).cut
    at = await c.fetchval('SELECT clock_timestamp()')
    facts = RunnerActionFacts(cfg.binding, at, 'controller1', ('intent1',), True)
    prep = await command(database, cfg, owner, ticket.action)
    request = dict(ticket_id=ticket.ticket_id, **prep.json(), dispatch=facts.json())
    version, digest = repo._cut(cut, owner)
    identity = fingerprint(dict(kind='QUOTE_BIRTH_PREPARE', request=request, binding=cfg.binding.json(),
                                owner=owner.json(), version=version, cut=digest))
    args = (cfg.account_id, json.dumps(cfg.binding.json()), json.dumps(owner.json()), version, digest, identity, json.dumps(request))
    return repo, cfg, owner, births, ticket, prep, facts, cut, args


@pytest.mark.asyncio
async def test_birth_native_view_uses_one_prewrite_history_audit_and_fresh_commit(database, request):
    repo, cfg, owner, births, ticket, prep, facts, cut, args = await original_request(database)
    c = database[0]
    try:
        signatures = ('quote_birth_prepare(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
                      'spot_prepare_action_core(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb,jsonb)',
                      'spot_state(uuid)', 'spot_body(uuid)', 'quote_birth_verified(uuid)',
                      'guard_account(uuid,jsonb,jsonb,bigint)', 'guard_spot_session(uuid)',
                      'guard_quote_admission(uuid,jsonb,jsonb,jsonb,jsonb)')
        predecessors = await c.fetch('''SELECT oid::regprocedure::text AS signature,
            encode(sha256(convert_to(prosrc,'UTF8')),'hex') AS sha256 FROM pg_proc
            WHERE oid=ANY($1::regprocedure[])''', ['life_financial.' + s for s in signatures])
        request.node.user_properties.append(('birth_view_predecessors', json.dumps([dict(r) for r in predecessors])))
        birth_calls = await instrument(c, 'quote_birth_verified')
        state_calls = await instrument(c, 'spot_state')
        tx = c.transaction()
        await tx.start()
        try:
            receipt = json.loads(await c.fetchval('SELECT life_financial.quote_birth_prepare($1,$2,$3,$4,$5,$6,$7)', *args))
            assert receipt['state'] == 'PREPARED' and not receipt['trading_permission']
            counts = (await calls(c, birth_calls), await calls(c, state_calls))
            request.node.user_properties.append(('birth_view_prewrite_audits', json.dumps(counts)))
            assert counts == (1, 1)
        finally:
            await tx.rollback()
        await calls(c, birth_calls, reset=True)
        await calls(c, state_calls, reset=True)
        # The public original-ticket path still commits a new write. Deferred
        # post-write guards must audit again, independently of the pre-write view.
        receipt = await births.prepare(ticket, prep, facts, cut=cut, owner=owner)
        assert receipt.state == 'PREPARED' and not receipt.replayed
        assert await calls(c, birth_calls) > 1
        after = await repo.snapshot(cfg.binding)
        assert after.balance.reserved_quote_usdt == D('2.2')
    finally:
        await repo.close()


async def financial_rows(c, account):
    result = {}
    for table in ('balances', 'budget_buckets', 'reservations', 'send_attempts', 'spot_preparations',
                  'quote_actions', 'spot_action_descriptors', 'spot_action_events', 'quote_birth_consumptions', 'operations'):
        # Only the physical recording/preparation instant differs between two
        # genuine executions. Preserve every command, receipt and amount.
        omit = "-'prepared_at'" if table == 'spot_preparations' else "-'recorded_at'" if table == 'operations' else ''
        result[table] = json.loads(await c.fetchval('SELECT COALESCE(jsonb_agg(row ORDER BY row::text),\'[]\'::jsonb)::text FROM '
                                                    '(SELECT to_jsonb(t)' + omit + ' AS row FROM life_financial.'
                                                    + table + ' t WHERE account_id=$1) q', account), parse_float=D)
    result['version'] = await c.fetchval('SELECT version FROM life_financial.accounts WHERE account_id=$1', account)
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [132], indirect=True)
@pytest.mark.parametrize('fault', ['none', 'old_cut', 'birth_hash', 'excess_fee', 'scope', 'expired'])
async def test_birth_native_view_matches_original_receipts_money_and_refusals(database, monkeypatch, fault):
    # Capture the actual unchanged predecessor, not an approximation of its
    # predicates. Its original core remains independent of the new private copy.
    load = runtime.load_migrations
    with monkeypatch.context() as patch:
        patch.setattr(runtime, 'load_migrations', lambda path: tuple(m for m in load(path) if m.version <= 132))
        repo, cfg, owner, births, ticket, prep, facts, cut, args = await original_request(database)
        await repo.close()
    c = database[0]
    await c.execute('CREATE SCHEMA birth_view_reference')
    definition = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', 'life_financial.' + PREPARE)
    original_core = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', 'life_financial.' + CORE)
    await c.execute(definition.replace('FUNCTION life_financial.quote_birth_prepare(', 'FUNCTION birth_view_reference.original(', 1))
    await migrate(c, action='up')
    assert await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', 'life_financial.' + CORE) == original_core
    # Neither the runtime nor reporting principal (nor PUBLIC) can supply a
    # fabricated native view. Provisioning during startup must retain this too.
    for role in (cfg.principal, 'life_pg2_reporter'):
        assert not await c.fetchval('SELECT has_function_privilege($1,$2,\'EXECUTE\')', role, 'life_financial.' + PRIVATE)
    args = list(args)
    body = json.loads(args[-1])
    if fault == 'old_cut':
        args[4] = 'f' * 64
    elif fault == 'birth_hash':
        await c.execute('ALTER TABLE life_financial.quote_births DISABLE TRIGGER USER')
        await c.execute("UPDATE life_financial.quote_births SET body_hash=repeat('f',64)")
        await c.execute('ALTER TABLE life_financial.quote_births ENABLE TRIGGER USER')
    elif fault == 'excess_fee':
        body['prepare']['fee_buffer_base_life'] = '1000000'
    elif fault == 'scope':
        body['dispatch']['scope_complete'] = False
    elif fault == 'expired':
        import asyncio
        await asyncio.sleep(1.05)
        # Genuinely new synthetic source observations, while the original
        # published action/ticket timestamp remains expired and unchanged.
        at = await c.fetchval('SELECT clock_timestamp()')
        body['dispatch'] = replace(facts, observed_at=at).json()
        body['admission'] = (await command(database, cfg, owner, ticket.action)).json()['admission']
    args[-1] = json.dumps(body)
    args[5] = fingerprint(dict(kind='QUOTE_BIRTH_PREPARE', request=body, binding=cfg.binding.json(),
                               owner=owner.json(), version=args[3], cut=args[4]))
    before = await financial_rows(c, cfg.account_id)
    outcomes = []
    for name in ('birth_view_reference.original', 'life_financial.quote_birth_prepare'):
        tx = c.transaction()
        await tx.start()
        try:
            try:
                value = await c.fetchval(f'SELECT {name}($1,$2,$3,$4,$5,$6,$7)', *args)
            except asyncpg.PostgresError as error:
                outcomes.append(('refused', error.sqlstate, error.message))
            else:
                assert await c.fetchval('''SELECT prepared_at>=to_timestamp($2::numeric/1000)
                    AND prepared_at<=clock_timestamp() FROM life_financial.spot_preparations
                    WHERE account_id=$1 AND intent_id='intent1' ''', cfg.account_id, ticket.born_at_ms)
                outcomes.append(('prepared', json.loads(value), await financial_rows(c, cfg.account_id)))
        finally:
            await tx.rollback()
        assert await financial_rows(c, cfg.account_id) == before
    assert outcomes[0][:2] == outcomes[1][:2]
    if outcomes[0][0] == 'prepared':
        for table in outcomes[0][2]:
            assert outcomes[0][2][table] == outcomes[1][2][table], table
    else:
        assert outcomes[0][2] == outcomes[1][2]
    if fault == 'none':
        assert outcomes[0][0] == 'prepared'
    else:
        assert outcomes[0][0] == 'refused'
        expected = dict(old_cut='LIFE_SPOT_CUT_CONFLICT', birth_hash='LIFE_BIRTH_UNVERIFIED',
                        excess_fee='LIFE_SPOT_CAPITAL_INCLUDING_FEES_DENIED', scope='LIFE_BIRTH_RUNNER_SCOPE_INVALID',
                        expired='LIFE_QUOTE_CAPTURE_STALE')
        assert outcomes[0][2] == expected[fault]


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['expiry', 'birth_corruption'])
async def test_birth_native_view_commit_rechecks_after_successful_prewrite_audit(database, fault):
    repo, cfg, owner, births, ticket, prep, facts, cut, args = await original_request(database)
    c = database[0]
    try:
        before = await financial_rows(c, cfg.account_id)
        tx = c.transaction()
        await tx.start()
        try:
            assert json.loads(await c.fetchval('SELECT life_financial.quote_birth_prepare($1,$2,$3,$4,$5,$6,$7)', *args))['state'] == 'PREPARED'
            if fault == 'expiry':
                import asyncio
                await asyncio.sleep(1.05)
            else:
                await c.execute('ALTER TABLE life_financial.quote_births DISABLE TRIGGER USER')
                await c.execute("UPDATE life_financial.quote_births SET body_hash=repeat('f',64)")
            with pytest.raises(asyncpg.PostgresError):
                await c.execute('SET CONSTRAINTS ALL IMMEDIATE')
        finally:
            await tx.rollback()
        assert await financial_rows(c, cfg.account_id) == before
        assert await c.fetchval('SELECT life_financial.quote_birth_verified($1)', cfg.account_id)
        cold = await births.read(ticket.ticket_id)
        assert cold.historical and not cold.trading_permission
        with pytest.raises(StorageError, match='BIRTH_ORIGINAL_PREPARATION_REQUIRED'):
            await births.prepare(cold, prep, facts, cut=cut, owner=owner)
    finally:
        await repo.close()
