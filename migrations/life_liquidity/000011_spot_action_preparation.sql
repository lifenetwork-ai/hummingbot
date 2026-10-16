-- Bind a qualified captured action to principal/fee/subsidy holds atomically.
-- No ownership, source authenticity, external runner or send authority is seeded.
CREATE TABLE life_financial.spot_quote_admission_policies (
    account_id uuid PRIMARY KEY REFERENCES life_financial.spot_action_policies,
    policy_hash life_financial.digest NOT NULL,
    objective text NOT NULL CHECK (objective IN ('profit_mm','liquidity_service')),
    min_net_edge_bps life_financial.amount NOT NULL,
    uncertainty_bps life_financial.amount NOT NULL,
    tick_size life_financial.positive_amount NOT NULL,
    lot_size life_financial.positive_amount NOT NULL,
    min_size life_financial.positive_amount NOT NULL,
    max_fact_age_ms integer NOT NULL CHECK (max_fact_age_ms>0),
    max_reference_age_ms integer NOT NULL CHECK (max_reference_age_ms>0),
    max_fee_age_ms integer NOT NULL CHECK (max_fee_age_ms>0),
    max_capture_age_ms integer NOT NULL CHECK (max_capture_age_ms>0),
    FOREIGN KEY(account_id,policy_hash) REFERENCES life_financial.policies
);
ALTER TABLE life_financial.spot_quote_admission_policies ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped_read ON life_financial.spot_quote_admission_policies FOR SELECT USING (life_financial.can_access(account_id));
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.spot_quote_admission_policies
    FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record();

