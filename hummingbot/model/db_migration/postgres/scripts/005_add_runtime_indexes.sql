-- Indexes correspond to the existing ORM query paths.

CREATE INDEX IF NOT EXISTS idx_controllers_type ON controllers (type);

CREATE INDEX IF NOT EXISTS idx_executors_close_timestamp ON executors (close_timestamp);

CREATE INDEX IF NOT EXISTS idx_executors_status ON executors (status);

CREATE INDEX IF NOT EXISTS idx_executors_timestamp ON executors (timestamp);

CREATE INDEX IF NOT EXISTS idx_executors_type ON executors (type);

CREATE INDEX IF NOT EXISTS idx_executors_type_status ON executors (type, status);

CREATE INDEX IF NOT EXISTS idx_executors_type_timestamp ON executors (type, timestamp);

CREATE INDEX IF NOT EXISTS idx_funding_payments_config_file_path_timestamp ON funding_payments (config_file_path, timestamp);

CREATE INDEX IF NOT EXISTS idx_funding_payments_market_symbol_timestamp ON funding_payments (market, symbol, timestamp);

CREATE INDEX IF NOT EXISTS idx_market_data_exchange_trading_pair ON market_data (exchange, trading_pair);

CREATE UNIQUE INDEX IF NOT EXISTS uq_market_states_config_file_path_market ON market_states (config_file_path, market);

CREATE INDEX IF NOT EXISTS idx_orders_config_file_path_creation_timestamp ON orders (config_file_path, creation_timestamp);

CREATE INDEX IF NOT EXISTS idx_orders_market_base_asset_creation_timestamp ON orders (market, base_asset, creation_timestamp);

CREATE INDEX IF NOT EXISTS idx_orders_market_quote_asset_creation_timestamp ON orders (market, quote_asset, creation_timestamp);

CREATE INDEX IF NOT EXISTS idx_orders_market_symbol_creation_timestamp ON orders (market, symbol, creation_timestamp);

CREATE INDEX IF NOT EXISTS idx_positions_connector_name_trading_pair_timestamp ON positions (connector_name, trading_pair, timestamp);

CREATE INDEX IF NOT EXISTS idx_positions_controller_id_timestamp ON positions (controller_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_range_position_collected_fees_token_id_config_file_path ON range_position_collected_fees (token_id, config_file_path);

CREATE INDEX IF NOT EXISTS idx_range_position_updates_config_file_path_timestamp ON range_position_updates (config_file_path, timestamp);

CREATE INDEX IF NOT EXISTS idx_range_position_updates_hb_id_timestamp ON range_position_updates (hb_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_range_position_updates_position_address ON range_position_updates (position_address);

CREATE INDEX IF NOT EXISTS idx_order_statuses_order_id_timestamp ON order_statuses (order_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_trade_fills_config_file_path_timestamp ON trade_fills (config_file_path, timestamp);

CREATE INDEX IF NOT EXISTS idx_trade_fills_market_base_asset_timestamp ON trade_fills (market, base_asset, timestamp);

CREATE INDEX IF NOT EXISTS idx_trade_fills_market_quote_asset_timestamp ON trade_fills (market, quote_asset, timestamp);

CREATE INDEX IF NOT EXISTS idx_trade_fills_market_symbol_timestamp ON trade_fills (market, symbol, timestamp);
