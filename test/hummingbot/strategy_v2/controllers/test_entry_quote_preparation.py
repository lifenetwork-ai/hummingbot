from decimal import ROUND_DOWN, Decimal
from test.hummingbot.strategy_v2.controllers.test_inventory_cap_lifecycle import setup_lifecycle

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executors import TrackedOrder
from hummingbot.strategy_v2.utils.mm_spread import SpreadPolicy


async def queued(monkeypatch, name, side):
    state = await setup_lifecycle(monkeypatch, name, side, runtime_config_managed=True,
                                  mm_session_id='session', mm_generation=1, scenario='follow_token',
                                  references=[dict(pair='BTC-USDT', weight='1')])
    controller, executor = state.controller, state.executor
    state.connector.get_fee.return_value = AddedToCostTradeFee(percent=Decimal('.001'))
    state.connector.get_order_price_quantum.return_value = Decimal('.00000001')
    state.connector.quantize_order_price.side_effect = lambda pair, price: price.quantize(Decimal('.00000001'), rounding=ROUND_DOWN)
    state.connector.quantize_order_amount.side_effect = lambda pair, amount: amount.quantize(Decimal('.00000001'), rounding=ROUND_DOWN)
    policy = controller.config.spread_policy.model_copy(update={'fee_floor': True})
    controller.config = controller.config.model_copy(update={'spread_policy': policy})
    controller._spread_policy = SpreadPolicy(policy)
    controller.update_reference()
    executor.config = executor.config.model_copy(update={'use_configured_entry_price': True, 'controller_id': controller.config.id})
    executor._open_order = None
    state.strategy.buy.return_value = state.strategy.sell.return_value = 'new-identity'
    controller.executors_info = [executor.executor_info]
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize('name', ['okx', 'okx_perpetual'])
@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
@pytest.mark.parametrize('change', ['reference', 'fee'])
async def test_queued_first_entry_prepares_fresh_price_without_expanding_budget(monkeypatch, name, side, change):
    state = await queued(monkeypatch, name, side)
    executor, controller = state.executor, state.controller
    # The new profile allocation could otherwise grow this smaller queued quote.
    executor.config.amount = Decimal('400')
    previous = executor.config.model_copy(deep=True)
    if change == 'reference':
        state.prices['BTC-USDT'] = Decimal('99') if side == TradeType.BUY else Decimal('101')
    else:
        state.connector.get_fee.return_value = AddedToCostTradeFee(percent=Decimal('.02'))
    executor.place_open_order()
    call = (state.strategy.buy if side == TradeType.BUY else state.strategy.sell).call_args
    assert call.args[4] != previous.entry_price
    assert call.args[2] <= previous.amount
    assert call.args[2] * call.args[4] <= previous.amount * previous.entry_price
    assert controller.spread_quote_valid(side, call.args[4], call.args[2], level_id=previous.level_id)
    assert executor.config.id == previous.id and executor.config.mm_generation == previous.mm_generation


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['stale', 'revoked', 'generation', 'controller_missing', 'caps', 'provider', 'control_error', 'stop_requested'])
async def test_no_order_identity_created_when_queued_entry_loses_admission(monkeypatch, failure):
    state = await queued(monkeypatch, 'okx', TradeType.BUY)
    if failure == 'stale':
        state.controller.market_data_provider.time.return_value = 1020
    elif failure == 'revoked':
        state.controller.revoke_session()
    elif failure == 'generation':
        state.executor.config.mm_generation += 1
    elif failure == 'controller_missing':
        state.strategy.controllers.clear()
    elif failure == 'caps':
        state.connector.get_balance.side_effect = lambda asset: Decimal('20000')
    elif failure == 'provider':
        state.controller.market_data_provider.ready = False
    elif failure == 'control_error':
        state.controller._control_error = 'failed'
    else:
        state.controller._stops_requested.add(state.executor.config.id)
    previous = state.executor.config.model_copy(deep=True)
    state.executor.place_open_order()
    state.strategy.buy.assert_not_called()
    state.strategy.sell.assert_not_called()
    assert state.executor._open_order is None
    assert state.executor.status == RunnableStatus.SHUTTING_DOWN
    assert state.executor.config == previous


@pytest.mark.asyncio
@pytest.mark.parametrize('existing', ['failed', 'open', 'prepared'])
async def test_retry_never_changes_existing_entry_parameters(monkeypatch, existing):
    state = await queued(monkeypatch, 'okx', TradeType.BUY)
    if existing == 'failed':
        state.executor._failed_orders = [TrackedOrder('old-identity')]
    elif existing == 'open':
        state.executor._open_order = TrackedOrder('old-identity')
    else:
        state.executor._entry_quote_prepared = True
    previous = state.executor.config.model_copy(deep=True)
    state.prices['BTC-USDT'] = Decimal('99')
    state.executor.place_open_order()
    assert state.executor.config == previous
    assert state.strategy.buy.call_args.args[4] == previous.entry_price


@pytest.mark.asyncio
async def test_unmanaged_executor_preserves_legacy_configured_price(monkeypatch):
    state = await queued(monkeypatch, 'okx', TradeType.BUY)
    state.executor.config.mm_session_id = None
    previous = state.executor.config.model_copy(deep=True)
    state.prices['BTC-USDT'] = Decimal('99')
    state.executor.place_open_order()
    assert state.executor.config == previous
    assert state.strategy.buy.call_args.args[4] == previous.entry_price


@pytest.mark.asyncio
async def test_preparation_exception_cannot_skip_fresh_preparation_on_next_attempt(monkeypatch):
    state = await queued(monkeypatch, 'okx', TradeType.BUY)
    previous = state.executor.config.model_copy(deep=True)
    original = type(state.controller).prepare_entry_order

    def fail(controller, config):
        raise RuntimeError('unexpected_preparation_error')

    monkeypatch.setattr(type(state.controller), 'prepare_entry_order', fail)
    with pytest.raises(RuntimeError, match='unexpected_preparation_error'):
        state.executor.place_open_order()
    assert not state.executor._entry_quote_prepared
    assert state.executor._open_order is None and state.executor.config == previous
    state.strategy.buy.assert_not_called()
    monkeypatch.setattr(type(state.controller), 'prepare_entry_order', original)
    state.prices['BTC-USDT'] = Decimal('99')
    state.executor.place_open_order()
    assert state.strategy.buy.call_args.args[4] != previous.entry_price
