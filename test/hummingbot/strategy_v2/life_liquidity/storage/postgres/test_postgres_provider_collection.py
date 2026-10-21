"""One bounded diagnostic collection commits before any qualified source report."""
import asyncio
import json
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import backend
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_hedge_source import sources
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_provider_journal import page

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.provider_journal import PgProviderJournal
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository


@pytest.mark.asyncio
async def test_provider_collection_preserves_nine_original_pages_and_existing_batch_bound(database):
    rt, cfg, owner = await backend(database)
    repo = PgSpotRepository(rt)
    journal = PgProviderJournal(repo)
    try:
        original = tuple(page(cfg.binding, owner, sequence=i) for i in range(1, 10))
        saved = await journal.capture_collection(original)
        assert tuple(p.body for p in saved) == original
        assert not any(p.replayed or p.trading_permission for p in saved)
        assert all(p.replayed for p in await journal.capture_collection(original))
        assert tuple(p.body for p in await journal.read_collection('collection1', max_rows=9, max_bytes=1048576)) == original
        with pytest.raises(StorageError, match='PROVIDER_BATCH_BOUND'):
            await journal.capture_many(original)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.operations') == 0
        assert await database[0].fetchval('SELECT sum(reserved) FROM life_financial.balances') == 0
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_collection_process_death_preserves_atomic_diagnostics_without_financial_authority(database, committed):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.provider_journal_process import exit_provider_page
    rt, cfg, owner = await backend(database)
    cold = None
    original = tuple(page(cfg.binding, owner, sequence=i) for i in range(1, 10))
    try:
        await exit_provider_page(cfg, original, committed, collection=True)
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        journal = PgProviderJournal(cold)
        rows = await journal.read_collection('collection1', max_rows=9, max_bytes=1048576)
        assert tuple(row.body for row in rows) == (original if committed else ())
        assert all(not row.trading_permission for row in rows)
        # Replaying diagnostics cannot create an action, release, or send permit.
        assert all(row.replayed == committed for row in await journal.capture_collection(original))
        assert all(row.replayed for row in await journal.capture_collection(original))
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_provider_pages') == 9
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.operations') == 0
        assert await database[0].fetchval('SELECT sum(reserved) FROM life_financial.balances') == 0
        assert next(row.total for row in await cold._runtime.balances() if row.asset == 'USDT') == 10
    finally:
        if cold is not None:
            await cold.close()
        await rt.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [0, 513])
async def test_provider_collection_bounds_are_native_as_well_as_client_checks(database, count):
    rt, cfg, _ = await backend(database)
    journal = PgProviderJournal(PgSpotRepository(rt))
    bodies = (None,) * count
    try:
        with pytest.raises(StorageError, match='PROVIDER_COLLECTION_BOUND'):
            await journal.capture_collection(bodies)
        with pytest.raises(StorageError, match='PROVIDER_COLLECTION_BOUND'):
            await rt._transaction(
                'SELECT life_financial.spot_capture_provider_collection($1,$2)',
                (cfg.account_id, bodies), None, lambda v: v, observation=True, untrusted_ingress=True)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_provider_pages') == 0
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_provider_collection_writer_grant_is_required_again_at_each_admission(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.access import PROVIDER_COLLECTION_ENTRYPOINT
    rt, cfg, owner = await backend(database)
    journal = PgProviderJournal(PgSpotRepository(rt))
    try:
        assert not await database[0].fetchval("SELECT has_function_privilege('life_pg2_reporter',$1,'EXECUTE')", PROVIDER_COLLECTION_ENTRYPOINT)
        await database[0].execute('REVOKE EXECUTE ON FUNCTION ' + PROVIDER_COLLECTION_ENTRYPOINT + ' FROM life_pg2_runtime')
        with pytest.raises(StorageError, match='ROLE_PROFILE_INVALID'):
            await journal.capture_collection((page(cfg.binding, owner),))
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_provider_pages') == 0
        assert not rt.healthy
    finally:
        await rt.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['gap', 'other_collection', 'foreign_binding'])
