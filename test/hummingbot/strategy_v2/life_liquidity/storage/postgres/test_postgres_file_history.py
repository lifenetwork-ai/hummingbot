"""Complete stopped history remains evidence, never native account authority."""
import asyncio
import hashlib
import json
import sys
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_file_staging import (
    fixture as source_fixture,
)

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError, encode
from hummingbot.strategy_v2.life_liquidity.storage.postgres.file_history import PgFileHistoryStaging


async def fixture(database, tmp_path, **kwargs):
    raw, source, command = await source_fixture(database, tmp_path, **kwargs)
    # Explicit synthetic import capacity: includes all normalized history and
    # structural markers, not only source SQL rows/archived monetary records.
    return raw, source, replace(command, max_rows=4096)


@pytest.mark.asyncio
@pytest.mark.parametrize('profile', ['empty', 'paid', 'segmented'])
async def test_history_cold_resume_preserves_operations_terminal_receipts_and_recorder(database, tmp_path, monkeypatch, profile):
    raw, source, command = await fixture(database, tmp_path, profile=profile)
    writer = PgFileHistoryStaging(database[0], timeout_ms=10000)
    await writer.stage(command, raw)
    await writer.translate_financial(command)
    original, calls = writer._history_batch, 0

    async def interrupted(*args):
        nonlocal calls
        await original(*args)
        calls += 1
        if calls == 1:
            raise OSError('after original durable history batch')
    monkeypatch.setattr(writer, '_history_batch', interrupted)
    with pytest.raises(OSError):
        await writer.translate_history(command, batch_rows=2)
    assert await database[0].fetchval('SELECT count(*) FROM life_storage.file_history_rows') == 2
    cold = PgFileHistoryStaging(database[0], timeout_ms=10000)
    assert (await cold.translate_history(command, batch_rows=3)).state == 'HISTORY_STAGED'
    documents = await cold.history_documents(command)
    checkpoint = json.loads(source.base.core.checkpoint_bytes)
    assert documents['checkpoint'] == checkpoint
    assert documents['service'] == json.loads(source.base.service_json)
    assert documents['recorder'] == json.loads(source.base.recorder.rows_json)
    if profile == 'paid':
        resources = checkpoint['state']['resources']
        assert documents['checkpoint']['state']['resources']['terminal_operations'] == resources['terminal_operations']
        assert documents['checkpoint']['state']['resources']['acknowledgements'] == resources['acknowledgements']
        assert documents['checkpoint']['state']['operations'] == checkpoint['state']['operations']
        assert documents['journal:wal']['records'] == json.loads(dict(source.base.core.journal_images)['wal'])['records']
        assert documents['ancillary:quote_actions'] == json.loads(dict(source.base.ancillary_images)['quote_actions'])
        assert documents['recorder']['tables']['Executors']
    if profile == 'segmented':
        assert source.archives
        assert documents['ancillary:shared_capital']['state'] == dict(source.archives)['ancillary.shared_capital'].materialize()['state']
        assert documents['manifest']['archives']

    async def no_batches(*args):
        pytest.fail('sealed replay must verify once, not issue per-batch SQL')
    monkeypatch.setattr(cold, '_history_batch', no_batches)
    assert (await cold.translate_history(command, batch_rows=1)).replayed
    assert await database[0].fetchval('SELECT count(*) FROM life_financial.accounts') == 0
    assert await database[0].fetchval('SELECT count(*) FROM life_financial.activation_grants') == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_lost_history_seal_reply_requires_exact_cold_readback(database, tmp_path, monkeypatch, committed):
    raw, _, command = await fixture(database, tmp_path, profile='paid')
    writer = PgFileHistoryStaging(database[0], timeout_ms=10000)
    await writer.stage(command, raw)
    await writer.translate_financial(command)
    commit = writer._commit

    async def lost(tx):
        if committed:
            await commit(tx)
        raise OSError('lost history seal response')
    monkeypatch.setattr(writer, '_commit', lost)
    with pytest.raises(CommitUnknown):
        await writer.translate_history(command)
    cold = PgFileHistoryStaging(database[0], timeout_ms=10000)
    assert (await cold.translate_history(command)).replayed is committed
    assert await cold.history_documents(command)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['rehashed_terminal', 'missing_row', 'extra_row', 'seal'])
