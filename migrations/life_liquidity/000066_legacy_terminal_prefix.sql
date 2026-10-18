-- A prior terminal must already exist natively at the original import cut.
-- Retaining its proof never releases money, subsidy or a shared claim again.
CREATE FUNCTION life_financial.legacy_terminal_prefix_valid(p_account uuid,basis jsonb,p_version bigint) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE j jsonb;o life_financial.operations;f life_financial.spot_terminal_facts;d life_financial.spot_preparations;
BEGIN
    IF basis ? 'spot_terminals' AND jsonb_typeof(basis->'spot_terminals') IS DISTINCT FROM 'array' THEN RETURN false;END IF;
    IF (SELECT count(DISTINCT x->>'operation_id') FROM jsonb_array_elements(COALESCE(basis->'spot_terminals','[]'::jsonb)) x)
        <>jsonb_array_length(COALESCE(basis->'spot_terminals','[]'::jsonb)) THEN RETURN false;END IF;
    FOR j IN SELECT value FROM jsonb_array_elements(COALESCE(basis->'spot_terminals','[]'::jsonb)) LOOP
        SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=j->>'operation_id';
        SELECT * INTO STRICT f FROM life_financial.spot_terminal_facts WHERE account_id=p_account AND operation_id=o.operation_id;
        SELECT * INTO STRICT d FROM life_financial.spot_preparations WHERE account_id=p_account AND operation_id=j->>'prepare_operation_id';
        IF o.committed_version>p_version OR o.command->>'kind' IS DISTINCT FROM 'SPOT_TERMINAL'
            OR (o.command->'command')-'facts' IS DISTINCT FROM j-'facts'
            OR (o.command#>'{command,facts}')-'binding' IS DISTINCT FROM (j->'facts')-'binding'
            OR f.command IS DISTINCT FROM o.command->'command' OR f.intent_id::text IS DISTINCT FROM j->>'intent_id'
            OR o.fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(o.command),'UTF8')),'hex')
            OR (j->>'intent_id',j->>'client_order_id') IS DISTINCT FROM (d.intent_id::text,d.request->>'client_order_id')
            OR NOT EXISTS(SELECT 1 FROM life_financial.operations prepared WHERE prepared.account_id=p_account AND prepared.operation_id=d.operation_id
                AND prepared.committed_version<o.committed_version AND prepared.command->'owner'=o.command->'owner'
                AND prepared.command->'binding'=o.command->'binding')
            OR f.subsidy_settled IS DISTINCT FROM ((j#>>'{facts,cumulative_base_life}')::numeric=0)
            OR o.receipt IS DISTINCT FROM jsonb_build_object('kind','SPOT_TERMINAL','operation_id',o.operation_id,
                'intent_id',f.intent_id,'exchange_order_id',j->>'exchange_order_id','state','TERMINAL','subsidy_settled',f.subsidy_settled,
                'version',o.committed_version,'replayed',false,'trading_permission',false)
            THEN RETURN false;END IF;
    END LOOP;
    RETURN jsonb_array_length(COALESCE(basis->'spot_terminals','[]'::jsonb))=(
        SELECT count(*) FROM life_financial.spot_terminal_facts f LEFT JOIN life_financial.operations o USING(account_id,operation_id)
        WHERE f.account_id=p_account AND (o.committed_version<=p_version OR o.operation_id IS NULL));
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
DO $$ DECLARE definition text;needle text;BEGIN
    definition:=pg_get_functiondef('life_financial.legacy_spot_verified(uuid)'::regprocedure);
    needle:='SELECT NOT EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports i WHERE i.account_id=p_account';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_TERMINAL_PREFIX_MISMATCH';END IF;
    EXECUTE replace(definition,needle,'SELECT NOT EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports i WHERE i.account_id=p_account
        AND NOT life_financial.legacy_terminal_prefix_valid(p_account,i.body->''source_basis'',(i.body#>>''{cut,checkpoint,revision}'')::bigint))
    AND NOT EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports i WHERE i.account_id=p_account');
END; $$;
DO $$ DECLARE r record;BEGIN
    FOR r IN SELECT p.oid::regprocedure AS signature,x.grantee FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE n.nspname='life_financial' AND p.proname='legacy_terminal_prefix_valid' AND x.grantee<>p.proowner LOOP
        IF r.grantee=0 THEN EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',r.signature);
        ELSE EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',r.signature,pg_get_userbyid(r.grantee));END IF;
    END LOOP;
END; $$;
