"""Observation must preserve the physical owner and transactional failure latch."""
import json
from decimal import Decimal
from test.integration.postgres.test_ownership import owned as _owned
from test.integration.postgres.test_position_checkpoint_batch import snapshot

import pytest
from sqlalchemy import text

from hummingbot.core.engine_diagnostics import EngineDiagnostics

owned = _owned


@pytest.mark.parametrize('halt_only', [False, True])
def test_owner_observation_uses_its_select_as_the_pool_liveness_check(owned, halt_only, monkeypatch):
    import hummingbot.core.engine_diagnostics as diagnostic

    owned.engine.pool.dispose()
    with owned.engine.connect() as connection:
        connection.execute(text('SELECT 1'))
    tracer = EngineDiagnostics(emit=lambda _: None)
    tracer.attach_engine(owned.engine)
    monkeypatch.setattr(diagnostic, '_ACTIVE', tracer)
    try:
        row = owned.loop.run_until_complete(owned.owner.observe_state(halt_only=halt_only))
        if halt_only:
            assert 'halt' in row and not row['halt']
        else:
            assert row['owner_generation'] == owned.owner.generation
        operations = tracer.snapshot()['operations']
        assert operations['db.sql']['completed'] == 1
        assert 'db.ping' not in operations
        with owned.engine.connect() as connection:
            assert connection.execute(text('SELECT 1')).scalar_one() == 1
        assert tracer.snapshot()['operations']['db.ping']['completed'] == 1
    finally:
        tracer.detach_engine()


def test_owner_observation_recovers_from_a_cut_pooled_session(owned, monkeypatch):
    owned.engine.pool.dispose()
    physical = owned.owner.connection
    with owned.engine.connect() as connection:
        pooled_pid = connection.execute(text('SELECT pg_backend_pid()')).scalar_one()
    assert owned.owner.connection.scalar(text('SELECT pg_terminate_backend(:pid, 2000)'), {'pid': pooled_pid})
    owned.owner.connection.commit()
    monkeypatch.setattr(owned.owner, 'check_owner', lambda: pytest.fail('A disconnected reader must retry off the engine loop'))
    row = owned.loop.run_until_complete(owned.owner.observe_state())
    assert row['owner_uuid'] == owned.owner.owner_uuid and owned.owner.connection is physical
    with owned.engine.connect() as connection:
        assert connection.execute(text('SELECT pg_backend_pid()')).scalar_one() != pooled_pid


def test_pre_ping_is_measured_separately_from_cursor_sql(pg_engine, monkeypatch):
    import hummingbot.core.engine_diagnostics as diagnostic
    from hummingbot.client.config.runtime_config import RuntimeSettings
    from hummingbot.model.db_migration.postgres.connection import create_postgres_engine

    clock = [1_000_000_000]
    tracer = EngineDiagnostics(emit=lambda _: None, clock=lambda: clock[0])
    monkeypatch.setattr(diagnostic, '_ACTIVE', tracer)
    dialect = type(pg_engine.dialect)
    native_ping = dialect.do_ping
    calls = []

    def delayed_ping(self, raw):
        calls.append(raw)
        clock[0] += 120_000_000
        return native_ping(self, raw)

    monkeypatch.setattr(dialect, 'do_ping', delayed_ping)
    engine = create_postgres_engine(RuntimeSettings.from_environment({
        'DATABASE_URL': pg_engine.url.render_as_string(hide_password=False)}))
    tracer.attach_engine(engine)
    try:
        with engine.connect() as connection:
            assert connection.execute(text('SELECT 7')).scalar_one() == 7
        with engine.connect() as connection:
            assert connection.execute(text('SELECT :secret'), {'secret': 'never-export-ping-parameter'}).scalar_one()
        snapshot = tracer.snapshot()
        assert len(calls) == 1
        assert snapshot['operations']['db.sql']['completed'] == 2
        assert snapshot['operations']['db.ping']['completed'] == 1
        assert snapshot['operations']['db.ping']['max_duration_ms'] == 120
        assert not snapshot['active'] and 'never-export' not in json.dumps(snapshot)
    finally:
        tracer.detach_engine()
        engine.dispose()


