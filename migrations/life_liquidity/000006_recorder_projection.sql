-- Legacy recorder reporting only; never financial authority or account bootstrap.

-- Static reviewed schema. Central runner owns repeat execution and checksum validation.

-- Projection FKs deliberately omitted: callbacks may receive fills before order ACK.

CREATE SCHEMA life_recorder;

REVOKE ALL ON SCHEMA life_recorder FROM PUBLIC;

CREATE TABLE life_recorder."Controllers" (
    id TEXT,
    controller_id SERIAL NOT NULL,
    timestamp FLOAT NOT NULL,
    type TEXT NOT NULL,
    config JSON NOT NULL,
    PRIMARY KEY (controller_id)
);

CREATE INDEX c_type ON life_recorder."Controllers" (type);

CREATE TABLE life_recorder."Executors" (
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
    PRIMARY KEY (id)
);

CREATE INDEX ex_close_timestamp ON life_recorder."Executors" (close_timestamp);

CREATE INDEX ex_status ON life_recorder."Executors" (status);

CREATE INDEX ex_timestamp ON life_recorder."Executors" (timestamp);

CREATE INDEX ex_type ON life_recorder."Executors" (type);

CREATE INDEX ex_type_status ON life_recorder."Executors" (type, status);

CREATE INDEX ex_type_timestamp ON life_recorder."Executors" (type, timestamp);

CREATE TABLE life_recorder."FundingPayment" (
    timestamp BIGSERIAL NOT NULL,
    config_file_path TEXT NOT NULL,
    market TEXT NOT NULL,
    rate FLOAT NOT NULL,
    symbol TEXT NOT NULL,
    amount FLOAT NOT NULL,
    PRIMARY KEY (timestamp)
);

CREATE INDEX fp_config_timestamp_index ON life_recorder."FundingPayment" (config_file_path, timestamp);

CREATE INDEX fp_market_trading_pair_timestamp_index ON life_recorder."FundingPayment" (market, symbol, timestamp);

CREATE TABLE life_recorder."InventoryCost" (
    id SERIAL NOT NULL,
    base_asset VARCHAR(45) NOT NULL,
    quote_asset VARCHAR(45) NOT NULL,
    base_volume NUMERIC(48, 18) NOT NULL,
    quote_volume NUMERIC(48, 18) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (base_asset, quote_asset)
);

CREATE TABLE life_recorder."MarketData" (
    timestamp BIGSERIAL NOT NULL,
    exchange TEXT NOT NULL,
    trading_pair TEXT NOT NULL,
    mid_price BIGINT NOT NULL,
    best_bid BIGINT NOT NULL,
    best_ask BIGINT NOT NULL,
    order_book JSON,
    PRIMARY KEY (timestamp)
);

CREATE INDEX timestamp ON life_recorder."MarketData" (exchange, trading_pair);

CREATE TABLE life_recorder."MarketState" (
    id SERIAL NOT NULL,
    config_file_path TEXT NOT NULL,
    market TEXT NOT NULL,
    timestamp BIGINT NOT NULL,
    saved_state JSON NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE life_recorder."Metadata" (
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (key)
);

CREATE TABLE life_recorder."Order" (
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
    PRIMARY KEY (id)
);

CREATE INDEX o_config_timestamp_index ON life_recorder."Order" (config_file_path, creation_timestamp);

CREATE INDEX o_market_base_asset_timestamp_index ON life_recorder."Order" (market, base_asset, creation_timestamp);

CREATE INDEX o_market_quote_asset_timestamp_index ON life_recorder."Order" (market, quote_asset, creation_timestamp);

CREATE INDEX o_market_trading_pair_timestamp_index ON life_recorder."Order" (market, symbol, creation_timestamp);

CREATE TABLE life_recorder."OrderStatus" (
    id SERIAL NOT NULL,
    order_id TEXT NOT NULL,
    timestamp BIGINT NOT NULL,
    status TEXT NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX os_order_id_timestamp_index ON life_recorder."OrderStatus" (order_id, timestamp);

CREATE TABLE life_recorder."Position" (
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
    PRIMARY KEY (id)
);

CREATE INDEX p_connector_name_trading_pair_timestamp_index ON life_recorder."Position" (connector_name, trading_pair, timestamp);

CREATE INDEX p_controller_id_timestamp_index ON life_recorder."Position" (controller_id, timestamp);

CREATE TABLE life_recorder."RangePositionCollectedFees" (
    id SERIAL NOT NULL,
    config_file_path TEXT NOT NULL,
    strategy TEXT NOT NULL,
    token_id INTEGER NOT NULL,
    token_0 TEXT NOT NULL,
    token_1 TEXT NOT NULL,
    claimed_fee_0 FLOAT NOT NULL,
    claimed_fee_1 FLOAT NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX rpf_id_index ON life_recorder."RangePositionCollectedFees" (token_id, config_file_path);

CREATE TABLE life_recorder."RangePositionUpdate" (
    id SERIAL NOT NULL,
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
    PRIMARY KEY (id)
);

CREATE INDEX rpu_config_file_index ON life_recorder."RangePositionUpdate" (config_file_path, timestamp);

CREATE INDEX rpu_position_index ON life_recorder."RangePositionUpdate" (position_address);

CREATE INDEX rpu_timestamp_index ON life_recorder."RangePositionUpdate" (hb_id, timestamp);

CREATE TABLE life_recorder."TradeFill" (
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
    PRIMARY KEY (market, order_id, exchange_trade_id)
);

CREATE INDEX tf_config_timestamp_index ON life_recorder."TradeFill" (config_file_path, timestamp);

CREATE INDEX tf_market_base_asset_timestamp_index ON life_recorder."TradeFill" (market, base_asset, timestamp);

CREATE INDEX tf_market_quote_asset_timestamp_index ON life_recorder."TradeFill" (market, quote_asset, timestamp);

CREATE INDEX tf_market_trading_pair_timestamp_index ON life_recorder."TradeFill" (market, symbol, timestamp);

INSERT INTO life_recorder."Metadata" (key, value) VALUES ('local_db_version', '20230516');

REVOKE ALL ON ALL TABLES IN SCHEMA life_recorder FROM PUBLIC;

REVOKE ALL ON ALL SEQUENCES IN SCHEMA life_recorder FROM PUBLIC;