async def test_provider_collection_invalid_tail_rolls_back_all_received_diagnostics(database, fault):
    rt, cfg, owner = await backend(database)
    repo = PgSpotRepository(rt)
    journal = PgProviderJournal(repo)
    try:
        pages = [page(cfg.binding, owner, sequence=i) for i in range(1, 10)]
        if fault == 'gap':
            pages[-1]['sequence'] = 10
        elif fault == 'other_collection':
            pages[-1]['collection_id'] = 'different'
        else:
            pages[-1]['binding'] = {**pages[-1]['binding'], 'account_uid': '999'}
        with pytest.raises(StorageError):
            await journal.capture_collection(tuple(pages))
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_provider_pages') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_provider_heads') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.operations') == 0
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('setting,value', [('jit', 'on'), ('plan_cache_mode', 'force_custom_plan')])
async def test_provider_collection_runtime_pins_engine_on_both_uncached_pools(database, setting, value):
    rt, _, _ = await backend(database)
    try:
        async def settings(c):
            return await c.fetchrow("SELECT current_setting('jit') AS jit,current_setting('plan_cache_mode') AS plans")
        for safety in (True, False):
            values = await rt._admit(settings, safety=safety)
            assert dict(values) == dict(jit='off', plans='force_generic_plan')

        async def changed(c):
            await c.execute(f'SET {setting}={value}')
            await rt._validate_connection(c)
        with pytest.raises(StorageError, match='PRIMARY_CONTEXT_INVALID'):
            await rt._admit(changed, safety=True)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.operations') == 0
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_provider_collection_barrier_and_report_wait_for_original_physical_flush(database, monkeypatch):
    t = await sources(database, clean=True, fresh=True)
    collector, runtime = t.source.collector, t.financial._runtime
    entered, release = asyncio.Event(), asyncio.Event()
    capture = collector.reader._journal.capture_collection

    async def held(pages):
        entered.set()
        await release.wait()
        return await capture(pages)

    monkeypatch.setattr(collector.reader._journal, 'capture_collection', held)
    work = asyncio.create_task(collector.collect())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert not work.done() and collector._report is None
        assert runtime._observation_inflight == 1 and collector.reader._active

        async def forbidden(c):
            raise AssertionError('no financial command may enter before collection COMMIT')
        with pytest.raises(StorageError, match='CLOSED_OR_UNQUALIFIED|OBSERVATION_IN_PROGRESS'):
            await runtime._admit(forbidden, safety=False)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_provider_pages') == 0
        release.set()
        report = await work
        assert collector._report is report and not report.trading_permission
        assert not collector.reader._active and runtime._observation_inflight == 0
        rows = await collector.reader._journal.read_collection(report.collection_id, max_rows=128, max_bytes=4194304)
        assert len(rows) == collector.reader._reads > 8
        assert [r.body['sequence'] for r in rows] == list(range(1, len(rows) + 1))
        writes = [d for d in runtime.diagnostics if d.operation.startswith('spot_capture_provider_')]
        assert [d.operation for d in writes] == ['spot_capture_provider_collection']
        assert writes[0].commit_confirmed
    finally:
        release.set()
        await asyncio.gather(work, return_exceptions=True)
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['semantic', 'transport', 'credential_revocation', 'ingress', 'collection_id'])
async def test_provider_collection_retains_original_partial_diagnostics_without_source_authority(database, fault):
    t = await sources(database, clean=True, fresh=True)
    collector, runtime = t.source.collector, t.financial._runtime
    reader, original = collector.reader, t.session.request
    collection_id = None
    applied = False

    async def http(**kwargs):
        nonlocal applied, collection_id
        response = await original(**kwargs)
        tail = kwargs['url'].rsplit('/', 1)[1]
        if not applied and tail == ('fills-history' if fault in ('semantic', 'transport') else 'bills-archive'):
            applied, collection_id = True, reader.collection_id
            if fault == 'semantic':
                async def bad():
                    return dict(code='51000', data=[])
                response.json = bad
            elif fault == 'transport':
                raise OSError('synthetic partial transport failure')
            elif fault == 'credential_revocation':
                reader.source.revoke()
            elif fault == 'ingress':
                runtime._ingress_epoch += 1
            else:
                reader.collection_id = 'substituted'
        return response

    t.session.request = http
    try:
        before = await database[0].fetchval('SELECT count(*) FROM life_financial.operations')
        with pytest.raises(StorageError):
            await collector.collect()
        assert applied and collector._report is None and not runtime.healthy
        assert not reader._active and runtime._observation_inflight == 0
        rows = await reader._journal.read_collection(collection_id, max_rows=128, max_bytes=4194304)
        assert len(rows) >= 2
        assert all(r.body['collection_id'] == collection_id and not r.trading_permission for r in rows)
        assert [r.body['sequence'] for r in rows] == list(range(1, len(rows) + 1))
        if fault == 'semantic':
            assert any(json.loads(r.body['response_json'])['code'] == '51000' for r in rows)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.operations') == before
        assert not [r for r in t.requests if r['method'] == 'POST']
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_provider_collection_unknown_commit_has_cold_diagnostics_and_no_report_or_retry(database, monkeypatch):
    t = await sources(database, clean=True, fresh=True)
    collector, runtime = t.source.collector, t.financial._runtime
    commit, attempts = runtime._commit, []

    async def lost(transaction):
        attempts.append(transaction)
        await commit(transaction)
        raise OSError('synthetic lost collection commit reply')

    monkeypatch.setattr(runtime, '_commit', lost)
    cold = None
    try:
        with pytest.raises(CommitUnknown):
            await collector.collect()
        assert len(attempts) == 1 and collector._report is None and not runtime.healthy
        assert not collector.reader._active and runtime._observation_inflight == 0
        cold = PgSpotRepository(await PgRuntime.open(t.cfg))
        rows = await PgProviderJournal(cold).read_collection(collector.reader.collection_id, max_rows=128, max_bytes=4194304)
        assert len(rows) > 8 and all(r.replayed and not r.trading_permission for r in rows)
        assert not [r for r in t.requests if r['method'] == 'POST']
    finally:
        if cold is not None:
            await cold.close()
        await t.financial.close()


