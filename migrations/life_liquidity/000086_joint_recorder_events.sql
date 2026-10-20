-- Extend diagnostic order/fill/status reporting to original native SWAP intents.
-- Preserve the original validator OID/ACL, SPOT descriptor rules and no new grants.
DO $$ DECLARE p record; BEGIN
    SELECT * INTO STRICT p FROM pg_proc WHERE oid='life_financial.recorder_event_validate(uuid,jsonb,jsonb)'::regprocedure;
    IF p.provolatile<>'s' OR NOT p.prosecdef OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
        OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')<>'0780eddd4674ca632d50e870e6c32a22fb45b9b6f01ad82dd1fdea49965e44a8' THEN
        RAISE EXCEPTION 'LIFE_JOINT_EVENT_MIGRATION_MISMATCH'; END IF;
END; $$;
CREATE OR REPLACE FUNCTION life_financial.recorder_event_validate(p_account uuid,p_binding jsonb,p_command jsonb) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE src jsonb; p jsonb; tracking jsonb; t life_financial.send_attempts; k text; known boolean; swap_permit jsonb;
BEGIN
    IF jsonb_typeof(p_command) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(p_command))<>4
        OR NOT p_command ?& ARRAY['binding','source','payload_json','tracking_json'] OR p_command->'binding' IS DISTINCT FROM p_binding
        OR jsonb_typeof(p_command->'payload_json') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_command->'tracking_json') IS DISTINCT FROM 'string' OR octet_length(p_command::text)>1048576 THEN
        RAISE EXCEPTION 'LIFE_EVENT_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    src:=p_command->'source'; p:=(p_command->>'payload_json')::jsonb; tracking:=(p_command->>'tracking_json')::jsonb;
    IF jsonb_typeof(src) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(src))<>4
        OR NOT src ?& ARRAY['controller_id','config_file_path','strategy_name','market']
        OR src->>'market' NOT IN ('okx','okx_perpetual')
        OR EXISTS(SELECT 1 FROM jsonb_each(src) WHERE jsonb_typeof(value)<>'string' OR length(value#>>'{}') NOT BETWEEN 1 AND 255)
        OR jsonb_typeof(p) IS DISTINCT FROM 'object' OR jsonb_typeof(tracking) IS DISTINCT FROM 'object'
        OR NOT EXISTS(SELECT 1 FROM life_financial.configurations c CROSS JOIN LATERAL jsonb_array_elements(c.body->'recorder_sources') r(value)
            WHERE c.account_id=p_account AND c.config_hash=p_binding->>'config_hash' AND c.policy_hash=p_binding->>'policy_hash' AND r.value=src) THEN
        RAISE EXCEPTION 'LIFE_EVENT_SOURCE_INVALID' USING ERRCODE='P0001'; END IF;
    PERFORM (src->>'controller_id')::life_financial.identity;
    IF jsonb_typeof(p->'timestamp_ms') IS DISTINCT FROM 'number' OR p->>'timestamp_ms' !~ '^(0|[1-9][0-9]*)$'
        OR (p->>'timestamp_ms')::numeric>=9223372036854775808::numeric
        OR jsonb_typeof(p->'kind') IS DISTINCT FROM 'string' THEN
        RAISE EXCEPTION 'LIFE_EVENT_PAYLOAD_INVALID' USING ERRCODE='P0001'; END IF;
    IF p->>'kind'='FundingPaymentCompleted' THEN
        IF src->>'market'<>'okx_perpetual' OR (SELECT count(*) FROM jsonb_object_keys(p))<>5
            OR NOT p ?& ARRAY['kind','timestamp_ms','pair','rate','amount'] OR p->>'pair' IS DISTINCT FROM 'LIFE-USDT'
            OR jsonb_typeof(p->'rate') IS DISTINCT FROM 'string' OR jsonb_typeof(p->'amount') IS DISTINCT FROM 'string'
            OR NOT ((p->>'rate')::numeric>'-Infinity'::numeric AND (p->>'rate')::numeric<'Infinity'::numeric
                AND (p->>'amount')::numeric>'-Infinity'::numeric AND (p->>'amount')::numeric<'Infinity'::numeric) THEN
            RAISE EXCEPTION 'LIFE_EVENT_FUNDING_INVALID' USING ERRCODE='P0001'; END IF;
        PERFORM (p->>'rate')::float8; PERFORM (p->>'amount')::float8;
    ELSE
        IF jsonb_typeof(p->'wire') IS DISTINCT FROM 'string'
            OR NOT p ? 'exchange_id' OR p->'exchange_id'<>'null'::jsonb AND jsonb_typeof(p->'exchange_id')<>'string' THEN
            RAISE EXCEPTION 'LIFE_EVENT_WIRE_INVALID' USING ERRCODE='P0001'; END IF;
        PERFORM (p->>'wire')::life_financial.identity;
        IF p->'exchange_id'<>'null'::jsonb THEN PERFORM (p->>'exchange_id')::life_financial.identity; END IF;
        IF src->>'market'='okx' THEN
            SELECT a.* INTO t FROM life_financial.send_attempts a JOIN life_financial.spot_action_descriptors d
                ON d.account_id=a.account_id AND d.action_id=a.intent_id
                WHERE a.account_id=p_account AND a.wire_id=p->>'wire' AND a.product='SPOT' AND d.descriptor->>'controller_id'=src->>'controller_id';
        ELSE
            SELECT a.* INTO t FROM life_financial.send_attempts a JOIN life_financial.swap_preparations d
                ON d.account_id=a.account_id AND d.intent_id=a.intent_id AND d.operation_id=a.operation_id
                WHERE a.account_id=p_account AND a.wire_id=p->>'wire' AND a.product='SWAP'
                    AND d.request#>>'{config,controller_id}'=src->>'controller_id';
            SELECT permit INTO swap_permit FROM life_financial.swap_preparations
                WHERE account_id=p_account AND intent_id=t.intent_id AND operation_id=t.operation_id;
        END IF;
        IF t.account_id IS NULL OR t.exchange_order_id IS NOT NULL AND p->'exchange_id'<>'null'::jsonb AND p->>'exchange_id'<>t.exchange_order_id THEN
            RAISE EXCEPTION 'LIFE_EVENT_WIRE_INVALID' USING ERRCODE='P0001'; END IF;
        IF p->>'kind' IN ('BuyOrderCreated','SellOrderCreated','OrderFilled') THEN
            IF (SELECT count(*) FROM jsonb_object_keys(p))<>(CASE WHEN p->>'kind'='OrderFilled' THEN 14 ELSE 11 END)
                OR NOT p ?& ARRAY['kind','timestamp_ms','wire','pair','exchange_id','order_type','side','amount','price','leverage','position']
                OR p->>'pair' IS DISTINCT FROM 'LIFE-USDT' OR p->>'side' IS DISTINCT FROM t.side
                OR p->>'order_type' IS DISTINCT FROM 'LIMIT_MAKER' OR jsonb_typeof(p->'position') IS DISTINCT FROM 'string' OR p->>'position' NOT IN ('OPEN','NIL','CLOSE')
                OR jsonb_typeof(p->'amount') IS DISTINCT FROM 'string' OR jsonb_typeof(p->'price') IS DISTINCT FROM 'string'
                OR (p->>'amount')::numeric<=0 OR (p->>'price')::numeric<=0 OR (p->>'amount')::numeric>t.quantity
                OR p->>'kind'<>'OrderFilled' AND ((p->>'amount')::numeric<>t.quantity OR (p->>'price')::numeric<>t.price)
                OR p->>'kind'='BuyOrderCreated' AND t.side<>'BUY' OR p->>'kind'='SellOrderCreated' AND t.side<>'SELL'
                OR jsonb_typeof(p->'leverage') IS DISTINCT FROM 'number' OR p->>'leverage' !~ '^[1-9][0-9]*$'
                OR (p->>'leverage')::numeric IS DISTINCT FROM (CASE WHEN t.product='SPOT' THEN 1
                    ELSE (swap_permit->>'leverage')::numeric END)
                OR t.product='SWAP' AND (p->>'position' IS DISTINCT FROM swap_permit->>'position_action'
                    OR swap_permit->>'position_action' NOT IN ('OPEN','CLOSE')) THEN
                RAISE EXCEPTION 'LIFE_EVENT_ORDER_INVALID' USING ERRCODE='P0001'; END IF;
            PERFORM life_financial.recorder_scaled(p->>'amount'); PERFORM life_financial.recorder_scaled(p->>'price');
            IF p->>'kind'='OrderFilled' THEN
                IF NOT p ?& ARRAY['trade_id','fee_json','fee_quote'] OR jsonb_typeof(p->'trade_id') IS DISTINCT FROM 'string'
                    OR jsonb_typeof(p->'fee_json') IS DISTINCT FROM 'string' OR jsonb_typeof((p->>'fee_json')::jsonb) IS DISTINCT FROM 'object'
                    OR jsonb_typeof(p->'fee_quote') IS DISTINCT FROM 'string' THEN
                    RAISE EXCEPTION 'LIFE_EVENT_FILL_INVALID' USING ERRCODE='P0001'; END IF;
                PERFORM (p->>'trade_id')::life_financial.identity; PERFORM life_financial.recorder_scaled(p->>'fee_quote');
            END IF;
        ELSIF p->>'kind' IN ('OrderCancelled','OrderFailure','OrderExpired','BuyOrderCompleted','SellOrderCompleted') THEN
            IF (SELECT count(*) FROM jsonb_object_keys(p))<>4 OR NOT p ?& ARRAY['kind','timestamp_ms','wire','exchange_id']
                OR p->>'kind'='BuyOrderCompleted' AND t.side<>'BUY'
                OR p->>'kind'='SellOrderCompleted' AND t.side<>'SELL' THEN
                RAISE EXCEPTION 'LIFE_EVENT_STATUS_INVALID' USING ERRCODE='P0001'; END IF;
        ELSE RAISE EXCEPTION 'LIFE_EVENT_KIND_INVALID' USING ERRCODE='P0001'; END IF;
    END IF;
    FOR k IN SELECT jsonb_object_keys(tracking) LOOP
        PERFORM k::life_financial.identity;
        SELECT EXISTS(SELECT 1 FROM life_financial.send_attempts a WHERE a.account_id=p_account AND a.wire_id=k
            AND a.product=CASE src->>'market' WHEN 'okx' THEN 'SPOT' ELSE 'SWAP' END) INTO known;
        IF NOT known OR jsonb_typeof(tracking->k)<>'object' THEN RAISE EXCEPTION 'LIFE_EVENT_TRACKING_INVALID' USING ERRCODE='P0001'; END IF;
    END LOOP;
    RETURN p;
END; $$;
