"""A bad optional balance sample cannot roll back a healthy control receipt."""
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import DataError, OperationalError

from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.model.admin_command import AdminCommand
from hummingbot.model.admin_store import AdminStore
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres


@pytest.fixture
def publication(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    store = BotStateStore(pg_engine, "hummingbot")
    store.ensure()
    store.control(expected_version=0, desired_state="stopped")
    owner = BotOwnership(pg_engine, "hummingbot")
    assert owner.try_acquire()
    now = datetime.now(timezone.utc)
    with pg_engine.begin() as connection:
        connection.execute(AdminCommand.__table__.insert().values(
            id="stop-command", bot_id="hummingbot", idempotency_key="stop-key", request_hash="hash",
            action="stop", expected_version=0, effective_version=1, status="accepted", payload={},
            created_at=now, updated_at=now))
    status = dict(running=False, engine=dict(strategy_running=False, clock_running=False), reporting={"accounts": []})
    try:
        yield store, owner, status
    finally:
        owner.release()


def publish(store, owner, status):
    return store.publish(owner_uuid=owner.owner_uuid, owner_generation=owner.generation, version=1, status=status)


@pytest.mark.parametrize("error", [ValueError("bad snapshot"), DataError("sample", {}, ValueError("invalid numeric"))])
def test_optional_daily_snapshot_failure_commits_real_control_outcome(publication, monkeypatch, error):
    store, owner, status = publication

    def broken(*args):
        raise error

    monkeypatch.setattr("hummingbot.model.admin_reporting.save_daily_balances", broken)
    assert publish(store, owner, status)
    assert store.read()["acknowledged_version"] == 1
    assert AdminStore(store).command_by_id("stop-command")["status"] == "succeeded"
    assert store.read()["status"]["daily_snapshot_error"] == type(error).__name__


def test_actual_bad_pg_value_is_rolled_back_to_savepoint_only(publication):
    store, owner, status = publication
    status["reporting"] = dict(accounts=[dict(account_id="account", environment="demo", identity_verified=True, balances=[dict(
        currency="USDT", connector="okx", source="okx_account_balance", observed_at=datetime.now(timezone.utc).timestamp(),
        cash_balance="not-a-number")])])
    assert publish(store, owner, status)
    assert store.read()["acknowledged_version"] == 1
    assert store.read()["status"]["daily_snapshot_error"]
    assert AdminStore(store).command_by_id("stop-command")["status"] == "succeeded"


def test_skipping_optional_snapshot_avoids_savepoint_and_keeps_required_control_commit(publication, monkeypatch):
    from sqlalchemy import event
    store, owner, status = publication
    statements = []

    def statement(connection, cursor, sql, parameters, context, many):
        statements.append(sql.upper())

    def broken(*args):
        pytest.fail("Skipped optional snapshot must not execute")

    monkeypatch.setattr("hummingbot.model.admin_reporting.save_daily_balances", broken)
    event.listen(store.engine, "before_cursor_execute", statement)
    try:
        assert store.publish(owner_uuid=owner.owner_uuid, owner_generation=owner.generation, version=1,
                             status=status, save_balances=False)
    finally:
        event.remove(store.engine, "before_cursor_execute", statement)
    assert not any("SAVEPOINT" in sql for sql in statements)
    assert store.read()["acknowledged_version"] == 1
    assert AdminStore(store).command_by_id("stop-command")["status"] == "succeeded"


@pytest.mark.parametrize("target", ["snapshot", "receipt"])
def test_critical_publication_failure_still_rolls_back_and_propagates(publication, monkeypatch, target):
    store, owner, status = publication

    def broken(*args):
        raise OperationalError("critical-query", {}, RuntimeError("connection lost"))

    monkeypatch.setattr("hummingbot.model.admin_reporting.save_daily_balances" if target == "snapshot" else
                        "hummingbot.model.admin_store.AdminStore.record_observation", broken)
    with pytest.raises(OperationalError):
        publish(store, owner, status)
    assert store.read()["acknowledged_version"] == 0
    assert AdminStore(store).command_by_id("stop-command")["status"] == "accepted"


def test_background_publication_cannot_acknowledge_superseding_stop(publication, monkeypatch):
    import asyncio
    import threading
    from unittest.mock import MagicMock

    from hummingbot.cli.service import ServiceHost
    from hummingbot.client.config.runtime_config import RuntimeSettings

    store, owner, status = publication
    entered, release = threading.Event(), threading.Event()
    original = owner.store.publish

    def delayed(**fields):
        entered.set()
        assert release.wait(3)
        return original(**fields)

    monkeypatch.setattr(owner.store, 'publish', delayed)

    async def exercise():
        host = ServiceHost(RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test'}),
                           asyncio.Event(), MagicMock())
        host.owner = owner
        host.snapshot = lambda: dict(status)
        task = asyncio.create_task(host.publish_status(1))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            store.control(expected_version=1, desired_state='stopped')
            release.set()
            await task
            row = store.read()
            assert row['control_version'] == 2 and row['acknowledged_version'] == 0
            assert AdminStore(store).command_by_id('stop-command')['status'] == 'accepted'
            assert host._daily_next_write_at == 0
            await host.publish_status(2)
            assert store.read()['acknowledged_version'] == 2
            assert AdminStore(store).command_by_id('stop-command')['status'] == 'superseded'
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())


