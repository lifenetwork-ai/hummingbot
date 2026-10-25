from decimal import Decimal

from sqlalchemy import text

from hummingbot.model.db_migration.postgres.runner import SCRIPTS, bootstrap_postgres


def test_precision_migration_unscales_legacy_rows_exactly_once(pg_engine):
    with pg_engine.begin() as c:
        for script in sorted(SCRIPTS.glob("*.sql")):
            if script.name < "013":
                c.exec_driver_sql(script.read_text())
        c.exec_driver_sql("""
            INSERT INTO orders (id,config_file_path,strategy,market,symbol,base_asset,quote_asset,
                creation_timestamp,order_type,amount,leverage,price,last_status,last_update_timestamp)
            VALUES ('old','old.yml','v2','okx','BTC-USDT','BTC','USDT',1700000000000,
                'LIMIT_MAKER',248,1,80584400000,'OrderFilled',1700000000000);
            INSERT INTO trade_fills (config_file_path,strategy,market,symbol,base_asset,quote_asset,
                timestamp,order_id,trade_type,order_type,price,amount,leverage,trade_fee,trade_fee_in_quote,exchange_trade_id)
            VALUES ('old.yml','v2','okx','BTC-USDT','BTC','USDT',1700000000000,'old','BUY','LIMIT_MAKER',
                80584400000,248,1,'{}',15999,'old-fill');
            INSERT INTO positions (id,controller_id,connector_name,side,trading_pair,timestamp,
                volume_traded_quote,amount,breakeven_price,unrealized_pnl_quote,realized_pnl_quote,cum_fees_quote)
            VALUES ('held','controller','okx','SELL','BTC-USDT',1700000000000,
                123456789,-123456789,123456789,-123456789,123456789,123456789);
            INSERT INTO market_data (timestamp,exchange,trading_pair,mid_price,best_bid,best_ask)
            VALUES (1700000000123456,'okx','BTC-USDT',80584400000,80584300000,80584500000);
        """)
    for enabled in (True, True, False):
        bootstrap_postgres(pg_engine, auto_migrate=enabled)
        with pg_engine.connect() as c:
            assert c.execute(text("SELECT amount,price,creation_timestamp FROM orders")).one() == (
                Decimal("0.000248"), Decimal("80584.4"), 1700000000000)
            assert c.execute(text("SELECT amount,price,trade_fee_in_quote FROM trade_fills")).one() == (
                Decimal("0.000248"), Decimal("80584.4"), Decimal("0.015999"))
            assert c.execute(text("SELECT volume_traded_quote,amount,breakeven_price,unrealized_pnl_quote,realized_pnl_quote,cum_fees_quote FROM positions")).one() == (
                Decimal("123.456789"), Decimal("-123.456789"), Decimal("123.456789"),
                Decimal("-123.456789"), Decimal("123.456789"), Decimal("123.456789"))
            assert c.execute(text("SELECT timestamp,mid_price,best_bid,best_ask FROM market_data")).one() == (
                Decimal("1700000000.123456"), Decimal("80584.4"), Decimal("80584.3"), Decimal("80584.5"))
    with pg_engine.begin() as c:
        c.execute(text("UPDATE orders SET amount=:amount"), {"amount": Decimal("0.00024818")})
    bootstrap_postgres(pg_engine, auto_migrate=True)
    with pg_engine.connect() as c:
        assert c.execute(text("SELECT amount FROM orders")).scalar_one() == Decimal("0.00024818")
