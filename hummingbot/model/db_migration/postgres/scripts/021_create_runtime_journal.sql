-- Additive only: no invented application markers for legacy fills/executors.
ALTER TABLE orders ADD COLUMN IF NOT EXISTS position_applied BOOLEAN NOT NULL DEFAULT false;
CREATE SEQUENCE IF NOT EXISTS seq_runtime_journal_id AS BIGINT;
CREATE TABLE IF NOT EXISTS runtime_journal (
    id BIGINT NOT NULL DEFAULT nextval('seq_runtime_journal_id'::regclass),
    event_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    order_id TEXT,
    applied BOOLEAN NOT NULL,
    payload JSONB NOT NULL,
    CONSTRAINT pk_runtime_journal PRIMARY KEY (id),
    CONSTRAINT uq_runtime_journal_event_key UNIQUE (event_key)
);
ALTER SEQUENCE seq_runtime_journal_id OWNED BY runtime_journal.id;
CREATE INDEX IF NOT EXISTS idx_runtime_journal_kind_applied_id ON runtime_journal (kind, applied, id);
CREATE INDEX IF NOT EXISTS idx_runtime_journal_order_id_id ON runtime_journal (order_id, id);
CREATE TABLE IF NOT EXISTS submission_attempts (
    order_id TEXT NOT NULL,
    state TEXT NOT NULL,
    context JSONB NOT NULL,
    payload_hash TEXT,
    deadline_ms BIGINT,
    receipt JSONB,
    CONSTRAINT pk_submission_attempts PRIMARY KEY (order_id)
);
CREATE INDEX IF NOT EXISTS idx_submission_attempts_state_order_id ON submission_attempts (state, order_id);
CREATE TABLE IF NOT EXISTS runtime_checkpoints (
    config_file_path TEXT NOT NULL,
    financial_hash TEXT NOT NULL,
    bindings_hash TEXT NOT NULL,
    watermark BIGINT NOT NULL,
    payload JSONB NOT NULL,
    CONSTRAINT pk_runtime_checkpoints PRIMARY KEY (config_file_path)
);
