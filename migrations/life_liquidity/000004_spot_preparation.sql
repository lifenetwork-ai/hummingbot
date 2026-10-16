-- Standalone SPOT preparation only. No seed/import, activation or exchange I/O.
CREATE TABLE life_financial.spot_policies (
    account_id uuid NOT NULL, policy_hash life_financial.digest NOT NULL,
    min_inventory_base life_financial.amount NOT NULL,
    max_inventory_base life_financial.positive_amount NOT NULL,
    max_gross_quote life_financial.positive_amount NOT NULL,
    max_net_base life_financial.positive_amount NOT NULL,
    campaign_id life_financial.identity NOT NULL,
    subsidy_campaign life_financial.positive_amount NOT NULL,
    subsidy_day life_financial.positive_amount NOT NULL,
    subsidy_session life_financial.positive_amount NOT NULL,
    loss_campaign life_financial.positive_amount NOT NULL,
    loss_day life_financial.positive_amount NOT NULL,
    loss_session life_financial.positive_amount NOT NULL,
    max_quote_levels integer NOT NULL CHECK (max_quote_levels BETWEEN 1 AND 64),
    PRIMARY KEY (account_id,policy_hash), FOREIGN KEY (account_id,policy_hash) REFERENCES life_financial.policies,
    CHECK (min_inventory_base<=max_inventory_base),
    CHECK (subsidy_session<=subsidy_day AND subsidy_day<=subsidy_campaign),
    CHECK (loss_session<=loss_day AND loss_day<=loss_campaign)
);
CREATE TABLE life_financial.spot_preparations (
    account_id uuid NOT NULL, operation_id life_financial.identity NOT NULL,
    intent_id life_financial.identity NOT NULL, request jsonb NOT NULL CHECK (jsonb_typeof(request)='object'),
    quote_level integer NOT NULL CHECK (quote_level>=0),
    fee_base life_financial.amount NOT NULL, fee_quote life_financial.amount NOT NULL,
    subsidy life_financial.amount NOT NULL,
    prepared_at timestamptz NOT NULL CHECK (isfinite(prepared_at)),
    PRIMARY KEY (account_id,operation_id), UNIQUE (account_id,intent_id),
    FOREIGN KEY (account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (account_id,intent_id) REFERENCES life_financial.send_attempts
);
DO $$ DECLARE t text; BEGIN
    FOREACH t IN ARRAY ARRAY['spot_policies','spot_preparations'] LOOP
        EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',t);
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY scoped_read ON life_financial.%I FOR SELECT USING (life_financial.can_access(account_id))',t);
    END LOOP;
END; $$;

CREATE FUNCTION life_financial.spot_state(p_account uuid) RETURNS jsonb
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
    SELECT jsonb_object_agg(asset,jsonb_build_object('total',total::text,'reserved',reserved::text,'liability',liability::text,'version',version,
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
    unverified := EXISTS(SELECT 1 FROM life_financial.send_attempts t WHERE t.account_id=p_account AND t.state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND')
        AND (t.product<>'SPOT' OR t.session_id<>a.current_session OR t.epoch<>a.owner_epoch
            OR t.process_id<>a.owner_process OR t.generation<>a.owner_generation
            OR NOT EXISTS(SELECT 1 FROM life_financial.spot_preparations d JOIN life_financial.operations o USING(account_id,operation_id)
                WHERE d.account_id=t.account_id AND d.intent_id=t.intent_id AND (d.request->>'config_version')::bigint=s.config_version
                AND o.command#>>'{binding,policy_hash}'=a.policy_hash)))
        OR EXISTS(SELECT 1 FROM life_financial.capital_claims WHERE account_id=p_account AND state='HELD')
        OR EXISTS(SELECT 1 FROM life_financial.positions WHERE account_id=p_account AND quantity<>0)
        OR EXISTS(SELECT 1 FROM life_financial.financial_events WHERE account_id=p_account)
        OR EXISTS(SELECT 1 FROM life_financial.fills WHERE account_id=p_account)
        OR EXISTS(SELECT 1 FROM life_financial.cashflows WHERE account_id=p_account)
        OR EXISTS(SELECT 1 FROM life_financial.recorder_outbox o WHERE o.account_id=p_account
            AND NOT EXISTS(SELECT 1 FROM life_financial.projection_receipts r WHERE r.account_id=o.account_id AND r.projection_id=o.projection_id))
        OR EXISTS(SELECT 1 FROM life_financial.quote_actions WHERE account_id=p_account AND state NOT IN ('REJECTED','RECONCILED'))
        OR EXISTS(SELECT 1 FROM life_financial.balances b WHERE b.account_id=p_account AND
            b.reserved<>COALESCE((SELECT sum(r.remaining) FROM life_financial.reservations r WHERE r.account_id=p_account AND r.asset=b.asset AND r.state<>'TERMINAL'),0))
        OR EXISTS(SELECT 1 FROM life_financial.reservations r WHERE r.account_id=p_account AND r.state<>'TERMINAL'
            AND NOT EXISTS(SELECT 1 FROM life_financial.spot_preparations d WHERE d.account_id=r.account_id AND d.intent_id=r.intent_id
                AND (r.reservation_id=d.operation_id||':principal' OR r.reservation_id=d.operation_id||':fee:'||r.asset)))
        OR EXISTS(SELECT 1 FROM (
            SELECT w.scope,w.window_key,sum(d.subsidy) AS held FROM life_financial.spot_preparations d
            JOIN life_financial.send_attempts t USING(account_id,intent_id)
            CROSS JOIN LATERAL (VALUES ('CAMPAIGN',p.campaign_id::text),
                ('DAY',to_char(d.prepared_at AT TIME ZONE 'UTC','YYYY-MM-DD')),('SESSION',t.session_id::text)) AS w(scope,window_key)
            WHERE d.account_id=p_account AND t.state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND') GROUP BY w.scope,w.window_key
        ) expected LEFT JOIN life_financial.budget_buckets b ON b.account_id=p_account AND b.family='SUBSIDY'
            AND b.scope=expected.scope AND b.window_key=expected.window_key WHERE b.account_id IS NULL OR b.committed<expected.held)
        OR EXISTS(SELECT 1 FROM life_financial.spot_preparations d JOIN life_financial.send_attempts t USING(account_id,intent_id)
            WHERE d.account_id=p_account AND t.state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND') AND (
                NOT EXISTS(SELECT 1 FROM life_financial.reservations r WHERE r.account_id=p_account AND r.reservation_id=d.operation_id||':principal'
                    AND r.intent_id=t.intent_id AND r.state='HELD' AND r.remaining=r.amount
                    AND r.asset=CASE WHEN t.side='BUY' THEN 'USDT' ELSE 'LIFE' END
                    AND r.amount=CASE WHEN t.side='BUY' THEN t.quantity*t.price ELSE t.quantity END)
                OR d.fee_base<>COALESCE((SELECT remaining FROM life_financial.reservations WHERE account_id=p_account AND reservation_id=d.operation_id||':fee:LIFE' AND state='HELD'),0)
                OR d.fee_quote<>COALESCE((SELECT remaining FROM life_financial.reservations WHERE account_id=p_account AND reservation_id=d.operation_id||':fee:USDT' AND state='HELD'),0)));
    SELECT jsonb_object_agg(k,CASE WHEN jsonb_typeof(v)='number' AND k<>'max_quote_levels' THEN to_jsonb(v::text) ELSE v END)
        INTO policy FROM jsonb_each(to_jsonb(p)) AS fields(k,v);
    snapshot_body := jsonb_build_object('account',to_jsonb(a),'session',to_jsonb(s),'policy',policy,'balances',b,'budgets',budgets,
        'day',today,'recovery_required',unknown OR unverified,'unverified',unverified);
    RETURN snapshot_body||jsonb_build_object('digest',encode(sha256(convert_to(snapshot_body::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
END; $$;

CREATE FUNCTION life_financial.spot_prepare(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,p_command jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE previous life_financial.operations; a life_financial.accounts; s life_financial.sessions; p life_financial.spot_policies;
    view jsonb; request jsonb; result jsonb; op life_financial.identity; intent life_financial.identity; wire life_financial.identity;
    quantity life_financial.positive_amount; price life_financial.positive_amount; reference life_financial.positive_amount;
    fee_base life_financial.amount; fee_quote life_financial.amount; subsidy life_financial.amount;
    base_hold life_financial.amount; quote_hold life_financial.amount; buy_base numeric; sell_base numeric; gross numeric;
    scope_name text; key text; subsidy_limit numeric; loss_limit numeric; bucket life_financial.budget_buckets; at timestamptz;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR p_fingerprint IS NULL OR p_cut IS NULL OR p_command IS NULL
        OR jsonb_typeof(p_command)<>'object' OR octet_length(p_command::text)>16384 THEN
        RAISE EXCEPTION 'LIFE_SPOT_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    IF (SELECT count(*) FROM jsonb_object_keys(p_command))<>15 OR NOT p_command ?& ARRAY[
        'operation_id','intent_id','client_order_id','market','side','quantity_base','limit_price_usdt','reference_price_usdt',
        'subsidy_quote_usdt','session_id','epoch','config_version','quote_level','fee_buffer_quote_usdt','fee_buffer_base_life']
        OR EXISTS(SELECT 1 FROM jsonb_each(p_command) WHERE value='null'::jsonb) THEN
        RAISE EXCEPTION 'LIFE_SPOT_MODE_UNSUPPORTED' USING ERRCODE='P0001'; END IF;
    op := p_command->>'operation_id'; intent := p_command->>'intent_id'; wire := p_command->>'client_order_id';
    IF length(op)>96 OR wire !~ '^[A-Za-z0-9]{1,32}$' OR p_command->>'market'<>'LIFE-USDT' OR p_command->>'side' NOT IN ('BUY','SELL') THEN
        RAISE EXCEPTION 'LIFE_SPOT_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    -- Match the file port's current-owner/recovery gate even on exact replay.
    -- Historical receipt lookup remains available without a new-risk command.
    PERFORM life_financial.guard_account(p_account,p_binding,p_owner,a.version);
    request := jsonb_build_object('kind','SPOT_PREPARE','binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',p_command);
    SELECT * INTO previous FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF previous.command<>request OR previous.fingerprint<>p_fingerprint THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001'; END IF;
        IF NOT EXISTS(SELECT 1 FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=intent AND state='PREPARED')
            OR (life_financial.spot_state(p_account)->>'recovery_required')::boolean THEN
            RAISE EXCEPTION 'LIFE_SPOT_RECONCILE_BEFORE_RETRY' USING ERRCODE='P0001'; END IF;
        RETURN previous.receipt||'{"replayed":true}'::jsonb;
    END IF;
    PERFORM life_financial.guard_account(p_account,p_binding,p_owner,p_version);
    view := life_financial.spot_state(p_account);
    IF view->>'digest'<>p_cut THEN RAISE EXCEPTION 'LIFE_SPOT_CUT_CONFLICT' USING ERRCODE='P0001'; END IF;
    IF (view->>'recovery_required')::boolean THEN RAISE EXCEPTION 'LIFE_SPOT_RECOVERY_REQUIRED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT s FROM life_financial.sessions WHERE account_id=p_account AND session_id=a.current_session;
    SELECT * INTO STRICT p FROM life_financial.spot_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
    IF (p_command->>'session_id')::uuid<>s.session_id OR (p_command->>'epoch')::bigint<>s.epoch
        OR (p_command->>'config_version')::bigint<>s.config_version OR (p_command->>'quote_level')::integer NOT BETWEEN 0 AND p.max_quote_levels-1 THEN
        RAISE EXCEPTION 'LIFE_SPOT_SESSION_OR_SLOT_INVALID' USING ERRCODE='P0001'; END IF;
    IF EXISTS(SELECT 1 FROM life_financial.send_attempts t JOIN life_financial.spot_preparations d USING(account_id,intent_id)
        WHERE t.account_id=p_account AND t.state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND')
        AND t.side=p_command->>'side' AND d.quote_level=(p_command->>'quote_level')::integer) THEN
        RAISE EXCEPTION 'LIFE_SPOT_SLOT_CONFLICT' USING ERRCODE='P0001'; END IF;
    quantity := (p_command->>'quantity_base')::numeric; price := (p_command->>'limit_price_usdt')::numeric;
    reference := (p_command->>'reference_price_usdt')::numeric; fee_base := (p_command->>'fee_buffer_base_life')::numeric;
    fee_quote := (p_command->>'fee_buffer_quote_usdt')::numeric; subsidy := (p_command->>'subsidy_quote_usdt')::numeric;
    base_hold := CASE WHEN p_command->>'side'='SELL' THEN quantity ELSE 0 END;
    quote_hold := CASE WHEN p_command->>'side'='BUY' THEN quantity*price ELSE 0 END;
    IF (view#>>'{balances,LIFE,total}')::numeric-(view#>>'{balances,LIFE,reserved}')::numeric-(view#>>'{balances,LIFE,liability}')::numeric-base_hold-fee_base<p.min_inventory_base
        OR (view#>>'{balances,USDT,total}')::numeric-(view#>>'{balances,USDT,reserved}')::numeric-(view#>>'{balances,USDT,liability}')::numeric<quote_hold+fee_quote THEN
        RAISE EXCEPTION 'LIFE_SPOT_CAPITAL_INCLUDING_FEES_DENIED' USING ERRCODE='P0001'; END IF;
    SELECT COALESCE(sum(t.quantity) FILTER(WHERE t.side='BUY'),0),COALESCE(sum(t.quantity) FILTER(WHERE t.side='SELL'),0),COALESCE(sum(t.quantity*t.price),0)
        INTO buy_base,sell_base,gross FROM life_financial.send_attempts t
        WHERE t.account_id=p_account AND t.product='SPOT' AND t.state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND');
    buy_base := buy_base+CASE WHEN p_command->>'side'='BUY' THEN quantity ELSE 0 END;
    sell_base := sell_base+CASE WHEN p_command->>'side'='SELL' THEN quantity ELSE 0 END;
    IF (view#>>'{balances,LIFE,total}')::numeric+buy_base>p.max_inventory_base
        OR (view#>>'{balances,LIFE,total}')::numeric-sell_base<p.min_inventory_base
        OR greatest(abs((view#>>'{balances,LIFE,total}')::numeric+buy_base),abs((view#>>'{balances,LIFE,total}')::numeric-sell_base))>p.max_net_base
        OR (view#>>'{balances,LIFE,total}')::numeric*reference+gross+quantity*price>p.max_gross_quote THEN
        RAISE EXCEPTION 'LIFE_SPOT_EXPOSURE_DENIED' USING ERRCODE='P0001'; END IF;
    at := clock_timestamp();
    FOREACH scope_name IN ARRAY ARRAY['CAMPAIGN','DAY','SESSION'] LOOP
        key := CASE scope_name WHEN 'CAMPAIGN' THEN p.campaign_id WHEN 'DAY' THEN to_char(at AT TIME ZONE 'UTC','YYYY-MM-DD') ELSE s.session_id::text END;
        subsidy_limit := CASE scope_name WHEN 'CAMPAIGN' THEN p.subsidy_campaign WHEN 'DAY' THEN p.subsidy_day ELSE p.subsidy_session END;
        loss_limit := CASE scope_name WHEN 'CAMPAIGN' THEN p.loss_campaign WHEN 'DAY' THEN p.loss_day ELSE p.loss_session END;
        IF EXISTS(SELECT 1 FROM life_financial.budget_buckets WHERE account_id=p_account AND family='LOSS' AND scope=scope_name
            AND window_key=key AND (budget_limit<>loss_limit OR incurred+committed>=loss_limit)) THEN
            RAISE EXCEPTION 'LIFE_SPOT_LOSS_BUDGET_DENIED' USING ERRCODE='P0001'; END IF;
        INSERT INTO life_financial.budget_buckets VALUES(p_account,'SUBSIDY',scope_name,key,subsidy_limit,0,0,0) ON CONFLICT DO NOTHING;
        SELECT * INTO STRICT bucket FROM life_financial.budget_buckets WHERE account_id=p_account AND family='SUBSIDY' AND scope=scope_name AND window_key=key FOR UPDATE;
        IF bucket.budget_limit<>subsidy_limit OR bucket.committed+bucket.incurred+subsidy>subsidy_limit THEN
            RAISE EXCEPTION 'LIFE_SPOT_SUBSIDY_BUDGET_DENIED' USING ERRCODE='P0001'; END IF;
        UPDATE life_financial.budget_buckets SET committed=committed+subsidy,version=version+1
            WHERE account_id=p_account AND family='SUBSIDY' AND scope=scope_name AND window_key=key;
        INSERT INTO life_financial.budget_events VALUES(p_account,op||':subsidy:'||scope_name,'SUBSIDY',scope_name,key,'COMMIT',subsidy);
    END LOOP;
    -- Canonical storage intent; final transport byte equivalence is a later PG3 sender gate.
    INSERT INTO life_financial.send_attempts(account_id,intent_id,operation_id,wire_id,product,side,session_id,process_id,generation,epoch,payload,payload_hash,quantity,price,state)
        VALUES(p_account,intent,op,wire,'SPOT',p_command->>'side',s.session_id,a.owner_process,a.owner_generation,a.owner_epoch,
            convert_to(p_command::text,'UTF8'),encode(sha256(convert_to(p_command::text,'UTF8')),'hex'),quantity,price,'PREPARED');
    INSERT INTO life_financial.spot_preparations VALUES(p_account,op,intent,p_command,(p_command->>'quote_level')::integer,fee_base,fee_quote,subsidy,at);
    INSERT INTO life_financial.reservations(account_id,reservation_id,operation_id,intent_id,asset,amount,remaining,state)
        VALUES(p_account,op||':principal',op,intent,CASE WHEN base_hold>0 THEN 'LIFE' ELSE 'USDT' END,quantity*CASE WHEN base_hold>0 THEN 1 ELSE price END,
            quantity*CASE WHEN base_hold>0 THEN 1 ELSE price END,'HELD');
    IF fee_base>0 THEN INSERT INTO life_financial.reservations(account_id,reservation_id,operation_id,intent_id,asset,amount,remaining,state)
        VALUES(p_account,op||':fee:LIFE',op,intent,'LIFE',fee_base,fee_base,'HELD'); END IF;
    IF fee_quote>0 THEN INSERT INTO life_financial.reservations(account_id,reservation_id,operation_id,intent_id,asset,amount,remaining,state)
        VALUES(p_account,op||':fee:USDT',op,intent,'USDT',fee_quote,fee_quote,'HELD'); END IF;
    UPDATE life_financial.balances SET reserved=reserved+CASE asset WHEN 'LIFE' THEN base_hold+fee_base ELSE quote_hold+fee_quote END,version=version+1 WHERE account_id=p_account;
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    result := jsonb_build_object('kind','SPOT_PREPARE','operation_id',op,'intent_id',intent,'state','PREPARED','version',p_version+1,'replayed',false,'trading_permission',false);
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version)
        VALUES(p_account,op,p_fingerprint,request,result,p_version+1);
    RETURN result;
END; $$;

CREATE FUNCTION life_financial.spot_arm(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,p_prepare life_financial.identity) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE d life_financial.spot_preparations; t life_financial.send_attempts; view jsonb; result jsonb;
BEGIN
    PERFORM life_financial.guard_account(p_account,p_binding,p_owner,p_version);
    view := life_financial.spot_state(p_account);
    IF p_cut IS NULL OR p_fingerprint IS NULL OR view->>'digest'<>p_cut THEN RAISE EXCEPTION 'LIFE_SPOT_CUT_CONFLICT' USING ERRCODE='P0001'; END IF;
    IF (view->>'recovery_required')::boolean THEN RAISE EXCEPTION 'LIFE_SPOT_RECOVERY_REQUIRED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT d FROM life_financial.spot_preparations WHERE account_id=p_account AND operation_id=p_prepare;
    SELECT * INTO STRICT t FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=d.intent_id;
    IF t.state<>'PREPARED' OR t.process_id<>(p_owner->>'process_id')::uuid OR t.generation<>(p_owner->>'generation')::uuid
        OR t.epoch<>(p_owner->>'epoch')::bigint OR t.session_id<>(p_owner->>'session_id')::uuid THEN
        RAISE EXCEPTION 'LIFE_SPOT_RECONCILE_BEFORE_RETRY' USING ERRCODE='P0001'; END IF;
    -- No reusable arm replay. Commit may precede an absent/unknown external request.
    UPDATE life_financial.send_attempts SET state='ARMED',version=version+1 WHERE account_id=p_account AND intent_id=d.intent_id;
    UPDATE life_financial.send_attempts SET state='SEND_UNKNOWN',version=version+1 WHERE account_id=p_account AND intent_id=d.intent_id;
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    result := jsonb_build_object('kind','SPOT_ARM','operation_id',p_prepare,'intent_id',d.intent_id,'state','SEND_UNKNOWN','version',p_version+1,'replayed',false,'trading_permission',false);
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version)
        VALUES(p_account,'arm:'||p_prepare,p_fingerprint,jsonb_build_object('binding',p_binding,'owner',p_owner,'cut',p_cut,'prepare',p_prepare),result,p_version+1);
    RETURN result;
END; $$;
REVOKE ALL ON life_financial.spot_policies,life_financial.spot_preparations FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
