import logging
from enum import Enum
from os.path import join
from typing import TYPE_CHECKING, Optional

from sqlalchemy import MetaData, create_engine
from sqlalchemy.engine.base import Engine
from sqlalchemy.orm import Query, Session, sessionmaker

from hummingbot import data_path
from hummingbot.logger.logger import HummingbotLogger
from hummingbot.model import get_declarative_base
from hummingbot.model.metadata import Metadata as LocalMetadata
from hummingbot.model.transaction_base import TransactionBase

if TYPE_CHECKING:
    from hummingbot.client.config.config_helpers import ClientConfigAdapter


class SQLConnectionType(Enum):
    TRADE_FILLS = 1


class SQLConnectionManager(TransactionBase):
    _scm_logger: Optional[HummingbotLogger] = None
    _scm_trade_fills_instance: Optional["SQLConnectionManager"] = None
    _postgres_engine: Optional[Engine] = None

    @classmethod
    def initialize_postgres(cls, settings) -> Engine:
        """Service-start gate; call before connector/application bootstrap."""
        from hummingbot.model.db_migration.postgres.connection import create_postgres_engine
        from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres
        engine = create_postgres_engine(settings)
        try:
            bootstrap_postgres(engine, auto_migrate=settings.enable_auto_migration)
        except BaseException:
            engine.dispose()
            raise
        if cls._postgres_engine is not None:
            cls._postgres_engine.dispose()
        cls._postgres_engine = engine
        cls._scm_trade_fills_instance = None
        return engine

    LOCAL_DB_VERSION_KEY = "local_db_version"
    LOCAL_DB_VERSION_VALUE = "20230516"

    @classmethod
    def logger(cls) -> HummingbotLogger:
        if cls._scm_logger is None:
            cls._scm_logger = logging.getLogger(__name__)
        return cls._scm_logger

    @classmethod
    def get_declarative_base(cls):
        return get_declarative_base()

    @classmethod
    def get_trade_fills_instance(
        cls, client_config_map: "ClientConfigAdapter", db_name: Optional[str] = None
    ) -> "SQLConnectionManager":
        if cls._scm_trade_fills_instance is None:
            cls._scm_trade_fills_instance = SQLConnectionManager(
                client_config_map, SQLConnectionType.TRADE_FILLS, db_name=db_name
            )
        elif cls._scm_trade_fills_instance.db_path is not None and cls.create_db_path(db_name=db_name) != cls._scm_trade_fills_instance.db_path:
            cls._scm_trade_fills_instance = SQLConnectionManager(
                client_config_map, SQLConnectionType.TRADE_FILLS, db_name=db_name
            )
        return cls._scm_trade_fills_instance

    @classmethod
    def create_db_path(cls, db_path: Optional[str] = None, db_name: Optional[str] = None) -> str:
        if db_path is not None:
            return db_path
        if db_name is not None:
            return join(data_path(), f"{db_name}.sqlite")
        else:
            return join(data_path(), "hummingbot_trades.sqlite")

    def __init__(self,
                 client_config_map: "ClientConfigAdapter",
                 connection_type: SQLConnectionType,
                 db_path: Optional[str] = None,
                 db_name: Optional[str] = None,
                 called_from_migrator = False):
        from hummingbot.client.config.runtime_config import RuntimeSettings, postgres_requested

        use_postgres = self._postgres_engine is not None or postgres_requested()
        self.db_path = None if use_postgres else self.create_db_path(db_path, db_name)

        if connection_type is SQLConnectionType.TRADE_FILLS:
            self._metadata: MetaData = self.get_declarative_base().metadata
            if use_postgres:
                self._engine = self._postgres_engine or self.initialize_postgres(RuntimeSettings.from_environment())
            else:
                # Transitional legacy path for existing local/unit tooling. The PG path
                # never computes a file path, invokes ORM DDL or removes constraints.
                self._engine = create_engine(client_config_map.db_mode.get_url(self.db_path))
                if self._engine.dialect.name != "sqlite":
                    raise ValueError("Configure PostgreSQL through DATABASE_URL; legacy DB modes are unsupported.")
                self._metadata.create_all(self._engine)

        self._session_cls = sessionmaker(bind=self._engine)

        if not use_postgres and connection_type is SQLConnectionType.TRADE_FILLS and (not called_from_migrator):
            self.check_and_migrate_db(client_config_map)

    @property
    def engine(self) -> Engine:
        return self._engine

    def get_new_session(self) -> Session:
        return self._session_cls()

    def get_local_db_version(self, session: Session):
        query: Query = (session.query(LocalMetadata)
                        .filter(LocalMetadata.key == self.LOCAL_DB_VERSION_KEY))
        result: Optional[LocalMetadata] = query.one_or_none()
        return result

    def check_and_migrate_db(self, client_config_map: "ClientConfigAdapter"):
        from hummingbot.model.db_migration.migrator import Migrator
        with self.get_new_session() as session:
            with session.begin():
                local_db_version = self.get_local_db_version(session=session)
                if local_db_version is None:
                    version_info: LocalMetadata = LocalMetadata(key=self.LOCAL_DB_VERSION_KEY,
                                                                value=self.LOCAL_DB_VERSION_VALUE)
                    session.add(version_info)
                    session.commit()
                else:
                    # There's no past db version to upgrade from at this moment. So we'll just update the version value
                    # if needed.
                    if local_db_version.value < self.LOCAL_DB_VERSION_VALUE:
                        was_migration_successful = Migrator().migrate_db_to_version(
                            client_config_map, self, int(local_db_version.value), int(self.LOCAL_DB_VERSION_VALUE)
                        )
                        if was_migration_successful:
                            # Cannot use variable local_db_version because reference is not valid
                            # since Migrator changed it
                            self.get_local_db_version(session=session).value = self.LOCAL_DB_VERSION_VALUE
