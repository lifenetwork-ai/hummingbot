-- Signed reconciliation retires diagnostics, NEVER an order or its claim.
-- Original terminal observations/receipts remain immutable and replayable.
CREATE TABLE life_financial.terminal_repair_policies (
    account_id uuid PRIMARY KEY REFERENCES life_financial.swap_terminal_policies,
    policy_hash life_financial.digest NOT NULL,
    max_approvals integer NOT NULL CHECK(max_approvals BETWEEN 1 AND 4096),
    max_repairs integer NOT NULL CHECK(max_repairs BETWEEN 1 AND 4096),
    max_total_bytes integer NOT NULL CHECK(max_total_bytes BETWEEN 1 AND 16777216),
    max_fact_age_ms integer NOT NULL CHECK(max_fact_age_ms BETWEEN 1 AND 60000),
    FOREIGN KEY(account_id,policy_hash) REFERENCES life_financial.policies
);
CREATE TABLE life_financial.terminal_repair_approvals (
    account_id uuid NOT NULL REFERENCES life_financial.terminal_repair_policies,
    operation_id life_financial.identity NOT NULL,pending_operation_id life_financial.identity NOT NULL,
    body jsonb NOT NULL CHECK(jsonb_typeof(body)='object'),source_hash life_financial.digest NOT NULL,captured_at timestamptz NOT NULL CHECK(isfinite(captured_at)),
    PRIMARY KEY(account_id,operation_id),
    FOREIGN KEY(account_id,pending_operation_id) REFERENCES life_financial.swap_terminal_facts
);
CREATE TABLE life_financial.terminal_repair_facts (
    account_id uuid NOT NULL,operation_id life_financial.identity NOT NULL,
    pending_operation_id life_financial.identity NOT NULL,received_at_ms bigint NOT NULL CHECK(received_at_ms>0),
    PRIMARY KEY(account_id,operation_id),
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.terminal_repair_approvals,
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE life_financial.terminal_repair_members (
    account_id uuid NOT NULL,pending_operation_id life_financial.identity NOT NULL,operation_id life_financial.identity NOT NULL,
    PRIMARY KEY(account_id,pending_operation_id),
    FOREIGN KEY(account_id,pending_operation_id) REFERENCES life_financial.swap_terminal_facts,
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.terminal_repair_facts DEFERRABLE INITIALLY DEFERRED
);
-- Both original-pending lookup and root-membership replay have indexed scope.
CREATE INDEX terminal_repair_members_by_root ON life_financial.terminal_repair_members(account_id,operation_id,pending_operation_id);
DO $$ DECLARE t text;BEGIN
    FOREACH t IN ARRAY ARRAY['terminal_repair_policies','terminal_repair_approvals','terminal_repair_facts','terminal_repair_members'] LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY scoped_read ON life_financial.%I FOR SELECT USING(life_financial.can_access(account_id))',t);
        EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',t);
    END LOOP;
END; $$;

CREATE FUNCTION life_financial.terminal_repair_targets(cmd jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
SELECT jsonb_build_array(cmd->>'pending_operation_id')||(cmd->'additional_pending_operation_ids');
$$;
CREATE FUNCTION life_financial.terminal_repair_sources(p_account uuid,cmd jsonb) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT COALESCE(jsonb_agg(to_jsonb(f) ORDER BY operation_id COLLATE "C"),'[]'::jsonb)
    FROM life_financial.swap_terminal_facts f WHERE account_id=p_account
        AND life_financial.terminal_repair_targets(cmd) @> jsonb_build_array(operation_id);
$$;
-- An unused approval never resolves anything. Historical cuts cannot observe
-- a later resolution, including during verification of the original denial.
CREATE FUNCTION life_financial.terminal_pending_unresolved(p_account uuid,p_operation text,p_version bigint DEFAULT NULL) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT NOT EXISTS(SELECT 1 FROM life_financial.terminal_repair_members m JOIN life_financial.operations o USING(account_id,operation_id)
    WHERE m.account_id=p_account AND m.pending_operation_id=p_operation AND o.command->>'kind'='TERMINAL_REPAIR'
        AND o.receipt->>'state'='RECONCILED' AND (p_version IS NULL OR o.committed_version<=p_version));
$$;

DO $migration$ DECLARE definition text;needle text:=$needle$WHERE f.account_id=p_account AND f.state='RECOVERY_REQUIRED' AND o.committed_version<=p_version) THEN$needle$;BEGIN
    definition:=pg_get_functiondef('life_financial.swap_terminal_reason(uuid,jsonb,bigint,bigint)'::regprocedure);
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_TERMINAL_REPAIR_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,$replacement$WHERE f.account_id=p_account AND f.state='RECOVERY_REQUIRED' AND o.committed_version<=p_version AND life_financial.terminal_pending_unresolved(p_account,f.operation_id,p_version)) THEN$replacement$);
END; $migration$;

CREATE OR REPLACE FUNCTION life_financial.terminal_repair_reason(p_account uuid, cmd jsonb, p_version bigint, at_ms bigint, selected jsonb)
 RETURNS text
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE p life_financial.swap_preparations;t life_financial.send_attempts;nav life_financial.portfolio_observations;
    basis jsonb;ids jsonb;filled numeric;ack_at bigint;
