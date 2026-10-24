-- Initial canonical schema. Replay preserves existing rows.
-- Transactions and migration locking are owned by the Python runner.


CREATE TABLE IF NOT EXISTS public.bot_state (
	bot_id TEXT NOT NULL,
	config_file_path TEXT,
	config_type TEXT,
	desired_state TEXT DEFAULT 'stopped' NOT NULL,
	control_version BIGINT DEFAULT 0 NOT NULL,
	acknowledged_version BIGINT DEFAULT 0 NOT NULL,
	owner_uuid TEXT,
	owner_generation BIGINT DEFAULT 0 NOT NULL,
	heartbeat TIMESTAMP WITH TIME ZONE,
	status JSONB,
	revision TEXT,
	CONSTRAINT pk_bot_state PRIMARY KEY (bot_id),
	CONSTRAINT chk_bot_state_desired_state CHECK (desired_state IN ('stopped', 'running'))
);
