-- Owner-only signed bridge from a verified restore to observation recovery.
-- No account, opening balance, lease or activation is seeded by migration.
DO $$ DECLARE p pg_proc;BEGIN
    SELECT * INTO STRICT p FROM pg_proc WHERE oid='life_financial.can_access(uuid,boolean)'::regprocedure;
    IF p.proowner<>(SELECT nspowner FROM pg_namespace WHERE nspname='life_financial')
        OR NOT p.prosecdef OR p.provolatile<>'s' OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
        OR p.prolang<>(SELECT oid FROM pg_language WHERE lanname='sql')
        OR btrim(regexp_replace(p.prosrc,'\s+',' ','g'))<>
            'SELECT EXISTS (SELECT 1 FROM life_financial.access_bindings WHERE principal=session_user AND account_id=p_account AND (NOT p_write OR can_write)) AND (NOT p_write OR EXISTS(SELECT 1 FROM life_storage.storage_metadata WHERE restore_operation_id IS NULL));' THEN
        RAISE EXCEPTION 'LIFE_RESTORE_RECOVERY_UNKNOWN_ACCESS_PREDECESSOR' USING ERRCODE='P0001';
    END IF;
END; $$;
CREATE TABLE life_storage.restore_recovery_imports (
    restore_operation text NOT NULL REFERENCES life_storage.restore_history(operation_id),
    account_id uuid NOT NULL REFERENCES life_financial.accounts(account_id),
    control_operation text NOT NULL,
    body jsonb NOT NULL CHECK(jsonb_typeof(body)='object' AND octet_length(body::text)<=131072),
    source_hash life_financial.digest NOT NULL
        CHECK(source_hash=encode(sha256(convert_to(body::text,'UTF8')),'hex')),
    registered_at timestamptz NOT NULL,
    PRIMARY KEY(restore_operation,account_id),
    UNIQUE(account_id,control_operation),
    FOREIGN KEY(account_id,control_operation) REFERENCES life_financial.control_records(account_id,operation_id)
);
CREATE TRIGGER immutable_restore_recovery BEFORE UPDATE OR DELETE ON life_storage.restore_recovery_imports
    FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record();

-- Preserve the original function OID and ACL: existing compiled kernels must
-- all see this additional origin guard. Runtime cannot read owner-only audit.
CREATE OR REPLACE FUNCTION life_financial.can_access(p_account uuid,p_write boolean DEFAULT false)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT EXISTS (SELECT 1 FROM life_financial.access_bindings
        WHERE principal=session_user AND account_id=p_account AND (NOT p_write OR can_write))
        AND (NOT p_write OR EXISTS(SELECT 1 FROM life_storage.storage_metadata WHERE restore_operation_id IS NULL))
        AND (
            NOT EXISTS(SELECT 1 FROM life_storage.restore_history h
                JOIN life_storage.storage_metadata m ON m.lineage=h.target_lineage)
            OR EXISTS(SELECT 1 FROM life_storage.restore_recovery_imports i
                JOIN life_storage.restore_history h ON h.operation_id=i.restore_operation
                JOIN life_storage.storage_metadata m ON m.lineage=h.target_lineage
                WHERE i.account_id=p_account AND i.body->'context'->'restore'->>'target_lineage'=m.lineage::text)
            OR (NOT p_write AND session_user=(SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname='life_financial')
                AND NOT (SELECT datallowconn FROM pg_database WHERE datname=current_database())
                AND NOT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datid=(SELECT oid FROM pg_database WHERE datname=current_database()) AND pid<>pg_backend_pid())
                AND NOT EXISTS(SELECT 1 FROM pg_prepared_xacts WHERE database=current_database()))
        );
$$;

