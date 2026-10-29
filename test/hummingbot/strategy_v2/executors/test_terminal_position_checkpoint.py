"""Terminal fill proof must precede persisted V2 position snapshots."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.data_type.common import OrderType, PositionMode, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState, TradeUpdate
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount
from hummingbot.core.rate_oracle.rate_oracle import RateOracle
from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator
from hummingbot.strategy_v2.executors.position_executor.data_types import PositionExecutorConfig, TripleBarrierConfig
from hummingbot.strategy_v2.executors.position_executor.position_executor import PositionExecutor
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executors import TrackedOrder


def setup_checkpoint(monkeypatch):
    recorder = MagicMock()
    recorder.iter_executor_performance.side_effect = lambda *args, **kwargs: (row for row in ())
    recorder.get_all_positions.return_value = []
    monkeypatch.setattr(MarketsRecorder, 'get_instance', lambda: recorder)
    monkeypatch.setattr(RateOracle, 'get_instance', lambda: SimpleNamespace(
        get_pair_rate=lambda pair: Decimal('10') if pair == 'USDT-LIFE' else Decimal('.1')))
    connector = MagicMock()
    connector.trading_rules = {'LIFE-USDT': TradingRule(trading_pair='LIFE-USDT')}
    connector.get_price_by_type.return_value = Decimal('.1')
    connector.quantize_order_amount.side_effect = lambda trading_pair, amount, **kwargs: amount
    connector.quantize_order_price.side_effect = lambda trading_pair, price, **kwargs: price
    connector.position_mode = PositionMode.HEDGE
    strategy = MagicMock()
    strategy.connectors = {'okx_perpetual': connector}
    strategy.config = SimpleNamespace(mm_drawdown_scope=None)
    strategy.markets = {'okx_perpetual': {'LIFE-USDT'}}
    strategy.current_timestamp = 100
    strategy.market_data_provider.get_price_by_type.return_value = Decimal('.1')
    strategy.get_price.return_value = Decimal('.1')
    config = PositionExecutorConfig(timestamp=1, controller_id='maker', connector_name='okx_perpetual',
                                    trading_pair='LIFE-USDT', side=TradeType.BUY, entry_price=Decimal('.1'),
                                    amount=Decimal('1'), use_configured_entry_price=True,
                                    triple_barrier_config=TripleBarrierConfig(open_order_type=OrderType.LIMIT_MAKER))
    executor = PositionExecutor(strategy, config)
    order = InFlightOrder(client_order_id='entry', exchange_order_id='1', trading_pair='LIFE-USDT',
                          order_type=OrderType.LIMIT_MAKER, trade_type=TradeType.BUY, amount=Decimal('1'),
                          price=Decimal('.1'), creation_timestamp=1)
    order.current_state = OrderState.OPEN
    executor._open_order = TrackedOrder(order_id='entry')
    executor._open_order.order = order
    executor._status = RunnableStatus.RUNNING
    orchestrator = ExecutorOrchestrator(strategy)
    orchestrator.active_executors = {'maker': [executor]}
    orchestrator.positions_held = {'maker': []}
    return orchestrator, executor, order, recorder


def fill(order, identity, amount):
    update = TradeUpdate(trade_id=identity, client_order_id='entry', exchange_order_id='1',
                         trading_pair='LIFE-USDT', fill_timestamp=2, fill_price=Decimal('.1'),
                         fill_base_amount=Decimal(amount), fill_quote_amount=Decimal(amount) * Decimal('.1'),
                         fee=AddedToCostTradeFee(flat_fees=[TokenAmount('USDT', Decimal('.000001'))]))
    order.update_with_trade_update(update)


@pytest.mark.asyncio
@pytest.mark.parametrize('initial', ['0', '.011'])
async def test_terminal_fill_is_included_once_before_position_store(monkeypatch, initial):
    orchestrator, executor, order, recorder = setup_checkpoint(monkeypatch)
    if Decimal(initial):
        fill(order, 'first', initial)

    async def terminal_proof(covered_ids):
        assert set(covered_ids) == {'entry'}
        assert executor.terminated.is_set()
        recorder.update_or_store_position.assert_not_called()
        fill(order, 'late', '.001')
        fill(order, 'late', '.001')  # Duplicate ACK/trade ID is not new exposure.
        order.current_state = OrderState.CANCELED
        # A report tick during cancellation cannot commit a partial checkpoint.
        orchestrator._update_positions_from_done_executors()
        assert not orchestrator.positions_held.get('maker')
        return {'entry': order.to_json()}

    await orchestrator.stop(keep_position=True, before_position_checkpoint=terminal_proof)
    recorder.update_or_store_position.assert_called_once()
    held = recorder.update_or_store_position.call_args.args[0]
    assert held.amount == Decimal(initial) + Decimal('.001')
    assert held.cum_fees_quote == Decimal('.000001') * (2 if Decimal(initial) else 1)
    assert len(executor._held_position_orders) == 1
    assert not orchestrator.active_executors


@pytest.mark.asyncio
async def test_failed_terminal_proof_keeps_candidates_without_persisting_partial_hold(monkeypatch):
    orchestrator, executor, order, recorder = setup_checkpoint(monkeypatch)

    async def unavailable(covered_ids):
        fill(order, 'late', '.001')
        raise OSError('terminal read unavailable')

    with pytest.raises(OSError, match='terminal read'):
        await orchestrator.stop(keep_position=True, before_position_checkpoint=unavailable)
    recorder.update_or_store_position.assert_not_called()
    assert orchestrator.active_executors['maker'] == [executor]

    async def retry(covered_ids):
        assert set(covered_ids) == {'entry'}
        order.current_state = OrderState.CANCELED
        return {'entry': order.to_json()}

    await orchestrator.stop(keep_position=True, before_position_checkpoint=retry)
    assert recorder.update_or_store_position.call_args.args[0].amount == Decimal('.001')


@pytest.mark.asyncio
async def test_short_entry_retains_late_fill_and_hedge_side(monkeypatch):
    orchestrator, executor, order, recorder = setup_checkpoint(monkeypatch)
    executor.config.side = TradeType.SELL
    order.trade_type = TradeType.SELL
    fill(order, 'first', '.011')

    async def terminal_proof(orders):
        fill(order, 'late', '.001')
        order.current_state = OrderState.CANCELED
        return {'entry': order.to_json()}

    await orchestrator.stop(keep_position=True, before_position_checkpoint=terminal_proof)
    saved = recorder.update_or_store_position.call_args.args[0]
    assert saved.amount == Decimal('.012') and saved.side == 'SELL'
    assert saved.cum_fees_quote == Decimal('.000002')


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['missing', 'nonterminal', 'base', 'quote', 'fills'])
async def test_invalid_terminal_proof_cannot_commit_a_position(monkeypatch, invalid):
    orchestrator, executor, order, recorder = setup_checkpoint(monkeypatch)
    fill(order, 'first', '.011')

    async def incomplete(covered_ids):
        order.current_state = OrderState.CANCELED
        proof = order.to_json()
        if invalid == 'missing':
            return {}
        if invalid == 'nonterminal':
            proof['last_state'] = str(OrderState.OPEN.value)
        elif invalid == 'base':
            proof['executed_amount_base'] = '.012'
        elif invalid == 'quote':
            proof['executed_amount_quote'] = '.0012'
        elif invalid == 'fills':
            proof['order_fills'] = {}
        return {'entry': proof}

    with pytest.raises(ValueError, match='[Tt]erminal|[Pp]osition'):
        await orchestrator.stop(keep_position=True, before_position_checkpoint=incomplete)
    # Even a report tick after rejection must not aggregate/persist bad data.
    orchestrator._update_positions_from_done_executors()
    assert not orchestrator.positions_held.get('maker')
    recorder.update_or_store_position.assert_not_called()
