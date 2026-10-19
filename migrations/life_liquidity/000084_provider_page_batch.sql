-- Bounded diagnostic ingestion only. Each original page still passes the
-- existing account/route/identity/sequence/byte checks; the entire batch commits
-- before the caller may parse it. No financial version or authority changes.
CREATE FUNCTION life_financial.spot_capture_provider_pages(p_account uuid,p_pages jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog AS $$
DECLARE page jsonb; receipt jsonb; receipts jsonb:='[]'::jsonb; replayed boolean:=true;
BEGIN
    IF jsonb_typeof(p_pages) IS DISTINCT FROM 'array' OR jsonb_array_length(p_pages) NOT BETWEEN 1 AND 8
        OR octet_length(p_pages::text)>16777216 THEN
        RAISE EXCEPTION 'LIFE_PROVIDER_BATCH_BOUND' USING ERRCODE='P0001';END IF;
    FOR page IN SELECT value FROM jsonb_array_elements(p_pages) LOOP
        receipt:=life_financial.spot_capture_provider_page(p_account,page);
        receipts:=receipts||jsonb_build_array(receipt);
        replayed:=replayed AND (receipt->>'replayed')::boolean;
    END LOOP;
    RETURN jsonb_build_object('kind','PROVIDER_DECODED_PAGES','pages',receipts,'replayed',replayed,'trading_permission',false);
END; $$;
-- Explicit runtime provisioning is required; deployment defaults cannot grant
-- this entry point to PUBLIC, reporting roles, or unrelated accounts.
DO $$ DECLARE r record;BEGIN
    FOR r IN SELECT DISTINCT x.grantee FROM pg_proc p
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE p.oid='life_financial.spot_capture_provider_pages(uuid,jsonb)'::regprocedure
        AND x.grantee<>p.proowner LOOP
        IF r.grantee=0 THEN
            REVOKE ALL ON FUNCTION life_financial.spot_capture_provider_pages(uuid,jsonb) FROM PUBLIC;
        ELSE
            EXECUTE format('REVOKE ALL ON FUNCTION life_financial.spot_capture_provider_pages(uuid,jsonb) FROM %I',pg_get_userbyid(r.grantee));
        END IF;
    END LOOP;
END; $$;
