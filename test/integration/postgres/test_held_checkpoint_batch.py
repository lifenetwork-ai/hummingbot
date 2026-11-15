"""Batch existing checkpoint metadata without deferring any financial commit."""
from contextlib import nullcontext
from decimal import Decimal
from test.integration.postgres.test_automatic_recovery import executor_info
from test.integration.postgres.test_ownership import owned as _owned
from test.integration.postgres.test_performance import queries
from test.integration.postgres.test_recorder import fill
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import delete, event, select, update

from hummingbot.core.bot_ownership import OwnershipLost
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount
from hummingbot.core.event.events import MarketEvent
from hummingbot.model.executors import Executors
from hummingbot.model.order import Order
from hummingbot.model.position import Position
from hummingbot.model.runtime_journal import RuntimeCheckpoint, RuntimeJournal, financial_hash
from hummingbot.model.trade_fill import TradeFill

owned = _owned


def group(context, count=6, *, prefix='batch-held-'):
    assert context.recorder.ensure_runtime_checkpoint()
    infos = [executor_info(prefix + str(i)) for i in range(count)]
    for info in infos:
        info.config.mm_session_id, info.config.mm_generation = 'batch-session', 0
        identity = info.custom_info['held_position_orders'][0]['client_order_id']
        trade_id = 'fill-' + identity
        info.custom_info['held_position_orders'][0]['order_fills'] = {trade_id: {}}
        context.recorder._did_fill_order(MarketEvent.OrderFilled.value, context.market, fill(
            order_id=identity, exchange_trade_id=trade_id, amount=Decimal('.1'), price=Decimal('100'),
            trade_fee=AddedToCostTradeFee(flat_fees=[TokenAmount('USDT', Decimal('.01'))])))
    position = Position(id='batch-position', controller_id='maker', connector_name='okx',
                        trading_pair='BTC-USDT', side='BUY', timestamp=1700000001000,
                        amount=Decimal('.1') * count, volume_traded_quote=Decimal('10') * count,
                        breakeven_price=Decimal('100'), unrealized_pnl_quote=Decimal(0),
                        realized_pnl_quote=Decimal(0), cum_fees_quote=Decimal('.01') * count)
    return infos, [position]


def unchanged(context, count, *, with_checkpoint=True):
    with context.manager.get_new_session() as session:
        assert session.query(Position).count() == 0
        assert session.query(Executors).count() == 0
        assert not session.query(RuntimeJournal).filter(RuntimeJournal.kind.in_(('hold', 'applied_order'))).first()
        assert session.query(Order).filter_by(position_applied=True).count() == 0
        assert session.query(RuntimeJournal).filter_by(kind='fill', applied=False).count() == count
        checkpoint = session.get(RuntimeCheckpoint, 'test.yml')
        if with_checkpoint:
            assert checkpoint.financial_hash == financial_hash(session)
        else:
            assert checkpoint is None


