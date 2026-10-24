-- EXPLICIT MAINTENANCE ONLY: all legacy writers/revisions must be stopped.
-- The runner executes this file, canonical schema scripts and validation in ONE
-- transaction under the migration lock. No business rows are copied/deleted.
DO $$
DECLARE item record;
BEGIN
    FOR item IN SELECT * FROM (VALUES
        ('Order', 'orders'),
        ('OrderStatus', 'order_statuses'),
        ('TradeFill', 'trade_fills'),
        ('MarketState', 'market_states'),
        ('Executors', 'executors'),
        ('Controllers', 'controllers'),
        ('Position', 'positions'),
        ('FundingPayment', 'funding_payments'),
        ('InventoryCost', 'inventory_costs'),
        ('MarketData', 'market_data'),
        ('RangePositionUpdate', 'range_position_updates'),
        ('RangePositionCollectedFees', 'range_position_collected_fees'),
        ('Metadata', 'metadata')
    ) AS names(old_name, new_name)
    LOOP
        IF to_regclass(format('public.%I', item.old_name)) IS NOT NULL THEN
            IF to_regclass(format('public.%I', item.new_name)) IS NOT NULL THEN
                RAISE EXCEPTION 'Both legacy and canonical table names exist; reconcile explicitly';
            END IF;
            EXECUTE format('ALTER TABLE public.%I RENAME TO %I', item.old_name, item.new_name);
        END IF;
    END LOOP;
END $$;

DO $$
DECLARE item record;
BEGIN
    FOR item IN SELECT * FROM (VALUES
        ('rpu_timestamp_index', 'idx_range_position_updates_hb_id_timestamp'),
        ('rpu_config_file_index', 'idx_range_position_updates_config_file_path_timestamp'),
        ('rpu_position_index', 'idx_range_position_updates_position_address'),
        ('tf_config_timestamp_index', 'idx_trade_fills_config_file_path_timestamp'),
        ('tf_market_trading_pair_timestamp_index', 'idx_trade_fills_market_symbol_timestamp'),
        ('tf_market_base_asset_timestamp_index', 'idx_trade_fills_market_base_asset_timestamp'),
        ('tf_market_quote_asset_timestamp_index', 'idx_trade_fills_market_quote_asset_timestamp'),
        ('ms_config_market_index', 'uq_market_states_config_file_path_market'),
        ('c_type', 'idx_controllers_type'),
        ('o_config_timestamp_index', 'idx_orders_config_file_path_creation_timestamp'),
        ('o_market_trading_pair_timestamp_index', 'idx_orders_market_symbol_creation_timestamp'),
        ('o_market_base_asset_timestamp_index', 'idx_orders_market_base_asset_creation_timestamp'),
        ('o_market_quote_asset_timestamp_index', 'idx_orders_market_quote_asset_creation_timestamp'),
        ('ex_type', 'idx_executors_type'),
        ('ex_type_timestamp', 'idx_executors_type_timestamp'),
        ('ex_timestamp', 'idx_executors_timestamp'),
        ('ex_close_timestamp', 'idx_executors_close_timestamp'),
        ('ex_status', 'idx_executors_status'),
        ('ex_type_status', 'idx_executors_type_status'),
        ('rpf_id_index', 'idx_range_position_collected_fees_token_id_config_file_path'),
        ('p_controller_id_timestamp_index', 'idx_positions_controller_id_timestamp'),
        ('p_connector_name_trading_pair_timestamp_index', 'idx_positions_connector_name_trading_pair_timestamp'),
        ('timestamp', 'idx_market_data_exchange_trading_pair'),
        ('fp_config_timestamp_index', 'idx_funding_payments_config_file_path_timestamp'),
        ('fp_market_trading_pair_timestamp_index', 'idx_funding_payments_market_symbol_timestamp'),
        ('os_order_id_timestamp_index', 'idx_order_statuses_order_id_timestamp')
    ) AS names(old_name, new_name)
    LOOP
        IF to_regclass(format('public.%I', item.old_name)) IS NOT NULL THEN
            IF to_regclass(format('public.%I', item.new_name)) IS NOT NULL THEN
                RAISE EXCEPTION 'Both legacy and canonical index names exist';
            END IF;
            EXECUTE format('ALTER INDEX public.%I RENAME TO %I', item.old_name, item.new_name);
        END IF;
    END LOOP;
END $$;

