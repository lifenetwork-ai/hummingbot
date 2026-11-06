from decimal import Decimal
from test.integration.postgres.test_cleanup_fill_checkpoint import prepare
from test.integration.postgres.test_credential_disconnect import disconnect, loaded
from test.integration.postgres.test_credential_store import managed as managed_fixture
from test.integration.postgres.test_recorder import fill, recording as recording_fixture

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from hummingbot.core.data_type.common import OrderType, PositionAction, TradeType
from hummingbot.core.event.events import MarketEvent
from scripts import mm_cleanup_faults as faults

managed = managed_fixture
recording = recording_fixture


class SQLHarness:
    def __init__(self, engine):
        self.engine = engine

    def sql(self, statement):
        with self.engine.begin() as connection:
            result = connection.execute(text(statement))
            return str(result.scalar()) if result.returns_rows else ''


def test_cleanup_fault_sql_fill_rolls_back_position_and_fee_with_the_fill(recording):
    recorder, manager, market = prepare(recording)
    harness = SQLHarness(manager.engine)
    faults.install_database_fault(harness, 'fill')
    try:
        event = fill(trade_type=TradeType.SELL, order_type=OrderType.MARKET, amount=Decimal('.1'),
                     position=PositionAction.CLOSE.value)
        with pytest.raises(DBAPIError):
            recorder._did_fill_order(MarketEvent.OrderFilled.value, market, event)
        assert faults.database_fault_attempts(harness) == 1
        assert harness.sql('SELECT count(*) FROM trade_fills') == '0'
        assert Decimal(harness.sql("SELECT amount FROM positions WHERE id='long-hold'")) == Decimal('.3')
        assert Decimal(harness.sql("SELECT cum_fees_quote FROM positions WHERE id='long-hold'")) == 0
        assert manager.persistence_healthy is False
    finally:
        faults.disarm_database_fault(harness, 'fill')


def test_cleanup_fault_sql_removal_audit_rollback_retains_key_and_exactly_one_completion(managed):
    store, actor = loaded(managed)
    root = disconnect(store, actor)
    proof = dict(orders_terminal_verified=True, positions_flat_verified=True, ledger_confirmed=True,
                 consumers_detached=True, observed_at=float(store.store.read()['database_now']), owner_generation=1)
    assert store.progress_disconnect(root['id'], 'owner', 1, stage='detaching', evidence=proof)
    harness = SQLHarness(store.engine)
    faults.install_database_fault(harness, 'removal')
    try:
        with pytest.raises(DBAPIError):
            store.finish_disconnect(root['id'], 'owner', 1, evidence=proof)
        assert faults.database_fault_attempts(harness) == 1
        assert store.load('okx', 'okx', 'demo')['envelope'] is not None
        assert store.operation(root['id'])['status'] == 'applying'
        assert harness.sql("SELECT count(*) FROM admin_audit_events WHERE action='credentials_disconnect' AND outcome='succeeded'") == '0'
    finally:
        faults.disarm_database_fault(harness, 'removal')
    assert store.finish_disconnect(root['id'], 'owner', 1, evidence=proof)
    assert store.load('okx', 'okx', 'demo')['envelope'] is None
    assert harness.sql("SELECT count(*) FROM admin_audit_events WHERE action='credentials_disconnect' AND outcome='succeeded'") == '1'