BEGIN
    SELECT * INTO STRICT p FROM life_financial.swap_preparations WHERE account_id=p_account AND operation_id=cmd->>'prepare_operation_id';
    SELECT * INTO STRICT t FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=p.intent_id;
    IF cmd->>'source'<>'okx_joint_reconciled' OR cmd->>'terminal_state' NOT IN ('filled','canceled')
        OR cmd->>'exchange_order_id' IS DISTINCT FROM t.exchange_order_id THEN RETURN 'SWAP_TERMINAL_ORDER_UNQUALIFIED';END IF;
    SELECT floor(extract(epoch FROM o.recorded_at)*1000)::bigint INTO ack_at FROM life_financial.swap_intent_events e JOIN life_financial.operations o USING(account_id,operation_id)
        WHERE e.account_id=p_account AND e.prepare_operation_id=p.operation_id AND e.kind='SWAP_ACK' AND o.committed_version<=p_version;
    IF ack_at IS NULL OR (cmd->>'observed_at_ms')::bigint<greatest(p.prepared_at_ms,ack_at)
        OR (cmd->>'coverage_from_ms')::bigint>p.prepared_at_ms OR (cmd->>'coverage_from_ms')::bigint>(cmd->>'observed_at_ms')::bigint THEN
        RETURN 'SWAP_TERMINAL_COVERAGE_INVALID';END IF;
    SELECT COALESCE(sum((f.observation->>'quantity_base_life')::numeric),0),COALESCE(jsonb_agg(f.trade_id::text ORDER BY f.trade_id COLLATE "C"),'[]'::jsonb)
        INTO filled,ids FROM life_financial.swap_fill_facts f JOIN life_financial.operations o ON o.account_id=f.account_id AND o.operation_id=f.source_operation_id
        WHERE f.account_id=p_account AND f.intent_id=t.intent_id AND o.committed_version<=p_version;
    IF (cmd->>'cumulative_base_life')::numeric<>filled OR filled>t.quantity
        OR cmd->>'terminal_state'='filled' AND filled<>t.quantity OR ids IS DISTINCT FROM cmd->'trade_ids' THEN
        RETURN 'SWAP_TERMINAL_FILLS_INCOMPLETE';END IF;
    basis:=life_financial.portfolio_basis(p_account,p_version);
    IF EXISTS(SELECT 1 FROM jsonb_array_elements((SELECT c.bundle#>'{account,snapshot,pending}' FROM life_financial.carry_checks c
        WHERE c.account_id=p_account AND c.operation_id=basis->>'carry_operation_id')) pending WHERE pending->>'wire_id'=t.wire_id) THEN
        RETURN 'SWAP_TERMINAL_ORDER_STILL_PENDING';END IF;
    SELECT n.* INTO STRICT nav FROM life_financial.portfolio_observations n JOIN life_financial.operations o USING(account_id,operation_id)
        WHERE n.account_id=p_account AND o.committed_version<=p_version ORDER BY o.committed_version DESC LIMIT 1;
    IF nav.basis IS DISTINCT FROM basis OR (cmd->>'observed_at_ms')::bigint<>(basis->>'observed_at_ms')::bigint
        OR NOT life_financial.portfolio_source_qualified_at(p_account,basis,at_ms) THEN RETURN 'SWAP_TERMINAL_FINANCIAL_CUT_UNQUALIFIED';END IF;
    IF EXISTS(SELECT 1 FROM life_financial.joint_financial_batches pending_batch JOIN life_financial.operations pending_operation USING(account_id,operation_id)
            WHERE pending_batch.account_id=p_account AND pending_batch.state='RECOVERY_REQUIRED' AND pending_operation.committed_version<=p_version
                AND life_financial.joint_source_unresolved(p_account,pending_batch.operation_id,p_version,false))
            OR EXISTS(SELECT 1 FROM life_financial.spot_pending_observations f JOIN life_financial.operations o USING(account_id,operation_id)
        WHERE f.account_id=p_account AND o.committed_version<=p_version AND life_financial.joint_source_unresolved(p_account,f.operation_id,p_version,false))
        OR EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts f JOIN life_financial.operations o ON o.account_id=f.account_id AND o.operation_id=f.source_operation_id
            WHERE f.account_id=p_account AND f.state='RECOVERY_REQUIRED' AND o.committed_version<=p_version AND life_financial.joint_source_unresolved(p_account,f.source_operation_id,p_version,false))
        OR EXISTS(SELECT 1 FROM life_financial.swap_pending_fills f JOIN life_financial.operations o USING(account_id,operation_id)
        WHERE f.account_id=p_account AND o.committed_version<=p_version AND life_financial.joint_source_unresolved(p_account,f.operation_id,p_version,false))
        OR EXISTS(SELECT 1 FROM life_financial.funding_observations f JOIN life_financial.operations o USING(account_id,operation_id)
            WHERE f.account_id=p_account AND f.state='RECOVERY_REQUIRED' AND o.committed_version<=p_version AND life_financial.funding_pending_unresolved(p_account,f.operation_id,p_version,false))
        OR EXISTS(SELECT 1 FROM life_financial.swap_terminal_facts f JOIN life_financial.operations o USING(account_id,operation_id)
            WHERE f.account_id=p_account AND f.state='RECOVERY_REQUIRED' AND o.committed_version<=p_version AND life_financial.terminal_pending_unresolved(p_account,f.operation_id,p_version) AND NOT selected @> jsonb_build_array(f.operation_id)) THEN
        RETURN 'SWAP_TERMINAL_PENDING_RECONCILIATION';END IF;
    RETURN 'SWAP_TERMINAL_QUALIFIED';
EXCEPTION WHEN OTHERS THEN RETURN 'SWAP_TERMINAL_SOURCE_INVALID';
END; $function$
;