def test_pre_ping_observation_preserves_stale_connection_replacement(pg_engine, monkeypatch):
    import hummingbot.core.engine_diagnostics as diagnostic
    from hummingbot.client.config.runtime_config import RuntimeSettings
    from hummingbot.model.db_migration.postgres.connection import create_postgres_engine

    tracer = EngineDiagnostics(emit=lambda _: None)
    monkeypatch.setattr(diagnostic, '_ACTIVE', tracer)
    engine = create_postgres_engine(RuntimeSettings.from_environment({
        'DATABASE_URL': pg_engine.url.render_as_string(hide_password=False)}))
    try:
        with engine.connect() as connection:
            old_pid = connection.execute(text('SELECT pg_backend_pid()')).scalar_one()
            raw = connection.connection.driver_connection
        raw.close()  # Native pre-ping must reject/reconnect this pooled reader.
        with engine.connect() as connection:
            assert connection.execute(text('SELECT pg_backend_pid()')).scalar_one() != old_pid
        state = tracer.snapshot()
        assert state['operations']['db.ping']['completed'] == 1
        assert state['operations']['db.ping']['errors'] == 1
        assert not state['active']
    finally:
        engine.dispose()


def test_pre_ping_diagnostic_failure_cannot_break_checkout(pg_engine, monkeypatch):
    import hummingbot.core.engine_diagnostics as diagnostic
    from hummingbot.client.config.runtime_config import RuntimeSettings
    from hummingbot.model.db_migration.postgres.connection import create_postgres_engine

    tracer = EngineDiagnostics(emit=lambda _: None)
    monkeypatch.setattr(diagnostic, '_ACTIVE', tracer)
    monkeypatch.setattr(tracer, 'begin', lambda *a, **kw: (_ for _ in ()).throw(MemoryError()))
    engine = create_postgres_engine(RuntimeSettings.from_environment({
        'DATABASE_URL': pg_engine.url.render_as_string(hide_password=False)}))
    try:
        for _ in range(2):
            with engine.connect() as connection:
                assert connection.execute(text('SELECT 7')).scalar_one() == 7
    finally:
        engine.dispose()


@pytest.mark.parametrize('fail', [False, True])
def test_diagnostics_commit_tracks_actual_dbapi_outcome_without_exporting_error(owned, fail, monkeypatch):
    import hummingbot.core.engine_diagnostics as diagnostic

    tracer = EngineDiagnostics(emit=lambda _: None)
    with owned.engine.begin() as connection:
        connection.execute(text('CREATE TABLE never_export_commit_table (value INT UNIQUE DEFERRABLE INITIALLY DEFERRED)'))
    monkeypatch.setattr(diagnostic, '_ACTIVE', tracer)
    with owned.engine.connect() as connection:
        connection.execute(text('INSERT INTO never_export_commit_table VALUES (1)'))
        if fail:
            connection.execute(text('INSERT INTO never_export_commit_table VALUES (1)'))
            with pytest.raises(Exception, match='never_export_commit_table'):
                connection.commit()
            connection.rollback()
        else:
            connection.commit()
        assert connection.execute(text('SELECT COUNT(*) FROM never_export_commit_table')).scalar_one() == (0 if fail else 1)
    state = tracer.snapshot()
    assert state['operations']['db.commit']['completed'] == 1
    assert state['operations']['db.commit']['errors'] == int(fail)
    assert not state['active']
    assert 'never_export' not in json.dumps(state)


def test_diagnostics_commit_telemetry_failure_does_not_change_success(owned, monkeypatch):
    import hummingbot.core.engine_diagnostics as diagnostic

    tracer = EngineDiagnostics(emit=lambda _: None)
    monkeypatch.setattr(diagnostic, '_ACTIVE', tracer)
    monkeypatch.setattr(tracer, 'begin', lambda *a, **kw: (_ for _ in ()).throw(MemoryError()))
    with owned.engine.begin() as connection:
        connection.execute(text('CREATE TABLE commit_success_despite_telemetry_failure (value INT)'))
    with owned.engine.connect() as connection:
        assert connection.execute(text("SELECT to_regclass('commit_success_despite_telemetry_failure')")).scalar_one()


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
