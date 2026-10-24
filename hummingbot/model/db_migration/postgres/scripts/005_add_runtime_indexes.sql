-- Indexes correspond to the existing ORM query paths.

CREATE INDEX IF NOT EXISTS idx_controllers_type ON public.controllers (type);

CREATE INDEX IF NOT EXISTS idx_executors_close_timestamp ON public.executors (close_timestamp);

CREATE INDEX IF NOT EXISTS idx_executors_status ON public.executors (status);

CREATE INDEX IF NOT EXISTS idx_executors_timestamp ON public.executors (timestamp);

CREATE INDEX IF NOT EXISTS idx_executors_type ON public.executors (type);

CREATE INDEX IF NOT EXISTS idx_executors_type_status ON public.executors (type, status);

CREATE INDEX IF NOT EXISTS idx_executors_type_timestamp ON public.executors (type, timestamp);

CREATE INDEX IF NOT EXISTS idx_funding_payments_config_file_path_timestamp ON public.funding_payments (config_file_path, timestamp);

CREATE INDEX IF NOT EXISTS idx_funding_payments_market_symbol_timestamp ON public.funding_payments (market, symbol, timestamp);

CREATE INDEX IF NOT EXISTS idx_market_data_exchange_trading_pair ON public.market_data (exchange, trading_pair);

CREATE UNIQUE INDEX IF NOT EXISTS uq_market_states_config_file_path_market ON public.market_states (config_file_path, market);

CREATE INDEX IF NOT EXISTS idx_orders_config_file_path_creation_timestamp ON public.orders (config_file_path, creation_timestamp);

CREATE INDEX IF NOT EXISTS idx_orders_market_base_asset_creation_timestamp ON public.orders (market, base_asset, creation_timestamp);

CREATE INDEX IF NOT EXISTS idx_orders_market_quote_asset_creation_timestamp ON public.orders (market, quote_asset, creation_timestamp);

CREATE INDEX IF NOT EXISTS idx_orders_market_symbol_creation_timestamp ON public.orders (market, symbol, creation_timestamp);

CREATE INDEX IF NOT EXISTS idx_positions_connector_name_trading_pair_timestamp ON public.positions (connector_name, trading_pair, timestamp);

CREATE INDEX IF NOT EXISTS idx_positions_controller_id_timestamp ON public.positions (controller_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_range_position_collected_fees_token_id_config_file_path ON public.range_position_collected_fees (token_id, config_file_path);

CREATE INDEX IF NOT EXISTS idx_range_position_updates_config_file_path_timestamp ON public.range_position_updates (config_file_path, timestamp);

CREATE INDEX IF NOT EXISTS idx_range_position_updates_hb_id_timestamp ON public.range_position_updates (hb_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_range_position_updates_position_address ON public.range_position_updates (position_address);

CREATE INDEX IF NOT EXISTS idx_order_statuses_order_id_timestamp ON public.order_statuses (order_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_trade_fills_config_file_path_timestamp ON public.trade_fills (config_file_path, timestamp);

CREATE INDEX IF NOT EXISTS idx_trade_fills_market_base_asset_timestamp ON public.trade_fills (market, base_asset, timestamp);

CREATE INDEX IF NOT EXISTS idx_trade_fills_market_quote_asset_timestamp ON public.trade_fills (market, quote_asset, timestamp);

CREATE INDEX IF NOT EXISTS idx_trade_fills_market_symbol_timestamp ON public.trade_fills (market, symbol, timestamp);
