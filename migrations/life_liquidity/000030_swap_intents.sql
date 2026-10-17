-- Native SWAP intent lifecycle. Receipts never authorize an external request.
CREATE FUNCTION life_financial.swap_policy_valid(p jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE n numeric;
BEGIN
    IF jsonb_typeof(p) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(p))<>5
        OR NOT p ?& ARRAY['controller_id','tick_size_usdt','min_size_contracts','max_quote_levels','max_capture_age_ms']
        OR jsonb_typeof(p->'controller_id') IS DISTINCT FROM 'string' THEN RETURN false; END IF;
    PERFORM (p->>'controller_id')::life_financial.identity;
    IF jsonb_typeof(p->'tick_size_usdt') IS DISTINCT FROM 'string' OR jsonb_typeof(p->'min_size_contracts') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p->'max_quote_levels') IS DISTINCT FROM 'number' OR p->>'max_quote_levels' !~ '^[1-9][0-9]*$'
        OR (p->>'max_quote_levels')::numeric>4096 OR jsonb_typeof(p->'max_capture_age_ms') IS DISTINCT FROM 'number'
        OR p->>'max_capture_age_ms' !~ '^[1-9][0-9]*$' OR (p->>'max_capture_age_ms')::numeric>2147483647 THEN RETURN false; END IF;
    n:=(p->>'tick_size_usdt')::life_financial.positive_amount;n:=(p->>'min_size_contracts')::life_financial.positive_amount;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
CREATE TABLE life_financial.swap_policies (
    account_id uuid PRIMARY KEY REFERENCES life_financial.shared_capital_policies,
    policy_hash life_financial.digest NOT NULL,body jsonb NOT NULL,model_hash life_financial.digest NOT NULL,
    FOREIGN KEY(account_id,policy_hash) REFERENCES life_financial.policies,
    CHECK(life_financial.swap_policy_valid(body)),
    CHECK(model_hash=encode(sha256(convert_to(life_financial.canonical_json_v1(body),'UTF8')),'hex'))
);
CREATE TABLE life_financial.swap_preparations (
    account_id uuid NOT NULL REFERENCES life_financial.accounts,operation_id life_financial.identity NOT NULL,
    intent_id life_financial.identity NOT NULL,request jsonb NOT NULL,permit jsonb NOT NULL,
    quote_level integer NOT NULL CHECK(quote_level>=0),prepared_at_ms bigint NOT NULL CHECK(prepared_at_ms>0),
    PRIMARY KEY(account_id,operation_id),UNIQUE(account_id,intent_id),
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(account_id,intent_id) REFERENCES life_financial.send_attempts
);
CREATE TABLE life_financial.swap_intent_events (
    account_id uuid NOT NULL REFERENCES life_financial.accounts,operation_id life_financial.identity NOT NULL,
    prepare_operation_id life_financial.identity NOT NULL,kind text NOT NULL CHECK(kind IN ('SWAP_ARM','SWAP_ACK')),
    exchange_order_id text,PRIMARY KEY(account_id,operation_id),UNIQUE(account_id,prepare_operation_id,kind),
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(account_id,prepare_operation_id) REFERENCES life_financial.swap_preparations,
    CHECK(kind='SWAP_ARM' AND exchange_order_id IS NULL OR kind='SWAP_ACK' AND exchange_order_id IS NOT NULL AND exchange_order_id ~ '^[1-9][0-9]{0,63}$')
);
CREATE INDEX swap_operation_history ON life_financial.operations(account_id,committed_version)
    WHERE command->>'kind' IN ('SWAP_PREPARE','SWAP_ARM','SWAP_ACK');

