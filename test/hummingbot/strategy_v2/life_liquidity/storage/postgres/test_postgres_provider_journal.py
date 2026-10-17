"""Received decoded provider pages persist before parsing; never financial authority."""
import json
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import backend
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_source import private_http
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_quote_account_source import source
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_quote_transport import transport
from uuid import uuid4

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


def page(binding, owner, *, sequence=1, result=None):
    return dict(binding=binding.json(), owner=owner.json(), collection_id='collection1', sequence=sequence,
                path='/api/v5/account/bills-archive', params={'limit': '100'},
                response_json=json.dumps({'code': '0', 'data': []} if result is None else result,
                                         ensure_ascii=False, allow_nan=False, separators=(',', ':')))


@pytest.mark.asyncio
async def test_reader_commits_original_response_before_return_or_semantic_rejection(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_source import PgSpotAccountReader
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.provider_journal import PgProviderJournal
    t = await transport(database)
    private_http(t)
    try:
        reader = PgSpotAccountReader(await source(t), max_reads=32, max_bytes=1048576, timeout_ms=1000)
        before = await t.repo.snapshot(t.cfg.binding)
        async with reader.collection():
            assert await reader.get_account_bills_page() == {'code': '0', 'data': []}
            rows = await PgProviderJournal(t.repo).read_collection(reader.collection_id, max_rows=32, max_bytes=1048576)
            assert len(rows) == 1 and json.loads(rows[0].body['response_json']) == {'code': '0', 'data': []}
            assert rows[0].body['params'] == {'limit': '100'} and not rows[0].trading_permission
        assert (await t.repo.snapshot(t.cfg.binding)).cut == before.cut
        original = t.session.request

        async def invalid(**kwargs):
            response = await original(**kwargs)
            if kwargs['url'].endswith('/bills-archive'):
                async def body():
                    return {'code': '51000', 'msg': 'synthetic rejection', 'data': []}
                response.json = body
            return response
        t.session.request = invalid
        with pytest.raises(StorageError, match='PG_FINANCIAL_SOURCE_RESPONSE_INVALID'):
            async with reader.collection():
                await reader.get_account_bills_page()
        rows = await PgProviderJournal(t.repo).read_collection(reader.collection_id, max_rows=32, max_bytes=1048576)
        assert json.loads(rows[0].body['response_json'])['code'] == '51000'
        assert not t.repo._runtime.healthy and not t.session.sent
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_original_page_exact_replay_and_permanent_sequence_conflicts_change_no_money(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.provider_journal import PgProviderJournal
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    rt, cfg, owner = await backend(database)
    repo = PgSpotRepository(rt)
    journal = PgProviderJournal(repo)
    cold = None
    try:
        original = page(cfg.binding, owner)
        result = await journal.capture(original)
        assert not result.replayed and not result.trading_permission
        assert (await journal.capture(original)).replayed
        for changed in (dict(original, response_json='null'), dict(original, path='/api/v5/account/balance'),
                        dict(original, owner=replace(owner, generation=uuid4()).json()), dict(original, sequence=3)):
            with pytest.raises(StorageError):
                await journal.capture(changed)
        await journal.capture(page(cfg.binding, owner, sequence=2))
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        rows = await PgProviderJournal(cold).read_collection('collection1', max_rows=2, max_bytes=1048576)
        assert [row.body['sequence'] for row in rows] == [1, 2]
        assert rows[0].body == original and rows[0].captured_at == result.captured_at
        assert all(row.replayed and not row.trading_permission for row in rows)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.operations') == 0
        assert next(row.total for row in await rt.balances() if row.asset == 'USDT') == 10
    finally:
        if cold is not None:
            await cold.close()
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['body', 'missing', 'head', 'tip'])
async def test_changed_or_removed_page_is_never_reported_as_verified_history(database, fault):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.provider_journal import PgProviderJournal
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    rt, cfg, owner = await backend(database)
    repo = PgSpotRepository(rt)
    journal = PgProviderJournal(repo)
    try:
        await journal.capture(page(cfg.binding, owner))
        table = 'spot_provider_pages' if fault in ('body', 'missing') else 'spot_provider_heads'
        await database[0].execute('ALTER TABLE life_financial.' + table + ' DISABLE TRIGGER USER')
        if fault == 'missing':
            await database[0].execute('DELETE FROM life_financial.spot_provider_pages')
        elif fault == 'body':
            await database[0].execute("UPDATE life_financial.spot_provider_pages SET body=jsonb_set(body,'{response_json}','\"null\"')")
        elif fault == 'head':
            await database[0].execute('UPDATE life_financial.spot_provider_heads SET page_count=2')
        else:
            await database[0].execute("UPDATE life_financial.spot_provider_heads SET tip=repeat('a',64)")
        await database[0].execute('ALTER TABLE life_financial.' + table + ' ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='PG_PROVIDER_JOURNAL_UNVERIFIED'):
            await journal.read_collection('collection1', max_rows=32, max_bytes=1048576)
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['row_bound', 'byte_bound', 'binding', 'endpoint', 'secret_param', 'gap', 'response'])
async def test_bounds_scope_and_fixed_route_enforced_by_native_command(database, fault):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.provider_journal import PgProviderJournal
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    rt, cfg, owner = await backend(database)
    repo = PgSpotRepository(rt)
    journal = PgProviderJournal(repo)
    try:
        await journal.capture(page(cfg.binding, owner))
        if fault in ('row_bound', 'byte_bound'):
            await journal.capture(page(cfg.binding, owner, sequence=2))
            with pytest.raises(StorageError, match='PG_PROVIDER_JOURNAL_READ_BOUND'):
                await journal.read_collection('collection1', max_rows=1 if fault == 'row_bound' else 32,
                                              max_bytes=32 if fault == 'byte_bound' else 1048576)
        else:
            body = page(cfg.binding, owner, sequence=2)
            if fault == 'binding':
                body['binding'] = replace(cfg.binding, account_uid='999').json()
            elif fault == 'endpoint':
                body['path'] = '/api/v5/asset/transfer'
            elif fault == 'secret_param':
                body['params'] = {'limit': '100', 'OK-ACCESS-KEY': 'must-not-be-stored'}
            elif fault == 'gap':
                body['sequence'] = 3
            else:
                body['response_json'] = '{invalid json'
            with pytest.raises(StorageError):
                await journal.capture(body)
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_provider_pages') == 1
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['process', 'backend'])
@pytest.mark.parametrize('committed', [False, True])
async def test_real_commit_loss_never_exposes_uncommitted_data_or_duplicates_pages(database, monkeypatch, fault, committed):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.provider_journal_process import exit_provider_page

    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.provider_journal import PgProviderJournal
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    rt, cfg, owner = await backend(database)
    repo = PgSpotRepository(rt)
    body = page(cfg.binding, owner)
    admin = await asyncpg.connect(database[1])
    cold = None
    try:
        if fault == 'process':
            await exit_provider_page(cfg, body, committed)
        else:
            original = rt._commit

            async def lost(tx):
                pid = tx._connection.get_server_pid()
                if committed:
                    await original(tx)
                assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid) is True
                raise ConnectionError('synthetic diagnostic COMMIT reply lost')
            monkeypatch.setattr(rt, '_commit', lost)
            with pytest.raises(CommitUnknown):
                await PgProviderJournal(repo).capture(body)
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        restored = PgProviderJournal(cold)
        rows = await restored.read_collection('collection1', max_rows=32, max_bytes=1048576)
        assert len(rows) == int(committed)
        result = await restored.capture(body)
        assert result.replayed == committed
        assert (await restored.capture(body)).replayed
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_provider_pages') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.operations') == 0
        assert next(row.total for row in await cold._runtime.balances() if row.asset == 'USDT') == 10
    finally:
        await admin.close()
        if cold is not None:
            await cold.close()
        await repo.close()


