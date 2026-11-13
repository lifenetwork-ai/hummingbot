"""Lose the actual PostgreSQL verification COMMIT, after a durable native fill."""
import json
import os
from decimal import Decimal
from test.integration.postgres.test_automatic_recovery import managed_native
from test.integration.postgres.test_ownership import owned
from test.integration.postgres.test_scoped_recovery_fault_matrix import evidence, proof_reads
from test.integration.postgres.test_scoped_uncertain_recovery import clone_pending, isolate
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import event
from sqlalchemy.exc import DBAPIError

from hummingbot.client.config.runtime_config import RuntimeSettings
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder
from hummingbot.core.order_submission_guard import SubmissionBlocked
from hummingbot.model.db_migration.postgres.connection import create_postgres_engine


def main():
    engine = create_postgres_engine(RuntimeSettings.from_environment({'DATABASE_URL': os.environ['HBOT_IO_TEST_URL']}))
    if not engine.url.database.startswith('hbot_test_'):
        raise RuntimeError('Disposable test database required')
    monkeypatch = pytest.MonkeyPatch()
    fixture = owned.__wrapped__(engine, monkeypatch)
    context = next(fixture)
    context.owner.scoped_recovery_enabled = True
    native = managed_native.__wrapped__(context)
    context, exchange, recorder, transport = next(native)
    isolate(context, exchange, recorder)
    clone_pending(context, 'still-unknown')
    recovery = context.owner.automatic_recovery
    recovery._isolate(recovery._pending())
    core = SimpleNamespace(markets_recorder=recorder, trade_fill_db=context.manager)
    status, raw = evidence()
    armed = [False]
    connection = context.owner.connection
    driver = connection.connection.driver_connection
    audit = recovery._audit

    def audited(*args):
        audit(*args)
        if args[0] == 'automatic_recovery_order_verified':
            armed[0] = True

    def committing(current):
        if armed[0]:
            armed[0] = False
            print('COMMIT_READY', flush=True)
            input()

    event.listen(connection, 'commit', committing)
    try:
        with proof_reads(exchange, status), patch.object(exchange, '_request_order_fills', AsyncMock(
                return_value={'code': '0', 'data': [raw]})), patch.object(recovery, '_audit', audited):
            try:
                context.loop.run_until_complete(recovery._merge(
                    core, exchange, recovery._attempt('auto-order'), exchange.in_flight_orders['auto-order']))
            except DBAPIError:
                print(json.dumps({'stage': 'uncertain', 'closed': bool(driver.closed)}), flush=True)
            else:
                raise AssertionError('Cut verification COMMIT unexpectedly succeeded')
        input()  # Parent restores network; this failed owner still cannot send.
        sale = InFlightOrder('sale', 'BTC-USDT', OrderType.LIMIT_MAKER, TradeType.SELL,
                             Decimal('.1'), 1700000002, Decimal('100'))
        try:
            recovery.authorize_isolated_order(exchange, sale)
        except (SubmissionBlocked, RuntimeError):
            blocked = True
        else:
            blocked = False
        print(json.dumps({'stage': 'blocked', 'blocked': blocked, 'posts': transport.call.await_count}), flush=True)
    finally:
        event.remove(connection, 'commit', committing)
        native.close()
        fixture.close()
        monkeypatch.undo()
        engine.dispose()


if __name__ == '__main__':
    main()
