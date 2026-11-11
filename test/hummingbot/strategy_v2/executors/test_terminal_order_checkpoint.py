"""A native closing quote must retain fills arriving after its loop freezes."""
from decimal import Decimal
from test.hummingbot.strategy_v2.executors.test_terminal_position_checkpoint import fill, setup_checkpoint
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hummingbot.core.data_type.common import OrderType, PositionAction, TradeType
from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount
from hummingbot.core.event.events import MarketOrderFailureEvent
from hummingbot.core.rate_oracle.rate_oracle import RateOracle
from hummingbot.strategy_v2.executors.executor_orchestrator import PositionHold
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executors import TrackedOrder


def closing_quote(monkeypatch, side=TradeType.SELL):
    orchestrator, entry, order, recorder = setup_checkpoint(monkeypatch)
    order.trade_type = side
    order.position = PositionAction.CLOSE
    config = OrderExecutorConfig(timestamp=1, controller_id='maker', connector_name='okx_perpetual',
                                 trading_pair='LIFE-USDT', side=side, amount=Decimal('1'), price=Decimal('.1'),
                                 position_action=PositionAction.CLOSE, execution_strategy=ExecutionStrategy.LIMIT_MAKER,
                                 mm_session_id='closing-session', mm_generation=1)
    executor = OrderExecutor(entry._strategy, config)
    executor._order = TrackedOrder(order_id=order.client_order_id)
    executor._order.order = order
    executor._status = RunnableStatus.RUNNING
    leg = TradeType.BUY if side == TradeType.SELL else TradeType.SELL
    held = PositionHold('okx_perpetual', 'LIFE-USDT', leg)
    held._process_order(leg == TradeType.BUY, Decimal('1'), Decimal('.1'))
    held.volume_traded_quote = Decimal('.1')
    held.cum_fees_quote = Decimal('.001')
    orchestrator.positions_held = {'maker': [held]}
    orchestrator.active_executors = {'maker': [executor]}
    return orchestrator, executor, order, recorder


@pytest.mark.parametrize('reason', ['QuoteRefreshRequired', 'MarketReadinessBlocked', 'SessionSubmissionBlocked', 'SubmissionBlocked'])
def test_managed_close_retires_a_locally_blocked_identity_instead_of_retrying_stale_authority(monkeypatch, reason):
    _, executor, order, _ = closing_quote(monkeypatch)
    executor._order.order = None  # A failed admission has no creation callback.
    executor.get_in_flight_order = lambda *args: order
    order.current_state = OrderState.FAILED
    event = MarketOrderFailureEvent(timestamp=2, order_id=order.client_order_id,
                                    order_type=OrderType.LIMIT_MAKER, error_type=reason)
    executor.process_order_failed_event(None, None, event)
    assert executor.status == RunnableStatus.SHUTTING_DOWN
    assert executor._current_retries == 0
    assert executor._failed_orders[0].order is order


@pytest.mark.parametrize('state', [OrderState.CANCELED, OrderState.FAILED, OrderState.PARTIALLY_FILLED])
def test_forced_order_snapshot_keeps_a_main_order_partial_before_its_callback(monkeypatch, state):
    _, executor, order, _ = closing_quote(monkeypatch)
    fill(order, 'first', '.2')
    order.current_state = state
    snapshots = executor._collect_held_position_orders()
    assert len(snapshots) == 1
    assert Decimal(snapshots[0]['executed_amount_base']) == Decimal('.2')
    assert snapshots[0]['position'] == 'CLOSE'
    assert set(snapshots[0]['order_fills']) == {'first'}


@pytest.mark.parametrize('retired', ['_canceled_orders', '_failed_orders'])
def test_retry_and_fee_totals_include_late_fill_on_a_previously_empty_retired_order(monkeypatch, retired):
    _, executor, order, _ = closing_quote(monkeypatch)
    tracked = executor._order
    getattr(executor, retired).append(tracked)
    executor._order = None
    order.current_state = OrderState.CANCELED if retired == '_canceled_orders' else OrderState.FAILED
    fill(order, 'late', '.2')
    executor.get_order_price = lambda: Decimal('.1')
    executor.place_order = MagicMock(return_value='replacement')
    executor.place_open_order()
    executor.place_order.assert_called_once()
    sent = executor.place_order.call_args.kwargs
    assert sent['amount'] == Decimal('.8')
    assert sent['position_action'] == PositionAction.CLOSE
    assert executor.executed_amount_base == Decimal('.2')
    assert executor.filled_amount_quote == Decimal('.02')
    assert executor.cum_fees_quote == Decimal('.000001')
    # Duplicate references (for example a callback moving a retired partial)
    # cannot double its execution or fees.
    executor._partial_filled_orders.append(tracked)
    assert executor.executed_amount_base == Decimal('.2')
    assert executor.cum_fees_quote == Decimal('.000001')


def test_cancel_callback_preserves_close_effect_and_partial_for_retry(monkeypatch):
    _, executor, order, _ = closing_quote(monkeypatch)
    fill(order, 'early', '.2')
    order.current_state = OrderState.CANCELED
    executor.process_order_canceled_event(None, None, SimpleNamespace(order_id='entry'))
    assert executor._order is None
    assert executor.executed_amount_base == Decimal('.2')
    assert executor._collect_held_position_orders()[0]['position'] == 'CLOSE'


