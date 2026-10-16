-- Qualified standalone SPOT observations; no send/release/grant lifecycle.
CREATE TABLE life_financial.spot_fill_policies (
    account_id uuid NOT NULL, policy_hash life_financial.digest NOT NULL,
    max_reference_skew_ms integer NOT NULL CHECK (max_reference_skew_ms BETWEEN 0 AND 60000),
    max_snapshot_fills integer NOT NULL CHECK (max_snapshot_fills BETWEEN 1 AND 10000),
    PRIMARY KEY (account_id,policy_hash), FOREIGN KEY (account_id,policy_hash) REFERENCES life_financial.policies
);
CREATE TABLE life_financial.spot_fill_facts (
    account_id uuid NOT NULL, trade_id life_financial.identity NOT NULL,
    product text NOT NULL CHECK (product='SPOT'), intent_id life_financial.identity NOT NULL,
    exchange_order_id text NOT NULL CHECK (exchange_order_id ~ '^[0-9]{1,128}$'),
    observation jsonb NOT NULL CHECK (jsonb_typeof(observation)='object'),
    execution_loss life_financial.amount NOT NULL,
    source_operation_id life_financial.identity NOT NULL,
    PRIMARY KEY (account_id,trade_id),
    FOREIGN KEY (account_id,product,trade_id) REFERENCES life_financial.fills,
    FOREIGN KEY (account_id,intent_id) REFERENCES life_financial.send_attempts,
    FOREIGN KEY (account_id,source_operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX spot_fills_by_intent ON life_financial.spot_fill_facts(account_id,intent_id,trade_id);
CREATE TABLE life_financial.spot_pending_observations (
    account_id uuid NOT NULL, operation_id life_financial.identity NOT NULL,
    intent_id life_financial.identity NOT NULL,
    exchange_order_id text NOT NULL CHECK (exchange_order_id ~ '^[0-9]{1,128}$'),
    command jsonb NOT NULL CHECK (jsonb_typeof(command)='object'),
    reason life_financial.identity NOT NULL, source_hash life_financial.digest NOT NULL,
    PRIMARY KEY (account_id,operation_id),
    FOREIGN KEY (account_id,intent_id) REFERENCES life_financial.send_attempts,
    FOREIGN KEY (account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX pending_spot_by_intent ON life_financial.spot_pending_observations(account_id,intent_id);
CREATE TABLE life_financial.spot_accounting_progress (
    account_id uuid NOT NULL, intent_id life_financial.identity NOT NULL,
    filled_base life_financial.amount NOT NULL,
    paid_base life_financial.amount NOT NULL, paid_quote life_financial.amount NOT NULL,
    execution_loss life_financial.amount NOT NULL,
    observed_order_id text CHECK (observed_order_id ~ '^[0-9]{1,128}$'),
    version bigint NOT NULL CHECK (version>=0),
    PRIMARY KEY (account_id,intent_id), FOREIGN KEY (account_id,intent_id) REFERENCES life_financial.send_attempts
);
DO $$ DECLARE t text; BEGIN
    FOREACH t IN ARRAY ARRAY['spot_fill_policies','spot_fill_facts','spot_accounting_progress','spot_pending_observations'] LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY scoped_read ON life_financial.%I FOR SELECT USING (life_financial.can_access(account_id))',t);
    END LOOP;
    FOREACH t IN ARRAY ARRAY['spot_fill_policies','spot_fill_facts','spot_pending_observations'] LOOP
        EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',t);
    END LOOP;
END; $$;
CREATE TRIGGER monotonic_version BEFORE UPDATE ON life_financial.spot_accounting_progress
    FOR EACH ROW EXECUTE FUNCTION life_financial.version_record();
CREATE TRIGGER retained_identity BEFORE DELETE ON life_financial.spot_accounting_progress
    FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record();
CREATE FUNCTION life_financial.spot_progress_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
    IF NEW.filled_base<OLD.filled_base OR NEW.paid_base<OLD.paid_base OR NEW.paid_quote<OLD.paid_quote
        OR NEW.execution_loss<OLD.execution_loss OR OLD.observed_order_id IS NOT NULL AND NEW.observed_order_id IS DISTINCT FROM OLD.observed_order_id THEN
        RAISE EXCEPTION 'LIFE_SPOT_ACCOUNTING_REGRESSION' USING ERRCODE='P0001'; END IF;
    RETURN NEW;
END; $$;
CREATE TRIGGER progress_guard BEFORE UPDATE ON life_financial.spot_accounting_progress
    FOR EACH ROW EXECUTE FUNCTION life_financial.spot_progress_guard();

-- Deficit is an observed funding shortfall, never additional spendable cash.
-- A real fee overrun may make existing holds exceed actual funds. Record truth
-- and halt new risk rather than discarding the financial observation.
ALTER TABLE life_financial.balances ADD COLUMN funding_deficit life_financial.amount NOT NULL DEFAULT 0;
ALTER TABLE life_financial.balances DROP CONSTRAINT balances_check;
ALTER TABLE life_financial.balances ADD CONSTRAINT exact_funding_deficit
    CHECK (funding_deficit=greatest(0,reserved-total));

CREATE FUNCTION life_financial.guard_spot_observation(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts; s life_financial.sessions; actual jsonb;
BEGIN
    IF p_binding IS NULL OR p_owner IS NULL OR p_version IS NULL OR NOT life_financial.can_access(p_account,true)
        OR pg_is_in_recovery() OR current_setting('transaction_read_only')<>'off'
        OR current_setting('synchronous_commit')<>'on' OR current_setting('fsync')<>'on' OR current_setting('full_page_writes')<>'on' THEN
        RAISE EXCEPTION 'LIFE_OBSERVATION_CONTEXT_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    SELECT * INTO STRICT s FROM life_financial.sessions WHERE account_id=p_account AND session_id=a.current_session;
    actual := jsonb_build_object('environment',a.environment,'exchange',a.exchange,'account_uid',a.account_uid,
        'risk_pool',a.risk_pool,'lineage',a.lineage,'schema_version',a.schema_version,'policy_hash',a.policy_hash,'config_hash',a.config_hash);
    IF p_binding<>actual OR jsonb_typeof(p_owner)<>'object' OR (SELECT count(*) FROM jsonb_object_keys(p_owner))<>6
        OR a.owner_process IS DISTINCT FROM (p_owner->>'process_id')::uuid
        OR a.owner_generation IS DISTINCT FROM (p_owner->>'generation')::uuid
        OR a.owner_epoch IS DISTINCT FROM (p_owner->>'epoch')::bigint
        OR a.current_session IS DISTINCT FROM (p_owner->>'session_id')::uuid
        OR a.owner_since IS DISTINCT FROM (p_owner->>'valid_from')::timestamptz
        OR a.owner_until IS DISTINCT FROM (p_owner->>'expires_at')::timestamptz
        OR a.mode NOT IN ('ACTIVE','DRAINING','RECOVERY_ONLY') OR clock_timestamp()<a.owner_since
        OR s.epoch<>a.owner_epoch OR NOT EXISTS(SELECT 1 FROM life_financial.configurations
            WHERE account_id=p_account AND config_version=s.config_version AND config_hash=a.config_hash AND policy_hash=a.policy_hash) THEN
        RAISE EXCEPTION 'LIFE_OBSERVATION_OWNER_OR_BINDING_INVALID' USING ERRCODE='P0001'; END IF;
    IF a.version<>p_version THEN RAISE EXCEPTION 'LIFE_EXPECTED_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
END; $$;

-- Only immutable operations published by the whitelisted observation commands
-- may use the late-accounting branch. No caller-controlled GUC bypass exists.
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
    IF op.command->>'kind' IN ('SPOT_ACK','SPOT_FILL') THEN
        PERFORM life_financial.guard_spot_observation(NEW.account_id,op.command->'binding',op.command->'owner',a.version);
    ELSE
        SELECT expires_at INTO STRICT expiry FROM life_financial.sessions WHERE account_id=a.account_id AND session_id=a.current_session;
        at := clock_timestamp();
        IF a.mode<>'ACTIVE' OR a.halted OR at<a.owner_since OR at>=a.owner_until OR at>=expiry
            OR current_setting('synchronous_commit')<>'on' OR current_setting('fsync')<>'on'
            OR current_setting('full_page_writes')<>'on' OR pg_is_in_recovery() THEN
            RAISE EXCEPTION 'LIFE_OWNER_EXPIRED_OR_CHANGED_AT_COMMIT' USING ERRCODE='P0001'; END IF;
    END IF;
    RETURN NULL;
END; $$;

CREATE FUNCTION life_financial.spot_accounting_verified(p_account uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT NOT EXISTS (
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
    WHERE f.account_id=p_account AND (f.product<>'SPOT' OR x.trade_id IS NULL OR fee.payment_id IS NULL OR e.event_id IS NULL
        OR f.wire_id<>t.wire_id OR f.quantity<>(x.observation->>'quantity_base_life')::numeric
        OR f.price<>(x.observation->>'price_usdt')::numeric OR f.independent_price<>(x.observation->>'independent_price_usdt')::numeric
        OR fee.product<>'SPOT' OR fee.trade_id<>f.trade_id OR fee.asset<>x.observation->>'fee_currency'
        OR fee.signed_fee<>(x.observation->>'signed_fee')::numeric
        OR f.source_hash<>encode(sha256(convert_to(x.observation::text,'UTF8')),'hex') OR e.source_hash<>f.source_hash
        OR e.base_delta<>(CASE WHEN t.side='BUY' THEN f.quantity ELSE -f.quantity END+CASE WHEN fee.asset='LIFE' THEN fee.signed_fee ELSE 0 END)
        OR e.quote_delta<>(CASE WHEN t.side='BUY' THEN -f.quantity*f.price ELSE f.quantity*f.price END+CASE WHEN fee.asset='USDT' THEN fee.signed_fee ELSE 0 END))
) AND NOT EXISTS (
    SELECT 1 FROM life_financial.fees fee WHERE fee.account_id=p_account AND NOT EXISTS(
        SELECT 1 FROM life_financial.spot_fill_facts x WHERE x.account_id=p_account AND fee.product='SPOT'
            AND fee.trade_id=x.trade_id AND fee.payment_id='fee:'||encode(sha256(convert_to(x.trade_id,'UTF8')),'hex'))
);
$$;

CREATE OR REPLACE FUNCTION life_financial.spot_state(p_account uuid) RETURNS jsonb
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
            (e.product<>'SPOT' OR e.kind NOT IN ('ACK','FILL') OR e.kind='ACK' AND NOT EXISTS(
                SELECT 1 FROM life_financial.send_attempts t WHERE t.account_id=p_account AND t.product='SPOT' AND t.exchange_order_id=e.exchange_identity)
            OR e.kind='FILL' AND NOT EXISTS(SELECT 1 FROM life_financial.spot_fill_facts x WHERE x.account_id=p_account AND x.trade_id=e.exchange_identity)))
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
    snapshot_body := jsonb_build_object('account',to_jsonb(a),'session',to_jsonb(s),'policy',policy,'fill_policy',(SELECT to_jsonb(fp) FROM life_financial.spot_fill_policies fp WHERE fp.account_id=p_account AND fp.policy_hash=a.policy_hash),'balances',b,'budgets',budgets,
        'day',today,'recovery_required',unknown OR unverified
            OR EXISTS(SELECT 1 FROM life_financial.spot_pending_observations WHERE account_id=p_account)
            OR EXISTS(SELECT 1 FROM life_financial.balances WHERE account_id=p_account AND funding_deficit>0)
            OR EXISTS(SELECT 1 FROM life_financial.spot_preparations d JOIN life_financial.spot_accounting_progress g USING(account_id,intent_id)
                WHERE d.account_id=p_account AND (g.paid_base>d.fee_base OR g.paid_quote>d.fee_quote)),'unverified',unverified);
    RETURN snapshot_body||jsonb_build_object('digest',encode(sha256(convert_to(snapshot_body::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
END; $$;


CREATE FUNCTION life_financial.spot_charge_loss(p_account uuid,p_session uuid,p_at timestamptz,p_trade text,p_loss numeric)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.spot_policies; scope_name text; key text; limit_value numeric;
BEGIN
    SELECT p0.* INTO STRICT p FROM life_financial.spot_policies p0 JOIN life_financial.accounts a USING(account_id,policy_hash) WHERE a.account_id=p_account;
    FOREACH scope_name IN ARRAY ARRAY['CAMPAIGN','DAY','SESSION'] LOOP
        key := CASE scope_name WHEN 'CAMPAIGN' THEN p.campaign_id WHEN 'DAY' THEN to_char(p_at AT TIME ZONE 'UTC','YYYY-MM-DD') ELSE p_session::text END;
        limit_value := CASE scope_name WHEN 'CAMPAIGN' THEN p.loss_campaign WHEN 'DAY' THEN p.loss_day ELSE p.loss_session END;
        INSERT INTO life_financial.budget_buckets VALUES(p_account,'LOSS',scope_name,key,limit_value,0,0,0) ON CONFLICT DO NOTHING;
        IF NOT EXISTS(SELECT 1 FROM life_financial.budget_buckets WHERE account_id=p_account AND family='LOSS' AND scope=scope_name AND window_key=key AND budget_limit=limit_value) THEN
            RAISE EXCEPTION 'LIFE_LOSS_POLICY_CONFLICT' USING ERRCODE='P0001'; END IF;
        UPDATE life_financial.budget_buckets SET incurred=incurred+p_loss,version=version+1 WHERE account_id=p_account AND family='LOSS' AND scope=scope_name AND window_key=key;
        INSERT INTO life_financial.budget_events VALUES(p_account,'loss:'||encode(sha256(convert_to(p_trade,'UTF8')),'hex')||':'||scope_name,'LOSS',scope_name,key,'SPEND',p_loss);
    END LOOP;
END; $$;

CREATE FUNCTION life_financial.spot_observe(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,p_kind text,p_command jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
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
        IF NOT life_financial.spot_accounting_verified(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNTING_PROJECTION_UNVERIFIED' USING ERRCODE='P0001'; END IF;
        RETURN previous.receipt||'{"replayed":true}'::jsonb;
    END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    view := life_financial.spot_state(p_account);
    IF view->>'digest'<>p_cut THEN RAISE EXCEPTION 'LIFE_SPOT_CUT_CONFLICT' USING ERRCODE='P0001'; END IF;
    IF (view->>'unverified')::boolean THEN RAISE EXCEPTION 'LIFE_ACCOUNTING_PROJECTION_UNVERIFIED' USING ERRCODE='P0001'; END IF;
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
        ELSIF EXISTS(SELECT 1 FROM life_financial.balances b WHERE b.account_id=p_account AND b.total+
            CASE b.asset WHEN 'LIFE' THEN delta_base ELSE delta_quote END<0) THEN
            pending_reason := 'NEGATIVE_WALLET_UNSUPPORTED';
        END IF;
        IF pending_reason IS NOT NULL THEN RAISE EXCEPTION 'LIFE_OBSERVATION_PENDING' USING ERRCODE='PZ001'; END IF;
        UPDATE life_financial.spot_accounting_progress SET filled_base=cumulative,paid_base=new_paid_base,paid_quote=new_paid_quote,
            execution_loss=new_loss,observed_order_id=p_command->>'exchange_order_id',version=version+1 WHERE account_id=p_account AND intent_id=t.intent_id;
        UPDATE life_financial.reservations SET remaining=CASE
            WHEN reservation_id=d.operation_id||':principal' THEN (t.quantity-cumulative)*CASE WHEN t.side='BUY' THEN t.price ELSE 1 END
            WHEN asset='LIFE' THEN greatest(0,d.fee_base-new_paid_base) ELSE greatest(0,d.fee_quote-new_paid_quote) END,version=version+1
            WHERE account_id=p_account AND intent_id=t.intent_id AND state='HELD';
        UPDATE life_financial.balances b SET total=b.total+CASE b.asset WHEN 'LIFE' THEN delta_base ELSE delta_quote END,
            reserved=(SELECT COALESCE(sum(remaining),0) FROM life_financial.reservations r WHERE r.account_id=p_account AND r.asset=b.asset AND r.state<>'TERMINAL'),
            funding_deficit=greatest(0,(SELECT COALESCE(sum(remaining),0) FROM life_financial.reservations r WHERE r.account_id=p_account AND r.asset=b.asset AND r.state<>'TERMINAL')
                -(b.total+CASE b.asset WHEN 'LIFE' THEN delta_base ELSE delta_quote END)),version=b.version+1 WHERE b.account_id=p_account;
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
REVOKE ALL ON life_financial.spot_fill_policies,life_financial.spot_fill_facts,life_financial.spot_accounting_progress,life_financial.spot_pending_observations FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;

CREATE OR REPLACE FUNCTION life_financial.spot_prepare(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
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
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version)
        VALUES(p_account,op,p_fingerprint,request,result,p_version+1);
    RETURN result;
END; $$;


REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
