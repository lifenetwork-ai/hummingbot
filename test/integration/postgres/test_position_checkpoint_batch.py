"""Position snapshots commit together through the existing recorder fence."""
from decimal import Decimal
from test.integration.postgres.test_ownership import owned as _owned

import pytest
from sqlalchemy import event, text

from hummingbot.core.bot_ownership import OwnershipLost
from hummingbot.model.position import Position

owned = _owned


def snapshot(identity, side, amount):
    return Position(id=identity, controller_id='maker', connector_name='okx_perpetual',
                    trading_pair='BTC-USDT', side=side, timestamp=1800000000000,
                    amount=Decimal(amount), volume_traded_quote=Decimal('123.456789012345678901'),
                    breakeven_price=Decimal('77432.812345678901234567'),
                    unrealized_pnl_quote=Decimal('0.123456789012345678'),
                    realized_pnl_quote=Decimal('-0.123456789012345678'),
                    cum_fees_quote=Decimal('0.015999474867200001'))


def test_batch_uses_one_lookup_and_commit_and_preserves_exact_sides(owned):
    owned.recorder.update_or_store_position(snapshot('long', 'BUY', '.0084'))
    lookups, commits = [], []

    def sql(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith('SELECT') and 'FROM positions' in statement:
            lookups.append(statement)

    def committed(session):
        commits.append(True)

    event.listen(owned.engine, 'before_cursor_execute', sql)
    event.listen(owned.manager._session_cls, 'after_commit', committed)
    try:
        owned.recorder.update_or_store_positions([
            snapshot('ignored-id', 'BUY', '.0089'), snapshot('short', 'SELL', '.0028'),
            snapshot('same-short', 'SELL', '.002800000000000001')])
    finally:
        event.remove(owned.engine, 'before_cursor_execute', sql)
        event.remove(owned.manager._session_cls, 'after_commit', committed)
    assert len(lookups) == 1 and len(commits) == 1
    rows = {row.side: row for row in owned.recorder.get_all_positions()}
    assert len(rows) == 2 and rows['BUY'].id == 'long'
    assert rows['BUY'].amount == Decimal('.0089')
    assert rows['SELL'].amount == Decimal('.002800000000000001')
    for row in rows.values():
        assert row.cum_fees_quote == Decimal('0.015999474867200001')
        assert row.volume_traded_quote == Decimal('123.456789012345678901')
        assert row.breakeven_price == Decimal('77432.812345678901234567')


def test_failed_batch_rolls_back_every_snapshot_and_latches_recorder(owned):
    owned.recorder.update_or_store_position(snapshot('long', 'BUY', '.0084'))
    with owned.engine.begin() as c:
        c.execute(text('ALTER TABLE positions ADD CONSTRAINT test_positive_amount CHECK (amount > 0)'))
    with pytest.raises(Exception, match='test_positive_amount'):
        owned.recorder.update_or_store_positions([
            snapshot('ignored-id', 'BUY', '.0089'), snapshot('short', 'SELL', '-.0028')])
    rows = owned.recorder.get_all_positions()
    assert len(rows) == 1 and rows[0].amount == Decimal('.0084')
    assert not owned.manager.persistence_healthy


def test_stale_owner_cannot_commit_any_position_in_a_batch(owned):
    owned.owner.release()
    with pytest.raises(OwnershipLost):
        owned.recorder.update_or_store_positions([
            snapshot('long', 'BUY', '.0089'), snapshot('short', 'SELL', '.0028')])
    assert owned.recorder.get_all_positions() == []


def test_empty_batch_has_no_connection_checkout(owned):
    checkouts = []

    def checkout(*args):
        checkouts.append(True)

    event.listen(owned.engine, 'checkout', checkout)
    try:
        owned.recorder.update_or_store_positions([])
    finally:
        event.remove(owned.engine, 'checkout', checkout)
    assert checkouts == []
