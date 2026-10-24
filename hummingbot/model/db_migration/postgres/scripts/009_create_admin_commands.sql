-- Retry identity and command outcome; replay never resets an existing receipt.
CREATE TABLE IF NOT EXISTS admin_commands (
    id TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    action TEXT NOT NULL,
    session_id TEXT NOT NULL,
    expected_version BIGINT NOT NULL,
    effective_version BIGINT NOT NULL,
    status TEXT NOT NULL,
    payload JSONB NOT NULL,
    error TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_admin_commands PRIMARY KEY (id),
    CONSTRAINT fk_admin_commands_bot_id FOREIGN KEY (bot_id) REFERENCES bot_state (bot_id),
    CONSTRAINT uq_admin_commands_bot_id_idempotency_key UNIQUE (bot_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_admin_commands_bot_id_created_at ON admin_commands (bot_id, created_at);
CREATE INDEX IF NOT EXISTS idx_admin_commands_bot_id_status_effective_version ON admin_commands (bot_id, status, effective_version);
