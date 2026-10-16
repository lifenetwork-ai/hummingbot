"""SQLAlchemy/MarketsRecorder compatibility; isolated DB, no financial authority."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine import make_url

from hummingbot.client.config.client_config_map import MarketDataCollectionConfigMap
from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee
from hummingbot.core.event.events import BuyOrderCreatedEvent, MarketEvent, OrderFilledEvent
from hummingbot.model.position import Position
from hummingbot.model.postgres_recorder import recorder_tables
from hummingbot.model.sql_connection_manager import SQLConnectionManager, SQLConnectionType
from hummingbot.strategy_v2.executors.position_executor.data_types import PositionExecutorConfig
from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import migrate
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executors import CloseType
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo


def config_for(dsn, *, tls=None):
    url = make_url(dsn).set(drivername='postgresql+psycopg', username='life_pg2_reporter')
    url = url.update_query_dict({'sslmode': 'disable'} if tls is None else tls)  # Disposable loopback DB only.
    return SimpleNamespace(db_mode=SimpleNamespace(get_url=lambda _: url))


def manager(dsn, *, tls=None):
    return SQLConnectionManager(config_for(dsn, tls=tls), SQLConnectionType.TRADE_FILLS, db_name='not_a_local_file')


async def provision(c):
    await c.execute('GRANT USAGE ON SCHEMA life_recorder TO life_pg2_reporter; '
                    'GRANT SELECT ON ALL TABLES IN SCHEMA life_recorder TO life_pg2_reporter; '
                    'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA life_recorder TO life_pg2_reporter')
    for table in recorder_tables():
        if table.name != 'Metadata':
            await c.execute(f'GRANT INSERT, UPDATE ON life_recorder."{table.name}" TO life_pg2_reporter')


@pytest.mark.asyncio
async def test_recorder_migration_is_empty_repeatable_and_runtime_has_no_ddl(database):
    c, dsn, _ = database
    before = await c.fetchval("SELECT count(*) FROM pg_constraint WHERE contype='f'")
    await provision(c)
    assert (await migrate(c, action='up'))['applied'] == []
    sql = manager(dsn)
    try:
        assert sql.engine.dialect.name == 'postgresql'
        assert sql.engine.pool._max_overflow == 0
        assert await c.fetchval('SELECT count(*) FROM life_recorder."Order"') == 0
        assert await c.fetchval("SELECT count(*) FROM pg_constraint WHERE contype='f'") == before
        assert not await c.fetchval("SELECT has_schema_privilege('life_pg2_reporter','life_recorder','CREATE')")
    finally:
        sql.engine.dispose()


@pytest.mark.asyncio
async def test_real_recorder_fill_before_ack_position_and_cold_read(database):
    c, dsn, _ = database
    await provision(c)
    market = MagicMock()
    market.display_name = 'okx'
    market.tracking_states = {'test_only': 'cold-state'}
    config = MarketDataCollectionConfigMap(market_data_collection_enabled=False)
    sql = manager(dsn)
    recorder = MarketsRecorder(sql, [market], 'synthetic.yml', 'life_liquidity', config)
    try:
        recorder._did_fill_order(MarketEvent.OrderFilled.value, market, OrderFilledEvent(
            timestamp=1700000000, order_id='synthetic-order', trading_pair='LIFE-USDT',
            trade_type=TradeType.BUY, order_type=OrderType.LIMIT, price=Decimal('1.234567'),
            amount=Decimal('2.123456'), trade_fee=AddedToCostTradeFee(percent=Decimal('0.001')),
            exchange_trade_id='synthetic-fill'))
        recorder._did_create_order(MarketEvent.BuyOrderCreated.value, market, BuyOrderCreatedEvent(
            timestamp=1700000001, type=OrderType.LIMIT, trading_pair='LIFE-USDT',
            amount=Decimal('2.123456'), price=Decimal('1.234567'), order_id='synthetic-order',
            creation_timestamp=1700000000, exchange_order_id='synthetic-exchange-order'))
        recorder.store_position(Position(
            id='synthetic-position', controller_id='synthetic-controller', connector_name='okx',
            side='BUY', trading_pair='LIFE-USDT', timestamp=1700000000000,
            volume_traded_quote=Decimal('3.1'), amount=Decimal('2.123456'),
            breakeven_price=Decimal('1.234567'), unrealized_pnl_quote=Decimal('0.2'),
            realized_pnl_quote=Decimal('-0.1'), cum_fees_quote=Decimal('0.003')))
        executor_config = PositionExecutorConfig(
            id='synthetic-executor', timestamp=1700000000, trading_pair='LIFE-USDT',
            connector_name='okx', side=TradeType.BUY, amount=Decimal('2.123456'))
        info = ExecutorInfo(
            id=executor_config.id, timestamp=executor_config.timestamp, type='position_executor',
            close_timestamp=1700000001, close_type=CloseType.COMPLETED, status=RunnableStatus.TERMINATED,
            controller_id='synthetic-controller', custom_info={'test_only': True}, config=executor_config,
            net_pnl_pct=Decimal('0.01'), net_pnl_quote=Decimal('0.1'), cum_fees_quote=Decimal('0.003'),
            filled_amount_quote=Decimal('3.1'), is_active=False, is_trading=False)
        recorder.store_or_update_executor(SimpleNamespace(executor_info=info, config=executor_config))
    finally:
        sql.engine.dispose()
    cold = manager(dsn)
    try:
        recovered = MarketsRecorder(cold, [market], 'synthetic.yml', 'life_liquidity', config)
        fills = recovered.get_trades_for_config('synthetic.yml')
        assert len(fills) == 1 and fills[0].exchange_trade_id == 'synthetic-fill'
        assert fills[0].price == Decimal('1.234567') and fills[0].amount == Decimal('2.123456')
        assert recovered.get_positions_by_ids(['synthetic-position'])[0].amount == Decimal('2.123456')
        assert recovered.get_executors_by_ids(['synthetic-executor'])[0].id == 'synthetic-executor'
        assert recovered.get_orders_for_config_and_market('synthetic.yml', market)[0].exchange_order_id == 'synthetic-exchange-order'
        with cold.get_new_session() as session:
            assert recovered.get_market_states('synthetic.yml', market, session).saved_state == market.tracking_states
        assert await c.fetchval('SELECT count(*) FROM life_financial.accounts') == 0
    finally:
        cold.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['missing_table', 'missing_column', 'wrong_type', 'missing_index', 'missing_unique',
                                    'stale_version', 'future_version', 'schema_create', 'version_write'])
async def test_incompatible_recorder_schema_fails_without_ddl_or_sqlite_migration(database, damage):
    c, dsn, _ = database
    await provision(c)
    changes = {
        'missing_table': 'DROP TABLE life_recorder."Position"',
        'missing_column': 'ALTER TABLE life_recorder."Position" DROP COLUMN amount',
        'wrong_type': 'ALTER TABLE life_recorder."Position" ALTER COLUMN amount TYPE TEXT',
        'missing_index': 'DROP INDEX life_recorder.p_controller_id_timestamp_index',
        'missing_unique': 'ALTER TABLE life_recorder."InventoryCost" DROP CONSTRAINT "InventoryCost_base_asset_quote_asset_key"',
        'stale_version': "UPDATE life_recorder.\"Metadata\" SET value='20200101'",
        'future_version': "UPDATE life_recorder.\"Metadata\" SET value='20990101'",
        'schema_create': 'GRANT CREATE ON SCHEMA life_recorder TO life_pg2_reporter',
        'version_write': 'GRANT UPDATE ON life_recorder."Metadata" TO life_pg2_reporter',
    }
    await c.execute(changes[damage])
    with pytest.raises(ValueError, match='RECORDER_POSTGRES_SCHEMA_INCOMPATIBLE'):
        manager(dsn)
    if damage == 'missing_table':
        assert await c.fetchval("SELECT to_regclass('life_recorder.\"Position\"')") is None
    elif damage in ('stale_version', 'future_version'):
        assert await c.fetchval('SELECT value FROM life_recorder."Metadata"') != SQLConnectionManager.LOCAL_DB_VERSION_VALUE


@pytest.mark.asyncio
async def test_missing_schema_cannot_bootstrap_or_fall_back_to_public(database):
    c, dsn, _ = database
    await c.execute('DROP SCHEMA life_recorder CASCADE; CREATE TABLE public."Order" (sentinel text)')
    with pytest.raises(ValueError, match='RECORDER_POSTGRES_SCHEMA_INCOMPATIBLE'):
        manager(dsn)
    assert await c.fetchval("SELECT to_regnamespace('life_recorder')") is None
    assert await c.fetchval('SELECT count(*) FROM public."Order"') == 0


@pytest.mark.asyncio
async def test_singleton_cannot_return_cached_sqlite_for_postgres_configuration(database, monkeypatch):
    c, dsn, _ = database
    await provision(c)
    monkeypatch.setattr(SQLConnectionManager, '_scm_trade_fills_instance', None)
    sqlite_config = SimpleNamespace(db_mode=SimpleNamespace(get_url=lambda _: 'sqlite:///:memory:'))
    first = SQLConnectionManager.get_trade_fills_instance(sqlite_config, db_name='same_name')
    second = None
    try:
        assert first.engine.dialect.name == 'sqlite'
        config = config_for(dsn)
        second = SQLConnectionManager.get_trade_fills_instance(config, db_name='same_name')
        assert second is not first and second.engine.dialect.name == 'postgresql'
        assert SQLConnectionManager.get_trade_fills_instance(config, db_name='same_name') is second
        missing_schema_dsn = dsn.rsplit('/', 1)[0] + '/life_pg2_test'
        with pytest.raises(ValueError, match='RECORDER_POSTGRES_SCHEMA_INCOMPATIBLE'):
            SQLConnectionManager.get_trade_fills_instance(config_for(missing_schema_dsn), db_name='same_name')
    finally:
        first.engine.dispose()
        if second is not None and second is not first:
            second.engine.dispose()
