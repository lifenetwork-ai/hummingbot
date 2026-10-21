-- Create a NEW V2 capture once inside its native CLAIM, after the original
-- owner/cut/full-state and metadata audits. Issued actions are never accepted
-- as templates. Existing publication, PREPARE, ARM and COMMIT gates stay intact.
DO $$ DECLARE p record;definition text;needle text;replacement text;stamp text;r record;BEGIN
    IF to_regprocedure('life_financial.spot_capture_quote_action(uuid,jsonb,jsonb,bigint,life_financial.digest,jsonb)') IS NOT NULL THEN
        RAISE EXCEPTION 'LIFE_NATIVE_CAPTURE_MIGRATION_MISMATCH';END IF;
    SELECT f.*,l.lanname INTO STRICT p FROM pg_proc f JOIN pg_language l ON l.oid=f.prolang
        WHERE f.oid='life_financial.spot_action_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)'::regprocedure;
    IF p.lanname<>'plpgsql' OR p.provolatile<>'v' OR NOT p.prosecdef OR p.proisstrict OR p.proleakproof OR p.proparallel<>'u'
        OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
        OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') IS DISTINCT FROM 'e4e24eba88c8ca49df9f75ebad9ec4e678ef0a5307864f7dd4142d66f6f2286e' THEN
        RAISE EXCEPTION 'LIFE_NATIVE_CAPTURE_MIGRATION_MISMATCH';END IF;
    definition:=pg_get_functiondef(p.oid);
    needle:='CREATE OR REPLACE FUNCTION life_financial.spot_action_command(p_account uuid, p_binding jsonb, p_owner jsonb, p_version bigint, p_cut life_financial.digest, p_fingerprint life_financial.digest, p_kind text, p_command jsonb)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_NATIVE_CAPTURE_MIGRATION_MISMATCH';END IF;
    definition:=replace(definition,needle,
        'CREATE FUNCTION life_financial.spot_capture_quote_action(p_account uuid, p_binding jsonb, p_owner jsonb, p_version bigint, p_cut life_financial.digest, p_template jsonb)');
    needle:='DECLARE a life_financial.accounts;';
    replacement:=$text$DECLARE p_kind CONSTANT text:='SPOT_ACTION_CLAIM';p_command jsonb:=p_template;
        p_fingerprint life_financial.digest:=repeat('0',64);payload jsonb;captured text;capture_ms bigint;capture_stamp text;
        a life_financial.accounts;$text$;
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_NATIVE_CAPTURE_MIGRATION_MISMATCH';END IF;
    definition:=replace(definition,needle,replacement);
    needle:='records:=p_command->''actions''; next_state:=''PROPOSED'';';
    replacement:=$text$records:=p_command->'actions';
        IF jsonb_array_length(records)<>1 THEN RAISE EXCEPTION 'LIFE_ACTION_CAPTURE_TEMPLATE_INVALID' USING ERRCODE='P0001';END IF;
        next_state:='PROPOSED';$text$;
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_NATIVE_CAPTURE_MIGRATION_MISMATCH';END IF;
    definition:=replace(definition,needle,replacement);
    needle:=$text$IF FOUND THEN
        IF previous.command<>request OR previous.fingerprint<>p_fingerprint THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001'; END IF;
        RETURN previous.receipt||'{"replayed":true}'::jsonb;
    END IF;$text$;
    replacement:=$text$IF FOUND THEN RAISE EXCEPTION 'LIFE_ACTION_IDENTITY_USED' USING ERRCODE='P0001';END IF;$text$;
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_NATIVE_CAPTURE_MIGRATION_MISMATCH';END IF;
    definition:=replace(definition,needle,replacement);
    needle:='INSERT INTO life_financial.quote_actions VALUES';
    stamp:=$text$
            -- No issued CreateExecutorAction exists before this boundary. All
            -- original checks above run before assigning its one native birth.
            payload:=(record->>'payload_json')::jsonb;
            IF payload->'timestamp' IS DISTINCT FROM '0'::jsonb
                OR (length(record->>'payload_json')-length(replace(record->>'payload_json','"timestamp":0.0,','')))
                    /length('"timestamp":0.0,')<>1 THEN
                RAISE EXCEPTION 'LIFE_ACTION_CAPTURE_TEMPLATE_INVALID' USING ERRCODE='P0001';END IF;
            capture_ms:=floor(extract(epoch FROM clock_timestamp())*1000)::bigint;
            IF capture_ms NOT BETWEEN 1 AND 253402300799000 THEN
                RAISE EXCEPTION 'LIFE_ACTION_CAPTURE_CLOCK_INVALID' USING ERRCODE='P0001';END IF;
            capture_stamp:=trim_scale(capture_ms::numeric/1000)::text;
            IF strpos(capture_stamp,'.')=0 THEN capture_stamp:=capture_stamp||'.0';END IF;
            captured:=replace(record->>'payload_json','"timestamp":0.0,','"timestamp":'||capture_stamp||',');
            IF captured::jsonb-'timestamp' IS DISTINCT FROM payload-'timestamp'
                OR (captured::jsonb->>'timestamp')::numeric<>capture_ms::numeric/1000
                OR octet_length(captured)>policy.max_payload_bytes THEN
                RAISE EXCEPTION 'LIFE_ACTION_CAPTURE_TEMPLATE_INVALID' USING ERRCODE='P0001';END IF;
            record:=jsonb_set(record,'{payload_json}',to_jsonb(captured));
            p_command:=jsonb_set(p_command,'{actions,0}',record);
            IF octet_length(p_command::text)>1048576 THEN
                RAISE EXCEPTION 'LIFE_ACTION_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
            request:=jsonb_build_object('kind',p_kind,'binding',p_binding,'owner',p_owner,'command',p_command);
            p_fingerprint:=encode(sha256(convert_to(life_financial.canonical_json_v1(request),'UTF8')),'hex');
            INSERT INTO life_financial.quote_actions VALUES$text$;
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_NATIVE_CAPTURE_MIGRATION_MISMATCH';END IF;
    definition:=replace(definition,needle,stamp);
    needle:='RETURN result;';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_NATIVE_CAPTURE_MIGRATION_MISMATCH';END IF;
    -- Persist the unchanged canonical CLAIM receipt. The captured descriptor is
    -- returned only with the confirmed new command; readback grants no adoption.
    definition:=replace(definition,needle,'RETURN result||jsonb_build_object(''captured_action'',record);');
    EXECUTE definition;
    FOR r IN SELECT DISTINCT x.grantee FROM pg_proc f
        CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
        WHERE f.oid='life_financial.spot_capture_quote_action(uuid,jsonb,jsonb,bigint,life_financial.digest,jsonb)'::regprocedure
        AND x.grantee<>f.proowner LOOP
        IF r.grantee=0 THEN
            REVOKE ALL ON FUNCTION life_financial.spot_capture_quote_action(uuid,jsonb,jsonb,bigint,life_financial.digest,jsonb) FROM PUBLIC;
        ELSE
            EXECUTE format('REVOKE ALL ON FUNCTION life_financial.spot_capture_quote_action(uuid,jsonb,jsonb,bigint,life_financial.digest,jsonb) FROM %I',pg_get_userbyid(r.grantee));
        END IF;
    END LOOP;
    FOR r IN SELECT DISTINCT x.grantee FROM pg_proc f
        CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
        WHERE f.oid='life_financial.spot_action_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)'::regprocedure
        AND x.grantee<>0 AND x.grantee<>f.proowner AND x.privilege_type='EXECUTE' LOOP
        EXECUTE format('GRANT EXECUTE ON FUNCTION life_financial.spot_capture_quote_action(uuid,jsonb,jsonb,bigint,life_financial.digest,jsonb) TO %I',pg_get_userbyid(r.grantee));
    END LOOP;
END; $$;
