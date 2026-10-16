-- Durable original financial command intake. Untrusted; no money or send grant.
CREATE TABLE life_financial.spot_financial_ingress (
    account_id uuid NOT NULL REFERENCES life_financial.accounts,
    ingress_id life_financial.identity NOT NULL, operation_id life_financial.identity NOT NULL,
    kind text NOT NULL CHECK(kind IN ('SPOT_FILL','SPOT_CASHFLOW')),
    body jsonb NOT NULL CHECK(jsonb_typeof(body)='object' AND octet_length(body::text)<=1048576),
    source_hash life_financial.digest NOT NULL,
    captured_at timestamptz NOT NULL CHECK(isfinite(captured_at)),
    capture_sequence bigint NOT NULL CHECK(capture_sequence>0),
    UNIQUE(account_id,capture_sequence),
    PRIMARY KEY(account_id,ingress_id),UNIQUE(account_id,kind,operation_id)
);
CREATE TABLE life_financial.spot_financial_ingress_receipts (
    account_id uuid NOT NULL,ingress_id life_financial.identity NOT NULL,
    financial_operation_id life_financial.identity NOT NULL,source_hash life_financial.digest NOT NULL,
    PRIMARY KEY(account_id,ingress_id),
    FOREIGN KEY(account_id,ingress_id) REFERENCES life_financial.spot_financial_ingress,
    FOREIGN KEY(account_id,financial_operation_id) REFERENCES life_financial.operations
);
DO $$ DECLARE name text; BEGIN
    FOREACH name IN ARRAY ARRAY['spot_financial_ingress','spot_financial_ingress_receipts'] LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',name);
        EXECUTE format('CREATE POLICY scoped_read ON life_financial.%I FOR SELECT USING(life_financial.can_access(account_id))',name);
        EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',name);
    END LOOP;
END; $$;

CREATE TABLE life_financial.spot_ingress_progress (
    account_id uuid PRIMARY KEY REFERENCES life_financial.accounts,
    ack_captured bigint NOT NULL CHECK(ack_captured>=0),ack_delivered bigint NOT NULL CHECK(ack_delivered>=0),
    financial_captured bigint NOT NULL CHECK(financial_captured>=0),financial_delivered bigint NOT NULL CHECK(financial_delivered>=0),
    version bigint NOT NULL CHECK(version=ack_captured+ack_delivered+financial_captured+financial_delivered)
);
ALTER TABLE life_financial.spot_ingress_progress ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped_read ON life_financial.spot_ingress_progress FOR SELECT USING(life_financial.can_access(account_id));
CREATE TRIGGER retained_identity BEFORE DELETE ON life_financial.spot_ingress_progress
    FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record();
CREATE FUNCTION life_financial.spot_ingress_progress_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
    IF NEW.account_id<>OLD.account_id OR NEW.version<>OLD.version+1 OR NEW.ack_captured<OLD.ack_captured
        OR NEW.ack_delivered<OLD.ack_delivered OR NEW.financial_captured<OLD.financial_captured OR NEW.financial_delivered<OLD.financial_delivered THEN
        RAISE EXCEPTION 'LIFE_INGRESS_PROGRESS_REGRESSION' USING ERRCODE='P0001'; END IF;
    RETURN NEW;
END; $$;
CREATE TRIGGER progress_guard BEFORE UPDATE ON life_financial.spot_ingress_progress
    FOR EACH ROW EXECUTE FUNCTION life_financial.spot_ingress_progress_guard();

-- Establish only diagnostic counters from the prior trusted intake baseline.
-- This imports no financial fact, amount, policy, owner or activation grant.
DO $$ DECLARE id uuid; n bigint; d bigint; BEGIN
    FOR id IN SELECT DISTINCT account_id FROM life_financial.spot_ack_ingress LOOP
        IF NOT life_financial.spot_ack_ingress_verified(id) THEN
            RAISE EXCEPTION 'LIFE_INGRESS_BASELINE_UNVERIFIED' USING ERRCODE='P0001'; END IF;
        SELECT count(*) INTO n FROM life_financial.spot_ack_ingress WHERE account_id=id;
        SELECT count(*) INTO d FROM life_financial.spot_ack_ingress_receipts WHERE account_id=id;
        INSERT INTO life_financial.spot_ingress_progress VALUES(id,n,d,0,0,n+d);
    END LOOP;
