-- One primary MVCC cut for signed account-wide collateral reconciliation.
-- Observation only: HALT is allowed; unresolved or corrupt money never is.
-- No funds, policy, approval, permission or release is created here.
CREATE FUNCTION life_financial.collateral_reconciliation_context(p_account uuid,p_rows integer,p_bytes integer) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE view jsonb;policy jsonb;legs jsonb;result jsonb;n bigint;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN
        RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID' USING ERRCODE='P0001';END IF;
    IF p_rows IS NULL OR p_rows NOT BETWEEN 1 AND 4096 OR p_bytes IS NULL OR p_bytes NOT BETWEEN 1 AND 16777216 THEN
        RAISE EXCEPTION 'LIFE_COLLATERAL_READ_BOUND_INVALID' USING ERRCODE='P0001';END IF;
    SELECT count(*) INTO n FROM (
        SELECT trade_id::text FROM life_financial.swap_fill_facts WHERE account_id=p_account
        UNION ALL SELECT trade_id::text FROM life_financial.spot_fill_facts WHERE account_id=p_account
        UNION ALL SELECT intent_id::text FROM life_financial.shared_capital_claims WHERE account_id=p_account
        LIMIT p_rows+1) x;
    IF n>p_rows THEN RAISE EXCEPTION 'LIFE_COLLATERAL_READ_BOUND' USING ERRCODE='P0001';END IF;
    view:=life_financial.spot_state(p_account);
    IF view->'unverified' IS DISTINCT FROM 'false'::jsonb THEN
        RAISE EXCEPTION 'LIFE_COLLATERAL_NATIVE_UNVERIFIED' USING ERRCODE='P0001';END IF;
    IF life_financial.joint_financial_pending(p_account) THEN
        RAISE EXCEPTION 'LIFE_COLLATERAL_PENDING_RESOLUTION_REQUIRED' USING ERRCODE='P0001';END IF;
    IF EXISTS(SELECT 1 FROM life_financial.balances WHERE account_id=p_account AND liability<>0) THEN
        RAISE EXCEPTION 'LIFE_COLLATERAL_LIABILITY_NONZERO' USING ERRCODE='P0001';END IF;
    SELECT body INTO policy FROM life_financial.shared_capital_policies
        WHERE account_id=p_account AND policy_hash=view#>>'{account,policy_hash}';
    legs:=life_financial.swap_fill_replay(p_account);
    IF policy IS NULL OR legs IS NULL THEN
        RAISE EXCEPTION 'LIFE_COLLATERAL_NATIVE_BASIS_REQUIRED' USING ERRCODE='P0001';END IF;
    result:=jsonb_build_object('snapshot',view,'positions',legs,'capital_policy',policy,'trading_permission',false,
        -- An idle opening may predate import. Subsequent actual cash/position
        -- changes cannot be explained by a provider update from before them.
        'money_update_ms',(SELECT jsonb_build_object(
            'LIFE',COALESCE(floor(extract(epoch FROM max(occurred_at) FILTER(WHERE base_delta<>0))*1000)::bigint,0),
            'USDT',COALESCE(floor(extract(epoch FROM max(occurred_at) FILTER(WHERE quote_delta<>0))*1000)::bigint,0))
            FROM life_financial.financial_events WHERE account_id=p_account),
        'position_update_ms',(SELECT jsonb_build_object(
            'net',COALESCE(max((f.observation->>'fill_at_ms')::bigint),0),
            'long',COALESCE(max((f.observation->>'fill_at_ms')::bigint) FILTER(WHERE
                t.side='BUY' AND d.permit->>'position_action'='OPEN' OR t.side='SELL' AND d.permit->>'position_action'='CLOSE'),0),
            'short',COALESCE(max((f.observation->>'fill_at_ms')::bigint) FILTER(WHERE
                t.side='SELL' AND d.permit->>'position_action'='OPEN' OR t.side='BUY' AND d.permit->>'position_action'='CLOSE'),0))
            FROM life_financial.swap_fill_facts f JOIN life_financial.send_attempts t USING(account_id,intent_id)
            JOIN life_financial.swap_preparations d USING(account_id,intent_id) WHERE f.account_id=p_account));
    IF octet_length(result::text)>p_bytes THEN RAISE EXCEPTION 'LIFE_COLLATERAL_READ_BOUND' USING ERRCODE='P0001';END IF;
    RETURN result;
END; $$;
-- Do not inherit permissive deployment default privileges.
DO $$ DECLARE r record;BEGIN
    FOR r IN SELECT DISTINCT x.grantee FROM pg_proc p
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE p.oid='life_financial.collateral_reconciliation_context(uuid,integer,integer)'::regprocedure
        AND x.grantee<>p.proowner LOOP
        IF r.grantee=0 THEN
            REVOKE ALL ON FUNCTION life_financial.collateral_reconciliation_context(uuid,integer,integer) FROM PUBLIC;
        ELSE
            EXECUTE format('REVOKE ALL ON FUNCTION life_financial.collateral_reconciliation_context(uuid,integer,integer) FROM %I',pg_get_userbyid(r.grantee));
        END IF;
    END LOOP;
END; $$;

-- Extend only the immutable diagnostic route whitelist. Both requests are
-- account-wide signed GETs; adding a filter would hide foreign exposure.
DO $$ DECLARE definition text;needle text;BEGIN
    definition:=pg_get_functiondef('life_financial.spot_provider_route(text,jsonb)'::regprocedure);
    needle:='        WHEN ''/api/v5/account/balance'' THEN ''{}''::jsonb ELSE NULL END;';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_COLLATERAL_SOURCE_ROUTE_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,
        '        WHEN ''/api/v5/account/config'' THEN ''{}''::jsonb
        WHEN ''/api/v5/account/positions'' THEN ''{}''::jsonb
'||needle);
END; $$;
