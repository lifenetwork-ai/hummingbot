-- Global keyset pages use the same order across spot and perpetual.
-- PostgreSQL can scan these indexes backward for descending pagination.
CREATE INDEX IF NOT EXISTS idx_orders_creation_timestamp_id
    ON orders (creation_timestamp, id);

CREATE INDEX IF NOT EXISTS idx_trade_fills_timestamp_market_order_id_exchange_trade_id
    ON trade_fills (timestamp, market, order_id, exchange_trade_id);