async def test_cold_history_parity_refuses_tampered_ancestry_even_with_valid_hash(database, tmp_path, fault):
    raw, _, command = await fixture(database, tmp_path, profile='paid')
    writer = PgFileHistoryStaging(database[0], timeout_ms=10000)
    await writer.stage(command, raw)
    await writer.translate_financial(command)
    await writer.translate_history(command)
    c = database[0]
    table = 'file_history_seals' if fault == 'seal' else 'file_history_rows'
    await c.execute(f'ALTER TABLE life_storage.{table} DISABLE TRIGGER USER')
    try:
        if fault == 'rehashed_terminal':
            row = await c.fetchrow('''SELECT * FROM life_storage.file_history_rows WHERE document='checkpoint'
                AND jsonb_array_length(path)=4 AND path->>2='terminal_operations' LIMIT 1''')
            assert row is not None
            replacement = encode({})
            await c.execute('UPDATE life_storage.file_history_rows SET body=$1,sha256=$2 WHERE sequence=$3',
                            replacement, hashlib.sha256(replacement).hexdigest(), row['sequence'])
        elif fault == 'missing_row':
            await c.execute('DELETE FROM life_storage.file_history_rows WHERE sequence=0')
        elif fault == 'extra_row':
            await c.execute('''INSERT INTO life_storage.file_history_rows
                SELECT operation_id,999999,'foreign','[]'::jsonb,'{}'::bytea,encode(sha256('{}'::bytea),'hex')
                FROM life_storage.file_history_rows LIMIT 1''')
        else:
            await c.execute("UPDATE life_storage.file_history_seals SET manifest_sha256=repeat('0',64)")
    finally:
        await c.execute(f'ALTER TABLE life_storage.{table} ENABLE TRIGGER USER')
    with pytest.raises(StorageError):
        await PgFileHistoryStaging(c, timeout_ms=10000).history_documents(command)
    assert await c.fetchval('SELECT count(*) FROM life_financial.accounts') == 0


@pytest.mark.asyncio
async def test_history_requires_original_financial_seal_and_combined_capacity(database, tmp_path):
    raw, _, command = await fixture(database, tmp_path, profile='paid')
    writer = PgFileHistoryStaging(database[0], timeout_ms=10000)
    await writer.stage(command, raw)
    with pytest.raises(StorageError, match='FINANCIAL_INCOMPLETE'):
        await writer.translate_history(command)
    await writer.translate_financial(command)
    # Changing the request cannot evade its original sealed bounds.
    with pytest.raises(StorageError):
        await writer.translate_history(replace(command, max_rows=2))
    assert await database[0].fetchval('SELECT count(*) FROM life_storage.file_history_rows') == 0


def test_history_reconstruction_preserves_list_order_empty_values_and_original_ids():
    from types import SimpleNamespace
    documents = {'recorder': {'tables': {'Trades': [{'id': n, 'timestamp': 1.0, 'amount': '0.000000000000000001'} for n in range(15)]}},
                 'checkpoint': {'empty_map': {}, 'empty_list': [], 'false': False, 'nil': None,
                                'operations': {'original:id/with~escapes': {'revision': 3}}}}
    rows = PgFileHistoryStaging._history_rows(documents, SimpleNamespace(max_bytes=100000, max_rows=1000))
    assert PgFileHistoryStaging._restore_documents(rows) == documents
    with pytest.raises(StorageError, match='CAPACITY'):
        PgFileHistoryStaging._history_rows(documents, SimpleNamespace(max_bytes=10, max_rows=1000))
    with pytest.raises(StorageError, match='CAPACITY'):
        PgFileHistoryStaging._history_rows(documents, SimpleNamespace(max_bytes=100000, max_rows=2))
    with pytest.raises(StorageError, match='JSON_INVALID'):
        PgFileHistoryStaging._history_rows({'service': {'invalid': float('nan')}}, SimpleNamespace(max_bytes=100000, max_rows=1000))


@pytest.mark.asyncio
async def test_killed_history_importer_resumes_original_records_in_a_fresh_process(database, tmp_path):
    raw, source, command = await fixture(database, tmp_path, profile='paid')
    writer = PgFileHistoryStaging(database[0], timeout_ms=10000)
    await writer.stage(command, raw)
    await writer.translate_financial(command)
    code = '''
import asyncio,json,os,sys
from uuid import UUID
import asyncpg
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StateBinding
from hummingbot.strategy_v2.life_liquidity.storage.postgres.file_staging import FileStageRequest
from hummingbot.strategy_v2.life_liquidity.storage.postgres.file_history import PgFileHistoryStaging
async def main():
    body=json.loads(sys.argv[2])
    for key in ('account_id','database_lineage'): body[key]=UUID(body[key])
    body['target_binding']=StateBinding.from_json(body['target_binding'])
    c=await asyncpg.connect(sys.argv[1])
    writer=PgFileHistoryStaging(c,timeout_ms=10000)
    original=writer._history_batch
    async def die(*args):
        await original(*args)
        os._exit(92)
    writer._history_batch=die
    await writer.translate_history(FileStageRequest(**body),batch_rows=2)
asyncio.run(main())
'''
    process = await asyncio.create_subprocess_exec(sys.executable, '-c', code, database[2], json.dumps(command.json()),
                                                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        output, error = await asyncio.wait_for(process.communicate(), 30)
        assert process.returncode == 92, (output, error)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert await database[0].fetchval('SELECT count(*) FROM life_storage.file_history_rows') == 2
    cold = PgFileHistoryStaging(database[0], timeout_ms=10000)
    assert not (await cold.translate_history(command)).trading_permission
    assert (await cold.history_documents(command))['checkpoint'] == json.loads(source.base.core.checkpoint_bytes)
