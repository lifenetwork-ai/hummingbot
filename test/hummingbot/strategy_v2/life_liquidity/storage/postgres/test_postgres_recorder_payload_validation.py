"""Historical recorder validation keeps numeric casts and head bytes intact."""
import json
from datetime import timedelta
from pathlib import Path
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.restore_capacity_cases import MODEL, seed_completed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_history_flow import calls, instrument
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_restore_matrix import source

import asyncpg
import pytest


@pytest.mark.asyncio
async def test_recorder_candidate_validates_every_old_payload_and_preserves_native_heads(database, request):
    c = database[0]
    values, _, _ = await source(database, managed_options=dict(owner_duration_ms=300000,
                                                               spot_options=dict(session_duration=timedelta(minutes=10))),
                                spot_quote_producer=MODEL)
    f, cfg, owner, _, lease, *_ = values
    try:
        await seed_completed(database, f, cfg, owner, lease, 4, {})
    finally:
        await f.close()
    await c.execute('UPDATE life_financial.access_bindings SET can_write=false WHERE principal=current_user AND account_id=$1', cfg.account_id)
    before = await c.fetchval('SELECT life_financial.spot_body($1)::text', cfg.account_id)
    await c.execute(Path(__file__).with_name('recorder_payload_validation.sql').read_text())
    assert await c.fetchval('SELECT life_financial.spot_body($1)::text', cfg.account_id) == before
    counter = await instrument(c, 'recorder_executor_checked_row', 'jsonb,boolean')
    await calls(c, counter, reset=True)
    assert await c.fetchval('SELECT life_financial.recorder_executor_only_integrity($1)', cfg.account_id)
    assert await calls(c, counter) == 8  # Four old INITIALs and four current heads.
    request.node.user_properties.append(('recorder_candidate_all_deliveries_validated', '8'))
    payloads = await c.fetch("SELECT (body#>>'{command,payload_json}')::jsonb AS payload FROM life_financial.recorder_outbox")

    async def result(query, payload):
        try:
            async with c.transaction():
                value = await c.fetchval(query, json.dumps(payload))
            # Compare the exact normalized JSON text, including number scale.
            return 'value', value
        except asyncpg.PostgresError as error:
            return 'error', error.sqlstate

    edits = (
        {}, {'timestamp': '1.0000000000000000001'}, {'net_pnl_quote': '-0.000000000000000001'},
        {'net_pnl_quote': '1e10000'}, {'close_timestamp': '9223372036854775808'},
        {'filled_amount_quote': '-1'}, {'net_pnl_pct': 'NaN'}, {'cum_fees_quote': 'Infinity'},
        {'close_type': 'bad'}, {'close_timestamp': 'bad'}, {'status': '4'}, {'is_active': 'false'},
        {'custom_info': None}, {'controller_id': 'bad id'}, {'extra': 1},
    )
    for row in payloads:
        original = json.loads(row['payload'])
        for edit in edits:
            payload = {**original, **edit}
            old = await result('SELECT life_financial.recorder_executor_row($1)', payload)
            head = await result('SELECT life_financial.recorder_executor_checked_row($1,true)', payload)
            historical = await result('SELECT life_financial.recorder_executor_checked_row($1,false)', payload)
            assert head == old, (edit, old, head)
            assert historical == (('value', None) if old[0] == 'value' else old), (edit, old, historical)
    for role in ('public', 'life_pg2_runtime', 'life_pg2_reporter'):
        assert not await c.fetchval("SELECT has_function_privilege($1,'life_financial.recorder_executor_checked_row(jsonb,boolean)','EXECUTE')", role)
