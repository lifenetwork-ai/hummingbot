CREATE TABLE IF NOT EXISTS exchange_credentials (
    bot_id TEXT NOT NULL,
    exchange TEXT NOT NULL,
    connector TEXT NOT NULL,
    environment TEXT NOT NULL,
    version BIGINT NOT NULL,
    account_id TEXT NOT NULL,
    state TEXT NOT NULL,
    envelope JSONB,
    changed_by TEXT NOT NULL,
    changed_at TIMESTAMP WITH TIME ZONE NOT NULL,
    operation_id TEXT,
    CONSTRAINT pk_exchange_credentials PRIMARY KEY (bot_id, exchange, connector, environment),
    CONSTRAINT fk_exchange_credentials_bot_id FOREIGN KEY (bot_id) REFERENCES bot_state (bot_id),
    CONSTRAINT chk_exchange_credentials_version CHECK (version > 0),
    CONSTRAINT chk_exchange_credentials_state CHECK (state IN ('active', 'revoking', 'revoked')),
    CONSTRAINT chk_exchange_credentials_envelope CHECK (
        (state = 'revoked' AND envelope IS NULL) OR (state != 'revoked' AND envelope IS NOT NULL)
    )
);
