CREATE TABLE session_cash_baselines (
    bot_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    environment TEXT NOT NULL,
    balances JSONB NOT NULL,
    CONSTRAINT pk_session_cash_baselines PRIMARY KEY (bot_id, session_id, account_id, environment)
);

CREATE INDEX idx_daily_balance_snapshots_scope_day
    ON daily_balance_snapshots (account_id, environment, reporting_timezone, day);
