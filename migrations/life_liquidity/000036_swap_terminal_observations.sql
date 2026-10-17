-- Native SWAP terminal bookkeeping. Claims remain held: this is not a shared
-- capital release, qualified joint settlement or configured provider source.
CREATE TABLE life_financial.swap_terminal_policies (
    account_id uuid PRIMARY KEY REFERENCES life_financial.portfolio_policies,
    policy_hash life_financial.digest NOT NULL,
    max_observations integer NOT NULL CHECK(max_observations BETWEEN 1 AND 4096),
    max_total_bytes integer NOT NULL CHECK(max_total_bytes BETWEEN 1 AND 16777216),
    FOREIGN KEY(account_id,policy_hash) REFERENCES life_financial.policies
);
CREATE TABLE life_financial.swap_terminal_facts (
    account_id uuid NOT NULL REFERENCES life_financial.swap_terminal_policies,operation_id life_financial.identity NOT NULL,
    intent_id life_financial.identity NOT NULL,command jsonb NOT NULL,received_at_ms bigint NOT NULL CHECK(received_at_ms>0),
    state text NOT NULL CHECK(state IN ('TERMINAL','RECOVERY_REQUIRED')),reason life_financial.identity,
    PRIMARY KEY(account_id,operation_id),FOREIGN KEY(account_id,intent_id) REFERENCES life_financial.send_attempts,
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED,
    CHECK(state='TERMINAL' AND reason IS NULL OR state='RECOVERY_REQUIRED' AND reason IS NOT NULL)
);
CREATE UNIQUE INDEX swap_terminal_once ON life_financial.swap_terminal_facts(account_id,intent_id) WHERE state='TERMINAL';
CREATE INDEX swap_terminal_operation_history ON life_financial.operations(account_id,committed_version) WHERE command->>'kind'='SWAP_TERMINAL';
DO $$ DECLARE t text;BEGIN
    FOREACH t IN ARRAY ARRAY['swap_terminal_policies','swap_terminal_facts'] LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY scoped_read ON life_financial.%I FOR SELECT USING(life_financial.can_access(account_id))',t);
        EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',t);
    END LOOP;
END; $$;

CREATE FUNCTION life_financial.swap_terminal_reason(p_account uuid,cmd jsonb,p_version bigint,at_ms bigint) RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
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
    IF EXISTS(SELECT 1 FROM life_financial.spot_pending_observations f JOIN life_financial.operations o USING(account_id,operation_id)
        WHERE f.account_id=p_account AND o.committed_version<=p_version)
        OR EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts f JOIN life_financial.operations o ON o.account_id=f.account_id AND o.operation_id=f.source_operation_id
            WHERE f.account_id=p_account AND f.state='RECOVERY_REQUIRED' AND o.committed_version<=p_version)
        OR EXISTS(SELECT 1 FROM life_financial.swap_pending_fills f JOIN life_financial.operations o USING(account_id,operation_id)
        WHERE f.account_id=p_account AND o.committed_version<=p_version)
        OR EXISTS(SELECT 1 FROM life_financial.funding_observations f JOIN life_financial.operations o USING(account_id,operation_id)
            WHERE f.account_id=p_account AND f.state='RECOVERY_REQUIRED' AND o.committed_version<=p_version)
        OR EXISTS(SELECT 1 FROM life_financial.swap_terminal_facts f JOIN life_financial.operations o USING(account_id,operation_id)
            WHERE f.account_id=p_account AND f.state='RECOVERY_REQUIRED' AND o.committed_version<=p_version) THEN
        RETURN 'SWAP_TERMINAL_PENDING_RECONCILIATION';END IF;
    RETURN 'SWAP_TERMINAL_QUALIFIED';
EXCEPTION WHEN OTHERS THEN RETURN 'SWAP_TERMINAL_SOURCE_INVALID';
END; $$;

