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


@pytest.mark.parametrize('before,after,amount,net', [
    ('BUY', 'SELL', '.00000409', '-.00000409'),
    ('SELL', 'BUY', '.00025419', '.00025419'),
    ('SELL', 'BUY', '0', '0'),
])
def test_spot_side_reversal_reuses_one_net_hold_and_restart_exposure(owned, before, after, amount, net):
    from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator
    first = snapshot('spot-net', before, '.00025419')
    first.connector_name = 'okx'
    owned.recorder.update_or_store_position(first)
    flipped = snapshot('unused-new-id', after, amount)
    flipped.connector_name = 'okx'
    owned.recorder.update_or_store_positions([flipped])
    owned.recorder.update_or_store_positions([flipped])  # An acknowledged replay cannot add a second hold.
    rows = owned.recorder.get_all_positions()
    assert len(rows) == 1 and rows[0].id == 'spot-net'
    assert rows[0].side == after and rows[0].amount == Decimal(amount)
    restored = object.__new__(ExecutorOrchestrator)
    restored.positions_held = {'maker': []}
    restored._load_position_from_db('maker', rows[0])
    assert len(restored.positions_held['maker']) == 1
    assert restored.positions_held['maker'][0].net_amount_base == Decimal(net)


def test_ambiguous_existing_spot_holds_require_review_before_any_batch_write(owned):
    from sqlalchemy.orm import Session

    # Simulate the inherited side-flip artifact. The writer must not silently
    # select/discard one of two historical aggregates to make recovery pass.
    first, second = snapshot('spot-buy', 'BUY', '.00025419'), snapshot('spot-sell', 'SELL', '.00000409')
    first.connector_name = second.connector_name = 'okx'
    with Session(owned.engine) as s, s.begin():
        s.add_all([first, second])
    pending = snapshot('spot-next', 'SELL', '.00000409')
    pending.connector_name = 'okx'
    with pytest.raises(ValueError, match='[Aa]mbiguous spot'):
        owned.recorder.update_or_store_positions([snapshot('perp', 'BUY', '.0089'), pending])
    rows = owned.recorder.get_all_positions()
    assert len(rows) == 2 and {p.id for p in rows} == {'spot-buy', 'spot-sell'}


def test_opposite_spot_snapshots_in_one_batch_are_rejected(owned):
    first, second = snapshot('a', 'BUY', '.00025419'), snapshot('b', 'SELL', '.00000409')
    first.connector_name = second.connector_name = 'okx'
    with pytest.raises(ValueError, match='[Aa]mbiguous spot'):
        owned.recorder.update_or_store_positions([first, second])
    assert owned.recorder.get_all_positions() == []
