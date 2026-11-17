-- Reporting-only and additive. No financial checkpoint/backfill work at startup.
CREATE TABLE IF NOT EXISTS overview_fee_scopes (
    scope_key TEXT NOT NULL,
    payload JSONB NOT NULL,
    CONSTRAINT pk_overview_fee_scopes PRIMARY KEY (scope_key)
);
CREATE TABLE IF NOT EXISTS overview_fee_contributions (
    scope_key TEXT NOT NULL,
    source_market TEXT NOT NULL,
    order_id TEXT NOT NULL,
    exchange_trade_id TEXT NOT NULL,
    market TEXT NOT NULL,
    quality TEXT NOT NULL,
    fee_usdt NUMERIC,
    CONSTRAINT pk_overview_fee_contributions PRIMARY KEY (scope_key, source_market, order_id, exchange_trade_id)
);
CREATE INDEX IF NOT EXISTS idx_submission_attempts_overview_scope_order
    ON submission_attempts ((context->>'bot_id'), (context->'session'->>0), order_id);
CREATE INDEX IF NOT EXISTS idx_trade_fills_overview_order_market_trade
    ON trade_fills (order_id, market, exchange_trade_id);
