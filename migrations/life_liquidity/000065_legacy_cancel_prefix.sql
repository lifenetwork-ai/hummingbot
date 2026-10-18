-- Cancel requests/attempts retain original holds. This proof creates no cancel
-- request, retries, terminal, release or money; missing native provenance denies.
CREATE FUNCTION life_financial.legacy_cancel_prefix_valid(p_account uuid,basis jsonb,p_version bigint) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE j jsonb;o life_financial.operations;e life_financial.spot_lifecycle_events;d life_financial.spot_preparations;g jsonb;prior jsonb;expected_count bigint;
BEGIN
    IF basis ? 'spot_cancellations' AND jsonb_typeof(basis->'spot_cancellations') IS DISTINCT FROM 'array' THEN RETURN false;END IF;
    FOR j IN SELECT value FROM jsonb_array_elements(COALESCE(basis->'spot_cancellations','[]'::jsonb)) LOOP
        SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=j->>'operation_id';
        SELECT * INTO STRICT e FROM life_financial.spot_lifecycle_events WHERE account_id=p_account AND operation_id=o.operation_id;
        SELECT * INTO STRICT d FROM life_financial.spot_preparations WHERE account_id=p_account AND operation_id=j->>'prepare_operation_id';
        IF j->>'action' NOT IN ('REQUEST_CANCEL','RECORD_CANCEL_ATTEMPT') OR o.committed_version>p_version
            OR o.command->>'kind' IS DISTINCT FROM 'SPOT_LIFECYCLE' OR o.command->'command' IS DISTINCT FROM j
            OR o.fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(o.command),'UTF8')),'hex')
            OR e.command IS DISTINCT FROM j OR e.intent_id::text IS DISTINCT FROM j->>'intent_id'
            OR e.result_state NOT IN ('SEND_UNKNOWN','ACKED')
            OR basis#>>ARRAY['spot_cancel_states',j->>'operation_id'] IS DISTINCT FROM e.result_state
            OR (j->>'intent_id',j->>'client_order_id') IS DISTINCT FROM (d.intent_id::text,d.request->>'client_order_id')
            OR NOT EXISTS(SELECT 1 FROM life_financial.operations prepared WHERE prepared.account_id=p_account AND prepared.operation_id=d.operation_id
                AND prepared.committed_version<o.committed_version AND prepared.command->'owner'=o.command->'owner'
                AND prepared.command->'binding'=o.command->'binding')
            OR o.receipt IS DISTINCT FROM jsonb_build_object('kind','SPOT_LIFECYCLE','operation_id',o.operation_id,
                'intent_id',e.intent_id,'state',e.result_state,'version',o.committed_version,'replayed',false,'trading_permission',false)
            THEN RETURN false;END IF;
        g:=e.after_cancel;
        SELECT COALESCE(max((x.command->>'expected_cancel_attempts')::bigint+1),0) INTO expected_count
            FROM life_financial.spot_lifecycle_events x WHERE x.account_id=p_account AND x.intent_id=e.intent_id
            AND x.sequence<=e.sequence AND x.command->>'action'='RECORD_CANCEL_ATTEMPT';
        SELECT x.after_cancel INTO prior FROM life_financial.spot_lifecycle_events x WHERE x.account_id=p_account
            AND x.intent_id=e.intent_id AND x.sequence<e.sequence AND x.command->>'action'='RECORD_CANCEL_ATTEMPT'
            ORDER BY x.sequence DESC LIMIT 1;
        IF g IS NULL OR g->>'account_id' IS DISTINCT FROM p_account::text OR g->>'intent_id' IS DISTINCT FROM e.intent_id::text
            OR (g->>'version')::bigint IS DISTINCT FROM (SELECT count(*) FROM life_financial.spot_lifecycle_events x
                WHERE x.account_id=p_account AND x.intent_id=e.intent_id AND x.sequence<=e.sequence AND x.after_cancel IS NOT NULL)
            OR (g->>'cancel_attempts')::bigint IS DISTINCT FROM expected_count THEN RETURN false;END IF;
        IF j->>'action'='RECORD_CANCEL_ATTEMPT' THEN
            IF (g->>'cancel_attempts')::bigint IS DISTINCT FROM (j->>'expected_cancel_attempts')::bigint+1
                OR (g->>'last_cancel_attempt_at')::timestamptz IS DISTINCT FROM (j->>'attempted_at')::timestamptz
                OR (g->>'last_recorded_at')::timestamptz>o.recorded_at
                OR (g->>'last_recorded_at')::timestamptz<(j->>'attempted_at')::timestamptz
                OR (j->>'attempted_at')::timestamptz<d.prepared_at
                OR (j->>'retry_interval_ms')::integer IS DISTINCT FROM (SELECT cancel_retry_interval_ms FROM life_financial.spot_order_policies WHERE account_id=p_account)
                THEN RETURN false;END IF;
        ELSIF g->'last_cancel_attempt_at' IS DISTINCT FROM COALESCE(prior->'last_cancel_attempt_at','null'::jsonb)
            OR g->'last_recorded_at' IS DISTINCT FROM COALESCE(prior->'last_recorded_at','null'::jsonb)
            THEN RETURN false;
        END IF;
    END LOOP;
    RETURN NOT EXISTS(SELECT 1 FROM life_financial.spot_lifecycle_events e LEFT JOIN life_financial.operations o USING(account_id,operation_id)
        WHERE e.account_id=p_account AND (o.committed_version<=p_version OR o.operation_id IS NULL)
        AND (o.operation_id IS NULL OR (SELECT count(*) FROM jsonb_array_elements(COALESCE(basis->'spot_cancellations','[]'::jsonb)) x WHERE x=e.command)<>1));
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
DO $$ DECLARE definition text;needle text;BEGIN
    definition:=pg_get_functiondef('life_financial.legacy_spot_verified(uuid)'::regprocedure);
    needle:='SELECT NOT EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports i WHERE i.account_id=p_account';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_CANCEL_PREFIX_MISMATCH';END IF;
    -- Only the first SELECT introduces this expression; later AND NOT EXISTS
    -- clauses retain their existing independent proofs.
    definition:=replace(definition,needle,'SELECT NOT EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports i WHERE i.account_id=p_account
        AND NOT life_financial.legacy_cancel_prefix_valid(p_account,i.body->''source_basis'',(i.body#>>''{cut,checkpoint,revision}'')::bigint))
    AND NOT EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports i WHERE i.account_id=p_account');
    EXECUTE definition;
END; $$;
DO $$ DECLARE r record;BEGIN
    FOR r IN SELECT p.oid::regprocedure AS signature,x.grantee FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE n.nspname='life_financial' AND p.proname='legacy_cancel_prefix_valid' AND x.grantee<>p.proowner LOOP
        IF r.grantee=0 THEN EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',r.signature);
        ELSE EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',r.signature,pg_get_userbyid(r.grantee));END IF;
    END LOOP;
END; $$;
