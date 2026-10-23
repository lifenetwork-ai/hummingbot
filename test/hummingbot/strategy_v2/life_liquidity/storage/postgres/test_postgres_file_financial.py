"""Relational financial staging retains original File facts, without authority."""
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_file_staging import fixture

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.file_financial import PgFileFinancialStaging


@pytest.mark.asyncio
@pytest.mark.parametrize('profile', ['empty', 'paid', 'segmented'])
async def test_financial_rows_resume_and_cold_compare_without_creating_account(database, tmp_path, monkeypatch, profile):
    raw, source, command = await fixture(database, tmp_path, profile=profile)
    writer = PgFileFinancialStaging(database[0], timeout_ms=10000)
    await writer.stage(command, raw)
    original = writer._financial_batch
    batches = 0

    async def interrupted(*args):
        nonlocal batches
        await original(*args)
        batches += 1
        if batches == 1:
            raise OSError('after durable financial batch')
    monkeypatch.setattr(writer, '_financial_batch', interrupted)
    with pytest.raises(OSError):
        await writer.translate_financial(command, batch_rows=2)
    assert await database[0].fetchval('SELECT count(*) FROM life_storage.file_financial_rows') == 2
    cold = PgFileFinancialStaging(database[0], timeout_ms=10000)
    receipt = await cold.translate_financial(command, batch_rows=2)
    assert receipt.state == 'FINANCIAL_STAGED' and not receipt.trading_permission
    rows = await cold.financial_rows(command)
    assert rows == writer._financial_facts(source, command)
    assert (await cold.translate_financial(command, batch_rows=3)).replayed
    if profile == 'paid':
        import json
        fills = {key: json.loads(body) for family, key, body in rows if family == 'FILL'}
        assert fills['trade1']['fill']['signed_fee'] == '-0.02'
        original = json.loads(source.base.core.checkpoint_bytes)
        assert fills == original['state']['resources']['fills']
        navs = {key: json.loads(body) for family, key, body in rows if family == 'NAV'}
        assert navs == original['state']['resources']['nav_operations'] and 'nav1' in navs
        policies = [json.loads(body) for family, key, body in rows if family == 'ACCOUNTING_POLICY']
        assert policies == [original['policy']['legacy_spot']['accounting_policy']]
        balances = {key: json.loads(body) for family, key, body in rows if family == 'BALANCE'}
        assert balances['USDT'] == '8.98'
        totals = {family: json.loads(body) for family, key, body in rows if family.endswith('_CAMPAIGN_TOTAL')}
        assert totals['LOSS_CAMPAIGN_TOTAL'] == totals['SUBSIDY_CAMPAIGN_TOTAL'] == '0.12'
    assert await database[0].fetchval('SELECT count(*) FROM life_financial.accounts') == 0
    assert await database[0].fetchval('SELECT count(*) FROM life_financial.activation_grants') == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_unknown_financial_seal_resolves_by_exact_cold_readback(database, tmp_path, monkeypatch, committed):
    raw, _, command = await fixture(database, tmp_path, profile='paid')
    writer = PgFileFinancialStaging(database[0], timeout_ms=10000)
    await writer.stage(command, raw)
    commit = writer._commit

    async def lost(tx):
        if committed:
            await commit(tx)
        raise OSError('lost seal response')
    monkeypatch.setattr(writer, '_commit', lost)
    with pytest.raises(CommitUnknown):
        await writer.translate_financial(command)
    cold = PgFileFinancialStaging(database[0], timeout_ms=10000)
    assert (await cold.translate_financial(command)).replayed is committed
    assert await cold.financial_rows(command)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['rehashed_row', 'missing_row', 'seal'])
async def test_cold_financial_parity_rejects_owner_corruption(database, tmp_path, fault):
    raw, _, command = await fixture(database, tmp_path, profile='paid')
    writer = PgFileFinancialStaging(database[0], timeout_ms=10000)
    await writer.stage(command, raw)
    await writer.translate_financial(command)
    table = 'file_financial_seals' if fault == 'seal' else 'file_financial_rows'
    await database[0].execute(f'ALTER TABLE life_storage.{table} DISABLE TRIGGER USER')
    try:
        if fault == 'rehashed_row':
            await database[0].execute("UPDATE life_storage.file_financial_rows SET body='{}'::bytea,sha256=encode(sha256('{}'::bytea),'hex') WHERE sequence=0")
        elif fault == 'missing_row':
            await database[0].execute('DELETE FROM life_storage.file_financial_rows WHERE sequence=0')
        else:
            await database[0].execute("UPDATE life_storage.file_financial_seals SET manifest_sha256=repeat('0',64)")
    finally:
        await database[0].execute(f'ALTER TABLE life_storage.{table} ENABLE TRIGGER USER')
    with pytest.raises(StorageError):
        await PgFileFinancialStaging(database[0], timeout_ms=10000).financial_rows(command)
    assert await database[0].fetchval('SELECT count(*) FROM life_financial.accounts') == 0
