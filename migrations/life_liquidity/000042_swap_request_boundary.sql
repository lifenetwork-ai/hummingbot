-- One original SWAP HTTP body per prepared intent. No source/owner bootstrap.
CREATE TABLE life_financial.swap_boundary_policies (
    account_id uuid PRIMARY KEY REFERENCES life_financial.swap_policies,policy_hash life_financial.digest NOT NULL,
    max_write_window_ms integer NOT NULL CHECK(max_write_window_ms BETWEEN 1 AND 60000),
    max_requests integer NOT NULL CHECK(max_requests BETWEEN 1 AND 4096),
    max_total_bytes integer NOT NULL CHECK(max_total_bytes BETWEEN 1 AND 16777216),
    FOREIGN KEY(account_id,policy_hash) REFERENCES life_financial.policies
);
CREATE TABLE life_financial.swap_boundaries (
    account_id uuid NOT NULL REFERENCES life_financial.swap_boundary_policies,operation_id life_financial.identity NOT NULL,
    prepare_operation_id life_financial.identity NOT NULL,arm_operation_id life_financial.identity NOT NULL,
    carry_operation_id life_financial.identity NOT NULL,hedge_operation_id life_financial.identity,
    wire_json text NOT NULL CHECK(octet_length(wire_json) BETWEEN 1 AND 4096),wire_sha256 life_financial.digest NOT NULL,
    checked_at_ms bigint NOT NULL,valid_until_ms bigint NOT NULL CHECK(valid_until_ms>checked_at_ms),
    PRIMARY KEY(account_id,operation_id),UNIQUE(account_id,prepare_operation_id),
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(account_id,prepare_operation_id) REFERENCES life_financial.swap_preparations,
    FOREIGN KEY(account_id,arm_operation_id) REFERENCES life_financial.operations,
    FOREIGN KEY(account_id,carry_operation_id) REFERENCES life_financial.carry_checks,
    FOREIGN KEY(account_id,hedge_operation_id) REFERENCES life_financial.hedge_plan_facts,
    CHECK(wire_sha256=encode(sha256(convert_to(wire_json,'UTF8')),'hex'))
);
CREATE TABLE life_financial.swap_ack_ingress (
    account_id uuid NOT NULL,prepare_operation_id life_financial.identity NOT NULL,
    body jsonb NOT NULL CHECK(jsonb_typeof(body)='object' AND octet_length(body::text)<=8192),
    source_hash life_financial.digest NOT NULL,captured_at timestamptz NOT NULL CHECK(isfinite(captured_at)),
    PRIMARY KEY(account_id,prepare_operation_id),
    FOREIGN KEY(account_id,prepare_operation_id) REFERENCES life_financial.swap_boundaries(account_id,prepare_operation_id)
);
CREATE TABLE life_financial.swap_ack_deliveries (
    account_id uuid NOT NULL,prepare_operation_id life_financial.identity NOT NULL,ack_operation_id life_financial.identity NOT NULL,
    source_hash life_financial.digest NOT NULL,PRIMARY KEY(account_id,prepare_operation_id),
    FOREIGN KEY(account_id,prepare_operation_id) REFERENCES life_financial.swap_ack_ingress,
    FOREIGN KEY(account_id,ack_operation_id) REFERENCES life_financial.operations
);
DO $$ DECLARE t text;BEGIN
    FOREACH t IN ARRAY ARRAY['swap_boundary_policies','swap_boundaries','swap_ack_ingress','swap_ack_deliveries'] LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY scoped_read ON life_financial.%I FOR SELECT USING(life_financial.can_access(account_id))',t);
        EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',t);
    END LOOP;
