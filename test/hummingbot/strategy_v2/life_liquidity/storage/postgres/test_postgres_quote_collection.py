"""A quote source audits the complete ledger before publishing its report."""
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_market import market

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


@pytest.mark.asyncio
async def test_quote_collection_audits_once_after_signed_reads_without_granting_money(database, monkeypatch):
    t = await market(database, clean=True, stream_market=True)
    collector = t.source.collector
    original = collector._collection_context
    audits = []

    async def audited(spot_policy):
        audits.append((spot_policy, len(t.requests)))
        return await original(spot_policy)
    monkeypatch.setattr(collector, '_collection_context', audited)
    try:
        before = await t.financial.snapshot(t.cfg.binding)
        capture = await t.market_source.collect()
        assert len(audits) == 1 and audits[0][0] is True
        assert audits[0][1] == len(t.requests) and audits[0][1] > 0
        assert not capture.trading_permission and not capture.report.trading_permission
        after = await t.financial.snapshot(t.cfg.binding)
        assert (after.cut, after.balance, after.budget) == (before.cut, before.balance, before.budget)
        assert all(row['method'] == 'GET' for row in t.requests)
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['initial_scope', 'provider_round', 'final_scope'])
async def test_quote_collection_keeps_original_epoch_across_opening_provider_and_closing_reads(database, monkeypatch, boundary):
    t = await market(database, clean=True, stream_market=True)
    collector = t.source.collector
    target = {'initial_scope': '_quote_collection_opening', 'provider_round': '_round',
              'final_scope': '_collection_context'}[boundary]
    original = getattr(collector, target)

    async def changed(*args, **kwargs):
        result = await original(*args, **kwargs)
        t.financial._runtime._ingress_epoch += 1
        return result
    monkeypatch.setattr(collector, target, changed)
    try:
        before = await t.financial.snapshot(t.cfg.binding)
        with pytest.raises(StorageError, match='PG_JOINT_SOURCE_INGRESS_CHANGED'):
            await t.market_source.collect()
        assert collector._report is None and not t.session.sent
        after = await t.financial.snapshot(t.cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('during', [False, True])
async def test_quote_collection_never_publishes_a_report_from_same_version_corrupt_money(database, monkeypatch, during):
    t = await market(database, clean=True, stream_market=True)
    c = database[0]
    original = t.session.request
    corrupted = False

    async def corrupt():
        nonlocal corrupted
        corrupted = True
        await c.execute('ALTER TABLE life_financial.balances DISABLE TRIGGER USER')
        await c.execute("UPDATE life_financial.balances SET total=total+1 WHERE account_id=$1 AND asset='USDT'", t.cfg.account_id)
        await c.execute('ALTER TABLE life_financial.balances ENABLE TRIGGER USER')

    async def http(**kwargs):
        if not corrupted:
            await corrupt()
        return await original(**kwargs)
    try:
        if during:
            monkeypatch.setattr(t.session, 'request', http)
        else:
            await corrupt()
        with pytest.raises(StorageError, match='PG_COLLATERAL_NATIVE_UNVERIFIED'):
            await t.market_source.collect()
        assert corrupted
        assert t.source.collector._report is None
        assert not t.session.sent
        assert await c.fetchval('SELECT count(*) FROM life_financial.send_attempts WHERE product=\'SPOT\'') == 0
    finally:
        await t.financial.close()
