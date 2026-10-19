-- Fixed instrument market/fee diagnostics only. No source or send authority.
-- Audited in-place extension preserves original OID, ACL and callers.
DO $$ DECLARE p record;BEGIN
    SELECT * INTO STRICT p FROM pg_proc WHERE oid='life_financial.spot_provider_route(text,jsonb)'::regprocedure;
    IF p.provolatile<>'i' OR p.prosecdef OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
        OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')<>'bbfcb4bf6d2c344338095e44757e4d046b89e78cae92fa9fb1205704db6bca9b' THEN
        RAISE EXCEPTION 'LIFE_HEDGE_ROUTE_MIGRATION_MISMATCH';END IF;
END; $$;
CREATE OR REPLACE FUNCTION life_financial.spot_provider_route(p_path text,p_params jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE base jsonb; extra jsonb;
BEGIN
    IF jsonb_typeof(p_params) IS DISTINCT FROM 'object' OR EXISTS(
        SELECT 1 FROM jsonb_each(p_params) x WHERE jsonb_typeof(x.value)<>'string') THEN RETURN false; END IF;
    -- Omitting instType is intentional for complete dedicated-account scope:
    -- an empty position does not prove absence of another product's orders.
    IF p_path IN ('/api/v5/trade/orders-pending','/api/v5/trade/orders-algo-pending') AND NOT p_params ? 'instType' THEN
        IF p_path='/api/v5/trade/orders-algo-pending' AND p_params='{"limit":"100","ordType":"chase"}'::jsonb THEN RETURN true; END IF;
        RETURN life_financial.spot_provider_route(p_path,p_params||'{"instType":"SPOT"}'::jsonb);
    END IF;
    -- Map only the fixed derivative product into the audited exact query
    -- grammar. Pending orders must remain account-wide within the product.
    IF p_path IN ('/api/v5/trade/fills-history','/api/v5/trade/orders-pending','/api/v5/trade/orders-algo-pending')
        AND p_params->>'instType'='SWAP' THEN
        IF p_params ? 'instId' AND p_params->>'instId'<>'LIFE-USDT-SWAP' THEN RETURN false; END IF;
        IF p_path='/api/v5/trade/orders-algo-pending'
            AND p_params='{"instType":"SWAP","limit":"100","ordType":"chase"}'::jsonb THEN RETURN true; END IF;
        extra:=jsonb_set(p_params,'{instType}','"SPOT"'::jsonb);
        IF extra ? 'instId' THEN extra:=jsonb_set(extra,'{instId}','"LIFE-USDT"'::jsonb); END IF;
        RETURN life_financial.spot_provider_route(p_path,extra);
    END IF;
    IF p_path='/api/v5/trade/order' AND p_params->>'instId'='LIFE-USDT-SWAP' THEN
        RETURN life_financial.spot_provider_route(p_path,jsonb_set(p_params,'{instId}','"LIFE-USDT"'::jsonb));
    END IF;
    base:=CASE p_path
        WHEN '/api/v5/public/instruments' THEN '{"instType":"SWAP","instId":"LIFE-USDT-SWAP"}'::jsonb
        WHEN '/api/v5/market/books' THEN '{"instId":"LIFE-USDT-SWAP","sz":"64"}'::jsonb
        WHEN '/api/v5/public/mark-price' THEN '{"instType":"SWAP","instId":"LIFE-USDT-SWAP"}'::jsonb
        WHEN '/api/v5/market/index-tickers' THEN '{"instId":"LIFE-USDT"}'::jsonb
        WHEN '/api/v5/public/funding-rate' THEN '{"instId":"LIFE-USDT-SWAP"}'::jsonb
        WHEN '/api/v5/public/position-tiers' THEN '{"instType":"SWAP","tdMode":"cross","instFamily":"LIFE-USDT"}'::jsonb
        WHEN '/api/v5/account/trade-fee' THEN '{"instType":"SWAP","instFamily":"LIFE-USDT"}'::jsonb
        WHEN '/api/v5/account/leverage-info' THEN '{"instId":"LIFE-USDT-SWAP","mgnMode":"cross"}'::jsonb
        ELSE NULL END;
    IF base IS NOT NULL THEN RETURN p_params=base; END IF;
    base:=CASE p_path
        WHEN '/api/v5/account/bills-archive' THEN '{"limit":"100"}'::jsonb
        WHEN '/api/v5/trade/fills-history' THEN '{"instType":"SPOT","limit":"100"}'::jsonb
        WHEN '/api/v5/trade/orders-pending' THEN '{"instType":"SPOT","limit":"100"}'::jsonb
        WHEN '/api/v5/trade/orders-algo-pending' THEN '{"instType":"SPOT","limit":"100"}'::jsonb
        WHEN '/api/v5/trade/order' THEN '{"instId":"LIFE-USDT"}'::jsonb
        WHEN '/api/v5/account/config' THEN '{}'::jsonb
        WHEN '/api/v5/account/positions' THEN '{}'::jsonb
        WHEN '/api/v5/account/balance' THEN '{}'::jsonb ELSE NULL END;
    IF base IS NULL OR NOT p_params @> base THEN RETURN false; END IF;
    SELECT COALESCE(jsonb_object_agg(key,value),'{}'::jsonb) INTO extra FROM jsonb_each(p_params)
        WHERE NOT base ? key;
    IF p_path IN ('/api/v5/account/bills-archive','/api/v5/trade/fills-history','/api/v5/trade/orders-pending') THEN
        IF extra ? 'after' THEN
            IF extra->>'after' !~ '^[0-9]{1,96}$' THEN RETURN false; END IF;
            extra:=extra-'after';
        END IF;
        IF p_path='/api/v5/trade/fills-history' AND extra ?& ARRAY['instId','ordId']
            AND (SELECT count(*) FROM jsonb_object_keys(extra))=2 THEN
            IF extra->>'instId'<>'LIFE-USDT' THEN RETURN false; END IF;
            PERFORM (extra->>'ordId')::life_financial.identity;
            extra:='{}'::jsonb;
        END IF;
    ELSIF p_path='/api/v5/trade/order' THEN
        IF (SELECT count(*) FROM jsonb_object_keys(extra))<>1 OR NOT extra ? 'clOrdId' THEN RETURN false; END IF;
        PERFORM (extra->>'clOrdId')::life_financial.identity;
        extra:='{}'::jsonb;
    ELSIF p_path='/api/v5/trade/orders-algo-pending' THEN
        IF (SELECT count(*) FROM jsonb_object_keys(extra))<>1 OR NOT extra ? 'ordType'
            OR extra->>'ordType' NOT IN ('conditional,oco','trigger','move_order_stop','iceberg','twap','smart_iceberg') THEN RETURN false; END IF;
        extra:='{}'::jsonb;
    END IF;
    RETURN extra='{}'::jsonb;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
