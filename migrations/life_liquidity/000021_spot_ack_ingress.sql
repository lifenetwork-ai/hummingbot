-- Received ACK diagnostics publish before financial application. No money/grant.
CREATE TABLE life_financial.spot_ack_ingress (
    account_id uuid NOT NULL REFERENCES life_financial.accounts,
    ingress_id life_financial.identity NOT NULL, ack_operation_id life_financial.identity NOT NULL,
    body jsonb NOT NULL CHECK(jsonb_typeof(body)='object' AND octet_length(body::text)<=1048576),
    source_hash life_financial.digest NOT NULL, captured_at timestamptz NOT NULL CHECK(isfinite(captured_at)),
    PRIMARY KEY(account_id,ingress_id), UNIQUE(account_id,ack_operation_id)
);
CREATE TABLE life_financial.spot_ack_ingress_receipts (
    account_id uuid NOT NULL, ingress_id life_financial.identity NOT NULL,
    ack_operation_id life_financial.identity NOT NULL, source_hash life_financial.digest NOT NULL,
    PRIMARY KEY(account_id,ingress_id),
    FOREIGN KEY(account_id,ingress_id) REFERENCES life_financial.spot_ack_ingress,
    FOREIGN KEY(account_id,ack_operation_id) REFERENCES life_financial.operations
);
DO $$ DECLARE name text; BEGIN
    FOREACH name IN ARRAY ARRAY['spot_ack_ingress','spot_ack_ingress_receipts'] LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',name);
        EXECUTE format('CREATE POLICY scoped_read ON life_financial.%I FOR SELECT USING(life_financial.can_access(account_id))',name);
        EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',name);
    END LOOP;
END; $$;

