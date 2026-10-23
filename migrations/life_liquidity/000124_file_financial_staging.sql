-- Owner-only relational financial evidence. No native account/authority seed.
CREATE TABLE life_storage.file_financial_rows (
    operation_id text NOT NULL REFERENCES life_storage.file_imports(operation_id),
    sequence integer NOT NULL CHECK(sequence>=0),
    family text NOT NULL CHECK(family ~ '^[A-Z_]{1,40}$'),
    identity text NOT NULL CHECK(length(identity) BETWEEN 1 AND 256),
    body bytea NOT NULL CHECK(octet_length(body) BETWEEN 1 AND 1048576),
    sha256 life_financial.digest NOT NULL CHECK(sha256=encode(sha256(body),'hex')),
    PRIMARY KEY(operation_id,sequence), UNIQUE(operation_id,family,identity)
);
CREATE TABLE life_storage.file_financial_seals (
    operation_id text PRIMARY KEY REFERENCES life_storage.file_imports(operation_id),
    manifest_sha256 life_financial.digest NOT NULL,
    row_count integer NOT NULL CHECK(row_count BETWEEN 1 AND 1000000),
    total_bytes bigint NOT NULL CHECK(total_bytes BETWEEN 1 AND 67108864)
);
CREATE TRIGGER immutable_financial_row BEFORE UPDATE OR DELETE ON life_storage.file_financial_rows
    FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record();
CREATE TRIGGER immutable_financial_seal BEFORE UPDATE OR DELETE ON life_storage.file_financial_seals
    FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record();
DO $$ DECLARE r record;BEGIN
    FOR r IN SELECT c.oid::regclass AS relation,x.grantee FROM pg_class c
        CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) x
        WHERE c.oid IN ('life_storage.file_financial_rows'::regclass,'life_storage.file_financial_seals'::regclass)
            AND x.grantee<>c.relowner LOOP
        IF r.grantee=0 THEN EXECUTE format('REVOKE ALL ON %s FROM PUBLIC',r.relation);
        ELSE EXECUTE format('REVOKE ALL ON %s FROM %I',r.relation,pg_get_userbyid(r.grantee));END IF;
    END LOOP;
END; $$;