def test_delayed_status_query_does_not_timeout_private_health(publication):
    import asyncio
    import threading
    import time
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock
    from urllib.request import urlopen

    from aiohttp import web
    from aiohttp.test_utils import TestServer
    from sqlalchemy import event

    from hummingbot.cli.service import ServiceHost
    from hummingbot.client.config.runtime_config import RuntimeSettings

    store, owner, status = publication
    entered, probe_ready = threading.Event(), threading.Event()

    def delay_publication(connection, cursor, statement, params, context, many):
        if not entered.is_set() and statement.startswith('SELECT bot_state.status, bot_state.mm_session'):
            entered.set()
            connection.exec_driver_sql('SELECT pg_sleep(0.4)')

    event.listen(store.engine, 'before_cursor_execute', delay_publication)

    async def exercise():
        host = ServiceHost(RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test'}),
                           asyncio.Event(), MagicMock())
        host.owner = owner
        host.hb = SimpleNamespace(trading_core=SimpleNamespace(strategy=None, connector_manager=SimpleNamespace(connectors={})))
        host.snapshot = lambda: dict(status)
        host.applied_version = 1
        host.bootstrapped = True
        host.observer_progress_at = time.monotonic()
        host.poll_reconciliation = AsyncMock()
        host.poll_funding = host.poll_outflow = MagicMock()
        app = web.Application()
        app.router.add_get('/live', host.health)
        async with TestServer(app) as server:
            def probe():
                probe_ready.set()
                assert entered.wait(2)
                try:
                    with urlopen(str(server.make_url('/live')), timeout=.3) as response:
                        return response.status
                except OSError as error:
                    return type(error).__name__
            request = asyncio.create_task(asyncio.to_thread(probe))
            assert await asyncio.to_thread(probe_ready.wait, 2)
            await host.observe_once()
            assert await request == 200
            assert entered.is_set()
            assert store.read()['acknowledged_version'] == 1

    try:
        asyncio.run(exercise())
    finally:
        event.remove(store.engine, 'before_cursor_execute', delay_publication)
        from hummingbot.core.order_submission_guard import install_guard
        install_guard(None)


def test_observer_reuses_no_authority_and_avoids_duplicate_acquired_owner_query(publication, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from sqlalchemy import event

    from hummingbot.cli.service import ServiceHost
    from hummingbot.client.config.runtime_config import RuntimeSettings
    from hummingbot.core.order_submission_guard import SessionSubmissionBlocked, install_guard

    store, owner, status = publication
    queries = []

    def record(connection, cursor, statement, params, context, many):
        if statement.strip().startswith('SELECT b.bot_id') and 'pg_locks' in statement:
            queries.append(statement)

    event.listen(store.engine, 'before_cursor_execute', record)
    monkeypatch.setattr('hummingbot.core.mm_liquidity.capture_liquidity', lambda host: None)

    async def exercise():
        host = ServiceHost(RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test'}),
                           asyncio.Event(), MagicMock())
        host.owner = owner
        host.hb = SimpleNamespace(trading_core=SimpleNamespace(strategy=None, connector_manager=SimpleNamespace(connectors={})))
        host.applied_version = 1
        host.poll_reconciliation = AsyncMock()
        host.poll_funding = host.poll_outflow = MagicMock()
        host.publish_status = AsyncMock()
        await host.observe_once()
        assert len(queries) == 2  # Entry read and final recovery read; neither is a cached authority.
        store.control(expected_version=1, desired_state='stopped')
        owner.version, owner.config_file_path = 1, None
        owner.allow_new_orders = True
        with pytest.raises(SessionSubmissionBlocked):
            owner.check_owner(new_risk=True)
        assert not owner.lost
        assert len(queries) == 3

    try:
        asyncio.run(exercise())
    finally:
        event.remove(store.engine, 'before_cursor_execute', record)
        install_guard(None)


def test_observer_ownership_round_trips_fit_private_probe_deadline(publication, monkeypatch):
    import asyncio
    import threading
    import time
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock
    from urllib.request import urlopen

    from aiohttp import web
    from aiohttp.test_utils import TestServer
    from sqlalchemy import event

    from hummingbot.cli.service import ServiceHost
    from hummingbot.client.config.runtime_config import RuntimeSettings
    from hummingbot.core.order_submission_guard import install_guard

    store, owner, status = publication
    entered, client_ready = threading.Event(), threading.Event()

    def delay_checks(connection, cursor, statement, params, context, many):
        if statement.strip().startswith('SELECT b.bot_id') and 'pg_locks' in statement:
            entered.set()
            connection.exec_driver_sql('SELECT pg_sleep(0.12)')

    event.listen(store.engine, 'before_cursor_execute', delay_checks)
    monkeypatch.setattr('hummingbot.core.mm_liquidity.capture_liquidity', lambda host: None)

    async def exercise():
        host = ServiceHost(RuntimeSettings.from_environment({'DATABASE_URL': 'postgresql://unused/test'}),
                           asyncio.Event(), MagicMock())
        host.owner = owner
        host.hb = SimpleNamespace(trading_core=SimpleNamespace(strategy=None, connector_manager=SimpleNamespace(connectors={})))
        host.applied_version = 1
        host.bootstrapped = True
        host.observer_progress_at = time.monotonic()
        host.poll_reconciliation = AsyncMock()
        host.poll_funding = host.poll_outflow = MagicMock()
        host.publish_status = AsyncMock()
        app = web.Application()
        app.router.add_get('/live', host.health)
        async with TestServer(app) as server:
            def probe():
                client_ready.set()
                assert entered.wait(2)
                try:
                    with urlopen(str(server.make_url('/live')), timeout=.3) as response:
                        return response.status
                except OSError as error:
                    return type(error).__name__
            request = asyncio.create_task(asyncio.to_thread(probe))
            assert await asyncio.to_thread(client_ready.wait, 2)
            await host.observe_once()
            assert await request == 200

    try:
        asyncio.run(exercise())
    finally:
        event.remove(store.engine, 'before_cursor_execute', delay_checks)
        install_guard(None)