CREATE FUNCTION life_financial.swap_budget_ready(p_account uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT NOT EXISTS(SELECT 1 FROM life_financial.budget_buckets b
        JOIN life_financial.accounts a USING(account_id) JOIN life_financial.spot_policies p USING(account_id,policy_hash)
        WHERE b.account_id=p_account AND b.committed+b.incurred>=b.budget_limit
        AND (b.scope='SESSION' AND b.window_key=a.current_session::text
            OR b.scope='DAY' AND b.window_key=to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD')
            OR b.scope='CAMPAIGN' AND b.window_key=p.campaign_id))
$$;

CREATE FUNCTION life_financial.swap_config_valid(c jsonb,p jsonb,cp jsonb,at_ms bigint) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE k text;q life_financial.positive_amount;price life_financial.positive_amount;stamp numeric;contracts numeric;
BEGIN
    IF jsonb_typeof(c) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(c))<>17
        OR NOT c ?& ARRAY['id','type','timestamp','controller_id','trading_pair','connector_name','side','amount','position_action','price',
            'chaser_config','execution_strategy','leverage','level_id','slippage_pct','slippage_multiplier','max_slippage_pct']
        OR c->'type' IS DISTINCT FROM '"order_executor"'::jsonb OR c->'controller_id' IS DISTINCT FROM p->'controller_id'
        OR c->'trading_pair' IS DISTINCT FROM '"LIFE-USDT"'::jsonb OR c->'connector_name' IS DISTINCT FROM '"okx_perpetual"'::jsonb
        OR c->'execution_strategy' IS DISTINCT FROM '"LIMIT_MAKER"'::jsonb OR c->'chaser_config' IS DISTINCT FROM 'null'::jsonb
        OR jsonb_typeof(c->'side') IS DISTINCT FROM 'number' OR c->>'side' !~ '^[12]$'
        OR c->'position_action' NOT IN ('"OPEN"'::jsonb,'"CLOSE"'::jsonb)
        OR jsonb_typeof(c->'leverage') IS DISTINCT FROM 'number' OR c->>'leverage' !~ '^[1-9][0-9]*$'
        OR c->'leverage' IS DISTINCT FROM cp->'leverage' OR jsonb_typeof(c->'id') IS DISTINCT FROM 'string'
        OR jsonb_typeof(c->'level_id') IS DISTINCT FROM 'string' OR c->>'level_id' !~ '^(0|[1-9][0-9]*)$'
        OR (c->>'level_id')::numeric>=(p->>'max_quote_levels')::integer THEN RETURN false; END IF;
    PERFORM (c->>'id')::life_financial.identity;
    FOREACH k IN ARRAY ARRAY['timestamp','amount','price','slippage_pct','slippage_multiplier','max_slippage_pct'] LOOP
        IF jsonb_typeof(c->k) IS DISTINCT FROM 'string' THEN RETURN false; END IF;
        PERFORM (c->>k)::life_financial.positive_amount;
    END LOOP;
    IF (c->>'slippage_multiplier')::numeric<=1 OR (c->>'slippage_pct')::numeric>(c->>'max_slippage_pct')::numeric THEN RETURN false; END IF;
    stamp:=(c->>'timestamp')::numeric*1000;
    IF stamp>at_ms OR at_ms-stamp>(p->>'max_capture_age_ms')::integer THEN RETURN false; END IF;
    q:=(c->>'amount')::numeric;price:=(c->>'price')::numeric;
    IF mod(price,(p->>'tick_size_usdt')::numeric)<>0
        OR mod(q,(cp->>'contract_value_life')::numeric*(cp->>'lot_contracts')::numeric)<>0
        OR mod(q*1000000000000000000,(cp->>'contract_value_life')::numeric)<>0 THEN RETURN false; END IF;
    contracts:=div(q*1000000000000000000,(cp->>'contract_value_life')::numeric)*0.000000000000000001;
    PERFORM contracts::life_financial.positive_amount;
    RETURN contracts>=(p->>'min_size_contracts')::numeric;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.swap_permit(c jsonb,claim life_financial.shared_capital_claims,cp jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
    SELECT jsonb_build_object('intent_id',claim.intent_id,'client_order_id',claim.wire_id,'reservation_id',claim.intent_id,
        'session_id',claim.session_id,'epoch',claim.epoch,'config_version',claim.config_version,'risk_epoch',claim.risk_epoch,
        'price_usdt',claim.price_usdt::text,'quantity_base',claim.quantity_base::text,'side',claim.side,'position_action',claim.position_action,
        'contracts',(div(claim.quantity_base*1000000000000000000,(cp->>'contract_value_life')::numeric)*0.000000000000000001)::text,
        'contract_value_life',cp->>'contract_value_life','instrument','LIFE-USDT-SWAP','position_mode',cp->>'position_mode',
        'leverage',claim.leverage,'account_uid',claim.account_uid)
$$;

CREATE FUNCTION life_financial.swap_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.swap_policies;cp life_financial.shared_capital_policies;d life_financial.swap_preparations;
    claim life_financial.shared_capital_claims;t life_financial.send_attempts;o life_financial.operations;e life_financial.swap_intent_events;
    c jsonb;payload jsonb;permit jsonb;expected text;last_state text;acks integer;arms integer;
BEGIN
    SELECT * INTO p FROM life_financial.swap_policies WHERE account_id=p_account;
    IF p.account_id IS NULL THEN RETURN NOT EXISTS(SELECT 1 FROM life_financial.swap_preparations WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.swap_intent_events WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.send_attempts WHERE account_id=p_account AND product='SWAP')
        AND NOT EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=p_account AND command->>'kind' IN ('SWAP_PREPARE','SWAP_ARM','SWAP_ACK')); END IF;
    SELECT * INTO STRICT cp FROM life_financial.shared_capital_policies WHERE account_id=p_account;
    IF NOT life_financial.swap_policy_valid(p.body) OR p.model_hash<>encode(sha256(convert_to(life_financial.canonical_json_v1(p.body),'UTF8')),'hex')
        OR p.policy_hash<>cp.policy_hash OR NOT EXISTS(SELECT 1 FROM life_financial.accounts a WHERE a.account_id=p_account AND a.policy_hash=p.policy_hash) THEN RETURN false; END IF;
    FOR d IN SELECT * FROM life_financial.swap_preparations WHERE account_id=p_account LOOP
        SELECT * INTO STRICT t FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=d.intent_id;
        SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=d.operation_id;
        SELECT * INTO STRICT claim FROM life_financial.shared_capital_claims WHERE account_id=p_account AND intent_id=d.intent_id;
        c:=d.request->'config';permit:=life_financial.swap_permit(c,claim,cp.body);
        IF NOT life_financial.swap_config_valid(c,p.body,cp.body,d.prepared_at_ms)
            OR d.permit IS DISTINCT FROM permit OR d.quote_level<>(c->>'level_id')::integer
            OR (t.product,t.intent_id,t.wire_id,t.side,t.quantity,t.price,t.session_id,t.epoch)
                IS DISTINCT FROM ('SWAP'::text,claim.intent_id,claim.wire_id,claim.side,claim.quantity_base,claim.price_usdt,claim.session_id,claim.epoch)
            OR claim.product<>'SWAP' OR claim.side<>(CASE c->>'side' WHEN '1' THEN 'BUY' ELSE 'SELL' END)
            OR claim.position_action<>c->>'position_action' OR claim.quantity_base<>(c->>'amount')::numeric OR claim.price_usdt<>(c->>'price')::numeric
            OR claim.leverage<>(c->>'leverage')::integer OR d.request->>'operation_id'<>d.operation_id OR d.request->>'client_order_id'<>t.wire_id
            OR t.operation_id<>d.operation_id OR o.command->>'kind' IS DISTINCT FROM 'SWAP_PREPARE' OR o.command->'command' IS DISTINCT FROM d.request
            OR o.command#>>'{owner,process_id}' IS DISTINCT FROM t.process_id::text OR o.command#>>'{owner,generation}' IS DISTINCT FROM t.generation::text
            OR o.fingerprint<>encode(sha256(convert_to(life_financial.canonical_json_v1(o.command),'UTF8')),'hex')
            OR o.receipt IS DISTINCT FROM jsonb_build_object('kind','SWAP_PREPARE','operation_id',d.operation_id,'intent_id',t.intent_id,
                'state','PREPARED','permit',permit,'version',o.committed_version,'prepared_at_ms',d.prepared_at_ms,'replayed',false,'trading_permission',false) THEN RETURN false; END IF;
        payload:=jsonb_build_object('command',d.request,'config_version',claim.config_version,'risk_epoch',claim.risk_epoch);
        IF t.payload<>convert_to(life_financial.canonical_json_v1(payload),'UTF8') OR t.payload_hash<>encode(sha256(t.payload),'hex') THEN RETURN false; END IF;
        SELECT count(*) FILTER(WHERE kind='SWAP_ACK'),count(*) FILTER(WHERE kind='SWAP_ARM') INTO acks,arms
            FROM life_financial.swap_intent_events WHERE account_id=p_account AND prepare_operation_id=d.operation_id;
        last_state:=CASE WHEN acks=1 THEN 'ACKED' WHEN arms=1 THEN 'SEND_UNKNOWN' ELSE 'PREPARED' END;
        IF t.state<>last_state OR t.version<>acks+2*arms OR acks>arms OR t.exchange_order_id IS DISTINCT FROM (
            SELECT exchange_order_id FROM life_financial.swap_intent_events WHERE account_id=p_account AND prepare_operation_id=d.operation_id AND kind='SWAP_ACK') THEN RETURN false; END IF;
    END LOOP;
    FOR e IN SELECT * FROM life_financial.swap_intent_events WHERE account_id=p_account LOOP
        SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=e.operation_id;
        SELECT * INTO STRICT d FROM life_financial.swap_preparations WHERE account_id=p_account AND operation_id=e.prepare_operation_id;
        expected:=CASE WHEN e.kind='SWAP_ARM' THEN 'SEND_UNKNOWN' ELSE 'ACKED' END;
        IF o.command->>'kind' IS DISTINCT FROM e.kind OR o.command#>>'{command,prepare_operation_id}' IS DISTINCT FROM e.prepare_operation_id
            OR o.command#>>'{command,operation_id}' IS DISTINCT FROM e.operation_id
            OR o.command#>>'{command,exchange_order_id}' IS DISTINCT FROM e.exchange_order_id
            OR o.fingerprint<>encode(sha256(convert_to(life_financial.canonical_json_v1(o.command),'UTF8')),'hex')
            OR o.receipt IS DISTINCT FROM jsonb_build_object('kind',e.kind,'operation_id',d.operation_id,'intent_id',d.intent_id,
                'state',expected,'permit',d.permit,'version',o.committed_version,'replayed',false,'trading_permission',false)
            OR o.committed_version<=(SELECT committed_version FROM life_financial.operations WHERE account_id=p_account AND operation_id=d.operation_id)
            OR e.kind='SWAP_ACK' AND o.committed_version<=(SELECT ar.committed_version FROM life_financial.operations ar JOIN life_financial.swap_intent_events ae USING(account_id,operation_id)
                WHERE ae.account_id=p_account AND ae.prepare_operation_id=d.operation_id AND ae.kind='SWAP_ARM') THEN RETURN false; END IF;
    END LOOP;
    RETURN NOT EXISTS(SELECT 1 FROM life_financial.send_attempts native WHERE native.account_id=p_account AND native.product='SWAP' AND NOT EXISTS(
        SELECT 1 FROM life_financial.swap_preparations prep WHERE prep.account_id=p_account AND prep.intent_id=native.intent_id))
        AND NOT EXISTS(SELECT 1 FROM life_financial.operations op WHERE op.account_id=p_account AND (
            op.command->>'kind'='SWAP_PREPARE' AND NOT EXISTS(SELECT 1 FROM life_financial.swap_preparations prep WHERE prep.account_id=p_account AND prep.operation_id=op.operation_id)
            OR op.command->>'kind' IN ('SWAP_ARM','SWAP_ACK') AND NOT EXISTS(SELECT 1 FROM life_financial.swap_intent_events event WHERE event.account_id=p_account AND event.operation_id=op.operation_id)));
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.swap_read(p_account uuid,p_rows integer,p_bytes integer,p_operation text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
    IF p_rows IS NULL OR p_rows NOT BETWEEN 1 AND 4096 OR p_bytes IS NULL OR p_bytes NOT BETWEEN 1 AND 16777216 THEN RAISE EXCEPTION 'LIFE_SWAP_READ_BOUND_INVALID' USING ERRCODE='P0001'; END IF;
    IF (SELECT count(*) FROM (SELECT 1 FROM life_financial.swap_preparations WHERE account_id=p_account LIMIT p_rows+1) x)>p_rows
        OR (SELECT count(*) FROM (SELECT 1 FROM life_financial.swap_intent_events WHERE account_id=p_account LIMIT p_rows+1) x)>p_rows THEN RAISE EXCEPTION 'LIFE_SWAP_READ_BOUND' USING ERRCODE='P0001'; END IF;
    IF NOT life_financial.swap_verified(p_account) OR NOT life_financial.shared_capital_verified(p_account) THEN RAISE EXCEPTION 'LIFE_SWAP_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    IF p_operation IS NULL THEN
        result:=jsonb_build_object('policy',(SELECT body FROM life_financial.swap_policies WHERE account_id=p_account),
            'preparations',COALESCE((SELECT jsonb_agg(jsonb_build_object('operation_id',operation_id,'request',request,'permit',permit,'prepared_at_ms',prepared_at_ms) ORDER BY operation_id) FROM life_financial.swap_preparations WHERE account_id=p_account),'[]'::jsonb),'trading_permission',false);
    ELSE
        SELECT jsonb_build_object('command',command,'receipt',receipt) INTO result FROM life_financial.operations
            WHERE account_id=p_account AND operation_id=p_operation AND command->>'kind' IN ('SWAP_PREPARE','SWAP_ARM','SWAP_ACK');
    END IF;
    IF octet_length(result::text)>p_bytes THEN RAISE EXCEPTION 'LIFE_SWAP_READ_BOUND' USING ERRCODE='P0001'; END IF;
    RETURN result;
END; $$;

CREATE FUNCTION life_financial.swap_command(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,p_kind text,cmd jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts;p life_financial.swap_policies;cp life_financial.shared_capital_policies;s life_financial.sessions;
    claim life_financial.shared_capital_claims;d life_financial.swap_preparations;t life_financial.send_attempts;old life_financial.operations;
    view jsonb;request jsonb;result jsonb;c jsonb;permit jsonb;payload bytea;op text;prepare_op text;at_ms bigint;exchange_id text;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR p_kind IS NULL OR p_kind NOT IN ('SWAP_PREPARE','SWAP_ARM','SWAP_ACK')
        OR jsonb_typeof(cmd) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(cmd))<>3
        OR jsonb_typeof(cmd->'operation_id') IS DISTINCT FROM 'string' OR cmd->>'operation_id' !~ '^[A-Za-z0-9_.:-]{1,96}$'
        OR octet_length(cmd::text)>16384 THEN RAISE EXCEPTION 'LIFE_SWAP_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    op:=cmd->>'operation_id';request:=jsonb_build_object('kind',p_kind,'binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',cmd);
    IF p_fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(request),'UTF8')),'hex') THEN RAISE EXCEPTION 'LIFE_SWAP_FINGERPRINT_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    IF NOT life_financial.swap_verified(p_account) THEN RAISE EXCEPTION 'LIFE_SWAP_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO old FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF old.command<>request OR old.fingerprint<>p_fingerprint THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001'; END IF;
        IF p_kind='SWAP_ARM' OR p_kind='SWAP_PREPARE' AND NOT EXISTS(SELECT 1 FROM life_financial.send_attempts WHERE account_id=p_account AND operation_id=op AND state='PREPARED') THEN
            RAISE EXCEPTION 'LIFE_SWAP_RECONCILE_BEFORE_RETRY' USING ERRCODE='P0001'; END IF;
        RETURN old.receipt||jsonb_build_object('replayed',true);
    END IF;
    IF p_kind='SWAP_ACK' THEN PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    ELSE PERFORM life_financial.guard_account(p_account,p_binding,p_owner,p_version);PERFORM life_financial.guard_spot_session(p_account); END IF;
    view:=life_financial.spot_state(p_account);
    IF view->>'digest' IS DISTINCT FROM p_cut THEN RAISE EXCEPTION 'LIFE_SWAP_CUT_STALE' USING ERRCODE='P0001'; END IF;
    IF (view->>'unverified')::boolean OR p_kind<>'SWAP_ACK' AND (view->>'recovery_required')::boolean THEN RAISE EXCEPTION 'LIFE_SWAP_RECOVERY_REQUIRED' USING ERRCODE='P0001'; END IF;
    IF p_kind<>'SWAP_ACK' AND NOT life_financial.swap_budget_ready(p_account) THEN RAISE EXCEPTION 'LIFE_SWAP_BUDGET_EXHAUSTED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT p FROM life_financial.swap_policies WHERE account_id=p_account;
    SELECT * INTO STRICT cp FROM life_financial.shared_capital_policies WHERE account_id=p_account;
    SELECT * INTO STRICT s FROM life_financial.sessions WHERE account_id=p_account AND session_id=a.current_session;
    at_ms:=floor(extract(epoch FROM clock_timestamp())*1000)::bigint;
    IF p_kind='SWAP_PREPARE' THEN
        IF NOT cmd ?& ARRAY['operation_id','client_order_id','config'] OR jsonb_typeof(cmd->'client_order_id') IS DISTINCT FROM 'string'
            OR cmd->>'client_order_id' !~ '^[A-Za-z0-9]{1,32}$' OR NOT life_financial.swap_config_valid(cmd->'config',p.body,cp.body,at_ms) THEN RAISE EXCEPTION 'LIFE_SWAP_CONFIG_INVALID' USING ERRCODE='P0001'; END IF;
        c:=cmd->'config';
        IF (c->>'timestamp')::numeric*1000<floor(extract(epoch FROM s.started_at)*1000) THEN
            RAISE EXCEPTION 'LIFE_SWAP_CAPTURE_SESSION_MISMATCH' USING ERRCODE='P0001'; END IF;
        SELECT * INTO claim FROM life_financial.shared_capital_claims WHERE account_id=p_account AND intent_id=c->>'id';
        IF NOT FOUND OR claim.product<>'SWAP' OR claim.wire_id<>cmd->>'client_order_id' OR claim.side<>(CASE c->>'side' WHEN '1' THEN 'BUY' ELSE 'SELL' END)
            OR claim.position_action<>c->>'position_action' OR claim.quantity_base<>(c->>'amount')::numeric OR claim.price_usdt<>(c->>'price')::numeric
            OR claim.leverage<>(c->>'leverage')::integer OR claim.session_id<>s.session_id OR claim.epoch<>a.owner_epoch
            OR claim.config_version<>s.config_version OR claim.risk_epoch<>a.risk_epoch OR NOT EXISTS(SELECT 1 FROM life_financial.operations origin
                WHERE origin.account_id=p_account AND origin.operation_id=claim.operation_id AND origin.command->'owner'=p_owner) THEN RAISE EXCEPTION 'LIFE_SWAP_CLAIM_MISMATCH' USING ERRCODE='P0001'; END IF;
        IF NOT EXISTS(SELECT 1 FROM life_financial.carry_policies WHERE account_id=p_account) OR NOT life_financial.carry_current_ready(p_account) THEN
            RAISE EXCEPTION 'LIFE_SWAP_CARRY_REQUIRED' USING ERRCODE='P0001'; END IF;
        IF EXISTS(SELECT 1 FROM life_financial.swap_preparations prep JOIN life_financial.send_attempts live USING(account_id,intent_id)
            WHERE prep.account_id=p_account AND live.state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND') AND live.side=claim.side AND prep.quote_level=(c->>'level_id')::integer) THEN RAISE EXCEPTION 'LIFE_SWAP_SLOT_CONFLICT' USING ERRCODE='P0001'; END IF;
        permit:=life_financial.swap_permit(c,claim,cp.body);
        payload:=convert_to(life_financial.canonical_json_v1(jsonb_build_object('command',cmd,'config_version',claim.config_version,'risk_epoch',claim.risk_epoch)),'UTF8');
        INSERT INTO life_financial.send_attempts(account_id,intent_id,operation_id,wire_id,product,side,session_id,process_id,generation,epoch,payload,payload_hash,quantity,price,state)
            VALUES(p_account,claim.intent_id,op,claim.wire_id,'SWAP',claim.side,s.session_id,a.owner_process,a.owner_generation,a.owner_epoch,
                payload,encode(sha256(payload),'hex'),claim.quantity_base,claim.price_usdt,'PREPARED');
        INSERT INTO life_financial.swap_preparations VALUES(p_account,op,claim.intent_id,cmd,permit,(c->>'level_id')::integer,at_ms);
        result:=jsonb_build_object('kind',p_kind,'operation_id',op,'intent_id',claim.intent_id,'state','PREPARED','permit',permit,
            'version',p_version+1,'prepared_at_ms',at_ms,'replayed',false,'trading_permission',false);
    ELSE
        IF NOT cmd ?& ARRAY['operation_id','prepare_operation_id','exchange_order_id'] OR jsonb_typeof(cmd->'prepare_operation_id') IS DISTINCT FROM 'string' THEN
            RAISE EXCEPTION 'LIFE_SWAP_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
        prepare_op:=cmd->>'prepare_operation_id';exchange_id:=cmd->>'exchange_order_id';
        SELECT * INTO STRICT d FROM life_financial.swap_preparations WHERE account_id=p_account AND operation_id=prepare_op;
        SELECT * INTO STRICT t FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=d.intent_id;
        SELECT * INTO STRICT old FROM life_financial.operations WHERE account_id=p_account AND operation_id=prepare_op;
        IF old.command->'owner'<>p_owner OR t.process_id<>a.owner_process OR t.generation<>a.owner_generation
            OR t.session_id<>s.session_id OR t.epoch<>a.owner_epoch THEN RAISE EXCEPTION 'LIFE_SWAP_ORIGINAL_OWNER_REQUIRED' USING ERRCODE='P0001'; END IF;
        IF p_kind='SWAP_ARM' THEN
            IF cmd->'exchange_order_id' IS DISTINCT FROM 'null'::jsonb OR t.state<>'PREPARED' THEN RAISE EXCEPTION 'LIFE_SWAP_RECONCILE_BEFORE_RETRY' USING ERRCODE='P0001'; END IF;
            IF NOT life_financial.carry_current_ready(p_account) OR NOT life_financial.swap_config_valid(d.request->'config',p.body,cp.body,at_ms) THEN
                RAISE EXCEPTION 'LIFE_SWAP_CAPTURE_OR_CARRY_STALE' USING ERRCODE='P0001'; END IF;
            UPDATE life_financial.send_attempts SET state='ARMED',version=version+1 WHERE account_id=p_account AND intent_id=d.intent_id;
            UPDATE life_financial.send_attempts SET state='SEND_UNKNOWN',version=version+1 WHERE account_id=p_account AND intent_id=d.intent_id;
        ELSE
            IF t.state<>'SEND_UNKNOWN' OR jsonb_typeof(cmd->'exchange_order_id') IS DISTINCT FROM 'string' OR exchange_id !~ '^[1-9][0-9]{0,63}$' THEN
                RAISE EXCEPTION 'LIFE_SWAP_ACK_INVALID' USING ERRCODE='P0001'; END IF;
            UPDATE life_financial.send_attempts SET state='ACKED',exchange_order_id=exchange_id,version=version+1 WHERE account_id=p_account AND intent_id=d.intent_id;
        END IF;
        INSERT INTO life_financial.swap_intent_events VALUES(p_account,op,prepare_op,p_kind,exchange_id);
        result:=jsonb_build_object('kind',p_kind,'operation_id',prepare_op,'intent_id',d.intent_id,'state',CASE WHEN p_kind='SWAP_ARM' THEN 'SEND_UNKNOWN' ELSE 'ACKED' END,
            'permit',d.permit,'version',p_version+1,'replayed',false,'trading_permission',false);
    END IF;
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version) VALUES(p_account,op,p_fingerprint,request,result,p_version+1);
    RETURN result;