END; $$;
CREATE FUNCTION life_financial.swap_wire_matches(wire_text text,permit jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE w jsonb;j json;expected_side text;close_net boolean;keys integer;
BEGIN
    j:=wire_text::json;w:=j::jsonb;
    IF json_typeof(j)<>'object' OR octet_length(wire_text)>4096 THEN RETURN false;END IF;
    SELECT count(*) INTO keys FROM json_object_keys(j);
    close_net:=permit->>'position_mode'='ONEWAY' AND permit->>'position_action'='CLOSE';
    expected_side:=CASE WHEN permit->>'position_mode'='ONEWAY' THEN 'net'
        WHEN (permit->>'side'='BUY')=(permit->>'position_action'='OPEN') THEN 'long' ELSE 'short' END;
    IF keys<>(CASE WHEN close_net THEN 9 ELSE 8 END) OR keys<>(SELECT count(*) FROM jsonb_object_keys(w))
        OR NOT w ?& ARRAY['instId','tdMode','ordType','clOrdId','side','sz','px','posSide']
        OR EXISTS(SELECT 1 FROM jsonb_each(w) e WHERE e.key<>'reduceOnly' AND jsonb_typeof(e.value)<>'string') THEN RETURN false;END IF;
    RETURN w->>'instId'='LIFE-USDT-SWAP' AND w->>'tdMode'='cross' AND w->>'ordType'='post_only'
        AND w->>'clOrdId'=permit->>'client_order_id' AND w->>'side'=lower(permit->>'side') AND w->>'posSide'=expected_side
        AND (w->>'sz')::life_financial.positive_amount=(permit->>'contracts')::numeric
        AND (w->>'px')::life_financial.positive_amount=(permit->>'price_usdt')::numeric
        AND (CASE WHEN close_net THEN w->'reduceOnly'='true'::jsonb ELSE NOT w ? 'reduceOnly' END);
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
CREATE FUNCTION life_financial.swap_boundary_until(p_account uuid,p_prepare text,p_carry text,p_hedge text,owner jsonb,at_ms bigint) RETURNS bigint
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE until_ms bigint;d life_financial.swap_preparations;b jsonb;age bigint;k text;
BEGIN
    SELECT least(at_ms+p.max_write_window_ms,floor(extract(epoch FROM (owner->>'expires_at')::timestamptz)*1000)::bigint,
        floor(extract(epoch FROM s.expires_at)*1000)::bigint) INTO STRICT until_ms FROM life_financial.swap_boundary_policies p
        JOIN life_financial.sessions s USING(account_id) WHERE p.account_id=p_account AND s.session_id=(owner->>'session_id')::uuid;
    SELECT * INTO STRICT d FROM life_financial.swap_preparations WHERE account_id=p_account AND operation_id=p_prepare;
    SELECT least(until_ms,floor((d.request#>>'{config,timestamp}')::numeric*1000)::bigint+(body->>'max_capture_age_ms')::bigint)
        INTO until_ms FROM life_financial.swap_policies WHERE account_id=p_account;
    SELECT bundle INTO STRICT b FROM life_financial.carry_checks WHERE account_id=p_account AND operation_id=p_carry;
    SELECT (body->>'max_age_ms')::bigint INTO STRICT age FROM life_financial.carry_policies WHERE account_id=p_account;
    FOREACH k IN ARRAY ARRAY['account','mark','index','funding','terms'] LOOP
        until_ms:=least(until_ms,(CASE WHEN k='account' THEN b#>>'{account,snapshot,observed_at_ms}' ELSE b#>>ARRAY[k,'observed_at_ms'] END)::bigint+age);
    END LOOP;
    SELECT least(until_ms,(b#>>'{account,snapshot,observed_at_ms}')::bigint+(body->>'max_age_ms')::bigint,
        (b#>>'{funding,settlement_ms}')::bigint-1) INTO until_ms FROM life_financial.shared_capital_policies WHERE account_id=p_account;
    IF p_hedge IS NOT NULL THEN
        SELECT least(until_ms,(h.progress#>>'{market,observed_at_ms}')::bigint+(p.body->>'max_market_age_ms')::bigint)
            INTO STRICT until_ms FROM life_financial.hedge_plan_facts h JOIN life_financial.hedge_policies p USING(account_id)
            WHERE h.account_id=p_account AND h.operation_id=p_hedge;
    END IF;
    RETURN until_ms;
END; $$;
CREATE FUNCTION life_financial.swap_boundary_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r life_financial.swap_boundaries;p life_financial.swap_boundary_policies;o life_financial.operations;arm life_financial.operations;d life_financial.swap_preparations;
BEGIN
    SELECT * INTO p FROM life_financial.swap_boundary_policies WHERE account_id=p_account;
    IF (SELECT count(*) FROM life_financial.swap_boundaries WHERE account_id=p_account)>p.max_requests
        OR (SELECT COALESCE(sum(octet_length(wire_json)),0) FROM life_financial.swap_boundaries WHERE account_id=p_account)>p.max_total_bytes THEN RETURN false;END IF;
    FOR r IN SELECT * FROM life_financial.swap_boundaries WHERE account_id=p_account LOOP
        SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=r.operation_id;
        SELECT * INTO STRICT arm FROM life_financial.operations WHERE account_id=p_account AND operation_id=r.arm_operation_id;
        SELECT * INTO STRICT d FROM life_financial.swap_preparations WHERE account_id=p_account AND operation_id=r.prepare_operation_id;
        IF p.policy_hash IS DISTINCT FROM (SELECT policy_hash FROM life_financial.accounts WHERE account_id=p_account)
            OR NOT life_financial.swap_wire_matches(r.wire_json,d.permit) OR arm.command->>'kind'<>'SWAP_ARM'
            OR arm.command#>>'{command,prepare_operation_id}'<>r.prepare_operation_id
            OR arm.command->'owner' IS DISTINCT FROM o.command->'owner' OR arm.command->'binding' IS DISTINCT FROM o.command->'binding'
            OR arm.committed_version<>o.committed_version-1 OR (o.command->>'version')::bigint<>o.committed_version-2
            OR r.carry_operation_id IS DISTINCT FROM (SELECT h.operation_id FROM life_financial.carry_checks h
                JOIN life_financial.operations history USING(account_id,operation_id) WHERE h.account_id=p_account
                AND history.committed_version<=o.committed_version-2 ORDER BY history.committed_version DESC LIMIT 1)
            OR r.hedge_operation_id IS DISTINCT FROM (SELECT h.operation_id FROM life_financial.hedge_plan_facts h
                JOIN life_financial.operations history USING(account_id,operation_id) WHERE h.account_id=p_account
                AND history.committed_version<=o.committed_version-2 ORDER BY history.committed_version DESC LIMIT 1)
            OR r.valid_until_ms IS DISTINCT FROM life_financial.swap_boundary_until(p_account,r.prepare_operation_id,r.carry_operation_id,r.hedge_operation_id,o.command->'owner',r.checked_at_ms)
            OR o.command->>'kind'<>'SWAP_BOUNDARY' OR o.command->'command' IS DISTINCT FROM jsonb_build_object('operation_id',r.operation_id,'prepare_operation_id',r.prepare_operation_id,'wire_json',r.wire_json)
            OR o.fingerprint<>encode(sha256(convert_to(life_financial.canonical_json_v1(o.command),'UTF8')),'hex')
            OR o.receipt IS DISTINCT FROM jsonb_build_object('kind','SWAP_BOUNDARY','prepare_operation_id',r.prepare_operation_id,'intent_id',d.intent_id,
                'wire_sha256',r.wire_sha256,'checked_at_ms',r.checked_at_ms,'valid_until_ms',r.valid_until_ms,'state','SEND_UNKNOWN',
                'version',o.committed_version,'replayed',false,'trading_permission',false) THEN RETURN false;END IF;
    END LOOP;
    RETURN NOT EXISTS(SELECT 1 FROM life_financial.operations history WHERE history.account_id=p_account AND history.command->>'kind'='SWAP_BOUNDARY'
        AND NOT EXISTS(SELECT 1 FROM life_financial.swap_boundaries boundary WHERE boundary.account_id=p_account AND boundary.operation_id=history.operation_id));
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
CREATE FUNCTION life_financial.swap_ack_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r life_financial.swap_ack_ingress;delivery life_financial.swap_ack_deliveries;op life_financial.operations;boundary life_financial.swap_boundaries;
BEGIN
    FOR r IN SELECT * FROM life_financial.swap_ack_ingress WHERE account_id=p_account LOOP
        IF r.source_hash IS DISTINCT FROM encode(sha256(convert_to(r.body::text,'UTF8')),'hex')
            OR r.prepare_operation_id IS DISTINCT FROM r.body->>'prepare_operation_id'
            OR (r.body->>'captured_at')::timestamptz IS DISTINCT FROM r.captured_at THEN RETURN false;END IF;
        -- Qualify a native ACK as soon as it exists, before any delivery
        -- marker. Otherwise a bad raw response could corrupt identity in an
        -- earlier committed accounting transaction and fail only at marking.
        SELECT * INTO op FROM life_financial.operations WHERE account_id=p_account
            AND operation_id='swap-ack:'||encode(sha256(convert_to(r.prepare_operation_id,'UTF8')),'hex');
        IF FOUND THEN
            SELECT * INTO STRICT boundary FROM life_financial.swap_boundaries WHERE account_id=p_account AND prepare_operation_id=r.prepare_operation_id;
            IF op.recorded_at<r.captured_at OR NOT isfinite(op.recorded_at) OR op.command->>'kind' IS DISTINCT FROM 'SWAP_ACK'
                OR op.command#>>'{command,prepare_operation_id}' IS DISTINCT FROM r.prepare_operation_id
                OR op.command#>>'{command,exchange_order_id}' IS DISTINCT FROM r.body->>'exchange_order_id'
                OR op.command->'binding' IS DISTINCT FROM r.body->'binding' OR op.command->'owner' IS DISTINCT FROM r.body->'owner'
                OR op.receipt->>'state' IS DISTINCT FROM 'ACKED' OR boundary.wire_sha256 IS DISTINCT FROM r.body->>'wire_sha256' THEN RETURN false;END IF;
        END IF;
        SELECT * INTO delivery FROM life_financial.swap_ack_deliveries WHERE account_id=p_account AND prepare_operation_id=r.prepare_operation_id;
        IF FOUND AND (op.operation_id IS NULL OR delivery.ack_operation_id IS DISTINCT FROM op.operation_id
            OR delivery.source_hash IS DISTINCT FROM r.source_hash) THEN RETURN false;END IF;
    END LOOP;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
CREATE FUNCTION life_financial.swap_capture_ack(p_account uuid,p_body jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r life_financial.swap_ack_ingress;b jsonb;at timestamptz;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR pg_is_in_recovery() OR current_setting('synchronous_commit')<>'on'
        OR current_setting('fsync')<>'on' OR current_setting('full_page_writes')<>'on'
        OR jsonb_typeof(p_body) IS DISTINCT FROM 'object' OR octet_length(p_body::text)>8000
        OR NOT p_body ?& ARRAY['binding','owner','prepare_operation_id','exchange_order_id','wire_sha256']
        OR (SELECT count(*) FROM jsonb_object_keys(p_body))<>5
        OR jsonb_typeof(p_body->'binding') IS DISTINCT FROM 'object' OR jsonb_typeof(p_body->'owner') IS DISTINCT FROM 'object'
        OR jsonb_typeof(p_body->'prepare_operation_id') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_body->'exchange_order_id') IS DISTINCT FROM 'string' OR p_body->>'exchange_order_id' !~ '^[0-9]{1,64}$'
        OR jsonb_typeof(p_body->'wire_sha256') IS DISTINCT FROM 'string' OR p_body->>'wire_sha256' !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'LIFE_SWAP_ACK_CAPTURE_INVALID' USING ERRCODE='P0001';END IF;
    -- Received diagnostics can survive HALT/expiry/inconsistent financial state;
    -- this function changes neither account version nor money/claims/permission.
    PERFORM 1 FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    IF NOT EXISTS(SELECT 1 FROM life_financial.swap_boundaries WHERE account_id=p_account AND prepare_operation_id=p_body->>'prepare_operation_id') THEN
        RAISE EXCEPTION 'LIFE_SWAP_ACK_CAPTURE_ORIGIN_MISSING' USING ERRCODE='P0001';END IF;
    SELECT * INTO r FROM life_financial.swap_ack_ingress WHERE account_id=p_account AND prepare_operation_id=p_body->>'prepare_operation_id';
    IF FOUND THEN
        IF r.body-'captured_at' IS DISTINCT FROM p_body OR r.source_hash IS DISTINCT FROM encode(sha256(convert_to(r.body::text,'UTF8')),'hex') THEN
            RAISE EXCEPTION 'LIFE_SWAP_ACK_IDENTITY_CONFLICT' USING ERRCODE='P0001';END IF;
        RETURN jsonb_build_object('kind','SWAP_ACK_CAPTURE','body',r.body,'replayed',true,'trading_permission',false);
    END IF;
    at:=clock_timestamp();b:=p_body||jsonb_build_object('captured_at',at);
    INSERT INTO life_financial.swap_ack_ingress VALUES(p_account,p_body->>'prepare_operation_id',b,encode(sha256(convert_to(b::text,'UTF8')),'hex'),at);
    RETURN jsonb_build_object('kind','SWAP_ACK_CAPTURE','body',b,'replayed',false,'trading_permission',false);
END; $$;
CREATE FUNCTION life_financial.swap_mark_ack(p_account uuid,p_prepare text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r life_financial.swap_ack_ingress;op life_financial.operations;replayed boolean;
BEGIN
    IF NOT life_financial.can_access(p_account,true) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID' USING ERRCODE='P0001';END IF;
    PERFORM 1 FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    SELECT * INTO STRICT r FROM life_financial.swap_ack_ingress WHERE account_id=p_account AND prepare_operation_id=p_prepare;
    IF clock_timestamp()<r.captured_at THEN RAISE EXCEPTION 'LIFE_SWAP_ACK_CLOCK_ROLLBACK' USING ERRCODE='P0001';END IF;
    SELECT * INTO op FROM life_financial.operations WHERE account_id=p_account
        AND operation_id='swap-ack:'||encode(sha256(convert_to(p_prepare,'UTF8')),'hex');
    IF NOT FOUND OR NOT life_financial.swap_verified(p_account) OR (life_financial.spot_state(p_account)->>'unverified')::boolean
        OR op.command->>'kind' IS DISTINCT FROM 'SWAP_ACK' THEN RAISE EXCEPTION 'LIFE_SWAP_ACK_APPLICATION_UNQUALIFIED' USING ERRCODE='P0001';END IF;
    replayed:=EXISTS(SELECT 1 FROM life_financial.swap_ack_deliveries WHERE account_id=p_account AND prepare_operation_id=p_prepare);
    IF NOT replayed THEN INSERT INTO life_financial.swap_ack_deliveries VALUES(p_account,p_prepare,op.operation_id,r.source_hash);END IF;
    IF NOT life_financial.swap_ack_verified(p_account) THEN RAISE EXCEPTION 'LIFE_SWAP_ACK_APPLICATION_UNQUALIFIED' USING ERRCODE='P0001';END IF;
    RETURN jsonb_build_object('kind','SWAP_ACK_DELIVERY','body',r.body,'replayed',replayed,'trading_permission',false);
END; $$;
CREATE FUNCTION life_financial.swap_read_ack(p_account uuid,p_rows integer,p_bytes integer,p_prepare text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID' USING ERRCODE='P0001';END IF;
    IF p_rows IS NULL OR p_bytes IS NULL OR p_rows NOT BETWEEN 1 AND 4096 OR p_bytes NOT BETWEEN 1 AND 16777216
        OR (SELECT count(*) FROM life_financial.swap_ack_ingress WHERE account_id=p_account)>p_rows THEN
        RAISE EXCEPTION 'LIFE_SWAP_ACK_READ_BOUND' USING ERRCODE='P0001';END IF;
    IF NOT life_financial.swap_ack_verified(p_account) THEN RAISE EXCEPTION 'LIFE_SWAP_ACK_UNVERIFIED' USING ERRCODE='P0001';END IF;
    SELECT COALESCE(jsonb_agg(jsonb_build_object('kind','SWAP_ACK_CAPTURE','body',r.body,'replayed',true,'trading_permission',false)
        ORDER BY r.prepare_operation_id),'[]'::jsonb) INTO result FROM life_financial.swap_ack_ingress r WHERE r.account_id=p_account
        AND (p_prepare IS NOT NULL AND r.prepare_operation_id=p_prepare OR p_prepare IS NULL AND NOT EXISTS(
            SELECT 1 FROM life_financial.swap_ack_deliveries d WHERE d.account_id=p_account AND d.prepare_operation_id=r.prepare_operation_id));
    IF octet_length(result::text)>p_bytes THEN RAISE EXCEPTION 'LIFE_SWAP_ACK_READ_BOUND' USING ERRCODE='P0001';END IF;
    RETURN result;
END; $$;
-- Keep the existing function OID and its role ACL; only add the new proof.
DO $$ DECLARE d text;BEGIN
    d:=pg_get_functiondef('life_financial.swap_verified(uuid)'::regprocedure);
    EXECUTE replace(d,'CREATE OR REPLACE FUNCTION life_financial.swap_verified(', 'CREATE FUNCTION life_financial.swap_verified_before_boundary(');
END; $$;
CREATE OR REPLACE FUNCTION life_financial.swap_verified(p_account uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT life_financial.swap_verified_before_boundary(p_account) AND life_financial.swap_boundary_verified(p_account) AND life_financial.swap_ack_verified(p_account);
$$;
CREATE FUNCTION life_financial.swap_boundary_arm(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,p_cut life_financial.digest,p_fingerprint life_financial.digest,cmd jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts;d life_financial.swap_preparations;p life_financial.swap_boundary_policies;request jsonb;sub jsonb;receipt jsonb;
    operation text;arm_id text;carry_id text;hedge_id text;at_ms bigint;until_ms bigint;wire_hash text;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR jsonb_typeof(cmd) IS DISTINCT FROM 'object'
        OR (SELECT count(*) FROM jsonb_object_keys(cmd))<>3 OR NOT cmd ?& ARRAY['operation_id','prepare_operation_id','wire_json']
        OR jsonb_typeof(cmd->'wire_json') IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'LIFE_SWAP_BOUNDARY_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
    operation:='swap-boundary:'||encode(sha256(convert_to(cmd->>'prepare_operation_id','UTF8')),'hex');
    arm_id:='swap-arm:'||encode(sha256(convert_to(cmd->>'prepare_operation_id','UTF8')),'hex');
    IF cmd->>'operation_id' IS DISTINCT FROM operation THEN RAISE EXCEPTION 'LIFE_SWAP_BOUNDARY_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
    request:=jsonb_build_object('kind','SWAP_BOUNDARY','binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',cmd);
    IF p_fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(request),'UTF8')),'hex') THEN RAISE EXCEPTION 'LIFE_SWAP_FINGERPRINT_INVALID' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    PERFORM life_financial.guard_account(p_account,p_binding,p_owner,p_version);
    IF EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=p_account AND operation_id IN (operation,arm_id)) THEN
        RAISE EXCEPTION 'LIFE_SWAP_RECONCILE_BEFORE_RETRY' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT p FROM life_financial.swap_boundary_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
    SELECT * INTO STRICT d FROM life_financial.swap_preparations WHERE account_id=p_account AND operation_id=cmd->>'prepare_operation_id';
    IF NOT life_financial.swap_wire_matches(cmd->>'wire_json',d.permit) THEN RAISE EXCEPTION 'LIFE_SWAP_WIRE_MISMATCH' USING ERRCODE='P0001';END IF;
    IF (SELECT count(*) FROM life_financial.swap_boundaries WHERE account_id=p_account)>=p.max_requests
        OR (SELECT COALESCE(sum(octet_length(wire_json)),0) FROM life_financial.swap_boundaries WHERE account_id=p_account)+octet_length(cmd->>'wire_json')>p.max_total_bytes THEN
        RAISE EXCEPTION 'LIFE_SWAP_BOUNDARY_BOUND' USING ERRCODE='P0001';END IF;
    SELECT operation_id INTO STRICT carry_id FROM life_financial.carry_heads WHERE account_id=p_account;
    SELECT h.operation_id INTO hedge_id FROM life_financial.hedge_plan_facts h JOIN life_financial.operations o USING(account_id,operation_id)
        WHERE h.account_id=p_account ORDER BY o.committed_version DESC LIMIT 1;
    at_ms:=floor(extract(epoch FROM clock_timestamp())*1000)::bigint;
    until_ms:=life_financial.swap_boundary_until(p_account,d.operation_id,carry_id,hedge_id,p_owner,at_ms);
    IF until_ms<=at_ms THEN RAISE EXCEPTION 'LIFE_SWAP_BOUNDARY_EXPIRED' USING ERRCODE='P0001';END IF;
    sub:=jsonb_build_object('operation_id',arm_id,'prepare_operation_id',d.operation_id,'exchange_order_id',NULL);
    receipt:=life_financial.swap_command(p_account,p_binding,p_owner,p_version,p_cut,
        encode(sha256(convert_to(life_financial.canonical_json_v1(jsonb_build_object('kind','SWAP_ARM','binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',sub)),'UTF8')),'hex'),'SWAP_ARM',sub);
    wire_hash:=encode(sha256(convert_to(cmd->>'wire_json','UTF8')),'hex');
    INSERT INTO life_financial.swap_boundaries VALUES(p_account,operation,d.operation_id,arm_id,carry_id,hedge_id,cmd->>'wire_json',wire_hash,at_ms,until_ms);
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    receipt:=jsonb_build_object('kind','SWAP_BOUNDARY','prepare_operation_id',d.operation_id,'intent_id',d.intent_id,'wire_sha256',wire_hash,
        'checked_at_ms',at_ms,'valid_until_ms',until_ms,'state','SEND_UNKNOWN','version',p_version+2,'replayed',false,'trading_permission',false);
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version) VALUES(p_account,operation,p_fingerprint,request,receipt,p_version+2);
    IF NOT life_financial.swap_boundary_verified(p_account) THEN RAISE EXCEPTION 'LIFE_SWAP_BOUNDARY_UNVERIFIED' USING ERRCODE='P0001';END IF;
    RETURN receipt;
END; $$;
CREATE FUNCTION life_financial.swap_boundary_read(p_account uuid,p_rows integer,p_bytes integer,p_prepare text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID' USING ERRCODE='P0001';END IF;
    IF p_rows IS NULL OR p_bytes IS NULL OR p_rows NOT BETWEEN 1 AND 4096 OR p_bytes NOT BETWEEN 1 AND 16777216
        OR (SELECT count(*) FROM life_financial.swap_boundaries WHERE account_id=p_account)>p_rows THEN RAISE EXCEPTION 'LIFE_SWAP_BOUNDARY_READ_BOUND' USING ERRCODE='P0001';END IF;
    IF NOT life_financial.swap_verified(p_account) THEN RAISE EXCEPTION 'LIFE_SWAP_BOUNDARY_UNVERIFIED' USING ERRCODE='P0001';END IF;
    IF p_prepare IS NULL THEN result:=jsonb_build_object('policy',(SELECT to_jsonb(p)-'account_id' FROM life_financial.swap_boundary_policies p WHERE p.account_id=p_account));
    ELSE SELECT jsonb_build_object('command',o.command,'receipt',o.receipt) INTO result FROM life_financial.swap_boundaries r JOIN life_financial.operations o USING(account_id,operation_id)
        WHERE r.account_id=p_account AND r.prepare_operation_id=p_prepare;END IF;
    IF octet_length(result::text)>p_bytes THEN RAISE EXCEPTION 'LIFE_SWAP_BOUNDARY_READ_BOUND' USING ERRCODE='P0001';END IF;
    RETURN result;
END; $$;
DO $$ DECLARE definition text;BEGIN
    definition:=pg_get_functiondef('life_financial.spot_state(uuid)'::regprocedure);
    EXECUTE replace(definition,'CREATE OR REPLACE FUNCTION life_financial.spot_state(',
        'CREATE FUNCTION life_financial.spot_state_before_swap_boundary(');
END; $$;
CREATE OR REPLACE FUNCTION life_financial.spot_state(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b jsonb;verified boolean;pending boolean;
BEGIN
    b:=life_financial.spot_state_before_swap_boundary(p_account)-'digest'-'observed_at';
    verified:=life_financial.swap_boundary_verified(p_account) AND life_financial.swap_ack_verified(p_account);
    pending:=EXISTS(SELECT 1 FROM life_financial.swap_ack_ingress r WHERE r.account_id=p_account
        AND NOT EXISTS(SELECT 1 FROM life_financial.swap_ack_deliveries d WHERE d.account_id=p_account AND d.prepare_operation_id=r.prepare_operation_id));
    b:=b||jsonb_build_object('swap_ack_pending',pending,'swap_ack_version',
        (SELECT count(*) FROM life_financial.swap_ack_ingress WHERE account_id=p_account)+(SELECT count(*) FROM life_financial.swap_ack_deliveries WHERE account_id=p_account));
    b:=b||jsonb_build_object('swap_boundary',jsonb_build_object('verified',verified,
        'policy',(SELECT to_jsonb(p) FROM life_financial.swap_boundary_policies p WHERE p.account_id=p_account),
        'requests',COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY checked_at_ms,operation_id)
            FROM life_financial.swap_boundaries r WHERE account_id=p_account),'[]'::jsonb)),
        'financial_recovery_required',(b->>'financial_recovery_required')::boolean OR NOT verified OR pending,
        'recovery_required',(b->>'recovery_required')::boolean OR NOT verified OR pending,
        'unverified',(b->>'unverified')::boolean OR NOT verified);
    RETURN b||jsonb_build_object('digest',encode(sha256(convert_to(b::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
END; $$;
DO $$ DECLARE definition text;BEGIN
    definition:=pg_get_functiondef('life_financial.guard_account(uuid,jsonb,jsonb,bigint)'::regprocedure);
    definition:=replace(definition,E'BEGIN\n',E'BEGIN\n    IF NOT life_financial.swap_ack_verified(p_account) OR EXISTS(SELECT 1 FROM life_financial.swap_ack_ingress r WHERE r.account_id=p_account AND NOT EXISTS(SELECT 1 FROM life_financial.swap_ack_deliveries d WHERE d.account_id=p_account AND d.prepare_operation_id=r.prepare_operation_id)) THEN RAISE EXCEPTION ''LIFE_SWAP_ACK_RECOVERY_REQUIRED'' USING ERRCODE=''P0001'';END IF;\n');
    EXECUTE definition;
END; $$;
CREATE FUNCTION life_financial.swap_boundary_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r life_financial.swap_boundaries;o life_financial.operations;now_ms bigint;captured_at timestamptz;
BEGIN
    IF NOT life_financial.swap_boundary_verified(NEW.account_id) OR NOT life_financial.swap_ack_verified(NEW.account_id) THEN RAISE EXCEPTION 'LIFE_SWAP_BOUNDARY_UNVERIFIED_AT_COMMIT' USING ERRCODE='P0001';END IF;
    IF TG_TABLE_NAME<>'accounts' THEN
        SELECT received.captured_at INTO STRICT captured_at FROM life_financial.swap_ack_ingress received
            WHERE received.account_id=NEW.account_id AND received.prepare_operation_id=NEW.prepare_operation_id;
        IF clock_timestamp()<captured_at THEN RAISE EXCEPTION 'LIFE_SWAP_ACK_CLOCK_ROLLBACK_AT_COMMIT' USING ERRCODE='P0001';END IF;
        RETURN NULL;
    END IF;
    SELECT * INTO o FROM life_financial.operations WHERE account_id=NEW.account_id AND committed_version=NEW.version;
    IF o.command->>'kind'='SWAP_ACK' THEN
        SELECT received.captured_at INTO captured_at FROM life_financial.swap_ack_ingress received
            WHERE received.account_id=NEW.account_id AND received.prepare_operation_id=o.command#>>'{command,prepare_operation_id}';
        IF clock_timestamp()<captured_at THEN RAISE EXCEPTION 'LIFE_SWAP_ACK_CLOCK_ROLLBACK_AT_COMMIT' USING ERRCODE='P0001';END IF;
    ELSIF o.command->>'kind'='SWAP_BOUNDARY' THEN
        SELECT * INTO STRICT r FROM life_financial.swap_boundaries WHERE account_id=NEW.account_id AND operation_id=o.operation_id;
        now_ms:=floor(extract(epoch FROM clock_timestamp())*1000)::bigint;
        IF NOT r.checked_at_ms<=now_ms OR now_ms>r.valid_until_ms THEN RAISE EXCEPTION 'LIFE_SWAP_BOUNDARY_EXPIRED_AT_COMMIT' USING ERRCODE='P0001';END IF;
    END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER swap_boundary_account_commit AFTER UPDATE ON life_financial.accounts DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    WHEN(NEW.version<>OLD.version) EXECUTE FUNCTION life_financial.swap_boundary_commit_guard();
CREATE CONSTRAINT TRIGGER swap_ack_delivery_commit AFTER INSERT ON life_financial.swap_ack_deliveries DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION life_financial.swap_boundary_commit_guard();
REVOKE ALL ON life_financial.swap_boundary_policies,life_financial.swap_boundaries,life_financial.swap_ack_ingress,life_financial.swap_ack_deliveries FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
