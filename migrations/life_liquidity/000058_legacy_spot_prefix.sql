-- An already booked native SPOT prefix must be an exact subset of the signed
-- original cumulative observations. Import never recreates that money/receipt.
-- Use the original committed cut: later repair/ordinary facts do not rewrite
-- the historical pre-import basis. Other pre-import monetary families still
-- require their own original-basis profiles and cannot hide behind this proof.
CREATE FUNCTION life_financial.legacy_spot_prefix_valid(p_account uuid,b jsonb,p_version bigint) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE f record;matches bigint;
BEGIN
    IF jsonb_typeof(b->'spot_fills') IS DISTINCT FROM 'array' THEN RETURN false;END IF;
    IF EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts x JOIN life_financial.operations o
        ON o.account_id=x.account_id AND o.operation_id=x.source_operation_id WHERE x.account_id=p_account AND o.committed_version<=p_version)
        OR EXISTS(SELECT 1 FROM life_financial.swap_fill_facts x JOIN life_financial.operations o
        ON o.account_id=x.account_id AND o.operation_id=x.source_operation_id WHERE x.account_id=p_account AND o.committed_version<=p_version)
        OR EXISTS(SELECT 1 FROM life_financial.funding_cash_payments x JOIN life_financial.operations o
        ON o.account_id=x.account_id AND o.operation_id=x.source_operation_id WHERE x.account_id=p_account AND o.committed_version<=p_version)
        THEN RETURN false;END IF;
    FOR f IN SELECT x.*,o.command AS original,o.receipt FROM life_financial.spot_fill_facts x JOIN life_financial.operations o
        ON o.account_id=x.account_id AND o.operation_id=x.source_operation_id
        WHERE x.account_id=p_account AND o.committed_version<=p_version LOOP
        SELECT count(*) INTO matches FROM jsonb_array_elements(b->'spot_fills') snapshot
            CROSS JOIN LATERAL jsonb_array_elements(snapshot.value->'fills') leaf
            WHERE leaf.value=f.observation AND leaf.value->>'trade_id'=f.trade_id::text
                AND snapshot.value->>'intent_id'=f.intent_id::text
                AND snapshot.value->>'exchange_order_id'=f.exchange_order_id::text
                AND snapshot.value-ARRAY['operation_id','fills','cumulative_base_life','observed_at']
                    = (f.original->'command')-ARRAY['operation_id','fills','cumulative_base_life','observed_at'];
        IF matches<>1 OR f.original->>'kind' IS DISTINCT FROM 'SPOT_FILL' OR f.receipt->>'state' IS DISTINCT FROM 'APPLIED'
            OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(f.original#>'{command,fills}') leaf WHERE leaf.value=f.observation)
            THEN RETURN false;END IF;
    END LOOP;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

DO $$ DECLARE definition text;needle text;replacement text;BEGIN
    needle:='OR EXISTS(SELECT 1 FROM life_financial.financial_events WHERE account_id=p_account)';
    replacement:='OR NOT life_financial.legacy_spot_prefix_valid(p_account,cmd,p_version)
        OR EXISTS(SELECT 1 FROM life_financial.financial_events e WHERE e.account_id=p_account AND
            (e.product<>''SPOT'' OR e.kind<>''FILL'' OR NOT EXISTS(SELECT 1 FROM life_financial.spot_fill_facts x
                JOIN life_financial.operations o ON o.account_id=x.account_id AND o.operation_id=x.source_operation_id
                WHERE x.account_id=p_account AND x.trade_id=e.exchange_identity AND o.committed_version<=p_version)))';
    definition:=pg_get_functiondef('life_financial.legacy_applying_pending(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'::regprocedure);
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_SPOT_PREFIX_MISMATCH';END IF;
    definition:=replace(definition,needle,replacement);
    -- Receipt JSON uses the same numeric subtraction spelling as the existing
    -- joint proof. Fractional prefix money may yield '0.000', still zero money.
    needle:='''cash_delta_usdt'',''0'',''base_delta_life'',''0''';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_SPOT_PREFIX_MISMATCH';END IF;
    EXECUTE replace(definition,needle,'''cash_delta_usdt'',((money->>''quote'')::numeric-(money->>''quote'')::numeric)::text,
        ''base_delta_life'',((money->>''base'')::numeric-(money->>''base'')::numeric)::text');
    needle:='SELECT NOT EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports i WHERE i.account_id=p_account';
    definition:=pg_get_functiondef('life_financial.legacy_spot_verified(uuid)'::regprocedure);
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_SPOT_PREFIX_MISMATCH';END IF;
    EXECUTE replace(definition,needle,needle||'
    AND NOT life_financial.legacy_spot_prefix_valid(p_account,i.body#>''{payload,batch}'',(i.body#>>''{cut,checkpoint,revision}'')::bigint))
    AND NOT EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports i WHERE i.account_id=p_account');
END; $$;
-- Preserve old function OIDs/ACLs, and do not expose the new proof even when
-- deployment-owner defaults grant EXECUTE to runtime/reporting roles.
DO $$ DECLARE r record;BEGIN
    FOR r IN SELECT p.oid::regprocedure AS signature,x.grantee FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE n.nspname='life_financial' AND p.proname='legacy_spot_prefix_valid' AND x.grantee<>p.proowner LOOP
        IF r.grantee=0 THEN EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',r.signature);
        ELSE EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',r.signature,pg_get_userbyid(r.grantee));END IF;
    END LOOP;
END; $$;
