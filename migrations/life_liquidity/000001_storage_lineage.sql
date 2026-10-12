-- Infrastructure only: no accounts, capital, ownership grants or trading state.
-- An existing unregistered schema is an error, not an empty-state bootstrap.
CREATE SCHEMA life_storage;
REVOKE ALL ON SCHEMA life_storage FROM PUBLIC;
CREATE TABLE life_storage.storage_metadata (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    lineage uuid NOT NULL UNIQUE DEFAULT pg_catalog.gen_random_uuid(),
    format_version integer NOT NULL CHECK (format_version = 1),
    created_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp()
);
INSERT INTO life_storage.storage_metadata (format_version) VALUES (1);
REVOKE ALL ON life_storage.storage_metadata FROM PUBLIC;
