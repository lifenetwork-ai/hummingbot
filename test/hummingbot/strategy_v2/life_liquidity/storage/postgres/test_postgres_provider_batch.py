"""Batching diagnostics preserves original pages, atomicity and uncertainty."""
import json
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import backend
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_source import private_http
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_provider_journal import page
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_quote_account_source import source
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_quote_transport import transport

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_source import PgSpotAccountReader
from hummingbot.strategy_v2.life_liquidity.storage.postgres.provider_journal import PgProviderJournal
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository


@pytest.mark.asyncio
async def test_batch_exact_replay_and_conflicting_tail_are_account_atomic(database):
    rt, cfg, owner = await backend(database)
    repo = PgSpotRepository(rt)
    journal = PgProviderJournal(repo)
    try:
        original = tuple(page(cfg.binding, owner, sequence=i) for i in range(1, 9))
        rows = await journal.capture_many(original)
        assert len(rows) == 8 and not any(v.replayed or v.trading_permission for v in rows)
        assert all(v.replayed for v in await journal.capture_many(original))
        # Page 9 would succeed alone; invalid page 11 rolls the whole append back.
        with pytest.raises(StorageError):
            await journal.capture_many((page(cfg.binding, owner, sequence=9), page(cfg.binding, owner, sequence=11)))
        saved = await journal.read_collection('collection1', max_rows=8, max_bytes=1048576)
        assert tuple(v.body for v in saved) == original
        with pytest.raises(StorageError):
            await journal.capture_many(original + (page(cfg.binding, owner, sequence=9),))
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.operations') == 0
        assert next(v.total for v in await rt.balances() if v.asset == 'USDT') == 10
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['semantic', 'transport', 'commit_reply'])
async def test_reader_batch_never_returns_uncommitted_or_malformed_data(database, monkeypatch, fault):
    t = await transport(database)
    private_http(t)
    reader = PgSpotAccountReader(await source(t), max_reads=8, max_bytes=1048576, timeout_ms=1000)
    original = t.session.request
    calls = 0

    async def http(**kwargs):
        nonlocal calls
        response = await original(**kwargs)
        if kwargs['url'].endswith('/bills-archive'):
            calls += 1
            if fault == 'transport' and calls == 2:
                raise OSError('synthetic transport failure')
            if fault == 'semantic' and calls == 2:
                async def body():
                    return dict(code='51000', data=[])
                response.json = body
        return response
    t.session.request = http
    committed = t.repo._runtime._commit

    async def lost(transaction):
        await committed(transaction)
        raise ConnectionError('synthetic lost batch commit response')
    if fault == 'commit_reply':
        monkeypatch.setattr(t.repo._runtime, '_commit', lost)
    try:
        with pytest.raises((StorageError, CommitUnknown)):
            async with reader.collection():
                await reader.read_batch((('/api/v5/account/bills-archive', {'limit': '100'}),) * 2)
        monkeypatch.setattr(t.repo._runtime, '_commit', committed)
        saved = await reader._journal.read_collection(reader.collection_id, max_rows=8, max_bytes=1048576)
        assert len(saved) == (1 if fault == 'transport' else 2)
        assert saved[0].body['sequence'] == 1
        if fault == 'semantic':
            assert json.loads(saved[1].body['response_json'])['code'] == '51000'
        assert not t.repo._runtime.healthy and not t.session.sent
        assert (await t.repo.attempt('intent1', t.cfg.binding)).state.value == 'PREPARED'
    finally:
        await t.close()