END; $$;

CREATE FUNCTION life_financial.spot_ingress_counts_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r life_financial.spot_ingress_progress; a bigint; d bigint; f bigint; g bigint;
BEGIN
    SELECT count(*) INTO a FROM life_financial.spot_ack_ingress WHERE account_id=p_account;
    SELECT count(*) INTO d FROM life_financial.spot_ack_ingress_receipts WHERE account_id=p_account;
    SELECT count(*) INTO f FROM life_financial.spot_financial_ingress WHERE account_id=p_account;
    SELECT count(*) INTO g FROM life_financial.spot_financial_ingress_receipts WHERE account_id=p_account;
    SELECT * INTO r FROM life_financial.spot_ingress_progress WHERE account_id=p_account;
    IF NOT FOUND THEN RETURN a+d+f+g=0; END IF;
    RETURN r.ack_captured=a AND r.ack_delivered=d AND r.financial_captured=f AND r.financial_delivered=g AND r.version=a+d+f+g;
END; $$;

CREATE FUNCTION life_financial.spot_ingress_increment(p_account uuid,p_kind text,p_stage text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result bigint;
BEGIN
    IF p_kind IS NULL OR p_kind NOT IN ('ACK','FINANCIAL') OR p_stage IS NULL OR p_stage NOT IN ('CAPTURE','DELIVERY') THEN
        RAISE EXCEPTION 'LIFE_INGRESS_PROGRESS_INVALID' USING ERRCODE='P0001'; END IF;
    PERFORM 1 FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    INSERT INTO life_financial.spot_ingress_progress VALUES(p_account,0,0,0,0,0) ON CONFLICT DO NOTHING;
    UPDATE life_financial.spot_ingress_progress SET
        ack_captured=ack_captured+CASE WHEN p_kind='ACK' AND p_stage='CAPTURE' THEN 1 ELSE 0 END,
        ack_delivered=ack_delivered+CASE WHEN p_kind='ACK' AND p_stage='DELIVERY' THEN 1 ELSE 0 END,
        financial_captured=financial_captured+CASE WHEN p_kind='FINANCIAL' AND p_stage='CAPTURE' THEN 1 ELSE 0 END,
        financial_delivered=financial_delivered+CASE WHEN p_kind='FINANCIAL' AND p_stage='DELIVERY' THEN 1 ELSE 0 END,
        version=version+1 WHERE account_id=p_account RETURNING version INTO result;
    RETURN result;
END; $$;

CREATE FUNCTION life_financial.spot_ingress_counts_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF NOT life_financial.spot_ingress_counts_verified(NEW.account_id) THEN
        RAISE EXCEPTION 'LIFE_INGRESS_COUNTS_UNVERIFIED_AT_COMMIT' USING ERRCODE='P0001'; END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER ingress_counts_at_commit AFTER INSERT OR UPDATE ON life_financial.spot_ingress_progress
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.spot_ingress_counts_commit_guard();

CREATE FUNCTION life_financial.spot_financial_ingress_matches(p_account uuid,p_body jsonb,p_operation text) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE op life_financial.operations; raw jsonb:=p_body->'command'; actual jsonb; qty numeric; loss numeric;
BEGIN
    SELECT * INTO op FROM life_financial.operations WHERE account_id=p_account AND operation_id=p_operation;
    IF NOT FOUND OR op.command->>'kind' IS DISTINCT FROM p_body->>'kind'
        OR op.command->'binding' IS DISTINCT FROM p_body->'binding' OR op.command->'owner' IS DISTINCT FROM p_body->'owner'
        OR op.receipt->>'kind' IS DISTINCT FROM p_body->>'kind' OR op.receipt->>'operation_id' IS DISTINCT FROM op.operation_id
        OR op.receipt->>'state' IS DISTINCT FROM 'APPLIED' OR op.receipt->'replayed' IS DISTINCT FROM 'false'::jsonb
        OR op.receipt->'trading_permission' IS DISTINCT FROM 'false'::jsonb
        OR (op.receipt->>'version')::bigint IS DISTINCT FROM op.committed_version THEN RETURN false; END IF;
    actual:=op.command->'command';
    IF p_body->>'kind'='SPOT_FILL' THEN
        IF actual-ARRAY['operation_id','cumulative_base_life','fills','observed_at']
                IS DISTINCT FROM raw-ARRAY['operation_id','cumulative_base_life','fills','observed_at']
            OR op.receipt->>'intent_id' IS DISTINCT FROM actual->>'intent_id'
            OR jsonb_typeof(raw->'fills') IS DISTINCT FROM 'array' OR jsonb_array_length(raw->'fills')=0
            OR (SELECT count(*)<>count(DISTINCT row->>'trade_id') FROM jsonb_array_elements(raw->'fills') row)
            OR NOT ((actual->'fills') @> (raw->'fills')) THEN RETURN false; END IF;
        SELECT sum((row->>'quantity_base_life')::numeric) INTO qty FROM jsonb_array_elements(raw->'fills') row;
        IF qty IS DISTINCT FROM (raw->>'cumulative_base_life')::numeric
            OR (op.receipt->>'cumulative_base_life')::numeric<qty THEN RETURN false; END IF;
        IF EXISTS(SELECT 1 FROM jsonb_array_elements(actual->'fills') row WHERE NOT EXISTS(
            SELECT 1 FROM life_financial.spot_fill_facts f WHERE f.account_id=p_account AND f.trade_id=row->>'trade_id'
                AND f.intent_id=actual->>'intent_id' AND f.exchange_order_id=actual->>'exchange_order_id' AND f.observation=row)) THEN RETURN false; END IF;
        SELECT sum((row->>'quantity_base_life')::numeric) INTO qty FROM jsonb_array_elements(actual->'fills') row;
        SELECT sum(f.execution_loss) INTO loss FROM life_financial.spot_fill_facts f WHERE f.account_id=p_account
            AND f.intent_id=actual->>'intent_id' AND EXISTS(SELECT 1 FROM jsonb_array_elements(actual->'fills') row WHERE row->>'trade_id'=f.trade_id);
        RETURN qty=(actual->>'cumulative_base_life')::numeric AND qty=(op.receipt->>'cumulative_base_life')::numeric
            AND loss=(op.receipt->>'execution_loss_usdt')::numeric;
    ELSIF p_body->>'kind'='SPOT_CASHFLOW' THEN
        RETURN actual-ARRAY['operation_id','observed_at']=raw-ARRAY['operation_id','observed_at']
            AND op.receipt->>'bill_id'=raw->>'bill_id'
            AND (op.receipt->>'value_usdt')::numeric=(raw->>'signed_amount')::numeric*(raw->>'independent_price_usdt')::numeric
            AND EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts f WHERE f.account_id=p_account AND f.bill_id=raw->>'bill_id'
                AND f.state='APPLIED' AND f.command-ARRAY['operation_id','observed_at']=raw-ARRAY['operation_id','observed_at']);
    END IF;
    RETURN false;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.spot_financial_ingress_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r life_financial.spot_financial_ingress; d life_financial.spot_financial_ingress_receipts;
