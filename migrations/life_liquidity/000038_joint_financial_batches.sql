-- Coupled observations stage original facts privately, then replay one causal
-- common wallet. No intermediate provider snapshot or positive proceeds is made up.
CREATE TABLE life_financial.joint_batch_policies (
    account_id uuid PRIMARY KEY REFERENCES life_financial.funding_accounting_policies,
    policy_hash life_financial.digest NOT NULL,
    max_members integer NOT NULL CHECK(max_members BETWEEN 1 AND 256),
    max_batches integer NOT NULL CHECK(max_batches BETWEEN 1 AND 4096),
    max_total_bytes integer NOT NULL CHECK(max_total_bytes BETWEEN 1 AND 16777216),
    FOREIGN KEY(account_id,policy_hash) REFERENCES life_financial.policies
);
CREATE TABLE life_financial.joint_financial_batches (
    account_id uuid NOT NULL REFERENCES life_financial.joint_batch_policies,
    operation_id life_financial.identity NOT NULL,command jsonb NOT NULL,
    state text NOT NULL CHECK(state IN ('APPLIED','RECOVERY_REQUIRED')),reason text,
    start_version bigint NOT NULL CHECK(start_version>=0),received_at_ms bigint NOT NULL CHECK(received_at_ms>0),
    money_before jsonb NOT NULL,money_after jsonb NOT NULL,members jsonb NOT NULL,
    PRIMARY KEY(account_id,operation_id),
    CHECK((state='APPLIED' AND reason IS NULL) OR (state='RECOVERY_REQUIRED' AND reason IS NOT NULL)),
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED
);
-- Transaction-local capability. No principal gets SELECT/DML/EXECUTE on this
-- table or stage functions. Deferred guard disallows a committed staging row.
CREATE TABLE life_financial.joint_batch_staging (
    account_id uuid PRIMARY KEY REFERENCES life_financial.accounts,
    transaction_id xid8 NOT NULL, members jsonb NOT NULL
);
DO $$ DECLARE t text;BEGIN
    FOREACH t IN ARRAY ARRAY['joint_batch_policies','joint_financial_batches'] LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY scoped_read ON life_financial.%I FOR SELECT USING(life_financial.can_access(account_id))',t);
        EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',t);
    END LOOP;
