"""The held-ACK proof concerns its admitted batch, not unrelated new intents."""
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, insert, text, update

from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres
from hummingbot.model.order import Order
from scripts.test_e2e_offline import terminal_cancel_batch_recorded


@pytest.mark.parametrize('damage', [None, 'pending', 'missing_exchange_id', 'wrong_exchange_id',
                                    'not_canceled', 'wrong_market', 'missing_order'])
def test_terminal_cancel_batch_is_independent_of_unrelated_pending_intents(pg_engine, damage):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    batch = [dict(clOrdId='one', ordId='101', instId='LIFE-USDT'),
             dict(clOrdId='two', ordId='102', instId='LIFE-USDT-SWAP')]
    with pg_engine.begin() as connection:
        for identity, market, exchange_id, pending in (('one', 'okx', '101', False),
                                                       ('two', 'okx_perpetual', '102', False),
                                                       ('replacement', 'okx', None, True)):
            connection.execute(insert(Order).values(
                id=identity, config_file_path='life_mm_profiles.yaml', strategy='v2_with_controllers',
                market=market, symbol='LIFE-USDT', base_asset='LIFE', quote_asset='USDT',
                creation_timestamp=1000, order_type='LIMIT_MAKER', amount=Decimal('1'), price=Decimal('.1'),
                leverage=1, last_status='OrderIntent' if pending else 'OrderCancelled',
                last_update_timestamp=1001, exchange_order_id=exchange_id, creation_pending=pending))
        changes = {'pending': {'creation_pending': True}, 'missing_exchange_id': {'exchange_order_id': None},
                   'wrong_exchange_id': {'exchange_order_id': 'foreign'}, 'not_canceled': {'last_status': 'BuyOrderCreated'},
                   'wrong_market': {'market': 'other'}}
        if damage == 'missing_order':
            connection.execute(delete(Order).where(Order.id == 'one'))
        elif damage:
            connection.execute(update(Order).where(Order.id == 'one').values(**changes[damage]))

    def sql(statement):
        with pg_engine.connect() as connection:
            return str(connection.execute(text(statement)).scalar_one())

    assert terminal_cancel_batch_recorded(SimpleNamespace(sql=sql), batch) is (damage is None)
