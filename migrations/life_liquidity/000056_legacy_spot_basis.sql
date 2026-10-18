-- A retired File SPOT command cannot create/remap an original native order.
-- Keep the exact prepared payload and independently committed pre-import arm.
CREATE FUNCTION life_financial.legacy_spot_basis_valid(p_account uuid,b jsonb,p_version bigint) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE j jsonb;d life_financial.spot_preparations;
BEGIN
    IF jsonb_typeof(b->'spot_preparations') IS DISTINCT FROM 'array'
        OR jsonb_array_length(b->'spot_preparations') NOT BETWEEN 1 AND 256
        OR (SELECT count(DISTINCT value->>'operation_id') FROM jsonb_array_elements(b->'spot_preparations'))<>jsonb_array_length(b->'spot_preparations')
        OR (SELECT count(DISTINCT value->>'intent_id') FROM jsonb_array_elements(b->'spot_preparations'))<>jsonb_array_length(b->'spot_preparations') THEN RETURN false;END IF;
    FOR j IN SELECT value FROM jsonb_array_elements(b->'spot_preparations') LOOP
        SELECT * INTO STRICT d FROM life_financial.spot_preparations WHERE account_id=p_account AND operation_id=j->>'operation_id';
        IF d.request IS DISTINCT FROM j OR d.intent_id::text IS DISTINCT FROM j->>'intent_id'
            OR NOT EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=p_account AND o.operation_id=d.operation_id
                AND o.command->>'kind'='SPOT_PREPARE' AND o.command->'command'=j AND o.committed_version<=p_version)
            OR NOT EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=p_account AND o.operation_id='arm:'||d.operation_id::text
                AND o.receipt->>'kind'='SPOT_ARM'
                AND o.receipt->>'operation_id'=d.operation_id::text AND o.receipt->>'intent_id'=d.intent_id::text
                AND o.receipt->>'state'='SEND_UNKNOWN' AND (o.receipt->>'version')::bigint=o.committed_version
                AND o.command->>'prepare'=d.operation_id::text AND o.committed_version<=p_version
                AND EXISTS(SELECT 1 FROM life_financial.operations prepared WHERE prepared.account_id=p_account AND prepared.operation_id=d.operation_id
                    AND prepared.committed_version<o.committed_version AND prepared.command->'owner'=o.command->'owner'
                    AND prepared.command->'binding'=o.command->'binding')) THEN RETURN false;END IF;
    END LOOP;
    RETURN jsonb_array_length(b->'spot_preparations')=(SELECT count(*) FROM life_financial.spot_preparations retained
        JOIN life_financial.operations o USING(account_id,operation_id) WHERE retained.account_id=p_account AND o.committed_version<=p_version);
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
CREATE FUNCTION life_financial.legacy_spot_verified(p_account uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT NOT EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports i WHERE i.account_id=p_account
    AND CASE WHEN i.body->'source_basis' ? 'spot_preparations' THEN
        NOT life_financial.legacy_spot_basis_valid(p_account,i.body->'source_basis',(i.body#>>'{cut,checkpoint,revision}')::bigint)
    ELSE COALESCE(jsonb_array_length(i.body#>'{payload,batch,spot_fills}'),-1)<>0 END);
$$;
DO $$ DECLARE definition text;needle text:='IF NOT life_financial.legacy_cashflow_verified(p_account) THEN RETURN false;END IF;';BEGIN
    definition:=pg_get_functiondef('life_financial.legacy_applying_verified(uuid)'::regprocedure);
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_SPOT_BASIS_MISMATCH';END IF;
    EXECUTE replace(definition,needle,needle||'
    IF NOT life_financial.legacy_spot_verified(p_account) THEN RETURN false;END IF;');
END; $$;
-- Even permissive owner defaults must not expose either independent proof.
DO $$ DECLARE r record;BEGIN
    FOR r IN SELECT p.oid::regprocedure AS signature,x.grantee FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE n.nspname='life_financial' AND p.proname IN ('legacy_spot_basis_valid','legacy_spot_verified') AND x.grantee<>p.proowner LOOP
        IF r.grantee=0 THEN EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',r.signature);
        ELSE EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',r.signature,pg_get_userbyid(r.grantee));END IF;
    END LOOP;
END; $$;
