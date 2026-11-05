from decimal import Decimal
from test.integration.postgres.test_recorder import fill, recording as recording_fixture

import pytest

from hummingbot.core.data_type.common import OrderType, PositionAction, TradeType
from hummingbot.core.event.events import MarketEvent
from hummingbot.model.bot_state import BotState
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.order import Order
from hummingbot.model.position import Position
from hummingbot.model.trade_fill import TradeFill

recording = recording_fixture


def prepare(recording):
    recorder, manager, market = recording
    BotStateStore(manager.engine, "hummingbot").ensure()
    market.name = market.display_name = "okx_perpetual"
    with manager.get_new_session() as session, session.begin():
        session.query(BotState).update({"recovery_state": {"credential_operation": {"id": "disconnect", "action": "credentials_disconnect"}}})
        session.add(Position(id="long-hold", controller_id="controller", connector_name="okx_perpetual", side="BUY",
                             trading_pair="BTC-USDT", timestamp=1, volume_traded_quote=27, amount=Decimal("0.3"), breakeven_price=90,
                             realized_pnl_quote=0, unrealized_pnl_quote=0, cum_fees_quote=0))
        session.add(Order(id="order-1", config_file_path="test.yml", strategy="pure_market_making", market="okx_perpetual",
                          symbol="BTC-USDT", base_asset="BTC", quote_asset="USDT", creation_timestamp=1, order_type="MARKET", amount=Decimal("0.3"),
                          leverage=2, price=0, last_status="PendingSell", last_update_timestamp=1, position="CLOSE", creation_pending=True,
                          cleanup_operation_id="disconnect", cleanup_scope=dict(position_id="long-hold", position_side="LONG", credential_version=1)))
    return recorder, manager, market


def test_close_fill_and_position_checkpoint_commit_atomically_and_deduplicate_after_restart(recording):
    recorder, manager, market = prepare(recording)
    event = fill(trade_type=TradeType.SELL, order_type=OrderType.MARKET, amount=Decimal("0.1"), price=Decimal("100"), position=PositionAction.CLOSE.value)
    recorder._did_fill_order(MarketEvent.OrderFilled.value, market, event)
    with manager.get_new_session() as session:
        position = session.get(Position, "long-hold")
        assert position.amount == Decimal("0.2") and position.realized_pnl_quote == 1
    recorder._did_fill_order(MarketEvent.OrderFilled.value, market, event)
    recorder._did_fill_order(MarketEvent.OrderFilled.value, market, fill(trade_type=TradeType.SELL, order_type=OrderType.MARKET,
                                                                         amount=Decimal("0.2"), price=Decimal("100"), position=PositionAction.CLOSE.value, exchange_trade_id="fill-2"))
    with manager.get_new_session() as session:
        position = session.get(Position, "long-hold")
        assert position.amount == 0 and position.realized_pnl_quote == 3
        assert position.cum_fees_quote == Decimal("0.4") and position.volume_traded_quote == 57
        assert session.query(TradeFill).count() == 2


@pytest.mark.parametrize("trade_type,amount", [(TradeType.BUY, Decimal("0.1")), (TradeType.SELL, Decimal("0.4"))])
def test_unexpected_close_direction_or_overfill_cannot_reverse_a_saved_position(recording, trade_type, amount):
    recorder, manager, market = prepare(recording)
    with pytest.raises(ValueError):
        recorder._did_fill_order(MarketEvent.OrderFilled.value, market, fill(trade_type=trade_type, order_type=OrderType.MARKET,
                                                                             amount=amount, price=Decimal("100"), position=PositionAction.CLOSE.value))
    with manager.get_new_session() as session:
        assert session.query(TradeFill).count() == 0
        assert session.get(Position, "long-hold").amount == Decimal("0.3")


def test_distinct_trade_ids_cannot_exceed_the_admitted_close_size(recording):
    recorder, manager, market = prepare(recording)
    with manager.get_new_session() as session, session.begin():
        session.get(Order, 'order-1').amount = Decimal('0.1')
    recorder._did_fill_order(MarketEvent.OrderFilled.value, market, fill(trade_type=TradeType.SELL, order_type=OrderType.MARKET,
                                                                         amount=Decimal('0.1'), price=Decimal('100'), position=PositionAction.CLOSE.value))
    with pytest.raises(ValueError):
        recorder._did_fill_order(MarketEvent.OrderFilled.value, market, fill(trade_type=TradeType.SELL, order_type=OrderType.MARKET,
                                                                             amount=Decimal('0.1'), price=Decimal('100'), position=PositionAction.CLOSE.value, exchange_trade_id='unapproved-extra'))
    with manager.get_new_session() as session:
        assert session.query(TradeFill).count() == 1
        assert session.get(Position, 'long-hold').amount == Decimal('0.2')