END; $$;

CREATE FUNCTION life_financial.swap_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE op life_financial.operations;d life_financial.swap_preparations;p jsonb;cp jsonb;
BEGIN
    IF NOT life_financial.swap_verified(NEW.account_id) THEN RAISE EXCEPTION 'LIFE_SWAP_UNVERIFIED_AT_COMMIT' USING ERRCODE='P0001'; END IF;
    SELECT * INTO op FROM life_financial.operations WHERE account_id=NEW.account_id AND committed_version=NEW.version;
    IF op.command->>'kind' IN ('SWAP_PREPARE','SWAP_ARM') THEN
        PERFORM life_financial.guard_spot_session(NEW.account_id);
        IF NOT life_financial.swap_budget_ready(NEW.account_id) THEN RAISE EXCEPTION 'LIFE_SWAP_BUDGET_EXHAUSTED_AT_COMMIT' USING ERRCODE='P0001'; END IF;
        IF NOT life_financial.carry_current_ready(NEW.account_id) THEN RAISE EXCEPTION 'LIFE_SWAP_CARRY_STALE_AT_COMMIT' USING ERRCODE='P0001'; END IF;
        SELECT * INTO STRICT d FROM life_financial.swap_preparations WHERE account_id=NEW.account_id AND operation_id=op.receipt->>'operation_id';
        SELECT body INTO STRICT p FROM life_financial.swap_policies WHERE account_id=NEW.account_id;
        SELECT body INTO STRICT cp FROM life_financial.shared_capital_policies WHERE account_id=NEW.account_id;
        IF NOT life_financial.swap_config_valid(d.request->'config',p,cp,floor(extract(epoch FROM clock_timestamp())*1000)::bigint) THEN
            RAISE EXCEPTION 'LIFE_SWAP_CAPTURE_STALE_AT_COMMIT' USING ERRCODE='P0001'; END IF;
    END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER swap_account_commit AFTER UPDATE ON life_financial.accounts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN(NEW.version<>OLD.version) EXECUTE FUNCTION life_financial.swap_commit_guard();