@pytest.mark.asyncio
async def test_reporter_foreign_account_and_private_functions_cannot_append_or_mutate_history(database):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.provider_journal import PgProviderJournal
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    rt, cfg, owner = await backend(database)
    repo = PgSpotRepository(rt)
    reader = c = None
    try:
        await PgProviderJournal(repo).capture(page(cfg.binding, owner))
        rcfg = replace(cfg, dsn=cfg.dsn.replace('life_pg2_runtime@', 'life_pg2_reporter@'), principal='life_pg2_reporter', write=False)
        reader = PgSpotRepository(await PgRuntime.open(rcfg))
        journal = PgProviderJournal(reader)
        assert len(await journal.read_collection('collection1', max_rows=32, max_bytes=1048576)) == 1
        with pytest.raises(StorageError, match='PG_PROVIDER_JOURNAL_WRITE_REQUIRED'):
            await journal.capture(page(cfg.binding, owner, sequence=2))
        c = await asyncpg.connect(cfg.dsn)
        queries = (
            ('DELETE FROM life_financial.spot_provider_pages WHERE account_id=$1', (cfg.account_id,)),
            ('UPDATE life_financial.spot_provider_heads SET page_count=2 WHERE account_id=$1', (cfg.account_id,)),
            ("SELECT life_financial.spot_provider_receipt($1,'collection1',1,true)", (cfg.account_id,)))
        for query, args in queries:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.execute(query, *args)
        with pytest.raises(asyncpg.RaiseError, match='LIFE_ACCOUNT_SCOPE_INVALID'):
            await c.fetchval("SELECT life_financial.spot_read_provider_collection($1,'collection1',32,1048576)", uuid4())
    finally:
        if c is not None:
            await c.close()
        if reader is not None:
            await reader.close()
        await repo.close()