CREATE FUNCTION life_financial.spot_ack_ingress_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r life_financial.spot_ack_ingress; delivery life_financial.spot_ack_ingress_receipts; op life_financial.operations;
BEGIN
    FOR r IN SELECT * FROM life_financial.spot_ack_ingress WHERE account_id=p_account LOOP
        IF r.source_hash IS DISTINCT FROM encode(sha256(convert_to(r.body::text,'UTF8')),'hex')
            OR r.ack_operation_id IS DISTINCT FROM r.body#>>'{command,operation_id}'
            OR r.ingress_id IS DISTINCT FROM 'rawack:'||encode(sha256(convert_to(r.ack_operation_id,'UTF8')),'hex')
            OR (r.body#>>'{command,observed_at}')::timestamptz IS DISTINCT FROM r.captured_at THEN RETURN false; END IF;
        SELECT * INTO delivery FROM life_financial.spot_ack_ingress_receipts WHERE account_id=p_account AND ingress_id=r.ingress_id;
        IF FOUND THEN
            SELECT * INTO op FROM life_financial.operations WHERE account_id=p_account AND operation_id=delivery.ack_operation_id;
            IF NOT FOUND OR delivery.ack_operation_id<>r.ack_operation_id OR delivery.source_hash<>r.source_hash
                OR op.command->>'kind' IS DISTINCT FROM 'SPOT_ACK' OR op.receipt->>'state' IS DISTINCT FROM 'ACKED'
                OR op.command->'binding' IS DISTINCT FROM r.body->'binding' OR op.command->'owner' IS DISTINCT FROM r.body->'owner'
                OR (op.command->'command')-'observed_at' IS DISTINCT FROM (r.body->'command')-'observed_at'
                OR (op.command#>>'{command,observed_at}')::timestamptz IS DISTINCT FROM r.captured_at
                OR NOT EXISTS(SELECT 1 FROM life_financial.operations arm WHERE arm.account_id=p_account
                    AND arm.operation_id='arm:'||(r.body#>>'{command,prepare_operation_id}')
                    AND arm.command->>'kind'='SPOT_QUOTE_ARM'
                    AND arm.receipt->>'wire_sha256'=r.body->>'request_sha256') THEN RETURN false; END IF;
        END IF;
    END LOOP;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.spot_capture_ack_ingress(p_account uuid,p_body jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE at timestamptz:=clock_timestamp(); item life_financial.spot_ack_ingress; key text; body jsonb; cmd jsonb;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR pg_is_in_recovery() OR current_setting('synchronous_commit')<>'on'
        OR current_setting('fsync')<>'on' OR current_setting('full_page_writes')<>'on'
        OR jsonb_typeof(p_body) IS DISTINCT FROM 'object' OR octet_length(p_body::text)>1048000
        OR NOT p_body ?& ARRAY['binding','owner','request_sha256','command'] OR (SELECT count(*) FROM jsonb_object_keys(p_body))<>4
        OR jsonb_typeof(p_body->'request_sha256') IS DISTINCT FROM 'string' OR p_body->>'request_sha256' !~ '^[0-9a-f]{64}$' OR jsonb_typeof(p_body->'owner') IS DISTINCT FROM 'object'
        OR jsonb_typeof(p_body->'binding') IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'LIFE_ACK_INGRESS_CAPTURE_INVALID' USING ERRCODE='P0001'; END IF;
    cmd:=p_body->'command';
    IF jsonb_typeof(cmd) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(cmd))<>9
        OR NOT cmd ?& ARRAY['operation_id','prepare_operation_id','intent_id','client_order_id','market','side','session_id','epoch','exchange_order_id']
        OR EXISTS(SELECT 1 FROM jsonb_each(cmd) AS fields(field_name,field_value) WHERE field_name<>'epoch' AND jsonb_typeof(field_value)<>'string')
        OR cmd->>'market' IS DISTINCT FROM 'LIFE-USDT' OR cmd->>'side' NOT IN ('BUY','SELL')
        OR cmd->>'exchange_order_id' !~ '^[0-9]{1,128}$' OR cmd->>'client_order_id' !~ '^[A-Za-z0-9]{1,32}$'
        OR jsonb_typeof(cmd->'epoch') IS DISTINCT FROM 'number' OR cmd->>'epoch' !~ '^[1-9][0-9]*$'
        OR length(cmd->>'operation_id')>96 THEN RAISE EXCEPTION 'LIFE_ACK_INGRESS_CAPTURE_INVALID' USING ERRCODE='P0001'; END IF;
    PERFORM (cmd->>'operation_id')::life_financial.identity; PERFORM (cmd->>'prepare_operation_id')::life_financial.identity;
    PERFORM (cmd->>'intent_id')::life_financial.identity; PERFORM (cmd->>'session_id')::life_financial.identity;
    PERFORM (cmd->>'epoch')::bigint;
    -- Account serialization, without a financial/owner-clock guard: received
    -- untrusted facts must survive HALT, expiry and inconsistent money state.
    -- This append cannot spend, activate, arm or release any reservation.
    PERFORM 1 FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    key:='rawack:'||encode(sha256(convert_to(cmd->>'operation_id','UTF8')),'hex');
    SELECT * INTO item FROM life_financial.spot_ack_ingress WHERE account_id=p_account AND ingress_id=key;
    IF FOUND THEN
        IF item.body-'command' IS DISTINCT FROM p_body-'command' OR (item.body->'command')-'observed_at' IS DISTINCT FROM cmd
            OR item.source_hash IS DISTINCT FROM encode(sha256(convert_to(item.body::text,'UTF8')),'hex') THEN
            RAISE EXCEPTION 'LIFE_ACK_INGRESS_IDENTITY_CONFLICT' USING ERRCODE='P0001'; END IF;
        RETURN jsonb_build_object('kind','ACK_INGRESS_CAPTURE','ingress_id',key,'body',item.body,'replayed',true,'trading_permission',false);
    END IF;
    at:=clock_timestamp();
    body:=p_body||jsonb_build_object('command',cmd||jsonb_build_object('observed_at',at));
    INSERT INTO life_financial.spot_ack_ingress VALUES(p_account,key,cmd->>'operation_id',body,
        encode(sha256(convert_to(body::text,'UTF8')),'hex'),at);
    RETURN jsonb_build_object('kind','ACK_INGRESS_CAPTURE','ingress_id',key,'body',body,'replayed',false,'trading_permission',false);
END; $$;

CREATE FUNCTION life_financial.spot_mark_ack_ingress(p_account uuid,p_ingress life_financial.identity) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE item life_financial.spot_ack_ingress; op life_financial.operations; replayed boolean;
BEGIN
    IF NOT life_financial.can_access(p_account,true) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
    PERFORM 1 FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    SELECT * INTO STRICT item FROM life_financial.spot_ack_ingress WHERE account_id=p_account AND ingress_id=p_ingress;
    SELECT * INTO op FROM life_financial.operations WHERE account_id=p_account AND operation_id=item.ack_operation_id;
    IF NOT FOUND OR op.command->>'kind' IS DISTINCT FROM 'SPOT_ACK' OR op.receipt->>'state' IS DISTINCT FROM 'ACKED'
        OR (life_financial.spot_state(p_account)->>'unverified')::boolean THEN
        RAISE EXCEPTION 'LIFE_ACK_INGRESS_APPLICATION_UNQUALIFIED' USING ERRCODE='P0001'; END IF;
    replayed:=EXISTS(SELECT 1 FROM life_financial.spot_ack_ingress_receipts WHERE account_id=p_account AND ingress_id=p_ingress);
    IF NOT replayed THEN
        INSERT INTO life_financial.spot_ack_ingress_receipts VALUES(p_account,p_ingress,item.ack_operation_id,item.source_hash);
    END IF;
    IF NOT life_financial.spot_ack_ingress_verified(p_account) THEN
        RAISE EXCEPTION 'LIFE_ACK_INGRESS_APPLICATION_UNQUALIFIED' USING ERRCODE='P0001'; END IF;
    RETURN jsonb_build_object('kind','ACK_INGRESS_APPLIED','ingress_id',p_ingress,'body',item.body,'replayed',replayed,'trading_permission',false);
END; $$;

CREATE FUNCTION life_financial.spot_read_ack_ingress(p_account uuid,p_ingress life_financial.identity) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE item life_financial.spot_ack_ingress;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
    IF NOT life_financial.spot_ack_ingress_verified(p_account) THEN
        RAISE EXCEPTION 'LIFE_ACK_INGRESS_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO item FROM life_financial.spot_ack_ingress WHERE account_id=p_account AND ingress_id=p_ingress;
    IF NOT FOUND THEN RETURN NULL; END IF;
    RETURN jsonb_build_object('kind','ACK_INGRESS_CAPTURE','ingress_id',p_ingress,'body',item.body,'replayed',true,'trading_permission',false);
END; $$;

CREATE OR REPLACE FUNCTION life_financial.guard_account(p_account uuid, p_binding jsonb, p_owner jsonb, p_version bigint)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts; s life_financial.sessions; actual jsonb; at timestamptz;
BEGIN
    IF p_binding IS NULL OR p_owner IS NULL OR p_version IS NULL
        OR NOT life_financial.can_access(p_account,true) OR pg_is_in_recovery() OR current_setting('transaction_read_only')<>'off'
        OR current_setting('synchronous_commit')<>'on' OR current_setting('fsync')<>'on' OR current_setting('full_page_writes')<>'on' THEN
        RAISE EXCEPTION 'LIFE_PRIMARY_DURABILITY_OR_SCOPE_INVALID' USING ERRCODE='P0001';
    END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    at := clock_timestamp(); -- after the account-row wait, not transaction start
    actual := jsonb_build_object('environment',a.environment,'exchange',a.exchange,'account_uid',a.account_uid,
        'risk_pool',a.risk_pool,'lineage',a.lineage,'schema_version',a.schema_version,'policy_hash',a.policy_hash,'config_hash',a.config_hash);
    SELECT * INTO STRICT s FROM life_financial.sessions WHERE account_id=a.account_id AND session_id=a.current_session;
    IF p_binding<>actual OR jsonb_typeof(p_owner)<>'object' OR (SELECT count(*) FROM jsonb_object_keys(p_owner))<>6
        OR a.owner_process IS DISTINCT FROM (p_owner->>'process_id')::uuid
        OR a.owner_generation IS DISTINCT FROM (p_owner->>'generation')::uuid
        OR a.owner_epoch IS DISTINCT FROM (p_owner->>'epoch')::bigint
        OR a.current_session IS DISTINCT FROM (p_owner->>'session_id')::uuid
        OR a.owner_since IS DISTINCT FROM (p_owner->>'valid_from')::timestamptz
        OR a.owner_until IS DISTINCT FROM (p_owner->>'expires_at')::timestamptz
        OR a.mode<>'ACTIVE' OR a.halted OR at<a.owner_since OR at>=a.owner_until
        OR at<s.started_at OR at>=s.expires_at OR a.owner_until>s.expires_at OR s.epoch<>a.owner_epoch
        OR NOT EXISTS (SELECT 1 FROM life_financial.configurations WHERE account_id=a.account_id AND config_version=s.config_version
            AND config_hash=a.config_hash AND policy_hash=a.policy_hash) THEN
        RAISE EXCEPTION 'LIFE_OWNER_OR_BINDING_INVALID' USING ERRCODE='P0001';
    END IF;
    IF a.version<>p_version THEN RAISE EXCEPTION 'LIFE_EXPECTED_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
    IF NOT life_financial.spot_ack_ingress_verified(p_account) OR EXISTS(
        SELECT 1 FROM life_financial.spot_ack_ingress r WHERE r.account_id=p_account AND NOT EXISTS(
            SELECT 1 FROM life_financial.spot_ack_ingress_receipts d WHERE d.account_id=p_account AND d.ingress_id=r.ingress_id)) THEN
        RAISE EXCEPTION 'LIFE_ACK_INGRESS_RECOVERY_REQUIRED' USING ERRCODE='P0001'; END IF;
END; $$;


CREATE OR REPLACE FUNCTION life_financial.spot_state(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE body jsonb; verified boolean; pending boolean; ingress_verified boolean; ingress_pending boolean;
BEGIN
    body:=life_financial.spot_state_v15(p_account)-'digest'-'observed_at';
    verified:=life_financial.recorder_executor_integrity(p_account);
    pending:=EXISTS(SELECT 1 FROM life_financial.recorder_outbox o WHERE o.account_id=p_account
        AND NOT EXISTS(SELECT 1 FROM life_financial.projection_receipts r WHERE r.account_id=p_account AND r.projection_id=o.projection_id));
    body:=body||jsonb_build_object('recorder_verified',verified,'recorder_pending',pending,
        'financial_recovery_required',(body->>'financial_recovery_required')::boolean OR NOT verified OR pending,
        'recovery_required',(body->>'recovery_required')::boolean OR NOT verified OR pending,
        'unverified',(body->>'unverified')::boolean OR NOT verified);
    ingress_verified:=life_financial.spot_ack_ingress_verified(p_account);
    ingress_pending:=EXISTS(SELECT 1 FROM life_financial.spot_ack_ingress r WHERE r.account_id=p_account AND NOT EXISTS(
        SELECT 1 FROM life_financial.spot_ack_ingress_receipts d WHERE d.account_id=p_account AND d.ingress_id=r.ingress_id));
    body:=body||jsonb_build_object('ack_ingress_verified',ingress_verified,'ack_ingress_pending',ingress_pending,
        'ack_ingress_version',(SELECT count(*) FROM life_financial.spot_ack_ingress WHERE account_id=p_account)
            +(SELECT count(*) FROM life_financial.spot_ack_ingress_receipts WHERE account_id=p_account),
        'financial_recovery_required',(body->>'financial_recovery_required')::boolean OR NOT ingress_verified OR ingress_pending,
        'recovery_required',(body->>'recovery_required')::boolean OR NOT ingress_verified OR ingress_pending,
        'unverified',(body->>'unverified')::boolean OR NOT ingress_verified);
    RETURN body||jsonb_build_object('digest',encode(sha256(convert_to(body::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
END; $$;


CREATE FUNCTION life_financial.spot_ack_ingress_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF NOT life_financial.spot_ack_ingress_verified(NEW.account_id) THEN
        RAISE EXCEPTION 'LIFE_ACK_INGRESS_UNVERIFIED_AT_COMMIT' USING ERRCODE='P0001'; END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER ack_ingress_delivery_at_commit AFTER INSERT ON life_financial.spot_ack_ingress_receipts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.spot_ack_ingress_commit_guard();
CREATE CONSTRAINT TRIGGER ack_ingress_financial_at_commit AFTER UPDATE ON life_financial.accounts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.spot_ack_ingress_commit_guard();
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
