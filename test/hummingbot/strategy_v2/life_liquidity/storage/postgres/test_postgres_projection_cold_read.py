"""Fresh-process-plan cancel controls; individual passes do not explain a tail."""
import json
from dataclasses import asdict
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_fill_projection import install
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_lifecycle import (
    test_unknown_send_cannot_abort_and_cancel_retains_money_and_budget as original_cancel,
)

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('enrolled', [False, True], ids=['native', 'candidate'])
@pytest.mark.parametrize('repeat', range(3))
async def test_cold_full_state_cancel_with_unchanged_sql_deadline(database, request, enrolled, repeat):
    if enrolled:
        await install(database[0], economics=True)
    try:
        await original_cancel(database)
    finally:
        samples = [asdict(s) for runtime in request.node._pg_diagnostic_runtimes
                   for s in runtime.diagnostics if s.operation == 'spot_state' or s.error_kind is not None]
        request.node.user_properties.append(('projection_cold_reads', json.dumps(dict(
            enrolled=enrolled, repeat=repeat, samples=samples), default=str)))
    reads = [s for s in samples if s['operation'] == 'spot_state']
    assert reads and all(s['outcome'] == 'success' for s in reads)
