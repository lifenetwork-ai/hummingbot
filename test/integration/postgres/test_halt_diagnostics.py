"""Publication returns bounded safe HALT facts on the real PostgreSQL path."""
import json

import pytest
from sqlalchemy import text

from hummingbot.core.bot_ownership import BotOwnership, OwnershipLost
from hummingbot.core.halt_diagnostics import RUNTIME_REASON, halt_cause
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres


def test_publication_retains_legacy_halt_but_excludes_private_and_large_metadata(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    store = BotStateStore(pg_engine, 'hummingbot')
    store.ensure()
    recovery = dict(halt=True, halt_kind='manual', reason=RUNTIME_REASON,
                    strategy_checkpoints={'private': 'x' * 100000},
                    halt_diagnostic=dict(reason_code='runtime_task_failed', occurred_at=1794817014,
                                         revision='hummingbot-demo-00158-dxf', runtime_reason_code='connector_task_stopped',
                                         component='okx_perpetual', task='_user_stream_tracker_task', task_state='completed',
                                         private_secret='do-not-transfer', details='x' * 100000))
    with pg_engine.begin() as c:
        c.execute(text('UPDATE bot_state SET recovery_state=CAST(:state AS jsonb)'), {'state': json.dumps(recovery)})
    row = store.read(publication=True)
    assert 'recovery_state' not in row
    summary = row['recovery_summary']
    assert len(json.dumps(summary)) < 2000
    assert 'do-not-transfer' not in json.dumps(summary)
    cause = halt_cause(summary)
    assert cause['reason_code'] == 'runtime_task_failed'
    assert cause['component'] == 'okx_perpetual'
    assert cause['occurred_at'] == 1794817014
    assert cause['revision'] == 'hummingbot-demo-00158-dxf'
    with pg_engine.begin() as c:
        c.execute(text("UPDATE bot_state SET recovery_state=jsonb_set(recovery_state, '{reason}', to_jsonb(CAST(:reason AS text)))"),
                  {'reason': 'private-secret-error'})
    assert store.read(publication=True)['recovery_summary']['reason'] is None
    # Full control/recovery reads preserve the saved state for verification.
    assert store.read()['recovery_state']['reason'] == 'private-secret-error'
    with pg_engine.begin() as c:
        c.execute(text('UPDATE bot_state SET recovery_state=CAST(:state AS jsonb)'), {'state': json.dumps(dict(
            halt=True, halt_kind='shutdown_pending', reason='private-unrecognized-risk',
            automatic_recovery=dict(cold_recovery_supported=True)))})
    cause = halt_cause(store.read(publication=True)['recovery_summary'])
    assert cause['reason_code'] == 'halt_reason_unknown'
    assert not cause['automatic_recovery_expected']


@pytest.mark.parametrize('failure', ['unlock', 'terminate'])
def test_real_owner_session_failure_has_specific_code_without_reconnection(pg_engine, failure):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    store = BotStateStore(pg_engine, 'hummingbot')
    store.ensure()
    owner = BotOwnership(pg_engine, 'hummingbot')
    assert owner.try_acquire()
    connection = owner.connection
    owner.allow_new_orders = True
    try:
        if failure == 'unlock':
            connection.execute(text('SELECT pg_advisory_unlock_all()'))
            connection.commit()
        else:
            with pg_engine.begin() as c:
                assert c.execute(text('SELECT pg_terminate_backend(:pid)'), {'pid': owner.backend_pid}).scalar()
        with pytest.raises(OwnershipLost) as caught:
            owner.check_owner()
        assert caught.value.reason_code == ('ownership_identity_mismatch' if failure == 'unlock' else 'ownership_database_error')
        assert owner.lost and not owner.allow_new_orders
        assert owner.connection is connection
        with pytest.raises(OwnershipLost):
            owner.try_acquire()
    finally:
        if not connection.closed:
            connection.invalidate()
            connection.close()
