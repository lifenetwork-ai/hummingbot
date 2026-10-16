-- Exact standalone SPOT inventory settlement. No bootstrap, activation or loss refund.
CREATE TABLE life_financial.spot_settlement_policies (
    account_id uuid PRIMARY KEY REFERENCES life_financial.accounts,
    policy_hash life_financial.digest NOT NULL, max_fact_age_ms integer NOT NULL CHECK(max_fact_age_ms>0),
    FOREIGN KEY(account_id,policy_hash) REFERENCES life_financial.policies
);
CREATE TABLE life_financial.spot_inventory_settlements (
    account_id uuid NOT NULL REFERENCES life_financial.accounts,
    operation_id life_financial.identity NOT NULL, cycle_id life_financial.identity NOT NULL,
    command jsonb NOT NULL CHECK(jsonb_typeof(command)='object'),
    evidence jsonb NOT NULL CHECK(jsonb_typeof(evidence)='object'),
    PRIMARY KEY(account_id,operation_id), UNIQUE(account_id,cycle_id),
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE life_financial.spot_inventory_members (
    account_id uuid NOT NULL REFERENCES life_financial.accounts,
    intent_id life_financial.identity NOT NULL, operation_id life_financial.identity NOT NULL,
    actual_usdt life_financial.amount NOT NULL, held_before_usdt life_financial.amount NOT NULL,
    PRIMARY KEY(account_id,intent_id),
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.spot_inventory_settlements DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(account_id,intent_id) REFERENCES life_financial.send_attempts
);

CREATE INDEX spot_inventory_members_by_operation ON life_financial.spot_inventory_members(account_id,operation_id,intent_id);

CREATE FUNCTION life_financial.canonical_json_v1(p_value jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
DECLARE value text;
BEGIN
    CASE jsonb_typeof(p_value)
        WHEN 'object' THEN SELECT '{'||COALESCE(string_agg(to_jsonb(key)::text||':'||life_financial.canonical_json_v1(item),',' ORDER BY key COLLATE "C"),'')||'}'
            INTO value FROM jsonb_each(p_value) AS e(key,item);
        WHEN 'array' THEN SELECT '['||COALESCE(string_agg(life_financial.canonical_json_v1(item),',' ORDER BY ordinal),'')||']'
            INTO value FROM jsonb_array_elements(p_value) WITH ORDINALITY AS e(item,ordinal);
        ELSE value:=p_value::text;
    END CASE;
    RETURN value;
END; $$;

CREATE FUNCTION life_financial.spot_inventory_evidence(p_account uuid,p_members jsonb) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE key text; t life_financial.send_attempts; d life_financial.spot_preparations;
    g life_financial.spot_accounting_progress; row record; identities jsonb:='[]'; fills jsonb:='[]';
    actuals jsonb:='{}'; held jsonb:='{}'; base numeric:=0; quote numeric:=0; gross numeric:=0;
    fee numeric; sign numeric; last_intent text; quantity numeric; price numeric; loss numeric; reference numeric;
BEGIN
    IF p_members IS NULL OR jsonb_typeof(p_members)<>'array' OR jsonb_array_length(p_members) NOT BETWEEN 1 AND 64
        OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_members) x WHERE jsonb_typeof(x)<>'string')
        OR (SELECT count(*)<>count(DISTINCT value) FROM jsonb_array_elements_text(p_members)) THEN
        RAISE EXCEPTION 'LIFE_SETTLEMENT_MEMBERS_INVALID' USING ERRCODE='P0001'; END IF;
    FOR key IN SELECT value FROM jsonb_array_elements_text(p_members) ORDER BY value COLLATE "C" LOOP
        PERFORM key::life_financial.identity;
        SELECT * INTO t FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=key;
        IF NOT FOUND OR t.product<>'SPOT' OR t.state<>'TERMINAL' OR t.exchange_order_id IS NULL THEN
            RAISE EXCEPTION 'LIFE_SETTLEMENT_TERMINAL_REQUIRED' USING ERRCODE='P0001'; END IF;
        SELECT * INTO STRICT d FROM life_financial.spot_preparations WHERE account_id=p_account AND intent_id=key;
        SELECT * INTO g FROM life_financial.spot_accounting_progress WHERE account_id=p_account AND intent_id=key;
        IF NOT FOUND OR g.filled_base<=0 OR d.request->>'budget_scope'='EXIT'
            OR NOT EXISTS(SELECT 1 FROM life_financial.spot_terminal_facts WHERE account_id=p_account AND intent_id=key)
            OR EXISTS(SELECT 1 FROM life_financial.reservations WHERE account_id=p_account AND intent_id=key AND (state<>'TERMINAL' OR remaining<>0))
            OR g.filled_base<>(SELECT COALESCE(sum((observation->>'quantity_base_life')::numeric),0)
                FROM life_financial.spot_fill_facts WHERE account_id=p_account AND intent_id=key)
            OR EXISTS(SELECT 1 FROM life_financial.spot_fill_facts WHERE account_id=p_account AND intent_id=key AND exchange_order_id<>t.exchange_order_id) THEN
            RAISE EXCEPTION 'LIFE_SETTLEMENT_MEMBER_UNVERIFIED' USING ERRCODE='P0001'; END IF;
        identities:=identities||jsonb_build_array(jsonb_build_array(key,t.wire_id,t.exchange_order_id,t.session_id,t.epoch));
        actuals:=actuals||jsonb_build_object(key,'0'); held:=held||jsonb_build_object(key,greatest(d.subsidy,g.execution_loss)::text);
    END LOOP;
    FOR row IN SELECT f.*,attempt.side FROM life_financial.spot_fill_facts f JOIN life_financial.send_attempts attempt USING(account_id,intent_id)
        WHERE f.account_id=p_account AND f.intent_id IN (SELECT value FROM jsonb_array_elements_text(p_members))
        ORDER BY (f.observation->>'fill_at_ms')::bigint,f.trade_id COLLATE "C" LOOP
        quantity:=(row.observation->>'quantity_base_life')::numeric; price:=(row.observation->>'price_usdt')::numeric;
        fee:=(row.observation->>'signed_fee')::numeric; reference:=(row.observation->>'independent_price_usdt')::numeric;
        sign:=CASE row.side WHEN 'BUY' THEN 1 ELSE -1 END;
        base:=base+sign*quantity+CASE WHEN row.observation->>'fee_currency'='LIFE' THEN fee ELSE 0 END;
        quote:=quote-sign*quantity*price+CASE WHEN row.observation->>'fee_currency'='USDT' THEN fee ELSE 0 END;
        loss:=greatest(0,-fee*CASE WHEN row.observation->>'fee_currency'='LIFE' THEN reference ELSE 1 END-sign*(reference-price)*quantity);
        gross:=gross+loss; actuals:=actuals||jsonb_build_object(row.intent_id,((actuals->>row.intent_id)::numeric+loss)::text);
        fills:=fills||jsonb_build_array(jsonb_build_array(row.trade_id,row.intent_id,row.side,row.observation));
        last_intent:=row.intent_id;
    END LOOP;
    IF last_intent IS NULL OR base<>0 THEN RAISE EXCEPTION 'LIFE_SETTLEMENT_INVENTORY_NOT_FLAT' USING ERRCODE='P0001'; END IF;
    actuals:=actuals||jsonb_build_object(last_intent,((actuals->>last_intent)::numeric+greatest(0,-quote-gross))::text);
    FOR key IN SELECT value FROM jsonb_array_elements_text(p_members) LOOP PERFORM (actuals->>key)::life_financial.amount; END LOOP;
    RETURN jsonb_build_object('actuals',actuals,'held_before',held,'proof',encode(sha256(convert_to(
        life_financial.canonical_json_v1(jsonb_build_object('identities',identities,'fills',fills)),'UTF8')),'hex'));
