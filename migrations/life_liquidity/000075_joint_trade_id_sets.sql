-- Legacy flat account_trade_ids is a set, not a globally unique trade key.
-- Actual fills retain their permanent (account, product, trade_id) identities.
-- Keep complete account coverage, including SWAP IDs; do not filter to SPOT.
DO $$ DECLARE item record;p record;definition text;replacement text;BEGIN
    FOR item IN SELECT * FROM (VALUES
        ('life_financial.spot_order_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb,boolean)',
         '8b960a9ce847973c3482afc7b99d7989e19cc7b5a7da7077be7d51872d4990e2',
         'jsonb_agg(trade_id ORDER BY trade_id)'),
        ('life_financial.spot_settle_inventory(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb,jsonb,boolean)',
         'e2b0583383da1fa8f484e9378f0cf8a496bcf1a787937960e96b73a778cd6e58',
         'jsonb_agg(trade_id ORDER BY trade_id COLLATE "C")')) AS x(signature,source_hash,needle) LOOP
        SELECT * INTO STRICT p FROM pg_proc WHERE oid=item.signature::regprocedure;
        IF p.provolatile<>'v' OR NOT p.prosecdef OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')<>item.source_hash
            OR (length(p.prosrc)-length(replace(p.prosrc,item.needle,'')))/length(item.needle)<>1 THEN
            RAISE EXCEPTION 'LIFE_JOINT_TRADE_SET_MIGRATION_MISMATCH';END IF;
        definition:=pg_get_functiondef(p.oid);
        replacement:='jsonb_agg(DISTINCT trade_id COLLATE "C" ORDER BY trade_id COLLATE "C")';
        EXECUTE replace(definition,item.needle,replacement);
    END LOOP;
END; $$;
