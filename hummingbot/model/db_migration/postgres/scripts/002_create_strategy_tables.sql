-- Initial canonical schema. Replay preserves existing rows.
-- Transactions and migration locking are owned by the Python runner.


CREATE TABLE IF NOT EXISTS executors (
	id TEXT NOT NULL,
	timestamp FLOAT NOT NULL,
	type TEXT NOT NULL,
	close_type INTEGER,
	close_timestamp BIGINT,
	status INTEGER NOT NULL,
	config JSON NOT NULL,
	net_pnl_pct FLOAT NOT NULL,
	net_pnl_quote FLOAT NOT NULL,
	cum_fees_quote FLOAT NOT NULL,
	filled_amount_quote FLOAT NOT NULL,
	is_active BOOLEAN NOT NULL,
	is_trading BOOLEAN NOT NULL,
	custom_info JSON NOT NULL,
	controller_id TEXT,
	CONSTRAINT pk_executors PRIMARY KEY (id)
);

CREATE SEQUENCE IF NOT EXISTS seq_controllers_controller_id;

CREATE TABLE IF NOT EXISTS controllers (
	id TEXT,
	controller_id INTEGER DEFAULT nextval('seq_controllers_controller_id'::regclass) NOT NULL,
	timestamp FLOAT NOT NULL,
	type TEXT NOT NULL,
	config JSON NOT NULL,
	CONSTRAINT pk_controllers PRIMARY KEY (controller_id)
);

ALTER SEQUENCE seq_controllers_controller_id OWNED BY controllers.controller_id;

CREATE TABLE IF NOT EXISTS positions (
	id TEXT NOT NULL,
	controller_id TEXT NOT NULL,
	connector_name TEXT NOT NULL,
	side TEXT NOT NULL,
	trading_pair TEXT NOT NULL,
	timestamp BIGINT NOT NULL,
	volume_traded_quote BIGINT NOT NULL,
	amount BIGINT NOT NULL,
	breakeven_price BIGINT NOT NULL,
	unrealized_pnl_quote BIGINT NOT NULL,
	realized_pnl_quote BIGINT NOT NULL,
	cum_fees_quote BIGINT NOT NULL,
	CONSTRAINT pk_positions PRIMARY KEY (id)
);