END; $$;

CREATE FUNCTION life_financial.spot_inventory_settlements_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s life_financial.spot_inventory_settlements; op life_financial.operations; evidence jsonb; member record; scope_name text;
    bucket_key text; policy life_financial.spot_policies; key text;
BEGIN
    IF NOT EXISTS(SELECT 1 FROM life_financial.spot_inventory_settlements WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.spot_inventory_members WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=p_account AND command->>'kind'='SPOT_INVENTORY_SETTLE') THEN RETURN true; END IF;
    SELECT p.* INTO STRICT policy FROM life_financial.spot_policies p JOIN life_financial.accounts a USING(account_id,policy_hash) WHERE a.account_id=p_account;
    IF EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=p_account AND o.command->>'kind'='SPOT_INVENTORY_SETTLE'
        AND NOT EXISTS(SELECT 1 FROM life_financial.spot_inventory_settlements registered WHERE registered.account_id=o.account_id AND registered.operation_id=o.operation_id))
        OR EXISTS(SELECT 1 FROM life_financial.spot_inventory_members m WHERE m.account_id=p_account AND NOT EXISTS(
            SELECT 1 FROM life_financial.spot_inventory_settlements registered WHERE registered.account_id=m.account_id AND registered.operation_id=m.operation_id AND registered.command->'intent_ids' ? m.intent_id)) THEN RETURN false; END IF;
    FOR s IN SELECT * FROM life_financial.spot_inventory_settlements WHERE account_id=p_account LOOP
        SELECT * INTO op FROM life_financial.operations WHERE account_id=p_account AND operation_id=s.operation_id;
        IF NOT FOUND OR op.command->>'kind' IS DISTINCT FROM 'SPOT_INVENTORY_SETTLE' OR op.command->'command' IS DISTINCT FROM s.command
            OR op.receipt->>'kind' IS DISTINCT FROM 'SPOT_INVENTORY_SETTLE' OR op.receipt->>'state' IS DISTINCT FROM 'SETTLED'
            OR op.receipt->>'operation_id' IS DISTINCT FROM s.operation_id OR op.receipt->>'cycle_id' IS DISTINCT FROM s.cycle_id
            OR s.command->>'operation_id' IS DISTINCT FROM s.operation_id OR s.command->>'cycle_id' IS DISTINCT FROM s.cycle_id
            OR op.receipt->'trading_permission' IS DISTINCT FROM 'false'::jsonb OR op.receipt->'replayed' IS DISTINCT FROM 'false'::jsonb
            OR (op.receipt->>'version')::bigint IS DISTINCT FROM op.committed_version THEN RETURN false; END IF;
        evidence:=life_financial.spot_inventory_evidence(p_account,s.command->'intent_ids');
        IF evidence IS DISTINCT FROM s.evidence OR op.receipt->'actuals_usdt' IS DISTINCT FROM evidence->'actuals'
            OR op.receipt->>'proof_digest' IS DISTINCT FROM evidence->>'proof'
            OR (SELECT count(*) FROM life_financial.spot_inventory_members WHERE account_id=p_account AND operation_id=s.operation_id)
                <>jsonb_array_length(s.command->'intent_ids') THEN RETURN false; END IF;
        FOR key IN SELECT value FROM jsonb_array_elements_text(s.command->'intent_ids') LOOP
            SELECT m.*,d.prepared_at,t.session_id INTO member FROM life_financial.spot_inventory_members m
                JOIN life_financial.spot_preparations d USING(account_id,intent_id) JOIN life_financial.send_attempts t USING(account_id,intent_id)
                WHERE m.account_id=p_account AND m.intent_id=key;
            IF NOT FOUND OR member.operation_id<>s.operation_id OR member.actual_usdt<>(evidence#>>ARRAY['actuals',key])::numeric
                OR member.held_before_usdt<>(evidence#>>ARRAY['held_before',key])::numeric THEN RETURN false; END IF;
            FOREACH scope_name IN ARRAY ARRAY['CAMPAIGN','DAY','SESSION'] LOOP
                bucket_key:=CASE scope_name WHEN 'CAMPAIGN' THEN policy.campaign_id WHEN 'DAY' THEN to_char(member.prepared_at AT TIME ZONE 'UTC','YYYY-MM-DD') ELSE member.session_id::text END;
                IF NOT EXISTS(SELECT 1 FROM life_financial.budget_events e WHERE e.account_id=p_account
                    AND e.event_id='settle-release:'||encode(sha256(convert_to(s.operation_id||':'||key||':'||scope_name,'UTF8')),'hex')
                    AND e.family='SUBSIDY' AND e.scope=scope_name AND e.window_key=bucket_key AND e.kind='RELEASE' AND e.amount=member.held_before_usdt)
                    OR NOT EXISTS(SELECT 1 FROM life_financial.budget_events e WHERE e.account_id=p_account
                    AND e.event_id='settle-spend:'||encode(sha256(convert_to(s.operation_id||':'||key||':'||scope_name,'UTF8')),'hex')
                    AND e.family='SUBSIDY' AND e.scope=scope_name AND e.window_key=bucket_key AND e.kind='SPEND' AND e.amount=member.actual_usdt) THEN RETURN false; END IF;
            END LOOP;
        END LOOP;
    END LOOP;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.spot_inventory_commit_guard(p_account uuid,p_request jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE policy life_financial.spot_settlement_policies; observed timestamptz; at timestamptz;
BEGIN
    SELECT p.* INTO STRICT policy FROM life_financial.spot_settlement_policies p JOIN life_financial.accounts a USING(account_id,policy_hash) WHERE a.account_id=p_account;
    observed:=(p_request#>>'{facts,observed_at}')::timestamptz; at:=clock_timestamp();
    IF NOT isfinite(observed) OR observed>at OR extract(epoch FROM at-observed)*1000>policy.max_fact_age_ms
        OR NOT life_financial.spot_inventory_settlements_verified(p_account) OR NOT life_financial.spot_lifecycle_verified(p_account) THEN
        RAISE EXCEPTION 'LIFE_SETTLEMENT_FACTS_OR_PROJECTION_INVALID_AT_COMMIT' USING ERRCODE='P0001'; END IF;
END; $$;

CREATE FUNCTION life_financial.spot_settle_inventory(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,p_command jsonb,p_facts jsonb,p_resume boolean) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts; policy life_financial.spot_settlement_policies; budget_policy life_financial.spot_policies;
    previous life_financial.operations; op life_financial.identity; cycle life_financial.identity; request jsonb;
    view jsonb; expected jsonb; evidence jsonb; result jsonb; observed timestamptz; at timestamptz;
    key text; scope_name text; bucket_key text; member record; actual numeric; held numeric;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR p_command IS NULL OR p_facts IS NULL OR p_resume IS NULL
        OR p_cut IS NULL OR p_fingerprint IS NULL OR jsonb_typeof(p_command)<>'object' OR jsonb_typeof(p_facts)<>'object'
        OR (SELECT count(*) FROM jsonb_object_keys(p_command))<>3
        OR NOT p_command ?& ARRAY['operation_id','cycle_id','intent_ids'] OR octet_length(p_command::text)>65536
        OR octet_length(p_facts::text)>1048576 THEN RAISE EXCEPTION 'LIFE_SETTLEMENT_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    op:=p_command->>'operation_id'; cycle:=p_command->>'cycle_id';
    IF length(op)>96 THEN RAISE EXCEPTION 'LIFE_SETTLEMENT_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    request:=jsonb_build_object('kind','SPOT_INVENTORY_SETTLE','binding',p_binding,'owner',p_owner,'command',p_command,'facts',p_facts);
    SELECT * INTO previous FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF previous.command->>'kind' IS DISTINCT FROM 'SPOT_INVENTORY_SETTLE' OR previous.command->'binding' IS DISTINCT FROM p_binding
            OR previous.command->'owner' IS DISTINCT FROM p_owner OR previous.command->'command' IS DISTINCT FROM p_command
            OR previous.fingerprint<>p_fingerprint THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001'; END IF;
        IF NOT life_financial.spot_inventory_settlements_verified(p_account) THEN RAISE EXCEPTION 'LIFE_SETTLEMENT_UNVERIFIED' USING ERRCODE='P0001'; END IF;
        RETURN previous.receipt||'{"replayed":true}'::jsonb;
    ELSIF p_resume THEN RAISE EXCEPTION 'LIFE_SETTLEMENT_RECOVERY_NOT_STARTED' USING ERRCODE='P0001'; END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    view:=life_financial.spot_state(p_account);
    IF view->>'digest'<>p_cut THEN RAISE EXCEPTION 'LIFE_SPOT_CUT_CONFLICT' USING ERRCODE='P0001'; END IF;
    IF (view->>'unverified')::boolean OR EXISTS(SELECT 1 FROM life_financial.spot_pending_observations WHERE account_id=p_account)
        OR EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts WHERE account_id=p_account AND state<>'APPLIED')
        OR EXISTS(SELECT 1 FROM life_financial.spot_ack_ingress r WHERE r.account_id=p_account AND NOT EXISTS(
            SELECT 1 FROM life_financial.spot_ack_ingress_receipts d WHERE d.account_id=r.account_id AND d.ingress_id=r.ingress_id))
        OR EXISTS(SELECT 1 FROM life_financial.spot_financial_ingress r WHERE r.account_id=p_account AND NOT EXISTS(
            SELECT 1 FROM life_financial.spot_financial_ingress_receipts d WHERE d.account_id=r.account_id AND d.ingress_id=r.ingress_id)) THEN
        RAISE EXCEPTION 'LIFE_SETTLEMENT_OTHER_RECOVERY_PENDING' USING ERRCODE='P0001'; END IF;
    SELECT * INTO policy FROM life_financial.spot_settlement_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
    IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_SETTLEMENT_POLICY_REQUIRED' USING ERRCODE='P0001'; END IF;
    IF (SELECT count(*) FROM jsonb_object_keys(p_facts))<>13 OR NOT p_facts ?& ARRAY[
        'binding','observed_at','account_base_life','account_quote_usdt','liability_base_life','liability_quote_usdt',
        'account_trade_ids','account_cashflow_ids','accepted_wire_ids','runner_wire_ids','order_scope_complete','bill_scope_complete','runner_scope_complete']
        OR EXISTS(SELECT 1 FROM jsonb_each(p_facts) WHERE value='null'::jsonb)
        OR p_facts->'binding'<>p_binding OR p_facts->'order_scope_complete'<>'true'::jsonb OR p_facts->'bill_scope_complete'<>'true'::jsonb
        OR p_facts->'runner_scope_complete'<>'true'::jsonb
        OR (p_facts->>'account_base_life')::life_financial.amount<>(view#>>'{balances,LIFE,total}')::numeric
        OR (p_facts->>'account_quote_usdt')::life_financial.amount<>(view#>>'{balances,USDT,total}')::numeric
        OR (p_facts->>'liability_base_life')::life_financial.amount<>0 OR (p_facts->>'liability_quote_usdt')::life_financial.amount<>0
        OR EXISTS(SELECT 1 FROM life_financial.balances WHERE account_id=p_account AND liability<>0) THEN
        RAISE EXCEPTION 'LIFE_SETTLEMENT_FACTS_UNQUALIFIED' USING ERRCODE='P0001'; END IF;
    observed:=(p_facts->>'observed_at')::timestamptz; at:=clock_timestamp();
    IF NOT isfinite(observed) OR observed>at OR extract(epoch FROM at-observed)*1000>policy.max_fact_age_ms
        OR observed<COALESCE((SELECT max((o.command#>>'{command,observed_at}')::timestamptz) FROM life_financial.operations o
            WHERE o.account_id=p_account AND o.command->>'kind' IN ('SPOT_FILL','SPOT_CASHFLOW')),(p_owner->>'valid_from')::timestamptz) THEN
        RAISE EXCEPTION 'LIFE_SETTLEMENT_FACTS_UNQUALIFIED' USING ERRCODE='P0001'; END IF;
    SELECT COALESCE(jsonb_agg(trade_id ORDER BY trade_id COLLATE "C"),'[]'::jsonb) INTO expected FROM life_financial.fills WHERE account_id=p_account;
    IF p_facts->'account_trade_ids'<>expected THEN RAISE EXCEPTION 'LIFE_SETTLEMENT_ACCOUNT_CUT_UNPROVEN' USING ERRCODE='P0001'; END IF;
    SELECT COALESCE(jsonb_agg(bill_id ORDER BY bill_id COLLATE "C"),'[]'::jsonb) INTO expected FROM life_financial.cashflows WHERE account_id=p_account;
    IF p_facts->'account_cashflow_ids'<>expected THEN RAISE EXCEPTION 'LIFE_SETTLEMENT_ACCOUNT_CUT_UNPROVEN' USING ERRCODE='P0001'; END IF;
    FOREACH key IN ARRAY ARRAY['accepted_wire_ids','runner_wire_ids'] LOOP
        IF jsonb_typeof(p_facts->key)<>'array' OR (SELECT count(*)<>count(DISTINCT value) FROM jsonb_array_elements_text(p_facts->key))
            OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(p_facts->key) x WHERE NOT EXISTS(
                SELECT 1 FROM life_financial.send_attempts t WHERE t.account_id=p_account AND t.wire_id=x.value
                    AND (key='runner_wire_ids' OR t.state IN ('SEND_UNKNOWN','ACKED','TERMINAL')))) THEN
            RAISE EXCEPTION 'LIFE_SETTLEMENT_ACCOUNT_CUT_UNPROVEN' USING ERRCODE='P0001'; END IF;
    END LOOP;
    evidence:=life_financial.spot_inventory_evidence(p_account,p_command->'intent_ids');
    IF EXISTS(SELECT 1 FROM life_financial.spot_inventory_members WHERE account_id=p_account AND p_command->'intent_ids' ? intent_id)
        OR EXISTS(SELECT 1 FROM life_financial.spot_inventory_settlements WHERE account_id=p_account AND cycle_id=cycle) THEN
        RAISE EXCEPTION 'LIFE_SETTLEMENT_MEMBER_OR_CYCLE_REUSED' USING ERRCODE='P0001'; END IF;
    INSERT INTO life_financial.spot_inventory_settlements VALUES(p_account,op,cycle,p_command,evidence);
    SELECT * INTO STRICT budget_policy FROM life_financial.spot_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
    FOR key IN SELECT value FROM jsonb_array_elements_text(p_command->'intent_ids') LOOP
        actual:=(evidence#>>ARRAY['actuals',key])::life_financial.amount; held:=(evidence#>>ARRAY['held_before',key])::life_financial.amount;
        INSERT INTO life_financial.spot_inventory_members VALUES(p_account,key,op,actual,held);
        SELECT d.prepared_at,t.session_id INTO STRICT member FROM life_financial.spot_preparations d JOIN life_financial.send_attempts t USING(account_id,intent_id)
            WHERE d.account_id=p_account AND d.intent_id=key;
        FOREACH scope_name IN ARRAY ARRAY['CAMPAIGN','DAY','SESSION'] LOOP
            bucket_key:=CASE scope_name WHEN 'CAMPAIGN' THEN budget_policy.campaign_id WHEN 'DAY' THEN to_char(member.prepared_at AT TIME ZONE 'UTC','YYYY-MM-DD') ELSE member.session_id::text END;
            UPDATE life_financial.budget_buckets b SET committed=committed-held,incurred=incurred+actual,version=version+1
                WHERE b.account_id=p_account AND b.family='SUBSIDY' AND b.scope=scope_name AND b.window_key=bucket_key AND b.committed>=held;
            IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_SETTLEMENT_HOLD_UNPROVEN' USING ERRCODE='P0001'; END IF;
            INSERT INTO life_financial.budget_events VALUES(p_account,'settle-release:'||encode(sha256(convert_to(op||':'||key||':'||scope_name,'UTF8')),'hex'),
                'SUBSIDY',scope_name,bucket_key,'RELEASE',held);
            INSERT INTO life_financial.budget_events VALUES(p_account,'settle-spend:'||encode(sha256(convert_to(op||':'||key||':'||scope_name,'UTF8')),'hex'),
                'SUBSIDY',scope_name,bucket_key,'SPEND',actual);
        END LOOP;
    END LOOP;
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    result:=jsonb_build_object('kind','SPOT_INVENTORY_SETTLE','operation_id',op,'cycle_id',cycle,'actuals_usdt',evidence->'actuals',
        'proof_digest',evidence->'proof','state','SETTLED','version',p_version+1,'replayed',false,'trading_permission',false);
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version)
        VALUES(p_account,op,p_fingerprint,request,result,p_version+1);
    PERFORM life_financial.spot_inventory_commit_guard(p_account,request);
    RETURN result;
END; $$;

CREATE OR REPLACE FUNCTION life_financial.spot_lifecycle_verified(p_account uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT life_financial.spot_inventory_settlements_verified(p_account) AND NOT EXISTS(
    SELECT 1 FROM life_financial.spot_cancel_progress g WHERE g.account_id=p_account AND NOT EXISTS(
        SELECT 1 FROM life_financial.spot_lifecycle_events e JOIN life_financial.operations o USING(account_id,operation_id)
        WHERE e.account_id=g.account_id AND e.intent_id=g.intent_id AND e.after_cancel=to_jsonb(g)
            AND e.sequence=(SELECT max(x.sequence) FROM life_financial.spot_lifecycle_events x
                WHERE x.account_id=g.account_id AND x.intent_id=g.intent_id AND x.after_cancel IS NOT NULL)
            AND o.command->>'kind'='SPOT_LIFECYCLE' AND o.command->'command'=e.command))
AND NOT EXISTS(
    SELECT 1 FROM life_financial.spot_lifecycle_events e LEFT JOIN life_financial.operations o USING(account_id,operation_id)
    WHERE e.account_id=p_account AND (o.operation_id IS NULL OR o.command->>'kind'<>'SPOT_LIFECYCLE'
        OR o.command->'command'<>e.command OR o.receipt->>'state'<>e.result_state
        OR e.command->>'intent_id'<>e.intent_id))
AND NOT EXISTS(
    SELECT 1 FROM life_financial.send_attempts t LEFT JOIN life_financial.spot_accounting_progress g USING(account_id,intent_id)
    WHERE t.account_id=p_account AND t.product='SPOT' AND (
        t.state='ABORTED_BEFORE_SEND' AND (COALESCE(g.filled_base,0)<>0 OR t.exchange_order_id IS NOT NULL
            OR EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=p_account AND operation_id='arm:'||t.operation_id)
            OR NOT EXISTS(SELECT 1 FROM life_financial.spot_lifecycle_events e WHERE e.account_id=p_account AND e.intent_id=t.intent_id AND e.result_state=t.state))
        OR t.state='TERMINAL' AND NOT EXISTS(SELECT 1 FROM life_financial.spot_terminal_facts f
            JOIN life_financial.operations o USING(account_id,operation_id) WHERE f.account_id=p_account AND f.intent_id=t.intent_id
                AND f.command->>'exchange_order_id'=t.exchange_order_id AND (f.command#>>'{facts,cumulative_base_life}')::numeric=COALESCE(g.filled_base,0)
                AND f.subsidy_settled=(COALESCE(g.filled_base,0)=0) AND o.command->>'kind'='SPOT_TERMINAL'
                AND o.command->'command'=f.command AND o.receipt->>'state'='TERMINAL')
        OR t.state IN ('TERMINAL','ABORTED_BEFORE_SEND') AND EXISTS(SELECT 1 FROM life_financial.reservations r
            WHERE r.account_id=p_account AND r.intent_id=t.intent_id AND (r.state<>'TERMINAL' OR r.remaining<>0))
        OR t.state<>'TERMINAL' AND EXISTS(SELECT 1 FROM life_financial.spot_terminal_facts WHERE account_id=p_account AND intent_id=t.intent_id)
        OR t.state<>'ABORTED_BEFORE_SEND' AND EXISTS(SELECT 1 FROM life_financial.spot_lifecycle_events
            WHERE account_id=p_account AND intent_id=t.intent_id AND result_state='ABORTED_BEFORE_SEND')))
AND NOT EXISTS(
    SELECT 1 FROM life_financial.budget_buckets b WHERE b.account_id=p_account AND b.family='SUBSIDY' AND (
        b.committed<>COALESCE((SELECT sum(CASE e.kind WHEN 'COMMIT' THEN e.amount WHEN 'RELEASE' THEN -e.amount ELSE 0 END)
            FROM life_financial.budget_events e WHERE e.account_id=p_account AND e.family=b.family AND e.scope=b.scope AND e.window_key=b.window_key),0)
        OR b.incurred<>COALESCE((SELECT sum(e.amount) FROM life_financial.budget_events e WHERE e.account_id=p_account
            AND e.family=b.family AND e.scope=b.scope AND e.window_key=b.window_key AND e.kind='SPEND'),0)))
AND NOT EXISTS(
    SELECT 1 FROM (
        SELECT w.scope,w.window_key,sum(greatest(d.subsidy,COALESCE(g.execution_loss,0))) AS held
        FROM life_financial.spot_preparations d JOIN life_financial.send_attempts t USING(account_id,intent_id)
        JOIN life_financial.accounts a USING(account_id) JOIN life_financial.spot_policies p USING(account_id,policy_hash)
        LEFT JOIN life_financial.spot_accounting_progress g USING(account_id,intent_id)
        CROSS JOIN LATERAL (VALUES ('CAMPAIGN',p.campaign_id::text),
            ('DAY',to_char(d.prepared_at AT TIME ZONE 'UTC','YYYY-MM-DD')),('SESSION',t.session_id::text)) w(scope,window_key)
        WHERE d.account_id=p_account AND (t.state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND') OR COALESCE(g.filled_base,0)>0)
            AND NOT EXISTS(SELECT 1 FROM life_financial.spot_inventory_members m WHERE m.account_id=d.account_id AND m.intent_id=d.intent_id)
        GROUP BY w.scope,w.window_key
    ) expected LEFT JOIN life_financial.budget_buckets b ON b.account_id=p_account AND b.family='SUBSIDY'
        AND b.scope=expected.scope AND b.window_key=expected.window_key
    WHERE b.account_id IS NULL OR b.committed<expected.held);
$$;

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
    IF op.command->>'kind' IN ('RECORDER_POSITION_STAGE','RECORDER_POSITION_APPLY','RECORDER_EVENT_STAGE','RECORDER_EVENT_APPLY','RECORDER_CONFIG_STAGE','RECORDER_CONFIG_APPLY','RECORDER_STAGE','RECORDER_APPLY','SPOT_INVENTORY_SETTLE','SPOT_ACK','SPOT_FILL','SPOT_CASHFLOW','SPOT_NAV','SPOT_SESSION_TICK','SPOT_LIFECYCLE','SPOT_TERMINAL') OR op.command->>'kind' IN ('SPOT_ACTION_TRANSITION','SPOT_ACTION_ABANDON')
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
    IF op.command->>'kind'='SPOT_INVENTORY_SETTLE' THEN
        PERFORM life_financial.spot_inventory_commit_guard(NEW.account_id,op.command); END IF;
    IF op.command->>'kind' IN ('SPOT_ACTION_CLAIM','SPOT_ACTION_TRANSITION','SPOT_ACTION_ABANDON') THEN
        PERFORM life_financial.spot_action_commit_guard(NEW.account_id,op.command); END IF;
    IF op.command->>'kind'='SPOT_PREPARE' AND op.command ? 'action' THEN
        PERFORM life_financial.spot_action_prepare_commit_guard(NEW.account_id,op.command); END IF;
    RETURN NULL;
END; $$;

DO $scope$
DECLARE name text;
BEGIN
    FOREACH name IN ARRAY ARRAY['spot_settlement_policies','spot_inventory_settlements','spot_inventory_members'] LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',name);
        EXECUTE format('CREATE POLICY account_scope ON life_financial.%I FOR SELECT USING(life_financial.can_access(account_id))',name);
        EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',name);
    END LOOP;
END; $scope$;
CREATE FUNCTION life_financial.spot_inventory_projection_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF NOT life_financial.spot_inventory_settlements_verified(NEW.account_id) OR NOT life_financial.spot_lifecycle_verified(NEW.account_id) THEN
        RAISE EXCEPTION 'LIFE_SETTLEMENT_PROJECTION_UNVERIFIED_AT_COMMIT' USING ERRCODE='P0001'; END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER settlement_projection_commit AFTER INSERT ON life_financial.spot_inventory_settlements
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.spot_inventory_projection_guard();
CREATE CONSTRAINT TRIGGER settlement_members_commit AFTER INSERT ON life_financial.spot_inventory_members
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.spot_inventory_projection_guard();
CREATE CONSTRAINT TRIGGER settlement_accounts_commit AFTER UPDATE ON life_financial.accounts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.spot_inventory_projection_guard();
REVOKE ALL ON life_financial.spot_settlement_policies,life_financial.spot_inventory_settlements,life_financial.spot_inventory_members FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
