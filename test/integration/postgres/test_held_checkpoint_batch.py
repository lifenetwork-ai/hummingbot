"""Batch existing checkpoint metadata without deferring any financial commit."""
from decimal import Decimal
from test.integration.postgres.test_automatic_recovery import executor_info
from test.integration.postgres.test_ownership import owned as _owned
from test.integration.postgres.test_performance import queries
from test.integration.postgres.test_recorder import fill
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, event, select

from hummingbot.core.bot_ownership import OwnershipLost
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount
from hummingbot.core.event.events import MarketEvent
from hummingbot.model.executors import Executors
from hummingbot.model.order import Order
from hummingbot.model.position import Position
from hummingbot.model.runtime_journal import RuntimeCheckpoint, RuntimeJournal, financial_hash
from hummingbot.model.trade_fill import TradeFill

owned = _owned


def group(context, count=6):
    assert context.recorder.ensure_runtime_checkpoint()
    infos = [executor_info('batch-held-' + str(i)) for i in range(count)]
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
    assert sum('FROM trade_fills' in sql and 'LIMIT' in sql for sql in selects) == len(infos)
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
    assert sum('FROM trade_fills' in sql and 'LIMIT' in sql for sql in selects) == 2
    with owned.manager.get_new_session() as session:
        assert session.query(Executors).count() == 1
        assert session.query(Order).filter_by(position_applied=True).count() == 2
        assert session.query(Position).one().amount == Decimal('.2')


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


def test_held_checkpoint_preserves_per_order_fill_bound(owned):
    infos, positions = group(owned, count=2)
    identity = infos[-1].custom_info['held_position_orders'][0]['client_order_id']
    with owned.engine.begin() as connection:
        original = dict(connection.execute(select(TradeFill).where(TradeFill.order_id == identity)).mappings().one())
        connection.execute(TradeFill.__table__.insert(), [dict(original, exchange_trade_id='overflow-' + str(i)) for i in range(1000)])
    with pytest.raises(ValueError, match='bounded native transfer'):
        owned.recorder.checkpoint_held_positions(positions, infos)
    unchanged(owned, 2)
