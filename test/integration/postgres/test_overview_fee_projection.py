"""Durable reporting replay on isolated PostgreSQL, never the Demo database."""
import asyncio
import json
import os
import select as select_io
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from test.integration.postgres.response_loss_proxy import response_loss_proxy
from test.integration.postgres.test_overview_fees import add_attempt, add_fill, run, store
from test.integration.postgres.test_ownership import owned as _owned

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError

from hummingbot.model.order import Order
from hummingbot.model.runtime_journal import SubmissionAttempt
from hummingbot.model.trade_fill import TradeFill

owned = _owned


def child(engine, mode, *, url=None):
    return subprocess.Popen([sys.executable, '-m', 'test.integration.postgres.overview_fee_worker', mode],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd=Path(__file__).resolve().parents[3],
                            env={**os.environ, 'HBOT_IO_TEST_URL': (url or engine.url).render_as_string(hide_password=False)})


def stop_child(process):
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)
    for stream in (process.stdin, process.stdout, process.stderr):
        stream.close()


@pytest.mark.parametrize('mode', ['read', 'contribution', 'aggregate', 'committed'])
def test_worker_crash_never_loses_or_doubles_a_fee(pg_engine, mode):
    admin = store(pg_engine)
    identity = add_fill(admin.engine, fee='.25')
    add_attempt(admin.engine, identity)
    process = child(admin.engine, mode)
    try:
        assert select_io.select([process.stdout], [], [], 10)[0], 'Reporting worker did not become ready'
        assert process.stdout.readline() == b'READY\n'
    finally:
        stop_child(process)
    value = caught_up(admin.engine)['value']
    assert value['total_usdt'] == Decimal('.25') and value['processed_fill_count'] == 1


@pytest.mark.parametrize('committed', [False, True], ids=['commit-not-delivered', 'commit-reply-lost'])
def test_unknown_commit_projection_replay_reads_durable_truth(pg_engine, committed):
    admin = store(pg_engine)
    identity = add_fill(admin.engine, fee='.25')
    add_attempt(admin.engine, identity)

    async def replay():
        async with response_loss_proxy(admin.engine.url) as proxy:
            process = child(admin.engine, 'unknown', url=proxy.url)
            try:
                assert await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 10) == b'READY\n'
                proxy.drop_responses, proxy.drop_requests = True, not committed
                process.stdin.write(b'go\n')
                process.stdin.flush()
                assert await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 7) == b'UNCERTAIN\n'
                with admin.engine.connect() as connection:
                    count = connection.execute(text('SELECT count(*) FROM overview_fee_contributions')).scalar_one()
                assert count == int(committed)
                proxy.drop_responses = proxy.drop_requests = False
                process.stdin.write(b'restored\n')
                process.stdin.flush()
                result = json.loads(await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 7))
                assert Decimal(result['total']) == Decimal('.25') and result['count'] == 1
                assert await asyncio.to_thread(process.wait, timeout=5) == 0
            finally:
                stop_child(process)

    asyncio.run(replay())


def test_reporting_migration_can_wait_for_old_owner_without_breaking_startup(owned):
    from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres

    with owned.engine.begin() as connection:
        connection.exec_driver_sql('DROP TABLE overview_fee_contributions, overview_fee_scopes')
        connection.execute(text("DELETE FROM hbot_schema_migrations WHERE name = '022_create_overview_fee_projection.sql'"))
    result = bootstrap_postgres(owned.engine, auto_migrate=True, allow_standby=True)
    assert result is False
    assert not owned.owner.lost
    assert owned.owner.check_owner()['owner_generation'] == owned.owner.generation


