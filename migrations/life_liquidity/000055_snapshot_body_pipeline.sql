-- Keep every financial proof. Build each layer's body without the digest and
-- capture time immediately discarded by its parent. Each existing entrypoint
-- still returns its original body + digest + observed_at, with its OID/ACL.
-- Bodies are owner-only and have no grant/receipt/cache/session authority.
DO $$
DECLARE names text[]:=ARRAY['spot_state_v7','spot_state_v13','spot_state_v14','spot_state_v15',
    'spot_state_before_carry','spot_state_before_swap','spot_state_before_swap_accounting',
    'spot_state_before_paid_funding','spot_state_before_portfolio','spot_state_before_swap_terminal',
    'spot_state_before_shared_release','spot_state_before_joint_batch','spot_state_before_hedge',
    'spot_state_before_swap_boundary','spot_state_before_terminal_repair','spot_state'];
    name text;previous text;body_name text;definition text;source text;body_source text;
    header text;needle text;candidate text;variable text;wrapper text;found integer;metadata record;r record;
BEGIN
    FOREACH name IN ARRAY names LOOP
        SELECT p.* INTO STRICT metadata FROM pg_proc p
            WHERE p.oid=to_regprocedure('life_financial.'||name||'(uuid)');
        IF metadata.provolatile<>'s' OR NOT metadata.prosecdef OR metadata.prorettype<>'jsonb'::regtype
            OR metadata.proargnames IS DISTINCT FROM ARRAY['p_account']::text[]
            OR metadata.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']::text[] THEN
            RAISE EXCEPTION 'LIFE_SNAPSHOT_BODY_CONTEXT_MISMATCH';
        END IF;
        definition:=pg_get_functiondef(metadata.oid);source:=metadata.prosrc;
        body_name:=replace(name,'spot_state','spot_body');
        header:='CREATE OR REPLACE FUNCTION life_financial.'||name||'(p_account uuid)';
        IF (length(definition)-length(replace(definition,header,'')))/length(header)<>1 THEN
            RAISE EXCEPTION 'LIFE_SNAPSHOT_BODY_HEADER_MISMATCH';
        END IF;
        -- Recognize exactly the historical final serialization expression.
        -- No regex edits to predicates, hashes inside proofs or field values.
        found:=0;
        FOREACH variable IN ARRAY ARRAY['snapshot_body','body','b'] LOOP
            candidate:='RETURN '||variable||'||jsonb_build_object(''digest'',encode(sha256(convert_to('||variable||'::text,''UTF8'')),''hex''),''observed_at'',clock_timestamp());';
            IF (length(source)-length(replace(source,candidate,'')))/length(candidate)=1 THEN
                found:=found+1;needle:=candidate;
                body_source:=replace(source,candidate,'RETURN '||variable||';');
            END IF;
        END LOOP;
        IF found<>1 THEN RAISE EXCEPTION 'LIFE_SNAPSHOT_BODY_RETURN_MISMATCH';END IF;
        IF previous IS NOT NULL THEN
            needle:='life_financial.'||previous||'(p_account)-''digest''-''observed_at''';
            IF (length(body_source)-length(replace(body_source,needle,'')))/length(needle)<>1 THEN
                RAISE EXCEPTION 'LIFE_SNAPSHOT_BODY_CHAIN_MISMATCH';
            END IF;
            body_source:=replace(body_source,needle,'life_financial.'||replace(previous,'spot_state','spot_body')||'(p_account)');
        END IF;
        EXECUTE replace(replace(definition,header,'CREATE FUNCTION life_financial.'||body_name||'(p_account uuid)'),source,body_source);
        -- Default grants must not expose the unwrapped body to any nonowner.
        FOR r IN SELECT x.grantee FROM pg_proc p
            CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
            WHERE p.oid=to_regprocedure('life_financial.'||body_name||'(uuid)') AND x.grantee<>p.proowner LOOP
            IF r.grantee=0 THEN
                EXECUTE format('REVOKE ALL ON FUNCTION life_financial.%I(uuid) FROM PUBLIC',body_name);
            ELSE
                EXECUTE format('REVOKE ALL ON FUNCTION life_financial.%I(uuid) FROM %I',body_name,pg_get_userbyid(r.grantee));
            END IF;
        END LOOP;
        wrapper:=E'DECLARE b jsonb;\nBEGIN\n    b:=life_financial.'||body_name||E'(p_account);\n    RETURN b||jsonb_build_object(''digest'',encode(sha256(convert_to(b::text,''UTF8'')),''hex''),''observed_at'',clock_timestamp());\nEND;\n';
        EXECUTE replace(definition,source,wrapper);
        previous:=name;
    END LOOP;
END; $$;
