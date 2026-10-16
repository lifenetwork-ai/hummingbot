-- Publication journal only. Pending actions still deny preparation/accounting;
-- the later atomic action-to-prepare bridge must qualify that integration.
CREATE TABLE life_financial.spot_action_policies (
    account_id uuid PRIMARY KEY REFERENCES life_financial.accounts,
    policy_hash life_financial.digest NOT NULL, controller_id life_financial.identity NOT NULL,
    max_batch integer NOT NULL CHECK (max_batch BETWEEN 1 AND 64),
    max_payload_bytes integer NOT NULL CHECK (max_payload_bytes BETWEEN 1 AND 65536),
    max_fact_age_ms integer NOT NULL CHECK (max_fact_age_ms>0),
    FOREIGN KEY(account_id,policy_hash) REFERENCES life_financial.policies
);
CREATE TABLE life_financial.spot_action_descriptors (
    account_id uuid NOT NULL, action_id life_financial.identity NOT NULL,
    descriptor jsonb NOT NULL CHECK (jsonb_typeof(descriptor)='object'),
    PRIMARY KEY(account_id,action_id), FOREIGN KEY(account_id,action_id) REFERENCES life_financial.quote_actions
);
CREATE TABLE life_financial.spot_action_events (
    account_id uuid NOT NULL, action_id life_financial.identity NOT NULL,
    action_version bigint NOT NULL CHECK (action_version>=0), operation_id life_financial.identity NOT NULL,
    command jsonb NOT NULL, descriptor jsonb NOT NULL, after_state jsonb NOT NULL,
    PRIMARY KEY(account_id,action_id,action_version),
    FOREIGN KEY(account_id,action_id) REFERENCES life_financial.spot_action_descriptors,
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED
);
CREATE UNIQUE INDEX spot_action_active_slot ON life_financial.quote_actions(account_id,side,level)
    WHERE state NOT IN ('REJECTED','RECONCILED');
DO $$ DECLARE t text; BEGIN
    FOREACH t IN ARRAY ARRAY['spot_action_policies','spot_action_descriptors','spot_action_events'] LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY scoped_read ON life_financial.%I FOR SELECT USING (life_financial.can_access(account_id))',t);
        EXECUTE format('CREATE TRIGGER retained_identity BEFORE DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',t);
        EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',t);
    END LOOP;
END; $$;

CREATE FUNCTION life_financial.spot_actions_verified(p_account uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT NOT EXISTS(
    SELECT 1 FROM life_financial.quote_actions q WHERE q.account_id=p_account AND NOT EXISTS(
        SELECT 1 FROM life_financial.spot_action_descriptors d JOIN life_financial.spot_action_events e USING(account_id,action_id)
        JOIN life_financial.operations o USING(account_id,operation_id)
        WHERE d.account_id=q.account_id AND d.action_id=q.action_id AND e.action_version=q.version
            AND e.after_state=to_jsonb(q) AND e.descriptor=d.descriptor AND e.command=o.command->'command'
            AND o.command->>'kind' IN ('SPOT_ACTION_CLAIM','SPOT_ACTION_TRANSITION')
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
            WHEN 'SPOT_ACTION_TRANSITION' THEN e.command->'action' END
        OR NOT o.receipt->'states' @> jsonb_build_array(jsonb_build_array(e.action_id,e.after_state->>'state'))))
AND NOT EXISTS(SELECT 1 FROM life_financial.quote_actions q WHERE q.account_id=p_account AND q.state='REJECTED'
    AND (EXISTS(SELECT 1 FROM life_financial.send_attempts t WHERE t.account_id=q.account_id AND t.intent_id=q.action_id)
        OR EXISTS(SELECT 1 FROM life_financial.reservations r WHERE r.account_id=q.account_id AND r.intent_id=q.action_id)));
$$;

-- Keep every existing financial barrier, including pending-action denial. Also
-- verify closed actions: a fabricated REJECTED tombstone cannot enable risk.
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
        'action_policy',(SELECT to_jsonb(p) FROM life_financial.spot_action_policies p WHERE p.account_id=p_account),
        'quote_actions',(SELECT COALESCE(jsonb_agg(jsonb_build_object('row',to_jsonb(q),'descriptor',d.descriptor) ORDER BY q.action_id),'[]'::jsonb)
            FROM life_financial.quote_actions q LEFT JOIN life_financial.spot_action_descriptors d USING(account_id,action_id) WHERE q.account_id=p_account),
        'recovery_required',(body->>'recovery_required')::boolean OR NOT verified,
        'unverified',(body->>'unverified')::boolean OR NOT verified);
    RETURN body||jsonb_build_object('digest',encode(sha256(convert_to(body::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
END; $$;

CREATE FUNCTION life_financial.spot_action_read(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
    IF NOT life_financial.spot_actions_verified(p_account) THEN RAISE EXCEPTION 'LIFE_ACTION_PROJECTION_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    RETURN (SELECT COALESCE(jsonb_agg(jsonb_build_object('action',d.descriptor,'state',q.state) ORDER BY q.action_id),'[]'::jsonb)
        FROM life_financial.quote_actions q JOIN life_financial.spot_action_descriptors d USING(account_id,action_id) WHERE q.account_id=p_account);
END; $$;

CREATE FUNCTION life_financial.spot_action_commit_guard(p_account uuid,p_request jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.spot_action_policies; observed timestamptz; at timestamptz;
BEGIN
    SELECT p0.* INTO p FROM life_financial.spot_action_policies p0 JOIN life_financial.accounts a USING(account_id,policy_hash) WHERE a.account_id=p_account;
    IF NOT FOUND OR NOT life_financial.spot_actions_verified(p_account) THEN
        RAISE EXCEPTION 'LIFE_ACTION_PROJECTION_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    IF p_request->>'kind'='SPOT_ACTION_TRANSITION' THEN
        observed:=(p_request#>>'{command,facts,observed_at}')::timestamptz; at:=clock_timestamp();
        IF NOT isfinite(observed) OR observed>at OR extract(epoch FROM at-observed)*1000>p.max_fact_age_ms THEN
            RAISE EXCEPTION 'LIFE_ACTION_FACTS_STALE_AT_COMMIT' USING ERRCODE='P0001'; END IF;
    END IF;
    IF p_request->>'kind'='SPOT_ACTION_CLAIM' OR p_request#>>'{command,state}'='DISPATCHED' THEN
        PERFORM life_financial.guard_spot_session(p_account); END IF;
END; $$;

CREATE FUNCTION life_financial.spot_action_command(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
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
    -- This increment has no action-to-prepare bridge. Never introduce an action
    -- barrier while an existing order could need late fill/fee accounting.
    IF p_kind='SPOT_ACTION_CLAIM' AND ((view->>'recovery_required')::boolean
        OR EXISTS(SELECT 1 FROM life_financial.send_attempts WHERE account_id=p_account AND state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND'))
        OR EXISTS(SELECT 1 FROM life_financial.reservations WHERE account_id=p_account AND state<>'TERMINAL')) THEN
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
    RETURN NULL;
END; $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