BEGIN
    FOR r IN SELECT * FROM life_financial.spot_financial_ingress WHERE account_id=p_account LOOP
        IF r.kind IS DISTINCT FROM r.body->>'kind' OR r.operation_id IS DISTINCT FROM r.body#>>'{command,operation_id}'
            OR r.source_hash IS DISTINCT FROM encode(sha256(convert_to(jsonb_build_object('body',r.body,'captured_at_epoch',extract(epoch FROM r.captured_at)::text,'capture_sequence',r.capture_sequence)::text,'UTF8')),'hex')
            OR r.ingress_id IS DISTINCT FROM 'rawfinancial:'||encode(sha256(convert_to(r.kind||':'||r.operation_id,'UTF8')),'hex') THEN RETURN false; END IF;
        SELECT * INTO d FROM life_financial.spot_financial_ingress_receipts WHERE account_id=p_account AND ingress_id=r.ingress_id;
        IF FOUND AND (d.source_hash<>r.source_hash OR NOT life_financial.spot_financial_ingress_matches(p_account,r.body,d.financial_operation_id)) THEN RETURN false; END IF;
    END LOOP;
    RETURN life_financial.spot_ingress_counts_verified(p_account);
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.spot_capture_financial_ingress(p_account uuid,p_body jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r life_financial.spot_financial_ingress; key text; kind text; operation text; replayed boolean; at timestamptz; seq bigint;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR pg_is_in_recovery() OR current_setting('synchronous_commit')<>'on'
        OR current_setting('fsync')<>'on' OR current_setting('full_page_writes')<>'on'
        OR jsonb_typeof(p_body) IS DISTINCT FROM 'object' OR octet_length(p_body::text)>1048576
        OR NOT p_body ?& ARRAY['kind','binding','owner','command'] OR (SELECT count(*) FROM jsonb_object_keys(p_body))<>4
        OR jsonb_typeof(p_body->'binding') IS DISTINCT FROM 'object' OR jsonb_typeof(p_body->'owner') IS DISTINCT FROM 'object'
        OR jsonb_typeof(p_body->'command') IS DISTINCT FROM 'object' OR jsonb_typeof(p_body->'kind') IS DISTINCT FROM 'string' OR p_body->>'kind' NOT IN ('SPOT_FILL','SPOT_CASHFLOW')
        OR jsonb_typeof(p_body#>'{command,operation_id}') IS DISTINCT FROM 'string' OR length(p_body#>>'{command,operation_id}')>96 THEN
        RAISE EXCEPTION 'LIFE_FINANCIAL_INGRESS_CAPTURE_INVALID' USING ERRCODE='P0001'; END IF;
    kind:=p_body->>'kind';operation:=(p_body#>>'{command,operation_id}')::life_financial.identity;
    PERFORM 1 FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    key:='rawfinancial:'||encode(sha256(convert_to(kind||':'||operation,'UTF8')),'hex');
    SELECT * INTO r FROM life_financial.spot_financial_ingress WHERE account_id=p_account AND ingress_id=key;
    replayed:=FOUND;
    IF replayed THEN
        IF r.body IS DISTINCT FROM p_body OR r.source_hash IS DISTINCT FROM encode(sha256(convert_to(jsonb_build_object('body',r.body,'captured_at_epoch',extract(epoch FROM r.captured_at)::text,'capture_sequence',r.capture_sequence)::text,'UTF8')),'hex') THEN
            RAISE EXCEPTION 'LIFE_FINANCIAL_INGRESS_IDENTITY_CONFLICT' USING ERRCODE='P0001'; END IF;
    ELSE
        seq:=life_financial.spot_ingress_increment(p_account,'FINANCIAL','CAPTURE'); at:=clock_timestamp();
        INSERT INTO life_financial.spot_financial_ingress VALUES(p_account,key,operation,kind,p_body,
            encode(sha256(convert_to(jsonb_build_object('body',p_body,'captured_at_epoch',extract(epoch FROM at)::text,'capture_sequence',seq)::text,'UTF8')),'hex'),at,seq);
        SELECT * INTO STRICT r FROM life_financial.spot_financial_ingress WHERE account_id=p_account AND ingress_id=key;
    END IF;
    RETURN jsonb_build_object('kind','FINANCIAL_INGRESS_CAPTURE','ingress_id',key,'body',r.body,'captured_at',r.captured_at,'capture_sequence',r.capture_sequence,
        'applied_operation_id',(SELECT financial_operation_id FROM life_financial.spot_financial_ingress_receipts WHERE account_id=p_account AND ingress_id=key),
        'replayed',replayed,'trading_permission',false);
END; $$;

CREATE FUNCTION life_financial.spot_mark_financial_ingress(p_account uuid,p_ingress life_financial.identity,p_operation life_financial.identity) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r life_financial.spot_financial_ingress; d life_financial.spot_financial_ingress_receipts; replayed boolean;
BEGIN
    IF NOT life_financial.can_access(p_account,true) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
    PERFORM 1 FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    SELECT * INTO STRICT r FROM life_financial.spot_financial_ingress WHERE account_id=p_account AND ingress_id=p_ingress;
    IF (life_financial.spot_state(p_account)->>'unverified')::boolean
        OR NOT life_financial.spot_financial_ingress_matches(p_account,r.body,p_operation) THEN
        RAISE EXCEPTION 'LIFE_FINANCIAL_INGRESS_APPLICATION_UNQUALIFIED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO d FROM life_financial.spot_financial_ingress_receipts WHERE account_id=p_account AND ingress_id=p_ingress;
    replayed:=FOUND;
    IF replayed THEN
        IF d.financial_operation_id<>p_operation THEN RAISE EXCEPTION 'LIFE_FINANCIAL_INGRESS_DELIVERY_CONFLICT' USING ERRCODE='P0001'; END IF;
    ELSE
        PERFORM life_financial.spot_ingress_increment(p_account,'FINANCIAL','DELIVERY');
        INSERT INTO life_financial.spot_financial_ingress_receipts VALUES(p_account,p_ingress,p_operation,r.source_hash);
    END IF;
    IF NOT life_financial.spot_financial_ingress_verified(p_account) THEN RAISE EXCEPTION 'LIFE_FINANCIAL_INGRESS_APPLICATION_UNQUALIFIED' USING ERRCODE='P0001'; END IF;
    RETURN jsonb_build_object('kind','FINANCIAL_INGRESS_APPLIED','ingress_id',p_ingress,'body',r.body,'captured_at',r.captured_at,'capture_sequence',r.capture_sequence,
        'applied_operation_id',p_operation,'replayed',replayed,'trading_permission',false);
END; $$;

CREATE FUNCTION life_financial.spot_read_financial_ingress(p_account uuid,p_ingress life_financial.identity) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r life_financial.spot_financial_ingress;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
    IF NOT life_financial.spot_financial_ingress_verified(p_account) THEN RAISE EXCEPTION 'LIFE_FINANCIAL_INGRESS_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO r FROM life_financial.spot_financial_ingress WHERE account_id=p_account AND ingress_id=p_ingress;
    IF NOT FOUND THEN RETURN NULL; END IF;
    RETURN jsonb_build_object('kind','FINANCIAL_INGRESS_CAPTURE','ingress_id',p_ingress,'body',r.body,'captured_at',r.captured_at,'capture_sequence',r.capture_sequence,
        'applied_operation_id',(SELECT financial_operation_id FROM life_financial.spot_financial_ingress_receipts WHERE account_id=p_account AND ingress_id=p_ingress),
        'replayed',true,'trading_permission',false);
END; $$;

CREATE FUNCTION life_financial.spot_pending_financial_ingress(p_account uuid,p_rows integer,p_bytes integer) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE n bigint; size bigint;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
    IF p_rows IS NULL OR p_rows NOT BETWEEN 1 AND 4096 OR p_bytes IS NULL OR p_bytes NOT BETWEEN 1 AND 16777216 THEN
        RAISE EXCEPTION 'LIFE_FINANCIAL_INGRESS_READ_BOUND_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT count(*),COALESCE(sum(x.bytes),0) INTO n,size FROM (
        SELECT octet_length(r.body::text)::bigint+512 AS bytes FROM life_financial.spot_financial_ingress r
        WHERE r.account_id=p_account AND NOT EXISTS(SELECT 1 FROM life_financial.spot_financial_ingress_receipts d
            WHERE d.account_id=p_account AND d.ingress_id=r.ingress_id) LIMIT p_rows+1) x;
    IF n>p_rows OR size>p_bytes THEN RAISE EXCEPTION 'LIFE_FINANCIAL_INGRESS_READ_BOUND' USING ERRCODE='P0001'; END IF;
    IF NOT life_financial.spot_financial_ingress_verified(p_account) THEN RAISE EXCEPTION 'LIFE_FINANCIAL_INGRESS_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    RETURN COALESCE((SELECT jsonb_agg(jsonb_build_object('kind','FINANCIAL_INGRESS_CAPTURE','ingress_id',r.ingress_id,
        'body',r.body,'captured_at',r.captured_at,'capture_sequence',r.capture_sequence,'applied_operation_id',NULL,'replayed',true,'trading_permission',false) ORDER BY r.capture_sequence)
        FROM life_financial.spot_financial_ingress r WHERE r.account_id=p_account AND NOT EXISTS(
            SELECT 1 FROM life_financial.spot_financial_ingress_receipts d WHERE d.account_id=p_account AND d.ingress_id=r.ingress_id)),'[]'::jsonb);
