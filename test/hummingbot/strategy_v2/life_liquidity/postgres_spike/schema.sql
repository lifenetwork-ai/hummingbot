-- Synthetic, single-account SPOT feasibility schema. Not a production migration.
CREATE TABLE account_state (
    account TEXT PRIMARY KEY,
    environment TEXT NOT NULL CHECK (environment = 'OFFLINE_SYNTHETIC_ONLY'),
    owner TEXT NOT NULL, lineage TEXT NOT NULL, epoch BIGINT NOT NULL,
    config_version BIGINT NOT NULL, risk_epoch BIGINT NOT NULL, session TEXT NOT NULL,
    armed BOOLEAN NOT NULL, expires_at TIMESTAMPTZ NOT NULL, observed_at TIMESTAMPTZ NOT NULL,
    version BIGINT NOT NULL DEFAULT 0,
    active_count INTEGER NOT NULL DEFAULT 0 CHECK (active_count BETWEEN 0 AND 32),
    cash NUMERIC(30,8) NOT NULL CHECK (cash >= 0 AND cash < 'Infinity'::numeric),
    base NUMERIC(30,8) NOT NULL CHECK (base >= 0 AND base < 'Infinity'::numeric),
    reserved_cash NUMERIC(30,8) NOT NULL DEFAULT 0 CHECK (reserved_cash >= 0),
    reserved_base NUMERIC(30,8) NOT NULL DEFAULT 0 CHECK (reserved_base >= 0),
    budget_spent NUMERIC(30,8) NOT NULL DEFAULT 0 CHECK (budget_spent >= 0),
    budget_reserved NUMERIC(30,8) NOT NULL DEFAULT 0 CHECK (budget_reserved >= 0),
    budget_limit NUMERIC(30,8) NOT NULL DEFAULT 100 CHECK (budget_limit > 0),
    CHECK (reserved_cash <= cash AND reserved_base <= base),
    CHECK (budget_spent + budget_reserved <= budget_limit)
);
CREATE TABLE attempts (
    account TEXT NOT NULL REFERENCES account_state(account),
    intent TEXT NOT NULL, wire TEXT NOT NULL, side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
    amount NUMERIC(30,8) NOT NULL CHECK (amount > 0 AND amount < 'Infinity'::numeric),
    price NUMERIC(30,8) NOT NULL CHECK (price > 0 AND price < 'Infinity'::numeric),
    hold_cash NUMERIC(30,8) NOT NULL CHECK (hold_cash >= 0),
    hold_base NUMERIC(30,8) NOT NULL CHECK (hold_base >= 0),
    hold_budget NUMERIC(30,8) NOT NULL CHECK (hold_budget >= 0),
    state TEXT NOT NULL CHECK (state IN ('PREPARED','SEND_UNKNOWN','ACKED','TERMINAL')),
    owner TEXT NOT NULL, lineage TEXT NOT NULL, epoch BIGINT NOT NULL,
    config_version BIGINT NOT NULL, risk_epoch BIGINT NOT NULL, session TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL, exchange_id TEXT,
    PRIMARY KEY (account, intent), UNIQUE (account, wire)
);
CREATE INDEX active_attempts ON attempts(account, intent)
    WHERE state IN ('PREPARED','SEND_UNKNOWN','ACKED');
CREATE TABLE events (
    account TEXT NOT NULL REFERENCES account_state(account),
    event_id TEXT NOT NULL, intent TEXT NOT NULL,
    kind TEXT NOT NULL, quote_delta NUMERIC(30,8) NOT NULL DEFAULT 0,
    base_delta NUMERIC(30,8) NOT NULL DEFAULT 0, budget_delta NUMERIC(30,8) NOT NULL DEFAULT 0,
    PRIMARY KEY (account, event_id)
);
