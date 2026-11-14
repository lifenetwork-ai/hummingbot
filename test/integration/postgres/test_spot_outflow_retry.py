"""Actual native failure events must not spend eleven local IDs on one cap denial."""
import asyncio
import logging
import time
from decimal import Decimal
from test.integration.postgres.test_ownership import assistant, owned as owned_fixture
from test.integration.postgres.test_spot_outflow import setup
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bidict import bidict

from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee
from hummingbot.model.order import Order
from hummingbot.model.runtime_journal import SubmissionAttempt
from hummingbot.strategy_v2.executors.position_executor.data_types import PositionExecutorConfig, TripleBarrierConfig
from hummingbot.strategy_v2.executors.position_executor.position_executor import PositionExecutor
from hummingbot.strategy_v2.models.base import RunnableStatus

owned = owned_fixture


@pytest.mark.parametrize('scoped', [False, True])
def test_native_outflow_denial_does_not_loop_through_retry_budget_or_send(owned, caplog, scoped):
    owned.owner.scoped_recovery_enabled = scoped
    exchange = OkxExchange(okx_api_key='offline', okx_secret_key='offline', okx_passphrase='offline',
                           trading_pairs=['LIFE-USDT'])
    exchange._set_current_timestamp(time.time())
    exchange._set_trading_pair_symbol_map(bidict({'LIFE-USDT': 'LIFE-USDT'}))
    exchange._trading_rules['LIFE-USDT'] = TradingRule('LIFE-USDT', min_order_size=Decimal('.01'),
                                                       min_price_increment=Decimal('.01'),
                                                       min_base_amount_increment=Decimal('.01'))
    exchange.get_fee = MagicMock(return_value=AddedToCostTradeFee(percent=Decimal('.001')))
    recorder = MarketsRecorder(owned.manager, [exchange], 'test.yml', 'pure_market_making',
                               owned.recorder._market_data_collection_config)
    recorder.start()
    context = SimpleNamespace(**{**vars(owned), 'market': exchange, 'recorder': recorder})
    owned.owner.connectors = {'okx': exchange}
    session, _ = setup(context, max_net_outflow_quote='1')
    config = PositionExecutorConfig(id='queued-entry', controller_id='maker', connector_name='okx',
                                    trading_pair='LIFE-USDT', side=TradeType.BUY, amount=Decimal('5'),
                                    entry_price=Decimal('10'), timestamp=time.time(),
                                    mm_session_id=session.id, mm_generation=session.generation,
                                    use_configured_entry_price=True,
                                    triple_barrier_config=TripleBarrierConfig(open_order_type=OrderType.LIMIT_MAKER))
    strategy = MagicMock()
    strategy.connectors = {'okx': exchange}
    strategy.current_timestamp = time.time()
    identities = []

    def buy(connector, pair, amount, order_type, price, position_action):
        identity = exchange.buy(pair, amount, order_type, price)
        identities.append(identity)
        return identity

    strategy.buy = buy
    executor = PositionExecutor(strategy, config)
    executor._status = RunnableStatus.RUNNING
    executor._entry_quote_prepared = True  # Already queued before the authoritative gate.
    executor.register_events()
    rest, transport = assistant()
    rest._auth = exchange.authenticator
    caplog.set_level(logging.WARNING)

    async def run():
        with patch.object(exchange._web_assistants_factory, 'get_rest_assistant', AsyncMock(return_value=rest)):
            for _ in range(12):
                if executor.status != RunnableStatus.TERMINATED:
                    await executor.control_task()
                await asyncio.sleep(.01)  # Drain real native OrderFailure callbacks.
    try:
        owned.loop.run_until_complete(run())
        assert len(identities) == 1
        assert executor.status == RunnableStatus.TERMINATED and executor._current_retries == 0
        assert transport.call.await_count == 0
        assert not owned.store.read()['recovery_state'].get('halt')
        assert not owned.owner.sent_intents
        assert owned.owner.spot_outflow.status()['reserved_quote'] == 0
        with owned.manager.get_new_session() as db:
            assert db.query(Order).count() == db.query(SubmissionAttempt).count() == 0
        records = [r for r in caplog.records if getattr(r, 'event', None) == 'order_submission_blocked']
        assert len(records) == 1 and records[0].levelno == logging.WARNING
        assert records[0].error_type == 'SpotOutflowBlocked' and records[0].reason_code == 'spot_outflow_limit'
    finally:
        executor.unregister_events()
        recorder.stop()
        owned.loop.run_until_complete(exchange._web_assistants_factory.close())
