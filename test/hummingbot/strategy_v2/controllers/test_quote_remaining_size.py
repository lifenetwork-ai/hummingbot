"""A resting quote's remaining size is distinct from conservative risk reservation."""
from decimal import ROUND_DOWN, Decimal
from test.hummingbot.strategy_v2.controllers.test_volatility_defense import defended_controller
from types import SimpleNamespace

import pytest

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState
from hummingbot.strategy_v2.models.base import RunnableStatus


def partial_quote(monkeypatch, side):
    clock = SimpleNamespace(epoch=1200, monotonic=100)
    controller = defended_controller(monkeypatch, clock, total_amount_quote='100',
                                     inventory_skew=dict(enabled=True, target_base_pct='.01', inventory_range_multiplier='10'))
    monkeypatch.setattr('hummingbot.strategy_v2.utils.mm_spread.natr_value', lambda *args: Decimal('.001'))
    connector = controller.market_data_provider.get_connector_with_fallback.return_value
    connector.quantize_order_price.side_effect = lambda pair, p: p.quantize(Decimal('.000001'), rounding=ROUND_DOWN)
    connector.get_order_price_quantum.return_value = Decimal('.000001')
    connector.quantize_order_amount.side_effect = lambda pair, a: a.quantize(Decimal('.00000001'), rounding=ROUND_DOWN)
    connector.trading_rules['LIFE-USDT'] = SimpleNamespace(min_order_size=Decimal('.00000001'), min_notional_size=Decimal('0'))
    controller._evaluate_guard.return_value = dict(ready=True, can_admit=True, market_mid=Decimal('.1'))
    cash = {'LIFE': Decimal('1000'), 'USDT': Decimal('10000')}
    connector.get_balance.side_effect = lambda asset: cash[asset]

    def refresh():
        connector.account_balance_observations = {asset: dict(cash_balance=str(value), observed_at=clock.epoch)
                                                  for asset, value in cash.items()}
        controller.update_reference(sample_guard=True)
        assert controller.processed_data['ready']

    refresh()
    controller._spread_policy.defense.state = 'normal'
    refresh()
    level = f'{side.name.lower()}_0'
    price, amount = controller.get_price_and_amount(level)
    config = controller.get_executor_config(level, price, amount)
    assert config is not None
    order = InFlightOrder(client_order_id='entry', exchange_order_id='venue-entry', trading_pair='LIFE-USDT',
                          order_type=OrderType.LIMIT_MAKER, trade_type=side, amount=config.amount,
                          price=config.entry_price, creation_timestamp=clock.epoch, initial_state=OrderState.PARTIALLY_FILLED)
    order.executed_amount_base = Decimal('.001')
    connector.in_flight_orders = {'entry': order}
    executor = SimpleNamespace(id='working', config=config, is_active=True, status=RunnableStatus.RUNNING,
                               custom_info={'order_ids': ['entry'], 'level_id': level}, close_type=None)
    controller.executors_info = [executor]
    direction = Decimal('1') if side == TradeType.BUY else Decimal('-1')
    cash['LIFE'] += direction * order.executed_amount_base
    cash['USDT'] -= direction * order.executed_amount_base * order.price + Decimal('.000001')
    refresh()
    return controller, connector, config, order, executor, refresh


@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
def test_partial_fill_keeps_remaining_quote_within_new_skew_limit(monkeypatch, side):
    controller, _, config, order, _, refresh = partial_quote(monkeypatch, side)
    price, amount = controller.get_price_and_amount(config.level_id)
    assert config.amount * config.entry_price > price * amount
    remaining = order.amount - order.executed_amount_base
    assert remaining * order.price < price * amount
    assert controller.spread_quote_valid(side, order.price, remaining, level_id=config.level_id)
    original_amount = config.amount
    for _ in range(3):
        refresh()
        assert controller._defense_refresh_actions() == []
        assert config.amount == original_amount  # Risk and allocation still reserve the original entry.
    if side == TradeType.BUY:
        assert controller._exposure()[0] == Decimal('1000.001') + original_amount


