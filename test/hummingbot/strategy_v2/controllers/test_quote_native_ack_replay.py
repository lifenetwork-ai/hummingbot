"""Native proposal -> executor -> OKX REST -> ACK after a committed fill handoff.

Only external book/account/transport observations and recorder persistence are
faked. Long periodic waits isolate event progress; they are not latency SLOs.
"""
import asyncio
import json
import logging
from decimal import Decimal
from test.hummingbot.strategy_v2.controllers.test_filled_entry_handoff import account_observed
from test.hummingbot.strategy_v2.controllers.test_inventory_cap_lifecycle import seed_lifecycle
from test.hummingbot.strategy_v2.controllers.test_inventory_skew_percent import btc_edge_controller
from test.hummingbot.strategy_v2.controllers.test_quote_progress_replay import complete, eventually, replay
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from bidict import bidict

from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core import engine_diagnostics, order_submission_guard as gates
from hummingbot.core.data_type.common import PositionAction, TradeType
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee
from hummingbot.core.event.event_forwarder import EventForwarder
from hummingbot.core.event.events import MarketEvent
from hummingbot.core.mm_liquidity import LiquidityObserver, own_quotes, rolling_coverage
from hummingbot.core.mm_quote_activity import quote_activity
from hummingbot.core.web_assistant.rest_assistant import RESTAssistant
from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator


@pytest.mark.asyncio
@pytest.mark.parametrize('side,pair', [
    (TradeType.BUY, 'LIFE-USDT'), (TradeType.SELL, 'LIFE-USDT'), (TradeType.SELL, 'BTC-USDT')])
