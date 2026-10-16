-- Shared quote admission arithmetic; imported policy and original holds remain authoritative.
CREATE FUNCTION life_financial.guard_quote_admission_core(p_account uuid,p_binding jsonb,p_action jsonb,p_prepare jsonb,p_facts jsonb,p_held_subsidy boolean) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.spot_quote_admission_policies; cfg jsonb; at timestamptz; observed timestamptz; event_at timestamptz;
    quantity life_financial.positive_amount; price life_financial.positive_amount; exit_value life_financial.positive_amount;
    reference life_financial.positive_amount; bid life_financial.positive_amount; ask life_financial.positive_amount;
    maker life_financial.signed_amount; exit_fee life_financial.signed_amount; impact life_financial.amount;
    carry life_financial.amount; inventory life_financial.amount; net numeric; subsidy numeric; item text;
BEGIN
    IF p_held_subsidy IS NULL THEN RAISE EXCEPTION 'LIFE_QUOTE_ADMISSION_FACTS_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT p0.* INTO p FROM life_financial.spot_quote_admission_policies p0 JOIN life_financial.accounts a USING(account_id,policy_hash) WHERE a.account_id=p_account;
    IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_QUOTE_ADMISSION_POLICY_REQUIRED' USING ERRCODE='P0001'; END IF;
    IF jsonb_typeof(p_facts) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(p_facts))<>16
        OR NOT p_facts ?& ARRAY['binding','observed_at','reference_at','exit_value_at','fee_at','reference_price_usdt','exit_value_usdt',
            'best_bid_usdt','best_ask_usdt','maker_fee_rate','exit_fee_rate','impact_cost_usdt','carry_cost_usdt','inventory_risk_usdt',
            'exit_value_includes_impact','exit_source_kind']
        OR EXISTS(SELECT 1 FROM jsonb_each(p_facts) WHERE value='null'::jsonb)
        OR p_facts->'binding' IS DISTINCT FROM p_binding OR p_facts->>'exit_source_kind'<>'independent_market'
        OR jsonb_typeof(p_facts->'exit_value_includes_impact')<>'boolean'
        OR EXISTS(SELECT 1 FROM jsonb_each(p_facts) WHERE key NOT IN ('binding','exit_value_includes_impact') AND jsonb_typeof(value)<>'string') THEN
        RAISE EXCEPTION 'LIFE_QUOTE_ADMISSION_FACTS_INVALID' USING ERRCODE='P0001'; END IF;
    at:=clock_timestamp(); observed:=(p_facts->>'observed_at')::timestamptz;
    IF NOT isfinite(observed) OR observed>at OR extract(epoch FROM at-observed)*1000>p.max_fact_age_ms THEN
        RAISE EXCEPTION 'LIFE_QUOTE_ADMISSION_STALE' USING ERRCODE='P0001'; END IF;
    FOREACH item IN ARRAY ARRAY['reference_at','exit_value_at','fee_at'] LOOP
        event_at:=(p_facts->>item)::timestamptz;
        IF NOT isfinite(event_at) OR event_at>observed OR extract(epoch FROM at-event_at)*1000>
            (CASE WHEN item='fee_at' THEN p.max_fee_age_ms ELSE p.max_reference_age_ms END) THEN
            RAISE EXCEPTION 'LIFE_QUOTE_SOURCE_STALE' USING ERRCODE='P0001'; END IF;
    END LOOP;
    cfg:=(p_action->>'payload_json')::jsonb;
    IF jsonb_typeof(cfg) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(cfg))<>17
        OR NOT cfg ?& ARRAY['id','type','timestamp','controller_id','trading_pair','connector_name','side','amount','position_action','price',
            'chaser_config','execution_strategy','leverage','level_id','slippage_pct','slippage_multiplier','max_slippage_pct']
        OR EXISTS(SELECT 1 FROM jsonb_each(cfg) WHERE key<>'chaser_config' AND value='null'::jsonb)
        OR EXISTS(SELECT 1 FROM jsonb_each(cfg) WHERE key NOT IN ('timestamp','side','leverage','chaser_config') AND jsonb_typeof(value)<>'string')
        OR jsonb_typeof(cfg->'timestamp')<>'number'
        OR cfg->>'id' IS DISTINCT FROM p_action->>'intent_id' OR cfg->>'controller_id' IS DISTINCT FROM p_action->>'controller_id'
        OR cfg->>'type'<>'order_executor' OR cfg->>'connector_name'<>'okx' OR cfg->>'trading_pair'<>'LIFE-USDT'
        OR cfg->>'execution_strategy'<>'LIMIT_MAKER' OR cfg->>'position_action'<>'OPEN' OR cfg->'leverage'<>'1'::jsonb
        OR cfg->'chaser_config'<>'null'::jsonb OR cfg->'side'<>(CASE p_action->>'side' WHEN 'BUY' THEN '1'::jsonb ELSE '2'::jsonb END)
        OR (cfg->>'slippage_pct')::life_financial.positive_amount>(cfg->>'max_slippage_pct')::life_financial.positive_amount
        OR (cfg->>'slippage_multiplier')::life_financial.positive_amount<=1
        OR cfg->>'level_id' IS DISTINCT FROM p_action->>'level'
        OR (cfg->>'amount')::life_financial.positive_amount<>(p_prepare->>'quantity_base')::life_financial.positive_amount
        OR (cfg->>'price')::life_financial.positive_amount<>(p_prepare->>'limit_price_usdt')::life_financial.positive_amount
        OR (cfg->>'timestamp')::life_financial.positive_amount>extract(epoch FROM observed)
        OR extract(epoch FROM at)*1000-(cfg->>'timestamp')::numeric*1000>p.max_capture_age_ms THEN
        RAISE EXCEPTION 'LIFE_QUOTE_CAPTURE_MISMATCH' USING ERRCODE='P0001'; END IF;
    quantity:=(p_prepare->>'quantity_base')::life_financial.positive_amount;
    price:=(p_prepare->>'limit_price_usdt')::life_financial.positive_amount;
    reference:=(p_facts->>'reference_price_usdt')::life_financial.positive_amount;
    exit_value:=(p_facts->>'exit_value_usdt')::life_financial.positive_amount;
    bid:=(p_facts->>'best_bid_usdt')::life_financial.positive_amount; ask:=(p_facts->>'best_ask_usdt')::life_financial.positive_amount;
    maker:=(p_facts->>'maker_fee_rate')::life_financial.signed_amount; exit_fee:=(p_facts->>'exit_fee_rate')::life_financial.signed_amount;
    impact:=(p_facts->>'impact_cost_usdt')::life_financial.amount; carry:=(p_facts->>'carry_cost_usdt')::life_financial.amount;
    inventory:=(p_facts->>'inventory_risk_usdt')::life_financial.amount;
    IF (p_prepare->>'reference_price_usdt')::life_financial.positive_amount<>reference OR bid>=ask
        OR mod(price,p.tick_size)<>0 OR mod(quantity,p.lot_size)<>0 OR quantity<p.min_size
        OR p_action->>'side'='BUY' AND price>=ask OR p_action->>'side'='SELL' AND price<=bid
        OR (p_facts->>'exit_value_includes_impact')::boolean AND impact>0
        OR (p_prepare->>'fee_buffer_quote_usdt')::life_financial.amount<greatest(0,maker)*price*quantity
        OR (p_prepare->>'fee_buffer_base_life')::life_financial.amount<greatest(0,maker)*quantity THEN
        RAISE EXCEPTION 'LIFE_QUOTE_RULE_BOOK_OR_FEES_DENIED' USING ERRCODE='P0001'; END IF;
    net:=CASE p_action->>'side' WHEN 'BUY' THEN exit_value-price ELSE price-exit_value END*quantity
        -greatest(0,maker)*price*quantity-greatest(0,exit_fee)*exit_value*quantity-impact-carry-inventory-p.uncertainty_bps*0.0001*exit_value*quantity;
    subsidy:=CASE WHEN p.objective='liquidity_service' THEN greatest(0,-net) ELSE 0 END;
    IF p.objective='profit_mm' AND net<p.min_net_edge_bps*0.0001*exit_value*quantity
        OR (CASE WHEN p_held_subsidy THEN (p_prepare->>'subsidy_quote_usdt')::life_financial.amount<subsidy
            ELSE (p_prepare->>'subsidy_quote_usdt')::life_financial.amount<>subsidy END) THEN
        RAISE EXCEPTION 'LIFE_QUOTE_ECONOMICS_DENIED' USING ERRCODE='P0001'; END IF;
