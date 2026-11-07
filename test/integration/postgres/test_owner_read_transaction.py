"""Fresh ownership reads need one statement, while recovery writes stay atomic."""
import asyncio
import json
import statistics
import threading
import time
from contextlib import nullcontext
from pathlib import Path
from test.integration.postgres.test_ownership import owned as _owned
from types import SimpleNamespace

import pytest
from aiohttp import web
from psycopg2.extensions import TRANSACTION_STATUS_IDLE, TRANSACTION_STATUS_INTRANS
from sqlalchemy import event

from hummingbot.cli.service import ServiceHost
from hummingbot.cli.service_processes import probe_health
from hummingbot.core import bot_ownership
from hummingbot.core.bot_ownership import BotOwnership, OwnershipLost
from hummingbot.core.order_submission_guard import SubmissionBlocked
from hummingbot.model.db_migration.postgres.io_deadline import DeadlineConnection

owned = _owned


@pytest.mark.parametrize('isolation', ['READ COMMITTED', 'REPEATABLE READ'])
def test_owner_select_finishes_without_an_explicit_read_transaction_and_restores_write_mode(owned, isolation):
    connection = owned.owner.connection
    connection.execution_options(isolation_level=isolation)
    observed = []

    def after(conn, cursor, statement, *args):
        if conn is connection and 'pg_backend_pid() AS backend_pid' in statement:
            raw = conn.connection.driver_connection
            observed.append((raw.autocommit, raw.get_transaction_status(), raw.get_backend_pid()))

    event.listen(owned.engine, 'after_cursor_execute', after)
    try:
        assert owned.owner.check_owner(new_risk=True)['control_version'] == 1
    finally:
        event.remove(owned.engine, 'after_cursor_execute', after)
    assert observed == [(True, TRANSACTION_STATUS_IDLE, owned.owner.backend_pid)]
    assert not connection.in_transaction()
    assert not connection.connection.driver_connection.autocommit
    assert connection.get_isolation_level() == isolation
    connection.rollback()


def test_owner_read_does_not_wait_for_a_separate_read_commit(owned, monkeypatch):
    raw = owned.owner.connection.connection.driver_connection
    original = DeadlineConnection.commit
    modes = []

    def commit(conn):
        if conn is raw:
            modes.append(conn.autocommit)
            if not conn.autocommit:
                raise OSError('Simulated redundant read-COMMIT timeout')
        return original(conn)

    with monkeypatch.context() as patch:
        patch.setattr(DeadlineConnection, 'commit', commit)
        assert owned.owner.check_owner(new_risk=True)['backend_pid'] == owned.owner.backend_pid
    assert modes == [True]
    assert not owned.owner.lost and owned.owner.allow_new_orders


def test_owner_read_restores_transactional_writes_and_rolls_back_a_failed_recovery_update(owned):
    connection = owned.owner.connection
    before = owned.store.read()['recovery_state']
    observed = []

    def after(conn, cursor, statement, *args):
        if conn is connection and statement.startswith('UPDATE bot_state'):
            raw = conn.connection.driver_connection
            observed.append((raw.autocommit, raw.get_transaction_status()))
            raise RuntimeError('Injected recovery update failure')

    event.listen(owned.engine, 'after_cursor_execute', after)
    try:
        with pytest.raises(RuntimeError, match='Injected recovery'):
            owned.owner._mutate_recovery(lambda state: state.update(halt=True, reason='uncommitted'))
    finally:
        event.remove(owned.engine, 'after_cursor_execute', after)
    assert observed == [(False, TRANSACTION_STATUS_INTRANS)]
    assert owned.store.read()['recovery_state'] == before
    assert not connection.connection.driver_connection.autocommit
    assert not connection.in_transaction()


def test_read_query_failure_closes_submissions_without_reconnecting(owned):
    connection = owned.owner.connection
    connections = []

    def opened(*args):
        connections.append(True)

    def before(conn, cursor, statement, *args):
        if conn is connection and 'pg_backend_pid() AS backend_pid' in statement:
            raise OSError('Injected ownership read failure')

    event.listen(owned.engine, 'before_cursor_execute', before)
    event.listen(owned.engine, 'connect', opened)
    try:
        with pytest.raises(OwnershipLost):
            owned.owner.check_owner(new_risk=True)
    finally:
        event.remove(owned.engine, 'before_cursor_execute', before)
        event.remove(owned.engine, 'connect', opened)
    assert owned.owner.lost and not owned.owner.allow_new_orders
    assert not connections and not connection.in_transaction()
    assert not connection.connection.driver_connection.autocommit


def test_fresh_owner_read_observes_new_stop_and_control_version(owned):
    owned.owner.check_owner(new_risk=True)
    owned.store.control(expected_version=1, desired_state='stopped')
    with pytest.raises(SubmissionBlocked):
        owned.owner.check_owner(new_risk=True)
    row = owned.owner.check_owner()
    assert row['desired_state'] == 'stopped' and row['control_version'] == 2
    assert not owned.owner.lost
    assert not owned.owner.connection.connection.driver_connection.autocommit


def test_repeated_read_scopes_preserve_the_same_session_advisory_lock(owned):
    successor = BotOwnership(owned.engine, owned.store.bot_id)
    try:
        for _ in range(3):
            assert owned.owner.check_owner()['backend_pid'] == owned.owner.backend_pid
            assert not successor.try_acquire()
    finally:
        successor.release()