def seed(engine, count):
    """Bounded fixture inserts, independent of the projection under test."""
    with engine.begin() as connection:
        for start in range(0, count, 500):
            orders, fills, attempts = [], [], []
            for index in range(start, min(start + 500, count)):
                identity = f'order-{index:08d}'
                orders.append(dict(id=identity, config_file_path='life.yaml', strategy='v2_with_controllers',
                                   market='okx', symbol='LIFE-USDT', base_asset='LIFE', quote_asset='USDT',
                                   creation_timestamp=1000, order_type='LIMIT_MAKER', amount=Decimal('10'),
                                   price=Decimal('.1'), leverage=1, last_status='BuyOrderCompleted', last_update_timestamp=1000))
                fills.append(dict(order_id=identity, config_file_path='life.yaml', strategy='v2_with_controllers',
                                  market='okx', symbol='LIFE-USDT', base_asset='LIFE', quote_asset='USDT',
                                  timestamp=1000, trade_type='BUY', order_type='LIMIT_MAKER', price=Decimal('.1'),
                                  amount=Decimal('10'), leverage=1, exchange_trade_id=f'trade-{index:08d}',
                                  trade_fee={'percent': '0', 'flat_fees': [{'token': 'USDT', 'amount': '.01'}]},
                                  trade_fee_in_quote=Decimal('.01')))
                attempts.append(dict(order_id=identity, state='submitted', context={'bot_id': 'hummingbot',
                                     'session': ['session', 0], 'bindings': [{'connector': 'okx',
                                                                              'account_id': 'acct-1', 'environment': 'live'}]}))
            connection.execute(Order.__table__.insert(), orders)
            connection.execute(TradeFill.__table__.insert(), fills)
            connection.execute(SubmissionAttempt.__table__.insert(), attempts)


def caught_up(engine, *, maximum=250):
    for _ in range(maximum):
        value = run(engine)
        if (value.get('value') or {}).get('processing_state') == 'ready':
            return value
    pytest.fail('Bounded reporting batches did not finish')


@pytest.mark.parametrize('count', [9999, 10000, 10001, 100000])
def test_long_session_never_loses_fees_at_the_old_row_limit(pg_engine, count):
    admin = store(pg_engine)
    seed(admin.engine, count)
    first = run(admin.engine)
    assert first['value'] is not None
    assert first['value'].get('processing_state') in ('ready', 'catching_up')
    value = caught_up(admin.engine)['value']
    assert value['total_usdt'] == Decimal(count) / 100
    assert value['processed_fill_count'] == count
    assert value['history_complete'] is False
    assert caught_up(admin.engine)['value']['total_usdt'] == value['total_usdt']


def test_late_old_timestamp_and_late_attribution_are_discovered_after_cursor_passes(pg_engine):
    admin = store(pg_engine)
    seed(admin.engine, 501)
    run(admin.engine)
    older = add_fill(admin.engine, order_id='000-late', timestamp=1000, fee='.25')
    add_attempt(admin.engine, older)
    unattributed = add_fill(admin.engine, order_id='000-unattributed', timestamp=1000, fee='.5')
    assert caught_up(admin.engine)['value']['processed_fill_count'] == 501
    value = caught_up(admin.engine)['value']
    assert value['processed_fill_count'] == 502
    assert value['total_usdt'] == Decimal('5.26')
    add_attempt(admin.engine, unattributed)
    assert caught_up(admin.engine)['value']['total_usdt'] == Decimal('5.76')


