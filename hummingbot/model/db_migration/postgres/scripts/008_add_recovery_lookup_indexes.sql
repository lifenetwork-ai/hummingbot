-- Recovery reads must not scan the full order/executor history on every send/start.
-- Additive and replayable; existing canonical revisions tolerate these indexes.
CREATE INDEX IF NOT EXISTS idx_orders_creation_pending_config_file_path_market
    ON public.orders (creation_pending, config_file_path, market);

CREATE INDEX IF NOT EXISTS idx_executors_controller_id
    ON public.executors (controller_id);
