"""Native OKX contract units, lost ACK, new owner and exactly-once closing PnL."""
import asyncio
from decimal import Decimal
from test.integration.postgres.test_ownership import assistant, owned as _owned
from test.integration.postgres.test_reduction_cost_checkpoint import runtime, seeded
from test.integration.postgres.test_reduction_transport import prepare
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from bidict import bidict

from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
from hummingbot.connector.derivative.position import Position as VenuePosition
from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.core.data_type.common import OrderType, PositionAction, PositionMode, PositionSide, TradeType
from hummingbot.core.order_submission_guard import install_guard, session_order_scope
from hummingbot.model.order import Order
from hummingbot.model.runtime_journal import RuntimeJournal, SubmissionAttempt
from hummingbot.model.trade_fill import TradeFill
from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executors import TrackedOrder

owned = _owned


@pytest.mark.parametrize('leg', [TradeType.BUY, TradeType.SELL])
def test_native_close_lost_ack_cold_recovery_reduces_held_leg_and_applies_original_fees_once(owned, leg):
    seeded(owned, leg)
    # The seed represents an already acknowledged and applied opening baseline.
    with owned.manager.get_new_session() as session, session.begin():
        opened = session.get(Order, 'opening-order')
        opened.creation_pending, opened.exchange_order_id = False, 'opening-venue'
    current = runtime()
    host, controller, _, _, _, _ = prepare(owned)
    exchange = OkxPerpetualDerivative(okx_perpetual_api_key='offline-test-only', okx_perpetual_secret_key='offline',
                                      okx_perpetual_passphrase='offline', trading_pairs=['BTC-USDT'])
    exchange._set_current_timestamp(1700000000)
    exchange._set_trading_pair_symbol_map(bidict({'BTC-USDT-SWAP': 'BTC-USDT'}))
    exchange._contract_sizes['BTC-USDT'] = Decimal('.01')
    exchange._trading_rules['BTC-USDT'] = TradingRule('BTC-USDT', min_order_size=Decimal('.01'),
                                                      min_base_amount_increment=Decimal('.01'), min_price_increment=Decimal('.01'),
                                                      buy_order_collateral_token='USDT', sell_order_collateral_token='USDT')
    exchange._perpetual_trading.set_position_mode(PositionMode.HEDGE)
    venue_side = PositionSide.LONG if leg == TradeType.BUY else PositionSide.SHORT
    direction = 1 if leg == TradeType.BUY else -1
    exchange._perpetual_trading.set_position(exchange._perpetual_trading.position_key('BTC-USDT', venue_side),
                                             VenuePosition('BTC-USDT', venue_side, Decimal(0), Decimal(100), Decimal(2 * direction), Decimal(1)))
    exchange._account_available_balances['USDT'] = Decimal('1000')
    exchange.position_snapshot_times['BTC-USDT'] = 1000
    key = exchange._perpetual_trading.position_key('BTC-USDT', venue_side)
    exchange.position_observations[key] = dict(observed_at=1000, margin_mode='cross')
    controller.market_data_provider.get_connector_with_fallback.return_value = exchange
    current.strategy.controllers = {'maker': controller}
    current.strategy.connectors = {'okx_perpetual': exchange}
    controller.bind_reduction_runtime(lambda: current.reduction_runtime('maker'))
    host.owner = owned.owner
    owned.owner.connectors = {'okx_perpetual': exchange}
    owned.owner.mm_ready_check, owned.owner.mm_order_ready_check = host.mm_runtime_ready, host.mm_runtime_order_ready
    owned.owner.mm_quote_check = host.mm_quote_valid
    recorder = MarketsRecorder(owned.manager, [exchange], 'test.yml', 'pure_market_making',
                               owned.recorder._market_data_collection_config)
    recorder.start()
    successor = None
    try:
        config = controller.get_executor_config('sell_0' if leg == TradeType.BUY else 'buy_0',
                                                Decimal('101' if leg == TradeType.BUY else '99'), Decimal('.1'))
        assert config.position_action == PositionAction.CLOSE
        executor = OrderExecutor(current.strategy, config)
        executor._status = RunnableStatus.RUNNING
        executor._order = TrackedOrder('native-close')
        current.active_executors['maker'] = [executor]
        rest, transport = assistant()
        rest._auth = exchange.authenticator
        response = transport.call.return_value
        response.status = 429
        response.text = AsyncMock(return_value='{"code":"50013","msg":"busy"}')
        response.json = AsyncMock(return_value={'code': '50013', 'data': []})

        async def create():
            with session_order_scope('session', 0, level_id=config.level_id, executor=config.model_dump(mode='json')):
                await exchange._create_order(config.side, 'native-close', config.trading_pair, config.amount,
                                             OrderType.LIMIT_MAKER, config.price, position_action=PositionAction.CLOSE)
            await asyncio.sleep(0)

        with patch.object(exchange._web_assistants_factory, 'get_rest_assistant', AsyncMock(return_value=rest)):
            owned.loop.run_until_complete(create())
        assert transport.call.await_count == 1
        assert owned.owner.automatic_recovery.active
        assert not owned.store.read()['recovery_state'].get('halt')
        with owned.manager.get_new_session() as session:
            attempt = session.get(SubmissionAttempt, 'native-close')
            assert attempt.context['order']['position'] == 'CLOSE'
        owned.owner.release()
        successor = BotOwnership(owned.engine, 'hummingbot')
        assert successor.try_acquire()
        install_guard(successor)
        successor.prepare(1, 'test.yml')
        successor.connectors = {'okx_perpetual': exchange}
        restarted = runtime()
        core = SimpleNamespace(markets_recorder=recorder, trade_fill_db=owned.manager,
                               strategy=SimpleNamespace(executor_orchestrator=restarted))
        size = str(exchange._format_amount_to_size('BTC-USDT', config.amount))
        fee = config.amount * config.price * Decimal('.001')
        status = dict(clOrdId='native-close', ordId='close-venue', instId='BTC-USDT-SWAP', side=config.side.name.lower(), posSide=venue_side.name.lower(),
                      ordType='post_only', px=str(config.price), sz=size, accFillSz=size, uTime='1700000001000', state='filled')
        trade = dict(tradeId='closing-trade', ordId='close-venue', instId='BTC-USDT-SWAP', side=config.side.name.lower(), posSide=venue_side.name.lower(),
                     fillSz=size, fillPx=str(config.price), fee=str(-fee), feeCcy='USDT', ts='1700000001000')
        with patch.object(exchange, '_request_order_update', AsyncMock(return_value={'code': '0', 'data': [status]})), \
                patch.object(exchange, '_request_order_fills', AsyncMock(return_value={'code': '0', 'data': [trade]})):
            owned.loop.run_until_complete(successor.automatic_recovery.restore(core))
            owned.loop.run_until_complete(successor.automatic_recovery.project(core))
            owned.loop.run_until_complete(successor.automatic_recovery.project(core))
        held = recorder.get_all_positions()
        assert len(held) == 1 and held[0].side == leg.name
        assert held[0].amount == Decimal(2) - config.amount
        assert held[0].cum_fees_quote == Decimal(2) + fee
        assert held[0].realized_pnl_quote == (config.price - Decimal(100)) * config.amount * direction
        basis = recorder.get_reduction_bases()[('maker', 'okx_perpetual', 'BTC-USDT', leg.name)]
        assert basis.amount == Decimal(2) - config.amount and basis.opening_fees_quote == basis.amount
        with owned.manager.get_new_session() as session:
            assert session.query(TradeFill).filter_by(order_id='native-close').count() == 1
            assert session.query(RuntimeJournal).filter_by(kind='fill', applied=False).count() == 0
            assert session.query(RuntimeJournal).filter_by(event_key='hold:' + config.id).count() == 1
        assert transport.call.await_count == 1
    finally:
        if successor is not None:
            successor.release()
        install_guard(owned.owner)
        recorder.stop()
        owned.loop.run_until_complete(exchange._web_assistants_factory.close())
