"""One existing recorder transaction persists a shutdown group atomically."""
import json
from test.integration.postgres.test_ownership import owned as _owned
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError

from hummingbot.model.executors import Executors

owned = _owned


def executor(identity, **updates):
    data = dict(id=identity, timestamp=1000, type='position_executor', status=3, config={'id': identity}, net_pnl_pct=0,
                net_pnl_quote=0, cum_fees_quote=.1, filled_amount_quote=10, is_active=False, is_trading=False, custom_info={}, controller_id='one')
    data.update(updates)
    return SimpleNamespace(config=SimpleNamespace(id=identity), executor_info=SimpleNamespace(model_dump_json=lambda: json.dumps(data)))


def test_batch_uses_one_lookup_and_one_ownership_fence_for_all_rows(owned):
    queries = []

    def count(connection, cursor, statement, *args):
        if statement.startswith('SELECT') and 'FROM executors' in statement:
            queries.append(statement)
    event.listen(owned.engine, 'before_cursor_execute', count)
    try:
        with patch.object(owned.owner, 'check_owner', wraps=owned.owner.check_owner) as fence:
            owned.recorder.store_or_update_executors([executor(str(i)) for i in range(32)])
        assert fence.call_count == 1
        assert len(queries) == 1
    finally:
        event.remove(owned.engine, 'before_cursor_execute', count)
    with owned.engine.connect() as connection:
        assert len(connection.execute(select(Executors.id)).all()) == 32
    owned.recorder.store_or_update_executors([executor('0', net_pnl_quote=9), executor('new')])
    with owned.engine.connect() as connection:
        assert connection.execute(select(Executors.net_pnl_quote).where(Executors.id == '0')).scalar() == 9


def test_failed_batch_rolls_back_updates_and_inserts_together(owned):
    owned.recorder.store_or_update_executor(executor('old', net_pnl_quote=1))
    with pytest.raises(IntegrityError):
        owned.recorder.store_or_update_executors([executor('old', net_pnl_quote=9), executor('new', type=None)])
    with owned.engine.connect() as connection:
        assert connection.execute(select(Executors.id, Executors.net_pnl_quote)).all() == [('old', 1)]


def test_stale_owner_cannot_commit_a_batch(owned):
    from hummingbot.core.order_submission_guard import SubmissionBlocked
    owned.owner.release()
    with pytest.raises(SubmissionBlocked):
        owned.recorder.store_or_update_executors([executor('new')])
    with owned.engine.connect() as connection:
        assert not connection.execute(select(Executors.id)).first()
