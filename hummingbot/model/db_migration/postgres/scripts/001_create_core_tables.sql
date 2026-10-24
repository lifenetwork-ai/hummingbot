-- Initial canonical schema. Replay preserves existing rows.
-- Transactions and migration locking are owned by the Python runner.


CREATE TABLE IF NOT EXISTS orders (
	id TEXT NOT NULL,
	config_file_path TEXT NOT NULL,
	strategy TEXT NOT NULL,
	market TEXT NOT NULL,
	symbol TEXT NOT NULL,
	base_asset TEXT NOT NULL,
	quote_asset TEXT NOT NULL,
	creation_timestamp BIGINT NOT NULL,
	order_type TEXT NOT NULL,
	amount BIGINT NOT NULL,
	leverage INTEGER NOT NULL,
	price BIGINT NOT NULL,
	last_status TEXT NOT NULL,
	last_update_timestamp BIGINT NOT NULL,
	exchange_order_id TEXT,
	position TEXT,
	CONSTRAINT pk_orders PRIMARY KEY (id)
);

CREATE SEQUENCE IF NOT EXISTS seq_order_statuses_id;

CREATE TABLE IF NOT EXISTS order_statuses (
	id INTEGER DEFAULT nextval('seq_order_statuses_id'::regclass) NOT NULL,
	order_id TEXT NOT NULL,
	timestamp BIGINT NOT NULL,
	status TEXT NOT NULL,
	CONSTRAINT pk_order_statuses PRIMARY KEY (id),
	CONSTRAINT fk_order_statuses_order_id FOREIGN KEY(order_id) REFERENCES orders (id)
);

ALTER SEQUENCE seq_order_statuses_id OWNED BY order_statuses.id;

CREATE TABLE IF NOT EXISTS trade_fills (
	config_file_path TEXT NOT NULL,
	strategy TEXT NOT NULL,
	market TEXT NOT NULL,
	symbol TEXT NOT NULL,
	base_asset TEXT NOT NULL,
	quote_asset TEXT NOT NULL,
	timestamp BIGINT NOT NULL,
	order_id TEXT NOT NULL,
	trade_type TEXT NOT NULL,
	order_type TEXT NOT NULL,
	price BIGINT NOT NULL,
	amount BIGINT NOT NULL,
	leverage INTEGER NOT NULL,
	trade_fee JSON NOT NULL,
	trade_fee_in_quote BIGINT,
	exchange_trade_id TEXT NOT NULL,
	position TEXT,
	CONSTRAINT pk_trade_fills PRIMARY KEY (market, order_id, exchange_trade_id),
	CONSTRAINT fk_trade_fills_order_id FOREIGN KEY(order_id) REFERENCES orders (id)
);

CREATE SEQUENCE IF NOT EXISTS seq_market_states_id;

CREATE TABLE IF NOT EXISTS market_states (
	id INTEGER DEFAULT nextval('seq_market_states_id'::regclass) NOT NULL,
	config_file_path TEXT NOT NULL,
	market TEXT NOT NULL,
	timestamp BIGINT NOT NULL,
	saved_state JSON NOT NULL,
	CONSTRAINT pk_market_states PRIMARY KEY (id)
);

ALTER SEQUENCE seq_market_states_id OWNED BY market_states.id;

CREATE TABLE IF NOT EXISTS metadata (
	key TEXT NOT NULL,
	value TEXT NOT NULL,
	CONSTRAINT pk_metadata PRIMARY KEY (key)
);
