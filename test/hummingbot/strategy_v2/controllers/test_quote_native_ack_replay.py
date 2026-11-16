"""Native proposal -> executor -> OKX REST -> ACK after a committed fill handoff.

Only external book/account/transport observations and recorder persistence are
faked. Long periodic waits isolate event progress; they are not latency SLOs.
"""
import asyncio
import json
from decimal import Decimal
from test.hummingbot.strategy_v2.controllers.test_filled_entry_handoff import account_observed
from test.hummingbot.strategy_v2.controllers.test_quote_progress_replay import complete, eventually, replay
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from bidict import bidict

from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core import order_submission_guard as gates
from hummingbot.core.data_type.common import PositionAction, TradeType
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee
from hummingbot.core.mm_liquidity import own_quotes
from hummingbot.core.mm_quote_activity import quote_activity
from hummingbot.core.web_assistant.rest_assistant import RESTAssistant
from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator


@pytest.mark.asyncio
@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
@pytest.mark.parametrize('account_first', [False, True])
async def test_native_replacement_waits_for_account_and_commit_then_ack_without_duplicate_send(monkeypatch, side, account_first):
    async with replay(monkeypatch, side) as state:
        baseline = asyncio.all_tasks()
        native = OkxExchange(okx_api_key='offline', okx_secret_key='offline', okx_passphrase='offline',
                             trading_pairs=['LIFE-USDT'])
        native._set_current_timestamp(1003)
        native._set_trading_pair_symbol_map(bidict({'LIFE-USDT': 'LIFE-USDT'}))
        native._trading_rules['LIFE-USDT'] = TradingRule(
            'LIFE-USDT', min_order_size=Decimal('.01'), min_price_increment=Decimal('.0001'),
            min_base_amount_increment=Decimal('.01'))
        native.get_fee = MagicMock(return_value=AddedToCostTradeFee(percent=Decimal('.001')))
        native.get_price_by_type = MagicMock(return_value=Decimal('.1'))
        native.get_price = MagicMock(return_value=Decimal('.1'))
        native.get_balance = MagicMock(side_effect=state.connector.get_balance.side_effect)
        native.get_available_balance = MagicMock(side_effect=state.connector.get_available_balance.side_effect)
        native._user_stream_tracker = SimpleNamespace(last_recv_time=1003)
        native.account_positions = {}  # Shared external-account fixture also handles hedge connectors.
        native._order_tracker.start_tracking_order(state.order)
        peer = next(e for e in state.orchestrator.active_executors[state.controller.config.id] if e is not state.executor)
        native._order_tracker.start_tracking_order(peer._open_order.order)
        state.connector = native
        state.strategy.connectors = {'okx': native}
        state.executor.connectors = {'okx': native}
        state.controller.market_data_provider.get_connector_with_fallback.return_value = native
        state.orchestrator.execute_actions = MethodType(ExecutorOrchestrator.execute_actions, state.orchestrator)
        # Strategy IO delegates to the actual native connector, retaining its
        # order task, REST serialization/authentication and tracker callbacks.
        for name in ('buy', 'sell'):
            def submit(connector_name, trading_pair, amount, order_type, price, position_action=PositionAction.NIL,
                       *, _method=name, **kwargs):
                assert connector_name == 'okx'
                return getattr(native, _method)(trading_pair, amount, order_type, price, position_action=position_action, **kwargs)
            setattr(state.strategy, name, MagicMock(side_effect=submit))
        entered, ack = asyncio.Event(), asyncio.Event()
        requests = []

        async def transport(request):
            assert request.url.endswith('/api/v5/trade/order')
            requests.append(json.loads(request.data))
            entered.set()
            await ack.wait()
            response = MagicMock(status=200)
            response.json = AsyncMock(return_value={
                'code': '0', 'data': [dict(ordId='replacement-venue', sCode='0')]})
            return response

        from test.integration.postgres.test_ownership import Throttler
        rest = RESTAssistant(SimpleNamespace(call=transport), Throttler(), auth=native.authenticator)
        monkeypatch.setattr(native._web_assistants_factory, 'get_rest_assistant', AsyncMock(return_value=rest))
        monkeypatch.setattr(gates, 'postgres_requested', lambda: False)
        account_task = asyncio.create_task(state.strategy._watch_spot_account_progress(native, state.account_event))
        try:
            if account_first:
                account_observed(state, side, initial=(2000, 0))
            complete(state)
            if not account_first:
                await asyncio.sleep(.08)
                assert not requests  # No account coverage means no capacity release/send.
                before = own_quotes(native, 'LIFE-USDT', max_orders=16)
                assert not before['two_sided'] and not before['pending_orders'][side.name]['submission_pending']
                observation = dict(valid=True, eligible=True, proposal=state.controller.quote_diagnostics, **before)
                activity = quote_activity(observation, now=1003, previous=None, delta=0, continuous=False,
                                          monitoring=True, warning_seconds=20)
                assert activity['sides'][side.name]['diagnosis']['stage'] == 'executor_activity'
                account_observed(state, side, initial=(2000, 0))
                state.account_event.set()
            await asyncio.wait_for(entered.wait(), 1)
            state.recorder.checkpoint_held_positions.assert_called_once()
            holds = state.orchestrator.positions_held[state.controller.config.id]
            assert len(holds) == 1 and abs(holds[0].net_amount_base) == state.order.amount
            pending = own_quotes(native, 'LIFE-USDT', max_orders=16)
            assert not pending['two_sided'] and pending['pending_orders'][side.name]['submission_pending'] == 1
            activity = quote_activity(dict(valid=True, eligible=True, proposal=state.controller.quote_diagnostics, **pending),
                                      now=1003, previous=None, delta=0, continuous=False, monitoring=True, warning_seconds=20)
            assert activity['sides'][side.name]['diagnosis']['stage'] == 'submission_pending'
            await asyncio.sleep(.08)
            assert len(requests) == 1  # Pending transport/ACK cannot free the same level again.
            ack.set()
            await eventually(lambda: own_quotes(native, 'LIFE-USDT', max_orders=16)['two_sided'])
            await asyncio.sleep(.08)
            assert len(requests) == 1
            assert requests[0]['side'] == side.name.lower()
            assert len(state.orchestrator.active_executors[state.controller.config.id]) == 3
        finally:
            ack.set()
            state.strategy._is_stop_triggered = True
            account_task.cancel()
            for executor in state.orchestrator.active_executors[state.controller.config.id]:
                executor.stop()
            pending = asyncio.all_tasks() - baseline
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            await native._web_assistants_factory.close()
