CREATE TABLE IF NOT EXISTS admin_accounts (
    id TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    email TEXT NOT NULL,
    issuer TEXT,
    subject TEXT,
    role TEXT NOT NULL,
    active BOOLEAN NOT NULL,
    version BIGINT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_admin_accounts PRIMARY KEY (id),
    CONSTRAINT fk_admin_accounts_bot_id FOREIGN KEY (bot_id) REFERENCES bot_state (bot_id),
    CONSTRAINT uq_admin_accounts_bot_id_email UNIQUE (bot_id, email),
    CONSTRAINT uq_admin_accounts_bot_id_issuer_subject UNIQUE (bot_id, issuer, subject),
    CONSTRAINT chk_admin_accounts_role CHECK (role IN ('admin', 'write', 'read')),
    CONSTRAINT chk_admin_accounts_binding CHECK (
        (issuer IS NULL AND subject IS NULL) OR (issuer IS NOT NULL AND subject IS NOT NULL)
    )
);

CREATE SEQUENCE IF NOT EXISTS seq_admin_audit_events_id AS BIGINT;
CREATE TABLE IF NOT EXISTS admin_audit_events (
    id BIGINT NOT NULL DEFAULT nextval('seq_admin_audit_events_id'::regclass),
    bot_id TEXT NOT NULL,
    event_key TEXT NOT NULL,
    request_id TEXT NOT NULL,
    operation_id TEXT,
    actor_kind TEXT NOT NULL,
    actor_id TEXT,
    actor_email TEXT,
    actor_role TEXT,
    action TEXT NOT NULL,
    target_id TEXT,
    exchange TEXT,
    connector TEXT,
    environment TEXT,
    outcome TEXT NOT NULL,
    reason_code TEXT,
    details JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_admin_audit_events PRIMARY KEY (id),
    CONSTRAINT fk_admin_audit_events_bot_id FOREIGN KEY (bot_id) REFERENCES bot_state (bot_id),
    CONSTRAINT uq_admin_audit_events_event_key UNIQUE (event_key)
);
ALTER SEQUENCE seq_admin_audit_events_id OWNED BY admin_audit_events.id;
CREATE INDEX IF NOT EXISTS idx_admin_audit_events_bot_id_created_at_id ON admin_audit_events (bot_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_admin_audit_events_bot_id_operation_id_id ON admin_audit_events (bot_id, operation_id, id);