@pytest.mark.asyncio
async def test_received_page_retained_when_credentials_change_after_http_without_parser_permission(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_source import PgSpotAccountReader
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.provider_journal import PgProviderJournal
    t = await transport(database)
    private_http(t)
    try:
        reader = PgSpotAccountReader(await source(t), max_reads=32, max_bytes=1048576, timeout_ms=1000)
        original = t.session.request

        async def changed(**kwargs):
            response = await original(**kwargs)
            if kwargs['url'].endswith('/bills-archive'):
                t.connector.okx_api_key = 'replaced-synthetic-key'
            return response
        t.session.request = changed
        with pytest.raises(StorageError):
            async with reader.collection():
                await reader.get_account_bills_page()
        rows = await PgProviderJournal(t.repo).read_collection(reader.collection_id, max_rows=32, max_bytes=1048576)
        assert len(rows) == 1 and not rows[0].trading_permission
        assert 'replaced-synthetic-key' not in json.dumps(rows[0].body)
        assert not t.repo._runtime.healthy and not t.session.sent
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.fills') == 0
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_native_collector_journals_every_page_before_first_financial_application(database, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_observer import (
        observed_source,
    )

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.provider_journal import PgProviderJournal
    t, observer, payloads, requests, values = await observed_source(database)
    original = observer.intake.capture
    journal = PgProviderJournal(t.repo)
    seen = []

    async def capture(*args, **kwargs):
        rows = await journal.read_collection(observer.reader.collection_id, max_rows=64, max_bytes=1048576)
        seen.append(rows)
        assert len(rows) == observer.reader._reads
        assert rows[-1].body['path'] == '/api/v5/account/balance'
        if len(seen) <= 2:
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.fills') == 0
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.cashflows') == 0
        return await original(*args, **kwargs)
    monkeypatch.setattr(observer.intake, 'capture', capture)
    try:
        results = await observer.observe()
        assert len(results) == 2 and seen
        rows = await journal.read_collection(observer.reader.collection_id, max_rows=64, max_bytes=1048576)
        assert len(rows) == 12 and all(not row.trading_permission for row in rows)
        assert all(not any(key in row.body for key in ('headers', 'credentials', 'url')) for row in rows)
        assert len(t.session.sent) == 1
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_cold_bounded_discovery_does_not_require_original_process_collection_id(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.provider_journal import PgProviderJournal
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    rt, cfg, owner = await backend(database)
    repo = PgSpotRepository(rt)
    cold = None
    try:
        for key in ('collection-a', 'collection-b', 'collection-c'):
            await PgProviderJournal(repo).capture(dict(page(cfg.binding, owner), collection_id=key))
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        journal = PgProviderJournal(cold)
        first = await journal.collections(limit=2)
        last = await journal.collections(after=first[-1].collection_id, limit=2)
        assert [row.collection_id for row in (*first, *last)] == ['collection-a', 'collection-b', 'collection-c']
        assert all(row.page_count == 1 and row.body_bytes > 0 and not row.trading_permission for row in (*first, *last))
        assert len(await journal.read_collection(last[0].collection_id, max_rows=1, max_bytes=1048576)) == 1
        assert await journal.collections(after=last[0].collection_id, limit=1) == ()
    finally:
        if cold is not None:
            await cold.close()
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_unknown_page_commit_after_real_signed_get_returns_no_parser_data_and_never_retries_http(database, monkeypatch, committed):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_source import PgSpotAccountReader
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.provider_journal import PgProviderJournal
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    t = await transport(database)
    requests = private_http(t)
    admin = await asyncpg.connect(database[1])
    cold = None
    try:
        reader = PgSpotAccountReader(await source(t), max_reads=32, max_bytes=1048576, timeout_ms=1000)
        original = t.repo._runtime._commit

        async def lost(tx):
            pid = tx._connection.get_server_pid()
            if committed:
                await original(tx)
            assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid) is True
            raise ConnectionError('synthetic received provider reply lost at COMMIT')
        monkeypatch.setattr(t.repo._runtime, '_commit', lost)
        with pytest.raises(CommitUnknown):
            async with reader.collection():
                await reader.get_account_bills_page()
        assert len([row for row in requests if row['url'].endswith('/bills-archive')]) == 1
        assert not t.repo._runtime.healthy and not t.session.sent
        cold = PgSpotRepository(await PgRuntime.open(t.cfg))
        rows = await PgProviderJournal(cold).read_collection(reader.collection_id, max_rows=32, max_bytes=1048576)
        assert len(rows) == int(committed)
        assert (await cold.balance(t.cfg.binding)).total_quote_usdt == 10
    finally:
        await admin.close()
        if cold is not None:
            await cold.close()
        await t.close()


@pytest.mark.asyncio
async def test_parallel_read_denial_cannot_move_original_response_to_another_collection_or_page(database):
    import asyncio

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_source import PgSpotAccountReader
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.provider_journal import PgProviderJournal
    t = await transport(database)
    private_http(t)
    reader = task = None
    release, reached = asyncio.Event(), asyncio.Event()
    try:
        reader = PgSpotAccountReader(await source(t), max_reads=32, max_bytes=1048576, timeout_ms=1000)
        original = t.session.request

        async def blocked(**kwargs):
            response = await original(**kwargs)
            if kwargs['url'].endswith('/bills-archive'):
                reached.set()
                await release.wait()
            return response
        t.session.request = blocked
        async with reader.collection():
            collection_id = reader.collection_id
            task = asyncio.create_task(reader.get_account_bills_page())
            await reached.wait()
            with pytest.raises(StorageError, match='PG_FINANCIAL_SOURCE_READ_BACKPRESSURE'):
                await reader.get_spot_cash_balances()
            with pytest.raises(StorageError, match='PG_FINANCIAL_SOURCE_BACKPRESSURE'):
                async with reader.collection():
                    pass
            release.set()
            assert await task == {'code': '0', 'data': []}
        rows = await PgProviderJournal(t.repo).read_collection(collection_id, max_rows=32, max_bytes=1048576)
        assert len(rows) == 1 and rows[0].body['sequence'] == 1
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await t.close()
