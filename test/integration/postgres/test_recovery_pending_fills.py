"""Review committed Spot fills missing their native position application."""
from decimal import Decimal
from test.integration.postgres.test_recovery_terminal_metadata import recovering as _recovering, seed_terminal_work

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from hummingbot.model.executors import Executors
from hummingbot.model.market_state import MarketState
from hummingbot.model.order import Order
from hummingbot.model.position import Position
from hummingbot.model.runtime_journal import (
    RuntimeCheckpoint,
    RuntimeJournal,
    SubmissionAttempt,
    digest,
    financial_hash,
)
from hummingbot.model.trade_fill import TradeFill

recovering = _recovering


def seed_pending(store, **options):
    orders = seed_terminal_work(store, count=3, fills=1, **options)
    fee = dict(fee_type='AddedToCost', percent='0', percent_token='USDT',
               flat_fees=[dict(token='USDT', amount='.1')])
    with store.engine.begin() as db:
        checkpoint = db.execute(select(RuntimeCheckpoint)).mappings().one()
        db.execute(update(Position).where(Position.id == 'spot').values(side='SELL', amount=3, breakeven_price=120))
        with Session(bind=db) as session:
            db.execute(update(RuntimeCheckpoint).values(financial_hash=financial_hash(session)))
        tracker = db.execute(select(MarketState)).mappings().one()
        saved = dict(tracker['saved_state'])
        for index, identity in enumerate(orders[1:], start=5003):
            attempt = db.execute(select(SubmissionAttempt).where(SubmissionAttempt.order_id == identity)).mappings().one()
            context = attempt['context']
            stamp = int(context['order']['creation_timestamp'] * 1000)
            context['executor'].update(trading_pair='BTC-USDT', amount='1', timestamp=stamp / 1000)
            db.execute(update(SubmissionAttempt).where(SubmissionAttempt.order_id == identity)
                       .values(state='terminal', context=context))
            db.execute(update(Order).where(Order.id == identity).values(last_status='BuyOrderCompleted', position_applied=False))
            payload = dict(market='okx', symbol='BTC-USDT', exchange_trade_id=str(index), price='100', amount='1',
                           timestamp=stamp, trade_type='BUY', trade_fee=fee, trade_fee_in_quote='.1', late_application=False)
            db.execute(TradeFill.__table__.insert().values(**{key: value for key, value in payload.items() if key != 'late_application'},
                       config_file_path=checkpoint['config_file_path'], strategy='life_mm', base_asset='BTC', quote_asset='USDT',
                       order_id=identity, order_type='LIMIT_MAKER', position='NIL'))
            db.execute(RuntimeJournal.__table__.insert().values(
                event_key='fill:' + digest(['okx', identity, str(index)]), kind='fill', order_id=identity, applied=False, payload=payload))
            saved.pop(identity)
        db.execute(update(MarketState).where(MarketState.id == tracker['id']).values(saved_state=saved))
    return orders


def pending_native_fills(snapshot, *, timestamp):
    rows = []
    for item in snapshot['pending_fill_transfers']:
        for fill in item['fills']:
            rows.append(dict(ordId=item['exchange_order_id'], clOrdId=item['id'], tradeId=fill['trade_id'],
                             billId=str(int(fill['trade_id']) + 1), ts=str(timestamp), fillTime=str(timestamp),
                             fillSz='1', fillPx='100', feeCcy='USDT', fee='-.1', instId='BTC-USDT', side='buy'))
    return list(reversed(rows))


def assert_pending_applied(store, identities):
    with store.engine.connect() as db, Session(bind=db) as session:
        assert db.scalar(select(func.count()).select_from(TradeFill)) == 3
        assert db.scalar(select(func.count()).select_from(Order)) == 5005
        assert db.scalar(select(func.count()).select_from(Executors).where(Executors.close_type.is_not(None))) == 2
        assert db.scalar(select(func.count()).select_from(RuntimeJournal).where(RuntimeJournal.kind == 'hold')) == 2
        assert db.scalar(select(func.count()).select_from(RuntimeJournal).where(RuntimeJournal.kind == 'applied_order')) == 2
        assert db.execute(select(Order.position_applied).where(Order.id.in_(identities[1:]))).scalars().all() == [True, True]
        assert db.scalar(select(func.count()).select_from(RuntimeJournal).where(RuntimeJournal.applied.is_(False))) == 0
        checkpoint = db.execute(select(RuntimeCheckpoint)).mappings().one()
        assert checkpoint['financial_hash'] == financial_hash(session)
        assert checkpoint['payload']['recovery_anchor']['journal_watermark'] == db.scalar(select(func.max(RuntimeJournal.id)))
        assert db.scalar(select(Position.amount).where(Position.id == 'spot')) == 1


def test_unapplied_fills_do_not_require_lifetime_replay(recovering, monkeypatch):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    monkeypatch.setenv('OKX_SITE', 'www')
    store, owner = recovering
    orders = seed_pending(store)
    snapshot = store.snapshot()
    assert snapshot.get('mode') == 'verified_checkpoint'
    assert not snapshot['truncated'] and not snapshot['orders']
    assert [row['id'] for row in snapshot['terminal_orders']] == orders[:1]
    change, = snapshot['pending_fill_plan']
    assert Decimal(change['before']['amount']) == 3
    assert change['after']['side'] == 'SELL'
    assert Decimal(change['after']['amount']) == 1
    assert Decimal(change['after']['realized_pnl_quote']) == 40
    assert Decimal(change['after']['cum_fees_quote']) == Decimal('.3')
    assert owner.check_owner()['recovery_state']['halt']


@pytest.mark.parametrize('mutation', ['late', 'duplicate_application', 'incomplete', 'later_application', 'unrelated_work'])
def test_unsupported_pending_work_does_not_qualify_for_bounded_recovery(recovering, monkeypatch, mutation):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    monkeypatch.setenv('OKX_SITE', 'www')
    store, owner = recovering
    identities = seed_pending(store)
    with store.engine.begin() as db:
        order_id = identities[1]
        if mutation == 'late':
            payload = db.scalar(select(RuntimeJournal.payload).where(RuntimeJournal.order_id == order_id))
            db.execute(update(RuntimeJournal).where(RuntimeJournal.order_id == order_id)
                       .values(payload={**payload, 'late_application': True}))
        elif mutation == 'duplicate_application':
            db.execute(update(Order).where(Order.id == order_id).values(position_applied=True))
        elif mutation == 'incomplete':
            db.execute(update(TradeFill).where(TradeFill.order_id == order_id).values(amount=Decimal('.5')))
        elif mutation == 'later_application':
            db.execute(RuntimeJournal.__table__.insert().values(
                event_key='applied-order:o00000', order_id='o00000', kind='applied_order', applied=True, payload={}))
        else:
            db.execute(RuntimeJournal.__table__.insert().values(
                event_key='unknown', order_id=order_id, kind='unknown', applied=False, payload={}))
    snapshot = store.snapshot()
    assert snapshot.get('mode') != 'verified_checkpoint' and snapshot['truncated']
    assert owner.check_owner()['recovery_state']['halt']
