from test.integration.postgres.test_recorder import create, fill, recording as recording_fixture

import pytest
from sqlalchemy import update

from hummingbot.core.event.events import MarketEvent
from hummingbot.model.bot_state import BotState
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.order import Order
from hummingbot.model.trade_fill import TradeFill

recording = recording_fixture


def test_cleanup_records_recovered_order_effects_without_relabeling_its_original_strategy(recording):
    recorder, manager, market = recording
    recorder._did_create_order(MarketEvent.BuyOrderCreated.value, market, create())
    BotStateStore(manager.engine, "hummingbot").ensure()
    with manager.engine.begin() as connection:
        connection.execute(update(BotState).values(recovery_state={"credential_operation": {
            "id": "disconnect", "action": "credentials_disconnect"}}))
    recorder._config_file_path = "credentials_cleanup"
    recorder.cleanup_operation_id = "disconnect"
    recorder._did_fill_order(MarketEvent.OrderFilled.value, market, fill())
    with manager.get_new_session() as session:
        assert session.get(Order, "order-1").config_file_path == "test.yml"
        saved = session.query(TradeFill).one()
        assert saved.config_file_path == "test.yml" and saved.strategy == "pure_market_making"


def test_stale_cleanup_cannot_adopt_foreign_configuration_events(recording):
    recorder, manager, market = recording
    recorder._did_create_order(MarketEvent.BuyOrderCreated.value, market, create())
    recorder._config_file_path = "credentials_cleanup"
    recorder.cleanup_operation_id = "not-current"
    with pytest.raises(ValueError):
        recorder._did_fill_order(MarketEvent.OrderFilled.value, market, fill())
    with manager.get_new_session() as session:
        assert session.query(TradeFill).count() == 0
