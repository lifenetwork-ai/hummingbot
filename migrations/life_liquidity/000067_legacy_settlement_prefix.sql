-- Retain original settled inventory costs. IMPORT cannot refund them again.
CREATE FUNCTION life_financial.legacy_settlement_prefix_valid(p_account uuid,basis jsonb,p_version bigint) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE j jsonb;o life_financial.operations;s life_financial.spot_inventory_settlements;k text;
BEGIN
    IF basis ? 'spot_settlements' AND jsonb_typeof(basis->'spot_settlements') IS DISTINCT FROM 'array' THEN RETURN false;END IF;
    IF (SELECT count(DISTINCT x#>>'{command,operation_id}') FROM jsonb_array_elements(COALESCE(basis->'spot_settlements','[]'::jsonb)) x)
        <>jsonb_array_length(COALESCE(basis->'spot_settlements','[]'::jsonb)) THEN RETURN false;END IF;
    FOR j IN SELECT value FROM jsonb_array_elements(COALESCE(basis->'spot_settlements','[]'::jsonb)) LOOP
        SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=j#>>'{command,operation_id}';
        SELECT * INTO STRICT s FROM life_financial.spot_inventory_settlements WHERE account_id=p_account AND operation_id=o.operation_id;
        IF o.committed_version>p_version OR o.command->>'kind' IS DISTINCT FROM 'SPOT_INVENTORY_SETTLE'
            OR o.command->'command' IS DISTINCT FROM j->'command' OR s.command IS DISTINCT FROM j->'command'
            OR (o.command->'facts')-'binding' IS DISTINCT FROM (j->'facts')-'binding'
            OR o.fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(o.command-'facts'),'UTF8')),'hex')
            OR s.evidence->>'proof' IS DISTINCT FROM j->>'proof'
            OR (SELECT count(*) FROM life_financial.spot_inventory_members m WHERE m.account_id=p_account AND m.operation_id=s.operation_id)
                IS DISTINCT FROM jsonb_array_length(s.command->'intent_ids')::bigint
            OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(j->'actuals') key)
                IS DISTINCT FROM (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(s.evidence->'actuals') key)
            OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(j->'committed_before') key)
                IS DISTINCT FROM (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(s.evidence->'held_before') key)
            OR o.receipt IS DISTINCT FROM jsonb_build_object('kind','SPOT_INVENTORY_SETTLE','operation_id',o.operation_id,
                'cycle_id',s.cycle_id,'actuals_usdt',s.evidence->'actuals','proof_digest',s.evidence->>'proof','state','SETTLED',
                'version',o.committed_version,'replayed',false,'trading_permission',false)
            THEN RETURN false;END IF;
        FOR k IN SELECT jsonb_object_keys(j->'actuals') LOOP
            IF (j#>>ARRAY['actuals',k])::numeric IS DISTINCT FROM (s.evidence#>>ARRAY['actuals',k])::numeric
                OR (j#>>ARRAY['committed_before',k])::numeric IS DISTINCT FROM (s.evidence#>>ARRAY['held_before',k])::numeric
                OR NOT EXISTS(SELECT 1 FROM life_financial.spot_inventory_members m WHERE m.account_id=p_account AND m.operation_id=s.operation_id
                    AND m.intent_id=k AND m.actual_usdt=(j#>>ARRAY['actuals',k])::numeric
                    AND m.held_before_usdt=(j#>>ARRAY['committed_before',k])::numeric)
                OR NOT EXISTS(SELECT 1 FROM life_financial.spot_preparations d JOIN life_financial.operations prepared USING(account_id,operation_id)
                    JOIN life_financial.spot_terminal_facts f ON f.account_id=d.account_id AND f.intent_id=d.intent_id
                    JOIN life_financial.operations terminal ON terminal.account_id=f.account_id AND terminal.operation_id=f.operation_id
                    WHERE d.account_id=p_account AND d.intent_id=k AND prepared.committed_version<terminal.committed_version
                        AND terminal.committed_version<o.committed_version AND prepared.command->'owner'=o.command->'owner'
                        AND prepared.command->'binding'=o.command->'binding')
                THEN RETURN false;END IF;
        END LOOP;
    END LOOP;
    RETURN jsonb_array_length(COALESCE(basis->'spot_settlements','[]'::jsonb))=(
        SELECT count(*) FROM life_financial.spot_inventory_settlements s LEFT JOIN life_financial.operations o USING(account_id,operation_id)
        WHERE s.account_id=p_account AND (o.committed_version<=p_version OR o.operation_id IS NULL));
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
DO $$ DECLARE definition text;needle text;BEGIN
    definition:=pg_get_functiondef('life_financial.legacy_spot_verified(uuid)'::regprocedure);
    needle:='SELECT NOT EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports i WHERE i.account_id=p_account';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_SETTLEMENT_PREFIX_MISMATCH';END IF;
    EXECUTE replace(definition,needle,'SELECT NOT EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports i WHERE i.account_id=p_account
        AND NOT life_financial.legacy_settlement_prefix_valid(p_account,i.body->''source_basis'',(i.body#>>''{cut,checkpoint,revision}'')::bigint))
    AND NOT EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports i WHERE i.account_id=p_account');
END; $$;
DO $$ DECLARE r record;BEGIN
    FOR r IN SELECT p.oid::regprocedure AS signature,x.grantee FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE n.nspname='life_financial' AND p.proname='legacy_settlement_prefix_valid' AND x.grantee<>p.proowner LOOP
        IF r.grantee=0 THEN EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',r.signature);
        ELSE EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',r.signature,pg_get_userbyid(r.grantee));END IF;
    END LOOP;
END; $$;
