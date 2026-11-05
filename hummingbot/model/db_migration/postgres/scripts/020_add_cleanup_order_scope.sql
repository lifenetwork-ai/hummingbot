ALTER TABLE orders ADD COLUMN IF NOT EXISTS cleanup_operation_id TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS cleanup_scope JSONB;
CREATE INDEX IF NOT EXISTS idx_orders_cleanup_operation_id ON orders (cleanup_operation_id);
