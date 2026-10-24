import os
from unittest.mock import patch

import pytest

from hummingbot.client.config.client_config_map import ClientConfigMap
from hummingbot.client.config.config_helpers import ClientConfigAdapter
from hummingbot.client.config.runtime_config import RuntimeConfigurationError
from hummingbot.model.market_state import MarketState
from hummingbot.model.sql_connection_manager import SQLConnectionManager, SQLConnectionType


def test_file_backend_uses_existing_sqlite_and_roundtrips(tmp_path):
    database = str(tmp_path / "legacy.sqlite")
    with patch.dict(os.environ, {"STORAGE_BACKEND": "file", "DATABASE_URL": "invalid-unused"}, clear=True), \
            patch.object(SQLConnectionManager, "_postgres_engine", None), \
            patch.object(SQLConnectionManager, "initialize_postgres") as postgres:
        manager = SQLConnectionManager(ClientConfigAdapter(ClientConfigMap()), SQLConnectionType.TRADE_FILLS,
                                       db_path=database)
        try:
            assert manager.engine.dialect.name == "sqlite"
            assert manager.db_path == database
            with manager.get_new_session() as session:
                session.add(MarketState(config_file_path="local.yml", market="okx", timestamp=1,
                                        saved_state={"order-1": {"state": "pending"}}))
                session.commit()
            with manager.get_new_session() as session:
                assert session.query(MarketState).one().saved_state == {"order-1": {"state": "pending"}}
            postgres.assert_not_called()
        finally:
            manager.engine.dispose()


def test_file_backend_cannot_reuse_initialized_postgres():
    with patch.dict(os.environ, {"STORAGE_BACKEND": "file"}, clear=True), \
            patch.object(SQLConnectionManager, "_postgres_engine", object()):
        with pytest.raises(RuntimeConfigurationError, match="restart the process"):
            SQLConnectionManager(None, SQLConnectionType.TRADE_FILLS)
