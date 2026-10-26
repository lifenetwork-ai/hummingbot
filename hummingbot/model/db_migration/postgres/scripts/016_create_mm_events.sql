-- Transition history starts here; do not invent older random-step/config history.
CREATE SEQUENCE IF NOT EXISTS seq_mm_events_id AS BIGINT;
CREATE TABLE IF NOT EXISTS mm_events (
    id BIGINT NOT NULL DEFAULT nextval('seq_mm_events_id'::regclass),
    bot_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    event_key TEXT NOT NULL,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,
    principal TEXT,
    generation BIGINT NOT NULL,
    control_version BIGINT NOT NULL,
    old_profile TEXT,
    new_profile TEXT,
    payload JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_mm_events PRIMARY KEY (id),
    CONSTRAINT fk_mm_events_bot_id FOREIGN KEY (bot_id) REFERENCES bot_state (bot_id),
    CONSTRAINT uq_mm_events_event_key UNIQUE (event_key)
);
ALTER SEQUENCE seq_mm_events_id OWNED BY mm_events.id;
CREATE INDEX IF NOT EXISTS idx_mm_events_bot_id_session_id_id ON mm_events (bot_id, session_id, id);
CREATE INDEX IF NOT EXISTS idx_mm_events_bot_id_session_id_event_type_id ON mm_events (bot_id, session_id, event_type, id);
CREATE INDEX IF NOT EXISTS idx_mm_events_bot_id_session_id_source_id ON mm_events (bot_id, session_id, source, id);
