"""Canonical fingerprint and permanent recorder identity bytes stay unchanged."""
import json
import random

import asyncpg
import pytest

from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import migrate


@pytest.mark.asyncio
async def test_large_ascii_identity_fits_the_existing_statement_limit(database):
    c = database[0]
    raw = json.dumps('a' * (512 * 1024), ensure_ascii=True)
    # A valid input below the existing one-MiB command bound. This exercises
    # linear string handling, not a calibrated wall-clock benchmark assertion.
    async with c.transaction():
        await c.execute("SET LOCAL statement_timeout='1000ms'")
        assert await c.fetchval('SELECT life_financial.recorder_identity_text($1::jsonb)', raw) == raw


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [94], indirect=True)
async def test_identity_upgrade_preserves_unicode_numeric_and_permanent_id_bytes(database):
    c = database[0]
    old = await c.fetchval("SELECT pg_get_functiondef('life_financial.recorder_identity_text(jsonb)'::regprocedure)")
    await c.execute('CREATE SCHEMA identity_reference')
    await c.execute(old.replace('life_financial.recorder_identity_text', 'identity_reference.original'))
    rng = random.Random(95)
    strings = ['', 'ascii', 'a b', '\\"\n\r\t\b\f', '\x7f', 'é', 'e\u0301', '中', '😀', '\u2028']
    strings += [chr(code) for code in range(1, 128)]
    strings += [chr(rng.choice([rng.randrange(128, 0xd800), rng.randrange(0xe000, 0x110000)])) for _ in range(256)]
    values = [None, 0, 1, 10**30, *strings]
    values += [{'source': {'controller_id': s, 'market': 'okx', 'path': strings[i % len(strings)]},
                'wire': 'wire1', 'kind': 'OrderFilled', 'trade_id': s} for i, s in enumerate(strings)]
    raw = [json.dumps(v, ensure_ascii=True) for v in values]
    before = await c.fetch('SELECT identity_reference.original(v::jsonb) AS body FROM unnest($1::text[]) WITH ORDINALITY AS e(v,n) ORDER BY n', raw)
    assert (await migrate(c, action='up'))['applied'] == ['000095_recorder_verification_reads.sql', '000096_recorder_history_lookups.sql',
                                                          '000097_atomic_metadata_dispatch.sql', '000098_native_quote_capture.sql', '000099_provider_collection.sql']
    after = await c.fetch('SELECT life_financial.recorder_identity_text(v::jsonb) AS body FROM unnest($1::text[]) WITH ORDINALITY AS e(v,n) ORDER BY n', raw)
    assert before == after
    for value, row in zip(values, after):
        assert row['body'] == json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
    for raw_value in ('true', 'false', '[]', '[1]', '-1', '1.0', '{"x":false}'):
        for function in ('identity_reference.original', 'life_financial.recorder_identity_text'):
            with pytest.raises(asyncpg.RaiseError, match='LIFE_EVENT_IDENTITY_INVALID'):
                await c.fetchval('SELECT ' + function + '($1::jsonb)', raw_value)
    assert (await migrate(c, action='up'))['applied'] == []
