"""Strict session fee attribution against the disposable PostgreSQL integration DB."""
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session

from hummingbot.model.admin_store import AdminStore
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres
from hummingbot.model.order import Order
from hummingbot.model.overview_fees import session_fees
from hummingbot.model.runtime_journal import SubmissionAttempt
from hummingbot.model.trade_fill import TradeFill


def store(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    state = BotStateStore(pg_engine, 'hummingbot')
    state.ensure()
    return AdminStore(state)


def add_fill(engine, *, order_id=None, trade_id=None, market='okx', timestamp=1_000,
             quote='USDT', fee='0', native_fee=None):
    order_id = order_id or uuid4().hex
    trade_id = trade_id or uuid4().hex
    native_fee = native_fee or {'percent': '0', 'percent_token': None,
                                'flat_fees': [{'token': 'USDT', 'amount': fee}] if fee != '0' else []}
    with Session(engine) as session, session.begin():
        order = Order(id=order_id, config_file_path='life.yaml', strategy='v2_with_controllers',
                      market=market, symbol='LIFE-USDT', base_asset='LIFE', quote_asset=quote,
                      creation_timestamp=timestamp, order_type='LIMIT_MAKER', amount=Decimal('10'),
                      price=Decimal('0.1'), leverage=1, last_status='BuyOrderCompleted',
                      last_update_timestamp=timestamp)
        session.add(order)
        session.add(TradeFill(order=order, order_id=order_id, config_file_path='life.yaml', strategy='v2_with_controllers',
                              market=market, symbol='LIFE-USDT', base_asset='LIFE', quote_asset=quote,
                              timestamp=timestamp, trade_type='BUY', order_type='LIMIT_MAKER',
                              price=Decimal('0.1'), amount=Decimal('10'), leverage=1,
                              trade_fee=native_fee, trade_fee_in_quote=Decimal(fee) if fee is not None else None,
                              exchange_trade_id=trade_id))
    return order_id


def add_attempt(engine, order_id, *, bot='hummingbot', session_id='session', generation=0,
                account='acct-1', environment='live', connector='okx'):
    context = {'bot_id': bot, 'session': [session_id, generation],
               'bindings': [{'connector': connector, 'account_id': account, 'environment': environment}]}
    with Session(engine) as session, session.begin():
        session.add(SubmissionAttempt(order_id=order_id, state='submitted', context=context))


def run(engine, **kwargs):
    return session_fees(engine, bot_id='hummingbot', session_id='session', account_ids=['acct-1'],
                        environment='live', start_ms=1_000, end_ms=2_000, **kwargs)


def test_session_fees_strictly_attribute_fill_decimals_rebates_and_repeated_reads(pg_engine):
    admin = store(pg_engine)
    first = add_fill(admin.engine, fee='0.123456789012345678')
    second = add_fill(admin.engine, market='okx_perpetual', fee='-0.023456789012345678')
    add_attempt(admin.engine, first, generation=2)
    add_attempt(admin.engine, second, generation=2, connector='okx_perpetual')
    result = run(admin.engine)
    assert result['value']['total_usdt'] == Decimal('0.100000000000000000')
    assert result['value']['by_market'] == [
        {'market': 'perpetual', 'total_usdt': Decimal('-0.023456789012345678')},
        {'market': 'spot', 'total_usdt': Decimal('0.123456789012345678')},
    ]
    assert result['value']['history_complete'] is False
    repeated = session_fees(admin.engine, bot_id='hummingbot', session_id='session', account_ids=['acct-1'],
                            environment='live', start_ms=1_000, end_ms=2_000)
    assert repeated['value'] == result['value']
    assert repeated['unavailable_reason'] == result['unavailable_reason']


def test_session_filter_excludes_other_bot_session_account_environment_and_half_open_edges(pg_engine):
    admin = store(pg_engine)
    valid = add_fill(admin.engine, fee='1', timestamp=1_000)
    excluded = [add_fill(admin.engine, fee='20', timestamp=1_000 + index)
                for index in range(5)]
    add_attempt(admin.engine, valid)
    add_attempt(admin.engine, excluded[0], bot='other')
    add_attempt(admin.engine, excluded[1], session_id='other')
    add_attempt(admin.engine, excluded[2], account='other-account')
    add_attempt(admin.engine, excluded[3], environment='demo')
    # Fill at the exclusive upper bound must never enter this window.
    edge = add_fill(admin.engine, fee='30', timestamp=2_000)
    add_attempt(admin.engine, edge)
    result = run(admin.engine)
    assert result['value']['total_usdt'] == Decimal('1')
    assert result['value']['history_complete'] is False


def test_legacy_unattributed_and_unconvertible_fees_do_not_make_a_complete_total(pg_engine):
    admin = store(pg_engine)
    known = add_fill(admin.engine, fee='0.5')
    add_attempt(admin.engine, known)
    legacy = add_fill(admin.engine, fee='0', native_fee={
        'percent': '0', 'percent_token': None, 'flat_fees': [{'token': 'USDT', 'amount': '.2'}]})
    add_attempt(admin.engine, legacy)
    third_token = add_fill(admin.engine, fee='0.4', native_fee={
        'percent': '.01', 'percent_token': 'BNB', 'flat_fees': []})
    add_attempt(admin.engine, third_token)
    add_fill(admin.engine, fee='100')
    result = run(admin.engine)
    assert result['value']['total_usdt'] == Decimal('0.5')
    assert result['value']['history_complete'] is False
    assert result['unavailable_reason'] is not None
    # A later fill appears in the same window without replaying or double counting.
    late = add_fill(admin.engine, fee='0.25')
    add_attempt(admin.engine, late)
    assert run(admin.engine)['value']['total_usdt'] == Decimal('0.75')


def test_no_provably_attributed_rows_returns_unavailable_not_zero(pg_engine):
    admin = store(pg_engine)
    add_fill(admin.engine, fee='0')
    result = run(admin.engine)
    assert result['value'] is None
    assert result['unavailable_reason'] is not None
