"""Position snapshots commit together through the existing recorder fence."""
from decimal import Decimal
from test.integration.postgres.test_ownership import owned as _owned
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import event, text

from hummingbot.core.bot_ownership import OwnershipLost
from hummingbot.model.position import Position

owned = _owned


def test_closed_hedge_legs_keep_separate_accounting_after_store_and_restart(owned):
    from hummingbot.core.data_type.common import TradeType
    from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator, PositionHold

    def hold(side, amount, opened, closed, volume, fees):
        result = PositionHold('okx_perpetual', 'BTC-USDT', side)
        result._process_order(side == TradeType.BUY, Decimal(amount), Decimal(amount) * Decimal(opened))
        result._process_order(side != TradeType.BUY, Decimal(amount), Decimal(amount) * Decimal(closed))
        result.volume_traded_quote, result.cum_fees_quote = Decimal(volume), Decimal(fees)
        return result

    owned.recorder.update_or_store_positions([snapshot('long', 'BUY', '1'), snapshot('short', 'SELL', '2')])
    strategy = SimpleNamespace(markets={'okx_perpetual': {'BTC-USDT'}}, current_timestamp=1800000000,
                               market_data_provider=MagicMock())
    strategy.market_data_provider.get_price_by_type.return_value = Decimal('120')
    orchestrator = object.__new__(ExecutorOrchestrator)
    orchestrator.strategy = strategy
    orchestrator.positions_held = {'maker': [hold(TradeType.BUY, '1', '100', '110', '210', '3'),
                                             hold(TradeType.SELL, '2', '120', '110', '460', '7')]}
    orchestrator.store_all_positions()
    rows = {row.side: row for row in owned.recorder.get_all_positions()}
    assert set(rows) == {'BUY', 'SELL'}
    assert rows['BUY'].id == 'long' and rows['SELL'].id == 'short'
    for side, volume, fees, pnl in [('BUY', '210', '3', '10'), ('SELL', '460', '7', '20')]:
        row = rows[side]
        assert row.amount == 0 and row.breakeven_price == 0
        assert row.volume_traded_quote == Decimal(volume) and row.cum_fees_quote == Decimal(fees)
        assert row.realized_pnl_quote == Decimal(pnl)
    orchestrator.positions_held = {'maker': []}
    for row in rows.values():
        orchestrator._load_position_from_db('maker', row)
    orchestrator.store_all_positions()
    again = {row.side: row for row in owned.recorder.get_all_positions()}
    assert again['BUY'].volume_traded_quote == 210 and again['BUY'].cum_fees_quote == 3
    assert again['SELL'].volume_traded_quote == 460 and again['SELL'].cum_fees_quote == 7


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
    with owned.engine.connect() as connection:
        ids = set(connection.execute(text('SELECT id FROM positions')).scalars())
    assert ids == {'spot-buy', 'spot-sell'}


def test_opposite_spot_snapshots_in_one_batch_are_rejected(owned):
    first, second = snapshot('a', 'BUY', '.00025419'), snapshot('b', 'SELL', '.00000409')
    first.connector_name = second.connector_name = 'okx'
    with pytest.raises(ValueError, match='[Aa]mbiguous spot'):
        owned.recorder.update_or_store_positions([first, second])
    assert owned.recorder.get_all_positions() == []


def test_duplicate_spot_holds_block_loading_before_strategy_starts(owned):
    from sqlalchemy.orm import Session

    first, second = snapshot('spot-buy', 'BUY', '.00025419'), snapshot('spot-sell', 'SELL', '.00000409')
    first.connector_name = second.connector_name = 'okx'
    with Session(owned.engine) as session, session.begin():
        session.add_all([first, second])
    # Managed strategy initialization loads this method before quote admission.
    # A database containing the old side-flip artifact must never be treated as
    # two valid spot positions or silently repaired during recovery.
    with pytest.raises(ValueError, match='[Aa]mbiguous spot'):
        owned.recorder.get_all_positions(controller_ids=['maker'])
    with owned.engine.connect() as connection:
        assert set(connection.execute(text('SELECT id FROM positions')).scalars()) == {'spot-buy', 'spot-sell'}


def test_duplicate_spot_in_another_controller_does_not_poison_a_filtered_read(owned):
    from sqlalchemy.orm import Session

    first, second = snapshot('spot-buy', 'BUY', '.00025419'), snapshot('spot-sell', 'SELL', '.00000409')
    first.connector_name = second.connector_name = 'okx'
    separate = snapshot('other', 'BUY', '.0089')
    separate.controller_id = 'other-controller'
    with Session(owned.engine) as session, session.begin():
        session.add_all([first, second, separate])
    rows = owned.recorder.get_all_positions(controller_ids=['other-controller'])
    assert len(rows) == 1 and rows[0].id == 'other'