@pytest.mark.asyncio
async def test_provider_collection_cancel_during_physical_commit_never_publishes_a_late_report(database, monkeypatch):
    t = await sources(database, clean=True, fresh=True)
    collector, runtime = t.source.collector, t.financial._runtime
    commit, entered, release = runtime._commit, asyncio.Event(), asyncio.Event()
    attempts = []

    async def held(transaction):
        attempts.append(transaction)
        entered.set()
        await release.wait()
        await commit(transaction)

    monkeypatch.setattr(runtime, '_commit', held)
    work = asyncio.create_task(collector.collect())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        work.cancel()
        with pytest.raises(asyncio.CancelledError):
            await work
        assert collector._report is None and not runtime.healthy and runtime._tasks
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_provider_pages') == 0
        release.set()
        await asyncio.wait_for(asyncio.gather(*tuple(runtime._tasks)), 5)
        assert len(attempts) == 1 and collector._report is None and not runtime.healthy
        rows = await collector.reader._journal.read_collection(collector.reader.collection_id, max_rows=128, max_bytes=4194304)
        assert len(rows) > 8 and not any(r.trading_permission for r in rows)
        assert not [r for r in t.requests if r['method'] == 'POST']
    finally:
        release.set()
        await asyncio.gather(work, return_exceptions=True)
        await t.financial.close()


@pytest.mark.asyncio
async def test_provider_collection_foreign_task_cannot_borrow_the_active_reader(database):
    t = await sources(database, clean=True, fresh=True)
    collector = t.source.collector
    original, refused = t.session.request, []

    async def foreign():
        with pytest.raises(StorageError, match='COLLECTION_ORIGIN'):
            collector.reader._original()
        refused.append(True)

    async def http(**kwargs):
        response = await original(**kwargs)
        if not refused and kwargs['url'].endswith('/bills-archive'):
            await asyncio.create_task(foreign())
        return response

    t.session.request = http
    try:
        report = await collector.collect()
        assert refused == [True] and collector._report is report
        assert not report.trading_permission
        with pytest.raises(StorageError, match='COLLECTION_ORIGIN'):
            async with collector.reader.collection(account_brackets=True, _collector=collector):
                raise AssertionError('foreign collection must be refused before entry')
    finally:
        await t.financial.close()
