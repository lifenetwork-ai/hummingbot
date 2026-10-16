-- Standalone SPOT storage lifecycle. Caller facts are not provider authentication.
-- Indexed prefixes for budget-window verification and exact-intent release.
CREATE INDEX spot_budget_event_windows ON life_financial.budget_events(account_id,family,scope,window_key);
CREATE INDEX spot_reservations_by_intent ON life_financial.reservations(account_id,intent_id);

CREATE TABLE life_financial.spot_order_policies (
    account_id uuid PRIMARY KEY REFERENCES life_financial.accounts,
    policy_hash life_financial.digest NOT NULL,
    max_fact_age_ms integer NOT NULL CHECK (max_fact_age_ms>0),
    cancel_retry_interval_ms integer NOT NULL CHECK (cancel_retry_interval_ms>0),
    FOREIGN KEY(account_id,policy_hash) REFERENCES life_financial.policies
);
CREATE TABLE life_financial.spot_cancel_progress (
    account_id uuid NOT NULL, intent_id life_financial.identity NOT NULL,
    cancel_attempts bigint NOT NULL CHECK (cancel_attempts>=0),
    last_cancel_attempt_at timestamptz CHECK (isfinite(last_cancel_attempt_at)),
    last_recorded_at timestamptz CHECK (isfinite(last_recorded_at)),
    version bigint NOT NULL CHECK (version>=0),
    PRIMARY KEY(account_id,intent_id),
    FOREIGN KEY(account_id,intent_id) REFERENCES life_financial.send_attempts,
    CHECK (cancel_attempts=0 AND last_cancel_attempt_at IS NULL AND last_recorded_at IS NULL
        OR cancel_attempts>0 AND last_cancel_attempt_at IS NOT NULL AND last_recorded_at IS NOT NULL)
);
CREATE TABLE life_financial.spot_lifecycle_events (
    account_id uuid NOT NULL, operation_id life_financial.identity NOT NULL,
    intent_id life_financial.identity NOT NULL, command jsonb NOT NULL,
    result_state text NOT NULL CHECK (result_state IN ('ABORTED_BEFORE_SEND','SEND_UNKNOWN','ACKED')),
    after_cancel jsonb, sequence bigint GENERATED ALWAYS AS IDENTITY,
    PRIMARY KEY(account_id,operation_id),
    FOREIGN KEY(account_id,intent_id) REFERENCES life_financial.send_attempts,
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED
);
CREATE UNIQUE INDEX spot_abort_once ON life_financial.spot_lifecycle_events(account_id,intent_id)
    WHERE result_state='ABORTED_BEFORE_SEND';
CREATE INDEX spot_cancel_latest ON life_financial.spot_lifecycle_events(account_id,intent_id,sequence DESC)
    WHERE after_cancel IS NOT NULL;
CREATE TABLE life_financial.spot_terminal_facts (
    account_id uuid NOT NULL, intent_id life_financial.identity NOT NULL,
    operation_id life_financial.identity NOT NULL, command jsonb NOT NULL,
    subsidy_settled boolean NOT NULL,
    PRIMARY KEY(account_id,intent_id), UNIQUE(account_id,operation_id),
    FOREIGN KEY(account_id,intent_id) REFERENCES life_financial.send_attempts,
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED
);
DO $$ DECLARE t text; BEGIN
    FOREACH t IN ARRAY ARRAY['spot_order_policies','spot_cancel_progress','spot_lifecycle_events','spot_terminal_facts'] LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY scoped_read ON life_financial.%I FOR SELECT USING (life_financial.can_access(account_id))',t);
        EXECUTE format('CREATE TRIGGER retained_identity BEFORE DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',t);
    END LOOP;
    FOREACH t IN ARRAY ARRAY['spot_order_policies','spot_lifecycle_events','spot_terminal_facts'] LOOP
        EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',t);
    END LOOP;
END; $$;
CREATE TRIGGER monotonic_version BEFORE UPDATE ON life_financial.spot_cancel_progress
    FOR EACH ROW EXECUTE FUNCTION life_financial.version_record();
