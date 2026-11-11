"""Remaining costs and native positions share a real PostgreSQL commit boundary."""
from decimal import Decimal
from test.integration.postgres.test_automatic_recovery import executor_info
from test.integration.postgres.test_ownership import owned as _owned
from test.integration.postgres.test_recorder import fill
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import event, select

from hummingbot.core.data_type.common import PositionAction, PositionMode, TradeType
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount
from hummingbot.core.event.events import MarketEvent
from hummingbot.core.order_submission_guard import SubmissionBlocked
from hummingbot.model.position import Position
from hummingbot.model.runtime_journal import RuntimeCheckpoint, RuntimeJournal, financial_hash
from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.utils.perpetual_reduction import HedgeCostBasis

owned = _owned
KEY = ('maker', 'okx_perpetual', 'BTC-USDT', 'BUY')


def runtime():
    strategy = SimpleNamespace(controllers={'maker': object()}, markets={'okx_perpetual': {'BTC-USDT'}},
                               connectors={'okx_perpetual': SimpleNamespace(position_mode=PositionMode.HEDGE)},
                               current_timestamp=1700000002, market_data_provider=MagicMock(),
                               config=SimpleNamespace(mm_drawdown_scope={
                                   'maker': SimpleNamespace(connector_name='okx_perpetual', trading_pair='BTC-USDT')}))
    strategy.market_data_provider.time.return_value = 1700000002
    strategy.market_data_provider.get_price_by_type.return_value = Decimal(100)
    return ExecutorOrchestrator(strategy)


def covered_order(context, identity, effect, amount, price, fee, leg=TradeType.BUY):
    side = leg if effect == PositionAction.OPEN else (TradeType.SELL if leg == TradeType.BUY else TradeType.BUY)
    info = executor_info(identity, side.name, amount, price, fee)
    config = OrderExecutorConfig(id=identity, timestamp=1700000000, controller_id='maker',
                                 connector_name='okx_perpetual', trading_pair='BTC-USDT', side=side,
                                 amount=Decimal(amount), price=Decimal(price), position_action=effect,
                                 execution_strategy=ExecutionStrategy.LIMIT_MAKER)
    info.config, info.type = config, config.type
    info.custom_info['held_position_orders'][0]['position'] = effect.value
    evt = fill(order_id=identity + '-order', exchange_trade_id=identity + '-trade',
               trade_type=side, position=effect.value, amount=Decimal(amount), price=Decimal(price),
               trade_fee=AddedToCostTradeFee(flat_fees=[TokenAmount('USDT', Decimal(fee))]))
    context.recorder._did_fill_order(MarketEvent.OrderFilled.value, context.market, evt)
    return info


def apply(current, info):
    current.active_executors['maker'] = [SimpleNamespace(config=info.config, executor_info=info)]
    current._update_positions_from_done_executors({'maker': [info]})


def seeded(context, leg=TradeType.BUY):
    assert context.recorder.ensure_runtime_checkpoint()
    context.market.name = context.market.display_name = 'okx_perpetual'
    current = runtime()
    info = covered_order(context, 'opening', PositionAction.OPEN, '2', '100', '2', leg)
    apply(current, info)
    return current


@pytest.mark.parametrize('leg', [TradeType.BUY, TradeType.SELL])
def test_partial_close_restart_and_duplicate_receipt_preserve_only_remaining_opening_fees(owned, leg):
    current = seeded(owned, leg)
    key = (*KEY[:3], leg.name)
    closed = covered_order(owned, 'partial', PositionAction.CLOSE, '.5', '110' if leg == TradeType.BUY else '90', '.55', leg)
    apply(current, closed)
    expected = HedgeCostBasis(leg, Decimal('1.5'), Decimal('150'), Decimal('1.5'))
    assert owned.recorder.get_reduction_bases() == {key: expected}
    restarted = runtime()
    assert restarted.positions_held['maker'][0].reduction_basis == expected
    apply(restarted, closed)  # Lost-ack replay of a terminal executor.
    assert restarted.positions_held['maker'][0].reduction_basis == expected
    with owned.manager.get_new_session() as session:
        row = session.query(Position).one()
        assert row.amount == Decimal('1.5') and row.cum_fees_quote == Decimal('2.55')
        assert row.realized_pnl_quote == 5
        assert session.query(RuntimeJournal).filter_by(event_key='hold:partial').count() == 1
    apply(restarted, covered_order(owned, 'new-opening', PositionAction.OPEN, '1', '120', '1.2', leg))
    assert owned.recorder.get_reduction_bases()[key] == HedgeCostBasis(
        leg, Decimal('2.5'), Decimal('270'), Decimal('2.7'))


def test_commit_failure_cannot_advance_cost_basis_separately_from_native_position_and_receipt(owned):
    current = seeded(owned)
    before = owned.recorder.get_reduction_bases()
    closed = covered_order(owned, 'partial', PositionAction.CLOSE, '.5', '110', '.55')

    def fail(session):
        raise OSError('synthetic cost checkpoint commit failure')

    event.listen(owned.manager._session_cls, 'before_commit', fail)
    try:
        with pytest.raises(OSError, match='cost checkpoint commit failure'):
            apply(current, closed)
    finally:
        event.remove(owned.manager._session_cls, 'before_commit', fail)
    assert current.positions_held['maker'][0].reduction_basis == before[KEY]
    assert owned.recorder.get_reduction_bases() == before
    with owned.manager.get_new_session() as session:
        assert session.query(Position).one().amount == 2
        assert session.query(RuntimeJournal).filter_by(event_key='hold:partial').count() == 0
        assert not session.query(RuntimeJournal).filter_by(order_id='partial-order', kind='fill').one().applied
        checkpoint = session.get(RuntimeCheckpoint, 'test.yml')
        assert checkpoint.financial_hash == financial_hash(session)


