-- PostgreSQL now stores decimal values in their natural units. Convert only
-- the previous scaled BIGINT columns; replay must never divide NUMERIC again.
-- Previously discarded fractional digits cannot be reconstructed here.
DO $$
DECLARE item record;
BEGIN
    FOR item IN SELECT * FROM (VALUES
        ('orders', 'amount'),
        ('orders', 'price'),
        ('trade_fills', 'amount'),
        ('trade_fills', 'price'),
        ('trade_fills', 'trade_fee_in_quote'),
        ('positions', 'volume_traded_quote'),
        ('positions', 'amount'),
        ('positions', 'breakeven_price'),
        ('positions', 'unrealized_pnl_quote'),
        ('positions', 'realized_pnl_quote'),
        ('positions', 'cum_fees_quote'),
        ('market_data', 'timestamp'),
        ('market_data', 'mid_price'),
        ('market_data', 'best_bid'),
        ('market_data', 'best_ask')
    ) AS names(table_name, column_name)
    LOOP
        IF EXISTS (
            SELECT 1 FROM pg_attribute
            WHERE attrelid = to_regclass(item.table_name)
                AND attname = item.column_name AND NOT attisdropped
                AND atttypid = 'bigint'::regtype
        ) THEN
            EXECUTE format('ALTER TABLE %I ALTER COLUMN %I TYPE NUMERIC USING %I::numeric / 1000000',
                item.table_name, item.column_name, item.column_name);
        END IF;
    END LOOP;
END $$;