@pytest.mark.parametrize('count', [1, 6, 101])
def test_held_checkpoint_batches_metadata_reads_and_fill_journal_update(owned, count):
    infos, positions = group(owned, count)
    # Include an existing executor alongside new rows.
    owned.recorder.store_or_update_executor(SimpleNamespace(config=infos[0].config, executor_info=infos[0]))
    commits = []

    def committed(session):
        commits.append(True)

    event.listen(owned.manager._session_cls, 'after_commit', committed)
    try:
        with queries(owned.engine) as statements:
            owned.recorder.checkpoint_held_positions(positions, infos)
    finally:
        event.remove(owned.manager._session_cls, 'after_commit', committed)
    assert len(commits) == 1
    selects = [sql for sql in statements if sql.lstrip().upper().startswith('SELECT')]
    chunks = (count + 99) // 100
    assert sum('FROM executors' in sql for sql in selects) == chunks
    assert sum('FROM orders' in sql for sql in selects) == chunks
    # Each branch keeps its own 1001-row sentinel; one round trip covers at
    # most eight orders, rather than one network wait for every order.
    fill_reads = [sql for sql in statements if 'FROM trade_fills' in sql and 'LIMIT' in sql]
    assert len(fill_reads) == (count + 7) // 8
    assert sum(sql.count('FROM trade_fills') for sql in fill_reads) == count
    assert sum(sql.lstrip().upper().startswith('UPDATE RUNTIME_JOURNAL') for sql in statements) == chunks
    with owned.manager.get_new_session() as session:
        position = session.query(Position).one()
        assert position.amount == Decimal('.1') * count and position.cum_fees_quote == Decimal('.01') * count
        assert session.query(Executors).count() == count
        assert session.query(Order).filter_by(position_applied=True).count() == count
        assert session.query(RuntimeJournal).filter_by(kind='hold', applied=True).count() == count
        assert [row.event_key for row in session.query(RuntimeJournal).filter_by(kind='hold').order_by(RuntimeJournal.id)] == [
            'hold:' + info.id for info in infos]
        assert session.query(RuntimeJournal).filter_by(kind='fill', applied=True).count() == count
        assert session.get(RuntimeCheckpoint, 'test.yml').financial_hash == financial_hash(session)
    with pytest.raises(ValueError, match='application changed'):
        owned.recorder.checkpoint_held_positions(positions, infos)


@pytest.mark.parametrize('corruption', ['fees', 'fill_identity', 'missing_order', 'duplicate_order'])
def test_held_checkpoint_bad_last_member_cannot_commit_a_prefix(owned, corruption):
    infos, positions = group(owned)
    order = infos[-1].custom_info['held_position_orders'][0]
    if corruption == 'fees':
        order['cumulative_fee_paid_quote'] = '.02'
    elif corruption == 'fill_identity':
        order['order_fills'] = {'another-fill': {}}
    elif corruption == 'missing_order':
        order['client_order_id'] = 'no-durable-order'
    else:
        order['client_order_id'] = infos[0].custom_info['held_position_orders'][0]['client_order_id']
    with pytest.raises(ValueError, match='[Ff]ill|[Dd]urable order'):
        owned.recorder.checkpoint_held_positions(positions, infos)
    unchanged(owned, 6)


def test_held_checkpoint_invalid_final_chunk_rolls_back_every_chunk(owned):
    infos, positions = group(owned, 101)
    infos[-1].custom_info['held_position_orders'][0]['cumulative_fee_paid_quote'] = '.02'
    with pytest.raises(ValueError, match='Fill coverage'):
        owned.recorder.checkpoint_held_positions(positions, infos)
    unchanged(owned, 101)


def test_held_checkpoint_single_executor_with_multiple_orders_still_batches(owned):
    infos, positions = group(owned, 2)
    info = infos[0]
    info.config.amount = Decimal('.2')
    info.filled_amount_quote, info.cum_fees_quote = Decimal('20'), Decimal('.02')
    info.custom_info['held_position_orders'] += infos[1].custom_info['held_position_orders']
    with queries(owned.engine) as statements:
        owned.recorder.checkpoint_held_positions(positions, [info])
    selects = [sql for sql in statements if sql.lstrip().upper().startswith('SELECT')]
    assert sum('FROM orders' in sql for sql in selects) == 1
    assert sum('FROM trade_fills' in sql and 'LIMIT' in sql for sql in statements) == 1
    with owned.manager.get_new_session() as session:
        assert session.query(Executors).count() == 1
        assert session.query(Order).filter_by(position_applied=True).count() == 2
        assert session.query(Position).one().amount == Decimal('.2')