END; $$;

CREATE OR REPLACE FUNCTION life_financial.spot_ack_ingress_verified(p_account uuid) RETURNS boolean
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
    RETURN life_financial.spot_ingress_counts_verified(p_account);
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE OR REPLACE FUNCTION life_financial.spot_capture_ack_ingress(p_account uuid,p_body jsonb) RETURNS jsonb
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
    PERFORM life_financial.spot_ingress_increment(p_account,'ACK','CAPTURE');
    INSERT INTO life_financial.spot_ack_ingress VALUES(p_account,key,cmd->>'operation_id',body,
        encode(sha256(convert_to(body::text,'UTF8')),'hex'),at);
    RETURN jsonb_build_object('kind','ACK_INGRESS_CAPTURE','ingress_id',key,'body',body,'replayed',false,'trading_permission',false);
END; $$;

CREATE OR REPLACE FUNCTION life_financial.spot_mark_ack_ingress(p_account uuid,p_ingress life_financial.identity) RETURNS jsonb
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
        PERFORM life_financial.spot_ingress_increment(p_account,'ACK','DELIVERY');
        INSERT INTO life_financial.spot_ack_ingress_receipts VALUES(p_account,p_ingress,item.ack_operation_id,item.source_hash);
    END IF;
    IF NOT life_financial.spot_ack_ingress_verified(p_account) THEN
        RAISE EXCEPTION 'LIFE_ACK_INGRESS_APPLICATION_UNQUALIFIED' USING ERRCODE='P0001'; END IF;
    RETURN jsonb_build_object('kind','ACK_INGRESS_APPLIED','ingress_id',p_ingress,'body',item.body,'replayed',replayed,'trading_permission',false);
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
    IF NOT life_financial.spot_financial_ingress_verified(p_account) OR EXISTS(
        SELECT 1 FROM life_financial.spot_financial_ingress r WHERE r.account_id=p_account AND NOT EXISTS(
            SELECT 1 FROM life_financial.spot_financial_ingress_receipts d WHERE d.account_id=p_account AND d.ingress_id=r.ingress_id)) THEN
        RAISE EXCEPTION 'LIFE_FINANCIAL_INGRESS_RECOVERY_REQUIRED' USING ERRCODE='P0001'; END IF;
    IF NOT life_financial.spot_ack_ingress_verified(p_account) OR EXISTS(
        SELECT 1 FROM life_financial.spot_ack_ingress r WHERE r.account_id=p_account AND NOT EXISTS(
            SELECT 1 FROM life_financial.spot_ack_ingress_receipts d WHERE d.account_id=p_account AND d.ingress_id=r.ingress_id)) THEN
        RAISE EXCEPTION 'LIFE_ACK_INGRESS_RECOVERY_REQUIRED' USING ERRCODE='P0001'; END IF;