CREATE FUNCTION life_financial.terminal_repair_profile(p_account uuid,cmd jsonb,facts jsonb,p_version bigint) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE selected jsonb;terminal jsonb;a jsonb;basis jsonb;ids jsonb;expected jsonb;observed_ms bigint;opening_ms bigint;
BEGIN
    IF jsonb_typeof(cmd) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(cmd))<>4
        OR NOT cmd ?& ARRAY['operation_id','pending_operation_id','additional_pending_operation_ids','terminals']
        OR cmd->>'operation_id' !~ '^[A-Za-z0-9_.:-]{1,96}$'
        OR jsonb_typeof(cmd->'additional_pending_operation_ids') IS DISTINCT FROM 'array'
        OR jsonb_array_length(cmd->'additional_pending_operation_ids')>255
        OR jsonb_typeof(cmd->'terminals') IS DISTINCT FROM 'array' OR jsonb_array_length(cmd->'terminals') NOT BETWEEN 1 AND 256 THEN RETURN false;END IF;
    selected:=life_financial.terminal_repair_targets(cmd);a:=facts->'account';
    IF EXISTS(SELECT 1 FROM jsonb_array_elements_text(selected) x WHERE x !~ '^[A-Za-z0-9_.:-]{1,96}$'
        OR NOT EXISTS(SELECT 1 FROM life_financial.swap_terminal_facts f JOIN life_financial.operations o USING(account_id,operation_id)
            WHERE f.account_id=p_account AND f.operation_id=x AND f.state='RECOVERY_REQUIRED' AND o.committed_version<=p_version
                AND life_financial.terminal_pending_unresolved(p_account,x,p_version)))
        OR EXISTS(SELECT 1 FROM (SELECT jsonb_array_elements_text(selected) id UNION ALL SELECT cmd->>'operation_id'
            UNION ALL SELECT t->>'operation_id' FROM jsonb_array_elements(cmd->'terminals') t) x GROUP BY id HAVING id IS NULL OR count(*)>1)
        OR EXISTS(SELECT 1 FROM jsonb_array_elements(cmd->'terminals') t GROUP BY t->>'prepare_operation_id' HAVING count(*)>1)
        OR facts->'binding' IS DISTINCT FROM a->'binding' OR facts->>'observed_at' IS DISTINCT FROM a->>'observed_at'
        OR a->'order_scope_complete' IS DISTINCT FROM 'true'::jsonb OR a->'bill_scope_complete' IS DISTINCT FROM 'true'::jsonb
        OR a->'runner_scope_complete' IS DISTINCT FROM 'true'::jsonb OR facts->'swap_scope_complete' IS DISTINCT FROM 'true'::jsonb
        OR (a->>'liability_base_life')::numeric IS DISTINCT FROM 0::numeric OR (a->>'liability_quote_usdt')::numeric IS DISTINCT FROM 0::numeric THEN RETURN false;END IF;
    basis:=life_financial.portfolio_basis(p_account,p_version);
    observed_ms:=floor(extract(epoch FROM (facts->>'observed_at')::timestamptz)*1000)::bigint;
    SELECT floor(extract(epoch FROM opened_at)*1000)::bigint INTO STRICT opening_ms FROM life_financial.spot_capital_policies WHERE account_id=p_account;
    IF (a->>'account_base_life')::numeric IS DISTINCT FROM (basis#>>'{money,base}')::numeric
        OR (a->>'account_quote_usdt')::numeric IS DISTINCT FROM (basis#>>'{money,quote}')::numeric
        OR observed_ms IS DISTINCT FROM (basis->>'observed_at_ms')::bigint
        OR NOT COALESCE(0<(facts->>'coverage_from_ms')::bigint AND (facts->>'coverage_from_ms')::bigint<=opening_ms
            AND (facts->>'coverage_from_ms')::bigint<=(facts->>'coverage_through_ms')::bigint
            AND (facts->>'coverage_through_ms')::bigint=observed_ms,false) THEN RETURN false;END IF;
    FOR terminal IN SELECT * FROM jsonb_array_elements(cmd->'terminals') LOOP
        IF jsonb_typeof(terminal) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(terminal))<>10
            OR NOT terminal ?& ARRAY['operation_id','prepare_operation_id','exchange_order_id','terminal_state','cumulative_base_life','trade_ids',
                'observed_at_ms','coverage_from_ms','source','product'] OR terminal->>'product' IS DISTINCT FROM 'SWAP'
            OR terminal->>'operation_id' !~ '^[A-Za-z0-9_.:-]{1,96}$'
            OR EXISTS(SELECT 1 FROM unnest(ARRAY['operation_id','prepare_operation_id','exchange_order_id','terminal_state','cumulative_base_life','source']) k
                WHERE jsonb_typeof(terminal->k) IS DISTINCT FROM 'string')
            OR jsonb_typeof(terminal->'trade_ids') IS DISTINCT FROM 'array' OR jsonb_array_length(terminal->'trade_ids')>4096
            OR jsonb_typeof(terminal->'observed_at_ms') IS DISTINCT FROM 'number' OR jsonb_typeof(terminal->'coverage_from_ms') IS DISTINCT FROM 'number'
            OR NOT COALESCE((terminal->>'coverage_from_ms')::bigint BETWEEN 1 AND 253402300799999,false)
            OR NOT EXISTS(SELECT 1 FROM life_financial.swap_terminal_facts f WHERE f.account_id=p_account
                AND selected @> jsonb_build_array(f.operation_id) AND f.command->>'prepare_operation_id'=terminal->>'prepare_operation_id')
            OR life_financial.terminal_repair_reason(p_account,terminal,p_version,observed_ms,selected)<>'SWAP_TERMINAL_QUALIFIED' THEN RETURN false;END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM life_financial.swap_terminal_facts f WHERE f.account_id=p_account AND selected @> jsonb_build_array(f.operation_id)
        AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(cmd->'terminals') t WHERE t->>'prepare_operation_id'=f.command->>'prepare_operation_id')) THEN RETURN false;END IF;
    SELECT COALESCE(jsonb_agg(pair ORDER BY pair::text),'[]') INTO expected FROM (
        SELECT jsonb_build_array('SPOT',f.trade_id) pair FROM life_financial.spot_fill_facts f JOIN life_financial.operations o ON o.account_id=f.account_id AND o.operation_id=f.source_operation_id
            WHERE f.account_id=p_account AND o.committed_version<=p_version
        UNION SELECT jsonb_build_array('SWAP',f.trade_id) FROM life_financial.swap_fill_facts f JOIN life_financial.operations o ON o.account_id=f.account_id AND o.operation_id=f.source_operation_id
            WHERE f.account_id=p_account AND o.committed_version<=p_version) x;
    SELECT COALESCE(jsonb_agg(value ORDER BY value::text),'[]') INTO ids FROM jsonb_array_elements(facts->'product_trade_ids');
    IF ids IS DISTINCT FROM expected THEN RETURN false;END IF;
    SELECT COALESCE(jsonb_agg(id ORDER BY id),'[]') INTO expected FROM (SELECT DISTINCT value->>1 id FROM jsonb_array_elements(ids)) x;
    SELECT COALESCE(jsonb_agg(value ORDER BY value),'[]') INTO ids FROM jsonb_array_elements_text(a->'account_trade_ids');
    IF ids IS DISTINCT FROM expected THEN RETURN false;END IF;
    SELECT COALESCE(jsonb_agg(f.bill_id::text ORDER BY f.bill_id),'[]') INTO expected FROM life_financial.cashflow_effective(p_account) f
        JOIN life_financial.operations o ON o.account_id=f.account_id AND o.operation_id=f.source_operation_id
        WHERE f.state='APPLIED' AND o.committed_version<=p_version;
    SELECT COALESCE(jsonb_agg(value ORDER BY value),'[]') INTO ids FROM jsonb_array_elements_text(a->'account_cashflow_ids');
    IF ids IS DISTINCT FROM expected THEN RETURN false;END IF;
    SELECT COALESCE(jsonb_agg(f.bill_id::text ORDER BY f.bill_id::text),'[]') INTO expected FROM life_financial.funding_cash_payments f
        JOIN life_financial.operations o ON o.account_id=f.account_id AND o.operation_id=f.source_operation_id WHERE f.account_id=p_account AND o.committed_version<=p_version;
    SELECT COALESCE(jsonb_agg(value ORDER BY value),'[]') INTO ids FROM jsonb_array_elements_text(facts->'funding_bill_ids');
    IF ids IS DISTINCT FROM expected THEN RETURN false;END IF;
    SELECT COALESCE(jsonb_agg(wire ORDER BY wire),'[]') INTO expected FROM (
        SELECT DISTINCT o.command#>>'{command,client_order_id}' wire FROM life_financial.operations o
            WHERE o.account_id=p_account AND o.committed_version<=p_version AND o.command->>'kind'='SPOT_ACK'
        UNION SELECT t.wire_id::text FROM life_financial.swap_intent_events e JOIN life_financial.operations o USING(account_id,operation_id)
            JOIN life_financial.swap_preparations p ON p.account_id=e.account_id AND p.operation_id=e.prepare_operation_id
            JOIN life_financial.send_attempts t ON t.account_id=p.account_id AND t.intent_id=p.intent_id
            WHERE e.account_id=p_account AND e.kind='SWAP_ACK' AND o.committed_version<=p_version) x;
    SELECT COALESCE(jsonb_agg(value ORDER BY value),'[]') INTO ids FROM jsonb_array_elements_text(a->'accepted_wire_ids');
    IF ids IS DISTINCT FROM expected OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(a->'runner_wire_ids') wire
        WHERE NOT EXISTS(SELECT 1 FROM life_financial.send_attempts t WHERE t.account_id=p_account AND t.wire_id=wire)) THEN RETURN false;END IF;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.terminal_repair_qualified(p_account uuid,cmd jsonb,p_version bigint,at_ms bigint) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT NOT EXISTS(SELECT 1 FROM jsonb_array_elements(cmd->'terminals') t WHERE
    life_financial.terminal_repair_reason(p_account,t,p_version,at_ms,life_financial.terminal_repair_targets(cmd))<>'SWAP_TERMINAL_QUALIFIED');
