-- Session discovery skips the much larger guard/transition stream.
CREATE INDEX IF NOT EXISTS idx_mm_events_bot_id_event_type_id
    ON mm_events (bot_id, event_type, id);
