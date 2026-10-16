-- Durable primary-session safety state. No successor activation or send grant.
-- Existing immutable session deadlines/configuration remain authoritative.
CREATE TABLE life_financial.spot_session_policies (
    account_id uuid NOT NULL, session_id uuid NOT NULL,
    policy_hash life_financial.digest NOT NULL,
    on_expiry text NOT NULL CHECK (on_expiry IN ('pause_quotes','switch_to_market_reference')),
    max_reconciliation_age_ms integer NOT NULL CHECK (max_reconciliation_age_ms>0),
    initial_last_seen_at timestamptz NOT NULL CHECK (isfinite(initial_last_seen_at)),
    initial_state text NOT NULL CHECK (initial_state IN ('ACTIVE','PAUSED')),
    PRIMARY KEY(account_id,session_id),
    FOREIGN KEY(account_id,session_id) REFERENCES life_financial.sessions,
    FOREIGN KEY(account_id,policy_hash) REFERENCES life_financial.policies
);
CREATE TABLE life_financial.spot_session_progress (
    account_id uuid NOT NULL, session_id uuid NOT NULL,
    state text NOT NULL CHECK (state IN ('ACTIVE','PAUSED','EXPIRED','TRANSITIONING')),
    reason_code life_financial.identity NOT NULL,
    last_seen_at timestamptz NOT NULL CHECK (isfinite(last_seen_at)),
    rollback_latched boolean NOT NULL,
    version bigint NOT NULL CHECK (version>=0),
    PRIMARY KEY(account_id,session_id),
    FOREIGN KEY(account_id,session_id) REFERENCES life_financial.spot_session_policies,
    CHECK (NOT rollback_latched OR state='PAUSED' AND reason_code='CLOCK_ROLLBACK')
);
CREATE TABLE life_financial.spot_session_events (
    account_id uuid NOT NULL, session_id uuid NOT NULL,
    operation_id life_financial.identity NOT NULL,
    progress_version bigint NOT NULL CHECK (progress_version>0),
    before_state jsonb NOT NULL, after_state jsonb NOT NULL,
    PRIMARY KEY(account_id,operation_id),
    UNIQUE(account_id,session_id,progress_version),
    FOREIGN KEY(account_id,session_id) REFERENCES life_financial.spot_session_progress,
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED
);
DO $$ DECLARE t text; BEGIN
    FOREACH t IN ARRAY ARRAY['spot_session_policies','spot_session_progress','spot_session_events'] LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY scoped_read ON life_financial.%I FOR SELECT USING (life_financial.can_access(account_id))',t);
        EXECUTE format('CREATE TRIGGER no_delete BEFORE DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',t);
    END LOOP;
END; $$;
CREATE TRIGGER immutable_history BEFORE UPDATE ON life_financial.spot_session_policies
    FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record();
CREATE TRIGGER immutable_history BEFORE UPDATE ON life_financial.spot_session_events
    FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record();
CREATE TRIGGER monotonic_version BEFORE UPDATE ON life_financial.spot_session_progress
    FOR EACH ROW EXECUTE FUNCTION life_financial.version_record();
CREATE FUNCTION life_financial.spot_session_progress_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
    IF NEW.last_seen_at<OLD.last_seen_at OR OLD.rollback_latched AND NOT NEW.rollback_latched
        OR OLD.state='EXPIRED' AND NEW.state NOT IN ('EXPIRED','PAUSED')
        OR OLD.state='TRANSITIONING' AND NEW.state NOT IN ('TRANSITIONING','PAUSED') THEN
        RAISE EXCEPTION 'LIFE_SESSION_STATE_REGRESSION' USING ERRCODE='P0001'; END IF;
    RETURN NEW;
END; $$;
CREATE TRIGGER safety_latches BEFORE UPDATE ON life_financial.spot_session_progress
    FOR EACH ROW EXECUTE FUNCTION life_financial.spot_session_progress_guard();

-- Indexed latest-event verification, under the same migration-owner trust as
-- other PG3 projections. This is not an independently signed history anchor.
CREATE FUNCTION life_financial.spot_session_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts; p life_financial.spot_session_policies;
    s life_financial.sessions; g life_financial.spot_session_progress; e life_financial.spot_session_events;
BEGIN
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account;
    SELECT * INTO p FROM life_financial.spot_session_policies WHERE account_id=p_account AND session_id=a.current_session;
    IF NOT FOUND OR p.policy_hash<>a.policy_hash THEN RETURN false; END IF;
    SELECT * INTO STRICT s FROM life_financial.sessions WHERE account_id=p_account AND session_id=a.current_session;
    SELECT * INTO g FROM life_financial.spot_session_progress WHERE account_id=p_account AND session_id=a.current_session;
    IF NOT FOUND OR g.last_seen_at<s.started_at THEN RETURN false; END IF;
    IF g.version=0 THEN
        RETURN g.last_seen_at=p.initial_last_seen_at AND g.state=p.initial_state AND NOT g.rollback_latched
            AND g.reason_code=CASE p.initial_state WHEN 'ACTIVE' THEN 'SESSION_ACTIVE' ELSE 'SESSION_GATE_UNAVAILABLE' END
            AND NOT EXISTS(SELECT 1 FROM life_financial.spot_session_events WHERE account_id=p_account AND session_id=a.current_session);
    END IF;
    SELECT * INTO e FROM life_financial.spot_session_events WHERE account_id=p_account AND session_id=a.current_session
        ORDER BY progress_version DESC LIMIT 1;
    RETURN FOUND AND e.progress_version=g.version AND e.after_state=to_jsonb(g)
        AND EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=p_account AND o.operation_id=e.operation_id
            AND o.command->>'kind'='SPOT_SESSION_TICK' AND o.receipt->>'state'=g.state
            AND o.receipt->>'reason_code'=g.reason_code);