$$;

CREATE OR REPLACE FUNCTION life_financial.terminal_repair_approval_integrity(p_account uuid)
 RETURNS boolean
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
#variable_conflict use_column
DECLARE p life_financial.terminal_repair_policies;r life_financial.terminal_repair_approvals;b jsonb;auth jsonb;policy jsonb;raw jsonb;v bigint;
BEGIN
    SELECT * INTO p FROM life_financial.terminal_repair_policies WHERE account_id=p_account;
    IF NOT FOUND THEN RETURN NOT EXISTS(SELECT 1 FROM life_financial.terminal_repair_approvals WHERE account_id=p_account);END IF;
    IF p.policy_hash IS DISTINCT FROM (SELECT policy_hash FROM life_financial.accounts WHERE account_id=p_account)
        OR (SELECT count(*) FROM life_financial.terminal_repair_approvals WHERE account_id=p_account)>p.max_approvals
        OR (SELECT COALESCE(sum(octet_length(to_jsonb(r)::text)),0) FROM life_financial.terminal_repair_approvals r WHERE account_id=p_account)
            +(SELECT COALESCE(sum(octet_length(to_jsonb(r)::text)),0) FROM life_financial.terminal_repair_facts r WHERE account_id=p_account)>p.max_total_bytes THEN RETURN false;END IF;
    FOR r IN SELECT * FROM life_financial.terminal_repair_approvals WHERE account_id=p_account LOOP
        b:=r.body;auth:=b->'approval';v:=(b#>>'{cut,checkpoint,revision}')::bigint;
        SELECT life_financial.terminal_repair_sources(p_account,b->'payload') INTO STRICT raw;
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
            OR NOT life_financial.terminal_repair_profile(p_account,b->'payload',b->'facts',v) THEN RETURN false;END IF;
    END LOOP;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $function$
;

CREATE FUNCTION life_financial.terminal_repair_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE r life_financial.terminal_repair_facts;g life_financial.terminal_repair_approvals;o life_financial.operations;expected jsonb;actual jsonb;
BEGIN
    IF NOT life_financial.terminal_repair_approval_integrity(p_account) THEN RETURN false;END IF;
    IF (SELECT count(*) FROM life_financial.terminal_repair_facts WHERE account_id=p_account)>
        (SELECT max_repairs FROM life_financial.terminal_repair_policies WHERE account_id=p_account) THEN RETURN false;END IF;
    IF (SELECT COALESCE(sum(octet_length(to_jsonb(r)::text)),0) FROM life_financial.terminal_repair_facts r WHERE account_id=p_account)
        +(SELECT COALESCE(sum(octet_length(to_jsonb(g)::text)),0) FROM life_financial.terminal_repair_approvals g WHERE account_id=p_account)
        +(SELECT COALESCE(sum(octet_length(to_jsonb(m)::text)),0) FROM life_financial.terminal_repair_members m WHERE account_id=p_account)>
        (SELECT max_total_bytes FROM life_financial.terminal_repair_policies WHERE account_id=p_account) THEN RETURN false;END IF;
    FOR r IN SELECT * FROM life_financial.terminal_repair_facts WHERE account_id=p_account LOOP
        SELECT * INTO STRICT g FROM life_financial.terminal_repair_approvals WHERE account_id=p_account AND operation_id=r.operation_id;
        SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=r.operation_id;
        SELECT jsonb_agg(x ORDER BY x) INTO expected FROM jsonb_array_elements_text(life_financial.terminal_repair_targets(g.body->'payload')) x;
        SELECT jsonb_agg(pending_operation_id::text ORDER BY pending_operation_id) INTO actual FROM life_financial.terminal_repair_members WHERE account_id=p_account AND operation_id=r.operation_id;
        IF expected IS DISTINCT FROM actual OR r.pending_operation_id<>g.pending_operation_id
            OR o.command->>'kind' IS DISTINCT FROM 'TERMINAL_REPAIR' OR o.command->'command' IS DISTINCT FROM g.body->'payload'
            OR o.command->'binding' IS DISTINCT FROM g.body#>'{approval,binding}'
            OR (o.command->'owner')-ARRAY['valid_from','expires_at'] IS DISTINCT FROM jsonb_build_object(
                'process_id',g.body#>>'{account,owner_process}','generation',g.body#>>'{account,owner_generation}',
                'session_id',g.body#>>'{account,current_session}','epoch',(g.body#>>'{account,owner_epoch}')::bigint)
            OR (o.command#>>'{owner,valid_from}')::timestamptz IS DISTINCT FROM (g.body#>>'{account,owner_since}')::timestamptz
            OR (o.command#>>'{owner,expires_at}')::timestamptz IS DISTINCT FROM (g.body#>>'{account,owner_until}')::timestamptz
            OR (o.command->>'version')::bigint IS DISTINCT FROM (g.body#>>'{cut,checkpoint,revision}')::bigint
            OR o.committed_version<>(o.command->>'version')::bigint+1
            OR o.command->>'cut' IS DISTINCT FROM g.body#>>'{cut,checkpoint,state_digest}'
            OR o.fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(o.command),'UTF8')),'hex')
            OR r.received_at_ms IS DISTINCT FROM floor(extract(epoch FROM o.recorded_at)*1000)::bigint
            OR NOT life_financial.terminal_repair_qualified(p_account,g.body->'payload',o.committed_version-1,r.received_at_ms)
            OR o.receipt IS DISTINCT FROM jsonb_build_object('kind','TERMINAL_REPAIR','operation_id',r.operation_id,
                'pending_operation_id',r.pending_operation_id,'additional_pending_operation_ids',g.body#>'{payload,additional_pending_operation_ids}',
                'state','RECONCILED','claim_released',false,'version',o.committed_version,'replayed',false,'trading_permission',false) THEN RETURN false;END IF;
    END LOOP;
    RETURN NOT EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=p_account AND o.command->>'kind'='TERMINAL_REPAIR'
        AND NOT EXISTS(SELECT 1 FROM life_financial.terminal_repair_facts r WHERE r.account_id=p_account AND r.operation_id=o.operation_id));
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;


CREATE FUNCTION life_financial.terminal_repair_command(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,cmd jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE a life_financial.accounts;g life_financial.terminal_repair_approvals;p life_financial.terminal_repair_policies;
    previous life_financial.operations;request jsonb;view jsonb;result jsonb;v bigint;at timestamptz;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR jsonb_typeof(cmd) IS DISTINCT FROM 'object' OR octet_length(cmd::text)>1048576 THEN
        RAISE EXCEPTION 'LIFE_TERMINAL_REPAIR_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
    request:=jsonb_build_object('kind','TERMINAL_REPAIR','binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',cmd);
    IF p_fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(request),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'LIFE_TERMINAL_REPAIR_FINGERPRINT_INVALID' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,a.version);
    IF NOT life_financial.swap_terminal_verified(p_account) THEN
        RAISE EXCEPTION 'LIFE_TERMINAL_REPAIR_UNVERIFIED' USING ERRCODE='P0001';END IF;
    SELECT * INTO previous FROM life_financial.operations WHERE account_id=p_account AND operation_id=cmd->>'operation_id';
    IF FOUND THEN
        IF previous.command<>request OR previous.fingerprint<>p_fingerprint THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001';END IF;
        RETURN previous.receipt||'{"replayed":true}'::jsonb;END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    view:=life_financial.spot_state(p_account);
    IF view->>'digest' IS DISTINCT FROM p_cut OR (view->>'unverified')::boolean THEN RAISE EXCEPTION 'LIFE_TERMINAL_REPAIR_CUT_STALE' USING ERRCODE='P0001';END IF;
    SELECT * INTO g FROM life_financial.terminal_repair_approvals WHERE account_id=p_account AND operation_id=cmd->>'operation_id';
    IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_TERMINAL_REPAIR_APPROVAL_REQUIRED' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT p FROM life_financial.terminal_repair_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
    at:=clock_timestamp();
    IF NOT a.halted OR g.body->'payload' IS DISTINCT FROM cmd OR g.body#>'{approval,binding}' IS DISTINCT FROM p_binding
        OR g.body#>>'{cut,checkpoint,state_digest}' IS DISTINCT FROM p_cut OR (g.body#>>'{cut,checkpoint,revision}')::bigint<>p_version
        OR at<g.captured_at OR at>=(g.body#>>'{approval,expires_at}')::timestamptz OR at<(g.body#>>'{facts,observed_at}')::timestamptz
        OR extract(epoch FROM (at-(g.body#>>'{facts,observed_at}')::timestamptz))*1000>p.max_fact_age_ms
        OR EXISTS(SELECT 1 FROM life_financial.terminal_repair_members m WHERE m.account_id=p_account
            AND life_financial.terminal_repair_targets(cmd) @> jsonb_build_array(m.pending_operation_id)) THEN
        RAISE EXCEPTION 'LIFE_TERMINAL_REPAIR_APPROVAL_STALE' USING ERRCODE='P0001';END IF;
    IF NOT life_financial.terminal_repair_qualified(p_account,cmd,p_version,floor(extract(epoch FROM at)*1000)::bigint) THEN
        RAISE EXCEPTION 'LIFE_TERMINAL_REPAIR_SOURCE_UNQUALIFIED' USING ERRCODE='P0001';END IF;
    IF (SELECT count(*) FROM life_financial.terminal_repair_facts WHERE account_id=p_account)>=p.max_repairs THEN
        RAISE EXCEPTION 'LIFE_TERMINAL_REPAIR_BOUND' USING ERRCODE='P0001';END IF;
    INSERT INTO life_financial.terminal_repair_facts VALUES(p_account,cmd->>'operation_id',cmd->>'pending_operation_id',floor(extract(epoch FROM at)*1000)::bigint);
    INSERT INTO life_financial.terminal_repair_members SELECT p_account,id,cmd->>'operation_id' FROM jsonb_array_elements_text(life_financial.terminal_repair_targets(cmd)) id;
    IF (SELECT COALESCE(sum(octet_length(to_jsonb(r)::text)),0) FROM life_financial.terminal_repair_facts r WHERE account_id=p_account)
        +(SELECT COALESCE(sum(octet_length(to_jsonb(g)::text)),0) FROM life_financial.terminal_repair_approvals g WHERE account_id=p_account)
        +(SELECT COALESCE(sum(octet_length(to_jsonb(m)::text)),0) FROM life_financial.terminal_repair_members m WHERE account_id=p_account)>p.max_total_bytes THEN
        RAISE EXCEPTION 'LIFE_TERMINAL_REPAIR_BOUND' USING ERRCODE='P0001';END IF;
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account RETURNING version INTO v;
    result:=jsonb_build_object('kind','TERMINAL_REPAIR','operation_id',cmd->>'operation_id','pending_operation_id',cmd->>'pending_operation_id',
        'additional_pending_operation_ids',cmd->'additional_pending_operation_ids','state','RECONCILED','claim_released',false,
        'version',v,'replayed',false,'trading_permission',false);
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version,recorded_at)
        VALUES(p_account,cmd->>'operation_id',p_fingerprint,request,result,v,at);
    RETURN result;
END; $$;

CREATE FUNCTION life_financial.terminal_repair_read(p_account uuid,p_rows integer,p_bytes integer,p_operation text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_SCOPE_DENIED' USING ERRCODE='P0001';END IF;
    IF p_rows IS NULL OR p_rows NOT BETWEEN 1 AND 4096 OR p_bytes IS NULL OR p_bytes NOT BETWEEN 1 AND 16777216
        OR (SELECT count(*) FROM life_financial.terminal_repair_facts WHERE account_id=p_account)>p_rows
        OR (SELECT count(*) FROM life_financial.swap_terminal_facts WHERE account_id=p_account)>p_rows THEN
        RAISE EXCEPTION 'LIFE_TERMINAL_REPAIR_READ_BOUND' USING ERRCODE='P0001';END IF;
    IF NOT life_financial.swap_terminal_verified(p_account)
        OR NOT life_financial.portfolio_verified(p_account) THEN RAISE EXCEPTION 'LIFE_TERMINAL_REPAIR_UNVERIFIED' USING ERRCODE='P0001';END IF;
    IF p_operation IS NULL THEN
        result:=jsonb_build_object('repairs',COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY operation_id) FROM life_financial.terminal_repair_facts r WHERE account_id=p_account),'[]'::jsonb),
            'pending',COALESCE((SELECT jsonb_agg(to_jsonb(f) ORDER BY operation_id) FROM life_financial.swap_terminal_facts f
                WHERE account_id=p_account AND state='RECOVERY_REQUIRED' AND life_financial.terminal_pending_unresolved(p_account,operation_id,NULL)),'[]'::jsonb),
            'trading_permission',false,'claim_released',false);
    ELSE SELECT jsonb_build_object('command',o.command,'receipt',o.receipt) INTO result FROM life_financial.operations o
        WHERE account_id=p_account AND operation_id=p_operation AND o.command->>'kind'='TERMINAL_REPAIR';END IF;
    IF octet_length(result::text)>p_bytes THEN RAISE EXCEPTION 'LIFE_TERMINAL_REPAIR_READ_BOUND' USING ERRCODE='P0001';END IF;
    RETURN result;
END; $$;


CREATE FUNCTION life_financial.terminal_only_recovery(p_account uuid,view jsonb) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT COALESCE(NOT (view->>'unverified')::boolean AND (view#>>'{swap_terminal,pending}')::boolean
    AND NOT (view#>>'{swap_terminal,base_financial_recovery_required}')::boolean
    AND NOT (view#>>'{joint_batches,pending}')::boolean,false);
$$;
DO $$ DECLARE definition text;needle text:=
    'IF (view->>''financial_recovery_required'')::boolean OR (view->>''unverified'')::boolean THEN';BEGIN
    definition:=pg_get_functiondef('life_financial.shared_capital_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'::regprocedure);
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_TERMINAL_REPAIR_CAPITAL_MISMATCH';END IF;
    EXECUTE replace(definition,needle,'IF (view->>''unverified'')::boolean OR ((view->>''financial_recovery_required'')::boolean
        AND NOT (cmd->>''kind''=''OBSERVE'' AND life_financial.terminal_only_recovery(p_account,view))) THEN');
END; $$;


DO $$ DECLARE definition text;needle text:='IF (view->>''unverified'')::boolean OR (view->>''financial_recovery_required'')::boolean THEN';BEGIN
    definition:=pg_get_functiondef('life_financial.portfolio_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'::regprocedure);
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_TERMINAL_REPAIR_NAV_MISMATCH';END IF;
    EXECUTE replace(definition,needle,'IF (view->>''unverified'')::boolean OR ((view->>''financial_recovery_required'')::boolean
        AND NOT life_financial.terminal_only_recovery(p_account,view)) THEN');
END; $$;

DO $migration$ DECLARE definition text;needle text:=$needle$'swap_terminal',jsonb_build_object('verified',verified,'pending',pending,$needle$;BEGIN
    definition:=pg_get_functiondef('life_financial.spot_state_before_shared_release(uuid)'::regprocedure);
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_TERMINAL_REPAIR_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,$replacement$'swap_terminal',jsonb_build_object('verified',verified,'pending',pending,'base_financial_recovery_required',(b->>'financial_recovery_required')::boolean,$replacement$);
END; $migration$;

DO $migration$ DECLARE definition text;needle text:=$needle$WHERE account_id=p_account AND state='RECOVERY_REQUIRED');$needle$;BEGIN
    definition:=pg_get_functiondef('life_financial.spot_state_before_shared_release(uuid)'::regprocedure);
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_TERMINAL_REPAIR_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,$replacement$WHERE account_id=p_account AND state='RECOVERY_REQUIRED' AND life_financial.terminal_pending_unresolved(p_account,operation_id,NULL));$replacement$);
END; $migration$;

DO $migration$ DECLARE definition text;needle text:=$needle$FROM life_financial.swap_terminal_facts WHERE account_id=p_account AND state='RECOVERY_REQUIRED')$needle$;BEGIN
    definition:=pg_get_functiondef('life_financial.joint_financial_pending(uuid)'::regprocedure);
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_TERMINAL_REPAIR_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,$replacement$FROM life_financial.swap_terminal_facts WHERE account_id=p_account AND state='RECOVERY_REQUIRED' AND life_financial.terminal_pending_unresolved(p_account,operation_id,NULL))$replacement$);
END; $migration$;

DO $migration$ DECLARE definition text;needle text:=$needle$WHERE f.account_id=p_account AND f.state='RECOVERY_REQUIRED' AND op.committed_version<=p_version) THEN$needle$;BEGIN
    definition:=pg_get_functiondef('life_financial.shared_release_proof(uuid,text,text,bigint,bigint)'::regprocedure);
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_TERMINAL_REPAIR_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,$replacement$WHERE f.account_id=p_account AND f.state='RECOVERY_REQUIRED' AND op.committed_version<=p_version AND life_financial.terminal_pending_unresolved(p_account,f.operation_id,p_version)) THEN$replacement$);
END; $migration$;


