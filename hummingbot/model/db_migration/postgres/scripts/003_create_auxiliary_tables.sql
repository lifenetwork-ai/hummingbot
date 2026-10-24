-- Initial canonical schema. Replay preserves existing rows.
-- Transactions and migration locking are owned by the Python runner.


CREATE TABLE IF NOT EXISTS public.funding_payments (
	timestamp BIGINT NOT NULL,
	config_file_path TEXT NOT NULL,
	market TEXT NOT NULL,
	rate FLOAT NOT NULL,
	symbol TEXT NOT NULL,
	amount FLOAT NOT NULL,
	CONSTRAINT pk_funding_payments PRIMARY KEY (timestamp, market, symbol)
);

CREATE SEQUENCE IF NOT EXISTS public.seq_inventory_costs_id;

CREATE TABLE IF NOT EXISTS public.inventory_costs (
	id INTEGER DEFAULT nextval('public.seq_inventory_costs_id'::regclass) NOT NULL,
	base_asset VARCHAR(45) NOT NULL,
	quote_asset VARCHAR(45) NOT NULL,
	base_volume NUMERIC(48, 18) NOT NULL,
	quote_volume NUMERIC(48, 18) NOT NULL,
	CONSTRAINT pk_inventory_costs PRIMARY KEY (id),
	CONSTRAINT uq_inventory_costs_base_asset_quote_asset UNIQUE (base_asset, quote_asset)
);

ALTER SEQUENCE public.seq_inventory_costs_id OWNED BY public.inventory_costs.id;

CREATE TABLE IF NOT EXISTS public.market_data (
	timestamp BIGINT NOT NULL,
	exchange TEXT NOT NULL,
	trading_pair TEXT NOT NULL,
	mid_price BIGINT NOT NULL,
	best_bid BIGINT NOT NULL,
	best_ask BIGINT NOT NULL,
	order_book JSON,
	CONSTRAINT pk_market_data PRIMARY KEY (timestamp, exchange, trading_pair)
);

CREATE SEQUENCE IF NOT EXISTS public.seq_range_position_updates_id;

CREATE TABLE IF NOT EXISTS public.range_position_updates (
	id INTEGER DEFAULT nextval('public.seq_range_position_updates_id'::regclass) NOT NULL,
	hb_id TEXT NOT NULL,
	timestamp BIGINT NOT NULL,
	tx_hash TEXT,
	token_id INTEGER NOT NULL,
	trade_fee JSON NOT NULL,
	config_file_path TEXT,
	market TEXT,
	order_action TEXT,
	trading_pair TEXT,
	position_address TEXT,
	lower_price FLOAT,
	upper_price FLOAT,
	mid_price FLOAT,
	base_amount FLOAT,
	quote_amount FLOAT,
	base_fee FLOAT,
	quote_fee FLOAT,
	position_rent FLOAT,
	position_rent_refunded FLOAT,
	trade_fee_in_quote FLOAT,
	CONSTRAINT pk_range_position_updates PRIMARY KEY (id)
);

ALTER SEQUENCE public.seq_range_position_updates_id OWNED BY public.range_position_updates.id;

CREATE SEQUENCE IF NOT EXISTS public.seq_range_position_collected_fees_id;

CREATE TABLE IF NOT EXISTS public.range_position_collected_fees (
	id INTEGER DEFAULT nextval('public.seq_range_position_collected_fees_id'::regclass) NOT NULL,
	config_file_path TEXT NOT NULL,
	strategy TEXT NOT NULL,
	token_id INTEGER NOT NULL,
	token_0 TEXT NOT NULL,
	token_1 TEXT NOT NULL,
	claimed_fee_0 FLOAT NOT NULL,
	claimed_fee_1 FLOAT NOT NULL,
	CONSTRAINT pk_range_position_collected_fees PRIMARY KEY (id)
);

ALTER SEQUENCE public.seq_range_position_collected_fees_id OWNED BY public.range_position_collected_fees.id;
