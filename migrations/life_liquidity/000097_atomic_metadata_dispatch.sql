-- Single-action dispatch captures its verified cut under the account lock.
-- The caller supplies only the original confirmed CLAIM's successor version.
-- No financial/source verdict is reused between commands. The private core
-- receives only the view produced within this same command; PREPARE, ARM and
-- deferred physical-COMMIT proofs remain independent.
DO $$ DECLARE p record;definition text;needle text;replacement text;r record;BEGIN
    IF to_regprocedure('life_financial.spot_action_with_verified_view(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb,jsonb)') IS NOT NULL
        OR to_regprocedure('life_financial.spot_dispatch_claimed_action(uuid,jsonb,jsonb,bigint,life_financial.digest,jsonb)') IS NOT NULL THEN
        RAISE EXCEPTION 'LIFE_ATOMIC_DISPATCH_MIGRATION_MISMATCH';END IF;
    SELECT f.*,l.lanname INTO STRICT p FROM pg_proc f JOIN pg_language l ON l.oid=f.prolang
        WHERE f.oid='life_financial.spot_action_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)'::regprocedure;
    IF p.lanname<>'plpgsql' OR p.provolatile<>'v' OR NOT p.prosecdef OR p.proisstrict OR p.proleakproof OR p.proparallel<>'u'
        OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
        OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') IS DISTINCT FROM 'e4e24eba88c8ca49df9f75ebad9ec4e678ef0a5307864f7dd4142d66f6f2286e' THEN
        RAISE EXCEPTION 'LIFE_ATOMIC_DISPATCH_MIGRATION_MISMATCH';END IF;
    definition:=pg_get_functiondef(p.oid);
    needle:='CREATE OR REPLACE FUNCTION life_financial.spot_action_command(p_account uuid, p_binding jsonb, p_owner jsonb, p_version bigint, p_cut life_financial.digest, p_fingerprint life_financial.digest, p_kind text, p_command jsonb)';
    replacement:='CREATE FUNCTION life_financial.spot_action_with_verified_view(p_account uuid, p_binding jsonb, p_owner jsonb, p_version bigint, p_cut life_financial.digest, p_fingerprint life_financial.digest, p_kind text, p_command jsonb, p_verified_view jsonb)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1
        OR (length(definition)-length(replace(definition,'view:=life_financial.spot_state(p_account);','')))
            /length('view:=life_financial.spot_state(p_account);')<>1 THEN
        RAISE EXCEPTION 'LIFE_ATOMIC_DISPATCH_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(replace(definition,needle,replacement),
        'view:=life_financial.spot_state(p_account);','view:=COALESCE(p_verified_view,life_financial.spot_state(p_account));');
    -- Revoke grants introduced by deployment-specific default privileges too.
    FOR r IN SELECT DISTINCT x.grantee FROM pg_proc f
        CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
        WHERE f.oid='life_financial.spot_action_with_verified_view(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb,jsonb)'::regprocedure
        AND x.grantee<>f.proowner LOOP
        IF r.grantee=0 THEN
            REVOKE ALL ON FUNCTION life_financial.spot_action_with_verified_view(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb,jsonb) FROM PUBLIC;
        ELSE
            EXECUTE format('REVOKE ALL ON FUNCTION life_financial.spot_action_with_verified_view(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb,jsonb) FROM %I',pg_get_userbyid(r.grantee));
        END IF;
    END LOOP;
END; $$;

CREATE FUNCTION life_financial.spot_dispatch_claimed_action(p_account uuid, p_binding jsonb, p_owner jsonb, p_version bigint, p_fingerprint life_financial.digest, p_command jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE view jsonb;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR p_version IS NULL OR p_version<=0 OR p_fingerprint IS NULL
        OR p_command IS NULL OR jsonb_typeof(p_command) IS DISTINCT FROM 'object'
        OR octet_length(p_command::text)>1048576
        OR (SELECT count(*) FROM jsonb_object_keys(p_command))<>5
        OR NOT p_command ?& ARRAY['operation_id','action','expected_state','state','facts']
        OR jsonb_typeof(p_command->'operation_id') IS DISTINCT FROM 'string' OR length(p_command->>'operation_id')>96
        OR p_command->>'expected_state' IS DISTINCT FROM 'PROPOSED'
        OR p_command->>'state' IS DISTINCT FROM 'DISPATCHED' THEN
        RAISE EXCEPTION 'LIFE_ACTION_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
    PERFORM 1 FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    -- Exact replay remains bookkeeping, including after expiry. The unchanged
    -- core verifies permanent request identity before returning that receipt.
    IF NOT EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=p_account AND operation_id=p_command->>'operation_id') THEN
        PERFORM life_financial.guard_account(p_account,p_binding,p_owner,p_version);
        PERFORM life_financial.guard_spot_session(p_account);
        view:=life_financial.spot_state(p_account);
    END IF;
    RETURN life_financial.spot_action_with_verified_view(p_account,p_binding,p_owner,p_version,
        COALESCE(view->>'digest',repeat('0',64))::life_financial.digest,p_fingerprint,'SPOT_ACTION_TRANSITION',p_command,view);
END; $$;

DO $$ DECLARE r record;BEGIN
    FOR r IN SELECT DISTINCT x.grantee FROM pg_proc f
        CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
        WHERE f.oid='life_financial.spot_dispatch_claimed_action(uuid,jsonb,jsonb,bigint,life_financial.digest,jsonb)'::regprocedure
        AND x.grantee<>f.proowner LOOP
        IF r.grantee=0 THEN
            REVOKE ALL ON FUNCTION life_financial.spot_dispatch_claimed_action(uuid,jsonb,jsonb,bigint,life_financial.digest,jsonb) FROM PUBLIC;
        ELSE
            EXECUTE format('REVOKE ALL ON FUNCTION life_financial.spot_dispatch_claimed_action(uuid,jsonb,jsonb,bigint,life_financial.digest,jsonb) FROM %I',pg_get_userbyid(r.grantee));
        END IF;
    END LOOP;
    -- Existing explicitly authorized writers receive the equivalent metadata
    -- entrypoint on automatic upgrade. Never propagate a PUBLIC/default grant
    -- or expose the internal view-taking helper. Account access is still
    -- checked by the entrypoint and freshly checked by runtime admission.
    FOR r IN SELECT DISTINCT x.grantee FROM pg_proc f
        CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
        WHERE f.oid='life_financial.spot_action_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)'::regprocedure
        AND x.grantee<>0 AND x.grantee<>f.proowner AND x.privilege_type='EXECUTE' LOOP
        EXECUTE format('GRANT EXECUTE ON FUNCTION life_financial.spot_dispatch_claimed_action(uuid,jsonb,jsonb,bigint,life_financial.digest,jsonb) TO %I',pg_get_userbyid(r.grantee));
    END LOOP;
END; $$;