def test_legacy_report_does_not_add_a_scan_of_zero_fill_cancel_history(monkeypatch):
    _, executor, order, _ = closing_quote(monkeypatch)
    executor.config = executor.config.model_copy(update={'mm_session_id': None, 'mm_generation': None})
    fill(order, 'live', '.2')

    class NoReportScan(list):
        def __iter__(self):
            raise AssertionError('Legacy reports must not scan lifetime cancellation history')

    executor._canceled_orders = NoReportScan()
    executor._failed_orders = NoReportScan()
    assert executor.executed_amount_base == Decimal('.2')
    assert executor.filled_amount_quote == Decimal('.02')
    assert executor.average_executed_price == Decimal('.1')
    assert executor.cum_fees_quote == Decimal('.000001')


@pytest.mark.parametrize('completion', [False, True])
def test_managed_snapshot_cannot_replace_unavailable_original_fee_valuation_with_zero(monkeypatch, completion):
    _, executor, order, _ = closing_quote(monkeypatch)
    monkeypatch.setenv('STORAGE_BACKEND', 'postgres')
    fill(order, 'first', '1')
    order.current_state = OrderState.FILLED
    order.order_fills['first'] = order.order_fills['first']._replace(
        fee=AddedToCostTradeFee(flat_fees=[TokenAmount('UNKNOWN', Decimal('1'))]))
    monkeypatch.setattr(RateOracle, 'get_instance', lambda: SimpleNamespace(get_pair_rate=lambda pair: None))
    with pytest.raises(ValueError):
        if completion:
            executor.update_tracked_order_with_order_id = lambda identity: None
            executor.process_order_completed_event(None, None, SimpleNamespace(order_id='entry'))
        else:
            executor._collect_held_position_orders()
    assert not executor._held_position_orders


@pytest.mark.asyncio
@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
async def test_order_terminal_first_checkpoint_reduces_its_own_hedge_bucket_once(monkeypatch, side):
    orchestrator, executor, order, recorder = closing_quote(monkeypatch, side)
    fill(order, 'early', '.2')

    async def terminal_proof(orders):
        assert set(orders) == {'entry'}
        assert executor.terminated.is_set()
        assert executor.close_type is None
        recorder.update_or_store_positions.assert_not_called()
        fill(order, 'late', '.1')
        fill(order, 'late', '.1')
        order.current_state = OrderState.CANCELED
        orchestrator._update_positions_from_done_executors()
        assert abs(orchestrator.positions_held['maker'][0].net_amount_base) == 1
        return {'entry': order.to_json()}

    await orchestrator.stop(keep_position=True, before_position_checkpoint=terminal_proof)
    recorder.update_or_store_positions.assert_called_once()
    saved = recorder.update_or_store_positions.call_args.args[0]
    assert len(saved) == 1
    assert saved[0].amount == Decimal('.7')
    assert saved[0].side == ('SELL' if side == TradeType.BUY else 'BUY')
    assert saved[0].volume_traded_quote == Decimal('.13')
    assert saved[0].cum_fees_quote == Decimal('.001002')
    assert len(executor._held_position_orders) == 1
    assert not orchestrator.active_executors


@pytest.mark.asyncio
@pytest.mark.parametrize('slot', ['_canceled_orders', '_failed_orders', '_partial_filled_orders'])
async def test_terminal_first_proof_keeps_retired_native_references_for_late_fills(monkeypatch, slot):
    orchestrator, executor, order, recorder = closing_quote(monkeypatch)
    getattr(executor, slot).append(executor._order)
    executor._order = None

    async def proof(orders):
        assert orders['entry'][1] is order
        assert executor.terminated.is_set()
        fill(order, 'late', '.2')
        order.current_state = OrderState.CANCELED
        return {'entry': order.to_json()}

    await orchestrator.stop(keep_position=True, before_position_checkpoint=proof)
    assert recorder.update_or_store_positions.call_args.args[0][0].amount == Decimal('.8')
    assert len(executor._held_position_orders) == 1


@pytest.mark.asyncio
async def test_failed_proof_retains_the_order_executor_and_live_reference_for_retry(monkeypatch):
    orchestrator, executor, order, recorder = closing_quote(monkeypatch)

    async def unavailable(orders):
        fill(order, 'late', '.2')
        raise OSError('terminal read unavailable')

    with pytest.raises(OSError, match='terminal read unavailable'):
        await orchestrator.stop(keep_position=True, before_position_checkpoint=unavailable)
    recorder.update_or_store_positions.assert_not_called()
    assert abs(orchestrator.positions_held['maker'][0].net_amount_base) == 1
    assert orchestrator._checkpoint_candidates == [executor]

    async def retry(orders):
        assert orders['entry'][1] is order
        order.current_state = OrderState.CANCELED
        return {'entry': order.to_json()}

    await orchestrator.stop(keep_position=True, before_position_checkpoint=retry)
    assert recorder.update_or_store_positions.call_args.args[0][0].amount == Decimal('.8')


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['missing', 'nonterminal', 'base', 'quote', 'fills'])
async def test_invalid_order_terminal_proof_never_publishes_the_partial_reduction(monkeypatch, invalid):
    orchestrator, executor, order, recorder = closing_quote(monkeypatch)
    fill(order, 'early', '.2')

    async def proof(orders):
        order.current_state = OrderState.CANCELED
        value = order.to_json()
        if invalid == 'missing':
            return {}
        if invalid == 'nonterminal':
            value['last_state'] = str(OrderState.OPEN.value)
        elif invalid == 'base':
            value['executed_amount_base'] = '.3'
        elif invalid == 'quote':
            value['executed_amount_quote'] = '.03'
        else:
            value['order_fills'] = {}
        return {'entry': value}

    with pytest.raises(ValueError, match='[Tt]erminal|[Pp]osition'):
        await orchestrator.stop(keep_position=True, before_position_checkpoint=proof)
    orchestrator._update_positions_from_done_executors()
    assert abs(orchestrator.positions_held['maker'][0].net_amount_base) == 1
    recorder.update_or_store_positions.assert_not_called()
