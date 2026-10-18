-- Retain independently approved, already booked native transfers. The signed
-- File history must include their exact economic facts; import credits nothing.
CREATE FUNCTION life_financial.legacy_cashflow_prefix_valid(p_account uuid,b jsonb,p_version bigint) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE f record;matches bigint;hash text;
BEGIN
    IF jsonb_typeof(b->'cashflows') IS DISTINCT FROM 'array' THEN RETURN false;END IF;
    FOR f IN SELECT x.*,o.command AS original,o.receipt,o.fingerprint,o.committed_version FROM life_financial.spot_cashflow_facts x LEFT JOIN life_financial.operations o
        ON o.account_id=x.account_id AND o.operation_id=x.source_operation_id
        WHERE x.account_id=p_account AND (o.committed_version<=p_version OR o.operation_id IS NULL) LOOP
        SELECT count(*) INTO matches FROM jsonb_array_elements(b->'cashflows') target
            WHERE target.value-ARRAY['operation_id','observed_at']=f.command-ARRAY['operation_id','observed_at']
                AND target.value->>'bill_id'=f.bill_id::text;
        hash:=encode(sha256(convert_to(life_financial.canonical_json_v1(f.original),'UTF8')),'hex');
        IF matches<>1 OR f.state<>'APPLIED' OR f.reason IS NOT NULL
            OR f.original->>'kind' IS DISTINCT FROM 'SPOT_CASHFLOW' OR f.original->'command' IS DISTINCT FROM f.command
            OR f.command->>'operation_id' IS DISTINCT FROM f.source_operation_id::text
            OR f.source_hash IS DISTINCT FROM hash OR f.fingerprint IS DISTINCT FROM hash
            OR f.receipt IS DISTINCT FROM jsonb_build_object('bill_id',f.bill_id,'state','APPLIED','kind','SPOT_CASHFLOW',
                'operation_id',f.source_operation_id,'version',f.committed_version,'replayed',false,'trading_permission',false)
            THEN RETURN false;END IF;
    END LOOP;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

DO $$ DECLARE definition text;needle text;replacement text;BEGIN
    definition:=pg_get_functiondef('life_financial.legacy_spot_prefix_valid(uuid,jsonb,bigint)'::regprocedure);
    needle:='IF EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts x JOIN life_financial.operations o
        ON o.account_id=x.account_id AND o.operation_id=x.source_operation_id WHERE x.account_id=p_account AND o.committed_version<=p_version)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_CASHFLOW_PREFIX_MISMATCH';END IF;
    definition:=replace(definition,needle,'IF NOT life_financial.legacy_cashflow_prefix_valid(p_account,b,p_version)');
    -- Missing original operations are corruption, not an empty historical
    -- prefix. Ordinary FK-protected writes cannot create these orphan rows.
    needle:='FOR f IN SELECT x.*,o.command AS original,o.receipt FROM life_financial.spot_fill_facts x JOIN life_financial.operations o';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_CASHFLOW_PREFIX_MISMATCH';END IF;
    definition:=replace(definition,needle,replace(needle,' x JOIN ',' x LEFT JOIN '));
    needle:='WHERE x.account_id=p_account AND o.committed_version<=p_version LOOP';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_CASHFLOW_PREFIX_MISMATCH';END IF;
    EXECUTE replace(definition,needle,'WHERE x.account_id=p_account AND (o.committed_version<=p_version OR o.operation_id IS NULL) LOOP');

    definition:=pg_get_functiondef('life_financial.legacy_applying_pending(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'::regprocedure);
    needle:='(e.product<>''SPOT'' OR e.kind<>''FILL'' OR NOT EXISTS(SELECT 1 FROM life_financial.spot_fill_facts x
                JOIN life_financial.operations o ON o.account_id=x.account_id AND o.operation_id=x.source_operation_id
                WHERE x.account_id=p_account AND x.trade_id=e.exchange_identity AND o.committed_version<=p_version))';
    replacement:='CASE WHEN e.product=''SPOT'' AND e.kind=''FILL'' THEN NOT EXISTS(SELECT 1 FROM life_financial.spot_fill_facts x
                JOIN life_financial.operations o ON o.account_id=x.account_id AND o.operation_id=x.source_operation_id
                WHERE x.account_id=p_account AND x.trade_id=e.exchange_identity AND o.committed_version<=p_version)
            WHEN e.product=''ACCOUNT'' AND e.kind=''CASHFLOW'' THEN NOT EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts x
                JOIN life_financial.operations o ON o.account_id=x.account_id AND o.operation_id=x.source_operation_id
                WHERE x.account_id=p_account AND x.bill_id=e.exchange_identity AND x.state=''APPLIED'' AND o.committed_version<=p_version)
            ELSE true END';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_CASHFLOW_PREFIX_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;
-- This proof is not a new public capability, including under permissive owner
-- defaults. Keep predecessor function OIDs, ACLs and all financial barriers.
DO $$ DECLARE r record;BEGIN
    FOR r IN SELECT p.oid::regprocedure AS signature,x.grantee FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE n.nspname='life_financial' AND p.proname='legacy_cashflow_prefix_valid' AND x.grantee<>p.proowner LOOP
        IF r.grantee=0 THEN EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',r.signature);
        ELSE EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',r.signature,pg_get_userbyid(r.grantee));END IF;
    END LOOP;
END; $$;
