"""The checkpoint fill projection retains SQLite compatibility and input order."""
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.model.trade_fill import TradeFill


@pytest.mark.parametrize('count', [0, 1, 2, 9])
def test_fill_proofs_keep_per_order_rows_and_empty_proof_without_fetching_other_orders(count):
    engine = create_engine('sqlite://')
    try:
        TradeFill.__table__.create(engine)
        identities = ['proof-' + str(index) for index in range(count)]
        with Session(engine) as session, session.begin():
            for identity in reversed(identities + ['unrelated']):
                for trade in range(2):
                    session.add(TradeFill(
                        config_file_path='offline.yml', strategy='maker', market='okx', symbol='BTC-USDT',
                        base_asset='BTC', quote_asset='USDT', timestamp=1700000000000,
                        order_id=identity, trade_type='BUY', order_type='LIMIT_MAKER',
                        price=Decimal('100'), amount=Decimal('.1'), leverage=1,
                        trade_fee={}, trade_fee_in_quote=Decimal('.01'), exchange_trade_id=str(trade)))
        statements = []

        def captured(connection, cursor, statement, *args):
            statements.append(statement)

        event.listen(engine, 'before_cursor_execute', captured)
        with Session(engine) as session:
            proofs = list(MarketsRecorder._iter_held_order_fill_proofs(session, identities))
        assert [identity for identity, rows in proofs] == identities
        assert len(statements) == (count + 7) // 8
        for identity, rows in proofs:
            assert len(rows) == 2 and {row.order_id for row in rows} == {identity}
            assert {row.exchange_trade_id for row in rows} == {'0', '1'}
            assert sum((row.trade_fee_in_quote for row in rows), Decimal(0)) == Decimal('.02')
        with Session(engine) as session:
            assert list(MarketsRecorder._iter_held_order_fill_proofs(session, ['missing'])) == [('missing', [])]
    finally:
        engine.dispose()
