-- Relational reporting effects and heads. Neither is financial authority.
CREATE TABLE life_financial.recorder_projection_effects (
    account_id uuid NOT NULL, projection_id life_financial.identity NOT NULL,
    table_kind text NOT NULL CHECK (table_kind IN ('ORDER','STATUS','TRADE','FUNDING','STATE')),
    key_hash life_financial.digest NOT NULL, target_key jsonb NOT NULL CHECK (jsonb_typeof(target_key)='object'),
    previous_projection life_financial.identity, before_row jsonb, after_row jsonb,
    source_version bigint NOT NULL CHECK (source_version>0),
    PRIMARY KEY(account_id,projection_id,table_kind,key_hash),
    FOREIGN KEY(account_id,projection_id) REFERENCES life_financial.recorder_outbox,
    FOREIGN KEY(account_id,previous_projection,table_kind,key_hash)
        REFERENCES life_financial.recorder_projection_effects(account_id,projection_id,table_kind,key_hash),
    UNIQUE NULLS NOT DISTINCT(account_id,table_kind,key_hash,previous_projection),
    CHECK (key_hash=encode(sha256(convert_to(target_key::text,'UTF8')),'hex')),
    CHECK (before_row IS NULL OR jsonb_typeof(before_row)='object'),
    CHECK (after_row IS NULL OR jsonb_typeof(after_row)='object')
);
CREATE INDEX recorder_effect_history ON life_financial.recorder_projection_effects(account_id,table_kind,key_hash,source_version DESC);
CREATE TABLE life_financial.recorder_projection_heads (
    account_id uuid NOT NULL,table_kind text NOT NULL,key_hash life_financial.digest NOT NULL,
    projection_id life_financial.identity NOT NULL,version bigint NOT NULL CHECK (version>0),
    PRIMARY KEY(account_id,table_kind,key_hash),
    FOREIGN KEY(account_id,projection_id,table_kind,key_hash) REFERENCES life_financial.recorder_projection_effects
);
ALTER TABLE life_financial.recorder_projection_effects ENABLE ROW LEVEL SECURITY;
ALTER TABLE life_financial.recorder_projection_heads ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped_read ON life_financial.recorder_projection_effects FOR SELECT USING(life_financial.can_access(account_id));
CREATE POLICY scoped_read ON life_financial.recorder_projection_heads FOR SELECT USING(life_financial.can_access(account_id));
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.recorder_projection_effects
    FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record();
CREATE TRIGGER guarded_version BEFORE UPDATE ON life_financial.recorder_projection_heads
    FOR EACH ROW EXECUTE FUNCTION life_financial.version_record();