@pytest.mark.parametrize('existing', [False, True])
def test_single_held_checkpoint_groups_writes_after_fill_validation(owned, existing):
    infos, positions = group(owned, 1)
    if existing:
        owned.recorder.store_or_update_executor(SimpleNamespace(config=infos[0].config, executor_info=infos[0]))
    flushes, commits = [], []

    def flushed(session, *args):
        flushes.append(True)

    def committed(session):
        commits.append(True)

    event.listen(owned.manager._session_cls, 'before_flush', flushed)
    event.listen(owned.manager._session_cls, 'after_commit', committed)
    try:
        with queries(owned.engine) as statements:
            owned.recorder.checkpoint_held_positions(positions, infos)
    finally:
        event.remove(owned.manager._session_cls, 'before_flush', flushed)
        event.remove(owned.manager._session_cls, 'after_commit', committed)
    assert len(flushes) == 2  # Financial application, then its checkpoint; no metadata-read flush.
    assert len(commits) == 1
    assert sum('FROM executors' in sql for sql in statements) == 1
    assert sum('FROM orders' in sql for sql in statements) == 1
    with owned.manager.get_new_session() as session:
        assert session.query(Position).one().amount == Decimal('.1')
        assert session.query(Executors).count() == 1
        assert session.query(Order).one().position_applied is True
        assert session.query(RuntimeJournal).filter_by(kind='fill', applied=False).count() == 0
        assert session.get(RuntimeCheckpoint, 'test.yml').financial_hash == financial_hash(session)


@pytest.mark.parametrize('corruption', ['fees', 'fill_identity', 'missing_order'])
def test_single_held_checkpoint_rejects_unproven_transfer_before_first_write(owned, corruption):
    infos, positions = group(owned, 1)
    order = infos[0].custom_info['held_position_orders'][0]
    if corruption == 'fees':
        order['cumulative_fee_paid_quote'] = '.02'
    elif corruption == 'fill_identity':
        order['order_fills'] = {'another-fill': {}}
    else:
        order['client_order_id'] = 'no-durable-order'
    with queries(owned.engine) as statements:
        with pytest.raises(ValueError, match='[Ff]ill|[Dd]urable order'):
            owned.recorder.checkpoint_held_positions(positions, infos)
    assert not any(sql.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for sql in statements)
    unchanged(owned, 1)


def test_paired_single_held_transfer_removes_redundant_fenced_flushes(owned):
    import time
    from statistics import median
    from test.integration.postgres.test_performance import report

    serial = 0

    def run(legacy):
        nonlocal serial
        serial += 1
        infos, positions = group(owned, 1, prefix=f'paired-held-{serial}-')
        for field in ('amount', 'volume_traded_quote', 'cum_fees_quote'):
            setattr(positions[0], field, getattr(positions[0], field) * serial)
        flushes, commits = [], []

        def flushed(session, *args):
            flushes.append(True)

        def committed(session):
            commits.append(True)

        # For the one-executor/one-order case this replays exactly the former
        # metadata-read autoflush schedule, retaining all native SQL/fences.
        control = patch.object(owned.manager._session_cls.class_, 'no_autoflush',
                               property(lambda session: nullcontext())) if legacy else nullcontext()
        event.listen(owned.manager._session_cls, 'before_flush', flushed)
        event.listen(owned.manager._session_cls, 'after_commit', committed)
        try:
            with control, queries(owned.engine) as statements, \
                    patch.object(owned.owner, '_read_owner', wraps=owned.owner._read_owner) as checks:
                started = time.perf_counter()
                owned.recorder.checkpoint_held_positions(positions, infos)
                elapsed = (time.perf_counter() - started) * 1000
        finally:
            event.remove(owned.manager._session_cls, 'before_flush', flushed)
            event.remove(owned.manager._session_cls, 'after_commit', committed)
        assert len(commits) == 1
        with owned.manager.get_new_session() as session:
            assert session.query(Position).one().amount == Decimal('.1') * serial
            assert session.query(Position).one().cum_fees_quote == Decimal('.01') * serial
            assert session.query(Order).filter_by(position_applied=True).count() == serial
            assert session.query(RuntimeJournal).filter_by(kind='fill', applied=False).count() == 0
            assert session.get(RuntimeCheckpoint, 'test.yml').financial_hash == financial_hash(session)
        return dict(duration_ms=elapsed, flushes=len(flushes), ownership_checks=checks.call_count,
                    sql_statements=len(statements))

    run(True)
    run(False)
    pairs = []
    for index in range(7):
        pair = {}
        for legacy in ((True, False) if index % 2 == 0 else (False, True)):
            pair['before' if legacy else 'after'] = run(legacy)
        assert pair['before']['flushes'] == 4 and pair['after']['flushes'] == 2
        # The native bulk journal UPDATE has its own physical-owner fence too.
        assert pair['before']['ownership_checks'] == 5 and pair['after']['ownership_checks'] == 3
        assert pair['after']['sql_statements'] < pair['before']['sql_statements']
        pairs.append(pair)
    report('single_held_checkpoint_coalescing', dict(
        pairs=pairs, before_p50_ms=median(p['before']['duration_ms'] for p in pairs),
        after_p50_ms=median(p['after']['duration_ms'] for p in pairs),
        qualification='Real PostgreSQL/recorder/owner fences; synthetic native fills. '
                      'Alternating warmed single-transfer control, no timing threshold or whole-MM attribution.'))