def test_projection_error_rolls_back_contribution_and_aggregate_together(pg_engine):
    admin = store(pg_engine)
    identity = add_fill(admin.engine, fee='.25')
    add_attempt(admin.engine, identity)

    def fail_after_write(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('UPDATE OVERVIEW_FEE_SCOPES'):
            raise OSError('injected reporting failure before commit')

    event.listen(admin.engine, 'after_cursor_execute', fail_after_write)
    try:
        with pytest.raises(OSError):
            run(admin.engine)
    finally:
        event.remove(admin.engine, 'after_cursor_execute', fail_after_write)
    value = caught_up(admin.engine)['value']
    assert value['total_usdt'] == Decimal('.25')
    assert value['processed_fill_count'] == 1
    with admin.engine.connect() as connection:
        assert connection.execute(select(TradeFill.trade_fee_in_quote)).scalar_one() == Decimal('.25')


def test_large_and_tiny_fees_match_exact_postgres_numeric_sum(pg_engine):
    admin = store(pg_engine)
    for fee in ('100000000000000000000', '.000000000000000001'):
        identity = add_fill(admin.engine, fee=fee)
        add_attempt(admin.engine, identity)
    value = caught_up(admin.engine)['value']
    with admin.engine.connect() as connection:
        expected = connection.execute(text('SELECT sum(trade_fee_in_quote) FROM trade_fills')).scalar_one()
    assert value['total_usdt'] == expected
    assert value['by_market'][0]['total_usdt'] == expected


def test_many_fills_on_one_order_cross_batch_boundaries_without_losing_identity(pg_engine):
    admin = store(pg_engine)
    identity = add_fill(admin.engine, order_id='one-order', trade_id='trade-0000', fee='.01')
    add_attempt(admin.engine, identity)
    with admin.engine.begin() as connection:
        first = dict(connection.execute(select(TradeFill.__table__)).mappings().one())
        connection.execute(TradeFill.__table__.insert(), [
            {**first, 'exchange_trade_id': f'trade-{index:04d}'} for index in range(1, 1001)])
    value = caught_up(admin.engine)['value']
    assert value['processed_fill_count'] == 1001
    assert value['total_usdt'] == Decimal('10.01')


def test_concurrent_workers_serialize_reporting_progress_and_replays(pg_engine):
    admin = store(pg_engine)
    seed(admin.engine, 600)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, admin.engine) for _ in range(2)]
        for future in futures:
            future.result(timeout=5)
    value = caught_up(admin.engine)['value']
    assert value['processed_fill_count'] == 600
    assert value['total_usdt'] == Decimal('6')


def test_busy_reporting_scope_times_out_and_replay_keeps_the_committed_total(pg_engine):
    admin = store(pg_engine)
    identity = add_fill(admin.engine, fee='.25')
    add_attempt(admin.engine, identity)
    assert caught_up(admin.engine)['value']['total_usdt'] == Decimal('.25')
    with admin.engine.begin() as held:
        held.execute(text("SELECT scope_key FROM overview_fee_scopes WHERE scope_key NOT LIKE 'published:%' FOR UPDATE"))
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=1) as worker:
            pending = worker.submit(run, admin.engine)
            with pytest.raises(DBAPIError) as error:
                pending.result(timeout=2)
        assert error.value.orig.pgcode == '55P03'
        assert time.monotonic() - started < 1
    value = caught_up(admin.engine)['value']
    assert value['processed_fill_count'] == 1
    assert value['total_usdt'] == Decimal('.25')
    with admin.engine.connect() as connection:
        assert connection.execute(select(TradeFill.trade_fee_in_quote)).scalar_one() == Decimal('.25')


def test_past_window_end_is_not_permanently_marked_as_processed(pg_engine):
    from hummingbot.model.overview_fees import session_fees

    admin = store(pg_engine)
    identity = add_fill(admin.engine, fee='.25', timestamp=2000)
    add_attempt(admin.engine, identity)
    assert run(admin.engine)['value'] is None
    value = session_fees(admin.engine, bot_id='hummingbot', session_id='session', account_ids=['acct-1'],
                         environment='live', start_ms=1000, end_ms=2001)['value']
    assert value['total_usdt'] == Decimal('.25')


def test_rebuild_keeps_published_total_and_old_worker_cannot_downgrade_it(pg_engine, monkeypatch):
    from hummingbot.model import overview_fee_projection as projection

    admin = store(pg_engine)
    seed(admin.engine, 1001)
    original = caught_up(admin.engine)['value']
    monkeypatch.setattr(projection, 'VERSION', 2)
    first = run(admin.engine)['value']
    assert first['total_usdt'] == original['total_usdt']
    assert first['processing_state'] == 'catching_up'
    assert first['reported_projection_version'] == 1
    assert first['processing_projection_version'] == 2
    assert first['backfill_processed_fill_count'] == 500
    rebuilt = caught_up(admin.engine)['value']
    assert rebuilt['reported_projection_version'] == 2
    assert rebuilt['total_usdt'] == Decimal('10.01')
    monkeypatch.setattr(projection, 'VERSION', 1)
    assert caught_up(admin.engine)['value']['reported_projection_version'] == 2
