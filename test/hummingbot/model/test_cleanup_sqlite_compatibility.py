from decimal import Decimal

from sqlalchemy import create_engine, select, text
from sqlalchemy.schema import CreateTable

from hummingbot.client.config.client_config_map import ClientConfigMap
from hummingbot.client.config.config_helpers import ClientConfigAdapter
from hummingbot.model.order import Order
from hummingbot.model.sql_connection_manager import SQLConnectionManager, SQLConnectionType


def test_existing_canonical_file_database_accepts_additive_cleanup_columns(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "file")
    monkeypatch.setattr(SQLConnectionManager, "_postgres_engine", None)
    path = tmp_path / "existing.sqlite"
    engine = create_engine("sqlite:///" + str(path))
    definition = str(CreateTable(Order.__table__).compile(engine)).replace("\tcleanup_operation_id TEXT, \n", "").replace("\tcleanup_scope JSON, \n", "")
    with engine.begin() as connection:
        connection.execute(text(definition))
    engine.dispose()
    for _ in range(2):
        manager = SQLConnectionManager(ClientConfigAdapter(ClientConfigMap()), SQLConnectionType.TRADE_FILLS, db_path=str(path))
        try:
            with manager.get_new_session() as session, session.begin():
                if session.get(Order, "existing") is None:
                    session.add(Order(id="existing", config_file_path="test.yml", strategy="pmm", market="okx", symbol="BTC-USDT",
                                      base_asset="BTC", quote_asset="USDT", creation_timestamp=1, order_type="LIMIT", amount=Decimal("0.1"),
                                      leverage=1, price=100, last_status="OrderCancelled", last_update_timestamp=1))
            with manager.engine.connect() as connection:
                assert connection.execute(select(Order.cleanup_operation_id)).scalar_one() is None
        finally:
            manager.engine.dispose()