CREATE FUNCTION life_storage.restore_recovery_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a jsonb;h life_storage.restore_history;b jsonb:=NEW.body;t timestamptz:=clock_timestamp();p life_financial.control_policies;
BEGIN
    SELECT * INTO STRICT h FROM life_storage.restore_history WHERE operation_id=NEW.restore_operation;
    SELECT to_jsonb(x) INTO STRICT a FROM life_financial.accounts x WHERE account_id=NEW.account_id;
    SELECT * INTO STRICT p FROM life_financial.control_policies WHERE account_id=NEW.account_id;
    IF session_user<>(SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname='life_storage')
        OR (SELECT count(*) FROM life_financial.accounts)<>1
        OR (SELECT datallowconn FROM pg_database WHERE datname=current_database())
        OR EXISTS(SELECT 1 FROM pg_stat_activity WHERE datid=(SELECT oid FROM pg_database WHERE datname=current_database()) AND pid<>pg_backend_pid())
        OR EXISTS(SELECT 1 FROM pg_prepared_xacts WHERE database=current_database())
        OR NOT EXISTS(SELECT 1 FROM life_storage.storage_metadata WHERE lineage=h.target_lineage AND restore_operation_id IS NULL)
        OR b->'context'->'restore' IS DISTINCT FROM to_jsonb(h)-'before_accounts'-'restored_at'
        OR b->'context'->>'account_id' IS DISTINCT FROM NEW.account_id::text
        OR b->'receipt'->>'control_operation' IS DISTINCT FROM NEW.control_operation
        OR b->'receipt'->>'trading_permission' IS DISTINCT FROM 'false'
        OR a->>'mode'<>'RECOVERY_ONLY'
        OR a->>'database_lineage'<>h.target_lineage::text
        OR (a->>'risk_epoch')::bigint<>(b->'context'->'target_account'->>'risk_epoch')::bigint
        OR life_financial.control_owner(a) IS DISTINCT FROM
            (life_financial.control_owner(b->'context'->'source_account')||jsonb_build_object('mode','RECOVERY_ONLY'))
        OR NOT life_financial.control_verified(NEW.account_id)
        OR NOT EXISTS(SELECT 1 FROM life_financial.control_records r
            WHERE r.account_id=NEW.account_id AND r.operation_id=NEW.control_operation
                AND r.body->'after_account'=a AND r.body->'payload'->>'mode'='RECOVERY_ONLY'
                AND r.body->'grant_id'='null'::jsonb)
        OR b->'operator_policy' IS DISTINCT FROM (SELECT body->'operator_verifier' FROM life_financial.configurations WHERE account_id=NEW.account_id AND config_hash=a->>'config_hash')
        OR b->'credential_policy' IS DISTINCT FROM (SELECT body->'credential_authority' FROM life_financial.configurations WHERE account_id=NEW.account_id AND config_hash=a->>'config_hash')
        OR (b->'facts'->>'observed_at')::timestamptz>t
        OR t-(b->'facts'->>'observed_at')::timestamptz>p.max_fact_age_ms*interval '1 millisecond'
        OR NOT ((b->'approval'->>'issued_at')::timestamptz<=t AND t<(b->'approval'->>'expires_at')::timestamptz)
        OR NOT ((b->'exclusion'->'body'->>'observed_at')::timestamptz<=t AND t<(b->'exclusion'->'body'->>'expires_at')::timestamptz)
        OR NOT EXISTS(SELECT 1 FROM life_financial.access_bindings WHERE account_id=NEW.account_id AND principal=b->'request'->'registration'->>'principal' AND can_write)
        OR EXISTS(SELECT 1 FROM life_financial.access_bindings WHERE account_id=NEW.account_id AND can_write
            AND principal NOT IN (session_user,b->'request'->'registration'->>'principal')) THEN
        RAISE EXCEPTION 'LIFE_RESTORE_RECOVERY_COMMIT_UNQUALIFIED' USING ERRCODE='P0001';
    END IF;
    PERFORM life_financial.control_commit_check(NEW.account_id,NEW.control_operation);
    RETURN NEW;
END; $$;
CREATE CONSTRAINT TRIGGER restore_recovery_physical_commit AFTER INSERT ON life_storage.restore_recovery_imports
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_storage.restore_recovery_commit_guard();

DO $$ DECLARE r record;BEGIN
    FOR r IN SELECT DISTINCT x.grantee FROM pg_class c
        CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) x
        WHERE c.oid='life_storage.restore_recovery_imports'::regclass AND x.grantee<>c.relowner LOOP
        IF r.grantee=0 THEN REVOKE ALL ON life_storage.restore_recovery_imports FROM PUBLIC;
        ELSE EXECUTE format('REVOKE ALL ON life_storage.restore_recovery_imports FROM %I',pg_get_userbyid(r.grantee));END IF;
    END LOOP;
    FOR r IN SELECT DISTINCT x.grantee FROM pg_proc p
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE p.oid='life_storage.restore_recovery_commit_guard()'::regprocedure AND x.grantee<>p.proowner LOOP
        IF r.grantee=0 THEN REVOKE ALL ON FUNCTION life_storage.restore_recovery_commit_guard() FROM PUBLIC;
        ELSE EXECUTE format('REVOKE ALL ON FUNCTION life_storage.restore_recovery_commit_guard() FROM %I',pg_get_userbyid(r.grantee));END IF;
    END LOOP;
END; $$;