@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
@pytest.mark.parametrize('fault', ['missing', 'pending', 'pending_cancel', 'no_exchange_id', 'unfilled', 'wrong_pair', 'wrong_side', 'wrong_price', 'nonfinite', 'overfilled', 'oversized', 'bad_ids', 'ambiguous'])
def test_ambiguous_order_keeps_full_size_protection(monkeypatch, side, fault):
    controller, connector, _, order, executor, _ = partial_quote(monkeypatch, side)
    if fault == 'missing':
        connector.in_flight_orders = {}
    elif fault == 'pending':
        order.current_state = OrderState.PENDING_CREATE
    elif fault == 'pending_cancel':
        order.current_state = OrderState.PENDING_CANCEL
    elif fault == 'no_exchange_id':
        order.exchange_order_id = None
    elif fault == 'unfilled':
        order.executed_amount_base = Decimal('0')
    elif fault == 'oversized':
        order.amount += 1
    elif fault == 'bad_ids':
        executor.custom_info['order_ids'] = ['entry'] * 4
    elif fault == 'ambiguous':
        connector.in_flight_orders['second'] = order
        executor.custom_info['order_ids'].append('second')
    elif fault == 'wrong_pair':
        order.trading_pair = 'OTHER-USDT'
    elif fault == 'wrong_side':
        order.trade_type = TradeType.SELL if side == TradeType.BUY else TradeType.BUY
    elif fault == 'wrong_price':
        order.price += Decimal('.001')
    elif fault == 'nonfinite':
        order.executed_amount_base = Decimal('NaN')
    else:
        order.executed_amount_base = order.amount + 1
    actions = controller._defense_refresh_actions()
    assert len(actions) == 1 and actions[0].executor_id == 'working' and actions[0].keep_position
    assert controller._defense_refresh_actions() == []


@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
@pytest.mark.parametrize('protection', ['reduced_size', 'spread', 'fee'])
def test_remaining_quote_still_withdraws_for_real_policy_breach(monkeypatch, side, protection):
    controller, connector, config, _, _, _ = partial_quote(monkeypatch, side)
    if protection == 'reduced_size':
        controller._spread_policy.defense.state = 'reduced'
    elif protection == 'spread':
        index = 0 if side == TradeType.BUY else 1
        controller._effective_spreads[index][0] = Decimal('.05')
    else:
        from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee
        connector.get_fee.return_value = AddedToCostTradeFee(percent=Decimal('.1'))
    assert controller._defense_refresh_actions()[0].keep_position
    assert config.amount > 0


@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
def test_retained_partial_quote_still_reserves_full_allocation(monkeypatch, side):
    controller, _, config, _, _, _ = partial_quote(monkeypatch, side)
    import hummingbot.core.order_submission_guard as gate
    monkeypatch.setattr(gate, 'postgres_requested', lambda: False)
    assert controller._defense_refresh_actions() == []
    other_side = TradeType.SELL if side == TradeType.BUY else TradeType.BUY
    new_price, new_amount = controller.get_price_and_amount(f'{other_side.name.lower()}_0')
    assert config.amount * config.entry_price + new_price * new_amount > controller._quote_budget()
    # Deducting partial fills from this separate reservation would admit extra risk.
    assert controller.create_actions_proposal() == []


@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
def test_late_fill_precision_and_duplicate_snapshots_keep_safe_remainder(monkeypatch, side):
    controller, _, config, order, executor, _ = partial_quote(monkeypatch, side)
    original = config.amount
    order.executed_amount_base += Decimal('0.000000000000000001')
    expected = order.amount - Decimal('0.001000000000000001')
    for _ in range(3):
        assert controller._resting_quote_amount(executor) == expected
        assert controller._defense_refresh_actions() == []
        assert config.amount == original
    if side == TradeType.BUY:
        assert controller._exposure()[0] == Decimal('1000.001') + original
