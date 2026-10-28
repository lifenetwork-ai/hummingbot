"""Optional SQL cancellation must not invalidate a healthy control publication."""
from datetime import datetime, timedelta, timezone
from test.integration.postgres.test_mm_events import history, start  # noqa: F401
from test.integration.postgres.test_service_availability import publication, publish  # noqa: F401

import pytest
from sqlalchemy.exc import OperationalError

from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.model.admin_reporting import save_daily_balances
from hummingbot.model.admin_store import AdminStore


def assert_control_committed(store, owner):
    assert owner.check_owner()['owner_uuid'] == owner.owner_uuid
    row = store.read()
    assert row['acknowledged_version'] == 1
    assert AdminStore(store).command_by_id('stop-command')['status'] == 'succeeded'
    assert row['status']['daily_snapshot_error'] == 'OperationalError'


def test_optional_statement_daily_row_lock_retains_receipt(publication):  # noqa: F811
    store, owner, status = publication
    account = dict(account_id='audit', environment='demo', identity_verified=True, balances=[dict(
        currency='USDT', connector='okx', source='okx_account_balance',
        observed_at=(datetime.now(timezone.utc) - timedelta(seconds=10)).timestamp(), cash_balance='100')])
    with store.engine.begin() as connection:
        assert save_daily_balances(connection, [account], 'UTC') == 1
        connection.exec_driver_sql("UPDATE daily_balance_snapshots SET updated_at=clock_timestamp()-interval '90 seconds'")
    account['balances'][0]['observed_at'] += 1
    status.update(reporting=dict(accounts=[account]), reporting_timezone='UTC')
    with store.engine.begin() as blocker:
        blocker.exec_driver_sql('SELECT * FROM daily_balance_snapshots FOR UPDATE').all()
        assert publish(store, owner, status)
    assert_control_committed(store, owner)


def test_optional_statement_timeout_retains_receipt_and_connection(publication, monkeypatch):  # noqa: F811
    store, owner, status = publication

    def slow(connection, *args):
        connection.exec_driver_sql('SET LOCAL statement_timeout=20')
        connection.exec_driver_sql('SELECT pg_sleep(.05)')

    monkeypatch.setattr('hummingbot.model.admin_reporting.save_daily_balances', slow)
    assert publish(store, owner, status)
    assert_control_committed(store, owner)
    with store.engine.connect() as connection:
        assert connection.exec_driver_sql('SHOW statement_timeout').scalar_one() == '2s'


def test_optional_statement_guard_history_lock_retains_publication(history):  # noqa: F811
    store, admin, _ = history
    receipt, _, _ = start(history)
    owner = BotOwnership(store.engine, store.bot_id)
    assert owner.try_acquire()
    session = store.read()['mm_session']
    status = dict(phase='waiting_for_mm_readiness', running=False,
                  mm_session=dict(id=session['id'], generation=session['generation']),
                  mm_markets=[dict(market='spot', ready=False)],
                  engine=dict(strategy_running=False, clock_running=False))
    try:
        with store.engine.begin() as blocker:
            blocker.exec_driver_sql('LOCK TABLE mm_events IN SHARE MODE')
            assert publish(store, owner, status)
        row = store.read()
        assert row['acknowledged_version'] == 1 and row['status']['mm_history_error'] == 'guard_history_unavailable'
        assert admin.command_by_id(receipt['id'])['status'] == 'applying'
        assert owner.check_owner()['owner_uuid'] == owner.owner_uuid
        # Once the lock clears, the unacknowledged guard transition can be saved.
        assert publish(store, owner, status)
        assert not store.read()['status'].get('mm_history_error')
    finally:
        owner.release()


@pytest.mark.parametrize('code,invalidated', [('55P03', True), ('57014', True), ('08006', False),
                                              ('57P01', False), ('40P01', False)])
def test_optional_statement_unrecognized_or_lost_connection_is_still_fatal(publication, monkeypatch, code, invalidated):  # noqa: F811
    store, owner, status = publication
    original = RuntimeError('redacted')
    original.pgcode = code
    error = OperationalError('optional-statement', {}, original, connection_invalidated=invalidated)

    def broken(*args):
        raise error

    monkeypatch.setattr('hummingbot.model.admin_reporting.save_daily_balances', broken)
    with pytest.raises(OperationalError):
        publish(store, owner, status)
    assert store.read()['acknowledged_version'] == 0
    assert AdminStore(store).command_by_id('stop-command')['status'] == 'accepted'
