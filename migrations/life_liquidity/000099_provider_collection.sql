-- An explicitly bounded diagnostic collection, never a financial authority.
-- Existing single-page/eight-page entrypoints and their bounds are unchanged.
DO $$ DECLARE p record;BEGIN
    FOR p IN SELECT f.*,l.lanname FROM pg_proc f JOIN pg_language l ON l.oid=f.prolang
        WHERE f.oid IN ('life_financial.spot_capture_provider_page(uuid,jsonb)'::regprocedure,
            'life_financial.spot_capture_provider_pages(uuid,jsonb)'::regprocedure) LOOP
        IF p.lanname<>'plpgsql' OR p.provolatile<>'v'
            OR p.prosecdef IS DISTINCT FROM (p.proname='spot_capture_provider_page')
            OR p.proisstrict OR p.proleakproof OR p.proparallel<>'u'
            OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') IS DISTINCT FROM
                (CASE p.proname WHEN 'spot_capture_provider_page' THEN 'e7a51bf1f951b3008492897e201c5bcf86f55f20d92d7368f732183dd54bb4d5'
                    ELSE '78abac55cb894737289cb0699c044268d87821fc097e4579976019c3248d1bf4' END) THEN
            RAISE EXCEPTION 'LIFE_PROVIDER_COLLECTION_MIGRATION_MISMATCH';END IF;
    END LOOP;
END; $$;
CREATE FUNCTION life_financial.spot_capture_provider_collection(p_account uuid,p_pages jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog AS $$
DECLARE item record;first_page jsonb;receipt jsonb;receipts jsonb:='[]'::jsonb;replayed boolean:=true;
BEGIN
    IF jsonb_typeof(p_pages) IS DISTINCT FROM 'array' OR jsonb_array_length(p_pages) NOT BETWEEN 1 AND 512
        OR octet_length(p_pages::text)>16777216 THEN
        RAISE EXCEPTION 'LIFE_PROVIDER_COLLECTION_BOUND' USING ERRCODE='P0001';END IF;
    first_page:=p_pages->0;
    FOR item IN SELECT value,ordinality FROM jsonb_array_elements(p_pages) WITH ORDINALITY LOOP
        IF item.value->'collection_id' IS DISTINCT FROM first_page->'collection_id'
            OR item.value->'binding' IS DISTINCT FROM first_page->'binding'
            OR item.value->'owner' IS DISTINCT FROM first_page->'owner'
            OR item.value->'sequence' IS DISTINCT FROM to_jsonb(item.ordinality) THEN
            RAISE EXCEPTION 'LIFE_PROVIDER_COLLECTION_SCOPE_INVALID' USING ERRCODE='P0001';END IF;
        -- Every original route, owner, immutable identity, sequence, page and
        -- total byte constraint is still enforced by the existing entrypoint.
        receipt:=life_financial.spot_capture_provider_page(p_account,item.value);
        receipts:=receipts||jsonb_build_array(receipt);
        replayed:=replayed AND (receipt->>'replayed')::boolean;
    END LOOP;
    RETURN jsonb_build_object('kind','PROVIDER_DECODED_COLLECTION','collection_id',first_page->'collection_id',
        'pages',receipts,'replayed',replayed,'trading_permission',false);
END; $$;

-- Ignore hostile deployment defaults. Upgrade only explicit existing batch
-- writers; fresh provisioning independently installs the same writer grant.
DO $$ DECLARE r record;BEGIN
    FOR r IN SELECT DISTINCT x.grantee FROM pg_proc p
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE p.oid='life_financial.spot_capture_provider_collection(uuid,jsonb)'::regprocedure
        AND x.grantee<>p.proowner LOOP
        IF r.grantee=0 THEN
            REVOKE ALL ON FUNCTION life_financial.spot_capture_provider_collection(uuid,jsonb) FROM PUBLIC;
        ELSE
            EXECUTE format('REVOKE ALL ON FUNCTION life_financial.spot_capture_provider_collection(uuid,jsonb) FROM %I',pg_get_userbyid(r.grantee));
        END IF;
    END LOOP;
    FOR r IN SELECT DISTINCT x.grantee FROM pg_proc p
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE p.oid='life_financial.spot_capture_provider_pages(uuid,jsonb)'::regprocedure
        AND x.grantee<>0 AND x.grantee<>p.proowner AND x.privilege_type='EXECUTE' LOOP
        EXECUTE format('GRANT EXECUTE ON FUNCTION life_financial.spot_capture_provider_collection(uuid,jsonb) TO %I',pg_get_userbyid(r.grantee));
    END LOOP;
END; $$;
