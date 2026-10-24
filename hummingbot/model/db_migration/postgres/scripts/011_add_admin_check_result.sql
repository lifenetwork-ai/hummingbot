-- Observational checks share retry receipts but do not alter trading authority.
ALTER TABLE admin_commands ALTER COLUMN session_id DROP NOT NULL;
ALTER TABLE admin_commands ADD COLUMN IF NOT EXISTS result JSONB;
