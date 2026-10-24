"""Opt-in retained-flow diagnosis, separate from capacity acceptance."""
import json
from datetime import timedelta
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.restore_capacity_cases import (
    HISTORY_RETENTION,
    RETENTION_MODEL,
    seed_completed,
)
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_restore_matrix import source
from time import perf_counter

import pytest


@pytest.mark.asyncio
@pytest.mark.timeout(1800)
async def test_retained_source_flow_growth(database, request, monkeypatch):
    """Diagnostic genuine 32/64/128/160-order flow, not capacity acceptance.

    Optional PostgreSQL function totals add diagnostic overhead, are sampled
    only between completed orders and may lag their backend's statistics flush.
    Inclusive function time is not CPU time or acceptance timing.
    """
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.retained_flow_profile import RetainedFlowProfile

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.diagnostics import _Trace

    profile = RetainedFlowProfile()
    original = _Trace.finish

    def finished(trace, outcome):
        sample = original(trace, outcome)
        profile.record(sample)
        return sample

    monkeypatch.setattr(_Trace, 'finish', finished)
    values, _, _ = await source(database, managed_options=dict(owner_duration_ms=1800000,
                                                               spot_options=dict(session_duration=timedelta(hours=1), safety_size=2)),
                                spot_quote_producer=RETENTION_MODEL)
    f, cfg, owner, _, lease, *_ = values
    progress = dict(requested_orders=160, completed_orders=0, diagnostic_only=True)
    identities = await database[0].fetch('''SELECT proname,encode(sha256(convert_to(prosrc,'UTF8')),'hex') AS sha256
        FROM pg_proc WHERE pronamespace='life_financial'::regnamespace AND proname IN (
            'spot_actions_verified','spot_actions_before_key_joins','recorder_executor_only_integrity',
            'recorder_executor_checked_row','recorder_executor_row','recorder_payload_value','recorder_envelope_hash',
            'spot_inventory_settlements_verified','spot_inventory_commit_guard') ORDER BY proname''')
    progress['database_function_sources'] = [dict(row) for row in identities]
    progress['track_functions'] = await database[0].fetchval("SELECT current_setting('track_functions')")
    checkpoints, started = [], perf_counter()
    # Start after managed-account setup. Each bucket contains the original
    # whole source flow, including its reads and physical COMMIT diagnostics.
    profile.reset()

    async def checkpoint(completed):
        if completed not in (32, 64, 128, 160):
            return
        row = profile.checkpoint(completed)
        c = database[0]
        await c.execute('SELECT pg_stat_clear_snapshot()')
        functions = await c.fetch('''SELECT funcname,calls,round(total_time::numeric,3) AS total_ms,
            round(self_time::numeric,3) AS self_ms FROM pg_stat_user_functions
            WHERE schemaname='life_financial' AND calls>0 ORDER BY self_time DESC LIMIT 20''')
        row['cumulative_functions_possibly_lagged'] = [dict(r) for r in functions]
        checkpoints.append(row)
        print('life_retained_flow_checkpoint=' + json.dumps(row, default=str), flush=True)
        profile.reset()

    try:
        await seed_completed(database, f, cfg, owner, lease, 160, progress,
                             model=RETENTION_MODEL, retention=HISTORY_RETENTION, on_progress=checkpoint)
        assert progress['completed_orders'] == 160
    finally:
        # Preserve a partial/failing interval too. No stopped diagnostic run
        # can produce a capacity or restore completion result.
        partial = profile.checkpoint(progress['completed_orders'])
        progress.update(elapsed_ms=round((perf_counter() - started) * 1000, 3), checkpoints=checkpoints, partial=partial)
        request.node.user_properties.append(('life_retained_flow_growth', json.dumps(progress, default=str)))
        print('life_retained_flow_growth=' + json.dumps(progress, default=str), flush=True)
        await f.close()
