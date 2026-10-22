-- Read-only recorder provenance admission. No reporting table DML is granted
-- to the LIFE runtime/reporting roles; unrelated source namespaces are excluded.
CREATE FUNCTION life_financial.recorder_scope_verified(p_account uuid,p_controller text,p_sources jsonb) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE registered jsonb;
BEGIN
    IF p_account IS NULL OR p_controller IS NULL OR p_sources IS NULL
        OR octet_length(p_sources::text)>4096 OR length(p_controller)>128
        OR NOT life_financial.can_access(p_account,false) THEN RETURN false;END IF;
    SELECT c.body->'recorder_sources' INTO registered FROM life_financial.accounts a
        JOIN life_financial.configurations c ON c.account_id=a.account_id AND c.config_hash=a.config_hash
        WHERE a.account_id=p_account;
    IF jsonb_typeof(registered) IS DISTINCT FROM 'array' OR jsonb_typeof(p_sources)<>'array'
        OR jsonb_array_length(registered) NOT BETWEEN 1 AND 2
        OR jsonb_array_length(p_sources)<>jsonb_array_length(registered)
        OR NOT (p_sources @> registered AND registered @> p_sources)
        OR EXISTS(SELECT 1 FROM jsonb_array_elements(registered) s WHERE s->>'controller_id' IS DISTINCT FROM p_controller)
        THEN RETURN false;END IF;
    RETURN NOT (EXISTS(SELECT 1 FROM life_recorder."OrderStatus" r JOIN life_recorder."Order" o ON o.id=r.order_id
        WHERE EXISTS(SELECT 1 FROM jsonb_to_recordset(p_sources) AS src(config_file_path text,market text)
            WHERE src.config_file_path=o.config_file_path AND src.market=o.market)
        AND NOT EXISTS(SELECT 1 FROM life_financial.recorder_projection_heads h
            JOIN life_financial.recorder_projection_effects e USING(account_id,projection_id,table_kind,key_hash)
            WHERE h.account_id=p_account AND h.table_kind='STATUS'
                AND h.key_hash=encode(sha256(convert_to(jsonb_build_object('order_id',r.order_id,'timestamp',r.timestamp,'status',r.status)::text,'UTF8')),'hex')
                AND e.after_row=to_jsonb(r)-'id')) OR EXISTS(SELECT 1 FROM life_recorder."Order" r WHERE EXISTS(SELECT 1 FROM jsonb_to_recordset(p_sources::jsonb) AS src(config_file_path text,market text)
                    WHERE src.config_file_path=r.config_file_path AND src.market=r.market) AND NOT EXISTS(
            SELECT 1 FROM life_financial.recorder_projection_heads h
            JOIN life_financial.recorder_projection_effects e USING(account_id,projection_id,table_kind,key_hash)
            WHERE h.account_id=p_account AND h.table_kind='ORDER'
                AND h.key_hash=encode(sha256(convert_to((jsonb_build_object('id',r.id))::text,'UTF8')),'hex')
                AND e.after_row=to_jsonb(r))) OR EXISTS(SELECT 1 FROM life_recorder."TradeFill" r WHERE EXISTS(SELECT 1 FROM jsonb_to_recordset(p_sources::jsonb) AS src(config_file_path text,market text)
                    WHERE src.config_file_path=r.config_file_path AND src.market=r.market) AND NOT EXISTS(
            SELECT 1 FROM life_financial.recorder_projection_heads h
            JOIN life_financial.recorder_projection_effects e USING(account_id,projection_id,table_kind,key_hash)
            WHERE h.account_id=p_account AND h.table_kind='TRADE'
                AND h.key_hash=encode(sha256(convert_to((jsonb_build_object('market',r.market,'order_id',r.order_id,'exchange_trade_id',r.exchange_trade_id))::text,'UTF8')),'hex')
                AND e.after_row=to_jsonb(r))) OR EXISTS(SELECT 1 FROM life_recorder."FundingPayment" r WHERE EXISTS(SELECT 1 FROM jsonb_to_recordset(p_sources::jsonb) AS src(config_file_path text,market text)
                    WHERE src.config_file_path=r.config_file_path AND src.market=r.market) AND NOT EXISTS(
            SELECT 1 FROM life_financial.recorder_projection_heads h
            JOIN life_financial.recorder_projection_effects e USING(account_id,projection_id,table_kind,key_hash)
            WHERE h.account_id=p_account AND h.table_kind='FUNDING'
                AND h.key_hash=encode(sha256(convert_to((jsonb_build_object('timestamp',r.timestamp))::text,'UTF8')),'hex')
                AND e.after_row=to_jsonb(r))) OR EXISTS(SELECT 1 FROM life_recorder."MarketState" r WHERE EXISTS(SELECT 1 FROM jsonb_to_recordset(p_sources::jsonb) AS src(config_file_path text,market text)
                    WHERE src.config_file_path=r.config_file_path AND src.market=r.market) AND NOT EXISTS(
            SELECT 1 FROM life_financial.recorder_projection_heads h
            JOIN life_financial.recorder_projection_effects e USING(account_id,projection_id,table_kind,key_hash)
            WHERE h.account_id=p_account AND h.table_kind='STATE'
                AND h.key_hash=encode(sha256(convert_to((jsonb_build_object('config_file_path',r.config_file_path,'market',r.market))::text,'UTF8')),'hex')
                AND e.after_row=to_jsonb(r)-'id')) OR EXISTS(SELECT 1 FROM life_recorder."Position" r WHERE r.controller_id=p_controller AND NOT EXISTS(
            SELECT 1 FROM life_financial.recorder_projection_heads h
            JOIN life_financial.recorder_projection_effects e USING(account_id,projection_id,table_kind,key_hash)
            WHERE h.account_id=p_account AND h.table_kind='POSITION'
                AND h.key_hash=encode(sha256(convert_to((jsonb_build_object('id',r.id))::text,'UTF8')),'hex')
                AND e.after_row=to_jsonb(r))) OR EXISTS(SELECT 1 FROM life_recorder."Executors" r WHERE r.controller_id=p_controller AND NOT EXISTS(
        SELECT 1 FROM life_financial.recorder_outbox o WHERE o.account_id=p_account
            AND o.stream='executor:'||encode(sha256(convert_to(r.id,'UTF8')),'hex') AND o.sequence=1)));
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
DO $$ DECLARE r record;f regprocedure:='life_financial.recorder_scope_verified(uuid,text,jsonb)'::regprocedure;BEGIN
    -- Scrub hostile inherited/default grants before copying the existing
    -- account-scoped recorder read profile. This adds no financial mutation.
    FOR r IN SELECT DISTINCT x.grantee FROM pg_proc p
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE p.oid=f AND x.grantee<>p.proowner LOOP
        IF r.grantee=0 THEN EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',f);
        ELSE EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',f,pg_get_userbyid(r.grantee));END IF;
    END LOOP;
    FOR r IN SELECT DISTINCT x.grantee FROM pg_proc p
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE p.oid='life_financial.recorder_executor_verified(uuid)'::regprocedure
            AND x.grantee<>0 AND x.grantee<>p.proowner AND x.privilege_type='EXECUTE' LOOP
        EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %I',f,pg_get_userbyid(r.grantee));
    END LOOP;
END; $$;