@pytest.mark.parametrize('failure', ['owner_after_flush', 'commit'])
@pytest.mark.parametrize('with_checkpoint', [False, True])
@pytest.mark.parametrize('count', [1, 6])
def test_held_checkpoint_batch_fence_or_commit_failure_rolls_back_every_member(owned, failure, with_checkpoint, count):
    infos, positions = group(owned, count)
    if not with_checkpoint:
        with owned.engine.begin() as connection:
            connection.execute(delete(RuntimeCheckpoint))

    def fail(session, *args):
        if failure == 'owner_after_flush':
            owned.owner.connection.invalidate()
        else:
            raise RuntimeError('synthetic batch COMMIT failure')

    hook = 'after_flush' if failure == 'owner_after_flush' else 'before_commit'
    event.listen(owned.manager._session_cls, hook, fail)
    try:
        with pytest.raises(OwnershipLost if failure == 'owner_after_flush' else RuntimeError):
            owned.recorder.checkpoint_held_positions(positions, infos)
    finally:
        event.remove(owned.manager._session_cls, hook, fail)
    assert not owned.manager.persistence_healthy
    unchanged(owned, count, with_checkpoint=with_checkpoint)


@pytest.mark.parametrize('count', [2, 9])
def test_held_checkpoint_preserves_per_order_fill_bound(owned, count):
    infos, positions = group(owned, count=count)
    identity = infos[-1].custom_info['held_position_orders'][0]['client_order_id']
    with owned.engine.begin() as connection:
        original = dict(connection.execute(select(TradeFill).where(TradeFill.order_id == identity)).mappings().one())
        connection.execute(TradeFill.__table__.insert(), [dict(original, exchange_trade_id='overflow-' + str(i)) for i in range(1000)])
    with pytest.raises(ValueError, match='bounded native transfer'):
        owned.recorder.checkpoint_held_positions(positions, infos)
    unchanged(owned, count)


