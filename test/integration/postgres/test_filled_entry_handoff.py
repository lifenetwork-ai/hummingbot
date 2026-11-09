"""An early filled-entry hold uses the same fenced PostgreSQL checkpoint/recovery."""
from decimal import Decimal
from test.hummingbot.strategy_v2.controllers.test_filled_entry_handoff import account_observed
from test.hummingbot.strategy_v2.controllers.test_inventory_cap_lifecycle import (
    controller_actions,
    fill,
    setup_lifecycle,
)
from test.integration.postgres.test_ownership import owned as _owned

import pytest

from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.core.bot_ownership import OwnershipLost
from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator
from hummingbot.strategy_v2.models.base import RunnableStatus

owned = _owned


@pytest.mark.parametrize('connector_name', ['okx', 'okx_perpetual'])
@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
@pytest.mark.parametrize('lose_owner', [False, True])
def test_early_handoff_checkpoint_restores_exact_hold_or_keeps_it_resident_on_fence_failure(
        owned, monkeypatch, connector_name, side, lose_owner):
    async def handoff():
        state = await setup_lifecycle(monkeypatch, connector_name, side, initial=(2000, 0), executor_refresh_time=60)
        assert fill(state.order, 'full', str(state.order.amount))
        state.order.current_state = OrderState.FILLED
        account_observed(state, side, initial=(2000, 0))
        state.controller.market_data_provider.time.return_value = 1003
        state.strategy.current_timestamp = 1003
        actions = await controller_actions(state.controller, state.executor)
        assert len(actions) == 1 and actions[0].keep_position
        state.orchestrator.execute_actions(actions)
        await state.executor.control_task()
        assert state.executor.status == RunnableStatus.TERMINATED
        state.orchestrator._update_positions_from_done_executors()
        state.orchestrator._update_positions_from_done_executors()
        return state

    state = owned.loop.run_until_complete(handoff())
    controller_id = state.controller.config.id
    monkeypatch.setattr(MarketsRecorder, 'get_instance', lambda: owned.recorder)
    state.strategy.markets = {connector_name: {'LIFE-USDT'}}
    state.strategy.market_data_provider = state.controller.market_data_provider
    if lose_owner:
        owned.owner.release()
        with pytest.raises(OwnershipLost):
            state.orchestrator.store_all_positions()
        assert owned.recorder.get_all_positions() == []
        assert len(state.orchestrator.positions_held[controller_id]) == 1
        return
    state.orchestrator.store_all_positions()
    rows = owned.recorder.get_all_positions()
    assert len(rows) == 1 and rows[0].amount == state.order.amount
    assert rows[0].side == side.name and rows[0].cum_fees_quote == Decimal('.000001')
    restored = object.__new__(ExecutorOrchestrator)
    restored.positions_held = {controller_id: []}
    restored._load_position_from_db(controller_id, rows[0])
    holds = restored.positions_held[controller_id]
    assert len(holds) == 1
    assert holds[0].net_amount_base == state.order.amount * (1 if side == TradeType.BUY else -1)
    assert holds[0].cum_fees_quote == Decimal('.000001')
