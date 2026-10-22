-- A locally created provisional executor can stop before native PREPARE.
-- Retain its diagnostics under the original birth; never invent a financial
-- action descriptor or a sent attempt to make legacy reporting succeed.
DO $$ DECLARE p record;BEGIN
    SELECT * INTO STRICT p FROM pg_proc WHERE oid='life_financial.recorder_executor_origin(uuid,jsonb,jsonb)'::regprocedure;
    IF NOT p.prosecdef OR p.provolatile<>'s' OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
        OR p.proisstrict OR p.proleakproof OR p.proparallel<>'u' OR p.proretset OR p.prokind<>'f'
        OR p.pronargdefaults<>0 OR p.prorettype<>'void'::regtype
        OR (SELECT lanname FROM pg_language WHERE oid=p.prolang)<>'plpgsql'
        OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')<>'7130a1974d765042900bb4dc0a408ab01205fd4db8609bb5ed7bf369763dfa88' THEN
        RAISE EXCEPTION 'LIFE_PROVISIONAL_RECORDER_MIGRATION_MISMATCH';END IF;
END; $$;
CREATE OR REPLACE FUNCTION life_financial.recorder_executor_origin(p_account uuid,p_binding jsonb,p jsonb) RETURNS void
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE wires jsonb;birth jsonb;wire text;
BEGIN
    PERFORM life_financial.recorder_executor_row(p);
    wires:=p#>'{custom_info,recovery_order_ids}';
    IF p#>'{custom_info,life_storage_binding}' IS DISTINCT FROM p_binding
        OR p#>>'{custom_info,recovery_account_uid}' IS DISTINCT FROM p_binding->>'account_uid'
        OR jsonb_typeof(wires) IS DISTINCT FROM 'array'
        OR jsonb_array_length(wires)>4096
        OR (SELECT count(*)<>count(DISTINCT value) FROM jsonb_array_elements(wires))
        OR EXISTS(SELECT 1 FROM jsonb_array_elements(wires) WHERE jsonb_typeof(value)<>'string') THEN
        RAISE EXCEPTION 'LIFE_RECORDER_ORIGIN_INVALID' USING ERRCODE='P0001';END IF;
    IF NOT EXISTS(SELECT 1 FROM life_financial.spot_action_descriptors d WHERE d.account_id=p_account
        AND d.action_id=p->>'id' AND d.descriptor->>'controller_id'=p->>'controller_id'
        AND (d.descriptor->>'payload_json')::jsonb=p->'config') THEN
        SELECT body INTO birth FROM life_financial.quote_births WHERE account_id=p_account AND intent_id=p->>'id';
        IF NOT FOUND OR NOT life_financial.quote_birth_verified(p_account)
            OR birth->'binding' IS DISTINCT FROM p_binding
            OR birth#>>'{action,controller_id}' IS DISTINCT FROM p->>'controller_id'
            OR (birth#>>'{action,payload_json}')::jsonb IS DISTINCT FROM p->'config'
            OR EXISTS(SELECT 1 FROM life_financial.quote_birth_consumptions WHERE account_id=p_account AND ticket_id=birth->>'ticket_id')
            OR EXISTS(SELECT 1 FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=p->>'id')
            OR (p->>'filled_amount_quote')::numeric<>0 OR (p->>'cum_fees_quote')::numeric<>0
            OR (p->>'net_pnl_quote')::numeric<>0 OR (p->>'net_pnl_pct')::numeric<>0 THEN
            RAISE EXCEPTION 'LIFE_RECORDER_ORIGIN_INVALID' USING ERRCODE='P0001';END IF;
        wire:='L'||substr(encode(sha256(convert_to(life_financial.canonical_json_v1(jsonb_build_object(
            'kind','native-spot-intent-v1','intent_id',p->>'id','controller_id',p->>'controller_id',
            'owner',birth->'owner','config_version',birth#>'{action,config_version}',
            'side',birth#>'{action,side}','level',birth#>'{action,level}',
            'quantity_base',p#>'{config,amount}','price_usdt',p#>'{config,price}')),'UTF8')),'hex'),1,31);
        IF wires<>'[]'::jsonb AND wires IS DISTINCT FROM jsonb_build_array(wire) THEN
            RAISE EXCEPTION 'LIFE_RECORDER_ORIGIN_INVALID' USING ERRCODE='P0001';END IF;
        -- This opaque local wire is diagnostic only. It is not inserted into
        -- send_attempts or accepted by provider financial/event reconciliation.
        RETURN;
    END IF;
    IF EXISTS(SELECT 1 FROM jsonb_array_elements_text(wires) w(wire) WHERE NOT EXISTS(
        SELECT 1 FROM life_financial.send_attempts t WHERE t.account_id=p_account AND t.intent_id=p->>'id'
            AND t.wire_id=w.wire AND t.product='SPOT')) THEN
        RAISE EXCEPTION 'LIFE_RECORDER_ORIGIN_INVALID' USING ERRCODE='P0001';END IF;
END; $$;