DO $$ DECLARE definition text;BEGIN
    definition:=pg_get_functiondef('life_financial.swap_terminal_verified(uuid)'::regprocedure);
    IF position('IF NOT life_financial.terminal_repair_verified' IN definition)>0 THEN RAISE EXCEPTION 'LIFE_TERMINAL_REPAIR_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,'BEGIN','BEGIN
    IF NOT life_financial.terminal_repair_verified(p_account) THEN RETURN false;END IF;');
END; $$;
DO $$ DECLARE definition text;BEGIN
    definition:=pg_get_functiondef('life_financial.spot_state(uuid)'::regprocedure);
    EXECUTE replace(definition,'life_financial.spot_state(', 'life_financial.spot_state_before_terminal_repair(');
END; $$;
CREATE OR REPLACE FUNCTION life_financial.spot_state(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b jsonb;verified boolean;
BEGIN
    b:=life_financial.spot_state_before_terminal_repair(p_account)-'digest'-'observed_at';
    verified:=(b#>>'{swap_terminal,verified}')::boolean;
    b:=b||jsonb_build_object('terminal_repairs',jsonb_build_object('verified',verified,
        'policy',(SELECT to_jsonb(p) FROM life_financial.terminal_repair_policies p WHERE account_id=p_account),
        'repairs',COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY operation_id) FROM life_financial.terminal_repair_facts r WHERE account_id=p_account),'[]'::jsonb)),
        'unverified',(b->>'unverified')::boolean OR NOT verified,'recovery_required',(b->>'recovery_required')::boolean OR NOT verified,
        'financial_recovery_required',(b->>'financial_recovery_required')::boolean OR NOT verified);
    RETURN b||jsonb_build_object('digest',encode(sha256(convert_to(b::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
END; $$;

CREATE FUNCTION life_financial.terminal_repair_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE g life_financial.terminal_repair_approvals;p life_financial.terminal_repair_policies;o life_financial.operations;
    a life_financial.accounts;at timestamptz:=clock_timestamp();
BEGIN
    IF TG_TABLE_NAME='operations' THEN
        IF NEW.command->>'kind' IS DISTINCT FROM 'TERMINAL_REPAIR' THEN RETURN NULL;END IF;
    END IF;
    IF NOT life_financial.terminal_repair_verified(NEW.account_id) THEN
        RAISE EXCEPTION 'LIFE_TERMINAL_REPAIR_UNVERIFIED_AT_COMMIT' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT g FROM life_financial.terminal_repair_approvals WHERE account_id=NEW.account_id AND operation_id=NEW.operation_id;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=NEW.account_id;
    SELECT * INTO STRICT p FROM life_financial.terminal_repair_policies WHERE account_id=NEW.account_id;
    IF at<g.captured_at OR at>=(g.body#>>'{approval,expires_at}')::timestamptz OR at<(g.body#>>'{facts,observed_at}')::timestamptz
        OR extract(epoch FROM (at-(g.body#>>'{facts,observed_at}')::timestamptz))*1000>p.max_fact_age_ms
        OR NOT a.halted OR a.mode IS DISTINCT FROM g.body#>>'{account,mode}' THEN
        RAISE EXCEPTION 'LIFE_TERMINAL_REPAIR_APPROVAL_EXPIRED_AT_COMMIT' USING ERRCODE='P0001';END IF;
    IF NOT life_financial.terminal_repair_qualified(NEW.account_id,g.body->'payload',(g.body#>>'{cut,checkpoint,revision}')::bigint,
        floor(extract(epoch FROM at)*1000)::bigint) THEN RAISE EXCEPTION 'LIFE_TERMINAL_REPAIR_SOURCE_STALE_AT_COMMIT' USING ERRCODE='P0001';END IF;
    IF TG_TABLE_NAME='terminal_repair_approvals' THEN
        IF (to_jsonb(a)-ARRAY['owner_since','owner_until']) IS DISTINCT FROM ((g.body->'account')-ARRAY['owner_since','owner_until'])
            OR a.owner_since IS DISTINCT FROM (g.body#>>'{account,owner_since}')::timestamptz
            OR a.owner_until IS DISTINCT FROM (g.body#>>'{account,owner_until}')::timestamptz THEN
            RAISE EXCEPTION 'LIFE_TERMINAL_REPAIR_APPROVAL_CONTEXT_CHANGED' USING ERRCODE='P0001';END IF;
    END IF;
    RETURN NULL;
END; $$;
-- Run added expensive proofs only for the affected approval/root insert.
CREATE CONSTRAINT TRIGGER terminal_repair_approval_commit AFTER INSERT ON life_financial.terminal_repair_approvals
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.terminal_repair_commit_guard();
CREATE CONSTRAINT TRIGGER terminal_repair_operation_commit AFTER INSERT ON life_financial.operations
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN(NEW.command->>'kind'='TERMINAL_REPAIR') EXECUTE FUNCTION life_financial.terminal_repair_commit_guard();
DO $$ DECLARE definition text;needle text:='IN (''HEDGE_PLAN'',';BEGIN
    definition:=pg_get_functiondef('life_financial.commit_owner_guard()'::regprocedure);
    IF position(needle IN definition)=0 THEN RAISE EXCEPTION 'LIFE_TERMINAL_REPAIR_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,'IN (''TERMINAL_REPAIR'',''HEDGE_PLAN'',');
END; $$;
REVOKE ALL ON life_financial.terminal_repair_policies,life_financial.terminal_repair_approvals,
    life_financial.terminal_repair_facts,life_financial.terminal_repair_members FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