END; $$;
CREATE FUNCTION life_financial.joint_stage_guard(p_account uuid,p_kind text,cmd jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF p_kind IS NULL OR p_kind NOT IN ('SPOT_FILL','SWAP_FILL','SPOT_CASHFLOW','PAID_FUNDING')
        OR NOT EXISTS(SELECT 1 FROM life_financial.joint_batch_staging s CROSS JOIN LATERAL jsonb_array_elements(s.members) m
            WHERE s.account_id=p_account AND s.transaction_id=pg_current_xact_id() AND m->>'kind'=p_kind AND m->'command'=cmd) THEN
        RAISE EXCEPTION 'LIFE_JOINT_STAGE_CAPABILITY_INVALID' USING ERRCODE='P0001';END IF;
END; $$;
-- Reviewed private fact writers retain original validation, identity, fee,
-- reserve-progress and gross-budget semantics. Only global wallet/position
-- validation/publication is deferred to joint_batch_command below. Public
-- standalone functions, their source checks and permissions are unchanged.
CREATE FUNCTION life_financial.joint_stage_spot(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,p_kind text,p_command jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE a life_financial.accounts; d life_financial.spot_preparations; t life_financial.send_attempts;
    g life_financial.spot_accounting_progress; policy life_financial.spot_fill_policies; p life_financial.spot_policies;
    previous life_financial.operations; fact life_financial.spot_fill_facts; view jsonb; request jsonb; result jsonb; row jsonb;
    op life_financial.identity; trade life_financial.identity; seen text[] := ARRAY[]::text[];
    received timestamptz; received_ms numeric; source_at timestamptz; fill_ms numeric; value_ms numeric; observed_ms numeric;
    qty life_financial.positive_amount; price life_financial.positive_amount; reference life_financial.positive_amount;
    fee life_financial.signed_amount; currency text; loss life_financial.amount; hash text;
    sum_qty numeric := 0; cumulative numeric; delta_base numeric := 0; delta_quote numeric := 0;
    new_paid_base numeric; new_paid_quote numeric; new_loss numeric; floor_delta numeric; scope_name text; key text; pending_reason text;
BEGIN
    PERFORM life_financial.joint_stage_guard(p_account,p_kind,p_command);
    IF NOT life_financial.can_access(p_account,true) OR p_kind NOT IN ('SPOT_ACK','SPOT_FILL') OR p_kind IS NULL OR p_fingerprint IS NULL OR p_cut IS NULL
        OR p_command IS NULL OR jsonb_typeof(p_command)<>'object' OR octet_length(p_command::text)>1048576
        OR NOT p_command ?& ARRAY['operation_id','prepare_operation_id','intent_id','client_order_id','market','side','session_id','epoch','exchange_order_id','observed_at']
        OR EXISTS(SELECT 1 FROM jsonb_each(p_command) WHERE value='null'::jsonb)
        OR (SELECT count(*) FROM jsonb_object_keys(p_command))<>(CASE WHEN p_kind='SPOT_ACK' THEN 10 ELSE 12 END) THEN
        RAISE EXCEPTION 'LIFE_OBSERVATION_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    op := p_command->>'operation_id'; received := (p_command->>'observed_at')::timestamptz;
    IF length(op)>96 OR p_command->>'exchange_order_id' !~ '^[0-9]{1,128}$' OR NOT isfinite(received) THEN
        RAISE EXCEPTION 'LIFE_OBSERVATION_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,a.version);
    SELECT * INTO STRICT d FROM life_financial.spot_preparations WHERE account_id=p_account AND operation_id=p_command->>'prepare_operation_id';
    SELECT * INTO STRICT t FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=d.intent_id;
    IF t.intent_id<>p_command->>'intent_id' OR t.wire_id<>p_command->>'client_order_id' OR t.product<>'SPOT'
        OR t.side<>p_command->>'side' OR p_command->>'market'<>'LIFE-USDT'
        OR t.session_id<>(p_command->>'session_id')::uuid OR t.epoch<>(p_command->>'epoch')::bigint
        OR t.process_id<>a.owner_process OR t.generation<>a.owner_generation OR t.epoch<>a.owner_epoch OR t.session_id<>a.current_session
        OR t.state NOT IN ('SEND_UNKNOWN','ACKED')
        OR t.exchange_order_id IS NOT NULL AND t.exchange_order_id<>p_command->>'exchange_order_id'
        OR received<d.prepared_at OR received>clock_timestamp() THEN
        RAISE EXCEPTION 'LIFE_OBSERVATION_ORIGINAL_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT * INTO g FROM life_financial.spot_accounting_progress WHERE account_id=p_account AND intent_id=t.intent_id;
    IF FOUND AND g.observed_order_id IS NOT NULL AND g.observed_order_id<>p_command->>'exchange_order_id' THEN
        RAISE EXCEPTION 'LIFE_OBSERVATION_ORDER_ID_CONFLICT' USING ERRCODE='P0001'; END IF;
    IF EXISTS(SELECT 1 FROM life_financial.spot_pending_observations WHERE account_id=p_account AND intent_id=t.intent_id
        AND exchange_order_id<>p_command->>'exchange_order_id') THEN
        RAISE EXCEPTION 'LIFE_OBSERVATION_ORDER_ID_CONFLICT' USING ERRCODE='P0001'; END IF;
    IF EXISTS(SELECT 1 FROM life_financial.send_attempts WHERE account_id=p_account AND product='SPOT'
        AND exchange_order_id=p_command->>'exchange_order_id' AND intent_id<>t.intent_id)
        OR EXISTS(SELECT 1 FROM life_financial.spot_accounting_progress WHERE account_id=p_account
            AND observed_order_id=p_command->>'exchange_order_id' AND intent_id<>t.intent_id)
        OR EXISTS(SELECT 1 FROM life_financial.spot_pending_observations WHERE account_id=p_account
            AND exchange_order_id=p_command->>'exchange_order_id' AND intent_id<>t.intent_id) THEN
        RAISE EXCEPTION 'LIFE_OBSERVATION_ORDER_ID_CONFLICT' USING ERRCODE='P0001'; END IF;
    request := jsonb_build_object('kind',p_kind,'binding',p_binding,'owner',p_owner,'command',p_command);
    SELECT * INTO previous FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF previous.command<>request OR previous.fingerprint<>p_fingerprint THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001'; END IF;
        RETURN previous.receipt||'{"replayed":true}'::jsonb;
    END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    INSERT INTO life_financial.spot_accounting_progress VALUES(p_account,t.intent_id,0,0,0,0,NULL,0) ON CONFLICT DO NOTHING;
    SELECT * INTO STRICT g FROM life_financial.spot_accounting_progress WHERE account_id=p_account AND intent_id=t.intent_id FOR UPDATE;
    IF p_kind='SPOT_ACK' THEN
        UPDATE life_financial.send_attempts SET state='ACKED',exchange_order_id=p_command->>'exchange_order_id',version=version+1 WHERE account_id=p_account AND intent_id=t.intent_id;
        UPDATE life_financial.spot_accounting_progress SET observed_order_id=p_command->>'exchange_order_id',version=version+1 WHERE account_id=p_account AND intent_id=t.intent_id;
        INSERT INTO life_financial.financial_events(account_id,event_id,product,kind,exchange_identity,occurred_at,source_hash,base_delta,quote_delta)
            VALUES(p_account,'ack:'||t.wire_id,'SPOT','ACK',p_command->>'exchange_order_id',received,
                encode(sha256(convert_to((p_command-ARRAY['operation_id','observed_at'])::text,'UTF8')),'hex'),0,0) ON CONFLICT DO NOTHING;
        result := jsonb_build_object('kind',p_kind,'operation_id',op,'intent_id',t.intent_id,'exchange_order_id',p_command->>'exchange_order_id','state','ACKED');
    ELSE
      BEGIN
        SELECT * INTO STRICT policy FROM life_financial.spot_fill_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
        IF NOT p_command ?& ARRAY['fills','cumulative_base_life'] OR jsonb_typeof(p_command->'fills')<>'array'
            OR jsonb_array_length(p_command->'fills') NOT BETWEEN 1 AND policy.max_snapshot_fills THEN
            RAISE EXCEPTION 'LIFE_FILL_SNAPSHOT_INVALID' USING ERRCODE='P0001'; END IF;
        cumulative := (p_command->>'cumulative_base_life')::life_financial.positive_amount;
        IF cumulative<g.filled_base OR cumulative>t.quantity THEN RAISE EXCEPTION 'LIFE_FILL_CUMULATIVE_INVALID' USING ERRCODE='P0001'; END IF;
        received_ms := floor(extract(epoch FROM received)*1000);
        new_paid_base := g.paid_base; new_paid_quote := g.paid_quote; new_loss := g.execution_loss;
        FOR row IN SELECT value FROM jsonb_array_elements(p_command->'fills') ORDER BY (value->>'fill_at_ms')::bigint,value->>'trade_id' LOOP
            IF jsonb_typeof(row)<>'object' OR (SELECT count(*) FROM jsonb_object_keys(row))<>10
                OR NOT row ?& ARRAY['trade_id','quantity_base_life','price_usdt','fee_currency','signed_fee','fill_at_ms','independent_price_usdt','independent_value_at_ms','independent_observed_at_ms','source_kind']
                OR EXISTS(SELECT 1 FROM jsonb_each(row) WHERE value='null'::jsonb) THEN
                RAISE EXCEPTION 'LIFE_FILL_FACT_INVALID' USING ERRCODE='P0001'; END IF;
            trade := row->>'trade_id'; qty := (row->>'quantity_base_life')::numeric; price := (row->>'price_usdt')::numeric;
            reference := (row->>'independent_price_usdt')::numeric; fee := (row->>'signed_fee')::numeric; currency := row->>'fee_currency';
            fill_ms := (row->>'fill_at_ms')::bigint; value_ms := (row->>'independent_value_at_ms')::bigint; observed_ms := (row->>'independent_observed_at_ms')::bigint;
            IF trade=ANY(seen) OR currency NOT IN ('LIFE','USDT') OR row->>'source_kind'<>'independent_market'
                OR fill_ms<floor(extract(epoch FROM d.prepared_at)*1000) OR fill_ms>received_ms
                OR fill_ms<=0 OR value_ms<=0 OR observed_ms<=0 OR greatest(fill_ms,value_ms,observed_ms)>253402300799999
                OR observed_ms>received_ms OR observed_ms<value_ms OR abs(value_ms-fill_ms)>policy.max_reference_skew_ms
                OR abs(observed_ms-fill_ms)>policy.max_reference_skew_ms
                OR t.side='BUY' AND price>t.price OR t.side='SELL' AND price<t.price THEN
                RAISE EXCEPTION 'LIFE_FILL_OBSERVATION_UNQUALIFIED' USING ERRCODE='P0001'; END IF;
            seen := array_append(seen,trade); sum_qty := sum_qty+qty;
            IF EXISTS(SELECT 1 FROM life_financial.spot_pending_observations pending
                CROSS JOIN LATERAL jsonb_array_elements(pending.command->'fills') prior
                WHERE pending.account_id=p_account AND prior->>'trade_id'=trade
                AND (pending.intent_id<>t.intent_id OR pending.exchange_order_id<>p_command->>'exchange_order_id' OR prior<>row)) THEN
                RAISE EXCEPTION 'LIFE_FILL_IDENTITY_CONFLICT' USING ERRCODE='P0001'; END IF;
            SELECT * INTO fact FROM life_financial.spot_fill_facts WHERE account_id=p_account AND trade_id=trade;
            IF FOUND THEN
                IF fact.intent_id<>t.intent_id OR fact.exchange_order_id<>p_command->>'exchange_order_id' OR fact.observation<>row THEN
                    RAISE EXCEPTION 'LIFE_FILL_IDENTITY_CONFLICT' USING ERRCODE='P0001'; END IF;
                CONTINUE;
            END IF;
            hash := encode(sha256(convert_to(row::text,'UTF8')),'hex');
            source_at := to_timestamp(fill_ms/1000);
            loss := greatest(0,-fee*CASE WHEN currency='LIFE' THEN reference ELSE 1 END
                -CASE WHEN t.side='BUY' THEN 1 ELSE -1 END*(reference-price)*qty);
            INSERT INTO life_financial.fills VALUES(p_account,'SPOT',trade,t.wire_id,qty,price,reference,source_at,to_timestamp(value_ms/1000),hash);
            INSERT INTO life_financial.fees VALUES(p_account,'fee:'||encode(sha256(convert_to(trade,'UTF8')),'hex'),'SPOT',trade,currency,fee);
            INSERT INTO life_financial.spot_fill_facts VALUES(p_account,trade,'SPOT',t.intent_id,p_command->>'exchange_order_id',row,loss,op);
            INSERT INTO life_financial.financial_events(account_id,event_id,product,kind,exchange_identity,occurred_at,source_hash,base_delta,quote_delta)
                VALUES(p_account,'fill:'||encode(sha256(convert_to(trade,'UTF8')),'hex'),'SPOT','FILL',trade,source_at,hash,
                    CASE WHEN t.side='BUY' THEN qty ELSE -qty END+CASE WHEN currency='LIFE' THEN fee ELSE 0 END,
                    CASE WHEN t.side='BUY' THEN -qty*price ELSE qty*price END+CASE WHEN currency='USDT' THEN fee ELSE 0 END);
            delta_base := delta_base+CASE WHEN t.side='BUY' THEN qty ELSE -qty END+CASE WHEN currency='LIFE' THEN fee ELSE 0 END;
            delta_quote := delta_quote+CASE WHEN t.side='BUY' THEN -qty*price ELSE qty*price END+CASE WHEN currency='USDT' THEN fee ELSE 0 END;
            new_paid_base := new_paid_base+CASE WHEN currency='LIFE' THEN greatest(0,-fee) ELSE 0 END;
            new_paid_quote := new_paid_quote+CASE WHEN currency='USDT' THEN greatest(0,-fee) ELSE 0 END;
            new_loss := new_loss+loss;
            PERFORM life_financial.spot_charge_loss(p_account,t.session_id,source_at,trade,loss);
        END LOOP;
        IF sum_qty<>cumulative OR EXISTS(SELECT 1 FROM life_financial.spot_fill_facts WHERE account_id=p_account AND intent_id=t.intent_id AND NOT trade_id=ANY(seen)) THEN
            RAISE EXCEPTION 'LIFE_FILL_SNAPSHOT_INCOMPLETE' USING ERRCODE='P0001'; END IF;
        IF EXISTS(SELECT 1 FROM life_financial.spot_pending_observations WHERE account_id=p_account AND intent_id=t.intent_id) THEN
            pending_reason := 'PRIOR_OBSERVATION_PENDING';
        END IF;
        IF pending_reason IS NOT NULL THEN RAISE EXCEPTION 'LIFE_OBSERVATION_PENDING' USING ERRCODE='PZ001'; END IF;
        UPDATE life_financial.spot_accounting_progress SET filled_base=cumulative,paid_base=new_paid_base,paid_quote=new_paid_quote,
            execution_loss=new_loss,observed_order_id=p_command->>'exchange_order_id',version=version+1 WHERE account_id=p_account AND intent_id=t.intent_id;
        UPDATE life_financial.reservations SET remaining=CASE
            WHEN reservation_id=d.operation_id||':principal' THEN (t.quantity-cumulative)*CASE WHEN t.side='BUY' THEN t.price ELSE 1 END
            WHEN asset='LIFE' THEN greatest(0,d.fee_base-new_paid_base) ELSE greatest(0,d.fee_quote-new_paid_quote) END,version=version+1
            WHERE account_id=p_account AND intent_id=t.intent_id AND state='HELD';
        SELECT * INTO STRICT p FROM life_financial.spot_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
        floor_delta := greatest(d.subsidy,new_loss)-greatest(d.subsidy,g.execution_loss);
        FOREACH scope_name IN ARRAY ARRAY['CAMPAIGN','DAY','SESSION'] LOOP
            key := CASE scope_name WHEN 'CAMPAIGN' THEN p.campaign_id WHEN 'DAY' THEN to_char(d.prepared_at AT TIME ZONE 'UTC','YYYY-MM-DD') ELSE t.session_id::text END;
            UPDATE life_financial.budget_buckets SET committed=committed+floor_delta,version=version+1
                WHERE account_id=p_account AND family='SUBSIDY' AND scope=scope_name AND window_key=key;
            INSERT INTO life_financial.budget_events VALUES(p_account,op||':floor:'||scope_name,'SUBSIDY',scope_name,key,'COMMIT',floor_delta);
        END LOOP;
        IF new_paid_base>d.fee_base OR new_paid_quote>d.fee_quote OR EXISTS(SELECT 1 FROM life_financial.balances WHERE account_id=p_account AND funding_deficit>0) THEN
            UPDATE life_financial.accounts SET halted=true WHERE account_id=p_account;
        END IF;
        result := jsonb_build_object('kind',p_kind,'operation_id',op,'intent_id',t.intent_id,'state','APPLIED','cumulative_base_life',cumulative::text,'execution_loss_usdt',new_loss::text);
      EXCEPTION WHEN SQLSTATE 'PZ001' THEN
        -- This subtransaction rolls back every provisional fill/fee/budget write.
        -- Retain the qualified raw facts and a durable HALT without inventing debt,
        -- changing spendable money or claiming financial application succeeded.
        INSERT INTO life_financial.spot_pending_observations VALUES(p_account,op,t.intent_id,p_command->>'exchange_order_id',
            p_command,pending_reason,encode(sha256(convert_to(p_command::text,'UTF8')),'hex'));
        UPDATE life_financial.accounts SET halted=true WHERE account_id=p_account;
        result := jsonb_build_object('kind',p_kind,'operation_id',op,'intent_id',t.intent_id,'state','RECOVERY_REQUIRED',
            'cumulative_base_life',cumulative::text,'execution_loss_usdt',g.execution_loss::text);
      END;
    END IF;
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    result := result||jsonb_build_object('version',p_version+1,'replayed',false,'trading_permission',false);
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version) VALUES(p_account,op,p_fingerprint,request,result,p_version+1);
    RETURN result;
END; $$;


CREATE FUNCTION life_financial.joint_stage_swap(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,cmd jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE a life_financial.accounts;p life_financial.swap_fill_policies;d life_financial.swap_preparations;t life_financial.send_attempts;
    old life_financial.operations;known life_financial.swap_fill_facts;g life_financial.swap_fill_progress;
    request jsonb;result jsonb;view jsonb;j jsonb;k text;r jsonb;op text;trade text;hash text;reason text;received timestamptz;received_ms bigint;
    quantity numeric;fee numeric;pnl numeric;loss numeric;total_quantity numeric:=0;delta_quote numeric:=0;paid numeric:=0;execution_loss numeric:=0;
    seen text[]:=ARRAY[]::text[];at timestamptz;
BEGIN
    PERFORM life_financial.joint_stage_guard(p_account,'SWAP_FILL',cmd);
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
    SELECT * INTO old FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF old.command<>request OR old.fingerprint<>p_fingerprint THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001';END IF;
        RETURN old.receipt||'{"replayed":true}'::jsonb;END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    SELECT * INTO STRICT p FROM life_financial.swap_fill_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
    IF (SELECT count(*) FROM life_financial.operations WHERE account_id=p_account AND command->>'kind'='SWAP_FILL')>=p.max_observations THEN
        RAISE EXCEPTION 'LIFE_SWAP_FILL_OBSERVATION_BOUND' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT d FROM life_financial.swap_preparations WHERE account_id=p_account AND operation_id=cmd->>'prepare_operation_id';
    SELECT * INTO STRICT t FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=d.intent_id;
    received:=(cmd->>'observed_at')::timestamptz;received_ms:=floor(extract(epoch FROM received)*1000)::bigint;
    IF NOT isfinite(received) OR received>clock_timestamp() OR received_ms<d.prepared_at_ms
        OR t.product<>'SWAP' OR t.state NOT IN ('SEND_UNKNOWN','ACKED','TERMINAL') OR t.intent_id<>cmd->>'intent_id' OR t.wire_id<>cmd->>'client_order_id'
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
        IF t.state='TERMINAL' THEN RAISE EXCEPTION 'LIFE_SWAP_TERMINAL_RECONCILIATION_REQUIRED' USING ERRCODE='P0001';END IF;
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
        IF EXISTS(SELECT 1 FROM life_financial.swap_pending_fills WHERE account_id=p_account AND intent_id=t.intent_id) THEN
            RAISE EXCEPTION 'LIFE_SWAP_PRIOR_FILL_PENDING' USING ERRCODE='P0001';END IF;
        INSERT INTO life_financial.swap_fill_progress VALUES(p_account,t.intent_id,total_quantity,paid,execution_loss,cmd->>'exchange_order_id',1)
            ON CONFLICT(account_id,intent_id) DO UPDATE SET filled_base=EXCLUDED.filled_base,paid_fees_usdt=EXCLUDED.paid_fees_usdt,
                execution_loss_usdt=EXCLUDED.execution_loss_usdt,version=life_financial.swap_fill_progress.version+1;
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


CREATE FUNCTION life_financial.joint_stage_funding(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,cmd jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE account_row life_financial.accounts;policy_row life_financial.funding_accounting_policies;old_operation life_financial.operations;
    prior_payment life_financial.funding_cash_payments;previous_funding text;previous_carry text;op text;request jsonb;result jsonb;
    b jsonb;payment jsonb;snapshot jsonb;view jsonb;reason text;result_state text:='APPLIED';at_ms bigint;opening_ms bigint;
    delta numeric:=0;debits numeric:=0;source_hash text;wallet numeric;source_time timestamptz;
BEGIN
    PERFORM life_financial.joint_stage_guard(p_account,'PAID_FUNDING',cmd);
    IF NOT life_financial.can_access(p_account,true) OR jsonb_typeof(cmd) IS DISTINCT FROM 'object'
        OR (SELECT count(*) FROM jsonb_object_keys(cmd))<>2 OR NOT cmd ?& ARRAY['operation_id','bundle'] OR octet_length(cmd::text)>1048576
        OR jsonb_typeof(cmd->'operation_id') IS DISTINCT FROM 'string' OR cmd->>'operation_id' !~ '^[A-Za-z0-9_.:-]{1,96}$' THEN
        RAISE EXCEPTION 'LIFE_FUNDING_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
    op:=cmd->>'operation_id';b:=cmd->'bundle';snapshot:=b#>'{account,snapshot}';
    request:=jsonb_build_object('kind','PAID_FUNDING','binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',cmd);
    IF p_fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(request),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'LIFE_FUNDING_FINGERPRINT_INVALID' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT account_row FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,account_row.version);
    SELECT * INTO old_operation FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF old_operation.command<>request OR old_operation.fingerprint<>p_fingerprint THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001';END IF;
        RETURN old_operation.receipt||'{"replayed":true}'::jsonb;END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    SELECT * INTO STRICT policy_row FROM life_financial.funding_accounting_policies WHERE account_id=p_account AND policy_hash=account_row.policy_hash;
    IF (SELECT count(*) FROM life_financial.funding_observations WHERE account_id=p_account)>=policy_row.max_observations
        OR (SELECT COALESCE(sum(octet_length(bundle::text)),0) FROM life_financial.funding_observations WHERE account_id=p_account)+octet_length(b::text)>policy_row.max_total_bytes THEN
        RAISE EXCEPTION 'LIFE_FUNDING_OBSERVATION_BOUND' USING ERRCODE='P0001';END IF;
    SELECT obs.operation_id INTO previous_funding FROM life_financial.funding_observations obs JOIN life_financial.operations src USING(account_id,operation_id)
        WHERE obs.account_id=p_account AND obs.state='APPLIED' ORDER BY src.committed_version DESC LIMIT 1;
    SELECT qualified_operation_id INTO previous_carry FROM life_financial.carry_heads WHERE account_id=p_account;
    SELECT floor(extract(epoch FROM opened_at)*1000)::bigint INTO STRICT opening_ms FROM life_financial.spot_capital_policies WHERE account_id=p_account;
    source_time:=clock_timestamp();at_ms:=floor(extract(epoch FROM source_time)*1000)::bigint;
    BEGIN
        reason:=life_financial.funding_bundle_reason(p_account,b,previous_funding,previous_carry,at_ms);
        IF reason<>'FUNDING_QUALIFIED' THEN RAISE EXCEPTION 'LIFE_FUNDING_SOURCE_UNQUALIFIED' USING ERRCODE='PZ001';END IF;
        FOR payment IN SELECT value FROM jsonb_array_elements(b#>'{funding,payments}') LOOP
            debits:=debits+greatest(0,-(payment->>'signed_payment_quote')::numeric);
            IF (payment->>'settled_at_ms')::bigint<opening_ms THEN CONTINUE;END IF;
            SELECT * INTO prior_payment FROM life_financial.funding_cash_payments WHERE account_id=p_account AND bill_id=payment->>'bill_id';
            IF FOUND THEN
                IF prior_payment.settled_at_ms<>(payment->>'settled_at_ms')::bigint OR prior_payment.signed_payment_usdt<>(payment->>'signed_payment_quote')::numeric THEN
                    reason:='FUNDING_BILL_IDENTITY_CONFLICT';RAISE EXCEPTION 'invalid' USING ERRCODE='PZ001';END IF;
            ELSE delta:=delta+(payment->>'signed_payment_quote')::numeric;END IF;
        END LOOP;
        IF EXISTS(SELECT 1 FROM life_financial.funding_cash_payments pay WHERE pay.account_id=p_account AND NOT EXISTS(
            SELECT 1 FROM jsonb_array_elements(b#>'{funding,payments}') item WHERE item->>'bill_id'=pay.bill_id
                AND (item->>'settled_at_ms')::bigint=pay.settled_at_ms AND (item->>'signed_payment_quote')::numeric=pay.signed_payment_usdt)) THEN
            reason:='FUNDING_HISTORY_INCOMPLETE';RAISE EXCEPTION 'invalid' USING ERRCODE='PZ001';END IF;
        IF EXISTS(SELECT 1 FROM life_financial.funding_observations WHERE account_id=p_account AND state='RECOVERY_REQUIRED') THEN
            reason:='FUNDING_PRIOR_PENDING';RAISE EXCEPTION 'invalid' USING ERRCODE='PZ001';END IF;
        INSERT INTO life_financial.funding_observations VALUES(p_account,op,b,previous_funding,previous_carry,at_ms,'APPLIED',NULL,delta,debits);
        FOR payment IN SELECT value FROM jsonb_array_elements(b#>'{funding,payments}') LOOP
            IF (payment->>'settled_at_ms')::bigint<opening_ms OR EXISTS(SELECT 1 FROM life_financial.funding_cash_payments
                WHERE account_id=p_account AND bill_id=payment->>'bill_id') THEN CONTINUE;END IF;
            source_hash:=encode(sha256(convert_to(life_financial.canonical_json_v1(payment),'UTF8')),'hex');
            INSERT INTO life_financial.funding_cash_payments VALUES(p_account,payment->>'bill_id',(payment->>'settled_at_ms')::bigint,
                (payment->>'signed_payment_quote')::numeric,op,source_hash);
            INSERT INTO life_financial.financial_events(account_id,event_id,product,kind,exchange_identity,occurred_at,source_hash,base_delta,quote_delta)
                VALUES(p_account,'funding:'||encode(sha256(convert_to(payment->>'bill_id','UTF8')),'hex'),'ACCOUNT','FUNDING',payment->>'bill_id',
                    to_timestamp((payment->>'settled_at_ms')::bigint/1000.0),source_hash,0,(payment->>'signed_payment_quote')::numeric);
        END LOOP;
        IF debits>0 AND debits>=(SELECT (body->>'funding_budget_quote')::numeric FROM life_financial.carry_policies WHERE account_id=p_account)
            OR EXISTS(SELECT 1 FROM life_financial.balances WHERE account_id=p_account AND funding_deficit>0) THEN
            UPDATE life_financial.accounts SET halted=true WHERE account_id=p_account;END IF;
        reason:=NULL;
    EXCEPTION WHEN SQLSTATE 'PZ001' OR raise_exception OR check_violation OR numeric_value_out_of_range OR unique_violation THEN
        IF reason IS NULL OR reason='FUNDING_QUALIFIED' THEN reason:='FUNDING_FINANCIAL_CONSTRAINT_REJECTED';END IF;
        result_state:='RECOVERY_REQUIRED';delta:=0;debits:=0;
        INSERT INTO life_financial.funding_observations VALUES(p_account,op,b,previous_funding,previous_carry,at_ms,result_state,reason,0,0);
        UPDATE life_financial.accounts SET halted=true WHERE account_id=p_account;
    END;
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    result:=jsonb_build_object('kind','PAID_FUNDING','operation_id',op,'state',result_state,'cash_delta_usdt',delta::text,'gross_debits_usdt',debits::text,
        'version',p_version+1,'replayed',false,'trading_permission',false);
    IF reason IS NOT NULL THEN result:=result||jsonb_build_object('reason',reason);END IF;
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version,recorded_at)
        VALUES(p_account,op,p_fingerprint,request,result,p_version+1,source_time);
    RETURN result;
END; $$;


CREATE FUNCTION life_financial.joint_stage_cashflow(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,p_kind text,p_command jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE a life_financial.accounts; p life_financial.spot_capital_policies; fp life_financial.spot_fill_policies;
    previous life_financial.operations; b life_financial.bill_approvals; fact life_financial.spot_cashflow_facts;
    old_nav life_financial.spot_nav_observations; request jsonb; result jsonb; view jsonb; r jsonb; measurement jsonb;
    op life_financial.identity; bill life_financial.identity; observed timestamptz; at_ms bigint; value_ms bigint; ref_ms bigint;
    reference life_financial.positive_amount; amount life_financial.signed_amount; state text:='APPLIED';
    stable jsonb; nav numeric; adjusted numeric; highwater numeric; drawdown numeric; inventory numeric;
    reason text;
BEGIN
    PERFORM life_financial.joint_stage_guard(p_account,p_kind,p_command);
    IF NOT life_financial.can_access(p_account,true) OR p_fingerprint IS NULL OR p_cut IS NULL OR p_command IS NULL
        OR p_kind IS NULL OR p_kind NOT IN ('SPOT_CASHFLOW','SPOT_NAV') OR jsonb_typeof(p_command)<>'object' OR octet_length(p_command::text)>16384
        OR EXISTS(SELECT 1 FROM jsonb_each(p_command) WHERE value='null'::jsonb) THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    IF p_kind='SPOT_CASHFLOW' AND ((SELECT count(*) FROM jsonb_object_keys(p_command))<>10 OR NOT p_command ?& ARRAY[
        'operation_id','bill_id','currency','signed_amount','transfer_at_ms','independent_price_usdt','value_at_ms','reference_observed_at_ms','source_kind','observed_at'])
        OR p_kind='SPOT_NAV' AND ((SELECT count(*) FROM jsonb_object_keys(p_command))<>6 OR NOT p_command ?& ARRAY[
        'operation_id','independent_price_usdt','value_at_ms','reference_observed_at_ms','source_kind','observed_at']) THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_MODE_UNSUPPORTED' USING ERRCODE='P0001'; END IF;
    op:=p_command->>'operation_id'; IF length(op)>96 THEN RAISE EXCEPTION 'LIFE_CAPITAL_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,a.version);
    request:=jsonb_build_object('kind',p_kind,'binding',p_binding,'owner',p_owner,'command',p_command);
    SELECT * INTO previous FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF previous.command<>request OR previous.fingerprint<>p_fingerprint THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001'; END IF;
        RETURN previous.receipt||'{"replayed":true}'::jsonb;
    END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    SELECT * INTO STRICT p FROM life_financial.spot_capital_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
    SELECT * INTO STRICT fp FROM life_financial.spot_fill_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
    observed:=(p_command->>'observed_at')::timestamptz;
    value_ms:=(p_command->>'value_at_ms')::bigint; ref_ms:=(p_command->>'reference_observed_at_ms')::bigint;
    reference:=(p_command->>'independent_price_usdt')::numeric;
    IF NOT isfinite(observed) OR observed>clock_timestamp() OR value_ms<=0 OR ref_ms<value_ms
        OR ref_ms>floor(extract(epoch FROM observed)*1000) THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_VALUE_UNQUALIFIED' USING ERRCODE='P0001'; END IF;
    IF p_kind='SPOT_CASHFLOW' THEN
        bill:=p_command->>'bill_id'; amount:=(p_command->>'signed_amount')::numeric; at_ms:=(p_command->>'transfer_at_ms')::bigint;
        SELECT * INTO b FROM life_financial.bill_approvals WHERE account_id=p_account AND bill_id=bill;
        IF NOT FOUND OR bill !~ '^[1-9][0-9]{0,63}$' OR bill::numeric<=p.anchor_bill::numeric OR amount=0
            OR octet_length(b.signature)=0 OR b.asset<>p_command->>'currency' OR b.signed_amount<>amount
            OR floor(extract(epoch FROM b.transfer_at)*1000)<>at_ms OR b.transfer_at<p.opened_at THEN
            RAISE EXCEPTION 'LIFE_CASHFLOW_UNAPPROVED' USING ERRCODE='P0001'; END IF;
        IF at_ms>floor(extract(epoch FROM observed)*1000) OR abs(value_ms-at_ms)>fp.max_reference_skew_ms
            OR abs(ref_ms-at_ms)>fp.max_reference_skew_ms
            OR b.asset='USDT' AND (reference<>1 OR p_command->>'source_kind'<>'quote_unit')
            OR b.asset='LIFE' AND p_command->>'source_kind'<>'independent_market' THEN
            RAISE EXCEPTION 'LIFE_CAPITAL_VALUE_UNQUALIFIED' USING ERRCODE='P0001'; END IF;
        stable:=p_command-ARRAY['operation_id','observed_at'];
        SELECT * INTO fact FROM life_financial.spot_cashflow_facts WHERE account_id=p_account AND bill_id=bill;
        IF FOUND THEN
            IF fact.command-ARRAY['operation_id','observed_at']<>stable THEN RAISE EXCEPTION 'LIFE_CASHFLOW_IDENTITY_CONFLICT' USING ERRCODE='P0001'; END IF;
            state:=fact.state;
        ELSE
          BEGIN
            INSERT INTO life_financial.spot_cashflow_facts VALUES(p_account,bill,p_command,'APPLIED',NULL,op,p_fingerprint);
            INSERT INTO life_financial.cashflows VALUES(p_account,bill,reference,amount*reference,p_fingerprint);
            INSERT INTO life_financial.financial_events(account_id,event_id,product,kind,exchange_identity,occurred_at,source_hash,base_delta,quote_delta)
                VALUES(p_account,op||':cash','ACCOUNT','CASHFLOW',bill,b.transfer_at,p_fingerprint,
                    CASE WHEN b.asset='LIFE' THEN amount ELSE 0 END,CASE WHEN b.asset='USDT' THEN amount ELSE 0 END);
          EXCEPTION WHEN raise_exception THEN
            GET STACKED DIAGNOSTICS reason=MESSAGE_TEXT;
            IF reason NOT IN ('LIFE_CAPITAL_CAUSAL_INVALID','LIFE_CAPITAL_REPLAY_BOUND') THEN RAISE; END IF;
            INSERT INTO life_financial.spot_cashflow_facts VALUES(p_account,bill,p_command,'RECOVERY_REQUIRED',reason,op,p_fingerprint);
            state:='RECOVERY_REQUIRED';
          END;
        END IF;
        IF state<>'APPLIED' OR EXISTS(SELECT 1 FROM life_financial.balances WHERE account_id=p_account AND funding_deficit>0) THEN
            UPDATE life_financial.accounts SET halted=true WHERE account_id=p_account;
        END IF;
        result:=jsonb_build_object('bill_id',bill,'state',state);
    ELSE
        IF p_command->>'source_kind'<>'independent_market'
            OR abs(floor(extract(epoch FROM clock_timestamp())*1000)-value_ms)>fp.max_reference_skew_ms
            OR abs(floor(extract(epoch FROM clock_timestamp())*1000)-ref_ms)>fp.max_reference_skew_ms
            OR NOT life_financial.spot_capital_verified(p_account) OR (view->>'unverified')::boolean THEN
            RAISE EXCEPTION 'LIFE_NAV_UNQUALIFIED' USING ERRCODE='P0001'; END IF;
        SELECT * INTO old_nav FROM life_financial.spot_nav_observations WHERE account_id=p_account ORDER BY sequence DESC LIMIT 1;
        IF FOUND AND (observed<(old_nav.command->>'observed_at')::timestamptz
            OR value_ms<(old_nav.command->>'value_at_ms')::bigint OR ref_ms<(old_nav.command->>'reference_observed_at_ms')::bigint) THEN
            RAISE EXCEPTION 'LIFE_NAV_REGRESSION' USING ERRCODE='P0001'; END IF;
        r:=life_financial.spot_capital_replay(p_account);
        nav:=(r->>'quote')::numeric+(r->>'base')::numeric*reference; adjusted:=nav-(r->>'net')::numeric;
        highwater:=greatest((r->>'opening_nav')::numeric,COALESCE(old_nav.highwater,0),adjusted);
        -- Match the existing 28-significant-digit Decimal drawdown contract.
        drawdown:=greatest(0,highwater-adjusted)::numeric(200,140)/highwater*10000;
        drawdown:=life_financial.spot_drawdown_round(drawdown);
        inventory:=(r->>'initial')::numeric*(reference-(r->>'opening_price')::numeric)+(r->>'realized')::numeric;
        measurement:=jsonb_build_object('nav_usdt',nav::text,'adjusted_nav_usdt',adjusted::text,'highwater_usdt',highwater::text,
            'drawdown_bps',drawdown::text,'execution_loss_usdt',r->>'loss','starting_inventory_pnl_usdt',inventory::text,'net_cashflows_usdt',r->>'net');
        INSERT INTO life_financial.spot_nav_observations(account_id,operation_id,command,measurement,money_digest,highwater)
            VALUES(p_account,op,p_command,measurement,life_financial.spot_capital_digest(p_account),highwater);
        result:=jsonb_build_object('state','APPLIED');
    END IF;
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    result:=result||jsonb_build_object('kind',p_kind,'operation_id',op,'version',p_version+1,'replayed',false,'trading_permission',false);
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version)
        VALUES(p_account,op,p_fingerprint,request,result,p_version+1);
    RETURN result;
END; $$;

CREATE FUNCTION life_financial.joint_batch_members(cmd jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT COALESCE(jsonb_agg(m ORDER BY n,i),'[]'::jsonb) FROM (
    SELECT jsonb_build_object('kind','SPOT_CASHFLOW','command',value) m,0 n,ordinality i FROM jsonb_array_elements(cmd->'cashflows') WITH ORDINALITY
    UNION ALL SELECT jsonb_build_object('kind','SPOT_FILL','command',value),1,ordinality FROM jsonb_array_elements(cmd->'spot_fills') WITH ORDINALITY
    UNION ALL SELECT jsonb_build_object('kind','SWAP_FILL','command',value),2,ordinality FROM jsonb_array_elements(cmd->'swap_fills') WITH ORDINALITY
    UNION ALL SELECT jsonb_build_object('kind','PAID_FUNDING','command',cmd->'funding'),3,1) ordered;
$$;
CREATE FUNCTION life_financial.joint_batch_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE p life_financial.joint_batch_policies;b life_financial.joint_financial_batches;o life_financial.operations;
    child life_financial.operations;m jsonb;money jsonb;positions jsonb;source jsonb;expected jsonb;
BEGIN
    SELECT * INTO p FROM life_financial.joint_batch_policies WHERE account_id=p_account;
    IF NOT FOUND THEN RETURN NOT EXISTS(SELECT 1 FROM life_financial.joint_financial_batches WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=p_account AND command->>'kind'='JOINT_BATCH');END IF;
    IF p.policy_hash IS DISTINCT FROM (SELECT policy_hash FROM life_financial.accounts WHERE account_id=p_account)
        OR (SELECT count(*) FROM life_financial.joint_financial_batches WHERE account_id=p_account)>p.max_batches
        OR (SELECT COALESCE(sum(octet_length(to_jsonb(batch)::text)),0) FROM life_financial.joint_financial_batches batch WHERE account_id=p_account)>p.max_total_bytes THEN RETURN false;END IF;
    FOR b IN SELECT * FROM life_financial.joint_financial_batches WHERE account_id=p_account LOOP
        SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=b.operation_id;
        IF o.command->>'kind'<>'JOINT_BATCH' OR o.command->'command' IS DISTINCT FROM b.command
            OR (o.command->>'version')::bigint<>b.start_version OR (o.receipt->>'version')::bigint<>o.committed_version
            OR o.fingerprint<>encode(sha256(convert_to(life_financial.canonical_json_v1(o.command),'UTF8')),'hex')
            OR b.money_before IS DISTINCT FROM life_financial.portfolio_money_at(p_account,b.start_version)
            OR jsonb_array_length(life_financial.joint_batch_members(b.command))>p.max_members THEN RETURN false;END IF;
        expected:=jsonb_build_object('kind','JOINT_BATCH','operation_id',b.operation_id,'state',b.state,
            'cash_delta_usdt',((b.money_after->>'quote')::numeric-(b.money_before->>'quote')::numeric)::text,
            'base_delta_life',((b.money_after->>'base')::numeric-(b.money_before->>'base')::numeric)::text,
            'version',o.committed_version,'replayed',false,'trading_permission',false);
        IF b.reason IS NOT NULL THEN expected:=expected||jsonb_build_object('reason',b.reason);END IF;
        IF o.receipt IS DISTINCT FROM expected THEN RETURN false;END IF;
        IF b.state='APPLIED' THEN
            IF (SELECT jsonb_agg(m-ARRAY['request','receipt'] ORDER BY ord) FROM jsonb_array_elements(b.members) WITH ORDINALITY x(m,ord)) IS DISTINCT FROM life_financial.joint_batch_members(b.command) THEN RETURN false;END IF;
            FOR m IN SELECT value FROM jsonb_array_elements(b.members) LOOP
                SELECT * INTO STRICT child FROM life_financial.operations WHERE account_id=p_account AND operation_id=m#>>'{command,operation_id}';
                IF child.command IS DISTINCT FROM m->'request' OR child.receipt IS DISTINCT FROM m->'receipt'
                    OR child.command->>'kind'<>m->>'kind' OR child.command->'command' IS DISTINCT FROM m->'command'
                    OR child.command->'binding' IS DISTINCT FROM o.command->'binding' OR child.command->'owner' IS DISTINCT FROM o.command->'owner'
                    OR child.receipt->>'state'<>'APPLIED' OR child.committed_version>=o.committed_version
                    OR child.fingerprint<>encode(sha256(convert_to(life_financial.canonical_json_v1(child.command),'UTF8')),'hex')
                    OR NOT life_financial.joint_batch_members(b.command) @> jsonb_build_array(jsonb_build_object('kind',m->>'kind','command',m->'command')) THEN RETURN false;END IF;
            END LOOP;
            money:=life_financial.portfolio_money_at(p_account,o.committed_version);
            positions:=life_financial.portfolio_positions_at(p_account,o.committed_version);
            source:=b.command#>'{funding,bundle,account,snapshot}';
            IF b.money_after IS DISTINCT FROM money OR (source->>'spot_cash_quote')::numeric<>(money->>'quote')::numeric
                OR (source->>'spot_life_base')::numeric<>(money->>'base')::numeric
                OR (source->>'long_base')::numeric<>(positions->>'long_base')::numeric
                OR (source->>'short_base')::numeric<>(positions->>'short_base')::numeric
                OR NOT EXISTS(SELECT 1 FROM life_financial.funding_observations f WHERE f.account_id=p_account
                    AND f.operation_id=b.command#>>'{funding,operation_id}' AND f.bundle=b.command#>'{funding,bundle}' AND f.state='APPLIED') THEN RETURN false;END IF;
        ELSIF b.money_after IS DISTINCT FROM b.money_before OR b.members<>'[]'::jsonb THEN RETURN false;END IF;
    END LOOP;
    RETURN NOT EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=p_account AND o.command->>'kind'='JOINT_BATCH'
        AND NOT EXISTS(SELECT 1 FROM life_financial.joint_financial_batches b WHERE b.account_id=p_account AND b.operation_id=o.operation_id));
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
CREATE FUNCTION life_financial.joint_financial_pending(p_account uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT EXISTS(SELECT 1 FROM life_financial.spot_pending_observations WHERE account_id=p_account)
    OR EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts WHERE account_id=p_account AND state='RECOVERY_REQUIRED')
    OR EXISTS(SELECT 1 FROM life_financial.swap_pending_fills WHERE account_id=p_account)
    OR EXISTS(SELECT 1 FROM life_financial.funding_observations WHERE account_id=p_account AND state='RECOVERY_REQUIRED')
    OR EXISTS(SELECT 1 FROM life_financial.swap_terminal_facts WHERE account_id=p_account AND state='RECOVERY_REQUIRED')
    OR EXISTS(SELECT 1 FROM life_financial.joint_financial_batches WHERE account_id=p_account AND state='RECOVERY_REQUIRED');
$$;
CREATE FUNCTION life_financial.joint_batch_command(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,cmd jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE a life_financial.accounts;p life_financial.joint_batch_policies;old life_financial.operations;child life_financial.operations;
    request jsonb;view jsonb;result jsonb;members jsonb;m jsonb;body jsonb;member_request jsonb;member_receipt jsonb;retained jsonb:='[]';
    before_money jsonb;after_money jsonb;positions jsonb;source jsonb;reason text;state text:='APPLIED';op text;kind text;v bigint;at_ms bigint;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR jsonb_typeof(cmd) IS DISTINCT FROM 'object'
        OR (SELECT count(*) FROM jsonb_object_keys(cmd))<>5 OR NOT cmd ?& ARRAY['operation_id','spot_fills','swap_fills','cashflows','funding']
        OR jsonb_typeof(cmd->'operation_id') IS DISTINCT FROM 'string' OR cmd->>'operation_id' !~ '^[A-Za-z0-9_.:-]{1,96}$'
        OR jsonb_typeof(cmd->'spot_fills') IS DISTINCT FROM 'array' OR jsonb_typeof(cmd->'swap_fills') IS DISTINCT FROM 'array'
        OR jsonb_typeof(cmd->'cashflows') IS DISTINCT FROM 'array' OR jsonb_typeof(cmd->'funding') IS DISTINCT FROM 'object'
        OR octet_length(cmd::text)>1048576 THEN RAISE EXCEPTION 'LIFE_JOINT_BATCH_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
    op:=cmd->>'operation_id';request:=jsonb_build_object('kind','JOINT_BATCH','binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',cmd);
    IF p_fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(request),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'LIFE_JOINT_BATCH_FINGERPRINT_INVALID' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,a.version);
    IF NOT life_financial.joint_batch_verified(p_account) THEN RAISE EXCEPTION 'LIFE_JOINT_BATCH_UNVERIFIED' USING ERRCODE='P0001';END IF;
    SELECT * INTO old FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF old.command<>request OR old.fingerprint<>p_fingerprint THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001';END IF;
        RETURN old.receipt||'{"replayed":true}'::jsonb;END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    view:=life_financial.spot_state(p_account);
    IF view->>'digest' IS DISTINCT FROM p_cut THEN RAISE EXCEPTION 'LIFE_SPOT_CUT_CONFLICT' USING ERRCODE='P0001';END IF;
    IF (view->>'unverified')::boolean THEN RAISE EXCEPTION 'LIFE_JOINT_BATCH_UNVERIFIED' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT p FROM life_financial.joint_batch_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
    members:=life_financial.joint_batch_members(cmd);
    IF jsonb_array_length(members)>p.max_members OR (SELECT count(*) FROM life_financial.joint_financial_batches WHERE account_id=p_account)>=p.max_batches
        OR (SELECT COALESCE(sum(octet_length(to_jsonb(batch)::text)),0) FROM life_financial.joint_financial_batches batch WHERE account_id=p_account)+octet_length(cmd::text)>p.max_total_bytes THEN
        RAISE EXCEPTION 'LIFE_JOINT_BATCH_BOUND' USING ERRCODE='P0001';END IF;
    IF EXISTS(SELECT 1 FROM jsonb_array_elements(members) m GROUP BY m#>>'{command,operation_id}' HAVING count(*)>1)
        OR EXISTS(SELECT 1 FROM jsonb_array_elements(members) m WHERE m#>>'{command,operation_id}'=op OR m#>>'{command,operation_id}' IS NULL) THEN
        RAISE EXCEPTION 'LIFE_JOINT_BATCH_MEMBER_ID_CONFLICT' USING ERRCODE='P0001';END IF;
    before_money:=life_financial.portfolio_money_at(p_account,p_version);
    after_money:=before_money;at_ms:=floor(extract(epoch FROM clock_timestamp())*1000)::bigint;
    BEGIN
        IF life_financial.joint_financial_pending(p_account) THEN RAISE EXCEPTION 'LIFE_JOINT_PRIOR_PENDING' USING ERRCODE='P0001';END IF;
        INSERT INTO life_financial.joint_batch_staging VALUES(p_account,pg_current_xact_id(),members);
        FOR m IN SELECT value FROM jsonb_array_elements(members) LOOP
            kind:=m->>'kind';body:=m->'command';
            SELECT version INTO STRICT v FROM life_financial.accounts WHERE account_id=p_account;
            SELECT * INTO child FROM life_financial.operations WHERE account_id=p_account AND operation_id=body->>'operation_id';
            IF FOUND THEN
                IF child.command->>'kind'<>kind OR child.command->'command' IS DISTINCT FROM body
                    OR child.command->'binding' IS DISTINCT FROM p_binding OR child.command->'owner' IS DISTINCT FROM p_owner
                    OR child.receipt->>'state'<>'APPLIED' THEN RAISE EXCEPTION 'LIFE_JOINT_MEMBER_CONFLICT' USING ERRCODE='P0001';END IF;
                member_request:=child.command;member_receipt:=child.receipt;
            ELSE
                member_request:=jsonb_build_object('kind',kind,'binding',p_binding,'owner',p_owner,'command',body);
                IF kind IN ('SWAP_FILL','PAID_FUNDING') THEN member_request:=member_request||jsonb_build_object('version',v,'cut',p_cut);END IF;
                IF kind='SPOT_FILL' THEN member_receipt:=life_financial.joint_stage_spot(p_account,p_binding,p_owner,v,p_cut,
                    encode(sha256(convert_to(life_financial.canonical_json_v1(member_request),'UTF8')),'hex'),kind,body);
                ELSIF kind='SWAP_FILL' THEN member_receipt:=life_financial.joint_stage_swap(p_account,p_binding,p_owner,v,p_cut,
                    encode(sha256(convert_to(life_financial.canonical_json_v1(member_request),'UTF8')),'hex'),body);
                ELSIF kind='SPOT_CASHFLOW' THEN member_receipt:=life_financial.joint_stage_cashflow(p_account,p_binding,p_owner,v,p_cut,
                    encode(sha256(convert_to(life_financial.canonical_json_v1(member_request),'UTF8')),'hex'),kind,body);
                ELSE member_receipt:=life_financial.joint_stage_funding(p_account,p_binding,p_owner,v,p_cut,
                    encode(sha256(convert_to(life_financial.canonical_json_v1(member_request),'UTF8')),'hex'),body);END IF;
                IF member_receipt->>'state'<>'APPLIED' THEN RAISE EXCEPTION 'LIFE_JOINT_MEMBER_PENDING' USING ERRCODE='P0001';END IF;
            END IF;
            retained:=retained||jsonb_build_array(m||jsonb_build_object('request',member_request,'receipt',member_receipt));
        END LOOP;
        -- Replay complete economics in their original economic order. The
        -- invocation order above confers no ordering or financing authority.
        PERFORM life_financial.swap_fill_replay(p_account);
        PERFORM life_financial.spot_capital_replay(p_account);
        SELECT version INTO STRICT v FROM life_financial.accounts WHERE account_id=p_account;
        after_money:=life_financial.portfolio_money_at(p_account,v);
        positions:=life_financial.portfolio_positions_at(p_account,v);source:=cmd#>'{funding,bundle,account,snapshot}';
        IF (source->>'spot_cash_quote')::numeric IS DISTINCT FROM (after_money->>'quote')::numeric
            OR (source->>'spot_life_base')::numeric IS DISTINCT FROM (after_money->>'base')::numeric
            OR (source->>'long_base')::numeric IS DISTINCT FROM (positions->>'long_base')::numeric
            OR (source->>'short_base')::numeric IS DISTINCT FROM (positions->>'short_base')::numeric THEN
            RAISE EXCEPTION 'LIFE_JOINT_FINAL_ACCOUNT_MISMATCH' USING ERRCODE='P0001';END IF;
        UPDATE life_financial.balances b SET total=CASE b.asset WHEN 'LIFE' THEN (after_money->>'base')::numeric ELSE (after_money->>'quote')::numeric END,
            reserved=(SELECT COALESCE(sum(r.remaining),0) FROM life_financial.reservations r WHERE r.account_id=p_account AND r.asset=b.asset AND r.state<>'TERMINAL'),
            funding_deficit=greatest(0,(SELECT COALESCE(sum(r.remaining),0) FROM life_financial.reservations r WHERE r.account_id=p_account AND r.asset=b.asset AND r.state<>'TERMINAL')-
                CASE b.asset WHEN 'LIFE' THEN (after_money->>'base')::numeric ELSE (after_money->>'quote')::numeric END),version=b.version+1 WHERE b.account_id=p_account;
        IF NOT life_financial.spot_capital_verified(p_account) OR NOT life_financial.swap_accounting_verified(p_account)
            OR NOT life_financial.funding_verified(p_account) THEN RAISE EXCEPTION 'LIFE_JOINT_FINAL_PROOF_INVALID' USING ERRCODE='P0001';END IF;
        IF EXISTS(SELECT 1 FROM life_financial.balances WHERE account_id=p_account AND funding_deficit>0) THEN
            UPDATE life_financial.accounts SET halted=true WHERE account_id=p_account;END IF;
        DELETE FROM life_financial.joint_batch_staging WHERE account_id=p_account;
    EXCEPTION WHEN raise_exception OR check_violation OR numeric_value_out_of_range OR unique_violation OR SQLSTATE 'PZ001' THEN
        GET STACKED DIAGNOSTICS reason=MESSAGE_TEXT;
        IF reason !~ '^LIFE_[A-Z_]+$' THEN reason:='LIFE_JOINT_FINANCIAL_CONSTRAINT_REJECTED';END IF;
        state:='RECOVERY_REQUIRED';after_money:=before_money;retained:='[]';
        UPDATE life_financial.accounts SET halted=true WHERE account_id=p_account;
    END;
    SELECT version INTO STRICT v FROM life_financial.accounts WHERE account_id=p_account;
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    result:=jsonb_build_object('kind','JOINT_BATCH','operation_id',op,'state',state,
        'cash_delta_usdt',((after_money->>'quote')::numeric-(before_money->>'quote')::numeric)::text,
        'base_delta_life',((after_money->>'base')::numeric-(before_money->>'base')::numeric)::text,
        'version',v+1,'replayed',false,'trading_permission',false);
    IF reason IS NOT NULL THEN result:=result||jsonb_build_object('reason',reason);END IF;
    INSERT INTO life_financial.joint_financial_batches VALUES(p_account,op,cmd,state,reason,p_version,at_ms,before_money,after_money,retained);
    IF (SELECT COALESCE(sum(octet_length(to_jsonb(batch)::text)),0) FROM life_financial.joint_financial_batches batch WHERE account_id=p_account)>p.max_total_bytes THEN
        -- Includes retained member requests/receipts and both original cuts.
        -- Overflow rolls back every member; a caller cannot publish money while
        -- silently dropping its parent proof or treating a failed capture as saved.
        RAISE EXCEPTION 'LIFE_JOINT_BATCH_BOUND' USING ERRCODE='P0001';END IF;
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version)
        VALUES(p_account,op,p_fingerprint,request,result,v+1);
    RETURN result;
END; $$;
CREATE FUNCTION life_financial.joint_batch_read(p_account uuid,p_rows integer,p_bytes integer,p_operation text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE result jsonb;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_ACCESS_DENIED' USING ERRCODE='P0001';END IF;
    IF p_rows IS NULL OR p_bytes IS NULL OR p_rows NOT BETWEEN 1 AND 4096 OR p_bytes NOT BETWEEN 1 AND 16777216
        OR (SELECT count(*) FROM life_financial.joint_financial_batches WHERE account_id=p_account)>p_rows THEN
        RAISE EXCEPTION 'LIFE_JOINT_BATCH_READ_BOUND' USING ERRCODE='P0001';END IF;
    IF NOT life_financial.joint_batch_verified(p_account) OR NOT life_financial.spot_capital_verified(p_account)
        OR NOT life_financial.swap_accounting_verified(p_account) OR NOT life_financial.funding_verified(p_account) THEN
        RAISE EXCEPTION 'LIFE_JOINT_BATCH_UNVERIFIED' USING ERRCODE='P0001';END IF;
    IF p_operation IS NULL THEN result:=jsonb_build_object('batches',COALESCE((SELECT jsonb_agg(to_jsonb(b) ORDER BY operation_id)
        FROM life_financial.joint_financial_batches b WHERE b.account_id=p_account),'[]'::jsonb),'trading_permission',false);
    ELSE SELECT jsonb_build_object('command',o.command,'receipt',o.receipt) INTO result FROM life_financial.operations o
        WHERE o.account_id=p_account AND o.operation_id=p_operation AND o.command->>'kind'='JOINT_BATCH';END IF;
    IF octet_length(result::text)>p_bytes THEN RAISE EXCEPTION 'LIFE_JOINT_BATCH_READ_BOUND' USING ERRCODE='P0001';END IF;
    RETURN result;
END; $$;
CREATE FUNCTION life_financial.joint_batch_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE o life_financial.operations;b life_financial.joint_financial_batches;f life_financial.funding_observations;
BEGIN
    IF EXISTS(SELECT 1 FROM life_financial.joint_batch_staging WHERE account_id=NEW.account_id)
        OR NOT life_financial.joint_batch_verified(NEW.account_id) THEN RAISE EXCEPTION 'LIFE_JOINT_BATCH_UNVERIFIED_AT_COMMIT' USING ERRCODE='P0001';END IF;
    SELECT * INTO o FROM life_financial.operations WHERE account_id=NEW.account_id AND committed_version=NEW.version;
    IF o.command->>'kind'='JOINT_BATCH' THEN
        SELECT * INTO STRICT b FROM life_financial.joint_financial_batches WHERE account_id=NEW.account_id AND operation_id=o.operation_id;
        IF b.state='APPLIED' THEN
            SELECT * INTO STRICT f FROM life_financial.funding_observations WHERE account_id=NEW.account_id AND operation_id=b.command#>>'{funding,operation_id}';
            IF life_financial.funding_bundle_reason(NEW.account_id,f.bundle,f.previous_funding_operation,f.previous_carry_operation,
                floor(extract(epoch FROM clock_timestamp())*1000)::bigint)<>'FUNDING_QUALIFIED' THEN
                RAISE EXCEPTION 'LIFE_JOINT_BATCH_SOURCE_EXPIRED_AT_COMMIT' USING ERRCODE='P0001';END IF;
        END IF;
    END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER joint_batch_account_commit AFTER UPDATE ON life_financial.accounts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN(NEW.version<>OLD.version) EXECUTE FUNCTION life_financial.joint_batch_commit_guard();
CREATE FUNCTION life_financial.joint_stage_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF EXISTS(SELECT 1 FROM life_financial.joint_batch_staging WHERE account_id=NEW.account_id) THEN
        RAISE EXCEPTION 'LIFE_JOINT_STAGE_STILL_APPLYING' USING ERRCODE='P0001';END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER joint_batch_staging_commit AFTER INSERT ON life_financial.joint_batch_staging
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.joint_stage_commit_guard();
CREATE FUNCTION life_financial.joint_pending_finance_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF NEW.command->>'kind' IN ('SPOT_FILL','SWAP_FILL','SPOT_CASHFLOW','PAID_FUNDING','SHARED_RELEASE')
        AND EXISTS(SELECT 1 FROM life_financial.joint_financial_batches b WHERE b.account_id=NEW.account_id AND b.state='RECOVERY_REQUIRED') THEN
        RAISE EXCEPTION 'LIFE_JOINT_PRIOR_PENDING' USING ERRCODE='P0001';END IF;
    RETURN NEW;
END; $$;
CREATE TRIGGER joint_pending_money_guard BEFORE INSERT ON life_financial.operations
    FOR EACH ROW EXECUTE FUNCTION life_financial.joint_pending_finance_guard();
CREATE OR REPLACE FUNCTION life_financial.commit_owner_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE a life_financial.accounts; expiry timestamptz; at timestamptz; op life_financial.operations;
BEGIN
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=NEW.account_id;
    SELECT * INTO op FROM life_financial.operations WHERE account_id=NEW.account_id AND committed_version=NEW.version;
    IF a.owner_generation IS DISTINCT FROM NEW.owner_generation OR a.owner_process IS DISTINCT FROM NEW.owner_process
        OR a.owner_epoch<>NEW.owner_epoch OR a.current_session IS DISTINCT FROM NEW.current_session
        OR a.lineage<>NEW.lineage OR a.policy_hash<>NEW.policy_hash OR a.config_hash<>NEW.config_hash THEN
        RAISE EXCEPTION 'LIFE_OWNER_EXPIRED_OR_CHANGED_AT_COMMIT' USING ERRCODE='P0001'; END IF;
    IF op.command->>'kind' IN ('JOINT_BATCH','SHARED_RELEASE','SWAP_TERMINAL','PORTFOLIO_NAV','PAID_FUNDING','SWAP_FILL','SWAP_ACK','CARRY_CHECK','RECORDER_POSITION_STAGE','RECORDER_POSITION_APPLY','RECORDER_EVENT_STAGE','RECORDER_EVENT_APPLY','RECORDER_CONFIG_STAGE','RECORDER_CONFIG_APPLY','RECORDER_STAGE','RECORDER_APPLY','SPOT_MARKOUT','SPOT_INVENTORY_SETTLE','SPOT_ACK','SPOT_FILL','SPOT_CASHFLOW','SPOT_NAV','SPOT_SESSION_TICK','SPOT_LIFECYCLE','SPOT_TERMINAL') OR op.command->>'kind' IN ('SPOT_ACTION_TRANSITION','SPOT_ACTION_ABANDON')
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

CREATE FUNCTION life_financial.spot_state_before_joint_batch(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE b jsonb;verified boolean;
BEGIN
    b:=life_financial.spot_state_before_shared_release(p_account)-'digest'-'observed_at';verified:=life_financial.shared_release_verified(p_account);
    b:=b||jsonb_build_object('shared_release',jsonb_build_object('verified',verified,
        'policy',(SELECT to_jsonb(p) FROM life_financial.shared_release_policies p WHERE p.account_id=p_account),
        'releases',COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY operation_id) FROM life_financial.shared_claim_releases r WHERE r.account_id=p_account),'[]'::jsonb)),
        'financial_recovery_required',(b->>'financial_recovery_required')::boolean OR NOT verified,
        'recovery_required',(b->>'recovery_required')::boolean OR NOT verified,'unverified',(b->>'unverified')::boolean OR NOT verified);
    RETURN b||jsonb_build_object('digest',encode(sha256(convert_to(b::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
END; $$;


CREATE OR REPLACE FUNCTION life_financial.spot_state(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE b jsonb;verified boolean;pending boolean;
BEGIN
    b:=life_financial.spot_state_before_joint_batch(p_account)-'digest'-'observed_at';verified:=life_financial.joint_batch_verified(p_account);
    pending:=EXISTS(SELECT 1 FROM life_financial.joint_financial_batches WHERE account_id=p_account AND state='RECOVERY_REQUIRED');
    b:=b||jsonb_build_object('joint_batches',jsonb_build_object('verified',verified,'pending',pending,
        'policy',(SELECT to_jsonb(p) FROM life_financial.joint_batch_policies p WHERE p.account_id=p_account),
        'batches',COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY operation_id) FROM life_financial.joint_financial_batches r WHERE r.account_id=p_account),'[]'::jsonb)),
        'financial_recovery_required',(b->>'financial_recovery_required')::boolean OR NOT verified OR pending,
        'recovery_required',(b->>'recovery_required')::boolean OR NOT verified OR pending,'unverified',(b->>'unverified')::boolean OR NOT verified);
    RETURN b||jsonb_build_object('digest',encode(sha256(convert_to(b::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
END; $$;
REVOKE ALL ON life_financial.joint_batch_policies,life_financial.joint_financial_batches,life_financial.joint_batch_staging FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