END; $$;

-- Preparation still reserves exactly its original economic requirement. Final
-- arming may use an already conservative hold; it can never enlarge/refund it.
CREATE OR REPLACE FUNCTION life_financial.guard_quote_admission(p_account uuid,p_binding jsonb,p_action jsonb,p_prepare jsonb,p_facts jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
    PERFORM life_financial.guard_quote_admission_core(p_account,p_binding,p_action,p_prepare,p_facts,false);
END; $$;
CREATE FUNCTION life_financial.guard_quote_send_admission(p_account uuid,p_binding jsonb,p_action jsonb,p_prepare jsonb,p_facts jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
    PERFORM life_financial.guard_quote_admission_core(p_account,p_binding,p_action,p_prepare,p_facts,true);
END; $$;

-- Exact captured-request arming. Existing immutable operations retain the
-- one-shot boundary; no table, owner, policy default or transport grant is added.
CREATE FUNCTION life_financial.guard_quote_wire(p_wire text,p_prepare jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE wire jsonb; fields integer; distinct_fields integer;
BEGIN
    IF p_wire IS NULL OR octet_length(convert_to(p_wire,'UTF8')) NOT BETWEEN 1 AND 4096 THEN
        RAISE EXCEPTION 'LIFE_QUOTE_WIRE_INVALID' USING ERRCODE='P0001'; END IF;
    wire:=p_wire::jsonb;
    IF jsonb_typeof(wire)<>'object' THEN RAISE EXCEPTION 'LIFE_QUOTE_WIRE_INVALID' USING ERRCODE='P0001'; END IF;
    -- jsonb collapses duplicates. Inspect original JSON before using its values.
    SELECT count(*),count(DISTINCT key) INTO fields,distinct_fields FROM json_each(p_wire::json);
    IF fields<>7 OR distinct_fields<>7 OR NOT wire ?& ARRAY['clOrdId','instId','side','ordType','tdMode','px','sz']
        OR EXISTS(SELECT 1 FROM jsonb_each(wire) WHERE jsonb_typeof(value)<>'string') THEN
        RAISE EXCEPTION 'LIFE_QUOTE_WIRE_INVALID' USING ERRCODE='P0001'; END IF;
    IF wire->>'clOrdId' IS DISTINCT FROM p_prepare->>'client_order_id'
        OR wire->>'instId' IS DISTINCT FROM 'LIFE-USDT'
        OR wire->>'side' IS DISTINCT FROM lower(p_prepare->>'side')
        OR wire->>'ordType' IS DISTINCT FROM 'post_only' OR wire->>'tdMode' IS DISTINCT FROM 'cash'
        OR (wire->>'px')::life_financial.positive_amount IS DISTINCT FROM (p_prepare->>'limit_price_usdt')::life_financial.positive_amount
        OR (wire->>'sz')::life_financial.positive_amount IS DISTINCT FROM (p_prepare->>'quantity_base')::life_financial.positive_amount THEN
        RAISE EXCEPTION 'LIFE_QUOTE_WIRE_MISMATCH' USING ERRCODE='P0001'; END IF;
END; $$;

CREATE INDEX spot_quote_arm_history ON life_financial.operations(account_id,operation_id)
    WHERE command->>'kind'='SPOT_QUOTE_ARM' OR receipt->>'kind'='SPOT_QUOTE_ARM';

CREATE FUNCTION life_financial.spot_quote_arms_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE o life_financial.operations; d life_financial.spot_preparations; original life_financial.operations;
    t life_financial.send_attempts; x life_financial.spot_action_descriptors; cmd jsonb;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RETURN false; END IF;
    FOR o IN SELECT * FROM life_financial.operations WHERE account_id=p_account
        AND (command->>'kind'='SPOT_QUOTE_ARM' OR receipt->>'kind'='SPOT_QUOTE_ARM') LOOP
        cmd:=o.command->'command';
        SELECT * INTO d FROM life_financial.spot_preparations WHERE account_id=p_account AND operation_id=cmd->>'prepare_operation_id';
        IF NOT FOUND THEN RETURN false; END IF;
        SELECT * INTO STRICT original FROM life_financial.operations WHERE account_id=p_account AND operation_id=d.operation_id;
        SELECT * INTO STRICT t FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=d.intent_id;
        SELECT * INTO x FROM life_financial.spot_action_descriptors WHERE account_id=p_account AND action_id=d.intent_id;
        IF NOT FOUND OR o.command->>'kind' IS DISTINCT FROM 'SPOT_QUOTE_ARM' OR jsonb_typeof(cmd) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(cmd))<>4
            OR NOT cmd ?& ARRAY['prepare_operation_id','action','admission','wire_json']
            OR jsonb_typeof(cmd->'wire_json') IS DISTINCT FROM 'string'
            OR o.operation_id<>'arm:'||d.operation_id OR cmd->'action' IS DISTINCT FROM x.descriptor
            OR cmd->'action' IS DISTINCT FROM original.command->'action'
            OR original.command->>'kind' IS DISTINCT FROM 'SPOT_PREPARE'
            OR original.command->'command' IS DISTINCT FROM d.request
            OR o.command->'binding' IS DISTINCT FROM original.command->'binding'
            OR o.command->'owner' IS DISTINCT FROM original.command->'owner'
            OR cmd#>'{admission,binding}' IS DISTINCT FROM o.command->'binding'
            OR o.committed_version<=original.committed_version OR o.recorded_at<original.recorded_at
            OR t.state NOT IN ('SEND_UNKNOWN','ACKED','TERMINAL')
            OR o.receipt->>'kind' IS DISTINCT FROM 'SPOT_QUOTE_ARM'
            OR o.receipt->>'operation_id' IS DISTINCT FROM d.operation_id
            OR o.receipt->>'intent_id' IS DISTINCT FROM d.intent_id
            OR o.receipt->>'state' IS DISTINCT FROM 'SEND_UNKNOWN'
            OR o.receipt->'replayed' IS DISTINCT FROM 'false'::jsonb
            OR o.receipt->'trading_permission' IS DISTINCT FROM 'false'::jsonb
            OR (o.receipt->>'version')::bigint IS DISTINCT FROM o.committed_version
            OR o.receipt->>'command_sha256' IS DISTINCT FROM encode(sha256(convert_to(o.command::text,'UTF8')),'hex')
            OR o.receipt->>'wire_sha256' IS DISTINCT FROM encode(sha256(convert_to(cmd->>'wire_json','UTF8')),'hex') THEN RETURN false; END IF;
        PERFORM life_financial.guard_quote_wire(cmd->>'wire_json',d.request);
    END LOOP;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

-- Preserve all existing financial/session/action barriers and bind the new
-- history proof into every current cut. Old entrypoints retain no runtime ACLs.
ALTER FUNCTION life_financial.spot_state(uuid) RENAME TO spot_state_v13;
DO $$ DECLARE r record; BEGIN
    FOR r IN SELECT p.oid::regprocedure AS signature,x.grantee FROM pg_proc p
        JOIN pg_namespace n ON n.oid=p.pronamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE n.nspname='life_financial' AND p.proname='spot_state_v13' AND x.grantee<>p.proowner LOOP
        IF r.grantee=0 THEN EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',r.signature);
        ELSE EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',r.signature,pg_get_userbyid(r.grantee)); END IF;
    END LOOP;
END; $$;
CREATE FUNCTION life_financial.spot_state(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE body jsonb; verified boolean;
BEGIN
    body:=life_financial.spot_state_v13(p_account)-'digest'-'observed_at';
    verified:=life_financial.spot_quote_arms_verified(p_account);
    body:=body||jsonb_build_object('quote_send_verified',verified,
        'quote_send_boundaries',(SELECT COALESCE(jsonb_agg(jsonb_build_object('operation_id',operation_id,
            'wire_sha256',receipt->>'wire_sha256') ORDER BY operation_id),'[]'::jsonb)
            FROM life_financial.operations WHERE account_id=p_account
                AND (command->>'kind'='SPOT_QUOTE_ARM' OR receipt->>'kind'='SPOT_QUOTE_ARM')),
        'financial_recovery_required',(body->>'financial_recovery_required')::boolean OR NOT verified,
        'recovery_required',(body->>'recovery_required')::boolean OR NOT verified,
        'unverified',(body->>'unverified')::boolean OR NOT verified);
    RETURN body||jsonb_build_object('digest',encode(sha256(convert_to(body::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
END; $$;

CREATE FUNCTION life_financial.spot_arm_quote(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,p_command jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE d life_financial.spot_preparations; t life_financial.send_attempts; original life_financial.operations;
    x life_financial.spot_action_descriptors; q life_financial.quote_actions; view jsonb; result jsonb; request jsonb;
BEGIN
    IF p_cut IS NULL OR p_fingerprint IS NULL OR jsonb_typeof(p_command) IS DISTINCT FROM 'object'
        OR octet_length(p_command::text)>131072 OR (SELECT count(*) FROM jsonb_object_keys(p_command))<>4
        OR NOT p_command ?& ARRAY['prepare_operation_id','action','admission','wire_json']
        OR jsonb_typeof(p_command->'prepare_operation_id') IS DISTINCT FROM 'string'
        OR length(p_command->>'prepare_operation_id') NOT BETWEEN 1 AND 96
        OR jsonb_typeof(p_command->'wire_json') IS DISTINCT FROM 'string' THEN
        RAISE EXCEPTION 'LIFE_QUOTE_ARM_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    -- Lock before checking the permanent boundary. A retry/readback never arms.
    PERFORM life_financial.guard_account(p_account,p_binding,p_owner,p_version);
    SELECT * INTO d FROM life_financial.spot_preparations WHERE account_id=p_account AND operation_id=p_command->>'prepare_operation_id';
    IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_QUOTE_PREPARATION_REQUIRED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT t FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=d.intent_id;
    IF t.state<>'PREPARED' OR EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=p_account AND operation_id='arm:'||d.operation_id) THEN
        RAISE EXCEPTION 'LIFE_SPOT_RECONCILE_BEFORE_RETRY' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT original FROM life_financial.operations WHERE account_id=p_account AND operation_id=d.operation_id;
    SELECT * INTO x FROM life_financial.spot_action_descriptors WHERE account_id=p_account AND action_id=d.intent_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_QUOTE_BOUND_PREPARATION_REQUIRED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT q FROM life_financial.quote_actions WHERE account_id=p_account AND action_id=d.intent_id;
    IF q.state<>'PREPARED' OR original.command->>'kind' IS DISTINCT FROM 'SPOT_PREPARE'
        OR p_command->'action' IS DISTINCT FROM x.descriptor OR x.descriptor IS DISTINCT FROM original.command->'action'
        OR original.command->'command' IS DISTINCT FROM d.request
        OR original.command->'owner' IS DISTINCT FROM p_owner OR original.command->'binding' IS DISTINCT FROM p_binding
        OR t.process_id<>(p_owner->>'process_id')::uuid OR t.generation<>(p_owner->>'generation')::uuid
        OR t.epoch<>(p_owner->>'epoch')::bigint OR t.session_id<>(p_owner->>'session_id')::uuid THEN
        RAISE EXCEPTION 'LIFE_QUOTE_ARM_ORIGIN_MISMATCH' USING ERRCODE='P0001'; END IF;
    PERFORM life_financial.guard_spot_session(p_account);
    view:=life_financial.spot_state(p_account);
    IF view->>'digest'<>p_cut THEN RAISE EXCEPTION 'LIFE_SPOT_CUT_CONFLICT' USING ERRCODE='P0001'; END IF;
    IF (view->>'recovery_required')::boolean THEN RAISE EXCEPTION 'LIFE_SPOT_RECOVERY_REQUIRED' USING ERRCODE='P0001'; END IF;
    IF (p_command#>>'{admission,observed_at}')::timestamptz<original.recorded_at THEN
        RAISE EXCEPTION 'LIFE_QUOTE_ARM_FACTS_BEFORE_PREPARE' USING ERRCODE='P0001'; END IF;
    PERFORM life_financial.guard_quote_send_admission(p_account,p_binding,p_command->'action',d.request,p_command->'admission');
    PERFORM life_financial.guard_quote_wire(p_command->>'wire_json',d.request);
    UPDATE life_financial.send_attempts SET state='ARMED',version=version+1 WHERE account_id=p_account AND intent_id=d.intent_id;
    UPDATE life_financial.send_attempts SET state='SEND_UNKNOWN',version=version+1 WHERE account_id=p_account AND intent_id=d.intent_id;
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    request:=jsonb_build_object('kind','SPOT_QUOTE_ARM','binding',p_binding,'owner',p_owner,'cut',p_cut,'command',p_command);
    result:=jsonb_build_object('kind','SPOT_QUOTE_ARM','operation_id',d.operation_id,'intent_id',d.intent_id,'state','SEND_UNKNOWN',
        'wire_sha256',encode(sha256(convert_to(p_command->>'wire_json','UTF8')),'hex'),
        'command_sha256',encode(sha256(convert_to(request::text,'UTF8')),'hex'),
        'version',p_version+1,'replayed',false,'trading_permission',false);
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version)
        VALUES(p_account,'arm:'||d.operation_id,p_fingerprint,request,result,p_version+1);
    RETURN result;
END; $$;

CREATE FUNCTION life_financial.quote_arm_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE o life_financial.operations; d life_financial.spot_preparations;
BEGIN
    SELECT * INTO o FROM life_financial.operations WHERE account_id=NEW.account_id AND committed_version=NEW.version;
    IF o.command->>'kind'='SPOT_QUOTE_ARM' THEN
        PERFORM life_financial.guard_spot_session(NEW.account_id);
        IF NOT life_financial.spot_quote_arms_verified(NEW.account_id) THEN
            RAISE EXCEPTION 'LIFE_QUOTE_SEND_UNVERIFIED' USING ERRCODE='P0001'; END IF;
        SELECT * INTO STRICT d FROM life_financial.spot_preparations WHERE account_id=NEW.account_id AND operation_id=o.receipt->>'operation_id';
        PERFORM life_financial.guard_quote_send_admission(NEW.account_id,o.command->'binding',o.command#>'{command,action}',d.request,o.command#>'{command,admission}');
        PERFORM life_financial.guard_quote_wire(o.command#>>'{command,wire_json}',d.request);
    END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER quote_arm_at_commit AFTER UPDATE ON life_financial.accounts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.quote_arm_commit_guard();
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
