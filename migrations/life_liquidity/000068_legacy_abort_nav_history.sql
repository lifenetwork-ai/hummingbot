-- Retain original no-send aborts and historical valuations, never a release or
-- current NAV grant. Both require independent native provenance at the cut.
CREATE FUNCTION life_financial.legacy_abort_prefix_valid(p_account uuid,basis jsonb,p_version bigint) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE j jsonb;o life_financial.operations;e life_financial.spot_lifecycle_events;d life_financial.spot_preparations;
BEGIN
    IF jsonb_typeof(basis) IS DISTINCT FROM 'object' THEN RETURN false;END IF;
    IF basis ? 'spot_aborts' AND jsonb_typeof(basis->'spot_aborts') IS DISTINCT FROM 'array' THEN RETURN false;END IF;
    IF (SELECT count(DISTINCT x->>'operation_id') FROM jsonb_array_elements(COALESCE(basis->'spot_aborts','[]'::jsonb)) x)
        <>jsonb_array_length(COALESCE(basis->'spot_aborts','[]'::jsonb)) THEN RETURN false;END IF;
    FOR j IN SELECT value FROM jsonb_array_elements(COALESCE(basis->'spot_aborts','[]'::jsonb)) LOOP
        SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=j->>'operation_id';
        SELECT * INTO STRICT e FROM life_financial.spot_lifecycle_events WHERE account_id=p_account AND operation_id=o.operation_id;
        SELECT * INTO STRICT d FROM life_financial.spot_preparations WHERE account_id=p_account AND operation_id=j->>'prepare_operation_id';
        IF o.committed_version>p_version OR j->>'action' IS DISTINCT FROM 'ABORT_PREPARED'
            OR o.command->>'kind' IS DISTINCT FROM 'SPOT_LIFECYCLE' OR o.command->'command' IS DISTINCT FROM j
            OR o.fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(o.command),'UTF8')),'hex')
            OR e.command IS DISTINCT FROM j OR e.result_state IS DISTINCT FROM 'ABORTED_BEFORE_SEND' OR e.after_cancel IS NOT NULL
            OR (e.intent_id::text,j->>'intent_id',j->>'client_order_id') IS DISTINCT FROM (d.intent_id::text,d.intent_id::text,d.request->>'client_order_id')
            OR NOT life_financial.legacy_spot_unsent_valid(p_account,d.request,o.committed_version-1)
            OR NOT EXISTS(SELECT 1 FROM life_financial.operations prepared WHERE prepared.account_id=p_account AND prepared.operation_id=d.operation_id
                AND prepared.command->'owner'=o.command->'owner' AND prepared.command->'binding'=o.command->'binding')
            OR o.receipt IS DISTINCT FROM jsonb_build_object('kind','SPOT_LIFECYCLE','operation_id',o.operation_id,
                'intent_id',e.intent_id,'state','ABORTED_BEFORE_SEND','version',o.committed_version,'replayed',false,'trading_permission',false)
            THEN RETURN false;END IF;
    END LOOP;
    RETURN jsonb_array_length(COALESCE(basis->'spot_aborts','[]'::jsonb))=(
        SELECT count(*) FROM life_financial.spot_lifecycle_events e LEFT JOIN life_financial.operations o USING(account_id,operation_id)
        WHERE e.account_id=p_account AND e.command->>'action'='ABORT_PREPARED' AND (o.committed_version<=p_version OR o.operation_id IS NULL));
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.legacy_nav_prefix_valid(p_account uuid,basis jsonb,p_version bigint) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE j jsonb;o life_financial.operations;n life_financial.spot_nav_observations;last_nav life_financial.spot_nav_observations;k text;digest text;
BEGIN
    IF jsonb_typeof(basis) IS DISTINCT FROM 'object' THEN RETURN false;END IF;
    IF basis ? 'spot_navs' AND jsonb_typeof(basis->'spot_navs') IS DISTINCT FROM 'array' THEN RETURN false;END IF;
    IF (SELECT count(DISTINCT x#>>'{command,operation_id}') FROM jsonb_array_elements(COALESCE(basis->'spot_navs','[]'::jsonb)) x)
        <>jsonb_array_length(COALESCE(basis->'spot_navs','[]'::jsonb)) THEN RETURN false;END IF;
    FOR j IN SELECT value FROM jsonb_array_elements(COALESCE(basis->'spot_navs','[]'::jsonb)) LOOP
        SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=j#>>'{command,operation_id}';
        SELECT * INTO STRICT n FROM life_financial.spot_nav_observations WHERE account_id=p_account AND operation_id=o.operation_id;
        IF o.committed_version>p_version OR o.command->>'kind' IS DISTINCT FROM 'SPOT_NAV'
            OR o.command->'command' IS DISTINCT FROM j->'command' OR n.command IS DISTINCT FROM j->'command'
            OR o.fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(o.command),'UTF8')),'hex')
            OR n.highwater IS DISTINCT FROM (n.measurement->>'highwater_usdt')::numeric
            OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(j->'measurement') key)
                IS DISTINCT FROM (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(n.measurement) key)
            OR o.receipt IS DISTINCT FROM jsonb_build_object('kind','SPOT_NAV','operation_id',o.operation_id,'state','APPLIED',
                'version',o.committed_version,'replayed',false,'trading_permission',false)
            THEN RETURN false;END IF;
        FOR k IN SELECT jsonb_object_keys(n.measurement) LOOP
            IF (j#>>ARRAY['measurement',k])::numeric IS DISTINCT FROM (n.measurement->>k)::numeric THEN RETURN false;END IF;
        END LOOP;
        -- Recreate the native monetary fingerprint at its ORIGINAL NAV cut.
        -- Later fills/transfers must neither corrupt nor re-date this proof.
        SELECT encode(sha256(convert_to(jsonb_build_object(
            'policy',(SELECT to_jsonb(p) FROM life_financial.spot_capital_policies p WHERE account_id=p_account),
            'fills',COALESCE((SELECT jsonb_agg(to_jsonb(f) ORDER BY f.trade_id COLLATE "C") FROM life_financial.spot_fill_facts f
                JOIN life_financial.operations original ON original.account_id=f.account_id AND original.operation_id=f.source_operation_id
                WHERE f.account_id=p_account AND original.committed_version<o.committed_version),'[]'::jsonb),
            'cashflows',COALESCE((SELECT jsonb_agg(to_jsonb(f) ORDER BY f.bill_id COLLATE "C") FROM life_financial.cashflow_effective(p_account) f
                JOIN life_financial.operations original ON original.account_id=f.account_id AND original.operation_id=f.source_operation_id
                WHERE original.committed_version<o.committed_version),'[]'::jsonb))::text,'UTF8')),'hex') INTO digest;
        IF n.money_digest IS DISTINCT FROM digest THEN RETURN false;END IF;
    END LOOP;
    IF jsonb_array_length(COALESCE(basis->'spot_navs','[]'::jsonb))<>(SELECT count(*) FROM life_financial.spot_nav_observations n
        LEFT JOIN life_financial.operations o USING(account_id,operation_id) WHERE n.account_id=p_account AND (o.committed_version<=p_version OR o.operation_id IS NULL)) THEN RETURN false;END IF;
    SELECT n.* INTO last_nav FROM life_financial.spot_nav_observations n JOIN life_financial.operations o USING(account_id,operation_id)
        WHERE n.account_id=p_account AND o.committed_version<=p_version ORDER BY o.committed_version DESC LIMIT 1;
    RETURN CASE WHEN FOUND THEN basis#>>'{spot_nav_capital,latest_operation_id}' IS NOT DISTINCT FROM last_nav.operation_id::text
        AND (basis#>>'{spot_nav_capital,highwater_usdt}')::numeric IS NOT DISTINCT FROM last_nav.highwater
        ELSE NOT basis ? 'spot_nav_capital' END;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

DO $$ DECLARE definition text;needle text;BEGIN
    definition:=pg_get_functiondef('life_financial.legacy_spot_basis_valid(uuid,jsonb,bigint)'::regprocedure);
    needle:='CASE WHEN COALESCE(b->''spot_prepared_only_ids'',''[]''::jsonb) @> jsonb_build_array(d.operation_id) THEN';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_LIFECYCLE_HISTORY_MISMATCH';END IF;
    EXECUTE replace(definition,needle,'CASE WHEN EXISTS(SELECT 1 FROM jsonb_array_elements(COALESCE(b->''spot_aborts'',''[]''::jsonb)) x
        WHERE x->>''prepare_operation_id''=d.operation_id::text) THEN NOT life_financial.legacy_abort_prefix_valid(p_account,b,p_version)
        WHEN COALESCE(b->''spot_prepared_only_ids'',''[]''::jsonb) @> jsonb_build_array(d.operation_id) THEN');
    definition:=pg_get_functiondef('life_financial.legacy_cancel_prefix_valid(uuid,jsonb,bigint)'::regprocedure);
    needle:='WHERE e.account_id=p_account AND (o.committed_version<=p_version OR o.operation_id IS NULL)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_LIFECYCLE_HISTORY_MISMATCH';END IF;
    EXECUTE replace(definition,needle,needle||' AND e.command->>''action''<>''ABORT_PREPARED''');
    definition:=pg_get_functiondef('life_financial.legacy_spot_verified(uuid)'::regprocedure);
    needle:='SELECT NOT EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports i WHERE i.account_id=p_account';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_LIFECYCLE_HISTORY_MISMATCH';END IF;
    EXECUTE replace(definition,needle,'SELECT NOT EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports i WHERE i.account_id=p_account
        AND (NOT life_financial.legacy_abort_prefix_valid(p_account,i.body->''source_basis'',(i.body#>>''{cut,checkpoint,revision}'')::bigint)
            OR NOT life_financial.legacy_nav_prefix_valid(p_account,i.body->''source_basis'',(i.body#>>''{cut,checkpoint,revision}'')::bigint)))
    AND NOT EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports i WHERE i.account_id=p_account');
END; $$;
DO $$ DECLARE r record;BEGIN
    FOR r IN SELECT p.oid::regprocedure AS signature,x.grantee FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE n.nspname='life_financial' AND p.proname IN ('legacy_abort_prefix_valid','legacy_nav_prefix_valid') AND x.grantee<>p.proowner LOOP
        IF r.grantee=0 THEN EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',r.signature);
        ELSE EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',r.signature,pg_get_userbyid(r.grantee));END IF;
    END LOOP;
END; $$;