def test_held_checkpoint_accepts_exactly_one_thousand_fills_without_starving_next_order(owned):
    # 1000 + 1 + 1 exceeds a shared 1001-row LIMIT, but each order is valid.
    infos, positions = group(owned, count=3)
    order = infos[0].custom_info['held_position_orders'][0]
    identity = order['client_order_id']
    with owned.engine.begin() as connection:
        original = dict(connection.execute(select(TradeFill).where(TradeFill.order_id == identity)).mappings().one())
        values = dict(amount=Decimal('.0001'), trade_fee_in_quote=Decimal('.00001'),
                      trade_fee=AddedToCostTradeFee(flat_fees=[TokenAmount('USDT', Decimal('.00001'))]).to_json())
        connection.execute(update(TradeFill).where(TradeFill.order_id == identity).values(**values))
        extra = [dict(original, **values, exchange_trade_id='boundary-' + str(i)) for i in range(999)]
        connection.execute(TradeFill.__table__.insert(), extra)
    order['order_fills'].update({row['exchange_trade_id']: {} for row in extra})
    with queries(owned.engine) as statements:
        owned.recorder.checkpoint_held_positions(positions, infos)
    assert sum('FROM trade_fills' in sql and 'LIMIT' in sql for sql in statements) == 1
    with owned.manager.get_new_session() as session:
        assert session.query(Position).one().amount == Decimal('.3')
        assert session.query(Position).one().cum_fees_quote == Decimal('.03')
        assert session.query(Order).filter_by(position_applied=True).count() == 3
        assert session.get(RuntimeCheckpoint, 'test.yml').financial_hash == financial_hash(session)


def test_held_checkpoint_paired_fill_read_roundtrips(owned):
    import time
    from statistics import median
    from test.integration.postgres.test_performance import report

    serial = 0

    def legacy(session, identities):
        for identity in identities:
            yield identity, session.query(TradeFill).filter_by(order_id=identity).limit(1001).all()

    def run(before, delay):
        nonlocal serial
        serial += 1
        infos, positions = group(owned, 6, prefix=f'paired-fill-read-{serial}-')
        for field in ('amount', 'volume_traded_quote', 'cum_fees_quote'):
            setattr(positions[0], field, getattr(positions[0], field) * serial)

        def delayed_roundtrip(connection, cursor, statement, *args):
            if 'FROM trade_fills' in statement and 'LIMIT' in statement:
                time.sleep(delay)

        control = patch.object(owned.recorder, '_iter_held_order_fill_proofs', legacy) if before else nullcontext()
        event.listen(owned.engine, 'before_cursor_execute', delayed_roundtrip)
        try:
            with control, queries(owned.engine) as statements:
                started = time.perf_counter()
                owned.recorder.checkpoint_held_positions(positions, infos)
                elapsed = (time.perf_counter() - started) * 1000
        finally:
            event.remove(owned.engine, 'before_cursor_execute', delayed_roundtrip)
        reads = sum('FROM trade_fills' in sql and 'LIMIT' in sql for sql in statements)
        assert reads == (6 if before else 1)
        with owned.manager.get_new_session() as session:
            assert session.query(Position).one().amount == Decimal('.6') * serial
            assert session.query(Position).one().cum_fees_quote == Decimal('.06') * serial
            assert session.query(Order).filter_by(position_applied=True).count() == 6 * serial
            assert session.query(RuntimeJournal).filter_by(kind='fill', applied=False).count() == 0
            assert session.get(RuntimeCheckpoint, 'test.yml').financial_hash == financial_hash(session)
        return dict(duration_ms=elapsed, fill_read_roundtrips=reads, sql_statements=len(statements))

    # Warm both native paths before alternating. Latency is an injected wait per
    # actual SQL round trip, not a Cloud measurement or a timing pass threshold.
    run(True, 0)
    run(False, 0)
    pairs = []
    for index in range(3):
        pair = {}
        for before in ((True, False) if index % 2 == 0 else (False, True)):
            pair['before' if before else 'after'] = run(before, .05)
        assert pair['before']['sql_statements'] - pair['after']['sql_statements'] == 5
        pairs.append(pair)
    report('held_fill_read_batch', dict(
        pairs=pairs, before_p50_ms=median(p['before']['duration_ms'] for p in pairs),
        after_p50_ms=median(p['after']['duration_ms'] for p in pairs), injected_wait_per_read_ms=50,
        qualification='Actual disposable PostgreSQL/recorder/owner, six synthetic native filled orders per transfer. '
                      'Alternating warmed former read schedule; 50 ms injected per fill SQL round trip. '
                      'No Cloud root-cause or whole-MM/zero-probe attribution.'))