CREATE FUNCTION life_financial.swap_terminal_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.swap_terminal_policies;f life_financial.swap_terminal_facts;op life_financial.operations;reason text;expected jsonb;
BEGIN
    SELECT * INTO p FROM life_financial.swap_terminal_policies WHERE account_id=p_account;
    IF NOT FOUND THEN RETURN NOT EXISTS(SELECT 1 FROM life_financial.swap_terminal_facts WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=p_account AND command->>'kind'='SWAP_TERMINAL');END IF;
    IF p.policy_hash IS DISTINCT FROM (SELECT policy_hash FROM life_financial.accounts WHERE account_id=p_account)
        OR (SELECT count(*) FROM life_financial.swap_terminal_facts WHERE account_id=p_account)>p.max_observations
        OR (SELECT COALESCE(sum(octet_length(command::text)),0) FROM life_financial.swap_terminal_facts WHERE account_id=p_account)>p.max_total_bytes THEN RETURN false;END IF;
    FOR f IN SELECT * FROM life_financial.swap_terminal_facts WHERE account_id=p_account LOOP
        SELECT * INTO STRICT op FROM life_financial.operations WHERE account_id=p_account AND operation_id=f.operation_id;
        reason:=life_financial.swap_terminal_reason(p_account,f.command,op.committed_version-1,f.received_at_ms);
        IF op.command->>'kind' IS DISTINCT FROM 'SWAP_TERMINAL' OR op.command->'command' IS DISTINCT FROM f.command
            OR op.command#>>'{command,operation_id}' IS DISTINCT FROM f.operation_id
            OR (op.command->>'version')::bigint<>op.committed_version-1
            OR op.command->'owner' IS DISTINCT FROM (SELECT original.command->'owner' FROM life_financial.operations original
                WHERE original.account_id=p_account AND original.operation_id=f.command->>'prepare_operation_id')
            OR op.command->'binding' IS DISTINCT FROM (SELECT original.command->'binding' FROM life_financial.operations original
                WHERE original.account_id=p_account AND original.operation_id=f.command->>'prepare_operation_id')
            OR f.received_at_ms<>floor(extract(epoch FROM op.recorded_at)*1000)::bigint
            OR op.fingerprint<>encode(sha256(convert_to(life_financial.canonical_json_v1(op.command),'UTF8')),'hex')
            OR NOT EXISTS(SELECT 1 FROM life_financial.swap_preparations d WHERE d.account_id=p_account AND d.intent_id=f.intent_id
                AND d.operation_id=f.command->>'prepare_operation_id')
            OR (f.state='TERMINAL') IS DISTINCT FROM (reason='SWAP_TERMINAL_QUALIFIED')
            OR f.reason IS DISTINCT FROM (CASE WHEN reason='SWAP_TERMINAL_QUALIFIED' THEN NULL ELSE reason END) THEN RETURN false;END IF;
        expected:=jsonb_build_object('kind','SWAP_TERMINAL','operation_id',f.operation_id,'intent_id',f.intent_id,'state',f.state,
            'claim_released',false,'version',op.committed_version,'replayed',false,'trading_permission',false);
        IF f.reason IS NOT NULL THEN expected:=expected||jsonb_build_object('reason',f.reason);END IF;
        IF expected IS DISTINCT FROM op.receipt THEN RETURN false;END IF;
    END LOOP;
    RETURN NOT EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=p_account AND o.command->>'kind'='SWAP_TERMINAL'
        AND NOT EXISTS(SELECT 1 FROM life_financial.swap_terminal_facts fact WHERE fact.account_id=o.account_id AND fact.operation_id=o.operation_id));
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.swap_terminal_command(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,cmd jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts;policy_row life_financial.swap_terminal_policies;d life_financial.swap_preparations;t life_financial.send_attempts;
    old life_financial.operations;request jsonb;result jsonb;view jsonb;op text;reason text;result_state text;at timestamptz;at_ms bigint;k text;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR jsonb_typeof(cmd) IS DISTINCT FROM 'object' OR octet_length(cmd::text)>1048576
        OR (SELECT count(*) FROM jsonb_object_keys(cmd))<>10 OR NOT cmd ?& ARRAY['operation_id','prepare_operation_id','exchange_order_id','terminal_state',
            'cumulative_base_life','trade_ids','observed_at_ms','coverage_from_ms','source','product'] OR cmd->>'product'<>'SWAP' THEN
        RAISE EXCEPTION 'LIFE_SWAP_TERMINAL_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
    FOREACH k IN ARRAY ARRAY['operation_id','prepare_operation_id','exchange_order_id','terminal_state','cumulative_base_life','source','product'] LOOP
        IF jsonb_typeof(cmd->k) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'LIFE_SWAP_TERMINAL_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
    END LOOP;
    FOREACH k IN ARRAY ARRAY['operation_id','prepare_operation_id'] LOOP
        PERFORM (cmd->>k)::life_financial.identity;END LOOP;
    FOREACH k IN ARRAY ARRAY['observed_at_ms','coverage_from_ms'] LOOP
        IF jsonb_typeof(cmd->k) IS DISTINCT FROM 'number' OR cmd->>k !~ '^[1-9][0-9]*$' OR (cmd->>k)::numeric>253402300799999 THEN
            RAISE EXCEPTION 'LIFE_SWAP_TERMINAL_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
    END LOOP;
    PERFORM (cmd->>'cumulative_base_life')::life_financial.amount;
    IF jsonb_typeof(cmd->'trade_ids') IS DISTINCT FROM 'array' OR jsonb_array_length(cmd->'trade_ids')>4096
        OR EXISTS(SELECT 1 FROM jsonb_array_elements(cmd->'trade_ids') v WHERE jsonb_typeof(v) IS DISTINCT FROM 'string' OR v#>>'{}' !~ '^[A-Za-z0-9_.:-]{1,96}$') THEN
        RAISE EXCEPTION 'LIFE_SWAP_TERMINAL_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
    op:=cmd->>'operation_id';request:=jsonb_build_object('kind','SWAP_TERMINAL','binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',cmd);
    IF p_fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(request),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'LIFE_SWAP_TERMINAL_FINGERPRINT_INVALID' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,a.version);
    IF NOT life_financial.swap_terminal_verified(p_account) OR NOT life_financial.swap_verified(p_account) THEN
        RAISE EXCEPTION 'LIFE_SWAP_TERMINAL_UNVERIFIED' USING ERRCODE='P0001';END IF;
    SELECT * INTO old FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF old.command<>request OR old.fingerprint<>p_fingerprint THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001';END IF;
        RETURN old.receipt||'{"replayed":true}'::jsonb;END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    view:=life_financial.spot_state(p_account);
    IF view->>'digest' IS DISTINCT FROM p_cut THEN RAISE EXCEPTION 'LIFE_SPOT_CUT_CONFLICT' USING ERRCODE='P0001';END IF;
    IF (view->>'unverified')::boolean THEN RAISE EXCEPTION 'LIFE_SWAP_TERMINAL_UNVERIFIED' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT policy_row FROM life_financial.swap_terminal_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
    IF (SELECT count(*) FROM life_financial.swap_terminal_facts WHERE account_id=p_account)>=policy_row.max_observations
        OR (SELECT COALESCE(sum(octet_length(command::text)),0) FROM life_financial.swap_terminal_facts WHERE account_id=p_account)+octet_length(cmd::text)>policy_row.max_total_bytes THEN
        RAISE EXCEPTION 'LIFE_SWAP_TERMINAL_OBSERVATION_BOUND' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT d FROM life_financial.swap_preparations WHERE account_id=p_account AND operation_id=cmd->>'prepare_operation_id';
    SELECT * INTO STRICT t FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=d.intent_id;
    IF t.state<>'ACKED' OR (SELECT command->'owner' FROM life_financial.operations WHERE account_id=p_account AND operation_id=d.operation_id) IS DISTINCT FROM p_owner THEN
        RAISE EXCEPTION 'LIFE_SWAP_TERMINAL_ORIGINAL_ORDER_REQUIRED' USING ERRCODE='P0001';END IF;
    at:=clock_timestamp();at_ms:=floor(extract(epoch FROM at)*1000)::bigint;
    reason:=life_financial.swap_terminal_reason(p_account,cmd,p_version,at_ms);
    result_state:=CASE WHEN reason='SWAP_TERMINAL_QUALIFIED' THEN 'TERMINAL' ELSE 'RECOVERY_REQUIRED' END;
    IF result_state='TERMINAL' THEN
        UPDATE life_financial.send_attempts SET state='TERMINAL',version=version+1 WHERE account_id=p_account AND intent_id=t.intent_id;
        reason:=NULL;
    ELSE UPDATE life_financial.accounts SET halted=true WHERE account_id=p_account;END IF;
    INSERT INTO life_financial.swap_terminal_facts VALUES(p_account,op,t.intent_id,cmd,at_ms,result_state,reason);
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    result:=jsonb_build_object('kind','SWAP_TERMINAL','operation_id',op,'intent_id',t.intent_id,'state',result_state,'claim_released',false,
        'version',p_version+1,'replayed',false,'trading_permission',false);
    IF reason IS NOT NULL THEN result:=result||jsonb_build_object('reason',reason);END IF;
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version,recorded_at)
        VALUES(p_account,op,p_fingerprint,request,result,p_version+1,at);
    RETURN result;
END; $$;

CREATE OR REPLACE FUNCTION life_financial.swap_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.swap_policies;cp life_financial.shared_capital_policies;d life_financial.swap_preparations;
    claim life_financial.shared_capital_claims;t life_financial.send_attempts;o life_financial.operations;e life_financial.swap_intent_events;
    c jsonb;payload jsonb;permit jsonb;expected text;last_state text;acks integer;arms integer;terminals integer;
