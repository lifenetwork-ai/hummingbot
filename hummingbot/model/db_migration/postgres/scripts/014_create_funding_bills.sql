-- Keep the original funding event table/file model intact. Bills have stable
-- exchange identities and exact original amounts; timestamps are UTC millis.
CREATE TABLE IF NOT EXISTS funding_bills (
    account_id TEXT NOT NULL,
    environment TEXT NOT NULL,
    bill_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timestamp BIGINT NOT NULL,
    currency TEXT NOT NULL,
    amount NUMERIC NOT NULL,
    rate NUMERIC,
    CONSTRAINT pk_funding_bills PRIMARY KEY (account_id, environment, bill_id)
);
CREATE INDEX IF NOT EXISTS idx_funding_bills_account_environment_timestamp_symbol
    ON funding_bills (account_id, environment, timestamp, symbol);