CREATE FUNCTION life_financial.spot_cancel_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
    IF NEW.cancel_attempts<OLD.cancel_attempts OR NEW.cancel_attempts>OLD.cancel_attempts+1
        OR OLD.last_cancel_attempt_at IS NOT NULL AND (NEW.last_cancel_attempt_at IS NULL OR NEW.last_cancel_attempt_at<OLD.last_cancel_attempt_at)
        OR OLD.last_recorded_at IS NOT NULL AND (NEW.last_recorded_at IS NULL OR NEW.last_recorded_at<OLD.last_recorded_at) THEN
        RAISE EXCEPTION 'LIFE_CANCEL_PROGRESS_REGRESSION' USING ERRCODE='P0001'; END IF;
    RETURN NEW;
END; $$;
CREATE TRIGGER cancel_progress_guard BEFORE UPDATE ON life_financial.spot_cancel_progress
    FOR EACH ROW EXECUTE FUNCTION life_financial.spot_cancel_guard();

CREATE FUNCTION life_financial.spot_lifecycle_verified(p_account uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT NOT EXISTS(
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
        GROUP BY w.scope,w.window_key
    ) expected LEFT JOIN life_financial.budget_buckets b ON b.account_id=p_account AND b.family='SUBSIDY'
        AND b.scope=expected.scope AND b.window_key=expected.window_key
    WHERE b.account_id IS NULL OR b.committed<expected.held);
$$;

