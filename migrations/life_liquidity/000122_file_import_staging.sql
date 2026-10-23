-- Owner-only, resumable artifact transport. Sealing never creates an account,
-- replaces financial history, retires credentials or grants trading authority.
CREATE TABLE life_storage.file_imports (
    operation_id text PRIMARY KEY CHECK (operation_id ~ '^[A-Za-z0-9_.:-]{1,96}$'),
    account_id uuid NOT NULL UNIQUE,
    -- Historical target identity remains unchanged in a quarantined restore.
    database_lineage uuid NOT NULL,
    source_sha256 life_financial.digest NOT NULL UNIQUE,
    scope_sha256 life_financial.digest NOT NULL UNIQUE,
    request jsonb NOT NULL CHECK (jsonb_typeof(request)='object' AND octet_length(request::text)<=16384),
    artifact_bytes bigint NOT NULL CHECK (artifact_bytes>0 AND artifact_bytes<=268500992),
    chunk_bytes integer NOT NULL CHECK (chunk_bytes>0 AND chunk_bytes<=1048576),
    state text NOT NULL CHECK (state IN ('IMPORTING','SEALED','DISCARDED')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE life_storage.file_import_chunks (
    operation_id text NOT NULL REFERENCES life_storage.file_imports(operation_id),
    sequence integer NOT NULL CHECK (sequence>=0),
    data bytea NOT NULL CHECK (octet_length(data)>0 AND octet_length(data)<=1048576),
    sha256 life_financial.digest NOT NULL CHECK (sha256=encode(sha256(data),'hex')),
    PRIMARY KEY(operation_id,sequence)
);
CREATE TRIGGER immutable_chunk BEFORE UPDATE OR DELETE ON life_storage.file_import_chunks
    FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record();
CREATE FUNCTION life_storage.file_import_transition() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
    IF TG_OP='DELETE' OR (to_jsonb(OLD)-'state') IS DISTINCT FROM (to_jsonb(NEW)-'state')
        OR (OLD.state='SEALED' AND NEW.state NOT IN ('SEALED','DISCARDED'))
        OR (OLD.state='DISCARDED' AND NEW.state<>'DISCARDED') THEN
        RAISE EXCEPTION 'LIFE_FILE_IMPORT_IMMUTABLE' USING ERRCODE='P0001';
    END IF;
    RETURN NEW;
END; $$;
CREATE TRIGGER immutable_import BEFORE UPDATE OR DELETE ON life_storage.file_imports
    FOR EACH ROW EXECUTE FUNCTION life_storage.file_import_transition();
REVOKE ALL ON FUNCTION life_storage.file_import_transition() FROM PUBLIC;
-- Do not inherit even deliberately hostile runtime/reporting default ACLs.
DO $$ DECLARE r record;BEGIN
    FOR r IN SELECT c.oid::regclass AS relation,c.relowner,x.grantee FROM pg_class c
        CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) x
        WHERE c.oid IN ('life_storage.file_imports'::regclass,'life_storage.file_import_chunks'::regclass)
            AND x.grantee<>c.relowner LOOP
        IF r.grantee=0 THEN EXECUTE format('REVOKE ALL ON %s FROM PUBLIC',r.relation);
        ELSE EXECUTE format('REVOKE ALL ON %s FROM %I',r.relation,pg_get_userbyid(r.grantee));END IF;
    END LOOP;
    FOR r IN SELECT x.grantee,p.proowner FROM pg_proc p
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE p.oid='life_storage.file_import_transition()'::regprocedure AND x.grantee<>p.proowner LOOP
        IF r.grantee=0 THEN REVOKE ALL ON FUNCTION life_storage.file_import_transition() FROM PUBLIC;
        ELSE EXECUTE format('REVOKE ALL ON FUNCTION life_storage.file_import_transition() FROM %I',pg_get_userbyid(r.grantee));END IF;
    END LOOP;
END; $$;
