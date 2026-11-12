"""Observation must preserve the physical owner and transactional failure latch."""
import json
from decimal import Decimal
from test.integration.postgres.test_ownership import owned as _owned
from test.integration.postgres.test_position_checkpoint_batch import snapshot

import pytest
from sqlalchemy import text

from hummingbot.core.engine_diagnostics import EngineDiagnostics

owned = _owned


@pytest.mark.parametrize('fail', [False, True])
def test_diagnostics_keeps_native_owner_and_batch_commit_or_rollback(owned, fail):
    owned.recorder.update_or_store_position(snapshot('long', 'BUY', '.0084'))
    with owned.engine.begin() as connection:
        connection.execute(text('ALTER TABLE positions ADD CONSTRAINT test_positive_amount CHECK (amount > 0)'))
    records = []
    tracer = EngineDiagnostics(emit=records.append)
    owned.loop.run_until_complete(tracer.start())
    tracer.attach_engine(owned.engine)
    identity = owned.owner.owner_uuid, owned.owner.generation, owned.owner.backend_pid
    try:
        with owned.engine.connect() as connection:
            connection.execute(text('SELECT pg_sleep(.12), :private_value'), {'private_value': 'never-copy-pg-parameter'})
        values = [snapshot('ignored-id', 'BUY', '.0089'), snapshot('short', 'SELL', '-.0028' if fail else '.0028')]
        if fail:
            with pytest.raises(Exception, match='test_positive_amount'):
                owned.recorder.update_or_store_positions(values)
        else:
            owned.recorder.update_or_store_positions(values)
        owned.owner.check_owner()
        assert identity == (owned.owner.owner_uuid, owned.owner.generation, owned.owner.backend_pid)
        assert owned.manager.persistence_healthy is (not fail)
        rows = {row.side: row for row in owned.recorder.get_all_positions()}
        assert rows['BUY'].amount == Decimal('.0084' if fail else '.0089')
        assert ('SELL' in rows) is (not fail)
        state = tracer.snapshot()
        assert state['operations']['db.sql']['max_duration_ms'] >= 100
        assert state['operations']['owner.read']['completed']
        assert not state['active']
        assert 'never-copy-pg-parameter' not in json.dumps(state)
        assert 'never-copy-pg-parameter' not in json.dumps(records)
    finally:
        owned.loop.run_until_complete(tracer.close())
    assert not tracer.worker.is_alive() and not tracer.hooks