END; $$;
CREATE FUNCTION life_financial.guard_spot_session(p_account uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts; g life_financial.spot_session_progress; s life_financial.sessions; at timestamptz;
BEGIN
    IF NOT life_financial.can_access(p_account,true) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    IF NOT life_financial.spot_session_verified(p_account) THEN RAISE EXCEPTION 'LIFE_SESSION_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT g FROM life_financial.spot_session_progress WHERE account_id=p_account AND session_id=a.current_session;
    SELECT * INTO STRICT s FROM life_financial.sessions WHERE account_id=p_account AND session_id=a.current_session;
    at:=clock_timestamp();
    IF g.state<>'ACTIVE' OR g.rollback_latched OR at<g.last_seen_at OR at<s.started_at OR at>=s.expires_at THEN
        RAISE EXCEPTION 'LIFE_SESSION_NOT_QUOTABLE' USING ERRCODE='P0001'; END IF;
END; $$;

-- Preserve the previous financial implementations, adding the mandatory
-- session barrier to both entrypoints and the content of every snapshot cut.
ALTER FUNCTION life_financial.spot_state(uuid) RENAME TO spot_state_v7;
ALTER FUNCTION life_financial.spot_prepare(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb) RENAME TO spot_prepare_v7;
ALTER FUNCTION life_financial.spot_arm(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,life_financial.identity) RENAME TO spot_arm_v7;
-- A rename retains ACLs. Remove every inherited nonowner grant so old entrypoints
-- cannot bypass the new barrier after upgrading an already provisioned database.
DO $$ DECLARE r record; BEGIN
    FOR r IN SELECT p.oid::regprocedure AS signature,x.grantee FROM pg_proc p
        JOIN pg_namespace n ON n.oid=p.pronamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE n.nspname='life_financial' AND p.proname IN ('spot_state_v7','spot_prepare_v7','spot_arm_v7')
            AND x.grantee<>p.proowner LOOP
        IF r.grantee=0 THEN EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',r.signature);
        ELSE EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',r.signature,pg_get_userbyid(r.grantee)); END IF;
    END LOOP;
END; $$;
CREATE FUNCTION life_financial.spot_state(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE body jsonb; current_id uuid; verified boolean;
BEGIN
    body:=life_financial.spot_state_v7(p_account)-'digest'-'observed_at';
    current_id:=(body#>>'{account,current_session}')::uuid;
    verified:=life_financial.spot_session_verified(p_account);
    body:=body||jsonb_build_object(
        'session_lifecycle',(SELECT to_jsonb(g) FROM life_financial.spot_session_progress g WHERE g.account_id=p_account AND g.session_id=current_id),
        'session_policy',(SELECT to_jsonb(p) FROM life_financial.spot_session_policies p WHERE p.account_id=p_account AND p.session_id=current_id),
        'recovery_required',(body->>'recovery_required')::boolean OR NOT verified,
        'unverified',(body->>'unverified')::boolean OR NOT verified);
    RETURN body||jsonb_build_object('digest',encode(sha256(convert_to(body::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
END; $$;
CREATE FUNCTION life_financial.spot_prepare(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,p_command jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
    PERFORM life_financial.guard_spot_session(p_account);
    RETURN life_financial.spot_prepare_v7(p_account,p_binding,p_owner,p_version,p_cut,p_fingerprint,p_command);
END; $$;
CREATE FUNCTION life_financial.spot_arm(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,p_prepare life_financial.identity) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
    PERFORM life_financial.guard_spot_session(p_account);
    RETURN life_financial.spot_arm_v7(p_account,p_binding,p_owner,p_version,p_cut,p_fingerprint,p_prepare);
END; $$;

CREATE FUNCTION life_financial.spot_session_tick(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,p_command jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts; s life_financial.sessions; p life_financial.spot_session_policies;
    g life_financial.spot_session_progress; prior life_financial.operations; request jsonb; result jsonb;
    before_state jsonb; at timestamptz; op life_financial.identity; enable boolean;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR p_cut IS NULL OR p_fingerprint IS NULL OR p_command IS NULL
        OR jsonb_typeof(p_command)<>'object' OR octet_length(p_command::text)>16384 THEN
        RAISE EXCEPTION 'LIFE_SESSION_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    IF (SELECT count(*) FROM jsonb_object_keys(p_command))<>10 OR NOT p_command ?& ARRAY[
        'operation_id','session_id','epoch','reference_ready','all_gates_ready','market_reference_ready',
        'max_reconciliation_age_ms','reconciliation','account','market_anchor_usdt']
        OR p_command->'reconciliation'<>'null'::jsonb OR p_command->'account'<>'null'::jsonb
        OR p_command->'market_anchor_usdt'<>'null'::jsonb
        OR EXISTS(SELECT 1 FROM jsonb_each(p_command) WHERE key NOT IN ('reconciliation','account','market_anchor_usdt') AND value='null'::jsonb)
        OR EXISTS(SELECT 1 FROM jsonb_each(p_command) WHERE key IN ('reference_ready','all_gates_ready','market_reference_ready')
            AND jsonb_typeof(value)<>'boolean') THEN
        RAISE EXCEPTION 'LIFE_SESSION_MODE_UNSUPPORTED' USING ERRCODE='P0001'; END IF;
    op:=p_command->>'operation_id';
    IF length(op)>96 THEN RAISE EXCEPTION 'LIFE_SESSION_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    request:=jsonb_build_object('kind','SPOT_SESSION_TICK','binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',p_command);
    SELECT * INTO prior FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF prior.command<>request OR prior.fingerprint<>p_fingerprint THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001'; END IF;
        RETURN prior.receipt||'{"replayed":true}'::jsonb;
    END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    IF life_financial.spot_state(p_account)->>'digest'<>p_cut THEN RAISE EXCEPTION 'LIFE_SPOT_CUT_CONFLICT' USING ERRCODE='P0001'; END IF;
    IF NOT life_financial.spot_session_verified(p_account) THEN RAISE EXCEPTION 'LIFE_SESSION_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT s FROM life_financial.sessions WHERE account_id=p_account AND session_id=a.current_session;
    SELECT * INTO STRICT p FROM life_financial.spot_session_policies WHERE account_id=p_account AND session_id=a.current_session;
    SELECT * INTO STRICT g FROM life_financial.spot_session_progress WHERE account_id=p_account AND session_id=a.current_session;
    IF (p_command->>'session_id')::uuid<>s.session_id OR (p_command->>'epoch')::bigint<>s.epoch
        OR (p_command->>'max_reconciliation_age_ms')::integer<>p.max_reconciliation_age_ms THEN
        RAISE EXCEPTION 'LIFE_SESSION_SCOPE_OR_POLICY_INVALID' USING ERRCODE='P0001'; END IF;
    enable:=(p_command->>'reference_ready')::boolean OR (p_command->>'all_gates_ready')::boolean
        OR (p_command->>'market_reference_ready')::boolean;
    IF enable THEN
        PERFORM life_financial.guard_account(p_account,p_binding,p_owner,p_version);
        IF (life_financial.spot_state(p_account)->>'recovery_required')::boolean THEN
            RAISE EXCEPTION 'LIFE_SESSION_RECOVERY_REQUIRED' USING ERRCODE='P0001'; END IF;
    END IF;
    before_state:=to_jsonb(g); at:=clock_timestamp();
    IF at<g.last_seen_at THEN
        g.state:='PAUSED'; g.reason_code:='CLOCK_ROLLBACK'; g.rollback_latched:=true;
    ELSIF NOT g.rollback_latched THEN
        IF at>=s.expires_at THEN
            IF p.on_expiry='pause_quotes' THEN g.state:='EXPIRED'; g.reason_code:='SESSION_DEADLINE_REACHED';
            ELSE g.state:='TRANSITIONING'; g.reason_code:='OLD_ORDERS_UNRESOLVED'; END IF;
        ELSIF g.state NOT IN ('EXPIRED','TRANSITIONING') THEN
            IF (p_command->>'reference_ready')::boolean AND (p_command->>'all_gates_ready')::boolean THEN
                g.state:='ACTIVE'; g.reason_code:='SESSION_ACTIVE';
            ELSE g.state:='PAUSED'; g.reason_code:='SESSION_GATE_UNAVAILABLE'; END IF;
        END IF;
        g.last_seen_at:=at;
    END IF;
    g.version:=g.version+1;
    UPDATE life_financial.spot_session_progress SET state=g.state,reason_code=g.reason_code,last_seen_at=g.last_seen_at,
        rollback_latched=g.rollback_latched,version=g.version WHERE account_id=p_account AND session_id=a.current_session;
    INSERT INTO life_financial.spot_session_events VALUES(p_account,a.current_session,op,g.version,before_state,to_jsonb(g));
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    result:=jsonb_build_object('kind','SPOT_SESSION_TICK','operation_id',op,'state',g.state,'reason_code',g.reason_code,
        'version',p_version+1,'replayed',false,'trading_permission',false);
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
    IF op.command->>'kind' IN ('SPOT_ACK','SPOT_FILL','SPOT_CASHFLOW','SPOT_NAV','SPOT_SESSION_TICK') THEN
        PERFORM life_financial.guard_spot_observation(NEW.account_id,op.command->'binding',op.command->'owner',a.version);
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
