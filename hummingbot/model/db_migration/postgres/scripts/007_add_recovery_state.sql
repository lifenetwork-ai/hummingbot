-- Persist fail-closed recovery across process death and revision changes.
ALTER TABLE public.bot_state ADD COLUMN IF NOT EXISTS recovery_state JSONB NOT NULL DEFAULT '{}'::jsonb;
