"""Compact wire projection retains the complete primary audit and state cut."""
import json
from datetime import timedelta
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.restore_capacity_cases import MODEL, seed_completed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_history_flow import calls, instrument
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_restore_matrix import source

import pytest


@pytest.mark.asyncio
async def test_snapshot_omits_retained_wire_history_but_keeps_the_full_audit_and_exact_dto(database, monkeypatch, request):
    values, _, _ = await source(database, managed_options=dict(lease_ms=10000, owner_duration_ms=300000,
                                                               spot_options=dict(session_duration=timedelta(minutes=10))),
                                spot_quote_producer=MODEL)
    f, cfg, owner, _, lease, *_ = values
    c = database[0]
    try:
        await seed_completed(database, f, cfg, owner, lease, 6, {})
        full = json.loads(await c.fetchval('SELECT life_financial.spot_state($1)::text', cfg.account_id))
        expected = f._snapshot(full, cfg.binding)
        sequence = await instrument(c, 'spot_state')
        admit, wire = f._runtime._admit, []

        async def captured(fn, **kwargs):
            result = await admit(fn, **kwargs)
            if kwargs.get('operation') == 'spot_state':
                wire.append(result)
            return result
        monkeypatch.setattr(f._runtime, '_admit', captured)
        actual = await f.snapshot(cfg.binding)
        assert actual == expected
        assert await calls(c, sequence) == 1
        assert len(wire) == 1
        assert 'quote_actions' not in wire[0] and 'quote_births' not in wire[0]
        sizes = dict(full_bytes=len(json.dumps(full)), wire_bytes=len(json.dumps(wire[0])))
        request.node.user_properties.append(('snapshot_payload_bytes', json.dumps(sizes)))
        assert sizes['wire_bytes'] < sizes['full_bytes'] / 2
        # An old retained ticket remains independently audited after the public
        # result stops transporting its JSON/hash arrays to Python.
        await c.execute('ALTER TABLE life_financial.quote_births DISABLE TRIGGER USER')
        await c.execute("UPDATE life_financial.quote_births SET body_hash=repeat('f',64) WHERE ticket_id='birth:capacity-0'")
        await c.execute('ALTER TABLE life_financial.quote_births ENABLE TRIGGER USER')
        damaged = await f.snapshot(cfg.binding)
        assert damaged.recovery_required and damaged.session.state == 'PAUSED'
    finally:
        await f.close()
