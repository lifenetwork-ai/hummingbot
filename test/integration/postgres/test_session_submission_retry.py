"""A real shutdown fence between quote preparation and transport cannot spin IDs."""
import asyncio
import logging
import time
from contextlib import asynccontextmanager
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


@pytest.mark.parametrize('stage', ['before_intent', 'after_throttle'])
def test_native_shutdown_fence_retires_one_queued_entry_without_send_or_retry(owned, caplog, stage):
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
    session, _ = setup(context)
    strategy = MagicMock()
    strategy.connectors = {'okx': exchange}
    strategy.current_timestamp = time.time()
    identities = []

    def buy(connector, pair, amount, order_type, price, position_action):
        identity = exchange.buy(pair, amount, order_type, price)
        identities.append(identity)
        return identity

    def shutdown_fence():
        owned.owner.allow_new_orders = False
        owned.owner._mutate_recovery(lambda state: state.update(
            halt=True, halt_kind='shutdown_pending', shutdown_incomplete=True))

    class ShutdownThrottle:
        @asynccontextmanager
        async def execute_task(self, limit_id):
            shutdown_fence()
            yield

    strategy.buy = buy
    config = PositionExecutorConfig(id='queued-entry', controller_id='maker', connector_name='okx',
                                    trading_pair='LIFE-USDT', side=TradeType.BUY, amount=Decimal('5'),
                                    entry_price=Decimal('10'), timestamp=time.time(),
                                    mm_session_id=session.id, mm_generation=session.generation,
                                    use_configured_entry_price=True,
                                    triple_barrier_config=TripleBarrierConfig(open_order_type=OrderType.LIMIT_MAKER))
    executor = PositionExecutor(strategy, config)
    executor._status = RunnableStatus.RUNNING
    executor._entry_quote_prepared = True
    executor.register_events()
    rest, transport = assistant(ShutdownThrottle() if stage == 'after_throttle' else None)
    rest._auth = exchange.authenticator
    caplog.set_level(logging.INFO)

    async def run():
        with patch.object(exchange._web_assistants_factory, 'get_rest_assistant', AsyncMock(return_value=rest)):
            for _ in range(12):
                if not executor.is_closed:
                    await executor.control_task()
                await asyncio.sleep(.01)  # Drain native failure events, not a mocked executor callback.

    try:
        if stage == 'before_intent':
            shutdown_fence()
        owned.loop.run_until_complete(run())
        assert len(identities) == 1
        assert executor.is_closed and executor._current_retries == 0
        transport.call.assert_not_awaited()
        assert not owned.owner.active_intents and not owned.owner.sent_intents
        assert owned.owner.spot_outflow.status()['reserved_quote'] == 0
        recovery = owned.store.read()['recovery_state']
        assert recovery['halt_kind'] == 'shutdown_pending' and recovery['halt']
        assert not recovery.get('reason') and not recovery.get('pending_mutations')
        with owned.manager.get_new_session() as db:
            assert db.query(Order).count() == db.query(SubmissionAttempt).count() == (stage == 'after_throttle')
            assert not db.query(Order).filter(Order.creation_pending.is_(True)).count()
            for attempt in db.query(SubmissionAttempt):
                # Native OrderFailure advances an unsent rejection to terminal.
                assert attempt.state == 'terminal'
                assert attempt.payload_hash is None and attempt.deadline_ms is None and attempt.receipt is None
        records = [row for row in caplog.records if getattr(row, 'event', None) == 'order_submission_blocked']
        assert len(records) == 2 and all(row.levelno == logging.INFO for row in records)
        assert all(row.error_type == 'SessionSubmissionBlocked' for row in records)
        assert 'Retry count' not in caplog.text
    finally:
        executor.unregister_events()
        recorder.stop()
        owned.loop.run_until_complete(exchange._web_assistants_factory.close())
