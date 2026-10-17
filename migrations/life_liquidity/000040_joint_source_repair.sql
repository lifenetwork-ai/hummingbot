-- Signed source-only repair. Original pending commands/receipts and HALT are
-- immutable; RECOVER never grants ACTIVATE or rewrites monetary observations.
CREATE TABLE life_financial.joint_repair_policies (
    account_id uuid PRIMARY KEY REFERENCES life_financial.joint_batch_policies,
    policy_hash life_financial.digest NOT NULL,
    max_approvals integer NOT NULL CHECK(max_approvals BETWEEN 1 AND 4096),
    max_repairs integer NOT NULL CHECK(max_repairs BETWEEN 1 AND 4096),
    max_total_bytes integer NOT NULL CHECK(max_total_bytes BETWEEN 1 AND 16777216),
    max_fact_age_ms integer NOT NULL CHECK(max_fact_age_ms BETWEEN 1 AND 60000),
    FOREIGN KEY(account_id,policy_hash) REFERENCES life_financial.policies
);
CREATE TABLE life_financial.joint_repair_approvals (
    account_id uuid NOT NULL REFERENCES life_financial.joint_repair_policies,
    operation_id life_financial.identity NOT NULL,pending_operation_id life_financial.identity NOT NULL,
    body jsonb NOT NULL,source_hash life_financial.digest NOT NULL,captured_at timestamptz NOT NULL CHECK(isfinite(captured_at)),
    PRIMARY KEY(account_id,operation_id),
    FOREIGN KEY(account_id,pending_operation_id) REFERENCES life_financial.joint_financial_batches
);
CREATE TABLE life_financial.joint_repair_facts (
    account_id uuid NOT NULL,operation_id life_financial.identity NOT NULL,
    pending_operation_id life_financial.identity NOT NULL,replacement_operation_id life_financial.identity NOT NULL,
    received_at_ms bigint NOT NULL CHECK(received_at_ms>0),
    PRIMARY KEY(account_id,operation_id),UNIQUE(account_id,pending_operation_id),UNIQUE(account_id,replacement_operation_id),
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.joint_repair_approvals,
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(account_id,pending_operation_id) REFERENCES life_financial.joint_financial_batches,
    FOREIGN KEY(account_id,replacement_operation_id) REFERENCES life_financial.joint_financial_batches
);
-- Private capability for the exact signed replacement only. Never committed.
CREATE TABLE life_financial.joint_repair_staging (
    account_id uuid PRIMARY KEY REFERENCES life_financial.accounts,
    transaction_id xid8 NOT NULL,command jsonb NOT NULL
);
DO $$ DECLARE t text;BEGIN
    FOREACH t IN ARRAY ARRAY['joint_repair_policies','joint_repair_approvals','joint_repair_facts'] LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY scoped_read ON life_financial.%I FOR SELECT USING(life_financial.can_access(account_id))',t);
        EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',t);
    END LOOP;
END; $$;

-- This proof checks a grant at its ORIGINAL cut, not against later facts. The
-- privileged importer verifies Ed25519 using external public keys; SQL proves
-- consistency under the declared trusted schema-owner contract.
CREATE FUNCTION life_financial.joint_repair_profile(p_account uuid,cmd jsonb,facts jsonb,p_version bigint) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE raw life_financial.joint_financial_batches;replacement jsonb;src jsonb;a jsonb;ids jsonb;expected jsonb;
    opening_ms bigint;coverage_from bigint;coverage_through bigint;observed_ms bigint;