-- Identity contains only objects, strings, null and integer timestamps.
-- Match the existing Python sorted/ASCII JSON identity, including UTF-16 escapes.
CREATE FUNCTION life_financial.recorder_identity_text(p jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE result text; value text; ch text; code integer; i integer;
BEGIN
    CASE jsonb_typeof(p)
    WHEN 'object' THEN
        SELECT '{'||COALESCE(string_agg(life_financial.recorder_identity_text(to_jsonb(fields.key))||':'||
            life_financial.recorder_identity_text(fields.value),',' ORDER BY fields.key COLLATE "C"),'')||'}'
            INTO result FROM jsonb_each(p) fields;
        RETURN result;
    WHEN 'string' THEN
        value:=to_jsonb(p#>>'{}')::text; result:='';
        FOR i IN 1..length(value) LOOP
            ch:=substr(value,i,1); code:=ascii(ch);
            IF code>65535 THEN
                code:=code-65536;
                result:=result||'\u'||lpad(to_hex(55296+(code/1024)),4,'0')||'\u'||lpad(to_hex(56320+(code%1024)),4,'0');
            ELSIF code>126 THEN result:=result||'\u'||lpad(to_hex(code),4,'0');
            ELSE result:=result||ch; END IF;
        END LOOP;
        RETURN result;
    WHEN 'number' THEN
        IF p::text !~ '^(0|[1-9][0-9]*)$' THEN RAISE EXCEPTION 'LIFE_EVENT_IDENTITY_INVALID' USING ERRCODE='P0001'; END IF;
        RETURN p::text;
    WHEN 'null' THEN RETURN 'null';
    ELSE RAISE EXCEPTION 'LIFE_EVENT_IDENTITY_INVALID' USING ERRCODE='P0001';
    END CASE;
END; $$;

CREATE FUNCTION life_financial.recorder_event_id(p_command jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE p jsonb:=(p_command->>'payload_json')::jsonb; body jsonb;
BEGIN
    body:=jsonb_build_object('source',p_command->'source','kind',p->'kind','wire',p->'wire');
    IF p->>'kind'='OrderFilled' THEN body:=body||jsonb_build_object('trade_id',p->'trade_id');
    ELSIF p->>'kind' NOT IN ('BuyOrderCreated','SellOrderCreated') THEN body:=body||jsonb_build_object('timestamp_ms',p->'timestamp_ms'); END IF;
    RETURN 'event:'||encode(sha256(convert_to(life_financial.recorder_identity_text(body),'UTF8')),'hex');
END; $$;

CREATE FUNCTION life_financial.recorder_scaled(p text) RETURNS bigint
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE n life_financial.signed_amount; result numeric;
BEGIN
    IF p IS NULL THEN RAISE EXCEPTION 'LIFE_EVENT_VALUE_INVALID' USING ERRCODE='P0001'; END IF;
    n:=p::numeric; result:=trunc(n*1000000);
    IF result NOT BETWEEN -9223372036854775808::numeric AND 9223372036854775807::numeric THEN
        RAISE EXCEPTION 'LIFE_EVENT_VALUE_INVALID' USING ERRCODE='P0001'; END IF;
    RETURN result::bigint;
END; $$;

CREATE FUNCTION life_financial.recorder_native_get(p_kind text,p_key jsonb) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb; n bigint;
BEGIN
    CASE p_kind
    WHEN 'ORDER' THEN SELECT count(*),jsonb_agg(to_jsonb(r))->0 INTO n,result FROM (SELECT * FROM life_recorder."Order" WHERE id=p_key->>'id' LIMIT 2) r;
    WHEN 'STATUS' THEN SELECT count(*),jsonb_agg(to_jsonb(r)-'id')->0 INTO n,result FROM (SELECT * FROM life_recorder."OrderStatus"
        WHERE order_id=p_key->>'order_id' AND timestamp=(p_key->>'timestamp')::bigint AND status=p_key->>'status' LIMIT 2) r;
    WHEN 'TRADE' THEN SELECT count(*),jsonb_agg(to_jsonb(r))->0 INTO n,result FROM (SELECT * FROM life_recorder."TradeFill"
        WHERE market=p_key->>'market' AND order_id=p_key->>'order_id' AND exchange_trade_id=p_key->>'exchange_trade_id' LIMIT 2) r;
    WHEN 'FUNDING' THEN SELECT count(*),jsonb_agg(to_jsonb(r))->0 INTO n,result FROM (SELECT * FROM life_recorder."FundingPayment" WHERE timestamp=(p_key->>'timestamp')::bigint LIMIT 2) r;
    WHEN 'STATE' THEN SELECT count(*),jsonb_agg(to_jsonb(r)-'id')->0 INTO n,result FROM (SELECT * FROM life_recorder."MarketState"
        WHERE config_file_path=p_key->>'config_file_path' AND market=p_key->>'market' LIMIT 2) r;
    ELSE RAISE EXCEPTION 'LIFE_EVENT_TABLE_INVALID' USING ERRCODE='P0001';
    END CASE;
    IF n>1 THEN RAISE EXCEPTION 'LIFE_EVENT_DUPLICATE_NATIVE_TARGET' USING ERRCODE='P0001'; END IF;
    RETURN result;
END; $$;

CREATE FUNCTION life_financial.recorder_native_put(p_kind text,p_key jsonb,p_before jsonb,p_after jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE o life_recorder."Order"; s life_recorder."MarketState";
BEGIN
    IF life_financial.recorder_native_get(p_kind,p_key) IS DISTINCT FROM p_before THEN
        RAISE EXCEPTION 'LIFE_EVENT_NATIVE_CONFLICT' USING ERRCODE='P0001'; END IF;
    IF p_after IS NULL THEN
        IF p_before IS NOT NULL THEN RAISE EXCEPTION 'LIFE_EVENT_DELETE_UNSUPPORTED' USING ERRCODE='P0001'; END IF;
        RETURN;
    END IF;
    IF p_before IS NOT NULL AND p_kind NOT IN ('ORDER','STATE') THEN
        IF p_before IS DISTINCT FROM p_after THEN RAISE EXCEPTION 'LIFE_EVENT_IMMUTABLE_TARGET' USING ERRCODE='P0001'; END IF;
        RETURN;
    END IF;
    CASE p_kind
    WHEN 'ORDER' THEN
        SELECT * INTO o FROM jsonb_populate_record(NULL::life_recorder."Order",p_after);
        IF p_before IS NULL THEN INSERT INTO life_recorder."Order" SELECT o.*;
        ELSE UPDATE life_recorder."Order" SET last_status=o.last_status,last_update_timestamp=o.last_update_timestamp WHERE id=o.id; END IF;
    WHEN 'STATUS' THEN INSERT INTO life_recorder."OrderStatus"(order_id,timestamp,status)
        SELECT order_id,timestamp,status FROM jsonb_populate_record(NULL::life_recorder."OrderStatus",p_after);
    WHEN 'TRADE' THEN INSERT INTO life_recorder."TradeFill" SELECT * FROM jsonb_populate_record(NULL::life_recorder."TradeFill",p_after);
    WHEN 'FUNDING' THEN INSERT INTO life_recorder."FundingPayment" SELECT * FROM jsonb_populate_record(NULL::life_recorder."FundingPayment",p_after);
    WHEN 'STATE' THEN
        SELECT * INTO s FROM jsonb_populate_record(NULL::life_recorder."MarketState",p_after);
        IF p_before IS NULL THEN INSERT INTO life_recorder."MarketState"(config_file_path,market,timestamp,saved_state)
            VALUES(s.config_file_path,s.market,s.timestamp,s.saved_state);
        ELSE UPDATE life_recorder."MarketState" SET timestamp=s.timestamp,saved_state=s.saved_state
            WHERE config_file_path=s.config_file_path AND market=s.market; END IF;
    ELSE RAISE EXCEPTION 'LIFE_EVENT_TABLE_INVALID' USING ERRCODE='P0001';
    END CASE;
END; $$;

CREATE FUNCTION life_financial.recorder_event_validate(p_account uuid,p_binding jsonb,p_command jsonb) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE src jsonb; p jsonb; tracking jsonb; t life_financial.send_attempts; k text; known boolean;
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
        IF src->>'market'<>'okx' OR jsonb_typeof(p->'wire') IS DISTINCT FROM 'string'
            OR NOT p ? 'exchange_id' OR p->'exchange_id'<>'null'::jsonb AND jsonb_typeof(p->'exchange_id')<>'string' THEN
            RAISE EXCEPTION 'LIFE_EVENT_WIRE_INVALID' USING ERRCODE='P0001'; END IF;
        PERFORM (p->>'wire')::life_financial.identity;
        IF p->'exchange_id'<>'null'::jsonb THEN PERFORM (p->>'exchange_id')::life_financial.identity; END IF;
        SELECT a.* INTO t FROM life_financial.send_attempts a JOIN life_financial.spot_action_descriptors d
            ON d.account_id=a.account_id AND d.action_id=a.intent_id
            WHERE a.account_id=p_account AND a.wire_id=p->>'wire' AND a.product='SPOT' AND d.descriptor->>'controller_id'=src->>'controller_id';
        IF NOT FOUND OR t.exchange_order_id IS NOT NULL AND p->'exchange_id'<>'null'::jsonb AND p->>'exchange_id'<>t.exchange_order_id THEN
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
                OR (p->>'leverage')::numeric<>1 THEN
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
CREATE FUNCTION life_financial.recorder_previous_effect(p_account uuid,p_kind text,p_key jsonb,p_version bigint)
RETURNS life_financial.recorder_projection_effects LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT e FROM life_financial.recorder_projection_effects e WHERE e.account_id=p_account AND e.table_kind=p_kind
        AND e.key_hash=encode(sha256(convert_to(p_key::text,'UTF8')),'hex') AND e.source_version<=p_version
        ORDER BY e.source_version DESC LIMIT 1;
$$;

CREATE FUNCTION life_financial.recorder_effect(p_account uuid,p_kind text,p_key jsonb,p_after jsonb,p_version bigint) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE previous life_financial.recorder_projection_effects;
BEGIN
    previous:=life_financial.recorder_previous_effect(p_account,p_kind,p_key,p_version);
    RETURN jsonb_build_object('table_kind',p_kind,'key_hash',encode(sha256(convert_to(p_key::text,'UTF8')),'hex'),
        'target_key',p_key,'previous_projection',previous.projection_id,'before_row',previous.after_row,'after_row',p_after);
END; $$;

CREATE FUNCTION life_financial.recorder_event_effects(p_account uuid,p_binding jsonb,p_command jsonb,p_version bigint) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p jsonb; src jsonb; key jsonb; value jsonb; effects jsonb:='[]'::jsonb;
    previous life_financial.recorder_projection_effects; latest jsonb; status text; status_at bigint;
BEGIN
    p:=life_financial.recorder_event_validate(p_account,p_binding,p_command); src:=p_command->'source';
    status:=p->>'kind'; status_at:=(p->>'timestamp_ms')::bigint;
    IF p ? 'wire' THEN
        SELECT e.after_row INTO latest FROM life_financial.recorder_projection_effects e
            WHERE e.account_id=p_account AND e.table_kind='STATUS' AND e.after_row->>'order_id'=p->>'wire'
                AND e.source_version<=p_version
            ORDER BY (e.after_row->>'timestamp')::bigint DESC,e.source_version ASC LIMIT 1;
        IF latest IS NOT NULL AND (latest->>'timestamp')::bigint>=status_at THEN
            status:=latest->>'status'; status_at:=(latest->>'timestamp')::bigint; END IF;
        key:=jsonb_build_object('id',p->>'wire');
        previous:=life_financial.recorder_previous_effect(p_account,'ORDER',key,p_version);
        IF p->>'kind' IN ('BuyOrderCreated','SellOrderCreated') THEN
            value:=jsonb_build_object('id',p->'wire','config_file_path',src->'config_file_path','strategy',src->'strategy_name',
                'market',src->'market','symbol','LIFE-USDT','base_asset','LIFE','quote_asset','USDT',
                'creation_timestamp',p->'timestamp_ms','order_type',p->'order_type','amount',life_financial.recorder_scaled(p->>'amount'),
                'leverage',p->'leverage','price',life_financial.recorder_scaled(p->>'price'),'last_status',status,
                'last_update_timestamp',status_at,'exchange_order_id',p->'exchange_id','position',p->'position');
        ELSE
            value:=previous.after_row;
            IF value IS NOT NULL THEN value:=value||jsonb_build_object('last_status',status,'last_update_timestamp',status_at); END IF;
        END IF;
        effects:=effects||jsonb_build_array(life_financial.recorder_effect(p_account,'ORDER',key,value,p_version));
        key:=jsonb_build_object('order_id',p->'wire','timestamp',p->'timestamp_ms','status',p->'kind');
        effects:=effects||jsonb_build_array(life_financial.recorder_effect(p_account,'STATUS',key,key,p_version));
    END IF;
    IF p->>'kind'='OrderFilled' THEN
        key:=jsonb_build_object('market',src->'market','order_id',p->'wire','exchange_trade_id',p->'trade_id');
        value:=key||jsonb_build_object('config_file_path',src->'config_file_path','strategy',src->'strategy_name',
            'symbol','LIFE-USDT','base_asset','LIFE','quote_asset','USDT','timestamp',p->'timestamp_ms','trade_type',p->'side',
            'order_type',p->'order_type','price',life_financial.recorder_scaled(p->>'price'),
            'amount',life_financial.recorder_scaled(p->>'amount'),'leverage',p->'leverage','position',p->'position',
            'trade_fee',(p->>'fee_json')::jsonb,'trade_fee_in_quote',life_financial.recorder_scaled(p->>'fee_quote'));
        effects:=effects||jsonb_build_array(life_financial.recorder_effect(p_account,'TRADE',key,value,p_version));
    END IF;
    IF p->>'kind'='FundingPaymentCompleted' THEN
        key:=jsonb_build_object('timestamp',p->'timestamp_ms');
        value:=key||jsonb_build_object('config_file_path',src->'config_file_path','market',src->'market','symbol','LIFE-USDT',
            'rate',(p->>'rate')::float8,'amount',(p->>'amount')::float8);
        effects:=effects||jsonb_build_array(life_financial.recorder_effect(p_account,'FUNDING',key,value,p_version));
    END IF;
    key:=jsonb_build_object('config_file_path',src->'config_file_path','market',src->'market');
    previous:=life_financial.recorder_previous_effect(p_account,'STATE',key,p_version);
    value:=key||jsonb_build_object('timestamp',greatest((p->>'timestamp_ms')::bigint,COALESCE((previous.after_row->>'timestamp')::bigint,0)),
        'saved_state',(p_command->>'tracking_json')::jsonb);
    effects:=effects||jsonb_build_array(life_financial.recorder_effect(p_account,'STATE',key,value,p_version));
    SELECT jsonb_agg(entries.entry ORDER BY entries.entry->>'table_kind',entries.entry->>'key_hash')
        INTO effects FROM jsonb_array_elements(effects) AS entries(entry);
    RETURN effects;
END; $$;

CREATE FUNCTION life_financial.recorder_event_integrity(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE item life_financial.recorder_outbox; retained life_financial.operations; expected jsonb; actual jsonb;
    e life_financial.recorder_projection_effects; parent life_financial.recorder_projection_effects; head record;
BEGIN
    FOR item IN SELECT * FROM life_financial.recorder_outbox WHERE account_id=p_account AND stream LIKE 'event:%' LOOP
        expected:=life_financial.recorder_event_effects(p_account,item.body->'binding',item.body->'command',(item.body->>'version')::bigint);
        SELECT jsonb_agg(to_jsonb(x)-ARRAY['account_id','projection_id','source_version'] ORDER BY x.table_kind,x.key_hash)
            INTO actual FROM life_financial.recorder_projection_effects x WHERE x.account_id=p_account AND x.projection_id=item.projection_id;
        SELECT * INTO retained FROM life_financial.operations WHERE account_id=p_account AND operation_id=item.projection_id;
        IF NOT FOUND OR item.body->>'subtype' IS DISTINCT FROM 'EVENT' OR item.sequence<>1
            OR item.projection_id IS DISTINCT FROM item.body->>'operation_id'
            OR item.projection_id IS DISTINCT FROM life_financial.recorder_event_id(item.body->'command')
            OR item.stream IS DISTINCT FROM 'event:'||(item.body->>'economic_id')
            OR actual IS DISTINCT FROM expected
            OR item.body->>'effects_hash' IS DISTINCT FROM encode(sha256(convert_to(expected::text,'UTF8')),'hex')
            OR item.source_hash IS DISTINCT FROM encode(sha256(convert_to(item.body::text,'UTF8')),'hex')
            OR retained.command IS DISTINCT FROM item.body||'{"kind":"RECORDER_EVENT_STAGE"}'::jsonb
            OR retained.committed_version<>(item.body->>'version')::bigint+1
            OR retained.receipt IS DISTINCT FROM jsonb_build_object('kind','RECORDER_EVENT_STAGE','projection_id',item.projection_id,
                'event_id',substr(item.projection_id,7),'state','STAGED','version',retained.committed_version,'replayed',false,'trading_permission',false) THEN RETURN false; END IF;
        IF EXISTS(SELECT 1 FROM life_financial.projection_receipts WHERE account_id=p_account AND projection_id=item.projection_id) THEN
            SELECT * INTO retained FROM life_financial.operations WHERE account_id=p_account
                AND operation_id='eventapply:'||encode(sha256(convert_to(item.projection_id,'UTF8')),'hex');
            IF NOT FOUND OR retained.command->>'kind' IS DISTINCT FROM 'RECORDER_EVENT_APPLY'
                OR retained.command->>'projection_id' IS DISTINCT FROM item.projection_id
                OR retained.command->'binding' IS DISTINCT FROM item.body->'binding' OR retained.command->'owner' IS DISTINCT FROM item.body->'owner'
                OR retained.receipt IS DISTINCT FROM jsonb_build_object('kind','RECORDER_EVENT_APPLY','projection_id',item.projection_id,
                    'event_id',substr(item.projection_id,7),'state','APPLIED','version',retained.committed_version,'replayed',false,'trading_permission',false)
                OR NOT EXISTS(SELECT 1 FROM life_financial.projection_receipts WHERE account_id=p_account AND projection_id=item.projection_id AND applied_hash=item.source_hash) THEN RETURN false; END IF;
        END IF;
    END LOOP;
    FOR e IN SELECT * FROM life_financial.recorder_projection_effects WHERE account_id=p_account LOOP
        IF NOT EXISTS(SELECT 1 FROM life_financial.recorder_outbox o WHERE o.account_id=p_account AND o.projection_id=e.projection_id
            AND o.body->>'subtype'='EVENT' AND (o.body->>'version')::bigint+1=e.source_version) THEN RETURN false; END IF;
        IF e.previous_projection IS NOT NULL THEN
            SELECT * INTO parent FROM life_financial.recorder_projection_effects WHERE account_id=p_account
                AND projection_id=e.previous_projection AND table_kind=e.table_kind AND key_hash=e.key_hash;
            IF NOT FOUND OR parent.source_version>=e.source_version OR parent.after_row IS DISTINCT FROM e.before_row
                OR NOT EXISTS(SELECT 1 FROM life_financial.operations op WHERE op.account_id=p_account
                    AND op.operation_id='eventapply:'||encode(sha256(convert_to(parent.projection_id,'UTF8')),'hex')
                    AND op.committed_version<e.source_version) THEN RETURN false; END IF;
        ELSIF e.before_row IS NOT NULL THEN RETURN false;
        END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM life_financial.operations op WHERE op.account_id=p_account AND op.command->>'kind'='RECORDER_EVENT_STAGE'
            AND NOT EXISTS(SELECT 1 FROM life_financial.recorder_outbox o WHERE o.account_id=p_account AND o.projection_id=op.operation_id AND o.stream LIKE 'event:%'))
        OR EXISTS(SELECT 1 FROM life_financial.operations op WHERE op.account_id=p_account AND op.command->>'kind'='RECORDER_EVENT_APPLY'
            AND NOT EXISTS(SELECT 1 FROM life_financial.projection_receipts r WHERE r.account_id=p_account AND r.projection_id=op.command->>'projection_id')) THEN RETURN false; END IF;
    FOR head IN SELECT DISTINCT ON(x.table_kind,x.key_hash) x.*,count(*) OVER(PARTITION BY x.table_kind,x.key_hash) AS expected_version
        FROM life_financial.recorder_projection_effects x JOIN life_financial.projection_receipts r USING(account_id,projection_id)
        WHERE x.account_id=p_account ORDER BY x.table_kind,x.key_hash,x.source_version DESC LOOP
        IF NOT EXISTS(SELECT 1 FROM life_financial.recorder_projection_heads h WHERE h.account_id=p_account
                AND h.table_kind=head.table_kind AND h.key_hash=head.key_hash AND h.projection_id=head.projection_id AND h.version=head.expected_version)
            OR life_financial.recorder_native_get(head.table_kind,head.target_key) IS DISTINCT FROM head.after_row THEN RETURN false; END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM life_financial.recorder_projection_heads h WHERE h.account_id=p_account AND NOT EXISTS(
        SELECT 1 FROM life_financial.projection_receipts r WHERE r.account_id=p_account AND r.projection_id=h.projection_id)) THEN RETURN false; END IF;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE OR REPLACE FUNCTION life_financial.recorder_executor_integrity(p_account uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT life_financial.recorder_executor_only_integrity(p_account) AND life_financial.recorder_config_integrity(p_account)
        AND life_financial.recorder_event_integrity(p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.recorder_outbox WHERE account_id=p_account
            AND stream NOT LIKE 'executor:%' AND stream NOT LIKE 'configuration:%' AND stream NOT LIKE 'event:%');
$$;

CREATE FUNCTION life_financial.recorder_event_stage(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,p_operation life_financial.identity,p_command jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE old life_financial.operations; p jsonb; effects jsonb; effect jsonb; body jsonb; result jsonb; economic jsonb; economic_id text;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR p_operation IS NULL OR p_operation !~ '^event:[0-9a-f]{64}$'
        OR p_cut IS NULL OR p_fingerprint IS NULL OR p_command IS NULL THEN
        RAISE EXCEPTION 'LIFE_EVENT_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    PERFORM 1 FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    SELECT * INTO old FROM life_financial.operations WHERE account_id=p_account AND operation_id=p_operation;
    IF FOUND THEN
        IF old.command->>'kind' IS DISTINCT FROM 'RECORDER_EVENT_STAGE' OR old.command->'command' IS DISTINCT FROM p_command
            OR old.command->'binding' IS DISTINCT FROM p_binding OR old.command->'owner' IS DISTINCT FROM p_owner
            OR NOT life_financial.recorder_executor_integrity(p_account) THEN
            RAISE EXCEPTION 'LIFE_EVENT_REPLAY_CONFLICT' USING ERRCODE='P0001'; END IF;
        RETURN old.receipt||'{"replayed":true}'::jsonb;
    END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    IF life_financial.spot_state(p_account)->>'digest' IS DISTINCT FROM p_cut OR NOT life_financial.recorder_executor_integrity(p_account)
        OR EXISTS(SELECT 1 FROM life_financial.recorder_outbox o WHERE o.account_id=p_account AND NOT EXISTS(
            SELECT 1 FROM life_financial.projection_receipts r WHERE r.account_id=p_account AND r.projection_id=o.projection_id)) THEN
        RAISE EXCEPTION 'LIFE_EVENT_CUT_OR_DELIVERY_INVALID' USING ERRCODE='P0001'; END IF;
    p:=life_financial.recorder_event_validate(p_account,p_binding,p_command);
    IF p_operation IS DISTINCT FROM life_financial.recorder_event_id(p_command) THEN
        RAISE EXCEPTION 'LIFE_EVENT_IDENTITY_INVALID' USING ERRCODE='P0001'; END IF;
    economic:=jsonb_build_object('market',p_command#>>'{source,market}','kind',p->'kind','wire',p->'wire');
    IF p->>'kind'='OrderFilled' THEN economic:=economic||jsonb_build_object('trade_id',p->'trade_id');
    ELSIF p->>'kind' NOT IN ('BuyOrderCreated','SellOrderCreated') THEN economic:=economic||jsonb_build_object('timestamp_ms',p->'timestamp_ms'); END IF;
    economic_id:=encode(sha256(convert_to(economic::text,'UTF8')),'hex');
    IF EXISTS(SELECT 1 FROM life_financial.recorder_outbox WHERE account_id=p_account AND stream='event:'||economic_id) THEN
        RAISE EXCEPTION 'LIFE_EVENT_ECONOMIC_IDENTITY_CONFLICT' USING ERRCODE='P0001'; END IF;
    effects:=life_financial.recorder_event_effects(p_account,p_binding,p_command,p_version);
    FOR effect IN SELECT jsonb_array_elements(effects) LOOP
        -- Stable global target lock order also covers legacy tables without a
        -- natural unique index. Account CAS alone cannot serialize two accounts.
        PERFORM pg_advisory_xact_lock(hashtextextended((effect->>'table_kind')||':'||(effect->'target_key')::text,0));
        IF life_financial.recorder_native_get(effect->>'table_kind',effect->'target_key') IS DISTINCT FROM NULLIF(effect->'before_row','null'::jsonb) THEN
            RAISE EXCEPTION 'LIFE_EVENT_NATIVE_CONFLICT' USING ERRCODE='P0001'; END IF;
    END LOOP;
    body:=jsonb_build_object('subtype','EVENT','operation_id',p_operation,'economic_id',economic_id,'command',p_command,
        'binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,
        'effects_hash',encode(sha256(convert_to(effects::text,'UTF8')),'hex'));
    INSERT INTO life_financial.recorder_outbox VALUES(p_account,p_operation,'event:'||economic_id,1,body,encode(sha256(convert_to(body::text,'UTF8')),'hex'));
    FOR effect IN SELECT jsonb_array_elements(effects) LOOP
        INSERT INTO life_financial.recorder_projection_effects VALUES(p_account,p_operation,effect->>'table_kind',effect->>'key_hash',
            effect->'target_key',effect->>'previous_projection',NULLIF(effect->'before_row','null'::jsonb),NULLIF(effect->'after_row','null'::jsonb),p_version+1);
    END LOOP;
    result:=jsonb_build_object('kind','RECORDER_EVENT_STAGE','projection_id',p_operation,'event_id',substr(p_operation,7),
        'state','STAGED','version',p_version+1,'replayed',false,'trading_permission',false);
    INSERT INTO life_financial.operations VALUES(p_account,p_operation,p_fingerprint,body||'{"kind":"RECORDER_EVENT_STAGE"}'::jsonb,result,p_version+1,clock_timestamp());
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    RETURN result;
END; $$;

CREATE FUNCTION life_financial.recorder_event_apply(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_projection life_financial.identity) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE op text:='eventapply:'||encode(sha256(convert_to(p_projection,'UTF8')),'hex'); old life_financial.operations;
    item life_financial.recorder_outbox; effect life_financial.recorder_projection_effects; request jsonb; result jsonb;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR p_projection IS NULL OR p_cut IS NULL THEN
        RAISE EXCEPTION 'LIFE_EVENT_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    PERFORM 1 FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    SELECT * INTO STRICT item FROM life_financial.recorder_outbox WHERE account_id=p_account AND projection_id=p_projection AND stream LIKE 'event:%';
    IF item.body->'binding' IS DISTINCT FROM p_binding OR item.body->'owner' IS DISTINCT FROM p_owner THEN
        RAISE EXCEPTION 'LIFE_EVENT_ORIGIN_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT * INTO old FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF old.command->>'kind' IS DISTINCT FROM 'RECORDER_EVENT_APPLY' OR old.command->>'projection_id' IS DISTINCT FROM p_projection
            OR old.command->'binding' IS DISTINCT FROM p_binding OR old.command->'owner' IS DISTINCT FROM p_owner
            OR NOT life_financial.recorder_executor_integrity(p_account) THEN
            RAISE EXCEPTION 'LIFE_EVENT_REPLAY_CONFLICT' USING ERRCODE='P0001'; END IF;
        RETURN old.receipt||'{"replayed":true}'::jsonb;
    END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    IF life_financial.spot_state(p_account)->>'digest' IS DISTINCT FROM p_cut OR NOT life_financial.recorder_executor_integrity(p_account) THEN
        RAISE EXCEPTION 'LIFE_EVENT_CUT_OR_DELIVERY_INVALID' USING ERRCODE='P0001'; END IF;
    FOR effect IN SELECT * FROM life_financial.recorder_projection_effects WHERE account_id=p_account AND projection_id=p_projection
        ORDER BY table_kind,key_hash LOOP
        PERFORM pg_advisory_xact_lock(hashtextextended(effect.table_kind||':'||effect.target_key::text,0));
        IF (SELECT h.projection_id FROM life_financial.recorder_projection_heads h WHERE h.account_id=p_account
            AND h.table_kind=effect.table_kind AND h.key_hash=effect.key_hash) IS DISTINCT FROM effect.previous_projection THEN
            RAISE EXCEPTION 'LIFE_EVENT_PREDECESSOR_INVALID' USING ERRCODE='P0001'; END IF;
        PERFORM life_financial.recorder_native_put(effect.table_kind,effect.target_key,effect.before_row,effect.after_row);
        INSERT INTO life_financial.recorder_projection_heads VALUES(p_account,effect.table_kind,effect.key_hash,p_projection,1)
        ON CONFLICT(account_id,table_kind,key_hash) DO UPDATE SET projection_id=EXCLUDED.projection_id,version=recorder_projection_heads.version+1;
    END LOOP;
    INSERT INTO life_financial.projection_receipts VALUES(p_account,p_projection,item.source_hash);
    request:=jsonb_build_object('kind','RECORDER_EVENT_APPLY','projection_id',p_projection,'binding',p_binding,'owner',p_owner,'cut',p_cut,'version',p_version);
    result:=jsonb_build_object('kind','RECORDER_EVENT_APPLY','projection_id',p_projection,'event_id',substr(p_projection,7),
        'state','APPLIED','version',p_version+1,'replayed',false,'trading_permission',false);
    INSERT INTO life_financial.operations VALUES(p_account,op,encode(sha256(convert_to(request::text,'UTF8')),'hex'),request,result,p_version+1,clock_timestamp());
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    RETURN result;
END; $$;

CREATE OR REPLACE FUNCTION life_financial.commit_owner_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts; expiry timestamptz; at timestamptz; op life_financial.operations;
BEGIN
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=NEW.account_id;
    SELECT * INTO op FROM life_financial.operations WHERE account_id=NEW.account_id AND committed_version=NEW.version;
    IF a.owner_generation IS DISTINCT FROM NEW.owner_generation OR a.owner_process IS DISTINCT FROM NEW.owner_process
        OR a.owner_epoch<>NEW.owner_epoch OR a.current_session IS DISTINCT FROM NEW.current_session
        OR a.lineage<>NEW.lineage OR a.policy_hash<>NEW.policy_hash OR a.config_hash<>NEW.config_hash THEN
        RAISE EXCEPTION 'LIFE_OWNER_EXPIRED_OR_CHANGED_AT_COMMIT' USING ERRCODE='P0001'; END IF;
    IF op.command->>'kind' IN ('RECORDER_EVENT_STAGE','RECORDER_EVENT_APPLY','RECORDER_CONFIG_STAGE','RECORDER_CONFIG_APPLY','RECORDER_STAGE','RECORDER_APPLY','SPOT_ACK','SPOT_FILL','SPOT_CASHFLOW','SPOT_NAV','SPOT_SESSION_TICK','SPOT_LIFECYCLE','SPOT_TERMINAL') OR op.command->>'kind' IN ('SPOT_ACTION_TRANSITION','SPOT_ACTION_ABANDON')
        AND op.command#>>'{command,state}'<>'DISPATCHED' THEN
        PERFORM life_financial.guard_spot_observation(NEW.account_id,op.command->'binding',op.command->'owner',a.version);
        IF op.command->>'kind' IN ('SPOT_LIFECYCLE','SPOT_TERMINAL') THEN
            PERFORM life_financial.spot_order_commit_guard(NEW.account_id,op.command); END IF;
        IF op.command->>'kind'='SPOT_SESSION_TICK' AND ((op.command#>>'{command,reference_ready}')::boolean
            OR (op.command#>>'{command,all_gates_ready}')::boolean
            OR (op.command#>>'{command,market_reference_ready}')::boolean) THEN
            PERFORM life_financial.guard_account(NEW.account_id,op.command->'binding',op.command->'owner',a.version);
        END IF;
        IF op.command->>'kind'='SPOT_SESSION_TICK' AND op.receipt->>'state'='ACTIVE' THEN
            PERFORM life_financial.guard_spot_session(NEW.account_id); END IF;
    ELSE
        SELECT expires_at INTO STRICT expiry FROM life_financial.sessions WHERE account_id=a.account_id AND session_id=a.current_session;
        at:=clock_timestamp();
        IF a.mode<>'ACTIVE' OR a.halted OR at<a.owner_since OR at>=a.owner_until OR at>=expiry
            OR current_setting('synchronous_commit')<>'on' OR current_setting('fsync')<>'on'
            OR current_setting('full_page_writes')<>'on' OR pg_is_in_recovery() THEN
            RAISE EXCEPTION 'LIFE_OWNER_EXPIRED_OR_CHANGED_AT_COMMIT' USING ERRCODE='P0001'; END IF;
        IF op.receipt->>'kind' IN ('SPOT_PREPARE','SPOT_ARM') THEN PERFORM life_financial.guard_spot_session(NEW.account_id); END IF;
    END IF;
    IF op.command->>'kind' IN ('SPOT_ACTION_CLAIM','SPOT_ACTION_TRANSITION','SPOT_ACTION_ABANDON') THEN
        PERFORM life_financial.spot_action_commit_guard(NEW.account_id,op.command); END IF;
    IF op.command->>'kind'='SPOT_PREPARE' AND op.command ? 'action' THEN
        PERFORM life_financial.spot_action_prepare_commit_guard(NEW.account_id,op.command); END IF;
    RETURN NULL;
END; $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