DO $$ DECLARE name text; BEGIN
    FOREACH name IN ARRAY ARRAY['swap_policies','swap_preparations','swap_intent_events'] LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',name);
        EXECUTE format('CREATE POLICY scoped_read ON life_financial.%I FOR SELECT USING(life_financial.can_access(account_id))',name);
        EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',name);
    END LOOP;
END; $$;
REVOKE ALL ON life_financial.swap_policies,life_financial.swap_preparations,life_financial.swap_intent_events FROM PUBLIC;

-- Add only the verified native SWAP intent family to the original base view.
-- All SPOT money/ledger/pending checks below are retained verbatim.
CREATE OR REPLACE FUNCTION life_financial.spot_state_v7(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts; s life_financial.sessions; p life_financial.spot_policies;
    snapshot_body jsonb; b jsonb; budgets jsonb; policy jsonb; unknown boolean; unverified boolean; today text;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account;
    SELECT * INTO STRICT s FROM life_financial.sessions WHERE account_id=p_account AND session_id=a.current_session;
    SELECT * INTO p FROM life_financial.spot_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
    IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_SPOT_POLICY_MISSING' USING ERRCODE='P0001'; END IF;
    today := to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD');
    SELECT jsonb_object_agg(asset,jsonb_build_object('total',total::text,'reserved',reserved::text,'liability',liability::text,'deficit',funding_deficit::text,'version',version,
        'fees',COALESCE((SELECT sum(r.remaining) FROM life_financial.reservations r JOIN life_financial.spot_preparations d
            ON d.account_id=r.account_id AND d.intent_id=r.intent_id WHERE r.account_id=p_account AND r.asset=b.asset
            AND r.state<>'TERMINAL' AND r.reservation_id=d.operation_id||':fee:'||r.asset),0)::text)) INTO b
        FROM life_financial.balances b WHERE account_id=p_account;
    IF b IS NULL OR NOT b ?& ARRAY['LIFE','USDT'] THEN RAISE EXCEPTION 'LIFE_SPOT_BALANCES_MISSING' USING ERRCODE='P0001'; END IF;
    SELECT COALESCE(jsonb_agg(jsonb_build_object('family',family,'scope',scope,'key',window_key,
        'limit',budget_limit::text,'committed',committed::text,'incurred',incurred::text,'version',version)
        ORDER BY family,scope),'[]'::jsonb) INTO budgets FROM life_financial.budget_buckets
        WHERE account_id=p_account AND (scope='SESSION' AND window_key=a.current_session::text OR scope='DAY' AND window_key=today
            OR scope='CAMPAIGN' AND window_key=p.campaign_id);
    unknown := EXISTS(SELECT 1 FROM life_financial.send_attempts WHERE account_id=p_account AND state IN ('ARMED','SEND_UNKNOWN'));
    unverified := NOT life_financial.swap_verified(p_account) OR EXISTS(SELECT 1 FROM life_financial.send_attempts t WHERE t.account_id=p_account AND t.state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND')
        AND (t.session_id<>a.current_session OR t.epoch<>a.owner_epoch
            OR t.process_id<>a.owner_process OR t.generation<>a.owner_generation
            OR t.product='SPOT' AND NOT EXISTS(SELECT 1 FROM life_financial.spot_preparations d JOIN life_financial.operations o USING(account_id,operation_id)
                WHERE d.account_id=t.account_id AND d.intent_id=t.intent_id AND (d.request->>'config_version')::bigint=s.config_version
                AND o.command#>>'{binding,policy_hash}'=a.policy_hash)
            OR t.product='SWAP' AND (NOT EXISTS(
                SELECT 1 FROM life_financial.swap_preparations sd WHERE sd.account_id=t.account_id AND sd.intent_id=t.intent_id
                    AND (sd.permit->>'config_version')::bigint=s.config_version))))
        OR EXISTS(SELECT 1 FROM life_financial.capital_claims WHERE account_id=p_account AND state='HELD')
        OR EXISTS(SELECT 1 FROM life_financial.positions WHERE account_id=p_account AND quantity<>0)
        OR NOT life_financial.spot_accounting_verified(p_account)
        OR EXISTS(SELECT 1 FROM life_financial.financial_events e WHERE e.account_id=p_account AND
            (e.product<>'SPOT' AND NOT (e.product='ACCOUNT' AND e.kind='CASHFLOW') OR e.kind NOT IN ('ACK','FILL','CASHFLOW') OR e.kind='ACK' AND NOT EXISTS(
                SELECT 1 FROM life_financial.send_attempts t WHERE t.account_id=p_account AND t.product='SPOT' AND t.exchange_order_id=e.exchange_identity)
            OR e.kind='CASHFLOW' AND NOT EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts x WHERE x.account_id=p_account AND x.bill_id=e.exchange_identity AND x.state='APPLIED')
            OR e.kind='FILL' AND NOT EXISTS(SELECT 1 FROM life_financial.spot_fill_facts x WHERE x.account_id=p_account AND x.trade_id=e.exchange_identity)))
        OR NOT life_financial.spot_capital_verified(p_account)
        OR NOT life_financial.spot_actions_verified(p_account)
        OR EXISTS(SELECT 1 FROM life_financial.balances b WHERE b.account_id=p_account AND
            b.reserved<>COALESCE((SELECT sum(r.remaining) FROM life_financial.reservations r WHERE r.account_id=p_account AND r.asset=b.asset AND r.state<>'TERMINAL'),0))
        OR EXISTS(SELECT 1 FROM life_financial.reservations r WHERE r.account_id=p_account AND r.state<>'TERMINAL'
            AND NOT EXISTS(SELECT 1 FROM life_financial.spot_preparations d WHERE d.account_id=r.account_id AND d.intent_id=r.intent_id
                AND (r.reservation_id=d.operation_id||':principal' OR r.reservation_id=d.operation_id||':fee:'||r.asset)))
        OR EXISTS(SELECT 1 FROM (
            SELECT w.scope,w.window_key,sum(greatest(d.subsidy,COALESCE(g.execution_loss,0))) AS held FROM life_financial.spot_preparations d
            JOIN life_financial.send_attempts t USING(account_id,intent_id)
            LEFT JOIN life_financial.spot_accounting_progress g USING(account_id,intent_id)
            CROSS JOIN LATERAL (VALUES ('CAMPAIGN',p.campaign_id::text),
                ('DAY',to_char(d.prepared_at AT TIME ZONE 'UTC','YYYY-MM-DD')),('SESSION',t.session_id::text)) AS w(scope,window_key)
            WHERE d.account_id=p_account AND t.state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND') GROUP BY w.scope,w.window_key
        ) expected LEFT JOIN life_financial.budget_buckets b ON b.account_id=p_account AND b.family='SUBSIDY'
            AND b.scope=expected.scope AND b.window_key=expected.window_key WHERE b.account_id IS NULL OR b.committed<expected.held)
        OR EXISTS(SELECT 1 FROM life_financial.spot_preparations d JOIN life_financial.send_attempts t USING(account_id,intent_id)
            LEFT JOIN life_financial.spot_accounting_progress g USING(account_id,intent_id)
            WHERE d.account_id=p_account AND t.state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND') AND (
                NOT EXISTS(SELECT 1 FROM life_financial.reservations r WHERE r.account_id=p_account AND r.reservation_id=d.operation_id||':principal'
                    AND r.intent_id=t.intent_id AND r.state='HELD' AND r.remaining=(t.quantity-COALESCE(g.filled_base,0))*CASE WHEN t.side='BUY' THEN t.price ELSE 1 END
                    AND r.asset=CASE WHEN t.side='BUY' THEN 'USDT' ELSE 'LIFE' END
                    AND r.amount=CASE WHEN t.side='BUY' THEN t.quantity*t.price ELSE t.quantity END)
                OR greatest(0,d.fee_base-COALESCE(g.paid_base,0))<>COALESCE((SELECT remaining FROM life_financial.reservations WHERE account_id=p_account AND reservation_id=d.operation_id||':fee:LIFE' AND state='HELD'),0)
                OR greatest(0,d.fee_quote-COALESCE(g.paid_quote,0))<>COALESCE((SELECT remaining FROM life_financial.reservations WHERE account_id=p_account AND reservation_id=d.operation_id||':fee:USDT' AND state='HELD'),0)));
    SELECT jsonb_object_agg(k,CASE WHEN jsonb_typeof(v)='number' AND k<>'max_quote_levels' THEN to_jsonb(v::text) ELSE v END)
        INTO policy FROM jsonb_each(to_jsonb(p)) AS fields(k,v);
    snapshot_body := jsonb_build_object('account',to_jsonb(a),'session',to_jsonb(s),'policy',policy,'fill_policy',(SELECT to_jsonb(fp) FROM life_financial.spot_fill_policies fp WHERE fp.account_id=p_account AND fp.policy_hash=a.policy_hash),'capital_policy',(SELECT to_jsonb(cp) FROM life_financial.spot_capital_policies cp WHERE cp.account_id=p_account),'balances',b,'budgets',budgets,
        'day',today,'recovery_required',unknown OR unverified
            OR EXISTS(SELECT 1 FROM life_financial.spot_pending_observations WHERE account_id=p_account)
            OR EXISTS(SELECT 1 FROM life_financial.balances WHERE account_id=p_account AND funding_deficit>0)
            OR EXISTS(SELECT 1 FROM life_financial.spot_preparations d JOIN life_financial.spot_accounting_progress g USING(account_id,intent_id)
                WHERE d.account_id=p_account AND (g.paid_base>d.fee_base OR g.paid_quote>d.fee_quote)),'unverified',unverified);
    RETURN snapshot_body||jsonb_build_object('digest',encode(sha256(convert_to(snapshot_body::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
END; $$;

CREATE FUNCTION life_financial.spot_state_before_swap(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b jsonb;verified boolean;ready boolean;
BEGIN
    b:=life_financial.spot_state_before_carry(p_account)-'digest'-'observed_at';
    verified:=life_financial.carry_verified(p_account); ready:=life_financial.carry_current_ready(p_account);
    b:=b||jsonb_build_object('carry',jsonb_build_object('verified',verified,'ready',ready,
        'head',(SELECT to_jsonb(h) FROM life_financial.carry_heads h WHERE account_id=p_account)),
        'recovery_required',(b->>'recovery_required')::boolean OR NOT verified OR NOT ready,
        'unverified',(b->>'unverified')::boolean OR NOT verified);
    RETURN b||jsonb_build_object('digest',encode(sha256(convert_to(b::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
END; $$;
CREATE OR REPLACE FUNCTION life_financial.spot_state(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b jsonb;verified boolean;
BEGIN
    b:=life_financial.spot_state_before_swap(p_account)-'digest'-'observed_at';verified:=life_financial.swap_verified(p_account);
    b:=b||jsonb_build_object('swap',jsonb_build_object('verified',verified,
        'model_hash',(SELECT model_hash FROM life_financial.swap_policies WHERE account_id=p_account),
        'preparations',(SELECT count(*) FROM life_financial.swap_preparations WHERE account_id=p_account),
        'events',(SELECT count(*) FROM life_financial.swap_intent_events WHERE account_id=p_account)),
        'recovery_required',(b->>'recovery_required')::boolean OR NOT verified,
        'unverified',(b->>'unverified')::boolean OR NOT verified);
    RETURN b||jsonb_build_object('digest',encode(sha256(convert_to(b::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
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
    IF op.command->>'kind' IN ('SWAP_ACK','CARRY_CHECK','RECORDER_POSITION_STAGE','RECORDER_POSITION_APPLY','RECORDER_EVENT_STAGE','RECORDER_EVENT_APPLY','RECORDER_CONFIG_STAGE','RECORDER_CONFIG_APPLY','RECORDER_STAGE','RECORDER_APPLY','SPOT_MARKOUT','SPOT_INVENTORY_SETTLE','SPOT_ACK','SPOT_FILL','SPOT_CASHFLOW','SPOT_NAV','SPOT_SESSION_TICK','SPOT_LIFECYCLE','SPOT_TERMINAL') OR op.command->>'kind' IN ('SPOT_ACTION_TRANSITION','SPOT_ACTION_ABANDON')
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
    IF op.command->>'kind'='SPOT_MARKOUT' THEN
        PERFORM life_financial.spot_markout_commit_guard(NEW.account_id,op.command); END IF;
    IF op.command->>'kind'='SPOT_INVENTORY_SETTLE' THEN
        PERFORM life_financial.spot_inventory_commit_guard(NEW.account_id,op.command); END IF;
    IF op.command->>'kind' IN ('SPOT_ACTION_CLAIM','SPOT_ACTION_TRANSITION','SPOT_ACTION_ABANDON') THEN
        PERFORM life_financial.spot_action_commit_guard(NEW.account_id,op.command); END IF;
    IF op.command->>'kind'='SPOT_PREPARE' AND op.command ? 'action' THEN
        PERFORM life_financial.spot_action_prepare_commit_guard(NEW.account_id,op.command); END IF;
    RETURN NULL;
END; $$;

REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
