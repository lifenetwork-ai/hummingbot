-- Offline maintenance only. A restored primary is not a trading generation.
-- All original grants/leases/holds/receipts remain historical evidence; neither
-- migrations nor restore supplies an activation or a reconciliation bypass.
DO $$ DECLARE p pg_proc;BEGIN
    SELECT * INTO STRICT p FROM pg_proc WHERE oid='life_financial.can_access(uuid,boolean)'::regprocedure;
    IF p.proowner<>(SELECT nspowner FROM pg_namespace WHERE nspname='life_financial')
        OR NOT p.prosecdef OR p.provolatile<>'s' OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
        OR p.prolang<>(SELECT oid FROM pg_language WHERE lanname='sql')
        OR btrim(regexp_replace(p.prosrc,'\s+',' ','g'))<>
            'SELECT EXISTS (SELECT 1 FROM life_financial.access_bindings WHERE principal=session_user AND account_id=p_account AND (NOT p_write OR can_write));' THEN
        RAISE EXCEPTION 'LIFE_RESTORE_UNKNOWN_ACCESS_PREDECESSOR' USING ERRCODE='P0001';
    END IF;
END; $$;
ALTER TABLE life_storage.storage_metadata ADD COLUMN restore_operation_id text
    CHECK (restore_operation_id ~ '^[A-Za-z0-9_.:-]{1,96}$');
CREATE TABLE life_storage.restore_history (
    operation_id text PRIMARY KEY CHECK (operation_id ~ '^[A-Za-z0-9_.:-]{1,96}$'),
    source_lineage uuid NOT NULL,
    target_lineage uuid NOT NULL UNIQUE CHECK (target_lineage<>source_lineage),
    backup_sha256 life_financial.digest NOT NULL,
    manifest_sha256 life_financial.digest NOT NULL,
    before_accounts jsonb NOT NULL CHECK (jsonb_typeof(before_accounts)='array'),
    restored_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_storage.restore_history
    FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record();
-- Keep this FK checked at transaction end during an isolated owner-side rotate.
-- Runtime cannot change either side, and its existing immutable-account trigger
-- continues rejecting database/account identity changes.
ALTER TABLE life_financial.accounts ALTER CONSTRAINT accounts_database_lineage_fkey
    DEFERRABLE INITIALLY IMMEDIATE;
CREATE OR REPLACE FUNCTION life_financial.can_access(p_account uuid,p_write boolean DEFAULT false)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT EXISTS (SELECT 1 FROM life_financial.access_bindings
        WHERE principal=session_user AND account_id=p_account AND (NOT p_write OR can_write))
        AND (NOT p_write OR EXISTS(SELECT 1 FROM life_storage.storage_metadata WHERE restore_operation_id IS NULL));
$$;
-- Even hostile default privileges cannot grant runtime restore-table mutation.
DO $$ DECLARE r record;BEGIN
    FOR r IN SELECT DISTINCT x.grantee FROM pg_class c
        CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) x
        WHERE c.oid='life_storage.restore_history'::regclass AND x.grantee<>c.relowner LOOP
        IF r.grantee=0 THEN REVOKE ALL ON life_storage.restore_history FROM PUBLIC;
        ELSE EXECUTE format('REVOKE ALL ON life_storage.restore_history FROM %I',pg_get_userbyid(r.grantee));END IF;
    END LOOP;
END; $$;