@pytest.mark.parametrize('account_first', [False, True])
async def test_native_replacement_waits_for_account_and_commit_then_ack_without_duplicate_send(
        monkeypatch, side, pair, account_first, record_property):
    monkeypatch.setattr(gates, 'postgres_requested', lambda: False)
    state, peers = None, None
    btc_edge = pair == 'BTC-USDT'
    if btc_edge:
        controller, _ = btc_edge_controller('.4806', runtime_config_managed=True,
                                            mm_session_id='offline-replay', mm_generation=1)
        controller.admit_session('offline-replay', 1)
        configs = [action.executor_config for action in controller.create_actions_proposal()]
        sell, = [config for config in configs if config.side == TradeType.SELL]
        assert sell.amount == Decimal('.00002')
        peers = [config for config in configs if config.side == TradeType.BUY]
        assert len(peers) == 2
        state = seed_lifecycle(monkeypatch, controller, sell)
    async with replay(monkeypatch, side, state=state, peers=peers) as state:
        clock = asyncio.get_running_loop().time
        timings = {}
        diagnostics = engine_diagnostics.EngineDiagnostics()
        monkeypatch.setattr(engine_diagnostics, '_ACTIVE', diagnostics)
        baseline = asyncio.all_tasks()
        state.strategy.logger.return_value = logging.getLogger(__name__)
        native = OkxExchange(okx_api_key='offline', okx_secret_key='offline', okx_passphrase='offline',
                             trading_pairs=[pair])
        native._set_current_timestamp(1003)
        native._set_trading_pair_symbol_map(bidict({pair: pair}))
        if btc_edge:
            native._trading_rules[pair] = state.connector.trading_rules[pair]
            native.get_fee = state.connector.get_fee
            price = Decimal('63816.75')
            original_cash = {asset: cell['cash_balance']
                             for asset, cell in state.connector.account_balance_observations.items()}
            native.account_balance_observations = state.connector.account_balance_observations
        else:
            native._trading_rules[pair] = TradingRule(pair, min_order_size=Decimal('.01'), min_price_increment=Decimal('.0001'),
                                                      min_base_amount_increment=Decimal('.01'))
            native.get_fee = MagicMock(return_value=AddedToCostTradeFee(percent=Decimal('.001')))
            price = Decimal('.1')
        native.get_price_by_type = MagicMock(return_value=price)
        native.get_price = MagicMock(return_value=price)
        native.get_balance = MagicMock(side_effect=state.connector.get_balance.side_effect)
        native.get_available_balance = MagicMock(side_effect=state.connector.get_available_balance.side_effect)
        native._user_stream_tracker = SimpleNamespace(last_recv_time=1003)
        native.account_positions = {}  # Shared external-account fixture also handles hedge connectors.
        for executor in state.orchestrator.active_executors[state.controller.config.id]:
            native._order_tracker.start_tracking_order(executor._open_order.order)
            executor.connectors = {'okx': native}
        state.connector = native
        state.strategy.connectors = {'okx': native}
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

        def observe_cash():
            if btc_edge:
                cash = {**original_cash, 'BTC': original_cash['BTC'] - state.order.amount,
                        'USDT': original_cash['USDT'] + state.order.amount * state.order.price}
                native.account_balance_observations = {
                    asset: dict(cash_balance=amount, observed_at=1002) for asset, amount in cash.items()}
                native.get_balance.side_effect = native.get_available_balance.side_effect = cash.__getitem__
            else:
                account_observed(state, side, initial=(2000, 0))
            timings['cash_ready'] = clock()

        def order_created(event):
            if event.exchange_order_id == 'replacement-venue':
                timings['open'] = clock()

        listener = EventForwarder(order_created)
        native.add_listener(MarketEvent.BuyOrderCreated if side == TradeType.BUY else MarketEvent.SellOrderCreated, listener)

        def checkpoint_committed(*args, **kwargs):
            timings['checkpoint'] = clock()

        state.recorder.checkpoint_held_positions.side_effect = checkpoint_committed

        async def transport(request):
            assert request.url.endswith('/api/v5/trade/order')
            requests.append(json.loads(request.data))
            timings['send'] = clock()
            entered.set()
            await ack.wait()
            timings['response'] = clock()
            response = MagicMock(status=200)
            response.json = AsyncMock(return_value={
                'code': '0', 'data': [dict(ordId='replacement-venue', sCode='0')]})
            return response

        from test.integration.postgres.test_ownership import Throttler
        rest = RESTAssistant(SimpleNamespace(call=transport), Throttler(), auth=native.authenticator)
        monkeypatch.setattr(native._web_assistants_factory, 'get_rest_assistant', AsyncMock(return_value=rest))
        account_task = asyncio.create_task(state.strategy._watch_spot_account_progress(native, state.account_event))
        try:
            starting_quotes = own_quotes(native, pair, max_orders=16)
            if account_first:
                observe_cash()
            timings['fill'] = clock()
            complete(state)
            if not account_first:
                await asyncio.sleep(.08)
                assert not requests  # No account coverage means no capacity release/send.
                before = own_quotes(native, pair, max_orders=16)
                assert not before['two_sided'] and not before['pending_orders'][side.name]['submission_pending']
                observation = dict(valid=True, eligible=True, proposal=state.controller.quote_diagnostics, **before)
                activity = quote_activity(observation, now=1003, previous=None, delta=0, continuous=False,
                                          monitoring=True, warning_seconds=20)
                assert activity['sides'][side.name]['diagnosis']['stage'] == 'executor_activity'
                observe_cash()
                state.account_event.set()
            try:
                await asyncio.wait_for(entered.wait(), 1)
            except asyncio.TimeoutError:
                pytest.fail(f'Replacement did not reach REST: {state.controller.quote_diagnostics!r}, '
                            f'report_error={state.strategy.controller_reports_error!r}')
            state.recorder.checkpoint_held_positions.assert_called_once()
            holds = state.orchestrator.positions_held[state.controller.config.id]
            assert len(holds) == 1 and abs(holds[0].net_amount_base) == state.order.amount
            pending = own_quotes(native, pair, max_orders=16)
            assert not pending['two_sided'] and pending['pending_orders'][side.name]['submission_pending'] == 1
            activity = quote_activity(dict(valid=True, eligible=True, proposal=state.controller.quote_diagnostics, **pending),
                                      now=1003, previous=None, delta=0, continuous=False, monitoring=True, warning_seconds=20)
            assert activity['sides'][side.name]['diagnosis']['stage'] == 'submission_pending'
            await asyncio.sleep(.08)
            assert len(requests) == 1  # Pending transport/ACK cannot free the same level again.
            ack.set()
            await eventually(lambda: own_quotes(native, pair, max_orders=16)['two_sided'])
            await asyncio.sleep(.08)
            assert len(requests) == 1
            assert requests[0]['side'] == side.name.lower()
            assert len(state.orchestrator.active_executors[state.controller.config.id]) == (4 if btc_edge else 3)
            ready = max(timings['fill'], timings['cash_ready'])
            assert ready <= timings['checkpoint'] <= timings['send'] <= timings['response'] <= timings['open']
            for name, start, end in (
                ('fill_to_send_ms', timings['fill'], timings['send']),
                ('fill_and_cash_ready_to_send_ms', ready, timings['send']),
                ('fill_and_cash_ready_to_checkpoint_ms', ready, timings['checkpoint']),
                ('checkpoint_to_send_ms', timings['checkpoint'], timings['send']),
                ('send_to_mock_response_ms', timings['send'], timings['response']),
                ('mock_response_to_open_ms', timings['response'], timings['open']),
                ('fill_to_open_ms', timings['fill'], timings['open']),
            ):
                record_property(name, round((end - start) * 1000, 3))
            if btc_edge:
                assert requests[0]['instId'] == pair
                assert Decimal(requests[0]['sz']) == Decimal('.00002')
                markers = [row for row in diagnostics.snapshot()['submission_recent'] if row['operation'].startswith('quote.')]
                assert [row['operation'] for row in markers] == [
                    'quote.sell_fill', 'quote.sell_send', 'quote.sell_response', 'quote.sell_open']
                fill, send, response, opened = markers
                assert len({row['lane'] for row in markers}) == 1
                assert fill['order'] != send['order'] == response['order'] == opened['order']
                assert send['replaces'] == opened['replaces'] == fill['order']
                assert fill['at_ns'] <= send['at_ns'] <= response['at_ns'] <= opened['at_ns']
                assert opened['fill_at_ns'] == fill['at_ns']
                assert opened['sent_at_ns'] == send['at_ns']
                assert opened['http_response_at_ns'] == response['at_ns']
                assert requests[0]['clOrdId'] not in json.dumps(markers)
                ending_quotes = own_quotes(native, pair, max_orders=16)
                observer = LiquidityObserver()
                session = dict(session_id='offline-replay', generation=1, profile_id='demo', started_at=1002, status='running')
                # A one-second sample can bracket both the fill and the replacement ACK.
                for at, quotes in ((1002, starting_quotes), (1003, ending_quotes)):
                    assert quotes['two_sided']
                    market = dict(**quotes, valid=True, eligible=True, slo_scope_valid=True, session_admitted=True,
                                  connector='okx', pair=pair, proposal=dict(observed_at=at, reasons={'BUY': [], 'SELL': []}))
                    frame = observer.observe(session, {'spot': market}, now=at, monotonic_now=at, owner_generation=1)
                coverage = rolling_coverage(frame, 'spot', now=1003)
                assert coverage['eligible_seconds'] == 1
                assert coverage['two_sided_eligible_seconds'] == coverage['eligibility_unknown_seconds'] == 0
                record_property('one_second_sample_uncredited_ms', 1000)
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
