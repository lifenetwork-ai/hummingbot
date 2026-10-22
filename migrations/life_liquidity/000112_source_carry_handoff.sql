-- A signed source's capital write has already committed. Verify its exact
-- parent under the account lock, then compute the carry cut and consume it in
-- this same call. Never accept a Python-supplied audit verdict. The original
-- carry command and all deferred physical-COMMIT guards remain unchanged.
DO $$ DECLARE p record;definition text;needle text;replacement text;BEGIN
    SELECT f.*,l.lanname INTO STRICT p FROM pg_proc f JOIN pg_language l ON l.oid=f.prolang
        WHERE f.oid='life_financial.carry_check(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'::regprocedure;
    IF p.lanname<>'plpgsql' OR p.provolatile<>'v' OR NOT p.prosecdef OR p.proisstrict OR p.proleakproof OR p.proparallel<>'u'
        OR p.proretset OR p.prokind<>'f' OR p.pronargdefaults<>0 OR p.prorettype<>'jsonb'::regtype
        OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
        OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') IS DISTINCT FROM '71c22159ccb104c59c2c7201edae2d7b5335cf7aca7eab4eb1ec516126b0c126' THEN
        RAISE EXCEPTION 'LIFE_SOURCE_CARRY_MIGRATION_MISMATCH';END IF;
    definition:=pg_get_functiondef(p.oid);
    needle:='CREATE OR REPLACE FUNCTION life_financial.carry_check(p_account uuid, p_binding jsonb, p_owner jsonb, p_version bigint, p_cut life_financial.digest, p_fingerprint life_financial.digest, cmd jsonb)';
    replacement:='CREATE FUNCTION life_financial.carry_from_native_view(p_account uuid, p_binding jsonb, p_owner jsonb, p_version bigint, p_cut life_financial.digest, p_fingerprint life_financial.digest, cmd jsonb, p_native_view jsonb)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1
        OR (length(definition)-length(replace(definition,'view:=life_financial.spot_state(p_account);','')))
            /length('view:=life_financial.spot_state(p_account);')<>1 THEN
        RAISE EXCEPTION 'LIFE_SOURCE_CARRY_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(replace(definition,needle,replacement),
        'view:=life_financial.spot_state(p_account);','view:=p_native_view;');
END; $$;

CREATE FUNCTION life_financial.carry_after_capital(p_account uuid,p_binding jsonb,p_owner jsonb,p_source_version bigint,
    p_source_cut life_financial.digest,p_capital_id text,cmd jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts;cap life_financial.operations;view jsonb;request jsonb;result jsonb;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR p_source_version IS NULL OR p_source_version<0
        OR p_source_cut IS NULL OR p_capital_id IS NULL OR cmd IS NULL OR jsonb_typeof(cmd) IS DISTINCT FROM 'object'
        OR octet_length(cmd::text)>1048576 THEN
        RAISE EXCEPTION 'LIFE_CARRY_CAPITAL_HANDOFF_INVALID' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    SELECT o.* INTO cap FROM life_financial.shared_capital_heads h JOIN life_financial.operations o USING(account_id,operation_id)
        WHERE h.account_id=p_account AND h.operation_id=p_capital_id;
    IF cap.account_id IS NULL OR a.version<>p_source_version+1 OR cap.committed_version<>a.version
        OR cap.command->>'kind' IS DISTINCT FROM 'SHARED_CAPITAL'
        OR cap.command->'binding' IS DISTINCT FROM p_binding OR cap.command->'owner' IS DISTINCT FROM p_owner
        OR cap.command->'version' IS DISTINCT FROM to_jsonb(p_source_version)
        OR cap.command->>'cut' IS DISTINCT FROM p_source_cut
        OR cap.receipt->'replayed' IS DISTINCT FROM 'false'::jsonb
        OR (cap.receipt->>'version')::bigint IS DISTINCT FROM a.version THEN
        RAISE EXCEPTION 'LIFE_CARRY_CAPITAL_HANDOFF_INVALID' USING ERRCODE='P0001';END IF;
    -- A replay is diagnostic through carry_read, never a new source handoff.
    IF EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=p_account AND operation_id=cmd->>'operation_id') THEN
        RAISE EXCEPTION 'LIFE_ORIGINAL_OPERATION_REQUIRES_RECONCILIATION' USING ERRCODE='P0001';END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,a.version);
    view:=life_financial.spot_state(p_account);
    request:=jsonb_build_object('kind','CARRY_CHECK','binding',p_binding,'owner',p_owner,'version',a.version,'cut',view->>'digest','command',cmd);
    result:=life_financial.carry_from_native_view(p_account,p_binding,p_owner,a.version,(view->>'digest')::life_financial.digest,
        encode(sha256(convert_to(life_financial.canonical_json_v1(request),'UTF8')),'hex'),cmd,view);
    -- Returning an audited post-write cut avoids a second Python read. This is
    -- exposed only after physical COMMIT, never a recovery/send capability.
    view:=life_financial.spot_state(p_account);
    IF view->'unverified' IS DISTINCT FROM 'false'::jsonb OR (view#>>'{account,version}')::bigint<>p_source_version+2 THEN
        RAISE EXCEPTION 'LIFE_CARRY_UNVERIFIED' USING ERRCODE='P0001';END IF;
    RETURN result||jsonb_build_object('native_snapshot',view);
END; $$;

DO $$ DECLARE r record;sig text;BEGIN
    FOREACH sig IN ARRAY ARRAY[
        'life_financial.carry_from_native_view(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb,jsonb)',
        'life_financial.carry_after_capital(uuid,jsonb,jsonb,bigint,life_financial.digest,text,jsonb)'] LOOP
        FOR r IN SELECT DISTINCT x.grantee FROM pg_proc f
            CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
            WHERE f.oid=sig::regprocedure AND x.grantee<>f.proowner LOOP
            IF r.grantee=0 THEN EXECUTE 'REVOKE ALL ON FUNCTION '||sig||' FROM PUBLIC';
            ELSE EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',sig,pg_get_userbyid(r.grantee));END IF;
        END LOOP;
    END LOOP;
    FOR r IN SELECT DISTINCT x.grantee FROM pg_proc f
        CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
        WHERE f.oid='life_financial.carry_check(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'::regprocedure
        AND x.grantee<>0 AND x.grantee<>f.proowner AND x.privilege_type='EXECUTE' LOOP
        EXECUTE format('GRANT EXECUTE ON FUNCTION life_financial.carry_after_capital(uuid,jsonb,jsonb,bigint,life_financial.digest,text,jsonb) TO %I',pg_get_userbyid(r.grantee));
    END LOOP;
END; $$;