BEGIN
    SELECT * INTO p FROM life_financial.swap_policies WHERE account_id=p_account;
    IF p.account_id IS NULL THEN RETURN NOT EXISTS(SELECT 1 FROM life_financial.swap_preparations WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.swap_intent_events WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.send_attempts WHERE account_id=p_account AND product='SWAP')
        AND NOT EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=p_account AND command->>'kind' IN ('SWAP_PREPARE','SWAP_ARM','SWAP_ACK','SWAP_TERMINAL')); END IF;
    SELECT * INTO STRICT cp FROM life_financial.shared_capital_policies WHERE account_id=p_account;
    IF NOT life_financial.swap_policy_valid(p.body) OR p.model_hash<>encode(sha256(convert_to(life_financial.canonical_json_v1(p.body),'UTF8')),'hex')
        OR p.policy_hash<>cp.policy_hash OR NOT EXISTS(SELECT 1 FROM life_financial.accounts a WHERE a.account_id=p_account AND a.policy_hash=p.policy_hash) THEN RETURN false; END IF;
    FOR d IN SELECT * FROM life_financial.swap_preparations WHERE account_id=p_account LOOP
        SELECT * INTO STRICT t FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=d.intent_id;
        SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=d.operation_id;
        SELECT * INTO STRICT claim FROM life_financial.shared_capital_claims WHERE account_id=p_account AND intent_id=d.intent_id;
        c:=d.request->'config';permit:=life_financial.swap_permit(c,claim,cp.body);
        IF NOT life_financial.swap_config_valid(c,p.body,cp.body,d.prepared_at_ms)
            OR d.permit IS DISTINCT FROM permit OR d.quote_level<>(c->>'level_id')::integer
            OR (t.product,t.intent_id,t.wire_id,t.side,t.quantity,t.price,t.session_id,t.epoch)
                IS DISTINCT FROM ('SWAP'::text,claim.intent_id,claim.wire_id,claim.side,claim.quantity_base,claim.price_usdt,claim.session_id,claim.epoch)
            OR claim.product<>'SWAP' OR claim.side<>(CASE c->>'side' WHEN '1' THEN 'BUY' ELSE 'SELL' END)
            OR claim.position_action<>c->>'position_action' OR claim.quantity_base<>(c->>'amount')::numeric OR claim.price_usdt<>(c->>'price')::numeric
            OR claim.leverage<>(c->>'leverage')::integer OR d.request->>'operation_id'<>d.operation_id OR d.request->>'client_order_id'<>t.wire_id
            OR t.operation_id<>d.operation_id OR o.command->>'kind' IS DISTINCT FROM 'SWAP_PREPARE' OR o.command->'command' IS DISTINCT FROM d.request
            OR o.command#>>'{owner,process_id}' IS DISTINCT FROM t.process_id::text OR o.command#>>'{owner,generation}' IS DISTINCT FROM t.generation::text
            OR o.fingerprint<>encode(sha256(convert_to(life_financial.canonical_json_v1(o.command),'UTF8')),'hex')
            OR o.receipt IS DISTINCT FROM jsonb_build_object('kind','SWAP_PREPARE','operation_id',d.operation_id,'intent_id',t.intent_id,
                'state','PREPARED','permit',permit,'version',o.committed_version,'prepared_at_ms',d.prepared_at_ms,'replayed',false,'trading_permission',false) THEN RETURN false; END IF;
        payload:=jsonb_build_object('command',d.request,'config_version',claim.config_version,'risk_epoch',claim.risk_epoch);
        IF t.payload<>convert_to(life_financial.canonical_json_v1(payload),'UTF8') OR t.payload_hash<>encode(sha256(t.payload),'hex') THEN RETURN false; END IF;
        SELECT count(*) FILTER(WHERE kind='SWAP_ACK'),count(*) FILTER(WHERE kind='SWAP_ARM') INTO acks,arms
            FROM life_financial.swap_intent_events WHERE account_id=p_account AND prepare_operation_id=d.operation_id;
        SELECT count(*) INTO terminals FROM life_financial.swap_terminal_facts WHERE account_id=p_account AND intent_id=d.intent_id AND state='TERMINAL';
        last_state:=CASE WHEN terminals=1 THEN 'TERMINAL' WHEN acks=1 THEN 'ACKED' WHEN arms=1 THEN 'SEND_UNKNOWN' ELSE 'PREPARED' END;
        IF t.state<>last_state OR t.version<>acks+2*arms+terminals OR acks>arms OR terminals>acks OR t.exchange_order_id IS DISTINCT FROM (
            SELECT exchange_order_id FROM life_financial.swap_intent_events WHERE account_id=p_account AND prepare_operation_id=d.operation_id AND kind='SWAP_ACK') THEN RETURN false; END IF;
    END LOOP;
    FOR e IN SELECT * FROM life_financial.swap_intent_events WHERE account_id=p_account LOOP
        SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=e.operation_id;
        SELECT * INTO STRICT d FROM life_financial.swap_preparations WHERE account_id=p_account AND operation_id=e.prepare_operation_id;
        expected:=CASE WHEN e.kind='SWAP_ARM' THEN 'SEND_UNKNOWN' ELSE 'ACKED' END;
        IF o.command->>'kind' IS DISTINCT FROM e.kind OR o.command#>>'{command,prepare_operation_id}' IS DISTINCT FROM e.prepare_operation_id
            OR o.command#>>'{command,operation_id}' IS DISTINCT FROM e.operation_id
            OR o.command#>>'{command,exchange_order_id}' IS DISTINCT FROM e.exchange_order_id
            OR o.fingerprint<>encode(sha256(convert_to(life_financial.canonical_json_v1(o.command),'UTF8')),'hex')
            OR o.receipt IS DISTINCT FROM jsonb_build_object('kind',e.kind,'operation_id',d.operation_id,'intent_id',d.intent_id,
                'state',expected,'permit',d.permit,'version',o.committed_version,'replayed',false,'trading_permission',false)
            OR o.committed_version<=(SELECT committed_version FROM life_financial.operations WHERE account_id=p_account AND operation_id=d.operation_id)
            OR e.kind='SWAP_ACK' AND o.committed_version<=(SELECT ar.committed_version FROM life_financial.operations ar JOIN life_financial.swap_intent_events ae USING(account_id,operation_id)
                WHERE ae.account_id=p_account AND ae.prepare_operation_id=d.operation_id AND ae.kind='SWAP_ARM') THEN RETURN false; END IF;
    END LOOP;
    RETURN life_financial.swap_terminal_verified(p_account) AND NOT EXISTS(SELECT 1 FROM life_financial.send_attempts native WHERE native.account_id=p_account AND native.product='SWAP' AND NOT EXISTS(
        SELECT 1 FROM life_financial.swap_preparations prep WHERE prep.account_id=p_account AND prep.intent_id=native.intent_id))
        AND NOT EXISTS(SELECT 1 FROM life_financial.operations op WHERE op.account_id=p_account AND (
            op.command->>'kind'='SWAP_PREPARE' AND NOT EXISTS(SELECT 1 FROM life_financial.swap_preparations prep WHERE prep.account_id=p_account AND prep.operation_id=op.operation_id)
            OR op.command->>'kind' IN ('SWAP_ARM','SWAP_ACK') AND NOT EXISTS(SELECT 1 FROM life_financial.swap_intent_events event WHERE event.account_id=p_account AND event.operation_id=op.operation_id)));
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
CREATE OR REPLACE FUNCTION life_financial.swap_accounting_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE p life_financial.swap_fill_policies;f life_financial.swap_fill_facts;t life_financial.send_attempts;
    d life_financial.swap_preparations;o life_financial.operations;g life_financial.swap_fill_progress;r jsonb;
    loss numeric;observed_ms bigint;expected jsonb;sc text;window_key_value text;at timestamptz;policy life_financial.spot_policies;