CREATE FUNCTION life_financial.guard_quote_admission(p_account uuid,p_binding jsonb,p_action jsonb,p_prepare jsonb,p_facts jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.spot_quote_admission_policies; cfg jsonb; at timestamptz; observed timestamptz; event_at timestamptz;
    quantity life_financial.positive_amount; price life_financial.positive_amount; exit_value life_financial.positive_amount;
    reference life_financial.positive_amount; bid life_financial.positive_amount; ask life_financial.positive_amount;
    maker life_financial.signed_amount; exit_fee life_financial.signed_amount; impact life_financial.amount;
    carry life_financial.amount; inventory life_financial.amount; net numeric; subsidy numeric; item text;
BEGIN
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
        OR (p_prepare->>'subsidy_quote_usdt')::life_financial.amount<>subsidy THEN
        RAISE EXCEPTION 'LIFE_QUOTE_ECONOMICS_DENIED' USING ERRCODE='P0001'; END IF;
END; $$;

CREATE OR REPLACE FUNCTION life_financial.spot_actions_verified(p_account uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT NOT EXISTS(
    SELECT 1 FROM life_financial.quote_actions q WHERE q.account_id=p_account AND NOT EXISTS(
        SELECT 1 FROM life_financial.spot_action_descriptors d JOIN life_financial.spot_action_events e USING(account_id,action_id)
        JOIN life_financial.operations o USING(account_id,operation_id)
        WHERE d.account_id=q.account_id AND d.action_id=q.action_id AND e.action_version=q.version
            AND e.after_state=to_jsonb(q) AND e.descriptor=d.descriptor AND e.command=o.command->'command'
            AND o.command->>'kind' IN ('SPOT_ACTION_CLAIM','SPOT_ACTION_TRANSITION','SPOT_PREPARE')
            AND o.receipt->'states' @> jsonb_build_array(jsonb_build_array(q.action_id,q.state))
            AND d.descriptor->>'intent_id'=q.action_id AND d.descriptor->>'session_id'=q.session_id::text
            AND d.descriptor->>'side'=q.side AND (d.descriptor->>'level')::integer=q.level
            AND q.version=(SELECT max(x.action_version) FROM life_financial.spot_action_events x
                WHERE x.account_id=q.account_id AND x.action_id=q.action_id)))
AND NOT EXISTS(
    SELECT 1 FROM life_financial.spot_action_events e JOIN life_financial.operations o USING(account_id,operation_id)
    WHERE e.account_id=p_account AND (e.command IS DISTINCT FROM o.command->'command'
        OR e.descriptor IS DISTINCT FROM CASE o.command->>'kind'
            WHEN 'SPOT_ACTION_CLAIM' THEN (SELECT value FROM jsonb_array_elements(e.command->'actions')
                WHERE value->>'intent_id'=e.action_id)
            WHEN 'SPOT_ACTION_TRANSITION' THEN e.command->'action'
            WHEN 'SPOT_PREPARE' THEN o.command->'action' END
        OR NOT o.receipt->'states' @> jsonb_build_array(jsonb_build_array(e.action_id,e.after_state->>'state'))))
AND NOT EXISTS(SELECT 1 FROM life_financial.quote_actions q WHERE q.account_id=p_account AND q.state IN ('PROPOSED','DISPATCHED','REJECTED')
    AND (EXISTS(SELECT 1 FROM life_financial.send_attempts t WHERE t.account_id=q.account_id AND t.intent_id=q.action_id)
        OR EXISTS(SELECT 1 FROM life_financial.reservations r WHERE r.account_id=q.account_id AND r.intent_id=q.action_id)) )
AND NOT EXISTS(SELECT 1 FROM life_financial.quote_actions q WHERE q.account_id=p_account AND (
    q.state='RECONCILED' OR q.state='PREPARED' AND NOT EXISTS(
        SELECT 1 FROM life_financial.send_attempts t JOIN life_financial.spot_preparations d USING(account_id,intent_id)
        JOIN life_financial.operations o ON o.account_id=t.account_id AND o.operation_id=t.operation_id
        JOIN life_financial.spot_action_descriptors x ON x.account_id=q.account_id AND x.action_id=q.action_id
        WHERE t.account_id=q.account_id AND t.intent_id=q.action_id AND t.session_id=q.session_id AND t.side=q.side
            AND d.quote_level=q.level AND o.command->>'kind'='SPOT_PREPARE' AND o.command->'action'=x.descriptor
            AND o.command->'command'=d.request AND o.command ? 'admission')));
$$;


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
    unverified := EXISTS(SELECT 1 FROM life_financial.send_attempts t WHERE t.account_id=p_account AND t.state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND')
        AND (t.product<>'SPOT' OR t.session_id<>a.current_session OR t.epoch<>a.owner_epoch
            OR t.process_id<>a.owner_process OR t.generation<>a.owner_generation
            OR NOT EXISTS(SELECT 1 FROM life_financial.spot_preparations d JOIN life_financial.operations o USING(account_id,operation_id)
                WHERE d.account_id=t.account_id AND d.intent_id=t.intent_id AND (d.request->>'config_version')::bigint=s.config_version
                AND o.command#>>'{binding,policy_hash}'=a.policy_hash)))
        OR EXISTS(SELECT 1 FROM life_financial.capital_claims WHERE account_id=p_account AND state='HELD')
        OR EXISTS(SELECT 1 FROM life_financial.positions WHERE account_id=p_account AND quantity<>0)
        OR NOT life_financial.spot_accounting_verified(p_account)
        OR EXISTS(SELECT 1 FROM life_financial.financial_events e WHERE e.account_id=p_account AND
            (e.product<>'SPOT' AND NOT (e.product='ACCOUNT' AND e.kind='CASHFLOW') OR e.kind NOT IN ('ACK','FILL','CASHFLOW') OR e.kind='ACK' AND NOT EXISTS(
                SELECT 1 FROM life_financial.send_attempts t WHERE t.account_id=p_account AND t.product='SPOT' AND t.exchange_order_id=e.exchange_identity)
            OR e.kind='CASHFLOW' AND NOT EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts x WHERE x.account_id=p_account AND x.bill_id=e.exchange_identity AND x.state='APPLIED')
            OR e.kind='FILL' AND NOT EXISTS(SELECT 1 FROM life_financial.spot_fill_facts x WHERE x.account_id=p_account AND x.trade_id=e.exchange_identity)))
        OR NOT life_financial.spot_capital_verified(p_account)
        OR EXISTS(SELECT 1 FROM life_financial.recorder_outbox o WHERE o.account_id=p_account
            AND NOT EXISTS(SELECT 1 FROM life_financial.projection_receipts r WHERE r.account_id=o.account_id AND r.projection_id=o.projection_id))
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

CREATE OR REPLACE FUNCTION life_financial.spot_state(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE body jsonb; current_id uuid; verified boolean;
BEGIN
    body:=life_financial.spot_state_v7(p_account)-'digest'-'observed_at';
    current_id:=(body#>>'{account,current_session}')::uuid;
    verified:=life_financial.spot_session_verified(p_account) AND life_financial.spot_lifecycle_verified(p_account)
        AND life_financial.spot_actions_verified(p_account);
    body:=body||jsonb_build_object(
        'session_lifecycle',(SELECT to_jsonb(g) FROM life_financial.spot_session_progress g WHERE g.account_id=p_account AND g.session_id=current_id),
        'session_policy',(SELECT to_jsonb(p) FROM life_financial.spot_session_policies p WHERE p.account_id=p_account AND p.session_id=current_id),
        'order_policy',(SELECT to_jsonb(p) FROM life_financial.spot_order_policies p WHERE p.account_id=p_account),
        'cancel_progress',(SELECT COALESCE(jsonb_agg(to_jsonb(g) ORDER BY intent_id),'[]'::jsonb) FROM life_financial.spot_cancel_progress g WHERE g.account_id=p_account),
        'quote_admission_policy',(SELECT to_jsonb(p) FROM life_financial.spot_quote_admission_policies p WHERE p.account_id=p_account),
        'action_policy',(SELECT to_jsonb(p) FROM life_financial.spot_action_policies p WHERE p.account_id=p_account),
        'quote_actions',(SELECT COALESCE(jsonb_agg(jsonb_build_object('row',to_jsonb(q),'descriptor',d.descriptor) ORDER BY q.action_id),'[]'::jsonb)
            FROM life_financial.quote_actions q LEFT JOIN life_financial.spot_action_descriptors d USING(account_id,action_id) WHERE q.account_id=p_account),
        'financial_recovery_required',(body->>'recovery_required')::boolean OR NOT verified,
        'recovery_required',(body->>'recovery_required')::boolean OR NOT verified
            OR EXISTS(SELECT 1 FROM life_financial.quote_actions WHERE account_id=p_account AND state IN ('PROPOSED','DISPATCHED')),
        'unverified',(body->>'unverified')::boolean OR NOT verified);
    RETURN body||jsonb_build_object('digest',encode(sha256(convert_to(body::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
END; $$;


CREATE FUNCTION life_financial.spot_prepare_action_core(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,p_command jsonb,p_context jsonb) RETURNS jsonb
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
    IF p_context IS NOT NULL THEN request:=request||p_context; END IF;
    SELECT * INTO previous FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF previous.command<>request OR previous.fingerprint<>p_fingerprint THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001'; END IF;
        IF NOT EXISTS(SELECT 1 FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=intent AND state='PREPARED')
            OR (life_financial.spot_state(p_account)->>(CASE WHEN p_context IS NULL THEN 'recovery_required' ELSE 'financial_recovery_required' END))::boolean THEN
            RAISE EXCEPTION 'LIFE_SPOT_RECONCILE_BEFORE_RETRY' USING ERRCODE='P0001'; END IF;
        RETURN previous.receipt||'{"replayed":true}'::jsonb;
    END IF;
    PERFORM life_financial.guard_account(p_account,p_binding,p_owner,p_version);
    view := life_financial.spot_state(p_account);
    IF view->>'digest'<>p_cut THEN RAISE EXCEPTION 'LIFE_SPOT_CUT_CONFLICT' USING ERRCODE='P0001'; END IF;
    IF (view->>(CASE WHEN p_context IS NULL THEN 'recovery_required' ELSE 'financial_recovery_required' END))::boolean THEN RAISE EXCEPTION 'LIFE_SPOT_RECOVERY_REQUIRED' USING ERRCODE='P0001'; END IF;
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
    SELECT COALESCE(sum(t.quantity-COALESCE(g.filled_base,0)) FILTER(WHERE t.side='BUY'),0),
        COALESCE(sum(t.quantity-COALESCE(g.filled_base,0)) FILTER(WHERE t.side='SELL'),0),
        COALESCE(sum((t.quantity-COALESCE(g.filled_base,0))*t.price),0)
        INTO buy_base,sell_base,gross FROM life_financial.send_attempts t
        LEFT JOIN life_financial.spot_accounting_progress g USING(account_id,intent_id)
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
    IF p_context IS NOT NULL THEN result:=result||jsonb_build_object('states',jsonb_build_array(jsonb_build_array(intent,'PREPARED'))); END IF;
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version)
        VALUES(p_account,op,p_fingerprint,request,result,p_version+1);
    RETURN result;
END; $$;

CREATE OR REPLACE FUNCTION life_financial.spot_action_command(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,p_kind text,p_command jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts; s life_financial.sessions; policy life_financial.spot_action_policies;
    previous life_financial.operations; claim_op life_financial.operations; q life_financial.quote_actions; view jsonb; request jsonb; result jsonb;
    record jsonb; facts jsonb; records jsonb; op life_financial.identity; item text; next_state text;
    observed timestamptz; at timestamptz; states jsonb:='[]'::jsonb;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR p_kind IS NULL OR p_kind NOT IN ('SPOT_ACTION_CLAIM','SPOT_ACTION_TRANSITION')
        OR p_cut IS NULL OR p_fingerprint IS NULL OR p_command IS NULL OR jsonb_typeof(p_command)<>'object'
        OR octet_length(p_command::text)>1048576 THEN RAISE EXCEPTION 'LIFE_ACTION_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    IF p_kind='SPOT_ACTION_CLAIM' THEN
        IF (SELECT count(*) FROM jsonb_object_keys(p_command))<>2 OR NOT p_command ?& ARRAY['operation_id','actions']
            OR jsonb_typeof(p_command->'actions') IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION 'LIFE_ACTION_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
        records:=p_command->'actions'; next_state:='PROPOSED';
    ELSE
        IF (SELECT count(*) FROM jsonb_object_keys(p_command))<>5 OR NOT p_command ?& ARRAY['operation_id','action','expected_state','state','facts']
            OR jsonb_typeof(p_command->'expected_state') IS DISTINCT FROM 'string'
            OR jsonb_typeof(p_command->'state') IS DISTINCT FROM 'string'
            OR NOT (p_command->>'expected_state'='PROPOSED' AND p_command->>'state' IN ('DISPATCHED','REJECTED','RECONCILED')
                OR p_command->>'expected_state'='DISPATCHED' AND p_command->>'state'='RECONCILED') THEN
            RAISE EXCEPTION 'LIFE_ACTION_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
        records:=jsonb_build_array(p_command->'action'); next_state:=p_command->>'state';
    END IF;
    IF jsonb_typeof(p_command->'operation_id') IS DISTINCT FROM 'string' OR length(p_command->>'operation_id')>96 THEN
        RAISE EXCEPTION 'LIFE_ACTION_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    op:=p_command->>'operation_id';
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    request:=jsonb_build_object('kind',p_kind,'binding',p_binding,'owner',p_owner,'command',p_command);
    SELECT * INTO previous FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF previous.command<>request OR previous.fingerprint<>p_fingerprint THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001'; END IF;
        RETURN previous.receipt||'{"replayed":true}'::jsonb;
    END IF;
    IF next_state='RECONCILED' THEN RAISE EXCEPTION 'LIFE_ACTION_INTEGRATION_PENDING' USING ERRCODE='P0001'; END IF;
    IF p_kind='SPOT_ACTION_CLAIM' OR next_state='DISPATCHED' THEN
        PERFORM life_financial.guard_account(p_account,p_binding,p_owner,p_version);
        PERFORM life_financial.guard_spot_session(p_account);
    ELSE PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version); END IF;
    SELECT * INTO policy FROM life_financial.spot_action_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
    IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_ACTION_POLICY_REQUIRED' USING ERRCODE='P0001'; END IF;
    view:=life_financial.spot_state(p_account);
    IF view->>'digest'<>p_cut THEN RAISE EXCEPTION 'LIFE_ACTION_CUT_STALE' USING ERRCODE='P0001'; END IF;
    IF NOT life_financial.spot_actions_verified(p_account) THEN RAISE EXCEPTION 'LIFE_ACTION_PROJECTION_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT s FROM life_financial.sessions WHERE account_id=p_account AND session_id=a.current_session;
    IF jsonb_array_length(records) NOT BETWEEN 1 AND policy.max_batch THEN RAISE EXCEPTION 'LIFE_ACTION_BATCH_INVALID' USING ERRCODE='P0001'; END IF;
    -- Pending publication still blocks new batches, but verified live siblings
    -- need not prevent a claim: late accounting uses the integrity barrier.
    IF p_kind='SPOT_ACTION_CLAIM' AND (view->>'recovery_required')::boolean THEN
        RAISE EXCEPTION 'LIFE_ACTION_INTEGRATION_PENDING' USING ERRCODE='P0001'; END IF;
    IF p_kind='SPOT_ACTION_TRANSITION' THEN
        facts:=p_command->'facts';
        IF jsonb_typeof(facts) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(facts))<>5
            OR NOT facts ?& ARRAY['binding','observed_at','controller_id','executor_ids','scope_complete']
            OR facts->'binding' IS DISTINCT FROM p_binding OR facts->'scope_complete' IS DISTINCT FROM 'true'::jsonb
            OR facts->>'controller_id' IS DISTINCT FROM policy.controller_id
            OR jsonb_typeof(facts->'observed_at') IS DISTINCT FROM 'string'
            OR jsonb_typeof(facts->'executor_ids') IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION 'LIFE_ACTION_RUNNER_FACTS_INVALID' USING ERRCODE='P0001'; END IF;
        IF jsonb_array_length(facts->'executor_ids')>4096 OR EXISTS(SELECT 1 FROM jsonb_array_elements(facts->'executor_ids') WHERE jsonb_typeof(value)<>'string')
            OR (SELECT count(*)<>count(DISTINCT value) FROM jsonb_array_elements(facts->'executor_ids')) THEN
            RAISE EXCEPTION 'LIFE_ACTION_RUNNER_FACTS_INVALID' USING ERRCODE='P0001'; END IF;
        FOR item IN SELECT jsonb_array_elements_text(facts->'executor_ids') LOOP PERFORM item::life_financial.identity; END LOOP;
        observed:=(facts->>'observed_at')::timestamptz; at:=clock_timestamp();
        IF NOT isfinite(observed) OR observed>at OR observed<s.started_at OR extract(epoch FROM at-observed)*1000>policy.max_fact_age_ms THEN
            RAISE EXCEPTION 'LIFE_ACTION_RUNNER_FACTS_STALE' USING ERRCODE='P0001'; END IF;
    END IF;
    FOR record IN SELECT jsonb_array_elements(records) LOOP
        IF jsonb_typeof(record) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(record))<>9
            OR NOT record ?& ARRAY['intent_id','controller_id','session_id','epoch','config_version','market','side','level','payload_json']
            OR EXISTS(SELECT 1 FROM jsonb_each(record) WHERE value='null'::jsonb)
            OR EXISTS(SELECT 1 FROM jsonb_each(record) WHERE key IN ('intent_id','controller_id','session_id','market','side','payload_json') AND jsonb_typeof(value)<>'string')
            OR EXISTS(SELECT 1 FROM jsonb_each(record) WHERE key IN ('epoch','config_version','level') AND (jsonb_typeof(value)<>'number' OR value::text !~ '^(0|[1-9][0-9]*)$')) THEN
            RAISE EXCEPTION 'LIFE_ACTION_DESCRIPTOR_INVALID' USING ERRCODE='P0001'; END IF;
        PERFORM (record->>'intent_id')::life_financial.identity;
        IF record->>'controller_id'<>policy.controller_id OR record->>'market'<>'LIFE-USDT' OR record->>'side' NOT IN ('BUY','SELL')
            OR (record->>'session_id')::uuid<>a.current_session OR (record->>'epoch')::bigint<>s.epoch
            OR (record->>'config_version')::bigint<>s.config_version OR (record->>'level')::bigint>2147483647
            OR octet_length(record->>'payload_json') NOT BETWEEN 1 AND policy.max_payload_bytes
            OR jsonb_typeof((record->>'payload_json')::jsonb)<>'object' OR (record->>'payload_json')::jsonb='{}'::jsonb THEN
            RAISE EXCEPTION 'LIFE_ACTION_DESCRIPTOR_INVALID' USING ERRCODE='P0001'; END IF;
        IF p_kind='SPOT_ACTION_CLAIM' THEN
            IF EXISTS(SELECT 1 FROM life_financial.quote_actions WHERE account_id=p_account AND action_id=record->>'intent_id')
                OR EXISTS(SELECT 1 FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=record->>'intent_id') THEN
                RAISE EXCEPTION 'LIFE_ACTION_IDENTITY_USED' USING ERRCODE='P0001'; END IF;
            INSERT INTO life_financial.quote_actions VALUES(p_account,record->>'intent_id',a.current_session,record->>'side',(record->>'level')::integer,'PROPOSED',0) RETURNING * INTO q;
            INSERT INTO life_financial.spot_action_descriptors VALUES(p_account,q.action_id,record);
        ELSE
            SELECT q0.* INTO q FROM life_financial.quote_actions q0 JOIN life_financial.spot_action_descriptors d USING(account_id,action_id)
                WHERE q0.account_id=p_account AND q0.action_id=record->>'intent_id' AND d.descriptor=record;
            IF NOT FOUND OR q.state<>p_command->>'expected_state' THEN RAISE EXCEPTION 'LIFE_ACTION_STATE_OR_IDENTITY_CONFLICT' USING ERRCODE='P0001'; END IF;
            SELECT o.* INTO claim_op FROM life_financial.spot_action_events e JOIN life_financial.operations o USING(account_id,operation_id)
                WHERE e.account_id=p_account AND e.action_id=q.action_id AND e.action_version=0 AND o.command->>'kind'='SPOT_ACTION_CLAIM';
            IF NOT FOUND OR ((claim_op.command->'owner')-'expires_at') IS DISTINCT FROM (p_owner-'expires_at')
                OR next_state='DISPATCHED' AND claim_op.command->'owner' IS DISTINCT FROM p_owner THEN
                RAISE EXCEPTION 'LIFE_ACTION_ORIGINAL_WRITER_REQUIRED' USING ERRCODE='P0001'; END IF;
            IF observed<claim_op.recorded_at THEN RAISE EXCEPTION 'LIFE_ACTION_RUNNER_FACTS_BEFORE_CLAIM' USING ERRCODE='P0001'; END IF;
            IF next_state='DISPATCHED' AND NOT facts->'executor_ids' @> jsonb_build_array(q.action_id)
                OR next_state<>'DISPATCHED' AND facts->'executor_ids' @> jsonb_build_array(q.action_id) THEN
                RAISE EXCEPTION 'LIFE_ACTION_RUNNER_SCOPE_CONFLICT' USING ERRCODE='P0001'; END IF;
            IF next_state='REJECTED' AND (EXISTS(SELECT 1 FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=q.action_id)
                OR EXISTS(SELECT 1 FROM life_financial.reservations WHERE account_id=p_account AND intent_id=q.action_id)) THEN
                RAISE EXCEPTION 'LIFE_ACTION_ACCEPTANCE_UNCERTAIN' USING ERRCODE='P0001'; END IF;
            UPDATE life_financial.quote_actions SET state=next_state,version=version+1 WHERE account_id=p_account AND action_id=q.action_id RETURNING * INTO q;
        END IF;
        INSERT INTO life_financial.spot_action_events VALUES(p_account,q.action_id,q.version,op,p_command,record,to_jsonb(q));
        states:=states||jsonb_build_array(jsonb_build_array(q.action_id,q.state));
    END LOOP;
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    result:=jsonb_build_object('kind',p_kind,'operation_id',op,'states',states,'version',p_version+1,'replayed',false,'trading_permission',false);
    INSERT INTO life_financial.operations VALUES(p_account,op,p_fingerprint,request,result,p_version+1,clock_timestamp());
    RETURN result;
END; $$;


CREATE OR REPLACE FUNCTION life_financial.spot_prepare(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,p_command jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
    PERFORM life_financial.guard_spot_session(p_account);
    IF EXISTS(SELECT 1 FROM life_financial.quote_actions q WHERE q.account_id=p_account AND (
        q.action_id=p_command->>'intent_id' OR q.state NOT IN ('REJECTED','RECONCILED')
        AND q.side=p_command->>'side' AND q.level=(p_command->>'quote_level')::integer)) THEN
        RAISE EXCEPTION 'LIFE_CLAIM_BOUND_PREPARATION_REQUIRED' USING ERRCODE='P0001'; END IF;
    RETURN life_financial.spot_prepare_action_core(p_account,p_binding,p_owner,p_version,p_cut,p_fingerprint,p_command,NULL);
END; $$;

CREATE FUNCTION life_financial.spot_action_prepare(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,p_command jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts; q life_financial.quote_actions; action jsonb; prep jsonb; facts jsonb; result jsonb;
    origin life_financial.operations; dispatch_at timestamptz; context jsonb; item text;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR p_command IS NULL OR jsonb_typeof(p_command)<>'object'
        OR octet_length(p_command::text)>1048576 OR (SELECT count(*) FROM jsonb_object_keys(p_command))<>3
        OR NOT p_command ?& ARRAY['action','prepare','admission'] THEN
        RAISE EXCEPTION 'LIFE_ACTION_PREPARATION_INVALID' USING ERRCODE='P0001'; END IF;
    action:=p_command->'action'; prep:=p_command->'prepare'; facts:=p_command->'admission'; context:=p_command-'prepare';
    IF jsonb_typeof(action) IS DISTINCT FROM 'object' OR jsonb_typeof(prep) IS DISTINCT FROM 'object'
        OR jsonb_typeof(facts) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'LIFE_ACTION_PREPARATION_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    PERFORM life_financial.guard_account(p_account,p_binding,p_owner,a.version);
    PERFORM life_financial.guard_spot_session(p_account);
    IF EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=p_account AND operation_id=prep->>'operation_id') THEN
        RETURN life_financial.spot_prepare_action_core(p_account,p_binding,p_owner,p_version,p_cut,p_fingerprint,prep,context); END IF;
    SELECT q0.* INTO q FROM life_financial.quote_actions q0 JOIN life_financial.spot_action_descriptors d USING(account_id,action_id)
        WHERE q0.account_id=p_account AND q0.action_id=action->>'intent_id' AND d.descriptor=action AND q0.state='DISPATCHED';
    IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_ACTION_NOT_DISPATCHED_OR_CHANGED' USING ERRCODE='P0001'; END IF;
    FOREACH item IN ARRAY ARRAY['intent_id','session_id','epoch','config_version','market','side'] LOOP
        IF action->item IS DISTINCT FROM prep->item THEN RAISE EXCEPTION 'LIFE_ACTION_PREPARATION_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
    END LOOP;
    IF action->'level' IS DISTINCT FROM prep->'quote_level' THEN RAISE EXCEPTION 'LIFE_ACTION_PREPARATION_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT o.* INTO origin FROM life_financial.spot_action_events e JOIN life_financial.operations o USING(account_id,operation_id)
        WHERE e.account_id=p_account AND e.action_id=q.action_id AND e.action_version=0;
    IF NOT FOUND OR origin.command->'owner' IS DISTINCT FROM p_owner THEN RAISE EXCEPTION 'LIFE_ACTION_ORIGINAL_WRITER_REQUIRED' USING ERRCODE='P0001'; END IF;
    SELECT o.recorded_at INTO dispatch_at FROM life_financial.spot_action_events e JOIN life_financial.operations o USING(account_id,operation_id)
        WHERE e.account_id=p_account AND e.action_id=q.action_id AND e.action_version=q.version;
    IF (facts->>'observed_at')::timestamptz<dispatch_at THEN RAISE EXCEPTION 'LIFE_ACTION_ADMISSION_BEFORE_DISPATCH' USING ERRCODE='P0001'; END IF;
    PERFORM life_financial.guard_quote_admission(p_account,p_binding,action,prep,facts);
    result:=life_financial.spot_prepare_action_core(p_account,p_binding,p_owner,p_version,p_cut,p_fingerprint,prep,context);
    UPDATE life_financial.quote_actions SET state='PREPARED',version=version+1 WHERE account_id=p_account AND action_id=q.action_id RETURNING * INTO q;
    INSERT INTO life_financial.spot_action_events VALUES(p_account,q.action_id,q.version,prep->>'operation_id',prep,action,to_jsonb(q));
    RETURN result;
END; $$;

CREATE FUNCTION life_financial.spot_action_prepare_commit_guard(p_account uuid,p_request jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
    IF NOT life_financial.spot_actions_verified(p_account) THEN RAISE EXCEPTION 'LIFE_ACTION_PREPARATION_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    PERFORM life_financial.guard_quote_admission(p_account,p_request->'binding',p_request->'action',p_request->'command',p_request->'admission');
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
    IF op.command->>'kind' IN ('SPOT_ACK','SPOT_FILL','SPOT_CASHFLOW','SPOT_NAV','SPOT_SESSION_TICK','SPOT_LIFECYCLE','SPOT_TERMINAL') OR op.command->>'kind'='SPOT_ACTION_TRANSITION'
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
    IF op.command->>'kind' IN ('SPOT_ACTION_CLAIM','SPOT_ACTION_TRANSITION') THEN
        PERFORM life_financial.spot_action_commit_guard(NEW.account_id,op.command); END IF;
    IF op.command->>'kind'='SPOT_PREPARE' AND op.command ? 'action' THEN
        PERFORM life_financial.spot_action_prepare_commit_guard(NEW.account_id,op.command); END IF;
    RETURN NULL;
END; $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