def test_physical_read_session_loss_cannot_restore_isolation_by_reconnecting(owned):
    connection = owned.owner.connection
    connections = []

    def opened(*args):
        connections.append(True)

    def after(conn, cursor, statement, *args):
        if conn is connection and 'pg_backend_pid() AS backend_pid' in statement:
            conn.invalidate()

    event.listen(owned.engine, 'after_cursor_execute', after)
    event.listen(owned.engine, 'connect', opened)
    try:
        with pytest.raises(OwnershipLost):
            owned.owner.check_owner(new_risk=True)
        assert connection.closed
        owned.owner.release()
    finally:
        event.remove(owned.engine, 'after_cursor_execute', after)
        event.remove(owned.engine, 'connect', opened)
    assert not connections and connection.closed
    assert owned.owner.lost and not owned.owner.allow_new_orders


@pytest.mark.parametrize('state', ['closed', 'invalidated'])
def test_release_of_a_dead_owner_session_never_reconnects(owned, state):
    connection = owned.owner.connection
    connection.close() if state == 'closed' else connection.invalidate()
    connections = []

    def opened(*args):
        connections.append(True)

    event.listen(owned.engine, 'connect', opened)
    try:
        owned.owner.release()
    finally:
        event.remove(owned.engine, 'connect', opened)
    assert not connections and connection.closed
    assert owned.owner.connection is None and not owned.owner.allow_new_orders


def test_paired_owner_read_keeps_every_select_and_avoids_explicit_read_commits(owned, monkeypatch):
    connection = owned.owner.connection
    original = bot_ownership._ownership_read
    commits, selects = [], []
    samples = []

    def committed(conn):
        if conn is connection:
            commits.append(conn.connection.driver_connection.autocommit)

    def selected(conn, cursor, statement, *args):
        if conn is connection and 'pg_backend_pid() AS backend_pid' in statement:
            selects.append(conn.connection.driver_connection.get_backend_pid())

    def measure(legacy):
        commits.clear()
        selects.clear()
        mode = (lambda conn: nullcontext()) if legacy else original
        with monkeypatch.context() as patch:
            patch.setattr(bot_ownership, '_ownership_read', mode)
            started = time.perf_counter_ns()
            for _ in range(25):
                row = owned.owner.check_owner(new_risk=True)
                assert row['backend_pid'] == owned.owner.backend_pid and row['control_version'] == 1
            elapsed = (time.perf_counter_ns() - started) / 25e6
        assert selects == [owned.owner.backend_pid] * 25
        assert commits == [not legacy] * 25
        return dict(ms_per_read=elapsed, selects=len(selects), explicit_read_commits=commits.count(False))

    event.listen(owned.engine, 'commit', committed)
    event.listen(owned.engine, 'after_cursor_execute', selected)
    try:
        measure(True)
        measure(False)
        for _ in range(7):
            samples.append(dict(before=measure(True), after=measure(False)))
    finally:
        event.remove(owned.engine, 'commit', committed)
        event.remove(owned.engine, 'after_cursor_execute', selected)
    result = dict(samples=samples,
                  before_p50_ms=statistics.median(s['before']['ms_per_read'] for s in samples),
                  after_p50_ms=statistics.median(s['after']['ms_per_read'] for s in samples),
                  scope='Warmed native recorder-owner fixture on real PostgreSQL; no whole-engine/probe latency claim.')
    output = Path('build/probe-regression-review-20261107')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'owner-read-pairs.json').write_text(json.dumps(result, indent=2) + '\n')


def test_read_commit_jitter_blocks_the_real_probe_only_with_the_legacy_transaction(owned, monkeypatch):
    """Controlled mechanism, not attribution of the earlier seven incidents."""
    original_scope, original_commit = bot_ownership._ownership_read, DeadlineConnection.commit
    raw = owned.owner.connection.connection.driver_connection
    results = []

    async def exercise():
        state = SimpleNamespace(stop=asyncio.Event(), bootstrapped=True, running=True,
                                phase='running', observer_progress_at=time.monotonic(), observer_wait_until=None)

        async def health(request):
            return await ServiceHost.health(state, request)

        app = web.Application()
        app.router.add_get('/live', health)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            for legacy in (True, False):
                ready = threading.Event()
                observed = []

                def commit(conn):
                    if conn is raw:
                        ready.set()
                        if not conn.autocommit:
                            time.sleep(.45)  # Inject only the separate physical read COMMIT.
                    return original_commit(conn)

                def client():
                    if ready.wait(2):
                        observed.append(probe_health(port, '/live'))

                with monkeypatch.context() as patch:
                    patch.setattr(bot_ownership, '_ownership_read',
                                  (lambda conn: nullcontext()) if legacy else original_scope)
                    patch.setattr(DeadlineConnection, 'commit', commit)
                    worker = threading.Thread(target=client)
                    worker.start()
                    try:
                        # The physical owner remains on the same loop/thread;
                        # the supervisor's real HTTP probe runs independently.
                        owned.owner.check_owner(new_risk=True)
                        await asyncio.to_thread(worker.join, 3)
                        assert not worker.is_alive() and len(observed) == 1
                    finally:
                        ready.set()
                        await asyncio.to_thread(worker.join, 3)
                healthy, details = observed[0]
                results.append(dict(legacy=legacy, healthy=healthy, **details))
                assert healthy is not legacy
                assert details['reason'] == ('timeout' if legacy else 'healthy')
        finally:
            await runner.cleanup()

    owned.loop.run_until_complete(exercise())
    assert not owned.owner.lost and owned.owner.allow_new_orders
    output = Path('build/probe-regression-review-20261107')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'owner-read-probe-repro.json').write_text(json.dumps(dict(
        injected_read_commit_seconds=.45, unchanged_probe_timeout_seconds=.3, results=results,
        scope='Real local PostgreSQL owner and production health/probe functions; controlled COMMIT jitter, not a full MM or historical-cause proof.'), indent=2) + '\n')
