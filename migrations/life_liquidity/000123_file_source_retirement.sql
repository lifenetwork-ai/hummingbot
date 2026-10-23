-- Owner-only source retirement audit. No account initialization or activation.
CREATE TABLE life_storage.file_import_retirements (
    operation_id text PRIMARY KEY REFERENCES life_storage.file_imports(operation_id),
    checkpoint bytea NOT NULL CHECK (octet_length(checkpoint) BETWEEN 1 AND 67108864),
    checkpoint_sha256 life_financial.digest NOT NULL UNIQUE
        CHECK (checkpoint_sha256=encode(sha256(checkpoint),'hex')),
    verification bytea NOT NULL CHECK (octet_length(verification) BETWEEN 1 AND 1048576),
    verification_sha256 life_financial.digest NOT NULL
        CHECK (verification_sha256=encode(sha256(verification),'hex')),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER immutable_retirement BEFORE UPDATE OR DELETE ON life_storage.file_import_retirements
    FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record();
-- A hostile inherited default ACL must not make source authority worker-owned.
DO $$ DECLARE r record;BEGIN
    FOR r IN SELECT x.grantee FROM pg_class c
        CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) x
        WHERE c.oid='life_storage.file_import_retirements'::regclass AND x.grantee<>c.relowner LOOP
        IF r.grantee=0 THEN REVOKE ALL ON life_storage.file_import_retirements FROM PUBLIC;
        ELSE EXECUTE format('REVOKE ALL ON life_storage.file_import_retirements FROM %I',pg_get_userbyid(r.grantee));END IF;
    END LOOP;
END; $$;