BEGIN
    IF jsonb_typeof(cmd) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(cmd))<>3
        OR NOT cmd ?& ARRAY['operation_id','pending_operation_id','replacement']
        OR cmd->>'operation_id' !~ '^[A-Za-z0-9_.:-]{1,96}$' THEN RETURN false;END IF;
    SELECT * INTO STRICT raw FROM life_financial.joint_financial_batches WHERE account_id=p_account AND operation_id=cmd->>'pending_operation_id';
    replacement:=cmd->'replacement';src:=replacement#>'{funding,bundle}';a:=facts->'account';
    IF raw.state<>'RECOVERY_REQUIRED' OR raw.start_version>=p_version
        OR replacement->'spot_fills' IS DISTINCT FROM raw.command->'spot_fills'
        OR replacement->'swap_fills' IS DISTINCT FROM raw.command->'swap_fills'
        OR replacement->'cashflows' IS DISTINCT FROM raw.command->'cashflows'
        OR replacement#>>'{funding,operation_id}' IS NOT DISTINCT FROM raw.command#>>'{funding,operation_id}'
        OR EXISTS(SELECT 1 FROM (SELECT cmd->>'operation_id' id UNION ALL SELECT raw.operation_id
            UNION ALL SELECT replacement->>'operation_id' UNION ALL SELECT m#>>'{command,operation_id}'
            FROM jsonb_array_elements(life_financial.joint_batch_members(replacement)) m) x GROUP BY id HAVING count(*)>1 OR id IS NULL)
        OR facts->'binding' IS DISTINCT FROM a->'binding' OR facts->>'observed_at' IS DISTINCT FROM a->>'observed_at'
        OR a->'order_scope_complete' IS DISTINCT FROM 'true'::jsonb OR a->'bill_scope_complete' IS DISTINCT FROM 'true'::jsonb
        OR a->'runner_scope_complete' IS DISTINCT FROM 'true'::jsonb OR facts->'swap_scope_complete' IS DISTINCT FROM 'true'::jsonb
        OR (a->>'liability_base_life')::numeric<>0 OR (a->>'liability_quote_usdt')::numeric<>0
        OR (a->>'account_base_life')::numeric IS DISTINCT FROM (src#>>'{account,snapshot,spot_life_base}')::numeric
        OR (a->>'account_quote_usdt')::numeric IS DISTINCT FROM (src#>>'{account,snapshot,spot_cash_quote}')::numeric THEN RETURN false;END IF;
    SELECT floor(extract(epoch FROM opened_at)*1000)::bigint INTO STRICT opening_ms FROM life_financial.spot_capital_policies WHERE account_id=p_account;
    coverage_from:=(facts->>'coverage_from_ms')::bigint;coverage_through:=(facts->>'coverage_through_ms')::bigint;
    observed_ms:=floor(extract(epoch FROM (facts->>'observed_at')::timestamptz)*1000)::bigint;
    IF NOT (0<coverage_from AND coverage_from<=opening_ms AND coverage_from<=coverage_through AND coverage_through<=observed_ms)
        OR coverage_through<(src#>>'{funding,coverage_through_ms}')::bigint
        OR coverage_through<(src#>>'{account,snapshot,observed_at_ms}')::bigint
        OR EXISTS(SELECT 1 FROM jsonb_array_elements(raw.command#>'{funding,bundle,funding,payments}') old
            WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(src#>'{funding,payments}') fresh
                WHERE fresh->>'bill_id'=old->>'bill_id' AND (fresh->>'settled_at_ms')::bigint=(old->>'settled_at_ms')::bigint
                AND (fresh->>'signed_payment_quote')::numeric=(old->>'signed_payment_quote')::numeric)) THEN RETURN false;END IF;
    -- Compare sorted sets AND lengths, so duplicates cannot hide extra facts.
    SELECT COALESCE(jsonb_agg(pair ORDER BY pair::text),'[]') INTO expected FROM (
        SELECT jsonb_build_array('SPOT',f.trade_id) pair FROM life_financial.spot_fill_facts f JOIN life_financial.operations o ON o.account_id=f.account_id AND o.operation_id=f.source_operation_id
            WHERE f.account_id=p_account AND o.committed_version<=p_version
        UNION SELECT jsonb_build_array('SWAP',f.trade_id) FROM life_financial.swap_fill_facts f JOIN life_financial.operations o ON o.account_id=f.account_id AND o.operation_id=f.source_operation_id
            WHERE f.account_id=p_account AND o.committed_version<=p_version
        UNION SELECT jsonb_build_array('SPOT',f->>'trade_id') FROM jsonb_array_elements(replacement->'spot_fills') m CROSS JOIN LATERAL jsonb_array_elements(m->'fills') f
        UNION SELECT jsonb_build_array('SWAP',f->>'trade_id') FROM jsonb_array_elements(replacement->'swap_fills') m CROSS JOIN LATERAL jsonb_array_elements(m->'fills') f) x;
    SELECT COALESCE(jsonb_agg(value ORDER BY value::text),'[]') INTO ids FROM jsonb_array_elements(facts->'product_trade_ids');
    IF ids IS DISTINCT FROM expected THEN RETURN false;END IF;
    SELECT COALESCE(jsonb_agg(id ORDER BY id),'[]') INTO expected FROM (SELECT DISTINCT value->>1 id FROM jsonb_array_elements(ids)) x;
    SELECT COALESCE(jsonb_agg(value ORDER BY value),'[]') INTO ids FROM jsonb_array_elements_text(a->'account_trade_ids');
    IF ids IS DISTINCT FROM expected THEN RETURN false;END IF;
    SELECT COALESCE(jsonb_agg(id ORDER BY id),'[]') INTO expected FROM (
        SELECT f.bill_id::text id FROM life_financial.spot_cashflow_facts f JOIN life_financial.operations o ON o.account_id=f.account_id AND o.operation_id=f.source_operation_id
            WHERE f.account_id=p_account AND f.state='APPLIED' AND o.committed_version<=p_version
        UNION SELECT m->>'bill_id' FROM jsonb_array_elements(replacement->'cashflows') m) x;
    SELECT COALESCE(jsonb_agg(value ORDER BY value),'[]') INTO ids FROM jsonb_array_elements_text(a->'account_cashflow_ids');
    IF ids IS DISTINCT FROM expected THEN RETURN false;END IF;
    SELECT COALESCE(jsonb_agg(value->>'bill_id' ORDER BY value->>'bill_id'),'[]') INTO expected FROM jsonb_array_elements(src#>'{funding,payments}');
    SELECT COALESCE(jsonb_agg(value ORDER BY value),'[]') INTO ids FROM jsonb_array_elements_text(facts->'funding_bill_ids');
    IF ids IS DISTINCT FROM expected THEN RETURN false;END IF;
    IF EXISTS(SELECT 1 FROM jsonb_array_elements_text(a->'accepted_wire_ids') wire WHERE NOT EXISTS(
            SELECT 1 FROM life_financial.send_attempts t WHERE t.account_id=p_account AND t.wire_id=wire))
        OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(a->'runner_wire_ids') wire WHERE NOT EXISTS(
            SELECT 1 FROM life_financial.send_attempts t WHERE t.account_id=p_account AND t.wire_id=wire))
        OR EXISTS(SELECT 1 FROM life_financial.send_attempts t WHERE t.account_id=p_account AND t.state IN ('ACKED','TERMINAL')
            AND EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=p_account AND o.committed_version<=p_version
                AND o.command->>'kind' IN ('SPOT_ACK','SPOT_FILL','SWAP_ACK','SWAP_FILL','SWAP_TERMINAL')
                AND o.command#>>'{command,client_order_id}'=t.wire_id)
            AND NOT a->'accepted_wire_ids' @> jsonb_build_array(t.wire_id))
        OR EXISTS(SELECT 1 FROM jsonb_array_elements(replacement->'spot_fills') m WHERE NOT a->'accepted_wire_ids' @> jsonb_build_array(m->>'client_order_id'))
        OR EXISTS(SELECT 1 FROM jsonb_array_elements(replacement->'swap_fills') m WHERE NOT a->'accepted_wire_ids' @> jsonb_build_array(m->>'client_order_id')) THEN RETURN false;END IF;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.joint_repair_approval_integrity(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE p life_financial.joint_repair_policies;r life_financial.joint_repair_approvals;b jsonb;auth jsonb;policy jsonb;raw jsonb;v bigint;
BEGIN
    SELECT * INTO p FROM life_financial.joint_repair_policies WHERE account_id=p_account;
    IF NOT FOUND THEN RETURN NOT EXISTS(SELECT 1 FROM life_financial.joint_repair_approvals WHERE account_id=p_account);END IF;
    IF p.policy_hash IS DISTINCT FROM (SELECT policy_hash FROM life_financial.accounts WHERE account_id=p_account)
        OR (SELECT count(*) FROM life_financial.joint_repair_approvals WHERE account_id=p_account)>p.max_approvals
        OR (SELECT COALESCE(sum(octet_length(to_jsonb(r)::text)),0) FROM life_financial.joint_repair_approvals r WHERE account_id=p_account)
            +(SELECT COALESCE(sum(octet_length(to_jsonb(r)::text)),0) FROM life_financial.joint_repair_facts r WHERE account_id=p_account)>p.max_total_bytes THEN RETURN false;END IF;
    FOR r IN SELECT * FROM life_financial.joint_repair_approvals WHERE account_id=p_account LOOP
        b:=r.body;auth:=b->'approval';v:=(b#>>'{cut,checkpoint,revision}')::bigint;
        SELECT command INTO STRICT raw FROM life_financial.joint_financial_batches WHERE account_id=p_account AND operation_id=r.pending_operation_id;
        SELECT c.body->'operator_verifier' INTO policy FROM life_financial.configurations c WHERE c.account_id=p_account
            AND c.config_hash=auth#>>'{binding,config_hash}' AND c.policy_hash=auth#>>'{binding,policy_hash}';
        IF policy IS NULL OR policy IS DISTINCT FROM b->'verifier_policy' OR auth->>'role' IS DISTINCT FROM 'RECOVER'
            OR NOT COALESCE(policy#>ARRAY[auth->>'operator_id','roles'] @> '["RECOVER"]'::jsonb,false)
            OR b#>>'{payload,operation_id}' IS DISTINCT FROM r.operation_id OR b#>>'{payload,pending_operation_id}' IS DISTINCT FROM r.pending_operation_id
            OR b#>'{cut,checkpoint,binding}' IS DISTINCT FROM auth->'binding' OR b#>'{facts,binding}' IS DISTINCT FROM auth->'binding'
            OR auth->>'request_digest' IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(jsonb_build_object('command',b->'payload','cut',b->'cut','facts',b->'facts')),'UTF8')),'hex')
            OR b->>'signature' IS NULL OR b->>'signature' !~ '^[0-9a-f]{128}$'
            OR r.captured_at<(auth->>'issued_at')::timestamptz OR r.captured_at>=(auth->>'expires_at')::timestamptz
            OR r.captured_at<(b#>>'{facts,observed_at}')::timestamptz
            OR extract(epoch FROM (r.captured_at-(b#>>'{facts,observed_at}')::timestamptz))*1000>p.max_fact_age_ms
            OR b->>'pending_source_hash' IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(raw),'UTF8')),'hex')
            OR r.source_hash IS DISTINCT FROM encode(sha256(convert_to(b::text,'UTF8')),'hex')
            OR NOT life_financial.joint_repair_profile(p_account,b->'payload',b->'facts',v) THEN RETURN false;END IF;
    END LOOP;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.joint_repair_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE r life_financial.joint_repair_facts;g life_financial.joint_repair_approvals;o life_financial.operations;n life_financial.operations;
BEGIN
    IF NOT life_financial.joint_repair_approval_integrity(p_account) THEN RETURN false;END IF;
    IF (SELECT count(*) FROM life_financial.joint_repair_facts WHERE account_id=p_account)>
        (SELECT max_repairs FROM life_financial.joint_repair_policies WHERE account_id=p_account) THEN RETURN false;END IF;
    FOR r IN SELECT * FROM life_financial.joint_repair_facts WHERE account_id=p_account LOOP
        SELECT * INTO STRICT g FROM life_financial.joint_repair_approvals WHERE account_id=p_account AND operation_id=r.operation_id;
        SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=r.operation_id;
        SELECT * INTO STRICT n FROM life_financial.operations WHERE account_id=p_account AND operation_id=r.replacement_operation_id;
        IF o.command->>'kind'<>'JOINT_REPAIR' OR o.command->'command' IS DISTINCT FROM g.body->'payload'
            OR o.command->'binding' IS DISTINCT FROM g.body#>'{approval,binding}'
            OR (o.command->>'version')::bigint<>(g.body#>>'{cut,checkpoint,revision}')::bigint
            OR o.command->>'cut' IS DISTINCT FROM g.body#>>'{cut,checkpoint,state_digest}'
            OR o.fingerprint<>encode(sha256(convert_to(life_financial.canonical_json_v1(o.command),'UTF8')),'hex')
            OR n.command->>'kind'<>'JOINT_BATCH' OR n.command->'command' IS DISTINCT FROM g.body#>'{payload,replacement}'
            OR n.receipt->>'state'<>'APPLIED' OR n.committed_version>=o.committed_version
            OR n.command->'owner' IS DISTINCT FROM o.command->'owner' OR n.command->'binding' IS DISTINCT FROM o.command->'binding'
            OR r.pending_operation_id<>g.pending_operation_id OR r.replacement_operation_id<>g.body#>>'{payload,replacement,operation_id}'
            OR o.receipt IS DISTINCT FROM jsonb_build_object('kind','JOINT_REPAIR','operation_id',r.operation_id,'state','REPAIRED',
                'pending_operation_id',r.pending_operation_id,'replacement_operation_id',r.replacement_operation_id,
                'version',o.committed_version,'replayed',false,'trading_permission',false) THEN RETURN false;END IF;
    END LOOP;
    RETURN NOT EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=p_account AND o.command->>'kind'='JOINT_REPAIR'
        AND NOT EXISTS(SELECT 1 FROM life_financial.joint_repair_facts r WHERE r.account_id=p_account AND r.operation_id=o.operation_id));
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.joint_unresolved_pending(p_account uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT NOT EXISTS(SELECT 1 FROM life_financial.joint_repair_staging WHERE account_id=p_account AND transaction_id=pg_current_xact_id())
        AND EXISTS(SELECT 1 FROM life_financial.joint_financial_batches b WHERE b.account_id=p_account AND b.state='RECOVERY_REQUIRED'
            AND NOT EXISTS(SELECT 1 FROM life_financial.joint_repair_facts r WHERE r.account_id=p_account AND r.pending_operation_id=b.operation_id));
$$;
CREATE OR REPLACE FUNCTION life_financial.joint_financial_pending(p_account uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT EXISTS(SELECT 1 FROM life_financial.spot_pending_observations WHERE account_id=p_account)
    OR EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts WHERE account_id=p_account AND state='RECOVERY_REQUIRED')
    OR EXISTS(SELECT 1 FROM life_financial.swap_pending_fills WHERE account_id=p_account)
    OR EXISTS(SELECT 1 FROM life_financial.funding_observations WHERE account_id=p_account AND state='RECOVERY_REQUIRED')
    OR EXISTS(SELECT 1 FROM life_financial.swap_terminal_facts WHERE account_id=p_account AND state='RECOVERY_REQUIRED')
    OR life_financial.joint_unresolved_pending(p_account);
$$;
CREATE OR REPLACE FUNCTION life_financial.joint_pending_finance_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF NEW.command->>'kind' IN ('SPOT_FILL','SWAP_FILL','SPOT_CASHFLOW','PAID_FUNDING','SHARED_RELEASE')
        AND life_financial.joint_unresolved_pending(NEW.account_id) THEN
        RAISE EXCEPTION 'LIFE_JOINT_PRIOR_PENDING' USING ERRCODE='P0001';END IF;
    -- Every captured member ID stays retired across ALL operation kinds.
    -- Only an unchanged member of this exact signed replacement may use it.
    IF EXISTS(SELECT 1 FROM life_financial.joint_financial_batches b CROSS JOIN LATERAL jsonb_array_elements(life_financial.joint_batch_members(b.command)) m
        WHERE b.account_id=NEW.account_id AND b.state='RECOVERY_REQUIRED' AND m#>>'{command,operation_id}'=NEW.operation_id)
        AND NOT EXISTS(SELECT 1 FROM life_financial.joint_repair_staging s CROSS JOIN LATERAL
            jsonb_array_elements(life_financial.joint_batch_members(s.command)) m WHERE s.account_id=NEW.account_id
            AND s.transaction_id=pg_current_xact_id() AND m->>'kind'=NEW.command->>'kind' AND m->'command'=NEW.command->'command') THEN
        RAISE EXCEPTION 'LIFE_JOINT_MEMBER_ID_RETIRED' USING ERRCODE='P0001';END IF;
    RETURN NEW;
END; $$;

CREATE OR REPLACE FUNCTION life_financial.spot_state(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE b jsonb;verified boolean;pending boolean;
BEGIN
    b:=life_financial.spot_state_before_joint_batch(p_account)-'digest'-'observed_at';
    verified:=life_financial.joint_batch_verified(p_account) AND life_financial.joint_repair_verified(p_account);
    -- A private staged repair changes no financial cut before its child starts.
    -- Raw pending flags remain visible in snapshots until a repair fact exists.
    pending:=EXISTS(SELECT 1 FROM life_financial.joint_financial_batches batch WHERE batch.account_id=p_account AND batch.state='RECOVERY_REQUIRED'
        AND NOT EXISTS(SELECT 1 FROM life_financial.joint_repair_facts r WHERE r.account_id=p_account AND r.pending_operation_id=batch.operation_id));
    b:=b||jsonb_build_object('joint_batches',jsonb_build_object('verified',verified,'pending',pending,
        'policy',(SELECT to_jsonb(p) FROM life_financial.joint_batch_policies p WHERE p.account_id=p_account),
        'batches',COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY operation_id) FROM life_financial.joint_financial_batches r WHERE r.account_id=p_account),'[]'::jsonb)),
        'joint_repairs',jsonb_build_object('policy',(SELECT to_jsonb(p) FROM life_financial.joint_repair_policies p WHERE p.account_id=p_account),
            'repairs',COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY operation_id) FROM life_financial.joint_repair_facts r WHERE r.account_id=p_account),'[]'::jsonb)),
        'financial_recovery_required',(b->>'financial_recovery_required')::boolean OR NOT verified OR pending,
        'recovery_required',(b->>'recovery_required')::boolean OR NOT verified OR pending,'unverified',(b->>'unverified')::boolean OR NOT verified);
    RETURN b||jsonb_build_object('digest',encode(sha256(convert_to(b::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
END; $$;

CREATE FUNCTION life_financial.joint_repair_command(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,cmd jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE a life_financial.accounts;g life_financial.joint_repair_approvals;p life_financial.joint_repair_policies;
    previous life_financial.operations;request jsonb;child_request jsonb;child jsonb;view jsonb;result jsonb;v bigint;at timestamptz;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR jsonb_typeof(cmd) IS DISTINCT FROM 'object' OR octet_length(cmd::text)>1048576 THEN
        RAISE EXCEPTION 'LIFE_JOINT_REPAIR_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
    request:=jsonb_build_object('kind','JOINT_REPAIR','binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',cmd);
    IF p_fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(request),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'LIFE_JOINT_REPAIR_FINGERPRINT_INVALID' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,a.version);
    IF NOT life_financial.joint_repair_verified(p_account) THEN RAISE EXCEPTION 'LIFE_JOINT_REPAIR_UNVERIFIED' USING ERRCODE='P0001';END IF;
    SELECT * INTO previous FROM life_financial.operations WHERE account_id=p_account AND operation_id=cmd->>'operation_id';
    IF FOUND THEN
        IF previous.command<>request OR previous.fingerprint<>p_fingerprint THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001';END IF;
        RETURN previous.receipt||'{"replayed":true}'::jsonb;END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    view:=life_financial.spot_state(p_account);
    IF view->>'digest' IS DISTINCT FROM p_cut OR (view->>'unverified')::boolean THEN RAISE EXCEPTION 'LIFE_JOINT_REPAIR_CUT_STALE' USING ERRCODE='P0001';END IF;
    SELECT * INTO g FROM life_financial.joint_repair_approvals WHERE account_id=p_account AND operation_id=cmd->>'operation_id';
    IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_JOINT_REPAIR_APPROVAL_REQUIRED' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT p FROM life_financial.joint_repair_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
    at:=clock_timestamp();
    IF NOT a.halted OR g.body->'payload' IS DISTINCT FROM cmd OR g.body#>'{approval,binding}' IS DISTINCT FROM p_binding
        OR g.body#>>'{cut,checkpoint,state_digest}' IS DISTINCT FROM p_cut OR (g.body#>>'{cut,checkpoint,revision}')::bigint<>p_version
        OR at<g.captured_at OR at>=(g.body#>>'{approval,expires_at}')::timestamptz OR at<(g.body#>>'{facts,observed_at}')::timestamptz
        OR extract(epoch FROM (at-(g.body#>>'{facts,observed_at}')::timestamptz))*1000>p.max_fact_age_ms
        OR EXISTS(SELECT 1 FROM life_financial.joint_repair_facts WHERE account_id=p_account AND pending_operation_id=cmd->>'pending_operation_id') THEN
        RAISE EXCEPTION 'LIFE_JOINT_REPAIR_APPROVAL_STALE' USING ERRCODE='P0001';END IF;
    IF (SELECT count(*) FROM life_financial.joint_repair_facts WHERE account_id=p_account)>=p.max_repairs THEN
        RAISE EXCEPTION 'LIFE_JOINT_REPAIR_BOUND' USING ERRCODE='P0001';END IF;
    INSERT INTO life_financial.joint_repair_staging VALUES(p_account,pg_current_xact_id(),cmd->'replacement');
    child_request:=jsonb_build_object('kind','JOINT_BATCH','binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',cmd->'replacement');
    child:=life_financial.joint_batch_command(p_account,p_binding,p_owner,p_version,p_cut,
        encode(sha256(convert_to(life_financial.canonical_json_v1(child_request),'UTF8')),'hex'),cmd->'replacement');
    IF child->>'state' IS DISTINCT FROM 'APPLIED' THEN RAISE EXCEPTION 'LIFE_JOINT_REPAIR_REPLACEMENT_REJECTED' USING ERRCODE='P0001';END IF;
    INSERT INTO life_financial.joint_repair_facts VALUES(p_account,cmd->>'operation_id',cmd->>'pending_operation_id',cmd#>>'{replacement,operation_id}',floor(extract(epoch FROM at)*1000)::bigint);
    IF (SELECT COALESCE(sum(octet_length(to_jsonb(r)::text)),0) FROM life_financial.joint_repair_facts r WHERE account_id=p_account)
        +(SELECT COALESCE(sum(octet_length(to_jsonb(g)::text)),0) FROM life_financial.joint_repair_approvals g WHERE account_id=p_account)>p.max_total_bytes THEN
        RAISE EXCEPTION 'LIFE_JOINT_REPAIR_BOUND' USING ERRCODE='P0001';END IF;
    DELETE FROM life_financial.joint_repair_staging WHERE account_id=p_account;
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account RETURNING version INTO v;
    result:=jsonb_build_object('kind','JOINT_REPAIR','operation_id',cmd->>'operation_id','pending_operation_id',cmd->>'pending_operation_id',
        'replacement_operation_id',cmd#>>'{replacement,operation_id}','state','REPAIRED','version',v,'replayed',false,'trading_permission',false);
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version) VALUES(p_account,cmd->>'operation_id',p_fingerprint,request,result,v);
    RETURN result;
END; $$;

CREATE FUNCTION life_financial.joint_repair_read(p_account uuid,p_rows integer,p_bytes integer,p_operation text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE result jsonb;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_ACCESS_DENIED' USING ERRCODE='P0001';END IF;
    IF p_rows IS NULL OR p_bytes IS NULL OR p_rows NOT BETWEEN 1 AND 4096 OR p_bytes NOT BETWEEN 1 AND 16777216
        OR (SELECT count(*) FROM life_financial.joint_repair_facts WHERE account_id=p_account)>p_rows THEN
        RAISE EXCEPTION 'LIFE_JOINT_REPAIR_READ_BOUND' USING ERRCODE='P0001';END IF;
    IF NOT life_financial.joint_repair_verified(p_account) OR NOT life_financial.joint_batch_verified(p_account) THEN
        RAISE EXCEPTION 'LIFE_JOINT_REPAIR_UNVERIFIED' USING ERRCODE='P0001';END IF;
    IF p_operation IS NULL THEN result:=jsonb_build_object('pending',life_financial.joint_unresolved_pending(p_account),
        'repairs',COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY operation_id) FROM life_financial.joint_repair_facts r WHERE account_id=p_account),'[]'::jsonb),'trading_permission',false);
    ELSE SELECT jsonb_build_object('command',o.command,'receipt',o.receipt) INTO result FROM life_financial.operations o
        WHERE o.account_id=p_account AND o.operation_id=p_operation AND o.command->>'kind'='JOINT_REPAIR';END IF;
    IF octet_length(result::text)>p_bytes THEN RAISE EXCEPTION 'LIFE_JOINT_REPAIR_READ_BOUND' USING ERRCODE='P0001';END IF;
    RETURN result;
END; $$;

CREATE FUNCTION life_financial.joint_repair_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE g life_financial.joint_repair_approvals;p life_financial.joint_repair_policies;o life_financial.operations;
    a life_financial.accounts;at timestamptz:=clock_timestamp();
BEGIN
    IF EXISTS(SELECT 1 FROM life_financial.joint_repair_staging WHERE account_id=NEW.account_id)
        OR NOT life_financial.joint_repair_verified(NEW.account_id) THEN RAISE EXCEPTION 'LIFE_JOINT_REPAIR_UNVERIFIED_AT_COMMIT' USING ERRCODE='P0001';END IF;
    IF TG_TABLE_NAME='joint_repair_approvals' THEN g:=NEW;
    ELSIF TG_TABLE_NAME='accounts' THEN
        SELECT * INTO o FROM life_financial.operations WHERE account_id=NEW.account_id AND committed_version=NEW.version;
        IF o.command->>'kind' IS DISTINCT FROM 'JOINT_REPAIR' THEN RETURN NULL;END IF;
        SELECT * INTO STRICT g FROM life_financial.joint_repair_approvals WHERE account_id=NEW.account_id AND operation_id=o.operation_id;
    ELSE RETURN NULL;END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=NEW.account_id;
    SELECT * INTO STRICT p FROM life_financial.joint_repair_policies WHERE account_id=NEW.account_id;
    IF at<g.captured_at OR at>=(g.body#>>'{approval,expires_at}')::timestamptz OR at<(g.body#>>'{facts,observed_at}')::timestamptz
        OR extract(epoch FROM (at-(g.body#>>'{facts,observed_at}')::timestamptz))*1000>p.max_fact_age_ms
        OR NOT a.halted OR a.mode IS DISTINCT FROM g.body#>>'{account,mode}' THEN
        RAISE EXCEPTION 'LIFE_JOINT_REPAIR_APPROVAL_EXPIRED_AT_COMMIT' USING ERRCODE='P0001';END IF;
    IF TG_TABLE_NAME='joint_repair_approvals' THEN
        -- The privileged importer holds this account lock while obtaining the
        -- current cut through the ORIGINAL scoped runtime connection. The owner
        -- gets no runtime access mapping. Preserve every captured account scalar
        -- at physical COMMIT; compare timestamps as instants, not timezone text.
        IF (to_jsonb(a)-ARRAY['owner_since','owner_until']) IS DISTINCT FROM
                ((g.body->'account')-ARRAY['owner_since','owner_until'])
            OR a.owner_since IS DISTINCT FROM (g.body#>>'{account,owner_since}')::timestamptz
            OR a.owner_until IS DISTINCT FROM (g.body#>>'{account,owner_until}')::timestamptz THEN
            RAISE EXCEPTION 'LIFE_JOINT_REPAIR_APPROVAL_CONTEXT_CHANGED' USING ERRCODE='P0001';END IF;
    END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER joint_repair_approval_commit AFTER INSERT ON life_financial.joint_repair_approvals
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.joint_repair_commit_guard();
CREATE CONSTRAINT TRIGGER joint_repair_account_commit AFTER UPDATE ON life_financial.accounts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN(NEW.version<>OLD.version) EXECUTE FUNCTION life_financial.joint_repair_commit_guard();
CREATE CONSTRAINT TRIGGER joint_repair_staging_commit AFTER INSERT ON life_financial.joint_repair_staging
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.joint_repair_commit_guard();

-- Preserve the public function OID/ACL and every preceding commit fence.
DO $$ DECLARE definition text;BEGIN
    definition:=pg_get_functiondef('life_financial.commit_owner_guard()'::regprocedure);
    IF position('IN (''JOINT_BATCH'',' IN definition)=0 THEN RAISE EXCEPTION 'LIFE_JOINT_REPAIR_MIGRATION_GUARD_MISMATCH';END IF;
    EXECUTE replace(definition,'IN (''JOINT_BATCH'',','IN (''JOINT_REPAIR'',''JOINT_BATCH'',');
END; $$;
REVOKE ALL ON life_financial.joint_repair_policies,life_financial.joint_repair_approvals,life_financial.joint_repair_facts,life_financial.joint_repair_staging FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
