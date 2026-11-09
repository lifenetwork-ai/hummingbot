"""Real SIGKILL separates a durable fill ledger from a position-hold checkpoint."""
import os
import subprocess
import sys
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.core.data_type.common import PositionSide
from hummingbot.core.order_submission_guard import SubmissionBlocked
from hummingbot.model.position import Position
from hummingbot.model.trade_fill import TradeFill
from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator

CHILD = r'''
import asyncio, os, threading
from decimal import Decimal
from unittest.mock import MagicMock, patch
from hummingbot.client.config.client_config_map import MarketDataCollectionConfigMap
from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.core.data_type.common import TradeType, OrderType, PositionAction
from hummingbot.core.event.events import BuyOrderCreatedEvent, SellOrderCreatedEvent, MarketEvent
from hummingbot.core.order_submission_guard import install_guard
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.connection import create_postgres_engine
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres
from hummingbot.model.position import Position
from hummingbot.client.config.runtime_config import RuntimeSettings
from hummingbot.model.sql_connection_manager import SQLConnectionManager, SQLConnectionType
from hummingbot.strategy_v2.executors.executor_orchestrator import PositionHold
from types import SimpleNamespace
from test.integration.postgres.test_recorder import fill

def held(side, amount, fee='0'):
    hold=PositionHold('okx_perpetual','BTC-USDT',TradeType[side])
    hold.add_orders_from_executor(SimpleNamespace(
        id='executor-'+side,config=SimpleNamespace(side=TradeType[side]),
        custom_info={'held_position_orders':[{
            'client_order_id':'order-'+side,'trade_type':side,
            'executed_amount_base':amount,
            'executed_amount_quote':Decimal(amount)*Decimal('78686.5'),
            'cumulative_fee_paid_quote':fee}]}))
    summary=hold.get_position_summary(Decimal('78686.5'))
    return Position(id=side,controller_id='maker',timestamp=1000,
                    **{**summary.model_dump(),'side':side})

engine=create_postgres_engine(RuntimeSettings.from_environment({'DATABASE_URL':os.environ['HBOT_TEST_POSTGRES_URL']}))
bootstrap_postgres(engine,auto_migrate=True)
store=BotStateStore(engine,'hummingbot');store.ensure()
store.control(expected_version=0,desired_state='running',config_file_path='test.yml',config_type='v1-strategy')
owner=BotOwnership(engine,'hummingbot');assert owner.try_acquire();owner.prepare(1,'test.yml')
owner.allow_new_orders=True;owner.reconciling=False
os.environ['DATABASE_URL']=os.environ['HBOT_TEST_POSTGRES_URL'];os.environ['K_SERVICE']='offline-test'
loop=asyncio.new_event_loop();asyncio.set_event_loop(loop)
with patch.object(SQLConnectionManager,'_postgres_engine',engine):
    manager=SQLConnectionManager(None,SQLConnectionType.TRADE_FILLS)
market=MagicMock();market.display_name=market.name='okx_perpetual';market.tracking_states={}
recorder=MarketsRecorder(manager,[market],'test.yml','pure_market_making',MarketDataCollectionConfigMap(market_data_collection_enabled=False))
owner.connectors={'okx_perpetual':market};install_guard(owner)
recorder.update_or_store_positions([held('BUY','0'),held('SELL','0')])
memory_holds=[]
for side,amount,fee in [('BUY','.0084','.0047181294'),('SELL','.0027','.00786827')]:
    identity='order-'+side
    cls=BuyOrderCreatedEvent if side=='BUY' else SellOrderCreatedEvent
    event=cls(timestamp=1700000000,creation_timestamp=1700000000,type=OrderType.LIMIT_MAKER,
              trading_pair='BTC-USDT',amount=Decimal(amount),price=Decimal('78686.5'),
              order_id=identity,exchange_order_id='venue-'+side)
    recorder._did_create_order((MarketEvent.BuyOrderCreated if side=='BUY' else MarketEvent.SellOrderCreated).value,market,event)
    from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount
    event=fill(order_id=identity,trade_type=TradeType[side],order_type=OrderType.LIMIT_MAKER,
               amount=Decimal(amount),price=Decimal('78686.5'),position=PositionAction.OPEN.value,
               exchange_trade_id='fill-'+side,trade_fee=AddedToCostTradeFee(flat_fees=[TokenAmount('USDT',Decimal(fee))]))
    recorder._did_fill_order(MarketEvent.OrderFilled.value,market,event)
    memory_holds.append(held(side,amount,fee))
if os.environ['CHECKPOINT_BEFORE_KILL']=='1':
    recorder.update_or_store_positions(memory_holds)
print('FILLS_COMMITTED',flush=True)
threading.Event().wait()
'''


@pytest.mark.parametrize('checkpointed', [False, True])
def test_sigkill_preserves_fills_and_startup_requires_matching_durable_positions(pg_engine, checkpointed):
    env = {**os.environ, 'HBOT_TEST_POSTGRES_URL': pg_engine.url.render_as_string(hide_password=False),
           'CHECKPOINT_BEFORE_KILL': '1' if checkpointed else '0'}
    # Only the disposable test DSN is available to the subprocess.
    for key in list(env):
        if key in ('DATABASE_URL', 'ENV_FILE', 'HBOT_ENV_FILE', 'K_SERVICE', 'STORAGE_BACKEND') or key.startswith('HBOT_OKX'):
            env.pop(key)
    process = subprocess.Popen([sys.executable, '-c', CHILD], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'FILLS_COMMITTED'
        process.kill()
        assert process.wait(timeout=10) == -9
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)
    with Session(pg_engine) as session:
        fills = session.scalars(select(TradeFill).order_by(TradeFill.exchange_trade_id)).all()
        before = [(f.exchange_trade_id, f.amount, f.price, f.trade_fee_in_quote) for f in fills]
        assert before == [('fill-BUY', Decimal('.0084'), Decimal('78686.5'), Decimal('.0047181294')),
                          ('fill-SELL', Decimal('.0027'), Decimal('78686.5'), Decimal('.00786827'))]
        positions = session.scalars(select(Position)).all()
        restored = object.__new__(ExecutorOrchestrator)
        restored.positions_held = {'maker': []}
        restored.initialize_initial_positions = lambda: None
        for row in positions:
            restored._load_position_from_db('maker', row)
        if checkpointed:
            assert sum((p.cum_fees_quote for p in positions), Decimal(0)) == Decimal('.0125863994')
            assert sum((p.volume_traded_quote for p in positions), Decimal(0)) == Decimal('.0111') * Decimal('78686.5')
        connector = SimpleNamespace(name='okx_perpetual', account_positions={
            'long': SimpleNamespace(trading_pair='BTC-USDT', position_side=PositionSide.LONG, amount=Decimal('.0084')),
            'short': SimpleNamespace(trading_pair='BTC-USDT', position_side=PositionSide.SHORT, amount=Decimal('-.0027'))})
        core = SimpleNamespace(strategy=SimpleNamespace(executor_orchestrator=restored))
        if checkpointed:
            BotOwnership._verify_positions(core, connector)
        else:
            with pytest.raises(SubmissionBlocked, match='positions do not match'):
                BotOwnership._verify_positions(core, connector)
            assert all(p.amount == 0 for p in positions)
        assert session.query(TradeFill).count() == 2
