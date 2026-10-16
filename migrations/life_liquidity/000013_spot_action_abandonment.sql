-- Explicit runner retirement before financial preparation. No financial release.
CREATE OR REPLACE FUNCTION life_financial.spot_actions_verified(p_account uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT NOT EXISTS(
    SELECT 1 FROM life_financial.quote_actions q WHERE q.account_id=p_account AND NOT EXISTS(
        SELECT 1 FROM life_financial.spot_action_descriptors d JOIN life_financial.spot_action_events e USING(account_id,action_id)
        JOIN life_financial.operations o USING(account_id,operation_id)
        WHERE d.account_id=q.account_id AND d.action_id=q.action_id AND e.action_version=q.version
            AND e.after_state=to_jsonb(q) AND e.descriptor=d.descriptor AND e.command=o.command->'command'
            AND o.command->>'kind' IN ('SPOT_ACTION_CLAIM','SPOT_ACTION_TRANSITION','SPOT_ACTION_ABANDON','SPOT_PREPARE')
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
            WHEN 'SPOT_ACTION_ABANDON' THEN e.command->'action'
            WHEN 'SPOT_PREPARE' THEN o.command->'action' END
        OR NOT o.receipt->'states' @> jsonb_build_array(jsonb_build_array(e.action_id,e.after_state->>'state'))))
AND NOT EXISTS(SELECT 1 FROM life_financial.quote_actions q WHERE q.account_id=p_account AND q.state IN ('PROPOSED','DISPATCHED','REJECTED')
    AND (EXISTS(SELECT 1 FROM life_financial.send_attempts t WHERE t.account_id=q.account_id AND t.intent_id=q.action_id)
        OR EXISTS(SELECT 1 FROM life_financial.reservations r WHERE r.account_id=q.account_id AND r.intent_id=q.action_id)) )
AND NOT EXISTS(SELECT 1 FROM life_financial.quote_actions q WHERE q.account_id=p_account AND (
    q.state IN ('PREPARED','RECONCILED') AND NOT EXISTS(
        SELECT 1 FROM life_financial.send_attempts t JOIN life_financial.spot_preparations d USING(account_id,intent_id)
        JOIN life_financial.operations o ON o.account_id=t.account_id AND o.operation_id=t.operation_id
        JOIN life_financial.spot_action_descriptors x ON x.account_id=q.account_id AND x.action_id=q.action_id
        WHERE t.account_id=q.account_id AND t.intent_id=q.action_id AND t.session_id=q.session_id AND t.side=q.side
            AND d.quote_level=q.level AND o.command->>'kind'='SPOT_PREPARE' AND o.command->'action'=x.descriptor
            AND o.command->'command'=d.request AND o.command ? 'admission')))
-- Verify account-wide lifecycle once, not once per retained action.
AND (NOT EXISTS(SELECT 1 FROM life_financial.quote_actions WHERE account_id=p_account AND state='RECONCILED')
    OR life_financial.spot_lifecycle_verified(p_account))
AND NOT EXISTS(SELECT 1 FROM life_financial.quote_actions q WHERE q.account_id=p_account AND q.state='RECONCILED'
    AND NOT life_financial.spot_action_closure_verified(q.account_id,q.action_id));
$$;




CREATE OR REPLACE FUNCTION life_financial.spot_action_command(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,p_kind text,p_command jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts; s life_financial.sessions; policy life_financial.spot_action_policies;
    previous life_financial.operations; claim_op life_financial.operations; q life_financial.quote_actions; view jsonb; request jsonb; result jsonb;
    record jsonb; facts jsonb; records jsonb; op life_financial.identity; item text; next_state text;
    observed timestamptz; at timestamptz; dispatched_at timestamptz; states jsonb:='[]'::jsonb;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR p_kind IS NULL OR p_kind NOT IN ('SPOT_ACTION_CLAIM','SPOT_ACTION_TRANSITION','SPOT_ACTION_ABANDON')
        OR p_cut IS NULL OR p_fingerprint IS NULL OR p_command IS NULL OR jsonb_typeof(p_command)<>'object'
        OR octet_length(p_command::text)>1048576 THEN RAISE EXCEPTION 'LIFE_ACTION_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    IF p_kind='SPOT_ACTION_CLAIM' THEN
        IF (SELECT count(*) FROM jsonb_object_keys(p_command))<>2 OR NOT p_command ?& ARRAY['operation_id','actions']
            OR jsonb_typeof(p_command->'actions') IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION 'LIFE_ACTION_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
        records:=p_command->'actions'; next_state:='PROPOSED';
    ELSE
        IF (SELECT count(*) FROM jsonb_object_keys(p_command))<>(CASE WHEN p_kind='SPOT_ACTION_ABANDON' THEN 6 ELSE 5 END) OR NOT p_command ?& ARRAY['operation_id','action','expected_state','state','facts']
            OR jsonb_typeof(p_command->'expected_state') IS DISTINCT FROM 'string'
            OR jsonb_typeof(p_command->'state') IS DISTINCT FROM 'string'
            OR p_kind='SPOT_ACTION_ABANDON' AND p_command->>'reason_code' IS DISTINCT FROM 'RUNNER_RETIRED_BEFORE_PREPARATION'
            OR NOT (p_kind='SPOT_ACTION_ABANDON' AND p_command->>'expected_state'='DISPATCHED' AND p_command->>'state'='REJECTED'
                OR p_kind='SPOT_ACTION_TRANSITION' AND (p_command->>'expected_state'='PROPOSED' AND p_command->>'state' IN ('DISPATCHED','REJECTED','RECONCILED')
                    OR p_command->>'expected_state' IN ('DISPATCHED','PREPARED') AND p_command->>'state'='RECONCILED')) THEN
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
    IF p_kind IN ('SPOT_ACTION_TRANSITION','SPOT_ACTION_ABANDON') THEN
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
            IF p_kind='SPOT_ACTION_ABANDON' THEN
                SELECT o.recorded_at INTO dispatched_at FROM life_financial.spot_action_events e
                    JOIN life_financial.operations o USING(account_id,operation_id)
                    WHERE e.account_id=p_account AND e.action_id=q.action_id AND e.action_version=q.version;
                IF observed<dispatched_at THEN RAISE EXCEPTION 'LIFE_ACTION_RUNNER_FACTS_BEFORE_DISPATCH' USING ERRCODE='P0001'; END IF;
            END IF;
            IF next_state='RECONCILED' THEN
                IF q.state<>'PREPARED' OR NOT life_financial.spot_lifecycle_verified(p_account)
                    OR NOT life_financial.spot_action_closure_verified(p_account,q.action_id) THEN
                    RAISE EXCEPTION 'LIFE_ACTION_RECONCILIATION_UNPROVEN' USING ERRCODE='P0001'; END IF;
                IF observed<life_financial.spot_action_closed_at(p_account,q.action_id) THEN
                    RAISE EXCEPTION 'LIFE_ACTION_RUNNER_FACTS_BEFORE_CLOSURE' USING ERRCODE='P0001'; END IF;
            END IF;
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




CREATE OR REPLACE FUNCTION life_financial.spot_action_commit_guard(p_account uuid,p_request jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.spot_action_policies; observed timestamptz; at timestamptz;
BEGIN
    SELECT p0.* INTO p FROM life_financial.spot_action_policies p0 JOIN life_financial.accounts a USING(account_id,policy_hash) WHERE a.account_id=p_account;
    IF NOT FOUND OR NOT life_financial.spot_actions_verified(p_account) THEN
        RAISE EXCEPTION 'LIFE_ACTION_PROJECTION_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    IF p_request->>'kind' IN ('SPOT_ACTION_TRANSITION','SPOT_ACTION_ABANDON') THEN
        observed:=(p_request#>>'{command,facts,observed_at}')::timestamptz; at:=clock_timestamp();
        IF NOT isfinite(observed) OR observed>at OR extract(epoch FROM at-observed)*1000>p.max_fact_age_ms THEN
            RAISE EXCEPTION 'LIFE_ACTION_FACTS_STALE_AT_COMMIT' USING ERRCODE='P0001'; END IF;
    END IF;
    IF p_request->>'kind'='SPOT_ACTION_CLAIM' OR p_request#>>'{command,state}'='DISPATCHED' THEN
        PERFORM life_financial.guard_spot_session(p_account); END IF;
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
    IF op.command->>'kind' IN ('SPOT_ACK','SPOT_FILL','SPOT_CASHFLOW','SPOT_NAV','SPOT_SESSION_TICK','SPOT_LIFECYCLE','SPOT_TERMINAL') OR op.command->>'kind' IN ('SPOT_ACTION_TRANSITION','SPOT_ACTION_ABANDON')
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
    IF op.command->>'kind' IN ('SPOT_ACTION_CLAIM','SPOT_ACTION_TRANSITION','SPOT_ACTION_ABANDON') THEN
        PERFORM life_financial.spot_action_commit_guard(NEW.account_id,op.command); END IF;
    IF op.command->>'kind'='SPOT_PREPARE' AND op.command ? 'action' THEN
        PERFORM life_financial.spot_action_prepare_commit_guard(NEW.account_id,op.command); END IF;
    RETURN NULL;
END; $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