@pytest.mark.parametrize('defect', ['missing_owned_leg', 'oversized_close'])
def test_managed_close_cannot_create_or_reverse_an_owned_hedge_position(owned, defect):
    current = seeded(owned)
    closed = covered_order(owned, 'contradictory', PositionAction.CLOSE, '2.5', '110', '.55')
    closed.config = closed.config.model_copy(update={'mm_session_id': 'session', 'mm_generation': 0})
    if defect == 'missing_owned_leg':
        current.positions_held['maker'].clear()
    before = owned.recorder.get_reduction_bases()
    with pytest.raises(SubmissionBlocked, match='recovery_position_application_conflict'):
        apply(current, closed)
    assert owned.recorder.get_reduction_bases() == before
    with owned.manager.get_new_session() as session:
        assert session.query(Position).one().amount == 2
        assert session.query(RuntimeJournal).filter_by(event_key='hold:contradictory').count() == 0
        assert not session.query(RuntimeJournal).filter_by(order_id='contradictory-order', kind='fill').one().applied


@pytest.mark.parametrize('change', ['old_image', 'external_financial', 'bad_basis', 'wrong_version'])
def test_stale_or_unknown_basis_never_survives_a_financial_checkpoint_change(owned, change):
    seeded(owned)
    with owned.manager.get_new_session() as session, session.begin():
        checkpoint = session.get(RuntimeCheckpoint, 'test.yml')
        if change in ('old_image', 'external_financial'):
            session.query(Position).one().amount = Decimal('1.5')
            session.flush()
            if change == 'old_image':
                checkpoint.financial_hash = financial_hash(session)
        else:
            payload = {**checkpoint.payload}
            basis = {**payload['reduction_basis']}
            if change == 'wrong_version':
                basis['version'] = 2
            else:
                basis['positions'] = [dict(basis['positions'][0], basis={'version': 1})]
            payload['reduction_basis'] = basis
            checkpoint.payload = payload
    assert owned.recorder.get_reduction_bases() == {}
    assert runtime().positions_held['maker'][0].reduction_basis is None


def test_cost_sidecar_rejects_a_different_owned_quantity_and_rolls_back_the_receipt(owned):
    seeded(owned)
    closed = covered_order(owned, 'partial', PositionAction.CLOSE, '.5', '110', '.55')
    with owned.manager.get_new_session() as session:
        row = session.execute(select(Position)).scalar_one()
        snapshot = Position(**{column.name: getattr(row, column.name) for column in Position.__table__.columns})
    snapshot.amount = Decimal('1.5')
    wrong = HedgeCostBasis(TradeType.BUY, Decimal('2'), Decimal('200'), Decimal('2'))
    with pytest.raises(ValueError, match='exact owned position'):
        owned.recorder.checkpoint_held_positions([snapshot], [closed], reduction_bases={KEY: wrong})
    with owned.manager.get_new_session() as session:
        assert session.query(Position).one().amount == 2
        assert session.query(RuntimeJournal).filter_by(event_key='hold:partial').count() == 0


def test_restoring_remaining_cost_does_not_scan_fills_orders_or_executor_history(owned):
    seeded(owned)
    statements = []

    def observe(connection, cursor, statement, parameters, context, many):
        statements.append(statement.lower())

    event.listen(owned.engine, 'before_cursor_execute', observe)
    try:
        assert owned.recorder.get_reduction_bases()[KEY].opening_fees_quote == 2
    finally:
        event.remove(owned.engine, 'before_cursor_execute', observe)
    assert any('runtime_checkpoints' in sql for sql in statements)
    assert not any('trade_fills' in sql or 'submission_attempts' in sql or 'executors' in sql for sql in statements)


def test_spot_only_session_carries_unchanged_cost_for_an_inactive_perpetual_controller(owned):
    seeded(owned)
    expected = owned.recorder.get_reduction_bases()
    owned.market.name = owned.market.display_name = 'okx'
    info = executor_info('spot')
    info.config.controller_id = info.controller_id = 'spot-maker'
    evt = fill(order_id='spot-order', amount=Decimal('.1'), price=Decimal('100'),
               trade_fee=AddedToCostTradeFee(flat_fees=[TokenAmount('USDT', Decimal('.01'))]))
    owned.recorder._did_fill_order(MarketEvent.OrderFilled.value, owned.market, evt)
    spot = Position(id='spot', controller_id='spot-maker', connector_name='okx', trading_pair='BTC-USDT',
                    side='BUY', timestamp=1700000000000, amount=Decimal('.1'), breakeven_price=Decimal(100),
                    volume_traded_quote=Decimal(10), cum_fees_quote=Decimal('.01'),
                    realized_pnl_quote=Decimal(0), unrealized_pnl_quote=Decimal(0))
    owned.recorder.checkpoint_held_positions([spot], [info], reduction_bases={})
    assert owned.recorder.get_reduction_bases() == expected
    assert runtime().positions_held['maker'][0].reduction_basis == expected[KEY]