-- Add lifecycle proof to the existing snapshot without relaxing its accounting,
-- session, pending-fact, deficit or unsupported-product barriers.
CREATE OR REPLACE FUNCTION life_financial.spot_state(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE body jsonb; current_id uuid; verified boolean;
BEGIN
    body:=life_financial.spot_state_v7(p_account)-'digest'-'observed_at';
    current_id:=(body#>>'{account,current_session}')::uuid;
    verified:=life_financial.spot_session_verified(p_account) AND life_financial.spot_lifecycle_verified(p_account);
    body:=body||jsonb_build_object(
        'session_lifecycle',(SELECT to_jsonb(g) FROM life_financial.spot_session_progress g WHERE g.account_id=p_account AND g.session_id=current_id),
        'session_policy',(SELECT to_jsonb(p) FROM life_financial.spot_session_policies p WHERE p.account_id=p_account AND p.session_id=current_id),
        'order_policy',(SELECT to_jsonb(p) FROM life_financial.spot_order_policies p WHERE p.account_id=p_account),
        'cancel_progress',(SELECT COALESCE(jsonb_agg(to_jsonb(g) ORDER BY intent_id),'[]'::jsonb) FROM life_financial.spot_cancel_progress g WHERE g.account_id=p_account),
        'recovery_required',(body->>'recovery_required')::boolean OR NOT verified,
        'unverified',(body->>'unverified')::boolean OR NOT verified);
    RETURN body||jsonb_build_object('digest',encode(sha256(convert_to(body::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
END; $$;

CREATE FUNCTION life_financial.spot_order_core(p_command jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
    SELECT (p_command-'facts')||jsonb_build_object('facts',jsonb_build_object(
        'exchange_state',p_command#>'{facts,exchange_state}','cumulative_base_life',p_command#>'{facts,cumulative_base_life}',
        'quantity_base_life',p_command#>'{facts,quantity_base_life}','limit_price_usdt',p_command#>'{facts,limit_price_usdt}',
        'fills',p_command#>'{facts,fills}'));
$$;
CREATE FUNCTION life_financial.spot_order_commit_guard(p_account uuid,p_request jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.spot_order_policies; observed timestamptz; at timestamptz;
BEGIN
    SELECT p0.* INTO STRICT p FROM life_financial.spot_order_policies p0 JOIN life_financial.accounts a USING(account_id,policy_hash) WHERE a.account_id=p_account;
    IF NOT life_financial.spot_lifecycle_verified(p_account) THEN RAISE EXCEPTION 'LIFE_ORDER_PROJECTION_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    IF p_request->>'kind'='SPOT_TERMINAL' THEN observed:=(p_request#>>'{command,facts,observed_at}')::timestamptz;
    ELSIF p_request#>>'{command,action}'='RECORD_CANCEL_ATTEMPT' THEN observed:=(p_request#>>'{command,attempted_at}')::timestamptz;
    ELSE RETURN; END IF;
    at:=clock_timestamp();
    IF NOT isfinite(observed) OR observed>at OR extract(epoch FROM at-observed)*1000>p.max_fact_age_ms THEN
        RAISE EXCEPTION 'LIFE_ORDER_FACTS_STALE_AT_COMMIT' USING ERRCODE='P0001'; END IF;
    IF p_request->>'kind'='SPOT_LIFECYCLE' AND EXISTS(SELECT 1 FROM life_financial.spot_cancel_progress
        WHERE account_id=p_account AND intent_id=p_request#>>'{command,intent_id}' AND last_recorded_at>at) THEN
        RAISE EXCEPTION 'LIFE_CANCEL_CLOCK_ROLLBACK' USING ERRCODE='P0001'; END IF;
END; $$;

CREATE FUNCTION life_financial.spot_order_command(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,p_kind text,p_command jsonb,p_resume boolean) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts; d life_financial.spot_preparations; t life_financial.send_attempts;
    policy life_financial.spot_order_policies; g life_financial.spot_cancel_progress; p life_financial.spot_policies;
    previous life_financial.operations; view jsonb; request jsonb; result jsonb; facts jsonb; expected jsonb; after_cancel jsonb;
    op life_financial.identity; action text; v_state text; observed timestamptz; at timestamptz; cumulative numeric;
    scope_name text; bucket_key text; item text; release_subsidy boolean:=false;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR p_kind IS NULL OR p_kind NOT IN ('SPOT_LIFECYCLE','SPOT_TERMINAL')
        OR p_resume IS NULL OR p_kind='SPOT_LIFECYCLE' AND p_resume OR p_cut IS NULL OR p_fingerprint IS NULL
        OR p_command IS NULL OR jsonb_typeof(p_command)<>'object' OR octet_length(p_command::text)>1048576 THEN
        RAISE EXCEPTION 'LIFE_ORDER_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    IF p_kind='SPOT_LIFECYCLE' THEN
        IF (SELECT count(*) FROM jsonb_object_keys(p_command))<>8 OR NOT p_command ?& ARRAY[
            'operation_id','prepare_operation_id','intent_id','client_order_id','action','attempted_at','expected_cancel_attempts','retry_interval_ms']
            OR EXISTS(SELECT 1 FROM jsonb_each(p_command) WHERE key NOT IN ('attempted_at','expected_cancel_attempts','retry_interval_ms') AND value='null'::jsonb)
            OR p_command->>'action' NOT IN ('ABORT_PREPARED','REQUEST_CANCEL','RECORD_CANCEL_ATTEMPT') THEN
            RAISE EXCEPTION 'LIFE_ORDER_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
        action:=p_command->>'action';
        IF action='RECORD_CANCEL_ATTEMPT' AND EXISTS(SELECT 1 FROM jsonb_each(p_command) WHERE value='null'::jsonb)
            OR action<>'RECORD_CANCEL_ATTEMPT' AND (p_command->'attempted_at'<>'null'::jsonb
                OR p_command->'expected_cancel_attempts'<>'null'::jsonb OR p_command->'retry_interval_ms'<>'null'::jsonb) THEN
            RAISE EXCEPTION 'LIFE_ORDER_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    ELSE
        IF (SELECT count(*) FROM jsonb_object_keys(p_command))<>10 OR NOT p_command ?& ARRAY[
            'operation_id','prepare_operation_id','intent_id','client_order_id','market','side','session_id','epoch','exchange_order_id','facts']
            OR EXISTS(SELECT 1 FROM jsonb_each(p_command) WHERE value='null'::jsonb)
            OR p_command->>'exchange_order_id' !~ '^[0-9]{1,128}$' THEN
            RAISE EXCEPTION 'LIFE_ORDER_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    END IF;
    op:=p_command->>'operation_id';
    IF length(op)>96 THEN RAISE EXCEPTION 'LIFE_ORDER_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    request:=jsonb_build_object('kind',p_kind,'binding',p_binding,'owner',p_owner,'command',p_command);
    SELECT * INTO previous FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF p_resume THEN
            IF previous.command->>'kind'<>'SPOT_TERMINAL' OR previous.command->'binding' IS DISTINCT FROM p_binding
                OR previous.command->'owner' IS DISTINCT FROM p_owner
                OR life_financial.spot_order_core(previous.command->'command')<>life_financial.spot_order_core(p_command) THEN
                RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001'; END IF;
        ELSIF previous.command<>request OR previous.fingerprint<>p_fingerprint THEN
            RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001'; END IF;
        RETURN previous.receipt||'{"replayed":true}'::jsonb;
    ELSIF p_resume THEN RAISE EXCEPTION 'LIFE_TERMINAL_RECOVERY_NOT_STARTED' USING ERRCODE='P0001'; END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    view:=life_financial.spot_state(p_account);
    IF view->>'digest'<>p_cut THEN RAISE EXCEPTION 'LIFE_SPOT_CUT_CONFLICT' USING ERRCODE='P0001'; END IF;
    IF (view->>'unverified')::boolean THEN RAISE EXCEPTION 'LIFE_ORDER_PROJECTION_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO policy FROM life_financial.spot_order_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
    IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_ORDER_POLICY_REQUIRED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT d FROM life_financial.spot_preparations WHERE account_id=p_account AND operation_id=p_command->>'prepare_operation_id';
    SELECT * INTO STRICT t FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=d.intent_id;
    IF t.intent_id<>p_command->>'intent_id' OR t.wire_id<>p_command->>'client_order_id' OR t.product<>'SPOT'
        OR t.process_id<>a.owner_process OR t.generation<>a.owner_generation OR t.epoch<>a.owner_epoch OR t.session_id<>a.current_session
        OR (SELECT command->'owner' FROM life_financial.operations WHERE account_id=p_account AND operation_id=d.operation_id)<>p_owner THEN
        RAISE EXCEPTION 'LIFE_ORDER_ORIGINAL_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
    at:=clock_timestamp();
    IF p_kind='SPOT_LIFECYCLE' THEN
        IF action='ABORT_PREPARED' THEN
            IF t.state<>'PREPARED' OR t.exchange_order_id IS NOT NULL
                OR EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=p_account AND operation_id='arm:'||d.operation_id)
                OR EXISTS(SELECT 1 FROM life_financial.spot_accounting_progress WHERE account_id=p_account AND intent_id=t.intent_id
                    AND (filled_base<>0 OR observed_order_id IS NOT NULL OR paid_base<>0 OR paid_quote<>0))
                OR (view->>'recovery_required')::boolean THEN
                RAISE EXCEPTION 'LIFE_ABORT_NOT_PROVEN_UNSENT' USING ERRCODE='P0001'; END IF;
            v_state:='ABORTED_BEFORE_SEND'; release_subsidy:=true;
            UPDATE life_financial.send_attempts SET state=v_state,version=version+1 WHERE account_id=p_account AND intent_id=t.intent_id;
        ELSE
            IF t.state NOT IN ('SEND_UNKNOWN','ACKED') THEN RAISE EXCEPTION 'LIFE_CANCEL_ATTEMPT_INVALID' USING ERRCODE='P0001'; END IF;
            v_state:=t.state;
            SELECT * INTO g FROM life_financial.spot_cancel_progress WHERE account_id=p_account AND intent_id=t.intent_id;
            IF NOT FOUND THEN
                INSERT INTO life_financial.spot_cancel_progress VALUES(p_account,t.intent_id,0,NULL,NULL,0);
                SELECT * INTO STRICT g FROM life_financial.spot_cancel_progress WHERE account_id=p_account AND intent_id=t.intent_id;
            ELSE g.version:=g.version+1; END IF;
            IF action='RECORD_CANCEL_ATTEMPT' THEN
                observed:=(p_command->>'attempted_at')::timestamptz;
                IF NOT isfinite(observed) OR observed<d.prepared_at OR observed>at OR extract(epoch FROM at-observed)*1000>policy.max_fact_age_ms
                    OR (p_command->>'expected_cancel_attempts')::bigint<>g.cancel_attempts
                    OR (p_command->>'retry_interval_ms')::integer<>policy.cancel_retry_interval_ms THEN
                    RAISE EXCEPTION 'LIFE_CANCEL_ATTEMPT_STALE_OR_POLICY_INVALID' USING ERRCODE='P0001'; END IF;
                IF g.last_cancel_attempt_at IS NOT NULL AND (observed<g.last_cancel_attempt_at+policy.cancel_retry_interval_ms*interval '1 millisecond'
                    OR at<g.last_recorded_at+policy.cancel_retry_interval_ms*interval '1 millisecond') THEN
                    RAISE EXCEPTION 'LIFE_CANCEL_RETRY_NOT_DUE' USING ERRCODE='P0001'; END IF;
                g.cancel_attempts:=g.cancel_attempts+1; g.last_cancel_attempt_at:=observed; g.last_recorded_at:=at;
            END IF;
            -- A newly inserted row also advances once before event publication.
            IF g.version=0 THEN g.version:=1; END IF;
            UPDATE life_financial.spot_cancel_progress SET cancel_attempts=g.cancel_attempts,last_cancel_attempt_at=g.last_cancel_attempt_at,
                last_recorded_at=g.last_recorded_at,version=g.version WHERE account_id=p_account AND intent_id=t.intent_id;
            after_cancel:=to_jsonb(g);
        END IF;
        INSERT INTO life_financial.spot_lifecycle_events(account_id,operation_id,intent_id,command,result_state,after_cancel)
            VALUES(p_account,op,t.intent_id,p_command,v_state,after_cancel);
        result:=jsonb_build_object('kind',p_kind,'operation_id',op,'intent_id',t.intent_id,'state',v_state);
    ELSE
        facts:=p_command->'facts';
        IF NOT EXISTS(SELECT 1 FROM life_financial.spot_capital_policies WHERE account_id=p_account AND policy_hash=a.policy_hash) THEN
            RAISE EXCEPTION 'LIFE_TERMINAL_CAPITAL_POLICY_REQUIRED' USING ERRCODE='P0001'; END IF;
        IF jsonb_typeof(facts)<>'object' OR (SELECT count(*) FROM jsonb_object_keys(facts))<>19 OR NOT facts ?& ARRAY[
            'binding','observed_at','exchange_state','cumulative_base_life','quantity_base_life','limit_price_usdt','fills',
            'account_base_life','account_quote_usdt','liability_base_life','liability_quote_usdt','account_trade_ids','account_cashflow_ids',
            'pending_wire_ids','pending_exchange_order_ids','runner_wire_ids','order_scope_complete','bill_scope_complete','runner_scope_complete']
            OR EXISTS(SELECT 1 FROM jsonb_each(facts) WHERE value='null'::jsonb) THEN
            RAISE EXCEPTION 'LIFE_TERMINAL_FACTS_INVALID' USING ERRCODE='P0001'; END IF;
        observed:=(facts->>'observed_at')::timestamptz;
        cumulative:=(facts->>'cumulative_base_life')::life_financial.amount;
        IF t.state NOT IN ('SEND_UNKNOWN','ACKED') OR t.side<>p_command->>'side' OR p_command->>'market'<>'LIFE-USDT'
            OR t.session_id<>(p_command->>'session_id')::uuid OR t.epoch<>(p_command->>'epoch')::bigint
            OR facts->'binding'<>p_binding OR facts->'order_scope_complete'<>'true'::jsonb OR facts->'bill_scope_complete'<>'true'::jsonb
            OR facts->'runner_scope_complete'<>'true'::jsonb OR facts->>'exchange_state' NOT IN ('CANCELED','FILLED')
            OR facts->>'exchange_state'='FILLED' AND cumulative<>t.quantity OR cumulative>t.quantity
            OR (facts->>'quantity_base_life')::life_financial.positive_amount<>t.quantity
            OR (facts->>'limit_price_usdt')::life_financial.positive_amount<>t.price
            OR NOT isfinite(observed) OR observed<d.prepared_at OR observed>at OR extract(epoch FROM at-observed)*1000>policy.max_fact_age_ms
            OR (facts->>'account_base_life')::life_financial.amount<>(view#>>'{balances,LIFE,total}')::numeric
            OR (facts->>'account_quote_usdt')::life_financial.amount<>(view#>>'{balances,USDT,total}')::numeric
            OR (facts->>'liability_base_life')::life_financial.amount<>0 OR (facts->>'liability_quote_usdt')::life_financial.amount<>0
            OR EXISTS(SELECT 1 FROM life_financial.balances WHERE account_id=p_account AND liability<>0)
            OR EXISTS(SELECT 1 FROM life_financial.spot_pending_observations WHERE account_id=p_account)
            OR EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts WHERE account_id=p_account AND state<>'APPLIED')
            OR t.exchange_order_id IS NOT NULL AND t.exchange_order_id<>p_command->>'exchange_order_id'
            OR EXISTS(SELECT 1 FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id<>t.intent_id AND state='SEND_UNKNOWN') THEN
            RAISE EXCEPTION 'LIFE_TERMINAL_SCOPE_OR_ACCOUNT_INVALID' USING ERRCODE='P0001'; END IF;
        IF cumulative<>COALESCE((SELECT filled_base FROM life_financial.spot_accounting_progress WHERE account_id=p_account AND intent_id=t.intent_id),0)
            OR EXISTS(SELECT 1 FROM life_financial.spot_accounting_progress WHERE account_id=p_account AND intent_id=t.intent_id
                AND observed_order_id IS NOT NULL AND observed_order_id<>p_command->>'exchange_order_id') THEN
            RAISE EXCEPTION 'LIFE_TERMINAL_FILLS_INCOMPLETE' USING ERRCODE='P0001'; END IF;
        SELECT COALESCE(jsonb_agg(observation ORDER BY (observation->>'fill_at_ms')::bigint,trade_id),'[]'::jsonb) INTO expected
            FROM life_financial.spot_fill_facts WHERE account_id=p_account AND intent_id=t.intent_id AND exchange_order_id=p_command->>'exchange_order_id';
        IF facts->'fills'<>expected THEN RAISE EXCEPTION 'LIFE_TERMINAL_FILLS_INCOMPLETE' USING ERRCODE='P0001'; END IF;
        SELECT COALESCE(jsonb_agg(trade_id ORDER BY trade_id),'[]'::jsonb) INTO expected FROM life_financial.fills WHERE account_id=p_account;
        IF facts->'account_trade_ids'<>expected THEN RAISE EXCEPTION 'LIFE_TERMINAL_ACCOUNT_MISMATCH' USING ERRCODE='P0001'; END IF;
        SELECT COALESCE(jsonb_agg(bill_id ORDER BY bill_id),'[]'::jsonb) INTO expected FROM life_financial.cashflows WHERE account_id=p_account;
        IF facts->'account_cashflow_ids'<>expected THEN RAISE EXCEPTION 'LIFE_TERMINAL_ACCOUNT_MISMATCH' USING ERRCODE='P0001'; END IF;
        FOREACH item IN ARRAY ARRAY['pending_wire_ids','pending_exchange_order_ids','runner_wire_ids'] LOOP
            IF jsonb_typeof(facts->item)<>'array' OR (SELECT count(*)<>count(DISTINCT value) FROM jsonb_array_elements_text(facts->item)) THEN
                RAISE EXCEPTION 'LIFE_TERMINAL_ORDER_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
        END LOOP;
        IF EXISTS(SELECT 1 FROM jsonb_array_elements_text(facts->'pending_wire_ids') x WHERE NOT EXISTS(
            SELECT 1 FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id<>t.intent_id AND wire_id=x.value AND state IN ('PREPARED','SEND_UNKNOWN','ACKED')))
            OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(facts->'pending_exchange_order_ids') x WHERE NOT EXISTS(
                SELECT 1 FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id<>t.intent_id AND exchange_order_id=x.value AND state IN ('SEND_UNKNOWN','ACKED')))
            OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(facts->'runner_wire_ids') x WHERE NOT EXISTS(
                SELECT 1 FROM life_financial.send_attempts WHERE account_id=p_account AND wire_id=x.value)) THEN
            RAISE EXCEPTION 'LIFE_TERMINAL_ORDER_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
        release_subsidy:=cumulative=0; v_state:='TERMINAL';
        UPDATE life_financial.send_attempts SET state='TERMINAL',exchange_order_id=p_command->>'exchange_order_id',version=version+1
            WHERE account_id=p_account AND intent_id=t.intent_id;
        INSERT INTO life_financial.spot_terminal_facts VALUES(p_account,t.intent_id,op,p_command,release_subsidy);
        result:=jsonb_build_object('kind',p_kind,'operation_id',op,'intent_id',t.intent_id,'exchange_order_id',p_command->>'exchange_order_id',
            'state',v_state,'subsidy_settled',release_subsidy);
    END IF;
    IF action='ABORT_PREPARED' OR p_kind='SPOT_TERMINAL' THEN
        UPDATE life_financial.reservations SET remaining=0,state='TERMINAL',version=version+1
            WHERE account_id=p_account AND intent_id=t.intent_id AND state<>'TERMINAL';
        UPDATE life_financial.balances b SET reserved=(SELECT COALESCE(sum(remaining),0) FROM life_financial.reservations r
            WHERE r.account_id=p_account AND r.asset=b.asset AND r.state<>'TERMINAL'),
            funding_deficit=greatest(0,(SELECT COALESCE(sum(remaining),0) FROM life_financial.reservations r
                WHERE r.account_id=p_account AND r.asset=b.asset AND r.state<>'TERMINAL')-b.total),version=b.version+1 WHERE b.account_id=p_account;
        IF release_subsidy THEN
            SELECT * INTO STRICT p FROM life_financial.spot_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
            FOREACH scope_name IN ARRAY ARRAY['CAMPAIGN','DAY','SESSION'] LOOP
                bucket_key:=CASE scope_name WHEN 'CAMPAIGN' THEN p.campaign_id WHEN 'DAY' THEN to_char(d.prepared_at AT TIME ZONE 'UTC','YYYY-MM-DD') ELSE t.session_id::text END;
                UPDATE life_financial.budget_buckets SET committed=committed-d.subsidy,version=version+1
                    WHERE account_id=p_account AND family='SUBSIDY' AND scope=scope_name AND window_key=bucket_key;
                INSERT INTO life_financial.budget_events VALUES(p_account,op||':release:'||scope_name,'SUBSIDY',scope_name,bucket_key,'RELEASE',d.subsidy);
            END LOOP;
        END IF;
    END IF;
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    result:=result||jsonb_build_object('version',p_version+1,'replayed',false,'trading_permission',false);
    INSERT INTO life_financial.operations VALUES(p_account,op,p_fingerprint,request,result,p_version+1,clock_timestamp());
    RETURN result;
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
    IF op.command->>'kind' IN ('SPOT_ACK','SPOT_FILL','SPOT_CASHFLOW','SPOT_NAV','SPOT_SESSION_TICK','SPOT_LIFECYCLE','SPOT_TERMINAL') THEN
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
    RETURN NULL;
END; $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
