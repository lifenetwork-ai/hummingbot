-- Preserve an explicit distinction between a provisional order and its create ack.
ALTER TABLE public.orders ADD COLUMN IF NOT EXISTS creation_pending BOOLEAN NOT NULL DEFAULT false;