BEGIN
    SELECT * INTO p FROM life_financial.swap_fill_policies WHERE account_id=p_account;
    IF NOT FOUND THEN RETURN NOT EXISTS(SELECT 1 FROM life_financial.swap_fill_facts WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.swap_fill_progress WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.swap_pending_fills WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.fills WHERE account_id=p_account AND product='SWAP')
        AND NOT EXISTS(SELECT 1 FROM life_financial.fees WHERE account_id=p_account AND product='SWAP')
        AND NOT EXISTS(SELECT 1 FROM life_financial.financial_events WHERE account_id=p_account AND product='SWAP');END IF;
    IF p.policy_hash<>(SELECT policy_hash FROM life_financial.accounts WHERE account_id=p_account) THEN RETURN false;END IF;
    IF (SELECT count(*) FROM life_financial.operations WHERE account_id=p_account AND command->>'kind'='SWAP_FILL')>p.max_observations THEN RETURN false;END IF;
    r:=life_financial.swap_fill_replay(p_account);
    SELECT * INTO STRICT policy FROM life_financial.spot_policies WHERE account_id=p_account AND policy_hash=p.policy_hash;
    FOR f IN SELECT * FROM life_financial.swap_fill_facts WHERE account_id=p_account LOOP
        SELECT * INTO STRICT t FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=f.intent_id;
        SELECT * INTO STRICT d FROM life_financial.swap_preparations WHERE account_id=p_account AND intent_id=f.intent_id;
        SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=f.source_operation_id;
        observed_ms:=floor(extract(epoch FROM (o.command#>>'{command,observed_at}')::timestamptz)*1000)::bigint;
        IF t.product<>'SWAP' OR t.state NOT IN ('SEND_UNKNOWN','ACKED','TERMINAL') OR t.exchange_order_id IS NOT NULL AND t.exchange_order_id<>f.exchange_order_id
            OR EXISTS(SELECT 1 FROM life_financial.send_attempts other WHERE other.account_id=p_account AND other.product='SWAP'
                AND other.exchange_order_id=f.exchange_order_id AND other.intent_id<>f.intent_id)
            OR NOT life_financial.swap_fill_valid(f.observation,p,d,t,observed_ms)
            OR f.trade_id<>f.observation->>'trade_id' OR f.source_hash<>encode(sha256(convert_to(life_financial.canonical_json_v1(f.observation),'UTF8')),'hex')
            OR o.command->>'kind'<>'SWAP_FILL' OR o.receipt->>'state'<>'APPLIED'
            OR o.command#>>'{command,intent_id}'<>f.intent_id OR o.command#>>'{command,exchange_order_id}'<>f.exchange_order_id
            OR o.command#>>'{command,prepare_operation_id}'<>d.operation_id OR o.command#>>'{command,client_order_id}'<>t.wire_id
            OR o.command#>>'{command,market}'<>'LIFE-USDT' OR o.command#>>'{command,side}'<>t.side
            OR o.command#>>'{command,session_id}'<>t.session_id::text OR (o.command#>>'{command,epoch}')::bigint<>t.epoch
            OR o.command->'owner' IS DISTINCT FROM (SELECT command->'owner' FROM life_financial.operations
                WHERE account_id=p_account AND operation_id=d.operation_id)
            OR NOT o.command#>'{command,fills}' @> jsonb_build_array(f.observation) THEN RETURN false;END IF;
        at:=to_timestamp((f.observation->>'fill_at_ms')::bigint/1000.0);
        IF NOT EXISTS(SELECT 1 FROM life_financial.fills n WHERE n.account_id=p_account AND n.product='SWAP' AND n.trade_id=f.trade_id
            AND n.wire_id=t.wire_id AND n.quantity=(f.observation->>'quantity_base_life')::numeric AND n.price=(f.observation->>'price_usdt')::numeric
            AND n.independent_price=(f.observation->>'independent_price_usdt')::numeric AND n.fill_at=at
            AND n.value_at=to_timestamp((f.observation->>'independent_value_at_ms')::bigint/1000.0) AND n.source_hash=f.source_hash)
            OR NOT EXISTS(SELECT 1 FROM life_financial.fees n WHERE n.account_id=p_account AND n.product='SWAP' AND n.trade_id=f.trade_id
                AND n.payment_id='swapfee:'||encode(sha256(convert_to(f.trade_id,'UTF8')),'hex') AND n.asset='USDT' AND n.signed_fee=(f.observation->>'signed_fee')::numeric)
            OR NOT EXISTS(SELECT 1 FROM life_financial.financial_events e WHERE e.account_id=p_account AND e.product='SWAP' AND e.kind='FILL'
                AND e.event_id='swapfill:'||encode(sha256(convert_to(f.trade_id,'UTF8')),'hex') AND e.exchange_identity=f.trade_id
                AND e.base_delta=0 AND e.quote_delta=(f.observation->>'signed_fee')::numeric+(f.observation->>'realized_pnl_usdt')::numeric
                AND e.occurred_at=at AND e.source_hash=f.source_hash) THEN RETURN false;END IF;
        loss:=greatest(0,-(f.observation->>'signed_fee')::numeric)+greatest(0,-(f.observation->>'realized_pnl_usdt')::numeric);
        FOREACH sc IN ARRAY ARRAY['SESSION','DAY','CAMPAIGN'] LOOP
            window_key_value:=CASE sc WHEN 'SESSION' THEN t.session_id::text WHEN 'DAY' THEN to_char(at AT TIME ZONE 'UTC','YYYY-MM-DD') ELSE policy.campaign_id END;
            IF NOT EXISTS(SELECT 1 FROM life_financial.budget_events e WHERE e.account_id=p_account
                AND e.event_id='loss:'||encode(sha256(convert_to('SWAP:'||f.trade_id,'UTF8')),'hex')||':'||sc
                AND e.family='LOSS' AND e.scope=sc AND e.window_key=window_key_value AND e.kind='SPEND' AND e.amount=loss) THEN RETURN false;END IF;
        END LOOP;
    END LOOP;
    IF EXISTS(SELECT 1 FROM life_financial.fills n WHERE n.account_id=p_account AND n.product='SWAP' AND NOT EXISTS(
        SELECT 1 FROM life_financial.swap_fill_facts f WHERE f.account_id=p_account AND f.trade_id=n.trade_id))
        OR EXISTS(SELECT 1 FROM life_financial.fees n WHERE n.account_id=p_account AND n.product='SWAP' AND NOT EXISTS(
            SELECT 1 FROM life_financial.swap_fill_facts f WHERE f.account_id=p_account AND f.trade_id=n.trade_id
                AND n.payment_id='swapfee:'||encode(sha256(convert_to(f.trade_id,'UTF8')),'hex')))
        OR EXISTS(SELECT 1 FROM life_financial.financial_events n WHERE n.account_id=p_account AND n.product='SWAP' AND (n.kind<>'FILL' OR NOT EXISTS(
            SELECT 1 FROM life_financial.swap_fill_facts f WHERE f.account_id=p_account AND f.trade_id=n.exchange_identity))) THEN RETURN false;END IF;
    IF EXISTS(SELECT 1 FROM life_financial.budget_buckets b WHERE b.account_id=p_account AND b.family='LOSS' AND b.incurred<>COALESCE((
        SELECT sum(e.amount) FROM life_financial.budget_events e WHERE e.account_id=p_account AND e.family=b.family AND e.scope=b.scope
            AND e.window_key=b.window_key AND e.kind='SPEND'),0)) THEN RETURN false;END IF;
    FOR g IN SELECT * FROM life_financial.swap_fill_progress WHERE account_id=p_account LOOP
        IF (g.filled_base::numeric,g.paid_fees_usdt::numeric,g.execution_loss_usdt::numeric) IS DISTINCT FROM (
            SELECT ROW(sum((f.observation->>'quantity_base_life')::numeric),sum(greatest(0,-(f.observation->>'signed_fee')::numeric)),
                sum(greatest(0,-(f.observation->>'signed_fee')::numeric)+greatest(0,-(f.observation->>'realized_pnl_usdt')::numeric)))
                FROM life_financial.swap_fill_facts f WHERE f.account_id=p_account AND f.intent_id=g.intent_id)
            OR g.filled_base>(SELECT quantity FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=g.intent_id)
            OR EXISTS(SELECT 1 FROM life_financial.swap_fill_facts f WHERE f.account_id=p_account AND f.intent_id=g.intent_id AND f.exchange_order_id<>g.exchange_order_id)
            OR g.version<>(SELECT count(*) FROM life_financial.operations op WHERE op.account_id=p_account AND op.command->>'kind'='SWAP_FILL'
                AND op.command#>>'{command,intent_id}'=g.intent_id AND op.receipt->>'state'='APPLIED') THEN RETURN false;END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM life_financial.swap_fill_facts f WHERE f.account_id=p_account AND NOT EXISTS(
        SELECT 1 FROM life_financial.swap_fill_progress g WHERE g.account_id=p_account AND g.intent_id=f.intent_id)) THEN RETURN false;END IF;
    FOR o IN SELECT * FROM life_financial.operations WHERE account_id=p_account AND command->>'kind'='SWAP_FILL' LOOP
        IF o.fingerprint<>encode(sha256(convert_to(life_financial.canonical_json_v1(o.command),'UTF8')),'hex')
            OR o.command#>>'{command,operation_id}'<>o.operation_id OR o.receipt->>'kind'<>'SWAP_FILL'
            OR o.receipt->>'operation_id'<>o.operation_id OR o.receipt->>'intent_id'<>o.command#>>'{command,intent_id}'
            OR o.receipt->'trading_permission' IS DISTINCT FROM 'false'::jsonb OR o.receipt->'replayed' IS DISTINCT FROM 'false'::jsonb
            OR o.receipt->>'cumulative_base_life'<>o.command#>>'{command,cumulative_base_life}'
            OR (o.receipt->>'version')::bigint<>o.committed_version THEN RETURN false;END IF;
        IF o.receipt->>'state'='APPLIED' THEN
            IF EXISTS(SELECT 1 FROM jsonb_array_elements(o.command#>'{command,fills}') item WHERE NOT EXISTS(
                SELECT 1 FROM life_financial.swap_fill_facts f WHERE f.account_id=p_account AND f.observation=item
                    AND f.intent_id=o.command#>>'{command,intent_id}' AND f.exchange_order_id=o.command#>>'{command,exchange_order_id}'))
                OR (o.receipt->>'execution_loss_usdt')::numeric<>(SELECT sum(greatest(0,-(item->>'signed_fee')::numeric)
                    +greatest(0,-(item->>'realized_pnl_usdt')::numeric)) FROM jsonb_array_elements(o.command#>'{command,fills}') item) THEN RETURN false;END IF;
        ELSIF o.receipt->>'state'='RECOVERY_REQUIRED' THEN
            IF NOT EXISTS(SELECT 1 FROM life_financial.swap_pending_fills pending WHERE pending.account_id=p_account AND pending.operation_id=o.operation_id
                AND pending.intent_id=o.command#>>'{command,intent_id}' AND pending.command=o.command->'command'
                AND pending.reason=o.receipt->>'reason' AND pending.source_hash=encode(sha256(convert_to(life_financial.canonical_json_v1(pending.command),'UTF8')),'hex')) THEN RETURN false;END IF;
        ELSE RETURN false;END IF;
    END LOOP;
    RETURN NOT EXISTS(SELECT 1 FROM life_financial.swap_pending_fills pending WHERE pending.account_id=p_account AND NOT EXISTS(
        SELECT 1 FROM life_financial.operations op WHERE op.account_id=p_account AND op.operation_id=pending.operation_id AND op.command->>'kind'='SWAP_FILL'
            AND op.receipt->>'state'='RECOVERY_REQUIRED'));
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
CREATE OR REPLACE FUNCTION life_financial.swap_fill_command(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,cmd jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts;p life_financial.swap_fill_policies;d life_financial.swap_preparations;t life_financial.send_attempts;
    old life_financial.operations;known life_financial.swap_fill_facts;g life_financial.swap_fill_progress;
    request jsonb;result jsonb;view jsonb;j jsonb;k text;r jsonb;op text;trade text;hash text;reason text;received timestamptz;received_ms bigint;
    quantity numeric;fee numeric;pnl numeric;loss numeric;total_quantity numeric:=0;delta_quote numeric:=0;paid numeric:=0;execution_loss numeric:=0;
    seen text[]:=ARRAY[]::text[];at timestamptz;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR jsonb_typeof(cmd) IS DISTINCT FROM 'object' OR octet_length(cmd::text)>1048576
        OR (SELECT count(*) FROM jsonb_object_keys(cmd))<>12 OR NOT cmd ?& ARRAY['operation_id','prepare_operation_id','intent_id','client_order_id',
            'market','side','session_id','epoch','exchange_order_id','cumulative_base_life','fills','observed_at']
        OR cmd->>'operation_id' !~ '^[A-Za-z0-9_.:-]{1,96}$' THEN RAISE EXCEPTION 'LIFE_SWAP_FILL_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
    FOREACH k IN ARRAY ARRAY['operation_id','prepare_operation_id','intent_id','client_order_id','market','side','session_id','exchange_order_id','cumulative_base_life','observed_at'] LOOP
        IF jsonb_typeof(cmd->k) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'LIFE_SWAP_FILL_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
    END LOOP;
    IF jsonb_typeof(cmd->'epoch') IS DISTINCT FROM 'number' OR cmd->>'epoch' !~ '^[1-9][0-9]*$'
        OR (cmd->>'epoch')::numeric>9223372036854775807 THEN RAISE EXCEPTION 'LIFE_SWAP_FILL_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
    op:=cmd->>'operation_id';request:=jsonb_build_object('kind','SWAP_FILL','binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',cmd);
    IF p_fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(request),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'LIFE_SWAP_FILL_FINGERPRINT_INVALID' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,a.version);
    IF NOT life_financial.swap_accounting_verified(p_account) OR NOT life_financial.swap_verified(p_account) THEN
        RAISE EXCEPTION 'LIFE_SWAP_ACCOUNTING_UNVERIFIED' USING ERRCODE='P0001';END IF;
    SELECT * INTO old FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF old.command<>request OR old.fingerprint<>p_fingerprint THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001';END IF;
        RETURN old.receipt||'{"replayed":true}'::jsonb;END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    view:=life_financial.spot_state(p_account);
    IF view->>'digest' IS DISTINCT FROM p_cut THEN RAISE EXCEPTION 'LIFE_SPOT_CUT_CONFLICT' USING ERRCODE='P0001';END IF;
    IF (view->>'unverified')::boolean THEN RAISE EXCEPTION 'LIFE_SWAP_ACCOUNTING_UNVERIFIED' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT p FROM life_financial.swap_fill_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
    IF (SELECT count(*) FROM life_financial.operations WHERE account_id=p_account AND command->>'kind'='SWAP_FILL')>=p.max_observations THEN
        RAISE EXCEPTION 'LIFE_SWAP_FILL_OBSERVATION_BOUND' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT d FROM life_financial.swap_preparations WHERE account_id=p_account AND operation_id=cmd->>'prepare_operation_id';
    SELECT * INTO STRICT t FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=d.intent_id;
    received:=(cmd->>'observed_at')::timestamptz;received_ms:=floor(extract(epoch FROM received)*1000)::bigint;
    IF NOT isfinite(received) OR received>clock_timestamp() OR received_ms<d.prepared_at_ms
        OR t.product<>'SWAP' OR t.state NOT IN ('SEND_UNKNOWN','ACKED','TERMINAL') OR t.intent_id<>cmd->>'intent_id' OR t.wire_id<>cmd->>'client_order_id'
        OR t.side<>cmd->>'side' OR cmd->>'market'<>'LIFE-USDT' OR t.session_id::text<>cmd->>'session_id' OR t.epoch<>(cmd->>'epoch')::bigint
        OR t.process_id<>a.owner_process OR t.generation<>a.owner_generation OR t.epoch<>a.owner_epoch OR t.session_id<>a.current_session
        OR jsonb_typeof(cmd->'exchange_order_id') IS DISTINCT FROM 'string' OR cmd->>'exchange_order_id' !~ '^[1-9][0-9]{0,63}$'
        OR t.exchange_order_id IS NOT NULL AND t.exchange_order_id<>cmd->>'exchange_order_id' THEN
        RAISE EXCEPTION 'LIFE_SWAP_FILL_ORIGINAL_SCOPE_INVALID' USING ERRCODE='P0001';END IF;
    SELECT * INTO g FROM life_financial.swap_fill_progress WHERE account_id=p_account AND intent_id=t.intent_id;
    IF EXISTS(SELECT 1 FROM life_financial.send_attempts other WHERE other.account_id=p_account AND other.product='SWAP'
        AND other.exchange_order_id=cmd->>'exchange_order_id' AND other.intent_id<>t.intent_id) THEN
        RAISE EXCEPTION 'LIFE_SWAP_FILL_ORDER_ID_CONFLICT' USING ERRCODE='P0001';END IF;
    BEGIN
        IF t.state='TERMINAL' THEN RAISE EXCEPTION 'LIFE_SWAP_TERMINAL_RECONCILIATION_REQUIRED' USING ERRCODE='P0001';END IF;
        IF jsonb_typeof(cmd->'fills') IS DISTINCT FROM 'array' OR jsonb_array_length(cmd->'fills') NOT BETWEEN 1 AND p.max_total_fills
            OR jsonb_typeof(cmd->'cumulative_base_life') IS DISTINCT FROM 'string' THEN
            RAISE EXCEPTION 'LIFE_SWAP_FILL_SNAPSHOT_INVALID' USING ERRCODE='P0001';END IF;
        PERFORM (cmd->>'cumulative_base_life')::life_financial.positive_amount;
        FOR j IN SELECT value FROM jsonb_array_elements(cmd->'fills') LOOP
            IF NOT life_financial.swap_fill_valid(j,p,d,t,received_ms) THEN RAISE EXCEPTION 'LIFE_SWAP_FILL_INVALID' USING ERRCODE='P0001';END IF;
            trade:=j->>'trade_id';IF trade=ANY(seen) THEN RAISE EXCEPTION 'LIFE_SWAP_FILL_IDENTITY_CONFLICT' USING ERRCODE='P0001';END IF;seen:=array_append(seen,trade);
            quantity:=(j->>'quantity_base_life')::numeric;fee:=(j->>'signed_fee')::numeric;pnl:=(j->>'realized_pnl_usdt')::numeric;
            loss:=greatest(0,-fee)+greatest(0,-pnl);total_quantity:=total_quantity+quantity;paid:=paid+greatest(0,-fee);execution_loss:=execution_loss+loss;
            SELECT * INTO known FROM life_financial.swap_fill_facts WHERE account_id=p_account AND trade_id=trade;
            IF FOUND THEN
                IF known.intent_id<>t.intent_id OR known.exchange_order_id<>cmd->>'exchange_order_id' OR known.observation<>j THEN
                    RAISE EXCEPTION 'LIFE_SWAP_FILL_IDENTITY_CONFLICT' USING ERRCODE='P0001';END IF;
                CONTINUE;END IF;
            IF EXISTS(SELECT 1 FROM life_financial.swap_pending_fills pending CROSS JOIN LATERAL jsonb_array_elements(pending.command->'fills') item
                WHERE pending.account_id=p_account AND item->>'trade_id'=trade AND (pending.intent_id<>t.intent_id OR item<>j)) THEN
                RAISE EXCEPTION 'LIFE_SWAP_FILL_IDENTITY_CONFLICT' USING ERRCODE='P0001';END IF;
            at:=to_timestamp((j->>'fill_at_ms')::bigint/1000.0);hash:=encode(sha256(convert_to(life_financial.canonical_json_v1(j),'UTF8')),'hex');
            INSERT INTO life_financial.swap_fill_facts VALUES(p_account,trade,t.intent_id,cmd->>'exchange_order_id',j,op,hash);
            INSERT INTO life_financial.fills VALUES(p_account,'SWAP',trade,t.wire_id,quantity,(j->>'price_usdt')::numeric,
                (j->>'independent_price_usdt')::numeric,at,to_timestamp((j->>'independent_value_at_ms')::bigint/1000.0),hash);
            INSERT INTO life_financial.fees VALUES(p_account,'swapfee:'||encode(sha256(convert_to(trade,'UTF8')),'hex'),'SWAP',trade,'USDT',fee);
            INSERT INTO life_financial.financial_events(account_id,event_id,product,kind,exchange_identity,occurred_at,source_hash,base_delta,quote_delta)
                VALUES(p_account,'swapfill:'||encode(sha256(convert_to(trade,'UTF8')),'hex'),'SWAP','FILL',trade,at,hash,0,pnl+fee);
            delta_quote:=delta_quote+pnl+fee;PERFORM life_financial.spot_charge_loss(p_account,t.session_id,at,'SWAP:'||trade,loss);
        END LOOP;
        IF total_quantity<>(cmd->>'cumulative_base_life')::numeric OR total_quantity>t.quantity
            OR EXISTS(SELECT 1 FROM life_financial.swap_fill_facts WHERE account_id=p_account AND intent_id=t.intent_id AND NOT trade_id=ANY(seen))
            OR g.account_id IS NOT NULL AND (g.exchange_order_id<>cmd->>'exchange_order_id' OR total_quantity<g.filled_base) THEN
            RAISE EXCEPTION 'LIFE_SWAP_FILL_HISTORY_INCOMPLETE' USING ERRCODE='P0001';END IF;
        r:=life_financial.swap_fill_replay(p_account);
        IF EXISTS(SELECT 1 FROM life_financial.swap_pending_fills WHERE account_id=p_account AND intent_id=t.intent_id) THEN
            RAISE EXCEPTION 'LIFE_SWAP_PRIOR_FILL_PENDING' USING ERRCODE='P0001';END IF;
        IF (SELECT total FROM life_financial.balances WHERE account_id=p_account AND asset='USDT')+delta_quote<0 THEN
            RAISE EXCEPTION 'LIFE_SWAP_NEGATIVE_WALLET_UNSUPPORTED' USING ERRCODE='P0001';END IF;
        UPDATE life_financial.balances SET total=total+delta_quote,funding_deficit=greatest(0,reserved-total-delta_quote),version=version+1
            WHERE account_id=p_account AND asset='USDT';
        INSERT INTO life_financial.swap_fill_progress VALUES(p_account,t.intent_id,total_quantity,paid,execution_loss,cmd->>'exchange_order_id',1)
            ON CONFLICT(account_id,intent_id) DO UPDATE SET filled_base=EXCLUDED.filled_base,paid_fees_usdt=EXCLUDED.paid_fees_usdt,
                execution_loss_usdt=EXCLUDED.execution_loss_usdt,version=life_financial.swap_fill_progress.version+1;
        -- Replaying the complete common wallet disallows funding an earlier
        -- debit with later profits/transfers. No debt model is inferred.
        r:=life_financial.spot_capital_replay(p_account);
        IF (r->>'quote')::numeric<>(SELECT total FROM life_financial.balances WHERE account_id=p_account AND asset='USDT') THEN
            RAISE EXCEPTION 'LIFE_SWAP_WALLET_REPLAY_MISMATCH' USING ERRCODE='P0001';END IF;
        IF EXISTS(SELECT 1 FROM life_financial.balances WHERE account_id=p_account AND funding_deficit>0) THEN
            UPDATE life_financial.accounts SET halted=true WHERE account_id=p_account;END IF;
        result:=jsonb_build_object('kind','SWAP_FILL','operation_id',op,'intent_id',t.intent_id,'state','APPLIED',
            'cumulative_base_life',cmd->>'cumulative_base_life','execution_loss_usdt',execution_loss::text,'version',p_version+1,'replayed',false,'trading_permission',false);
    EXCEPTION WHEN raise_exception OR check_violation OR numeric_value_out_of_range OR unique_violation THEN
        GET STACKED DIAGNOSTICS reason=MESSAGE_TEXT;
        IF reason !~ '^LIFE_[A-Z_]+$' THEN reason:='LIFE_SWAP_FINANCIAL_CONSTRAINT_REJECTED';END IF;
        INSERT INTO life_financial.swap_pending_fills VALUES(p_account,op,t.intent_id,cmd,replace(reason,'LIFE_',''),
            encode(sha256(convert_to(life_financial.canonical_json_v1(cmd),'UTF8')),'hex'));
        UPDATE life_financial.accounts SET halted=true WHERE account_id=p_account;
        result:=jsonb_build_object('kind','SWAP_FILL','operation_id',op,'intent_id',t.intent_id,'state','RECOVERY_REQUIRED','reason',replace(reason,'LIFE_',''),
            'cumulative_base_life',cmd->>'cumulative_base_life','execution_loss_usdt',COALESCE(g.execution_loss_usdt,0)::text,'version',p_version+1,'replayed',false,'trading_permission',false);
    END;
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version) VALUES(p_account,op,p_fingerprint,request,result,p_version+1);
    RETURN result;
END; $$;
CREATE FUNCTION life_financial.spot_state_before_swap_terminal(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b jsonb;verified boolean;
BEGIN
    b:=life_financial.spot_state_before_portfolio(p_account)-'digest'-'observed_at';verified:=life_financial.portfolio_verified(p_account);
    b:=b||jsonb_build_object('portfolio',jsonb_build_object('verified',verified,
        'policy',(SELECT to_jsonb(p) FROM life_financial.portfolio_policies p WHERE p.account_id=p_account),
        'observations',COALESCE((SELECT jsonb_agg(to_jsonb(n) ORDER BY operation_id) FROM life_financial.portfolio_observations n WHERE n.account_id=p_account),'[]'::jsonb)),
        'financial_recovery_required',(b->>'financial_recovery_required')::boolean OR NOT verified,
        'recovery_required',(b->>'recovery_required')::boolean OR NOT verified,'unverified',(b->>'unverified')::boolean OR NOT verified);
    RETURN b||jsonb_build_object('digest',encode(sha256(convert_to(b::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
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
    IF op.command->>'kind' IN ('SWAP_TERMINAL','PORTFOLIO_NAV','PAID_FUNDING','SWAP_FILL','SWAP_ACK','CARRY_CHECK','RECORDER_POSITION_STAGE','RECORDER_POSITION_APPLY','RECORDER_EVENT_STAGE','RECORDER_EVENT_APPLY','RECORDER_CONFIG_STAGE','RECORDER_CONFIG_APPLY','RECORDER_STAGE','RECORDER_APPLY','SPOT_MARKOUT','SPOT_INVENTORY_SETTLE','SPOT_ACK','SPOT_FILL','SPOT_CASHFLOW','SPOT_NAV','SPOT_SESSION_TICK','SPOT_LIFECYCLE','SPOT_TERMINAL') OR op.command->>'kind' IN ('SPOT_ACTION_TRANSITION','SPOT_ACTION_ABANDON')
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
    IF op.command->>'kind'='SPOT_MARKOUT' THEN
        PERFORM life_financial.spot_markout_commit_guard(NEW.account_id,op.command); END IF;
    IF op.command->>'kind'='SPOT_INVENTORY_SETTLE' THEN
        PERFORM life_financial.spot_inventory_commit_guard(NEW.account_id,op.command); END IF;
    IF op.command->>'kind' IN ('SPOT_ACTION_CLAIM','SPOT_ACTION_TRANSITION','SPOT_ACTION_ABANDON') THEN
        PERFORM life_financial.spot_action_commit_guard(NEW.account_id,op.command); END IF;
    IF op.command->>'kind'='SPOT_PREPARE' AND op.command ? 'action' THEN
        PERFORM life_financial.spot_action_prepare_commit_guard(NEW.account_id,op.command); END IF;
    RETURN NULL;
END; $$;
CREATE FUNCTION life_financial.swap_terminal_read(p_account uuid,p_rows integer,p_bytes integer,p_operation text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_ACCESS_DENIED' USING ERRCODE='P0001';END IF;
    IF p_rows IS NULL OR p_bytes IS NULL OR p_rows NOT BETWEEN 1 AND 4096 OR p_bytes NOT BETWEEN 1 AND 16777216
        OR (SELECT count(*) FROM life_financial.swap_terminal_facts WHERE account_id=p_account)>p_rows THEN
        RAISE EXCEPTION 'LIFE_SWAP_TERMINAL_READ_BOUND' USING ERRCODE='P0001';END IF;
    IF NOT life_financial.swap_terminal_verified(p_account) OR NOT life_financial.swap_verified(p_account)
        OR NOT life_financial.portfolio_verified(p_account) THEN RAISE EXCEPTION 'LIFE_SWAP_TERMINAL_UNVERIFIED' USING ERRCODE='P0001';END IF;
    IF p_operation IS NULL THEN
        result:=jsonb_build_object('terminal',COALESCE((SELECT jsonb_agg(to_jsonb(f) ORDER BY operation_id) FROM life_financial.swap_terminal_facts f
                WHERE account_id=p_account AND state='TERMINAL'),'[]'::jsonb),
            'pending',COALESCE((SELECT jsonb_agg(to_jsonb(f) ORDER BY operation_id) FROM life_financial.swap_terminal_facts f
                WHERE account_id=p_account AND state='RECOVERY_REQUIRED'),'[]'::jsonb),'claim_released',false,'trading_permission',false);
    ELSE SELECT jsonb_build_object('command',o.command,'receipt',o.receipt) INTO result FROM life_financial.operations o
        WHERE o.account_id=p_account AND o.operation_id=p_operation AND o.command->>'kind'='SWAP_TERMINAL';END IF;
    IF octet_length(result::text)>p_bytes THEN RAISE EXCEPTION 'LIFE_SWAP_TERMINAL_READ_BOUND' USING ERRCODE='P0001';END IF;
    RETURN result;
END; $$;
CREATE OR REPLACE FUNCTION life_financial.spot_state(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b jsonb;verified boolean;pending boolean;
BEGIN
    b:=life_financial.spot_state_before_swap_terminal(p_account)-'digest'-'observed_at';verified:=life_financial.swap_terminal_verified(p_account);
    pending:=EXISTS(SELECT 1 FROM life_financial.swap_terminal_facts WHERE account_id=p_account AND state='RECOVERY_REQUIRED');
    b:=b||jsonb_build_object('swap_terminal',jsonb_build_object('verified',verified,'pending',pending,
        'policy',(SELECT to_jsonb(p) FROM life_financial.swap_terminal_policies p WHERE p.account_id=p_account),
        'facts',COALESCE((SELECT jsonb_agg(to_jsonb(f) ORDER BY operation_id) FROM life_financial.swap_terminal_facts f WHERE f.account_id=p_account),'[]'::jsonb)),
        'financial_recovery_required',(b->>'financial_recovery_required')::boolean OR NOT verified OR pending,
        'recovery_required',(b->>'recovery_required')::boolean OR NOT verified OR pending,'unverified',(b->>'unverified')::boolean OR NOT verified);
    RETURN b||jsonb_build_object('digest',encode(sha256(convert_to(b::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
END; $$;
CREATE FUNCTION life_financial.swap_terminal_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE o life_financial.operations;f life_financial.swap_terminal_facts;
BEGIN
    IF NOT life_financial.swap_terminal_verified(NEW.account_id) THEN RAISE EXCEPTION 'LIFE_SWAP_TERMINAL_UNVERIFIED_AT_COMMIT' USING ERRCODE='P0001';END IF;
    SELECT * INTO o FROM life_financial.operations WHERE account_id=NEW.account_id AND committed_version=NEW.version;
    IF o.command->>'kind'='SWAP_TERMINAL' AND o.receipt->>'state'='TERMINAL' THEN
        SELECT * INTO STRICT f FROM life_financial.swap_terminal_facts WHERE account_id=NEW.account_id AND operation_id=o.operation_id;
        IF life_financial.swap_terminal_reason(NEW.account_id,f.command,NEW.version-1,floor(extract(epoch FROM clock_timestamp())*1000)::bigint)<>'SWAP_TERMINAL_QUALIFIED' THEN
            RAISE EXCEPTION 'LIFE_SWAP_TERMINAL_SOURCE_STALE_AT_COMMIT' USING ERRCODE='P0001';END IF;
    END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER swap_terminal_account_commit AFTER UPDATE ON life_financial.accounts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN(NEW.version<>OLD.version) EXECUTE FUNCTION life_financial.swap_terminal_commit_guard();
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
