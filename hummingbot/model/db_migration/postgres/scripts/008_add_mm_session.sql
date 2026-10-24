-- Current MM session; replay preserves its saved draw, anchors and deadlines.
ALTER TABLE bot_state ADD COLUMN IF NOT EXISTS mm_session JSONB;
