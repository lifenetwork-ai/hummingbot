-- Native cumulative SWAP fills. No account/policy bootstrap, send, terminal
-- proof or shared claim release. Qualified source assembly remains separate.
CREATE TABLE life_financial.swap_fill_policies (
    account_id uuid PRIMARY KEY REFERENCES life_financial.swap_policies,
    policy_hash life_financial.digest NOT NULL,
    opening_long_base life_financial.amount NOT NULL,opening_short_base life_financial.amount NOT NULL,
    opening_long_entry life_financial.amount NOT NULL,opening_short_entry life_financial.amount NOT NULL,
    max_total_fills integer NOT NULL CHECK(max_total_fills BETWEEN 1 AND 4096),
    max_observations integer NOT NULL CHECK(max_observations BETWEEN 1 AND 4096),
    max_reference_skew_ms integer NOT NULL CHECK(max_reference_skew_ms BETWEEN 0 AND 2147483647),
    FOREIGN KEY(account_id,policy_hash) REFERENCES life_financial.policies,
    CHECK(opening_long_base=0 OR opening_long_entry>0),CHECK(opening_short_base=0 OR opening_short_entry>0)
);
CREATE TABLE life_financial.swap_fill_facts (
    account_id uuid NOT NULL REFERENCES life_financial.swap_fill_policies,trade_id life_financial.identity NOT NULL,
    intent_id life_financial.identity NOT NULL,exchange_order_id text NOT NULL CHECK(exchange_order_id ~ '^[1-9][0-9]{0,63}$'),
    observation jsonb NOT NULL,source_operation_id life_financial.identity NOT NULL,source_hash life_financial.digest NOT NULL,
    PRIMARY KEY(account_id,trade_id),FOREIGN KEY(account_id,intent_id) REFERENCES life_financial.send_attempts,
    FOREIGN KEY(account_id,source_operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX swap_fill_intent ON life_financial.swap_fill_facts(account_id,intent_id,trade_id);
CREATE TABLE life_financial.swap_fill_progress (
    account_id uuid NOT NULL,intent_id life_financial.identity NOT NULL,filled_base life_financial.amount NOT NULL,
    paid_fees_usdt life_financial.amount NOT NULL,execution_loss_usdt life_financial.amount NOT NULL,
    exchange_order_id text NOT NULL,version bigint NOT NULL CHECK(version>0),
    PRIMARY KEY(account_id,intent_id),UNIQUE(account_id,exchange_order_id),
    FOREIGN KEY(account_id,intent_id) REFERENCES life_financial.send_attempts
);
CREATE TABLE life_financial.swap_pending_fills (
    account_id uuid NOT NULL REFERENCES life_financial.accounts,operation_id life_financial.identity NOT NULL,
    intent_id life_financial.identity NOT NULL,command jsonb NOT NULL,reason life_financial.identity NOT NULL,
    source_hash life_financial.digest NOT NULL,PRIMARY KEY(account_id,operation_id),
    FOREIGN KEY(account_id,intent_id) REFERENCES life_financial.send_attempts,
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED
);
DO $$ DECLARE name text;BEGIN
    FOREACH name IN ARRAY ARRAY['swap_fill_policies','swap_fill_facts','swap_fill_progress','swap_pending_fills'] LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',name);
        EXECUTE format('CREATE POLICY scoped_read ON life_financial.%I FOR SELECT USING(life_financial.can_access(account_id))',name);
        IF name<>'swap_fill_progress' THEN
            EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',name);
        END IF;
    END LOOP;
END; $$;

CREATE FUNCTION life_financial.swap_fill_replay(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.swap_fill_policies;anchor jsonb;trades jsonb;mode text;through_ms bigint;
BEGIN
    SELECT * INTO p FROM life_financial.swap_fill_policies WHERE account_id=p_account;
    IF NOT FOUND THEN RETURN NULL;END IF;
    SELECT jsonb_build_object('long_base',p.opening_long_base::text,'short_base',p.opening_short_base::text,
        'opening_long_entry',p.opening_long_entry::text,'opening_short_entry',p.opening_short_entry::text,
        'observed_at_ms',floor(extract(epoch FROM opened_at)*1000)::bigint) INTO STRICT anchor
        FROM life_financial.spot_capital_policies WHERE account_id=p_account AND policy_hash=p.policy_hash;
    SELECT body->>'position_mode' INTO STRICT mode FROM life_financial.shared_capital_policies WHERE account_id=p_account AND policy_hash=p.policy_hash;
    SELECT COALESCE(jsonb_agg(jsonb_build_object('trade_id',f.trade_id,'intent_id',f.intent_id,'side',t.side,
        'position_action',d.permit->>'position_action','quantity_base',f.observation->>'quantity_base_life',
        'price_usdt',f.observation->>'price_usdt','fee_currency',f.observation->>'fee_currency','signed_fee',f.observation->>'signed_fee',
        'realized_pnl_quote',f.observation->>'realized_pnl_usdt','at_ms',f.observation->'fill_at_ms','sequence',f.observation->'sequence')),'[]'::jsonb),
        greatest((anchor->>'observed_at_ms')::bigint,COALESCE(max((f.observation->>'fill_at_ms')::bigint),0)) INTO trades,through_ms
        FROM life_financial.swap_fill_facts f JOIN life_financial.send_attempts t USING(account_id,intent_id)
        JOIN life_financial.swap_preparations d USING(account_id,intent_id) WHERE f.account_id=p_account;
    RETURN life_financial.swap_position_replay(anchor,trades,'[]'::jsonb,mode,through_ms,p.max_total_fills);
END; $$;

CREATE FUNCTION life_financial.swap_fill_valid(j jsonb,p life_financial.swap_fill_policies,d life_financial.swap_preparations,
    t life_financial.send_attempts,observed_ms bigint) RETURNS boolean
LANGUAGE plpgsql STABLE SET search_path=pg_catalog AS $$
DECLARE k text;q numeric;price numeric;at_ms bigint;value_ms bigint;value_observed_ms bigint;
BEGIN
    IF jsonb_typeof(j) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(j))<>12
        OR NOT j ?& ARRAY['trade_id','quantity_base_life','price_usdt','fee_currency','signed_fee','fill_at_ms',
            'independent_price_usdt','independent_value_at_ms','independent_observed_at_ms','source_kind','realized_pnl_usdt','sequence']
        OR j->>'trade_id' !~ '^[A-Za-z0-9_.-]{1,64}$' OR jsonb_typeof(j->'trade_id') IS DISTINCT FROM 'string'
        OR j->'fee_currency' IS DISTINCT FROM '"USDT"'::jsonb OR j->'source_kind' IS DISTINCT FROM '"independent_market"'::jsonb THEN RETURN false;END IF;
    FOREACH k IN ARRAY ARRAY['quantity_base_life','price_usdt','independent_price_usdt','signed_fee','realized_pnl_usdt'] LOOP
        IF jsonb_typeof(j->k) IS DISTINCT FROM 'string' THEN RETURN false;END IF;
        PERFORM (j->>k)::life_financial.signed_amount;
    END LOOP;
    FOREACH k IN ARRAY ARRAY['fill_at_ms','independent_value_at_ms','independent_observed_at_ms'] LOOP
        IF jsonb_typeof(j->k) IS DISTINCT FROM 'number' OR j->>k !~ '^[1-9][0-9]*$'
            OR (j->>k)::numeric>253402300799999 THEN RETURN false;END IF;
    END LOOP;
    q:=(j->>'quantity_base_life')::numeric;price:=(j->>'price_usdt')::numeric;
    at_ms:=(j->>'fill_at_ms')::bigint;value_ms:=(j->>'independent_value_at_ms')::bigint;
    value_observed_ms:=(j->>'independent_observed_at_ms')::bigint;
    RETURN q>0 AND price>0 AND (j->>'independent_price_usdt')::numeric>0
        AND at_ms BETWEEN d.prepared_at_ms AND observed_ms AND value_observed_ms BETWEEN value_ms AND observed_ms
        AND abs(at_ms-value_ms)<=p.max_reference_skew_ms AND abs(at_ms-value_observed_ms)<=p.max_reference_skew_ms
        AND (t.side='BUY' AND price<=t.price OR t.side='SELL' AND price>=t.price)
        AND mod(q,(d.permit->>'contract_value_life')::numeric*(SELECT (body->>'lot_contracts')::numeric
            FROM life_financial.shared_capital_policies WHERE account_id=p.account_id))=0
        AND (j->'sequence'='null'::jsonb OR jsonb_typeof(j->'sequence')='number' AND j->>'sequence' ~ '^(0|[1-9][0-9]*)$'
            AND (j->>'sequence')::numeric<=9223372036854775807);
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.swap_accounting_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE p life_financial.swap_fill_policies;f life_financial.swap_fill_facts;t life_financial.send_attempts;
    d life_financial.swap_preparations;o life_financial.operations;g life_financial.swap_fill_progress;r jsonb;
    loss numeric;observed_ms bigint;expected jsonb;sc text;window_key_value text;at timestamptz;policy life_financial.spot_policies;
BEGIN
    SELECT * INTO p FROM life_financial.swap_fill_policies WHERE account_id=p_account;
    IF NOT FOUND THEN RETURN NOT EXISTS(SELECT 1 FROM life_financial.swap_fill_facts WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.swap_fill_progress WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.swap_pending_fills WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.fills WHERE account_id=p_account AND product='SWAP')
        AND NOT EXISTS(SELECT 1 FROM life_financial.fees WHERE account_id=p_account AND product='SWAP')
        AND NOT EXISTS(SELECT 1 FROM life_financial.financial_events WHERE account_id=p_account AND product='SWAP');END IF;
    IF p.policy_hash<>(SELECT policy_hash FROM life_financial.accounts WHERE account_id=p_account) THEN RETURN false;END IF;
    IF (SELECT count(*) FROM life_financial.operations WHERE account_id=p_account AND command->>'kind'='SWAP_FILL')>p.max_observations THEN RETURN false;END IF;
    r:=life_financial.swap_fill_replay(p_account);
    SELECT * INTO STRICT policy FROM life_financial.spot_policies WHERE account_id=p_account AND policy_hash=p.policy_hash;
    FOR f IN SELECT * FROM life_financial.swap_fill_facts WHERE account_id=p_account LOOP
        SELECT * INTO STRICT t FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=f.intent_id;
        SELECT * INTO STRICT d FROM life_financial.swap_preparations WHERE account_id=p_account AND intent_id=f.intent_id;
        SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=f.source_operation_id;
        observed_ms:=floor(extract(epoch FROM (o.command#>>'{command,observed_at}')::timestamptz)*1000)::bigint;
        IF t.product<>'SWAP' OR t.state NOT IN ('SEND_UNKNOWN','ACKED') OR t.exchange_order_id IS NOT NULL AND t.exchange_order_id<>f.exchange_order_id
            OR EXISTS(SELECT 1 FROM life_financial.send_attempts other WHERE other.account_id=p_account AND other.product='SWAP'
                AND other.exchange_order_id=f.exchange_order_id AND other.intent_id<>f.intent_id)
            OR NOT life_financial.swap_fill_valid(f.observation,p,d,t,observed_ms)
            OR f.trade_id<>f.observation->>'trade_id' OR f.source_hash<>encode(sha256(convert_to(life_financial.canonical_json_v1(f.observation),'UTF8')),'hex')
            OR o.command->>'kind'<>'SWAP_FILL' OR o.receipt->>'state'<>'APPLIED'
            OR o.command#>>'{command,intent_id}'<>f.intent_id OR o.command#>>'{command,exchange_order_id}'<>f.exchange_order_id
            OR o.command#>>'{command,prepare_operation_id}'<>d.operation_id OR o.command#>>'{command,client_order_id}'<>t.wire_id
            OR o.command#>>'{command,market}'<>'LIFE-USDT' OR o.command#>>'{command,side}'<>t.side
            OR o.command#>>'{command,session_id}'<>t.session_id::text OR (o.command#>>'{command,epoch}')::bigint<>t.epoch
            OR o.command->'owner' IS DISTINCT FROM (SELECT command->'owner' FROM life_financial.operations
                WHERE account_id=p_account AND operation_id=d.operation_id)
            OR NOT o.command#>'{command,fills}' @> jsonb_build_array(f.observation) THEN RETURN false;END IF;
        at:=to_timestamp((f.observation->>'fill_at_ms')::bigint/1000.0);
        IF NOT EXISTS(SELECT 1 FROM life_financial.fills n WHERE n.account_id=p_account AND n.product='SWAP' AND n.trade_id=f.trade_id
            AND n.wire_id=t.wire_id AND n.quantity=(f.observation->>'quantity_base_life')::numeric AND n.price=(f.observation->>'price_usdt')::numeric
            AND n.independent_price=(f.observation->>'independent_price_usdt')::numeric AND n.fill_at=at
            AND n.value_at=to_timestamp((f.observation->>'independent_value_at_ms')::bigint/1000.0) AND n.source_hash=f.source_hash)
            OR NOT EXISTS(SELECT 1 FROM life_financial.fees n WHERE n.account_id=p_account AND n.product='SWAP' AND n.trade_id=f.trade_id
                AND n.payment_id='swapfee:'||encode(sha256(convert_to(f.trade_id,'UTF8')),'hex') AND n.asset='USDT' AND n.signed_fee=(f.observation->>'signed_fee')::numeric)
            OR NOT EXISTS(SELECT 1 FROM life_financial.financial_events e WHERE e.account_id=p_account AND e.product='SWAP' AND e.kind='FILL'
                AND e.event_id='swapfill:'||encode(sha256(convert_to(f.trade_id,'UTF8')),'hex') AND e.exchange_identity=f.trade_id
                AND e.base_delta=0 AND e.quote_delta=(f.observation->>'signed_fee')::numeric+(f.observation->>'realized_pnl_usdt')::numeric
                AND e.occurred_at=at AND e.source_hash=f.source_hash) THEN RETURN false;END IF;
        loss:=greatest(0,-(f.observation->>'signed_fee')::numeric)+greatest(0,-(f.observation->>'realized_pnl_usdt')::numeric);
        FOREACH sc IN ARRAY ARRAY['SESSION','DAY','CAMPAIGN'] LOOP
            window_key_value:=CASE sc WHEN 'SESSION' THEN t.session_id::text WHEN 'DAY' THEN to_char(at AT TIME ZONE 'UTC','YYYY-MM-DD') ELSE policy.campaign_id END;
            IF NOT EXISTS(SELECT 1 FROM life_financial.budget_events e WHERE e.account_id=p_account
                AND e.event_id='loss:'||encode(sha256(convert_to('SWAP:'||f.trade_id,'UTF8')),'hex')||':'||sc
                AND e.family='LOSS' AND e.scope=sc AND e.window_key=window_key_value AND e.kind='SPEND' AND e.amount=loss) THEN RETURN false;END IF;
        END LOOP;
    END LOOP;
    IF EXISTS(SELECT 1 FROM life_financial.fills n WHERE n.account_id=p_account AND n.product='SWAP' AND NOT EXISTS(
        SELECT 1 FROM life_financial.swap_fill_facts f WHERE f.account_id=p_account AND f.trade_id=n.trade_id))
        OR EXISTS(SELECT 1 FROM life_financial.fees n WHERE n.account_id=p_account AND n.product='SWAP' AND NOT EXISTS(
            SELECT 1 FROM life_financial.swap_fill_facts f WHERE f.account_id=p_account AND f.trade_id=n.trade_id
                AND n.payment_id='swapfee:'||encode(sha256(convert_to(f.trade_id,'UTF8')),'hex')))
        OR EXISTS(SELECT 1 FROM life_financial.financial_events n WHERE n.account_id=p_account AND n.product='SWAP' AND (n.kind<>'FILL' OR NOT EXISTS(
            SELECT 1 FROM life_financial.swap_fill_facts f WHERE f.account_id=p_account AND f.trade_id=n.exchange_identity))) THEN RETURN false;END IF;
    IF EXISTS(SELECT 1 FROM life_financial.budget_buckets b WHERE b.account_id=p_account AND b.family='LOSS' AND b.incurred<>COALESCE((
        SELECT sum(e.amount) FROM life_financial.budget_events e WHERE e.account_id=p_account AND e.family=b.family AND e.scope=b.scope
            AND e.window_key=b.window_key AND e.kind='SPEND'),0)) THEN RETURN false;END IF;
    FOR g IN SELECT * FROM life_financial.swap_fill_progress WHERE account_id=p_account LOOP
        IF (g.filled_base::numeric,g.paid_fees_usdt::numeric,g.execution_loss_usdt::numeric) IS DISTINCT FROM (
            SELECT ROW(sum((f.observation->>'quantity_base_life')::numeric),sum(greatest(0,-(f.observation->>'signed_fee')::numeric)),
                sum(greatest(0,-(f.observation->>'signed_fee')::numeric)+greatest(0,-(f.observation->>'realized_pnl_usdt')::numeric)))
                FROM life_financial.swap_fill_facts f WHERE f.account_id=p_account AND f.intent_id=g.intent_id)
            OR g.filled_base>(SELECT quantity FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=g.intent_id)
            OR EXISTS(SELECT 1 FROM life_financial.swap_fill_facts f WHERE f.account_id=p_account AND f.intent_id=g.intent_id AND f.exchange_order_id<>g.exchange_order_id)
            OR g.version<>(SELECT count(*) FROM life_financial.operations op WHERE op.account_id=p_account AND op.command->>'kind'='SWAP_FILL'
                AND op.command#>>'{command,intent_id}'=g.intent_id AND op.receipt->>'state'='APPLIED') THEN RETURN false;END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM life_financial.swap_fill_facts f WHERE f.account_id=p_account AND NOT EXISTS(
        SELECT 1 FROM life_financial.swap_fill_progress g WHERE g.account_id=p_account AND g.intent_id=f.intent_id)) THEN RETURN false;END IF;
    FOR o IN SELECT * FROM life_financial.operations WHERE account_id=p_account AND command->>'kind'='SWAP_FILL' LOOP
        IF o.fingerprint<>encode(sha256(convert_to(life_financial.canonical_json_v1(o.command),'UTF8')),'hex')
            OR o.command#>>'{command,operation_id}'<>o.operation_id OR o.receipt->>'kind'<>'SWAP_FILL'
            OR o.receipt->>'operation_id'<>o.operation_id OR o.receipt->>'intent_id'<>o.command#>>'{command,intent_id}'
            OR o.receipt->'trading_permission' IS DISTINCT FROM 'false'::jsonb OR o.receipt->'replayed' IS DISTINCT FROM 'false'::jsonb
            OR o.receipt->>'cumulative_base_life'<>o.command#>>'{command,cumulative_base_life}'
            OR (o.receipt->>'version')::bigint<>o.committed_version THEN RETURN false;END IF;
        IF o.receipt->>'state'='APPLIED' THEN
            IF EXISTS(SELECT 1 FROM jsonb_array_elements(o.command#>'{command,fills}') item WHERE NOT EXISTS(
                SELECT 1 FROM life_financial.swap_fill_facts f WHERE f.account_id=p_account AND f.observation=item
                    AND f.intent_id=o.command#>>'{command,intent_id}' AND f.exchange_order_id=o.command#>>'{command,exchange_order_id}'))
                OR (o.receipt->>'execution_loss_usdt')::numeric<>(SELECT sum(greatest(0,-(item->>'signed_fee')::numeric)
                    +greatest(0,-(item->>'realized_pnl_usdt')::numeric)) FROM jsonb_array_elements(o.command#>'{command,fills}') item) THEN RETURN false;END IF;
        ELSIF o.receipt->>'state'='RECOVERY_REQUIRED' THEN
            IF NOT EXISTS(SELECT 1 FROM life_financial.swap_pending_fills pending WHERE pending.account_id=p_account AND pending.operation_id=o.operation_id
                AND pending.intent_id=o.command#>>'{command,intent_id}' AND pending.command=o.command->'command'
                AND pending.reason=o.receipt->>'reason' AND pending.source_hash=encode(sha256(convert_to(life_financial.canonical_json_v1(pending.command),'UTF8')),'hex')) THEN RETURN false;END IF;
        ELSE RETURN false;END IF;
    END LOOP;
    RETURN NOT EXISTS(SELECT 1 FROM life_financial.swap_pending_fills pending WHERE pending.account_id=p_account AND NOT EXISTS(
        SELECT 1 FROM life_financial.operations op WHERE op.account_id=p_account AND op.operation_id=pending.operation_id AND op.command->>'kind'='SWAP_FILL'
            AND op.receipt->>'state'='RECOVERY_REQUIRED'));
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.swap_fill_read(p_account uuid,p_rows integer,p_bytes integer,p_operation text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID' USING ERRCODE='P0001';END IF;
    IF p_rows IS NULL OR p_rows NOT BETWEEN 1 AND 4096 OR p_bytes IS NULL OR p_bytes NOT BETWEEN 1 AND 16777216 THEN
        RAISE EXCEPTION 'LIFE_SWAP_FILL_READ_BOUND_INVALID' USING ERRCODE='P0001';END IF;
    IF (SELECT count(*) FROM (SELECT 1 FROM life_financial.swap_fill_facts WHERE account_id=p_account LIMIT p_rows+1) x)>p_rows
        OR (SELECT count(*) FROM (SELECT 1 FROM life_financial.swap_pending_fills WHERE account_id=p_account LIMIT p_rows+1) x)>p_rows THEN
        RAISE EXCEPTION 'LIFE_SWAP_FILL_READ_BOUND' USING ERRCODE='P0001';END IF;
    IF NOT life_financial.swap_accounting_verified(p_account) OR NOT life_financial.swap_verified(p_account)
        OR NOT life_financial.spot_capital_verified(p_account) THEN
        RAISE EXCEPTION 'LIFE_SWAP_ACCOUNTING_UNVERIFIED' USING ERRCODE='P0001';END IF;
    IF p_operation IS NULL THEN result:=jsonb_build_object('replay',life_financial.swap_fill_replay(p_account),
        'fills',COALESCE((SELECT jsonb_agg(to_jsonb(f) ORDER BY trade_id) FROM life_financial.swap_fill_facts f WHERE account_id=p_account),'[]'::jsonb),
        'pending',COALESCE((SELECT jsonb_agg(to_jsonb(p) ORDER BY operation_id) FROM life_financial.swap_pending_fills p WHERE account_id=p_account),'[]'::jsonb),
        'trading_permission',false);
    ELSE SELECT jsonb_build_object('command',command,'receipt',receipt) INTO result FROM life_financial.operations
        WHERE account_id=p_account AND operation_id=p_operation AND command->>'kind'='SWAP_FILL';END IF;
    IF octet_length(result::text)>p_bytes THEN RAISE EXCEPTION 'LIFE_SWAP_FILL_READ_BOUND' USING ERRCODE='P0001';END IF;
    RETURN result;
END; $$;

CREATE FUNCTION life_financial.swap_fill_command(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,cmd jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts;p life_financial.swap_fill_policies;d life_financial.swap_preparations;t life_financial.send_attempts;
    old life_financial.operations;known life_financial.swap_fill_facts;g life_financial.swap_fill_progress;
    request jsonb;result jsonb;view jsonb;j jsonb;k text;r jsonb;op text;trade text;hash text;reason text;received timestamptz;received_ms bigint;
    quantity numeric;fee numeric;pnl numeric;loss numeric;total_quantity numeric:=0;delta_quote numeric:=0;paid numeric:=0;execution_loss numeric:=0;
    seen text[]:=ARRAY[]::text[];at timestamptz;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR jsonb_typeof(cmd) IS DISTINCT FROM 'object' OR octet_length(cmd::text)>1048576
        OR (SELECT count(*) FROM jsonb_object_keys(cmd))<>12 OR NOT cmd ?& ARRAY['operation_id','prepare_operation_id','intent_id','client_order_id',
            'market','side','session_id','epoch','exchange_order_id','cumulative_base_life','fills','observed_at']
        OR cmd->>'operation_id' !~ '^[A-Za-z0-9_.:-]{1,96}$' THEN RAISE EXCEPTION 'LIFE_SWAP_FILL_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
    FOREACH k IN ARRAY ARRAY['operation_id','prepare_operation_id','intent_id','client_order_id','market','side','session_id','exchange_order_id','cumulative_base_life','observed_at'] LOOP
        IF jsonb_typeof(cmd->k) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'LIFE_SWAP_FILL_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
    END LOOP;
    IF jsonb_typeof(cmd->'epoch') IS DISTINCT FROM 'number' OR cmd->>'epoch' !~ '^[1-9][0-9]*$'
        OR (cmd->>'epoch')::numeric>9223372036854775807 THEN RAISE EXCEPTION 'LIFE_SWAP_FILL_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
    op:=cmd->>'operation_id';request:=jsonb_build_object('kind','SWAP_FILL','binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',cmd);
    IF p_fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(request),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'LIFE_SWAP_FILL_FINGERPRINT_INVALID' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,a.version);
    IF NOT life_financial.swap_accounting_verified(p_account) OR NOT life_financial.swap_verified(p_account) THEN
        RAISE EXCEPTION 'LIFE_SWAP_ACCOUNTING_UNVERIFIED' USING ERRCODE='P0001';END IF;
    SELECT * INTO old FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF old.command<>request OR old.fingerprint<>p_fingerprint THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001';END IF;
        RETURN old.receipt||'{"replayed":true}'::jsonb;END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    view:=life_financial.spot_state(p_account);
    IF view->>'digest' IS DISTINCT FROM p_cut THEN RAISE EXCEPTION 'LIFE_SPOT_CUT_CONFLICT' USING ERRCODE='P0001';END IF;
    IF (view->>'unverified')::boolean THEN RAISE EXCEPTION 'LIFE_SWAP_ACCOUNTING_UNVERIFIED' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT p FROM life_financial.swap_fill_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
    IF (SELECT count(*) FROM life_financial.operations WHERE account_id=p_account AND command->>'kind'='SWAP_FILL')>=p.max_observations THEN
        RAISE EXCEPTION 'LIFE_SWAP_FILL_OBSERVATION_BOUND' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT d FROM life_financial.swap_preparations WHERE account_id=p_account AND operation_id=cmd->>'prepare_operation_id';
    SELECT * INTO STRICT t FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=d.intent_id;
    received:=(cmd->>'observed_at')::timestamptz;received_ms:=floor(extract(epoch FROM received)*1000)::bigint;
    IF NOT isfinite(received) OR received>clock_timestamp() OR received_ms<d.prepared_at_ms
        OR t.product<>'SWAP' OR t.state NOT IN ('SEND_UNKNOWN','ACKED') OR t.intent_id<>cmd->>'intent_id' OR t.wire_id<>cmd->>'client_order_id'
        OR t.side<>cmd->>'side' OR cmd->>'market'<>'LIFE-USDT' OR t.session_id::text<>cmd->>'session_id' OR t.epoch<>(cmd->>'epoch')::bigint
        OR t.process_id<>a.owner_process OR t.generation<>a.owner_generation OR t.epoch<>a.owner_epoch OR t.session_id<>a.current_session
        OR jsonb_typeof(cmd->'exchange_order_id') IS DISTINCT FROM 'string' OR cmd->>'exchange_order_id' !~ '^[1-9][0-9]{0,63}$'
        OR t.exchange_order_id IS NOT NULL AND t.exchange_order_id<>cmd->>'exchange_order_id' THEN
        RAISE EXCEPTION 'LIFE_SWAP_FILL_ORIGINAL_SCOPE_INVALID' USING ERRCODE='P0001';END IF;
    SELECT * INTO g FROM life_financial.swap_fill_progress WHERE account_id=p_account AND intent_id=t.intent_id;
    IF EXISTS(SELECT 1 FROM life_financial.send_attempts other WHERE other.account_id=p_account AND other.product='SWAP'
        AND other.exchange_order_id=cmd->>'exchange_order_id' AND other.intent_id<>t.intent_id) THEN
        RAISE EXCEPTION 'LIFE_SWAP_FILL_ORDER_ID_CONFLICT' USING ERRCODE='P0001';END IF;
    BEGIN
        IF jsonb_typeof(cmd->'fills') IS DISTINCT FROM 'array' OR jsonb_array_length(cmd->'fills') NOT BETWEEN 1 AND p.max_total_fills
            OR jsonb_typeof(cmd->'cumulative_base_life') IS DISTINCT FROM 'string' THEN
            RAISE EXCEPTION 'LIFE_SWAP_FILL_SNAPSHOT_INVALID' USING ERRCODE='P0001';END IF;
        PERFORM (cmd->>'cumulative_base_life')::life_financial.positive_amount;
        FOR j IN SELECT value FROM jsonb_array_elements(cmd->'fills') LOOP
            IF NOT life_financial.swap_fill_valid(j,p,d,t,received_ms) THEN RAISE EXCEPTION 'LIFE_SWAP_FILL_INVALID' USING ERRCODE='P0001';END IF;
            trade:=j->>'trade_id';IF trade=ANY(seen) THEN RAISE EXCEPTION 'LIFE_SWAP_FILL_IDENTITY_CONFLICT' USING ERRCODE='P0001';END IF;seen:=array_append(seen,trade);
            quantity:=(j->>'quantity_base_life')::numeric;fee:=(j->>'signed_fee')::numeric;pnl:=(j->>'realized_pnl_usdt')::numeric;
            loss:=greatest(0,-fee)+greatest(0,-pnl);total_quantity:=total_quantity+quantity;paid:=paid+greatest(0,-fee);execution_loss:=execution_loss+loss;
            SELECT * INTO known FROM life_financial.swap_fill_facts WHERE account_id=p_account AND trade_id=trade;
            IF FOUND THEN
                IF known.intent_id<>t.intent_id OR known.exchange_order_id<>cmd->>'exchange_order_id' OR known.observation<>j THEN
                    RAISE EXCEPTION 'LIFE_SWAP_FILL_IDENTITY_CONFLICT' USING ERRCODE='P0001';END IF;
                CONTINUE;END IF;
            IF EXISTS(SELECT 1 FROM life_financial.swap_pending_fills pending CROSS JOIN LATERAL jsonb_array_elements(pending.command->'fills') item
                WHERE pending.account_id=p_account AND item->>'trade_id'=trade AND (pending.intent_id<>t.intent_id OR item<>j)) THEN
                RAISE EXCEPTION 'LIFE_SWAP_FILL_IDENTITY_CONFLICT' USING ERRCODE='P0001';END IF;
            at:=to_timestamp((j->>'fill_at_ms')::bigint/1000.0);hash:=encode(sha256(convert_to(life_financial.canonical_json_v1(j),'UTF8')),'hex');
            INSERT INTO life_financial.swap_fill_facts VALUES(p_account,trade,t.intent_id,cmd->>'exchange_order_id',j,op,hash);
            INSERT INTO life_financial.fills VALUES(p_account,'SWAP',trade,t.wire_id,quantity,(j->>'price_usdt')::numeric,
                (j->>'independent_price_usdt')::numeric,at,to_timestamp((j->>'independent_value_at_ms')::bigint/1000.0),hash);
            INSERT INTO life_financial.fees VALUES(p_account,'swapfee:'||encode(sha256(convert_to(trade,'UTF8')),'hex'),'SWAP',trade,'USDT',fee);
            INSERT INTO life_financial.financial_events(account_id,event_id,product,kind,exchange_identity,occurred_at,source_hash,base_delta,quote_delta)
                VALUES(p_account,'swapfill:'||encode(sha256(convert_to(trade,'UTF8')),'hex'),'SWAP','FILL',trade,at,hash,0,pnl+fee);
            delta_quote:=delta_quote+pnl+fee;PERFORM life_financial.spot_charge_loss(p_account,t.session_id,at,'SWAP:'||trade,loss);
        END LOOP;
        IF total_quantity<>(cmd->>'cumulative_base_life')::numeric OR total_quantity>t.quantity
            OR EXISTS(SELECT 1 FROM life_financial.swap_fill_facts WHERE account_id=p_account AND intent_id=t.intent_id AND NOT trade_id=ANY(seen))
            OR g.account_id IS NOT NULL AND (g.exchange_order_id<>cmd->>'exchange_order_id' OR total_quantity<g.filled_base) THEN
            RAISE EXCEPTION 'LIFE_SWAP_FILL_HISTORY_INCOMPLETE' USING ERRCODE='P0001';END IF;
        r:=life_financial.swap_fill_replay(p_account);
        IF EXISTS(SELECT 1 FROM life_financial.swap_pending_fills WHERE account_id=p_account AND intent_id=t.intent_id) THEN
            RAISE EXCEPTION 'LIFE_SWAP_PRIOR_FILL_PENDING' USING ERRCODE='P0001';END IF;
        IF (SELECT total FROM life_financial.balances WHERE account_id=p_account AND asset='USDT')+delta_quote<0 THEN
            RAISE EXCEPTION 'LIFE_SWAP_NEGATIVE_WALLET_UNSUPPORTED' USING ERRCODE='P0001';END IF;
        UPDATE life_financial.balances SET total=total+delta_quote,funding_deficit=greatest(0,reserved-total-delta_quote),version=version+1
            WHERE account_id=p_account AND asset='USDT';
        INSERT INTO life_financial.swap_fill_progress VALUES(p_account,t.intent_id,total_quantity,paid,execution_loss,cmd->>'exchange_order_id',1)
            ON CONFLICT(account_id,intent_id) DO UPDATE SET filled_base=EXCLUDED.filled_base,paid_fees_usdt=EXCLUDED.paid_fees_usdt,
                execution_loss_usdt=EXCLUDED.execution_loss_usdt,version=life_financial.swap_fill_progress.version+1;
        -- Replaying the complete common wallet disallows funding an earlier
        -- debit with later profits/transfers. No debt model is inferred.
        r:=life_financial.spot_capital_replay(p_account);
        IF (r->>'quote')::numeric<>(SELECT total FROM life_financial.balances WHERE account_id=p_account AND asset='USDT') THEN
            RAISE EXCEPTION 'LIFE_SWAP_WALLET_REPLAY_MISMATCH' USING ERRCODE='P0001';END IF;
        IF EXISTS(SELECT 1 FROM life_financial.balances WHERE account_id=p_account AND funding_deficit>0) THEN
            UPDATE life_financial.accounts SET halted=true WHERE account_id=p_account;END IF;
        result:=jsonb_build_object('kind','SWAP_FILL','operation_id',op,'intent_id',t.intent_id,'state','APPLIED',
            'cumulative_base_life',cmd->>'cumulative_base_life','execution_loss_usdt',execution_loss::text,'version',p_version+1,'replayed',false,'trading_permission',false);
    EXCEPTION WHEN raise_exception OR check_violation OR numeric_value_out_of_range OR unique_violation THEN
        GET STACKED DIAGNOSTICS reason=MESSAGE_TEXT;
        IF reason !~ '^LIFE_[A-Z_]+$' THEN reason:='LIFE_SWAP_FINANCIAL_CONSTRAINT_REJECTED';END IF;
        INSERT INTO life_financial.swap_pending_fills VALUES(p_account,op,t.intent_id,cmd,replace(reason,'LIFE_',''),
            encode(sha256(convert_to(life_financial.canonical_json_v1(cmd),'UTF8')),'hex'));
        UPDATE life_financial.accounts SET halted=true WHERE account_id=p_account;
        result:=jsonb_build_object('kind','SWAP_FILL','operation_id',op,'intent_id',t.intent_id,'state','RECOVERY_REQUIRED','reason',replace(reason,'LIFE_',''),
            'cumulative_base_life',cmd->>'cumulative_base_life','execution_loss_usdt',COALESCE(g.execution_loss_usdt,0)::text,'version',p_version+1,'replayed',false,'trading_permission',false);
    END;
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version) VALUES(p_account,op,p_fingerprint,request,result,p_version+1);
    RETURN result;
END; $$;

-- Extend current SPOT proofs without renaming their OIDs or removing approval,
-- action, recorder or ingress wrappers. Financial SWAP events require the
-- independent immutable native fill proof above.
CREATE OR REPLACE FUNCTION life_financial.spot_accounting_verified(p_account uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT life_financial.swap_accounting_verified(p_account) AND NOT EXISTS (
    SELECT 1 FROM life_financial.spot_preparations d JOIN life_financial.send_attempts t USING(account_id,intent_id)
    LEFT JOIN life_financial.spot_accounting_progress g USING(account_id,intent_id)
    LEFT JOIN LATERAL (SELECT COALESCE(sum(f.quantity),0) AS filled,
        COALESCE(sum(greatest(0,-(x.observation->>'signed_fee')::numeric)) FILTER(WHERE x.observation->>'fee_currency'='LIFE'),0) AS paid_base,
        COALESCE(sum(greatest(0,-(x.observation->>'signed_fee')::numeric)) FILTER(WHERE x.observation->>'fee_currency'='USDT'),0) AS paid_quote,
        COALESCE(sum(x.execution_loss),0) AS loss,
        count(DISTINCT x.exchange_order_id) AS order_ids,min(x.exchange_order_id) AS order_id
        FROM life_financial.spot_fill_facts x JOIN life_financial.fills f USING(account_id,product,trade_id)
        WHERE x.account_id=t.account_id AND x.intent_id=t.intent_id) totals ON true
    WHERE d.account_id=p_account AND (COALESCE(g.filled_base,0)<>totals.filled OR COALESCE(g.paid_base,0)<>totals.paid_base
        OR COALESCE(g.paid_quote,0)<>totals.paid_quote OR COALESCE(g.execution_loss,0)<>totals.loss
        OR COALESCE(g.filled_base,0)>t.quantity OR totals.order_ids>1
        OR totals.order_ids>0 AND (g.observed_order_id IS DISTINCT FROM totals.order_id
            OR t.exchange_order_id IS NOT NULL AND t.exchange_order_id<>totals.order_id))
) AND NOT EXISTS (
    SELECT 1 FROM life_financial.fills f LEFT JOIN life_financial.spot_fill_facts x USING(account_id,product,trade_id)
    LEFT JOIN life_financial.fees fee ON fee.account_id=f.account_id AND fee.payment_id='fee:'||encode(sha256(convert_to(f.trade_id,'UTF8')),'hex')
    LEFT JOIN life_financial.send_attempts t ON t.account_id=x.account_id AND t.intent_id=x.intent_id
    LEFT JOIN life_financial.financial_events e ON e.account_id=f.account_id AND e.product='SPOT' AND e.kind='FILL' AND e.exchange_identity=f.trade_id
    WHERE f.account_id=p_account AND f.product='SPOT' AND (f.product<>'SPOT' OR x.trade_id IS NULL OR fee.payment_id IS NULL OR e.event_id IS NULL
        OR f.wire_id<>t.wire_id OR f.quantity<>(x.observation->>'quantity_base_life')::numeric
        OR f.price<>(x.observation->>'price_usdt')::numeric OR f.independent_price<>(x.observation->>'independent_price_usdt')::numeric
        OR fee.product<>'SPOT' OR fee.trade_id<>f.trade_id OR fee.asset<>x.observation->>'fee_currency'
        OR fee.signed_fee<>(x.observation->>'signed_fee')::numeric
        OR f.source_hash<>encode(sha256(convert_to(x.observation::text,'UTF8')),'hex') OR e.source_hash<>f.source_hash
        OR e.base_delta<>(CASE WHEN t.side='BUY' THEN f.quantity ELSE -f.quantity END+CASE WHEN fee.asset='LIFE' THEN fee.signed_fee ELSE 0 END)
        OR e.quote_delta<>(CASE WHEN t.side='BUY' THEN -f.quantity*f.price ELSE f.quantity*f.price END+CASE WHEN fee.asset='USDT' THEN fee.signed_fee ELSE 0 END))
) AND NOT EXISTS (
    SELECT 1 FROM life_financial.fees fee WHERE fee.account_id=p_account AND fee.product='SPOT' AND NOT EXISTS(
        SELECT 1 FROM life_financial.spot_fill_facts x WHERE x.account_id=p_account AND fee.product='SPOT'
            AND fee.trade_id=x.trade_id AND fee.payment_id='fee:'||encode(sha256(convert_to(x.trade_id,'UTF8')),'hex'))
);
$$;

CREATE OR REPLACE FUNCTION life_financial.spot_capital_replay(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.spot_capital_policies; e record; j jsonb;
    base numeric; quote numeric; initial numeric; realized numeric:=0; net numeric:=0; loss numeric:=0;
    amount numeric; price numeric; reference numeric; fee numeric; sign numeric; consumed numeric;
    next_base numeric; next_quote numeric; n bigint;
BEGIN
    SELECT * INTO STRICT p FROM life_financial.spot_capital_policies WHERE account_id=p_account;
    IF p.policy_hash<>(SELECT policy_hash FROM life_financial.accounts WHERE account_id=p_account) THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_POLICY_MISMATCH' USING ERRCODE='P0001'; END IF;
    SELECT (SELECT count(*) FROM life_financial.spot_fill_facts WHERE account_id=p_account)+
           (SELECT count(*) FROM life_financial.spot_cashflow_facts WHERE account_id=p_account)+
           (SELECT count(*) FROM life_financial.swap_fill_facts WHERE account_id=p_account) INTO n;
    IF n>p.max_replay_events THEN RAISE EXCEPTION 'LIFE_CAPITAL_REPLAY_BOUND' USING ERRCODE='P0001'; END IF;
    base:=p.opening_base; quote:=p.opening_quote; initial:=base;
    FOR e IN SELECT * FROM (
        SELECT (x.command->>'transfer_at_ms')::bigint AS at_ms,0 AS kind,x.bill_id::text AS id,x.command AS data,NULL::text AS side
            FROM life_financial.spot_cashflow_facts x WHERE account_id=p_account AND state='APPLIED'
        UNION ALL
        SELECT (x.observation->>'fill_at_ms')::bigint,1,x.trade_id::text,x.observation,t.side
            FROM life_financial.spot_fill_facts x JOIN life_financial.send_attempts t USING(account_id,intent_id) WHERE x.account_id=p_account
        UNION ALL
        SELECT (x.observation->>'fill_at_ms')::bigint,2,x.trade_id::text,x.observation,NULL::text
            FROM life_financial.swap_fill_facts x WHERE x.account_id=p_account
        ) timeline ORDER BY at_ms,kind,id COLLATE "C"
    LOOP
        j:=e.data;
        IF e.at_ms<floor(extract(epoch FROM p.opened_at)*1000) THEN
            RAISE EXCEPTION 'LIFE_CAPITAL_CAUSAL_INVALID' USING ERRCODE='P0001'; END IF;
        reference:=(j->>'independent_price_usdt')::numeric;
        IF e.kind=2 THEN
            quote:=quote+(j->>'realized_pnl_usdt')::numeric+(j->>'signed_fee')::numeric;
        ELSIF e.kind=0 THEN
            amount:=(j->>'signed_amount')::numeric;
            IF j->>'currency'='LIFE' THEN
                base:=base+amount;
                IF amount<0 THEN
                    consumed:=least(initial,-amount); initial:=initial-consumed;
                    realized:=realized+consumed*(reference-p.opening_price);
                END IF;
            ELSE quote:=quote+amount; END IF;
            net:=net+amount*reference;
        ELSE
            amount:=(j->>'quantity_base_life')::numeric; price:=(j->>'price_usdt')::numeric;
            fee:=(j->>'signed_fee')::numeric; sign:=CASE WHEN e.side='BUY' THEN 1 ELSE -1 END;
            IF e.side='SELL' AND amount>base OR e.side='BUY' AND amount*price-(CASE WHEN j->>'fee_currency'='USDT' THEN fee ELSE 0 END)>quote THEN
                RAISE EXCEPTION 'LIFE_CAPITAL_CAUSAL_INVALID' USING ERRCODE='P0001'; END IF;
            next_base:=base+sign*amount+CASE WHEN j->>'fee_currency'='LIFE' THEN fee ELSE 0 END;
            next_quote:=quote-sign*amount*price+CASE WHEN j->>'fee_currency'='USDT' THEN fee ELSE 0 END;
            base:=next_base; quote:=next_quote;
            loss:=loss+greatest(0,-(sign*(reference-price)*amount+fee*CASE WHEN j->>'fee_currency'='LIFE' THEN reference ELSE 1 END));
            IF e.side='SELL' THEN
                consumed:=least(initial,amount); initial:=initial-consumed;
                realized:=realized+consumed*(reference-p.opening_price);
                IF j->>'fee_currency'='LIFE' AND fee<0 THEN
                    consumed:=least(initial,-fee); initial:=initial-consumed;
                    realized:=realized+consumed*(reference-p.opening_price);
                END IF;
            END IF;
        END IF;
        IF base<0 OR quote<0 THEN RAISE EXCEPTION 'LIFE_CAPITAL_CAUSAL_INVALID' USING ERRCODE='P0001'; END IF;
    END LOOP;
    RETURN jsonb_build_object('base',base::text,'quote',quote::text,'initial',initial::text,'realized',realized::text,
        'net',net::text,'loss',loss::text,'opening_price',p.opening_price::text,'opening_nav',(p.opening_quote+p.opening_base*p.opening_price)::text);
END; $$;
CREATE OR REPLACE FUNCTION life_financial.spot_capital_verified_v19(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r jsonb;
BEGIN
    IF NOT EXISTS(SELECT 1 FROM life_financial.spot_capital_policies WHERE account_id=p_account) THEN
        RETURN NOT EXISTS(SELECT 1 FROM life_financial.cashflows WHERE account_id=p_account)
            AND NOT EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts WHERE account_id=p_account);
    END IF;
    IF EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts WHERE account_id=p_account AND state<>'APPLIED')
        OR EXISTS(SELECT 1 FROM life_financial.positions WHERE account_id=p_account AND quantity<>0)
        OR NOT life_financial.spot_accounting_verified(p_account)
        OR EXISTS(SELECT 1 FROM life_financial.fills f WHERE f.account_id=p_account AND f.product='SPOT' AND (NOT EXISTS(
            SELECT 1 FROM life_financial.spot_fill_facts x WHERE x.account_id=f.account_id AND x.trade_id=f.trade_id)))
        OR EXISTS(SELECT 1 FROM life_financial.cashflows c FULL JOIN life_financial.spot_cashflow_facts x USING(account_id,bill_id)
            LEFT JOIN life_financial.bill_approvals b USING(account_id,bill_id)
            WHERE COALESCE(c.account_id,x.account_id)=p_account AND (c.account_id IS NULL OR x.account_id IS NULL
                OR x.state<>'APPLIED' OR b.asset<>x.command->>'currency' OR b.signed_amount<>(x.command->>'signed_amount')::numeric
                OR floor(extract(epoch FROM b.transfer_at)*1000)<>(x.command->>'transfer_at_ms')::bigint
                OR c.independent_price<>(x.command->>'independent_price_usdt')::numeric OR c.value_usdt<>b.signed_amount*c.independent_price
                OR c.source_hash<>x.source_hash OR NOT EXISTS(SELECT 1 FROM life_financial.financial_events e
                    WHERE e.account_id=x.account_id AND e.product='ACCOUNT' AND e.kind='CASHFLOW' AND e.exchange_identity=x.bill_id
                    AND e.source_hash=x.source_hash AND e.occurred_at=b.transfer_at
                    AND e.base_delta=CASE WHEN b.asset='LIFE' THEN b.signed_amount ELSE 0 END
                    AND e.quote_delta=CASE WHEN b.asset='USDT' THEN b.signed_amount ELSE 0 END))) THEN RETURN false; END IF;
    r:=life_financial.spot_capital_replay(p_account);
    RETURN (r->>'base')::numeric=(SELECT total FROM life_financial.balances WHERE account_id=p_account AND asset='LIFE')
        AND (r->>'quote')::numeric=(SELECT total FROM life_financial.balances WHERE account_id=p_account AND asset='USDT');
EXCEPTION WHEN raise_exception OR no_data_found OR check_violation OR numeric_value_out_of_range THEN RETURN false;
END; $$;
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
            (e.product<>'SPOT' AND e.product<>'SWAP' AND NOT (e.product='ACCOUNT' AND e.kind='CASHFLOW') OR e.kind NOT IN ('ACK','FILL','CASHFLOW') OR e.kind='ACK' AND NOT EXISTS(
                SELECT 1 FROM life_financial.send_attempts t WHERE t.account_id=p_account AND t.product='SPOT' AND t.exchange_order_id=e.exchange_identity)
            OR e.kind='CASHFLOW' AND NOT EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts x WHERE x.account_id=p_account AND x.bill_id=e.exchange_identity AND x.state='APPLIED')
            OR e.kind='FILL' AND e.product='SPOT' AND NOT EXISTS(SELECT 1 FROM life_financial.spot_fill_facts x WHERE x.account_id=p_account AND x.trade_id=e.exchange_identity)))
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
    IF op.command->>'kind' IN ('SWAP_FILL','SWAP_ACK','CARRY_CHECK','RECORDER_POSITION_STAGE','RECORDER_POSITION_APPLY','RECORDER_EVENT_STAGE','RECORDER_EVENT_APPLY','RECORDER_CONFIG_STAGE','RECORDER_CONFIG_APPLY','RECORDER_STAGE','RECORDER_APPLY','SPOT_MARKOUT','SPOT_INVENTORY_SETTLE','SPOT_ACK','SPOT_FILL','SPOT_CASHFLOW','SPOT_NAV','SPOT_SESSION_TICK','SPOT_LIFECYCLE','SPOT_TERMINAL') OR op.command->>'kind' IN ('SPOT_ACTION_TRANSITION','SPOT_ACTION_ABANDON')
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

CREATE FUNCTION life_financial.spot_state_before_swap_accounting(p_account uuid) RETURNS jsonb
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
CREATE OR REPLACE FUNCTION life_financial.spot_state(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b jsonb;verified boolean;pending boolean;
BEGIN
    b:=life_financial.spot_state_before_swap_accounting(p_account)-'digest'-'observed_at';
    verified:=life_financial.swap_accounting_verified(p_account);
    pending:=EXISTS(SELECT 1 FROM life_financial.swap_pending_fills WHERE account_id=p_account);
    b:=b||jsonb_build_object('swap_accounting',jsonb_build_object('verified',verified,'pending',pending,
        'policy',(SELECT to_jsonb(p) FROM life_financial.swap_fill_policies p WHERE account_id=p_account),
        'facts',COALESCE((SELECT jsonb_agg(to_jsonb(f) ORDER BY trade_id) FROM life_financial.swap_fill_facts f WHERE account_id=p_account),'[]'::jsonb),
        'progress',COALESCE((SELECT jsonb_agg(to_jsonb(g) ORDER BY intent_id) FROM life_financial.swap_fill_progress g WHERE account_id=p_account),'[]'::jsonb),
        'pending_facts',COALESCE((SELECT jsonb_agg(to_jsonb(f) ORDER BY operation_id) FROM life_financial.swap_pending_fills f WHERE account_id=p_account),'[]'::jsonb)),
        'financial_recovery_required',(b->>'financial_recovery_required')::boolean OR NOT verified OR pending,
        'recovery_required',(b->>'recovery_required')::boolean OR NOT verified OR pending,
        'unverified',(b->>'unverified')::boolean OR NOT verified);
    RETURN b||jsonb_build_object('digest',encode(sha256(convert_to(b::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
END; $$;
CREATE FUNCTION life_financial.swap_fill_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF NOT life_financial.swap_accounting_verified(NEW.account_id)
        OR EXISTS(SELECT 1 FROM life_financial.swap_fill_policies WHERE account_id=NEW.account_id)
            AND NOT life_financial.spot_capital_verified(NEW.account_id) THEN
        RAISE EXCEPTION 'LIFE_SWAP_ACCOUNTING_UNVERIFIED_AT_COMMIT' USING ERRCODE='P0001';END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER swap_fill_at_commit AFTER UPDATE ON life_financial.accounts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN(NEW.version<>OLD.version) EXECUTE FUNCTION life_financial.swap_fill_commit_guard();
-- Stale account snapshots cannot erase a newly accounted derivative leg even
-- when a zero-fee open leaves cash unchanged. Facts and readiness are distinct:
-- old observations remain verified history, but cannot admit another risk.
CREATE FUNCTION life_financial.swap_snapshot_current(p_account uuid,snapshot jsonb) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r jsonb;
BEGIN
    IF NOT EXISTS(SELECT 1 FROM life_financial.swap_fill_policies WHERE account_id=p_account) THEN RETURN true;END IF;
    IF NOT life_financial.swap_accounting_verified(p_account)
        OR EXISTS(SELECT 1 FROM life_financial.swap_pending_fills WHERE account_id=p_account) THEN RETURN false;END IF;
    r:=life_financial.swap_fill_replay(p_account);
    RETURN COALESCE((snapshot->>'long_base')::numeric=(r->>'long_base')::numeric
        AND (snapshot->>'short_base')::numeric=(r->>'short_base')::numeric
        AND (snapshot->>'observed_at_ms')::bigint>=COALESCE((SELECT max((observation->>'fill_at_ms')::bigint)
            FROM life_financial.swap_fill_facts WHERE account_id=p_account),0),false);
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE OR REPLACE FUNCTION life_financial.shared_capital_command(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,cmd jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts;p life_financial.shared_capital_policies;h life_financial.shared_capital_heads;
    previous life_financial.operations;observed life_financial.shared_capital_observations;known life_financial.shared_capital_claims;
    request jsonb;view jsonb;orders jsonb;candidate jsonb:=cmd->'claim';snapshot jsonb:=cmd->'snapshot';decision jsonb;result jsonb;
    op text;at timestamptz;now_ms bigint;new_observation boolean:=false;changed boolean:=false;source_hash text;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR jsonb_typeof(cmd) IS DISTINCT FROM 'object'
        OR (SELECT count(*) FROM jsonb_object_keys(cmd))<>4 OR NOT cmd ?& ARRAY['kind','operation_id','snapshot','claim']
        OR cmd->>'kind' NOT IN ('RESERVE','OBSERVE') OR jsonb_typeof(cmd->'operation_id') IS DISTINCT FROM 'string'
        OR cmd->>'operation_id' !~ '^[A-Za-z0-9_.:-]{1,96}$' OR octet_length(cmd::text)>1048576
        OR cmd->>'kind'='OBSERVE' AND candidate IS DISTINCT FROM 'null'::jsonb THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    op:=cmd->>'operation_id';
    request:=jsonb_build_object('kind','SHARED_CAPITAL','binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',cmd);
    IF p_fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(request),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_FINGERPRINT_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    IF NOT life_financial.shared_capital_verified(p_account) THEN RAISE EXCEPTION 'LIFE_CAPITAL_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO previous FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF NOT life_financial.shared_operation_verified(p_account,op) THEN RAISE EXCEPTION 'LIFE_CAPITAL_UNVERIFIED' USING ERRCODE='P0001'; END IF;
        IF previous.command<>request OR previous.fingerprint<>p_fingerprint THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001'; END IF;
        RETURN previous.receipt||jsonb_build_object('replayed',true);
    END IF;
    PERFORM life_financial.guard_account(p_account,p_binding,p_owner,p_version); PERFORM life_financial.guard_spot_session(p_account);
    view:=life_financial.spot_state(p_account);
    IF view->>'digest' IS DISTINCT FROM p_cut THEN RAISE EXCEPTION 'LIFE_CAPITAL_CUT_STALE' USING ERRCODE='P0001'; END IF;
    IF (view->>'financial_recovery_required')::boolean OR (view->>'unverified')::boolean THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_FINANCIAL_RECOVERY_REQUIRED' USING ERRCODE='P0001'; END IF;
    SELECT p0.* INTO p FROM life_financial.shared_capital_policies p0 JOIN life_financial.accounts a0 USING(account_id,policy_hash) WHERE p0.account_id=p_account;
    IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_CAPITAL_POLICY_REQUIRED' USING ERRCODE='P0001'; END IF;
    at:=clock_timestamp();now_ms:=floor(extract(epoch FROM at)*1000)::bigint;
    SELECT * INTO h FROM life_financial.shared_capital_heads WHERE account_id=p_account;
    IF h.account_id IS NOT NULL AND at<h.last_checked_at THEN RAISE EXCEPTION 'LIFE_CAPITAL_CLOCK_ROLLBACK' USING ERRCODE='P0001'; END IF;
    IF NOT life_financial.swap_snapshot_current(p_account,snapshot) THEN RAISE EXCEPTION 'LIFE_SWAP_ACCOUNT_SNAPSHOT_MISMATCH' USING ERRCODE='P0001';END IF;
    IF NOT life_financial.shared_snapshot_valid(snapshot,p.body,now_ms,p.max_claims) THEN RAISE EXCEPTION 'LIFE_CAPITAL_SNAPSHOT_UNQUALIFIED' USING ERRCODE='P0001'; END IF;
    IF (snapshot->>'spot_life_base')::numeric<>(view#>>'{balances,LIFE,total}')::numeric
        OR (snapshot->>'spot_cash_quote')::numeric<>(view#>>'{balances,USDT,total}')::numeric THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_SPOT_BALANCE_MISMATCH' USING ERRCODE='P0001'; END IF;
    IF h.account_id IS NOT NULL AND ((snapshot->>'sequence')::bigint<h.sequence
        OR (snapshot->>'observed_at_ms')::bigint<(SELECT observed_at_ms FROM life_financial.shared_capital_observations WHERE account_id=p_account AND sequence=h.sequence)) THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_SNAPSHOT_REPLAY_OR_MUTATION' USING ERRCODE='P0001'; END IF;
    SELECT * INTO observed FROM life_financial.shared_capital_observations WHERE account_id=p_account AND sequence=(snapshot->>'sequence')::bigint;
    source_hash:=encode(sha256(convert_to(life_financial.canonical_json_v1(snapshot),'UTF8')),'hex');
    IF FOUND THEN
        IF observed.body<>snapshot OR observed.source_hash<>source_hash THEN RAISE EXCEPTION 'LIFE_CAPITAL_SNAPSHOT_REPLAY_OR_MUTATION' USING ERRCODE='P0001'; END IF;
    ELSE new_observation:=true; END IF;
    SELECT COALESCE(jsonb_agg(life_financial.shared_claim_body(c) ORDER BY c.intent_id),'[]'::jsonb) INTO orders FROM life_financial.shared_capital_claims c WHERE c.account_id=p_account;
    IF cmd->>'kind'='RESERVE' THEN
        IF NOT life_financial.shared_claim_valid(candidate,p.body) OR candidate->>'session_id'<>a.current_session::text
            OR (candidate->>'epoch')::bigint<>a.owner_epoch OR (candidate->>'risk_epoch')::bigint<>a.risk_epoch
            OR NOT EXISTS(SELECT 1 FROM life_financial.sessions WHERE account_id=p_account AND session_id=a.current_session AND config_version=(candidate->>'config_version')::bigint) THEN
            RAISE EXCEPTION 'LIFE_CAPITAL_CLAIM_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
        SELECT * INTO known FROM life_financial.shared_capital_claims WHERE account_id=p_account AND intent_id=candidate->>'intent_id';
        IF FOUND THEN
            IF NOT life_financial.shared_claim_equal(life_financial.shared_claim_body(known),candidate) OR NOT EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=p_account
                AND operation_id=known.operation_id AND command->'owner'=p_owner) THEN RAISE EXCEPTION 'LIFE_CAPITAL_CLAIM_CHANGED' USING ERRCODE='P0001'; END IF;
        ELSE
            IF jsonb_array_length(orders)>=p.max_claims THEN RAISE EXCEPTION 'LIFE_CAPITAL_CLAIM_BOUND' USING ERRCODE='P0001'; END IF;
            IF EXISTS(SELECT 1 FROM jsonb_array_elements(snapshot->'pending') c WHERE c->>'intent_id'=candidate->>'intent_id') THEN
                RAISE EXCEPTION 'LIFE_CAPITAL_UNOWNED_PENDING_ID' USING ERRCODE='P0001'; END IF;
            orders:=orders||jsonb_build_array(candidate);
        END IF;
        IF EXISTS(SELECT 1 FROM jsonb_array_elements(orders||(snapshot->'pending')) c WHERE c->>'session_id'<>a.current_session::text OR (c->>'epoch')::bigint<>a.owner_epoch) THEN
            RAISE EXCEPTION 'LIFE_CAPITAL_OLD_SESSION_CLAIMS' USING ERRCODE='P0001'; END IF;
    END IF;
    -- Native working orders cannot vanish from a caller-qualified account DTO.
    IF EXISTS(SELECT 1 FROM life_financial.send_attempts t WHERE t.account_id=p_account AND t.state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND') AND NOT EXISTS(
        SELECT 1 FROM jsonb_array_elements(orders||(snapshot->'pending')) c WHERE c->>'intent_id'=t.intent_id AND c->>'wire_id'=t.wire_id
            AND c->>'product'=t.product AND c->>'side'=t.side AND (c->>'quantity_base')::numeric=t.quantity AND (c->>'price_usdt')::numeric=t.price)) THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_NATIVE_ORDER_SCOPE_INCOMPLETE' USING ERRCODE='P0001'; END IF;
    decision:=life_financial.shared_evaluate(p.body,snapshot,orders);
    changed:=cmd->>'kind'='RESERVE' AND (decision->>'allowed')::boolean AND known.account_id IS NULL;
    IF new_observation THEN INSERT INTO life_financial.shared_capital_observations VALUES(p_account,(snapshot->>'sequence')::bigint,snapshot,(snapshot->>'observed_at_ms')::bigint,source_hash,op); END IF;
    IF changed THEN
        INSERT INTO life_financial.shared_capital_claims VALUES(p_account,candidate->>'intent_id',candidate->>'wire_id',candidate->>'account_uid',
            (candidate->>'session_id')::uuid,(candidate->>'epoch')::bigint,(candidate->>'config_version')::bigint,(candidate->>'risk_epoch')::bigint,
            candidate->>'product',candidate->>'side',candidate->>'position_action',(candidate->>'quantity_base')::numeric,(candidate->>'price_usdt')::numeric,(candidate->>'leverage')::integer,op);
    END IF;
    INSERT INTO life_financial.shared_capital_heads VALUES(p_account,(snapshot->>'sequence')::bigint,at,1,1,CASE WHEN changed THEN 1 ELSE 0 END,op)
        ON CONFLICT(account_id) DO UPDATE SET sequence=EXCLUDED.sequence,last_checked_at=EXCLUDED.last_checked_at,
            observation_count=life_financial.shared_capital_heads.observation_count+CASE WHEN new_observation THEN 1 ELSE 0 END,
            operation_count=life_financial.shared_capital_heads.operation_count+1,
            claim_count=life_financial.shared_capital_heads.claim_count+CASE WHEN changed THEN 1 ELSE 0 END,operation_id=op;
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    result:=jsonb_build_object('kind','SHARED_CAPITAL','operation_id',op,'version',p_version+1,'decision',decision,'changed',changed,'replayed',false,'trading_permission',false);
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version) VALUES(p_account,op,p_fingerprint,request,result,p_version+1);
    RETURN result;
END; $$;
CREATE OR REPLACE FUNCTION life_financial.carry_current_ready(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE h life_financial.carry_heads;r life_financial.carry_checks;op life_financial.operations;p life_financial.carry_policies;cp jsonb;previous jsonb;
BEGIN
    SELECT * INTO p FROM life_financial.carry_policies WHERE account_id=p_account;
    IF NOT FOUND THEN RETURN true; END IF;
    IF NOT life_financial.carry_verified(p_account) THEN RETURN false; END IF;
    SELECT * INTO STRICT h FROM life_financial.carry_heads WHERE account_id=p_account;
    SELECT * INTO STRICT r FROM life_financial.carry_checks WHERE account_id=p_account AND operation_id=h.operation_id;
    SELECT * INTO STRICT op FROM life_financial.operations WHERE account_id=p_account AND operation_id=h.operation_id;
    SELECT body INTO STRICT cp FROM life_financial.shared_capital_policies WHERE account_id=p_account;
    SELECT bundle INTO previous FROM life_financial.carry_checks WHERE account_id=p_account AND operation_id=r.previous_qualified;
    RETURN life_financial.swap_snapshot_current(p_account,r.bundle#>'{account,snapshot}') AND r.qualified AND op.receipt#>'{decision,allowed}'='true'::jsonb
        AND (r.bundle#>>'{account,snapshot,spot_life_base}')::numeric=(SELECT total FROM life_financial.balances WHERE account_id=p_account AND asset='LIFE')
        AND (r.bundle#>>'{account,snapshot,spot_cash_quote}')::numeric=(SELECT total FROM life_financial.balances WHERE account_id=p_account AND asset='USDT')
        AND r.capital_operation_id=(SELECT operation_id FROM life_financial.shared_capital_heads WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.shared_capital_claims c JOIN life_financial.operations o USING(account_id,operation_id)
            WHERE c.account_id=p_account AND o.committed_version>op.committed_version)
        AND life_financial.carry_bundle_reason(p.body,cp,r.bundle,previous,r.bundle#>'{account,snapshot}',
            floor(extract(epoch FROM clock_timestamp())*1000)::bigint,p.max_payments)='CARRY_QUALIFIED';
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
CREATE FUNCTION life_financial.swap_snapshot_attempt_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE body jsonb;
BEGIN
    IF NEW.state IN ('PREPARED','ARMED','SEND_UNKNOWN') THEN
        SELECT o.body INTO body FROM life_financial.shared_capital_heads h
            JOIN life_financial.shared_capital_observations o USING(account_id,sequence) WHERE h.account_id=NEW.account_id;
        IF NOT life_financial.swap_snapshot_current(NEW.account_id,body) THEN
            RAISE EXCEPTION 'LIFE_SWAP_ACCOUNT_SNAPSHOT_MISMATCH' USING ERRCODE='P0001';END IF;
    END IF;
    RETURN NEW;
END; $$;
CREATE TRIGGER swap_snapshot_before_attempt BEFORE INSERT OR UPDATE OF state ON life_financial.send_attempts
    FOR EACH ROW EXECUTE FUNCTION life_financial.swap_snapshot_attempt_guard();

-- SPOT-only NAV omits derivative unrealized PnL and is never a portfolio
-- measurement. Keep historical diagnostics, but require the joint NAV port
-- before a SWAP-enabled account can publish a new/current valuation.
CREATE FUNCTION life_financial.swap_requires_joint_nav() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF EXISTS(SELECT 1 FROM life_financial.swap_fill_policies WHERE account_id=NEW.account_id) THEN
        RAISE EXCEPTION 'LIFE_PORTFOLIO_NAV_REQUIRED' USING ERRCODE='P0001';END IF;
    RETURN NEW;
END; $$;
CREATE TRIGGER swap_disallows_spot_nav BEFORE INSERT ON life_financial.spot_nav_observations
    FOR EACH ROW EXECUTE FUNCTION life_financial.swap_requires_joint_nav();
CREATE OR REPLACE FUNCTION life_financial.spot_capital_read(p_account uuid,p_operation text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE n life_financial.spot_nav_observations; view jsonb; result jsonb; valid boolean:=false;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_ACCESS_DENIED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO n FROM life_financial.spot_nav_observations WHERE account_id=p_account
        AND (p_operation IS NULL OR operation_id=p_operation) ORDER BY sequence DESC LIMIT 1;
    IF NOT FOUND THEN RETURN NULL; END IF;
    view:=life_financial.spot_state(p_account);
    IF life_financial.spot_capital_verified(p_account) AND NOT (view->>'unverified')::boolean THEN
        valid:=NOT EXISTS(SELECT 1 FROM life_financial.swap_fill_policies WHERE account_id=p_account) AND n.money_digest=life_financial.spot_capital_digest(p_account)
            AND NOT EXISTS(SELECT 1 FROM life_financial.balances WHERE account_id=p_account AND funding_deficit>0);
    END IF;
    result:=jsonb_build_object('command',n.command,'measurement',n.measurement,'snapshot',view,
        'current',valid);
    RETURN result;
END; $$;

REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
