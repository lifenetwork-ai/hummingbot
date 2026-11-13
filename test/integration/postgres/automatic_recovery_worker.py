"""Parent kills this real owner at a selected durable financial boundary."""
import json
import os
import sys
from test.integration.postgres.test_automatic_recovery import executor_info, managed_native, orchestrator
from test.integration.postgres.test_ownership import owned

import pytest

from hummingbot.client.config.runtime_config import RuntimeSettings
from hummingbot.core.data_type.in_flight_order import OrderState, OrderUpdate
from hummingbot.model.db_migration.postgres.connection import create_postgres_engine
from hummingbot.model.runtime_journal import held_order_snapshot


def main():
    engine = create_postgres_engine(RuntimeSettings.from_environment({'DATABASE_URL': os.environ['HBOT_IO_TEST_URL']}))
    if not engine.url.database.startswith('hbot_test_'):
        raise RuntimeError('Disposable test database required')
    fixture = owned.__wrapped__(engine, pytest.MonkeyPatch())
    context = next(fixture)
    if '--scoped' in sys.argv:
        context.owner.scoped_recovery_enabled = True
        context.owner._mutate_recovery(lambda state: state.update(spot_outflow={
            'policy': {'max_net_outflow_quote': '50'}}))
    native = managed_native.__wrapped__(context)
    context, exchange, recorder, transport = next(native)
    stage = sys.argv[1]
    if stage in ('fill', 'hold'):
        order = exchange.in_flight_orders['auto-order']
        order.update_exchange_order_id('venue-auto')
        trade = exchange._trade_update_from_fill(order, dict(tradeId='fill-auto', ordId='venue-auto',
                                                             fillSz='.1', fillPx='100', fee='-.01', feeCcy='USDT',
                                                             ts='1700000001000'))

        async def fill():
            exchange._order_tracker.process_trade_update(trade)
            await exchange._order_tracker.process_order_update(OrderUpdate('BTC-USDT', 1700000001,
                                                                           OrderState.FILLED, 'auto-order', 'venue-auto'))
        context.loop.run_until_complete(fill())
        if stage == 'hold':
            info = executor_info('auto')
            info.custom_info['held_position_orders'] = [held_order_snapshot(order)]
            runtime = orchestrator()
            runtime._update_positions_from_done_executors({'maker': [info]})
    print(json.dumps({'ready': stage, 'posts': transport.call.await_count}), flush=True)
    input()  # SIGKILL bypasses every fixture's owner release and cleanup.


if __name__ == '__main__':
    main()
