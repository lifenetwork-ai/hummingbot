-- Latest actual account observation per local reporting day; no synthetic gaps.
CREATE TABLE IF NOT EXISTS daily_balance_snapshots (
    account_id TEXT NOT NULL,
    environment TEXT NOT NULL,
    currency TEXT NOT NULL,
    reporting_timezone TEXT NOT NULL,
    day DATE NOT NULL,
    observed_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    connector TEXT NOT NULL,
    source TEXT NOT NULL,
    cash_balance NUMERIC(38, 18),
    available_cash NUMERIC(38, 18),
    equity NUMERIC(38, 18),
    available_equity NUMERIC(38, 18),
    CONSTRAINT pk_daily_balance_snapshots PRIMARY KEY (account_id, environment, currency, reporting_timezone, day)
);
