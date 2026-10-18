-- Retired transfers require independently approved ORIGINAL bills.
-- This creates no approval, wallet, owner, policy or financial application.
CREATE FUNCTION life_financial.legacy_cashflow_basis_valid(p_account uuid,b jsonb) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r jsonb;a life_financial.bill_approvals;
BEGIN
    IF jsonb_typeof(b->'approved_cashflows') IS DISTINCT FROM 'array'
        OR jsonb_array_length(b->'approved_cashflows')>256
        OR (SELECT count(DISTINCT value->>'bill_id') FROM jsonb_array_elements(b->'approved_cashflows'))<>jsonb_array_length(b->'approved_cashflows') THEN RETURN false;END IF;
    FOR r IN SELECT value FROM jsonb_array_elements(b->'approved_cashflows') LOOP
        SELECT * INTO STRICT a FROM life_financial.bill_approvals WHERE account_id=p_account AND bill_id=r->>'bill_id';
        IF a.asset IS DISTINCT FROM r->>'currency' OR a.signed_amount IS DISTINCT FROM (r->>'signed_amount')::numeric
            OR extract(epoch FROM a.transfer_at)*1000 IS DISTINCT FROM (r->>'transfer_at_ms')::numeric
            OR octet_length(a.signature)=0 THEN RETURN false;END IF;
    END LOOP;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
CREATE FUNCTION life_financial.legacy_cashflow_verified(p_account uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT NOT EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports i WHERE i.account_id=p_account
    AND NOT life_financial.legacy_cashflow_basis_valid(p_account,i.body->'source_basis'));
$$;
DO $$ DECLARE definition text;needle text:='IF NOT life_financial.legacy_joint_verified(p_account) THEN RETURN false;END IF;';BEGIN
    definition:=pg_get_functiondef('life_financial.legacy_applying_verified(uuid)'::regprocedure);
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_CASHFLOW_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,needle||'
    IF NOT life_financial.legacy_cashflow_verified(p_account) THEN RETURN false;END IF;');
END; $$;
-- Exchange transfer times and File replay are milliseconds. Comparing one of
-- these with a microsecond opening timestamp falsely rejects a same-ms bill;
-- use the existing causal-replay unit on both admission paths. A previous-ms
-- transfer is still before opening and must be refused.
DO $$ DECLARE signature text;definition text;needle text:='b.transfer_at<p.opened_at';BEGIN
    FOREACH signature IN ARRAY ARRAY[
        'life_financial.spot_capital_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)',
        'life_financial.joint_stage_cashflow(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)'] LOOP
        definition:=pg_get_functiondef(signature::regprocedure);
        IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_CASHFLOW_TIME_MISMATCH';END IF;
        EXECUTE replace(definition,needle,'at_ms<floor(extract(epoch FROM p.opened_at)*1000)');
    END LOOP;
END; $$;
-- Keep the original public error while retaining the bounded kernel reason in
-- PostgreSQL diagnostics. No request, source body, identity or secret is logged.
DO $$ DECLARE definition text;needle text:='RAISE EXCEPTION ''LIFE_JOINT_REPAIR_REPLACEMENT_REJECTED'' USING ERRCODE=''P0001'';';BEGIN
    definition:=pg_get_functiondef('life_financial.joint_repair_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'::regprocedure);
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_REPAIR_REASON_MISMATCH';END IF;
    EXECUTE replace(definition,needle,'RAISE EXCEPTION ''LIFE_JOINT_REPAIR_REPLACEMENT_REJECTED'' USING ERRCODE=''P0001'', DETAIL=CASE WHEN child->>''reason'' ~ ''^LIFE_[A-Z_]{1,96}$'' THEN child->>''reason'' ELSE ''LIFE_JOINT_REASON_UNAVAILABLE'' END;');
END; $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