END; $$;


CREATE OR REPLACE FUNCTION life_financial.spot_state(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE body jsonb; verified boolean; pending boolean; ingress_verified boolean; ingress_pending boolean; financial_verified boolean; financial_pending boolean;
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
    financial_verified:=life_financial.spot_financial_ingress_verified(p_account);
    financial_pending:=EXISTS(SELECT 1 FROM life_financial.spot_financial_ingress r WHERE r.account_id=p_account AND NOT EXISTS(
        SELECT 1 FROM life_financial.spot_financial_ingress_receipts d WHERE d.account_id=p_account AND d.ingress_id=r.ingress_id));
    body:=body||jsonb_build_object('financial_ingress_verified',financial_verified,'financial_ingress_pending',financial_pending,
        'ingress_progress',(SELECT to_jsonb(g) FROM life_financial.spot_ingress_progress g WHERE g.account_id=p_account),
        'financial_recovery_required',(body->>'financial_recovery_required')::boolean OR NOT financial_verified OR financial_pending,
        'recovery_required',(body->>'recovery_required')::boolean OR NOT financial_verified OR financial_pending,
        'unverified',(body->>'unverified')::boolean OR NOT financial_verified);
    RETURN body||jsonb_build_object('digest',encode(sha256(convert_to(body::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
END; $$;



-- One bounded primary snapshot for provider reconciliation. Read only; no grant.
CREATE OR REPLACE FUNCTION life_financial.spot_observation_context(p_account uuid,p_rows integer,p_bytes integer) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE view jsonb; result jsonb; n bigint; bytes bigint;
    p life_financial.spot_capital_policies; f life_financial.spot_fill_policies;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
    IF p_rows IS NULL OR p_rows NOT BETWEEN 1 AND 4096 OR p_bytes IS NULL OR p_bytes NOT BETWEEN 1 AND 16777216 THEN
        RAISE EXCEPTION 'LIFE_SPOT_CONTEXT_READ_BOUND_INVALID' USING ERRCODE='P0001'; END IF;
    -- Materialize only bounded identities/sizes before constructing payloads.
    SELECT count(*),COALESCE(sum(x.bytes),0) INTO n,bytes FROM (
            SELECT * FROM (
                SELECT 'COMMAND'::text AS kind,operation_id::text AS id,octet_length(request::text)::bigint+256 AS bytes
                    FROM life_financial.spot_preparations WHERE account_id=p_account
                UNION ALL SELECT 'FILL',trade_id,octet_length(observation::text)::bigint+512
                    FROM life_financial.spot_fill_facts WHERE account_id=p_account
                UNION ALL SELECT 'CASHFLOW',bill_id,octet_length(command::text)::bigint+256
                    FROM life_financial.spot_cashflow_facts WHERE account_id=p_account
                UNION ALL SELECT 'APPROVAL',bill_id,512::bigint FROM life_financial.bill_approvals WHERE account_id=p_account
                UNION ALL SELECT 'ACK',r.ingress_id,octet_length(r.body::text)::bigint+256 FROM life_financial.spot_ack_ingress r
                    WHERE r.account_id=p_account AND NOT EXISTS(SELECT 1 FROM life_financial.spot_ack_ingress_receipts d
                        WHERE d.account_id=p_account AND d.ingress_id=r.ingress_id)
                UNION ALL SELECT 'FINANCIAL_PENDING',r.ingress_id,octet_length(r.body::text)::bigint+768
                    FROM life_financial.spot_financial_ingress r WHERE r.account_id=p_account AND NOT EXISTS(
                        SELECT 1 FROM life_financial.spot_financial_ingress_receipts d WHERE d.account_id=p_account AND d.ingress_id=r.ingress_id)
                UNION ALL SELECT 'POLICY',policy_hash,1024::bigint FROM life_financial.spot_capital_policies WHERE account_id=p_account
                UNION ALL SELECT 'FILL_POLICY',policy_hash,128::bigint FROM life_financial.spot_fill_policies
                    WHERE account_id=p_account AND policy_hash=(SELECT policy_hash FROM life_financial.accounts WHERE account_id=p_account)
            ) candidates LIMIT p_rows+1
        ) x;
    IF n>p_rows OR bytes>p_bytes THEN RAISE EXCEPTION 'LIFE_SPOT_CONTEXT_READ_BOUND' USING ERRCODE='P0001'; END IF;
    view:=life_financial.spot_state(p_account);
    IF (view->>'unverified')::boolean THEN RAISE EXCEPTION 'LIFE_SPOT_CONTEXT_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    -- A known pending financial command must not disappear into an empty DTO.
    -- Its original-price, signed resolution port is a separate operation.
    IF EXISTS(SELECT 1 FROM life_financial.spot_pending_observations WHERE account_id=p_account)
        OR EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts WHERE account_id=p_account AND state<>'APPLIED') THEN
        RAISE EXCEPTION 'LIFE_SPOT_CONTEXT_PENDING_RESOLUTION_REQUIRED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO p FROM life_financial.spot_capital_policies WHERE account_id=p_account;
    IF NOT FOUND OR p.policy_hash IS DISTINCT FROM view#>>'{account,policy_hash}' THEN
        RAISE EXCEPTION 'LIFE_SPOT_CONTEXT_POLICY_REQUIRED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO f FROM life_financial.spot_fill_policies WHERE account_id=p_account AND policy_hash=p.policy_hash;
    IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_SPOT_CONTEXT_POLICY_REQUIRED' USING ERRCODE='P0001'; END IF;
    result:=jsonb_build_object('snapshot',view,
        'policy',jsonb_build_object('opening_base_life',p.opening_base::text,'opening_quote_usdt',p.opening_quote::text,
            'opening_independent_price_usdt',p.opening_price::text,'opened_at',p.opened_at,'anchor_bill_id',p.anchor_bill),
        'fill_policy',jsonb_build_object('max_reference_skew_ms',f.max_reference_skew_ms),
        'commands',COALESCE((SELECT jsonb_agg(request ORDER BY operation_id COLLATE "C")
            FROM life_financial.spot_preparations WHERE account_id=p_account),'[]'::jsonb),
        'fills',COALESCE((SELECT jsonb_agg(jsonb_build_object('prepare_operation_id',d.operation_id,'intent_id',x.intent_id,
            'exchange_order_id',x.exchange_order_id,'fill',x.observation) ORDER BY x.trade_id COLLATE "C")
            FROM life_financial.spot_fill_facts x JOIN life_financial.spot_preparations d USING(account_id,intent_id)
            WHERE x.account_id=p_account),'[]'::jsonb),
        'cashflows',COALESCE((SELECT jsonb_agg(command ORDER BY bill_id COLLATE "C")
            FROM life_financial.spot_cashflow_facts WHERE account_id=p_account),'[]'::jsonb),
        'approvals',COALESCE((SELECT jsonb_agg(jsonb_build_object('bill_id',bill_id,'currency',asset,'signed_amount',signed_amount::text,
            'transfer_at_ms',floor(extract(epoch FROM transfer_at)*1000)::bigint) ORDER BY bill_id::numeric)
            FROM life_financial.bill_approvals WHERE account_id=p_account),'[]'::jsonb),
        'financial_pending',COALESCE((SELECT jsonb_agg(r.body ORDER BY r.capture_sequence) FROM life_financial.spot_financial_ingress r
            WHERE r.account_id=p_account AND NOT EXISTS(SELECT 1 FROM life_financial.spot_financial_ingress_receipts d
                WHERE d.account_id=p_account AND d.ingress_id=r.ingress_id)),'[]'::jsonb),
        'ack_pending',COALESCE((SELECT jsonb_agg(r.body ORDER BY r.ingress_id COLLATE "C") FROM life_financial.spot_ack_ingress r
            WHERE r.account_id=p_account AND NOT EXISTS(SELECT 1 FROM life_financial.spot_ack_ingress_receipts d
                WHERE d.account_id=p_account AND d.ingress_id=r.ingress_id)),'[]'::jsonb));
    IF octet_length(result::text)>p_bytes THEN RAISE EXCEPTION 'LIFE_SPOT_CONTEXT_READ_BOUND' USING ERRCODE='P0001'; END IF;
    RETURN result;
END; $$;
REVOKE ALL ON FUNCTION life_financial.spot_observation_context(uuid,integer,integer) FROM PUBLIC;

CREATE FUNCTION life_financial.spot_financial_ingress_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF NOT life_financial.spot_financial_ingress_verified(NEW.account_id) THEN
        RAISE EXCEPTION 'LIFE_FINANCIAL_INGRESS_UNVERIFIED_AT_COMMIT' USING ERRCODE='P0001'; END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER financial_ingress_delivery_at_commit AFTER INSERT ON life_financial.spot_financial_ingress_receipts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.spot_financial_ingress_commit_guard();
CREATE CONSTRAINT TRIGGER financial_ingress_account_at_commit AFTER UPDATE ON life_financial.accounts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.spot_financial_ingress_commit_guard();
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