DO $$
DECLARE item record; target text; columns_name text;
BEGIN
    FOR item IN
        SELECT c.oid, c.conname, c.contype, c.conkey, c.conrelid, t.relname
        FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid
        JOIN pg_namespace n ON n.oid=t.relnamespace
        WHERE n.nspname='public' AND t.relname IN ('orders', 'order_statuses', 'trade_fills', 'market_states', 'executors', 'controllers', 'positions', 'funding_payments', 'inventory_costs', 'market_data', 'range_position_updates', 'range_position_collected_fees', 'metadata')
          AND c.contype IN ('p', 'u', 'f')
    LOOP
        SELECT string_agg(a.attname, '_' ORDER BY keys.ordinality) INTO columns_name
        FROM unnest(item.conkey) WITH ORDINALITY AS keys(attnum, ordinality)
        JOIN pg_attribute a ON a.attrelid=item.conrelid AND a.attnum=keys.attnum;
        target := CASE item.contype
            WHEN 'p' THEN 'pk_' || item.relname
            WHEN 'u' THEN 'uq_' || item.relname || '_' || columns_name
            WHEN 'f' THEN 'fk_' || item.relname || '_' || split_part(columns_name, '_id', 1) || '_id'
        END;
        IF length(target) > 63 THEN RAISE EXCEPTION 'Constraint name exceeds identifier limit'; END IF;
        IF item.conname <> target THEN
            IF EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid=item.conrelid AND conname=target) THEN
                RAISE EXCEPTION 'Both legacy and canonical constraint names exist';
            END IF;
            EXECUTE format('ALTER TABLE public.%I RENAME CONSTRAINT %I TO %I', item.relname, item.conname, target);
        END IF;
    END LOOP;
END $$;

DO $$
DECLARE item record; current_sequence text; current_name text; target text;
BEGIN
    FOR item IN SELECT * FROM (VALUES
        ('order_statuses', 'id'), ('market_states', 'id'), ('controllers', 'controller_id'),
        ('inventory_costs', 'id'), ('range_position_updates', 'id'), ('range_position_collected_fees', 'id'),
        ('funding_payments', 'timestamp'), ('market_data', 'timestamp')
    ) AS names(table_name, column_name)
    LOOP
        IF to_regclass(format('public.%I', item.table_name)) IS NULL THEN CONTINUE; END IF;
        current_sequence := pg_get_serial_sequence(format('public.%I', item.table_name), item.column_name);
        IF current_sequence IS NULL THEN CONTINUE; END IF;
        SELECT relname INTO current_name FROM pg_class WHERE oid=current_sequence::regclass;
        target := 'seq_' || item.table_name || '_' || item.column_name;
        IF current_name <> target THEN
            IF to_regclass(format('public.%I', target)) IS NOT NULL THEN
                RAISE EXCEPTION 'Both legacy and canonical sequence names exist';
            END IF;
            EXECUTE format('ALTER SEQUENCE %s RENAME TO %I', current_sequence, target);
        END IF;
    END LOOP;
END $$;

-- Original timestamp-only primary keys prevent recording two markets at the same
-- timestamp. Expand these keys without changing any stored identities or rows.
DO $$
DECLARE item record; actual_columns text[];
BEGIN
    FOR item IN SELECT * FROM (VALUES
        ('funding_payments', ARRAY['timestamp', 'market', 'symbol']::text[]),
        ('market_data', ARRAY['timestamp', 'exchange', 'trading_pair']::text[])
    ) AS names(table_name, columns)
    LOOP
        IF to_regclass(format('public.%I', item.table_name)) IS NULL THEN CONTINUE; END IF;
        SELECT array_agg(a.attname::text ORDER BY keys.ordinality) INTO actual_columns
        FROM pg_constraint c CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS keys(attnum, ordinality)
        JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=keys.attnum
        WHERE c.conrelid=to_regclass(format('public.%I', item.table_name)) AND c.contype='p';
        IF actual_columns = ARRAY['timestamp']::text[] THEN
            EXECUTE format('ALTER TABLE public.%I DROP CONSTRAINT %I', item.table_name, 'pk_' || item.table_name);
            EXECUTE format('ALTER TABLE public.%I ADD CONSTRAINT %I PRIMARY KEY (%s)',
                item.table_name, 'pk_' || item.table_name, array_to_string(item.columns, ', '));
        ELSIF actual_columns IS DISTINCT FROM item.columns THEN
            RAISE EXCEPTION 'Unexpected legacy primary key; reconcile explicitly';
        END IF;
        EXECUTE format('ALTER TABLE public.%I ALTER COLUMN timestamp DROP DEFAULT', item.table_name);
    END LOOP;
END $$;

-- The original non-SQLite manager removed FKs. Reintroduce only the two declared
-- relationships. Orphan rows fail validation and roll back the entire cutover.
DO $$
DECLARE item record; target text;
BEGIN
    FOR item IN SELECT unnest(ARRAY['order_statuses', 'trade_fills']) AS table_name
    LOOP
        target := 'fk_' || item.table_name || '_order_id';
        IF to_regclass(format('public.%I', item.table_name)) IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid=to_regclass(format('public.%I', item.table_name)) AND conname=target)
        THEN
            EXECUTE format('ALTER TABLE public.%I ADD CONSTRAINT %I FOREIGN KEY (order_id) REFERENCES public.orders(id)', item.table_name, target);
        END IF;
    END LOOP;
END $$;
