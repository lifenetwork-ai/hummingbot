-- Permanent, proof-bound retirement of a pending shared claim. The actual
-- position, original claim, budgets, economic history and identities survive.
CREATE TABLE life_financial.shared_release_policies (
    account_id uuid PRIMARY KEY REFERENCES life_financial.portfolio_policies,
    policy_hash life_financial.digest NOT NULL,
    max_releases integer NOT NULL CHECK(max_releases BETWEEN 1 AND 4096),
    max_total_bytes integer NOT NULL CHECK(max_total_bytes BETWEEN 1 AND 16777216),
    FOREIGN KEY(account_id,policy_hash) REFERENCES life_financial.policies
);
CREATE TABLE life_financial.shared_claim_releases (
    account_id uuid NOT NULL REFERENCES life_financial.shared_release_policies,
    intent_id life_financial.identity NOT NULL,operation_id life_financial.identity NOT NULL,
    terminal_operation_id life_financial.identity NOT NULL,basis jsonb NOT NULL,received_at_ms bigint NOT NULL CHECK(received_at_ms>0),
    PRIMARY KEY(account_id,intent_id),UNIQUE(account_id,operation_id),
    FOREIGN KEY(account_id,intent_id) REFERENCES life_financial.shared_capital_claims,
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(account_id,terminal_operation_id) REFERENCES life_financial.operations
);
DO $$ DECLARE t text;BEGIN
    FOREACH t IN ARRAY ARRAY['shared_release_policies','shared_claim_releases'] LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY scoped_read ON life_financial.%I FOR SELECT USING(life_financial.can_access(account_id))',t);
        EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',t);
    END LOOP;
END; $$;
CREATE FUNCTION life_financial.shared_claim_active_at(p_account uuid,p_intent text,p_version bigint) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT NOT EXISTS(SELECT 1 FROM life_financial.shared_claim_releases r JOIN life_financial.operations o USING(account_id,operation_id)
    WHERE r.account_id=p_account AND r.intent_id=p_intent AND o.committed_version<=p_version);
$$;
CREATE FUNCTION life_financial.shared_release_proof(p_account uuid,p_intent text,p_terminal text,p_version bigint,at_ms bigint) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE c life_financial.shared_capital_claims;t life_financial.send_attempts;o life_financial.operations;n life_financial.portfolio_observations;b jsonb;
BEGIN
    SELECT * INTO STRICT c FROM life_financial.shared_capital_claims WHERE account_id=p_account AND intent_id=p_intent;
    SELECT * INTO STRICT t FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=p_intent;
    SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=p_terminal AND committed_version<=p_version;
    IF t.state<>'TERMINAL' OR (t.wire_id,t.product,t.side,t.quantity,t.price,t.session_id,t.epoch)
        IS DISTINCT FROM (c.wire_id,c.product,c.side,c.quantity_base,c.price_usdt,c.session_id,c.epoch)
        OR o.receipt->>'state'<>'TERMINAL' OR o.command->'owner' IS DISTINCT FROM
            (SELECT origin.command->'owner' FROM life_financial.operations origin WHERE origin.account_id=c.account_id AND origin.operation_id=c.operation_id) THEN
        RAISE EXCEPTION 'LIFE_SHARED_RELEASE_PROOF_INVALID' USING ERRCODE='P0001';END IF;
    IF c.product='SWAP' THEN
        IF o.command->>'kind'<>'SWAP_TERMINAL' OR NOT life_financial.swap_terminal_verified(p_account)
            OR NOT EXISTS(SELECT 1 FROM life_financial.swap_terminal_facts f WHERE f.account_id=p_account AND f.intent_id=p_intent
                AND f.operation_id=p_terminal AND f.state='TERMINAL') THEN
            RAISE EXCEPTION 'LIFE_SHARED_RELEASE_PROOF_INVALID' USING ERRCODE='P0001';END IF;
    ELSIF c.product='SPOT' THEN
        IF o.command->>'kind'<>'SPOT_TERMINAL' OR NOT life_financial.spot_lifecycle_verified(p_account)
            OR NOT EXISTS(SELECT 1 FROM life_financial.spot_terminal_facts f WHERE f.account_id=p_account AND f.intent_id=p_intent AND f.operation_id=p_terminal)
            OR EXISTS(SELECT 1 FROM life_financial.reservations r WHERE r.account_id=p_account AND r.intent_id=p_intent AND (r.state<>'TERMINAL' OR r.remaining<>0)) THEN
            RAISE EXCEPTION 'LIFE_SHARED_RELEASE_PROOF_INVALID' USING ERRCODE='P0001';END IF;
    ELSE RAISE EXCEPTION 'LIFE_SHARED_RELEASE_PROOF_INVALID' USING ERRCODE='P0001';END IF;
    IF EXISTS(SELECT 1 FROM life_financial.spot_pending_observations f JOIN life_financial.operations op USING(account_id,operation_id)
            WHERE f.account_id=p_account AND op.committed_version<=p_version)
        OR EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts f JOIN life_financial.operations op ON op.account_id=f.account_id AND op.operation_id=f.source_operation_id
            WHERE f.account_id=p_account AND f.state='RECOVERY_REQUIRED' AND op.committed_version<=p_version)
        OR EXISTS(SELECT 1 FROM life_financial.swap_pending_fills f JOIN life_financial.operations op USING(account_id,operation_id)
            WHERE f.account_id=p_account AND op.committed_version<=p_version)
        OR EXISTS(SELECT 1 FROM life_financial.funding_observations f JOIN life_financial.operations op USING(account_id,operation_id)
            WHERE f.account_id=p_account AND f.state='RECOVERY_REQUIRED' AND op.committed_version<=p_version)
        OR EXISTS(SELECT 1 FROM life_financial.swap_terminal_facts f JOIN life_financial.operations op USING(account_id,operation_id)
            WHERE f.account_id=p_account AND f.state='RECOVERY_REQUIRED' AND op.committed_version<=p_version) THEN
        RAISE EXCEPTION 'LIFE_SHARED_RELEASE_PENDING' USING ERRCODE='P0001';END IF;
    b:=life_financial.portfolio_basis(p_account,p_version);
    SELECT x.* INTO STRICT n FROM life_financial.portfolio_observations x JOIN life_financial.operations op USING(account_id,operation_id)
        WHERE x.account_id=p_account AND op.committed_version<=p_version ORDER BY op.committed_version DESC LIMIT 1;
    IF b IS DISTINCT FROM n.basis OR NOT life_financial.portfolio_source_qualified_at(p_account,b,at_ms)
        OR EXISTS(SELECT 1 FROM life_financial.carry_checks carry CROSS JOIN LATERAL jsonb_array_elements(carry.bundle#>'{account,snapshot,pending}') pending
            WHERE carry.account_id=p_account AND carry.operation_id=b->>'carry_operation_id'
                AND (pending->>'wire_id'=c.wire_id OR pending->>'intent_id'=c.intent_id)) THEN
        RAISE EXCEPTION 'LIFE_SHARED_RELEASE_SOURCE_UNQUALIFIED' USING ERRCODE='P0001';END IF;
    RETURN b;
EXCEPTION WHEN no_data_found THEN RAISE EXCEPTION 'LIFE_SHARED_RELEASE_PROOF_INVALID' USING ERRCODE='P0001';
END; $$;
CREATE FUNCTION life_financial.shared_release_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.shared_release_policies;r life_financial.shared_claim_releases;o life_financial.operations;expected jsonb;
BEGIN
    SELECT * INTO p FROM life_financial.shared_release_policies WHERE account_id=p_account;
    IF NOT FOUND THEN RETURN NOT EXISTS(SELECT 1 FROM life_financial.shared_claim_releases WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=p_account AND command->>'kind'='SHARED_RELEASE');END IF;
    IF p.policy_hash IS DISTINCT FROM (SELECT policy_hash FROM life_financial.accounts WHERE account_id=p_account)
        OR (SELECT count(*) FROM life_financial.shared_claim_releases WHERE account_id=p_account)>p.max_releases
        OR (SELECT COALESCE(sum(octet_length(basis::text)),0) FROM life_financial.shared_claim_releases WHERE account_id=p_account)>p.max_total_bytes THEN RETURN false;END IF;
    FOR r IN SELECT * FROM life_financial.shared_claim_releases WHERE account_id=p_account LOOP
        SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=r.operation_id;
        expected:=jsonb_build_object('operation_id',r.operation_id,'intent_id',r.intent_id,'terminal_operation_id',r.terminal_operation_id);
        IF o.command->>'kind' IS DISTINCT FROM 'SHARED_RELEASE' OR o.command->'command' IS DISTINCT FROM expected
            OR o.fingerprint<>encode(sha256(convert_to(life_financial.canonical_json_v1(o.command),'UTF8')),'hex')
            OR (o.command->>'version')::bigint<>o.committed_version-1
            OR r.received_at_ms<>floor(extract(epoch FROM o.recorded_at)*1000)::bigint
            OR o.command->'owner' IS DISTINCT FROM (SELECT orig.command->'owner' FROM life_financial.shared_capital_claims c
                JOIN life_financial.operations orig USING(account_id,operation_id) WHERE c.account_id=p_account AND c.intent_id=r.intent_id)
            OR r.basis IS DISTINCT FROM life_financial.shared_release_proof(p_account,r.intent_id,r.terminal_operation_id,o.committed_version-1,r.received_at_ms)
            OR o.receipt IS DISTINCT FROM jsonb_build_object('kind','SHARED_RELEASE','operation_id',r.operation_id,'intent_id',r.intent_id,
                'released',true,'version',o.committed_version,'replayed',false,'trading_permission',false) THEN RETURN false;END IF;
    END LOOP;
    RETURN NOT EXISTS(SELECT 1 FROM life_financial.operations op WHERE op.account_id=p_account AND op.command->>'kind'='SHARED_RELEASE'
        AND NOT EXISTS(SELECT 1 FROM life_financial.shared_claim_releases fact WHERE fact.account_id=op.account_id AND fact.operation_id=op.operation_id));
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
CREATE FUNCTION life_financial.shared_release_command(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,cmd jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts;p life_financial.shared_release_policies;c life_financial.shared_capital_claims;o life_financial.operations;
    request jsonb;result jsonb;view jsonb;b jsonb;op text;k text;source_time timestamptz;at_ms bigint;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR jsonb_typeof(cmd) IS DISTINCT FROM 'object'
        OR (SELECT count(*) FROM jsonb_object_keys(cmd))<>3 OR NOT cmd ?& ARRAY['operation_id','intent_id','terminal_operation_id'] THEN
        RAISE EXCEPTION 'LIFE_SHARED_RELEASE_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
    FOREACH k IN ARRAY ARRAY['operation_id','intent_id','terminal_operation_id'] LOOP
        IF jsonb_typeof(cmd->k) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'LIFE_SHARED_RELEASE_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
        PERFORM (cmd->>k)::life_financial.identity;
    END LOOP;
    op:=cmd->>'operation_id';request:=jsonb_build_object('kind','SHARED_RELEASE','binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',cmd);
    IF p_fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(request),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'LIFE_SHARED_RELEASE_FINGERPRINT_INVALID' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,a.version);
    IF NOT life_financial.shared_release_verified(p_account) THEN RAISE EXCEPTION 'LIFE_SHARED_RELEASE_UNVERIFIED' USING ERRCODE='P0001';END IF;
    SELECT * INTO o FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF o.command<>request OR o.fingerprint<>p_fingerprint THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001';END IF;
        RETURN o.receipt||'{"replayed":true}'::jsonb;END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    view:=life_financial.spot_state(p_account);
    IF view->>'digest' IS DISTINCT FROM p_cut THEN RAISE EXCEPTION 'LIFE_SPOT_CUT_CONFLICT' USING ERRCODE='P0001';END IF;
    IF (view->>'unverified')::boolean OR (view->>'financial_recovery_required')::boolean THEN
        RAISE EXCEPTION 'LIFE_SHARED_RELEASE_PENDING' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT p FROM life_financial.shared_release_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
    IF (SELECT count(*) FROM life_financial.shared_claim_releases WHERE account_id=p_account)>=p.max_releases THEN
        RAISE EXCEPTION 'LIFE_SHARED_RELEASE_BOUND' USING ERRCODE='P0001';END IF;
    IF EXISTS(SELECT 1 FROM life_financial.shared_claim_releases WHERE account_id=p_account AND intent_id=cmd->>'intent_id') THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_ID_RETIRED' USING ERRCODE='P0001';END IF;
    SELECT * INTO c FROM life_financial.shared_capital_claims WHERE account_id=p_account AND intent_id=cmd->>'intent_id';
    IF NOT FOUND OR (SELECT command->'owner' FROM life_financial.operations WHERE account_id=p_account AND operation_id=c.operation_id) IS DISTINCT FROM p_owner THEN
        RAISE EXCEPTION 'LIFE_SHARED_RELEASE_PROOF_INVALID' USING ERRCODE='P0001';END IF;
    source_time:=clock_timestamp();at_ms:=floor(extract(epoch FROM source_time)*1000)::bigint;
    b:=life_financial.shared_release_proof(p_account,c.intent_id,cmd->>'terminal_operation_id',p_version,at_ms);
    IF NOT life_financial.portfolio_verified(p_account) OR NOT life_financial.portfolio_source_current(p_account,b,at_ms) THEN
        RAISE EXCEPTION 'LIFE_SHARED_RELEASE_SOURCE_UNQUALIFIED' USING ERRCODE='P0001';END IF;
    IF (SELECT COALESCE(sum(octet_length(basis::text)),0) FROM life_financial.shared_claim_releases WHERE account_id=p_account)+octet_length(b::text)>p.max_total_bytes THEN
        RAISE EXCEPTION 'LIFE_SHARED_RELEASE_BOUND' USING ERRCODE='P0001';END IF;
    INSERT INTO life_financial.shared_claim_releases VALUES(p_account,c.intent_id,op,cmd->>'terminal_operation_id',b,at_ms);
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    result:=jsonb_build_object('kind','SHARED_RELEASE','operation_id',op,'intent_id',c.intent_id,'released',true,
        'version',p_version+1,'replayed',false,'trading_permission',false);
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version,recorded_at)
        VALUES(p_account,op,p_fingerprint,request,result,p_version+1,source_time);
    RETURN result;
END; $$;

CREATE OR REPLACE FUNCTION life_financial.shared_operation_verified(p_account uuid,p_operation text) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE op life_financial.operations;p life_financial.shared_capital_policies;orders jsonb;candidate jsonb;decision jsonb;
    known boolean;changed boolean;expected jsonb;s jsonb;
BEGIN
    SELECT * INTO STRICT op FROM life_financial.operations WHERE account_id=p_account AND operation_id=p_operation;
    SELECT * INTO STRICT p FROM life_financial.shared_capital_policies WHERE account_id=p_account;
    IF op.command->>'kind' IS DISTINCT FROM 'SHARED_CAPITAL' OR (SELECT count(*) FROM jsonb_object_keys(op.command))<>6
        OR op.command#>>'{command,operation_id}' IS DISTINCT FROM p_operation
        OR (op.command->>'version')::bigint<>op.committed_version-1
        OR op.command#>>'{binding,account_uid}' IS DISTINCT FROM p.body->>'account_uid'
        OR op.command#>>'{binding,policy_hash}' IS DISTINCT FROM p.policy_hash
        OR op.fingerprint<>encode(sha256(convert_to(life_financial.canonical_json_v1(op.command),'UTF8')),'hex') THEN RETURN false; END IF;
    s:=op.command#>'{command,snapshot}';candidate:=op.command#>'{command,claim}';
    IF NOT life_financial.shared_snapshot_valid(s,p.body,floor(extract(epoch FROM op.recorded_at)*1000)::bigint,p.max_claims)
        OR NOT EXISTS(SELECT 1 FROM life_financial.shared_capital_observations x
            WHERE x.account_id=p_account AND x.sequence=(s->>'sequence')::bigint AND x.body=s)
        OR op.command#>>'{command,kind}' NOT IN ('RESERVE','OBSERVE') THEN RETURN false; END IF;
    SELECT COALESCE(jsonb_agg(life_financial.shared_claim_body(c) ORDER BY c.intent_id),'[]'::jsonb) INTO orders
        FROM life_financial.shared_capital_claims c JOIN life_financial.operations origin USING(account_id,operation_id)
        WHERE c.account_id=p_account AND origin.committed_version<op.committed_version AND life_financial.shared_claim_active_at(p_account,c.intent_id,op.committed_version-1);
    known:=EXISTS(SELECT 1 FROM jsonb_array_elements(orders) e WHERE e.value->>'intent_id'=candidate->>'intent_id');
    IF op.command#>>'{command,kind}'='RESERVE' THEN
        IF NOT life_financial.shared_claim_valid(candidate,p.body)
            OR candidate->>'session_id' IS DISTINCT FROM op.command#>>'{owner,session_id}'
            OR candidate->'epoch' IS DISTINCT FROM op.command#>'{owner,epoch}'
            OR known AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(orders) e WHERE life_financial.shared_claim_equal(e.value,candidate)) THEN RETURN false; END IF;
        IF NOT known THEN orders:=orders||jsonb_build_array(candidate); END IF;
    ELSIF candidate IS DISTINCT FROM 'null'::jsonb THEN RETURN false; END IF;
    decision:=life_financial.shared_evaluate(p.body,s,orders);
    changed:=op.command#>>'{command,kind}'='RESERVE' AND (decision->>'allowed')::boolean AND NOT known;
    IF (SELECT count(*) FROM life_financial.shared_capital_claims WHERE account_id=p_account AND operation_id=p_operation)
        <>(CASE WHEN changed THEN 1 ELSE 0 END) THEN RETURN false; END IF;
    IF changed AND NOT EXISTS(SELECT 1 FROM life_financial.shared_capital_claims c WHERE c.account_id=p_account AND c.operation_id=p_operation
        AND life_financial.shared_claim_equal(life_financial.shared_claim_body(c),candidate)) THEN RETURN false; END IF;
    expected:=jsonb_build_object('kind','SHARED_CAPITAL','operation_id',p_operation,'version',op.committed_version,
        'decision',decision,'changed',changed,'replayed',false,'trading_permission',false);
    RETURN op.receipt=expected;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
CREATE OR REPLACE FUNCTION life_financial.shared_capital_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.shared_capital_policies; h life_financial.shared_capital_heads; obs life_financial.shared_capital_observations;
    c life_financial.shared_capital_claims; op life_financial.operations; data jsonb; n bigint; f bigint; g bigint;
BEGIN
    SELECT * INTO p FROM life_financial.shared_capital_policies WHERE account_id=p_account;
    SELECT count(*) INTO n FROM life_financial.shared_capital_observations WHERE account_id=p_account;
    SELECT count(*) INTO f FROM life_financial.shared_capital_claims WHERE account_id=p_account;
    SELECT count(*) INTO g FROM life_financial.operations WHERE account_id=p_account AND command->>'kind'='SHARED_CAPITAL';
    SELECT * INTO h FROM life_financial.shared_capital_heads WHERE account_id=p_account;
    IF p.account_id IS NULL THEN RETURN h.account_id IS NULL AND n+f+g=0; END IF;
    IF NOT life_financial.shared_capital_policy_valid(p.body) OR p.model_hash<>encode(sha256(convert_to(life_financial.canonical_json_v1(jsonb_build_object('policy',p.body,'max_claims',p.max_claims)),'UTF8')),'hex')
        OR NOT EXISTS(SELECT 1 FROM life_financial.accounts a WHERE a.account_id=p_account AND a.policy_hash=p.policy_hash AND a.account_uid=p.body->>'account_uid') THEN RETURN false; END IF;
    IF h.account_id IS NULL THEN RETURN n+f+g=0; END IF;
    IF (n,f,g) IS DISTINCT FROM (h.observation_count,h.claim_count::bigint,h.operation_count) OR f>p.max_claims THEN RETURN false; END IF;
    SELECT * INTO STRICT obs FROM life_financial.shared_capital_observations WHERE account_id=p_account AND sequence=h.sequence;
    IF obs.sequence<>(obs.body->>'sequence')::bigint OR obs.observed_at_ms<>(obs.body->>'observed_at_ms')::bigint
        OR obs.source_hash<>encode(sha256(convert_to(life_financial.canonical_json_v1(obs.body),'UTF8')),'hex')
        OR NOT life_financial.shared_snapshot_valid(obs.body,p.body,obs.observed_at_ms,p.max_claims)
        OR floor(extract(epoch FROM h.last_checked_at)*1000)::bigint<obs.observed_at_ms THEN RETURN false; END IF;
    IF h.sequence<>(SELECT max(sequence) FROM life_financial.shared_capital_observations WHERE account_id=p_account) THEN RETURN false; END IF;
    SELECT * INTO STRICT op FROM life_financial.operations WHERE account_id=p_account AND operation_id=h.operation_id;
    IF op.committed_version<>(SELECT max(committed_version) FROM life_financial.operations WHERE account_id=p_account AND command->>'kind'='SHARED_CAPITAL') THEN RETURN false; END IF;
    IF NOT EXISTS(SELECT 1 FROM life_financial.operations origin WHERE origin.account_id=p_account AND origin.operation_id=obs.operation_id
        AND origin.command#>'{command,snapshot}'=obs.body
        AND origin.fingerprint=encode(sha256(convert_to(life_financial.canonical_json_v1(origin.command),'UTF8')),'hex')) THEN RETURN false; END IF;
    IF NOT life_financial.shared_operation_verified(p_account,op.operation_id) OR op.command#>'{command,snapshot}'<>obs.body
        OR op.receipt->'trading_permission' IS DISTINCT FROM 'false'::jsonb OR op.receipt->'replayed' IS DISTINCT FROM 'false'::jsonb
        OR op.receipt->>'kind' IS DISTINCT FROM 'SHARED_CAPITAL' OR op.receipt->>'operation_id' IS DISTINCT FROM op.operation_id
        OR (op.receipt->>'version')::bigint IS DISTINCT FROM op.committed_version
        OR op.fingerprint<>encode(sha256(convert_to(life_financial.canonical_json_v1(op.command),'UTF8')),'hex') THEN RETURN false; END IF;
    FOR c IN SELECT * FROM life_financial.shared_capital_claims WHERE account_id=p_account LOOP
        data:=life_financial.shared_claim_body(c);
        SELECT * INTO STRICT op FROM life_financial.operations WHERE account_id=p_account AND operation_id=c.operation_id;
        IF NOT life_financial.shared_claim_valid(data,p.body) OR op.command->>'kind'<>'SHARED_CAPITAL'
            OR NOT life_financial.shared_claim_equal(op.command#>'{command,claim}',data) OR op.receipt#>'{decision,allowed}' IS DISTINCT FROM 'true'::jsonb
            OR op.receipt->'changed' IS DISTINCT FROM 'true'::jsonb OR op.receipt->'trading_permission' IS DISTINCT FROM 'false'::jsonb
            OR op.fingerprint<>encode(sha256(convert_to(life_financial.canonical_json_v1(op.command),'UTF8')),'hex') THEN RETURN false; END IF;
    END LOOP;
    RETURN life_financial.shared_release_verified(p_account);
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
CREATE OR REPLACE FUNCTION life_financial.shared_capital_frame(p_account uuid,p_rows integer,p_bytes integer) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE h life_financial.shared_capital_heads; result jsonb; n bigint;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
    IF p_rows IS NULL OR p_rows NOT BETWEEN 1 AND 4096 OR p_bytes IS NULL OR p_bytes NOT BETWEEN 1 AND 16777216 THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_READ_BOUND_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT count(*) INTO n FROM (SELECT 1 FROM life_financial.shared_capital_claims WHERE account_id=p_account LIMIT p_rows+1) x;
    SELECT * INTO h FROM life_financial.shared_capital_heads WHERE account_id=p_account;
    IF n>p_rows OR (SELECT octet_length(body::text) FROM life_financial.shared_capital_observations WHERE account_id=p_account AND sequence=h.sequence)>p_bytes THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_READ_BOUND' USING ERRCODE='P0001'; END IF;
    IF NOT life_financial.shared_capital_verified(p_account) THEN RAISE EXCEPTION 'LIFE_CAPITAL_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    result:=jsonb_build_object('head',to_jsonb(h),'policy',(SELECT body FROM life_financial.shared_capital_policies WHERE account_id=p_account),
        'snapshot',(SELECT body FROM life_financial.shared_capital_observations WHERE account_id=p_account AND sequence=h.sequence),
        'claims',COALESCE((SELECT jsonb_agg(life_financial.shared_claim_body(c) ORDER BY intent_id) FROM life_financial.shared_capital_claims c WHERE account_id=p_account AND life_financial.shared_claim_active_at(p_account,c.intent_id,9223372036854775807)),'[]'::jsonb),
        'settled_claims',COALESCE((SELECT jsonb_agg(life_financial.shared_claim_body(c) ORDER BY intent_id) FROM life_financial.shared_capital_claims c
            WHERE account_id=p_account AND NOT life_financial.shared_claim_active_at(p_account,c.intent_id,9223372036854775807)),'[]'::jsonb),
        'trading_permission',false);
    IF octet_length(result::text)>p_bytes THEN RAISE EXCEPTION 'LIFE_CAPITAL_READ_BOUND' USING ERRCODE='P0001'; END IF;
    RETURN result;
END; $$;
CREATE OR REPLACE FUNCTION life_financial.shared_capital_command(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,cmd jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts;p life_financial.shared_capital_policies;h life_financial.shared_capital_heads;
    previous life_financial.operations;observed life_financial.shared_capital_observations;known life_financial.shared_capital_claims;
    request jsonb;view jsonb;orders jsonb;candidate jsonb:=cmd->'claim';snapshot jsonb:=cmd->'snapshot';decision jsonb;result jsonb;
    op text;at timestamptz;now_ms bigint;new_observation boolean:=false;changed boolean:=false;source_hash text;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR jsonb_typeof(cmd) IS DISTINCT FROM 'object'
        OR (SELECT count(*) FROM jsonb_object_keys(cmd))<>4 OR NOT cmd ?& ARRAY['kind','operation_id','snapshot','claim']
        OR cmd->>'kind' NOT IN ('RESERVE','OBSERVE') OR jsonb_typeof(cmd->'operation_id') IS DISTINCT FROM 'string'
        OR cmd->>'operation_id' !~ '^[A-Za-z0-9_.:-]{1,96}$' OR octet_length(cmd::text)>1048576
        OR cmd->>'kind'='OBSERVE' AND candidate IS DISTINCT FROM 'null'::jsonb THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    op:=cmd->>'operation_id';
    request:=jsonb_build_object('kind','SHARED_CAPITAL','binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',cmd);
    IF p_fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(request),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_FINGERPRINT_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    IF NOT life_financial.shared_capital_verified(p_account) THEN RAISE EXCEPTION 'LIFE_CAPITAL_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO previous FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF NOT life_financial.shared_operation_verified(p_account,op) THEN RAISE EXCEPTION 'LIFE_CAPITAL_UNVERIFIED' USING ERRCODE='P0001'; END IF;
        IF previous.command<>request OR previous.fingerprint<>p_fingerprint THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001'; END IF;
        RETURN previous.receipt||jsonb_build_object('replayed',true);
    END IF;
    PERFORM life_financial.guard_account(p_account,p_binding,p_owner,p_version); PERFORM life_financial.guard_spot_session(p_account);
    view:=life_financial.spot_state(p_account);
    IF view->>'digest' IS DISTINCT FROM p_cut THEN RAISE EXCEPTION 'LIFE_CAPITAL_CUT_STALE' USING ERRCODE='P0001'; END IF;
    IF (view->>'financial_recovery_required')::boolean OR (view->>'unverified')::boolean THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_FINANCIAL_RECOVERY_REQUIRED' USING ERRCODE='P0001'; END IF;
    SELECT p0.* INTO p FROM life_financial.shared_capital_policies p0 JOIN life_financial.accounts a0 USING(account_id,policy_hash) WHERE p0.account_id=p_account;
    IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_CAPITAL_POLICY_REQUIRED' USING ERRCODE='P0001'; END IF;
    at:=clock_timestamp();now_ms:=floor(extract(epoch FROM at)*1000)::bigint;
    SELECT * INTO h FROM life_financial.shared_capital_heads WHERE account_id=p_account;
    IF h.account_id IS NOT NULL AND at<h.last_checked_at THEN RAISE EXCEPTION 'LIFE_CAPITAL_CLOCK_ROLLBACK' USING ERRCODE='P0001'; END IF;
    IF NOT life_financial.swap_snapshot_current(p_account,snapshot) THEN RAISE EXCEPTION 'LIFE_SWAP_ACCOUNT_SNAPSHOT_MISMATCH' USING ERRCODE='P0001';END IF;
    IF NOT life_financial.shared_snapshot_valid(snapshot,p.body,now_ms,p.max_claims) THEN RAISE EXCEPTION 'LIFE_CAPITAL_SNAPSHOT_UNQUALIFIED' USING ERRCODE='P0001'; END IF;
    IF (snapshot->>'spot_life_base')::numeric<>(view#>>'{balances,LIFE,total}')::numeric
        OR (snapshot->>'spot_cash_quote')::numeric<>(view#>>'{balances,USDT,total}')::numeric THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_SPOT_BALANCE_MISMATCH' USING ERRCODE='P0001'; END IF;
    IF h.account_id IS NOT NULL AND ((snapshot->>'sequence')::bigint<h.sequence
        OR (snapshot->>'observed_at_ms')::bigint<(SELECT observed_at_ms FROM life_financial.shared_capital_observations WHERE account_id=p_account AND sequence=h.sequence)) THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_SNAPSHOT_REPLAY_OR_MUTATION' USING ERRCODE='P0001'; END IF;
    SELECT * INTO observed FROM life_financial.shared_capital_observations WHERE account_id=p_account AND sequence=(snapshot->>'sequence')::bigint;
    source_hash:=encode(sha256(convert_to(life_financial.canonical_json_v1(snapshot),'UTF8')),'hex');
    IF FOUND THEN
        IF observed.body<>snapshot OR observed.source_hash<>source_hash THEN RAISE EXCEPTION 'LIFE_CAPITAL_SNAPSHOT_REPLAY_OR_MUTATION' USING ERRCODE='P0001'; END IF;
    ELSE new_observation:=true; END IF;
    SELECT COALESCE(jsonb_agg(life_financial.shared_claim_body(c) ORDER BY c.intent_id),'[]'::jsonb) INTO orders FROM life_financial.shared_capital_claims c WHERE c.account_id=p_account AND life_financial.shared_claim_active_at(p_account,c.intent_id,9223372036854775807);
    IF cmd->>'kind'='RESERVE' THEN
        IF NOT life_financial.shared_claim_active_at(p_account,candidate->>'intent_id',9223372036854775807) THEN
            RAISE EXCEPTION 'LIFE_CAPITAL_ID_RETIRED' USING ERRCODE='P0001';END IF;
        IF NOT life_financial.shared_claim_valid(candidate,p.body) OR candidate->>'session_id'<>a.current_session::text
            OR (candidate->>'epoch')::bigint<>a.owner_epoch OR (candidate->>'risk_epoch')::bigint<>a.risk_epoch
            OR NOT EXISTS(SELECT 1 FROM life_financial.sessions WHERE account_id=p_account AND session_id=a.current_session AND config_version=(candidate->>'config_version')::bigint) THEN
            RAISE EXCEPTION 'LIFE_CAPITAL_CLAIM_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
        SELECT * INTO known FROM life_financial.shared_capital_claims WHERE account_id=p_account AND intent_id=candidate->>'intent_id';
        IF FOUND THEN
            IF NOT life_financial.shared_claim_equal(life_financial.shared_claim_body(known),candidate) OR NOT EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=p_account
                AND operation_id=known.operation_id AND command->'owner'=p_owner) THEN RAISE EXCEPTION 'LIFE_CAPITAL_CLAIM_CHANGED' USING ERRCODE='P0001'; END IF;
        ELSE
            IF (SELECT count(*) FROM life_financial.shared_capital_claims WHERE account_id=p_account)>=p.max_claims THEN RAISE EXCEPTION 'LIFE_CAPITAL_CLAIM_BOUND' USING ERRCODE='P0001'; END IF;
            IF EXISTS(SELECT 1 FROM jsonb_array_elements(snapshot->'pending') c WHERE c->>'intent_id'=candidate->>'intent_id') THEN
                RAISE EXCEPTION 'LIFE_CAPITAL_UNOWNED_PENDING_ID' USING ERRCODE='P0001'; END IF;
            orders:=orders||jsonb_build_array(candidate);
        END IF;
        IF EXISTS(SELECT 1 FROM jsonb_array_elements(orders||(snapshot->'pending')) c WHERE c->>'session_id'<>a.current_session::text OR (c->>'epoch')::bigint<>a.owner_epoch) THEN
            RAISE EXCEPTION 'LIFE_CAPITAL_OLD_SESSION_CLAIMS' USING ERRCODE='P0001'; END IF;
    END IF;
    -- Native working orders cannot vanish from a caller-qualified account DTO.
    IF EXISTS(SELECT 1 FROM life_financial.send_attempts t WHERE t.account_id=p_account AND t.state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND') AND NOT EXISTS(
        SELECT 1 FROM jsonb_array_elements(orders||(snapshot->'pending')) c WHERE c->>'intent_id'=t.intent_id AND c->>'wire_id'=t.wire_id
            AND c->>'product'=t.product AND c->>'side'=t.side AND (c->>'quantity_base')::numeric=t.quantity AND (c->>'price_usdt')::numeric=t.price)) THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_NATIVE_ORDER_SCOPE_INCOMPLETE' USING ERRCODE='P0001'; END IF;
    decision:=life_financial.shared_evaluate(p.body,snapshot,orders);
    changed:=cmd->>'kind'='RESERVE' AND (decision->>'allowed')::boolean AND known.account_id IS NULL;
    IF new_observation THEN INSERT INTO life_financial.shared_capital_observations VALUES(p_account,(snapshot->>'sequence')::bigint,snapshot,(snapshot->>'observed_at_ms')::bigint,source_hash,op); END IF;
    IF changed THEN
        INSERT INTO life_financial.shared_capital_claims VALUES(p_account,candidate->>'intent_id',candidate->>'wire_id',candidate->>'account_uid',
            (candidate->>'session_id')::uuid,(candidate->>'epoch')::bigint,(candidate->>'config_version')::bigint,(candidate->>'risk_epoch')::bigint,
            candidate->>'product',candidate->>'side',candidate->>'position_action',(candidate->>'quantity_base')::numeric,(candidate->>'price_usdt')::numeric,(candidate->>'leverage')::integer,op);
    END IF;
    INSERT INTO life_financial.shared_capital_heads VALUES(p_account,(snapshot->>'sequence')::bigint,at,1,1,CASE WHEN changed THEN 1 ELSE 0 END,op)
        ON CONFLICT(account_id) DO UPDATE SET sequence=EXCLUDED.sequence,last_checked_at=EXCLUDED.last_checked_at,
            observation_count=life_financial.shared_capital_heads.observation_count+CASE WHEN new_observation THEN 1 ELSE 0 END,
            operation_count=life_financial.shared_capital_heads.operation_count+1,
            claim_count=life_financial.shared_capital_heads.claim_count+CASE WHEN changed THEN 1 ELSE 0 END,operation_id=op;
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    result:=jsonb_build_object('kind','SHARED_CAPITAL','operation_id',op,'version',p_version+1,'decision',decision,'changed',changed,'replayed',false,'trading_permission',false);
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version) VALUES(p_account,op,p_fingerprint,request,result,p_version+1);
    RETURN result;
END; $$;
CREATE OR REPLACE FUNCTION life_financial.shared_capital_attempt_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.shared_capital_policies;h life_financial.shared_capital_heads;c life_financial.shared_capital_claims;s jsonb;orders jsonb;decision jsonb;
    at_ms bigint;request jsonb;fees numeric;
BEGIN
    SELECT * INTO p FROM life_financial.shared_capital_policies WHERE account_id=NEW.account_id;
    IF NOT FOUND OR NEW.state NOT IN ('PREPARED','ARMED','SEND_UNKNOWN') THEN RETURN NEW; END IF;
    SELECT * INTO c FROM life_financial.shared_capital_claims WHERE account_id=NEW.account_id AND intent_id=NEW.intent_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_CAPITAL_CLAIM_REQUIRED' USING ERRCODE='P0001'; END IF;
    IF NOT life_financial.shared_claim_active_at(NEW.account_id,c.intent_id,9223372036854775807) THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_ID_RETIRED' USING ERRCODE='P0001';END IF;
    IF (c.wire_id,c.product,c.side,c.quantity_base,c.price_usdt,c.session_id,c.epoch)
        IS DISTINCT FROM (NEW.wire_id,NEW.product,NEW.side,NEW.quantity,NEW.price,NEW.session_id,NEW.epoch) THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_NATIVE_CLAIM_MISMATCH' USING ERRCODE='P0001'; END IF;
    request:=convert_from(NEW.payload,'UTF8')::jsonb;
    IF c.config_version IS DISTINCT FROM (request->>'config_version')::bigint
        OR c.risk_epoch IS DISTINCT FROM (request->>'risk_epoch')::bigint
        OR NOT EXISTS(SELECT 1 FROM life_financial.accounts a WHERE a.account_id=NEW.account_id AND a.risk_epoch=c.risk_epoch) THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_NATIVE_CLAIM_MISMATCH' USING ERRCODE='P0001'; END IF;
    IF NOT life_financial.shared_capital_verified(NEW.account_id) THEN RAISE EXCEPTION 'LIFE_CAPITAL_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT h FROM life_financial.shared_capital_heads WHERE account_id=NEW.account_id;
    SELECT body INTO STRICT s FROM life_financial.shared_capital_observations WHERE account_id=NEW.account_id AND sequence=h.sequence;
    at_ms:=floor(extract(epoch FROM clock_timestamp())*1000)::bigint;
    IF NOT life_financial.shared_snapshot_valid(s,p.body,at_ms,p.max_claims) THEN RAISE EXCEPTION 'LIFE_CAPITAL_SNAPSHOT_UNQUALIFIED' USING ERRCODE='P0001'; END IF;
    IF (s->>'spot_life_base')::numeric<>(SELECT total FROM life_financial.balances WHERE account_id=NEW.account_id AND asset='LIFE')
        OR (s->>'spot_cash_quote')::numeric<>(SELECT total FROM life_financial.balances WHERE account_id=NEW.account_id AND asset='USDT') THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_SPOT_BALANCE_MISMATCH' USING ERRCODE='P0001'; END IF;
    SELECT COALESCE(jsonb_agg(life_financial.shared_claim_body(x)),'[]'::jsonb) INTO orders FROM life_financial.shared_capital_claims x WHERE account_id=NEW.account_id AND life_financial.shared_claim_active_at(NEW.account_id,x.intent_id,9223372036854775807);
    decision:=life_financial.shared_evaluate(p.body,s,orders);
    IF decision->'allowed' IS DISTINCT FROM 'true'::jsonb THEN RAISE EXCEPTION 'LIFE_CAPITAL_RISK_DENIED' USING ERRCODE='P0001'; END IF;
    -- The model's fee allowance must cover the actual native SPOT buffers too.
    -- A lower configured rate cannot lend fee-reserved cash to another product.
    IF NEW.product='SPOT' THEN
        request:=convert_from(NEW.payload,'UTF8')::jsonb;
        fees:=(request->>'fee_buffer_quote_usdt')::numeric+(request->>'fee_buffer_base_life')::numeric*NEW.price;
        IF fees>NEW.quantity*NEW.price*(p.body->>'fee_rate')::numeric THEN
            RAISE EXCEPTION 'LIFE_CAPITAL_FEE_BUFFER_UNCOVERED' USING ERRCODE='P0001'; END IF;
    END IF;
    RETURN NEW;
END; $$;
CREATE OR REPLACE FUNCTION life_financial.carry_check_verified(p_account uuid,p_operation text) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r life_financial.carry_checks;op life_financial.operations;cap life_financial.operations;before life_financial.operations;
    p life_financial.carry_policies;cp life_financial.shared_capital_policies;previous jsonb;claims jsonb;decision jsonb;reason text;
BEGIN
    SELECT * INTO STRICT r FROM life_financial.carry_checks WHERE account_id=p_account AND operation_id=p_operation;
    SELECT * INTO STRICT op FROM life_financial.operations WHERE account_id=p_account AND operation_id=p_operation;
    SELECT * INTO STRICT cap FROM life_financial.operations WHERE account_id=p_account AND operation_id=r.capital_operation_id;
    SELECT * INTO STRICT p FROM life_financial.carry_policies WHERE account_id=p_account;
    SELECT * INTO STRICT cp FROM life_financial.shared_capital_policies WHERE account_id=p_account;
    IF op.command->>'kind' IS DISTINCT FROM 'CARRY_CHECK' OR op.command#>'{command,bundle}' IS DISTINCT FROM r.bundle
        OR op.command#>'{command,prospective}' IS DISTINCT FROM r.prospective
        OR op.command#>>'{command,operation_id}' IS DISTINCT FROM r.operation_id
        OR op.fingerprint<>encode(sha256(convert_to(life_financial.canonical_json_v1(op.command),'UTF8')),'hex')
        OR cap.committed_version>=op.committed_version OR NOT life_financial.shared_operation_verified(p_account,cap.operation_id) THEN RETURN false; END IF;
    IF r.previous_qualified IS NOT NULL THEN
        SELECT q.bundle INTO STRICT previous FROM life_financial.carry_checks q WHERE account_id=p_account AND operation_id=r.previous_qualified AND q.qualified;
        SELECT * INTO STRICT before FROM life_financial.operations WHERE account_id=p_account AND operation_id=r.previous_qualified;
        IF before.committed_version>=op.committed_version THEN RETURN false; END IF;
    END IF;
    reason:=life_financial.carry_bundle_reason(p.body,cp.body,r.bundle,previous,cap.command#>'{command,snapshot}',r.checked_at_ms,p.max_payments);
    IF r.reason IS DISTINCT FROM reason OR r.qualified IS DISTINCT FROM (reason='CARRY_QUALIFIED') THEN RETURN false; END IF;
    SELECT COALESCE(jsonb_agg(life_financial.shared_claim_body(c) ORDER BY c.intent_id),'[]'::jsonb) INTO claims
        FROM life_financial.shared_capital_claims c JOIN life_financial.operations origin ON origin.account_id=c.account_id AND origin.operation_id=c.operation_id
        WHERE c.account_id=p_account AND origin.committed_version<op.committed_version AND life_financial.shared_claim_active_at(p_account,c.intent_id,op.committed_version-1);
    decision:=CASE WHEN r.qualified THEN life_financial.carry_evaluate(p.body,cp.body,r.bundle,claims,r.prospective) ELSE life_financial.carry_denial(reason) END;
    RETURN op.receipt=jsonb_build_object('kind','CARRY_CHECK','operation_id',op.operation_id,'version',op.committed_version,'decision',decision,'checked_at_ms',r.checked_at_ms,'replayed',false,'trading_permission',false);
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
CREATE OR REPLACE FUNCTION life_financial.carry_check(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,cmd jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts;h life_financial.carry_heads;p life_financial.carry_policies;cp life_financial.shared_capital_policies;
    cap life_financial.operations;prior life_financial.operations;request jsonb;view jsonb;previous jsonb;claims jsonb;decision jsonb;result jsonb;pay jsonb;
    reason text;op text;now_ms bigint;candidate jsonb:=cmd->'prospective';b jsonb:=cmd->'bundle';
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR jsonb_typeof(cmd) IS DISTINCT FROM 'object'
        OR (SELECT count(*) FROM jsonb_object_keys(cmd))<>3 OR NOT cmd ?& ARRAY['operation_id','bundle','prospective']
        OR jsonb_typeof(cmd->'operation_id') IS DISTINCT FROM 'string' OR cmd->>'operation_id' !~ '^[A-Za-z0-9_.:-]{1,96}$'
        OR octet_length(cmd::text)>1048576 THEN RAISE EXCEPTION 'LIFE_CARRY_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    op:=cmd->>'operation_id';request:=jsonb_build_object('kind','CARRY_CHECK','binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',cmd);
    IF p_fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(request),'UTF8')),'hex') THEN RAISE EXCEPTION 'LIFE_CARRY_FINGERPRINT_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    IF NOT life_financial.carry_verified(p_account) OR NOT life_financial.shared_capital_verified(p_account) THEN RAISE EXCEPTION 'LIFE_CARRY_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO prior FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF prior.command<>request OR prior.fingerprint<>p_fingerprint THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001'; END IF;
        RETURN prior.receipt||jsonb_build_object('replayed',true);
    END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    view:=life_financial.spot_state(p_account);
    IF view->>'digest' IS DISTINCT FROM p_cut THEN RAISE EXCEPTION 'LIFE_CARRY_CUT_STALE' USING ERRCODE='P0001'; END IF;
    IF (view->>'unverified')::boolean THEN RAISE EXCEPTION 'LIFE_CARRY_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT p FROM life_financial.carry_policies WHERE account_id=p_account;
    SELECT * INTO STRICT cp FROM life_financial.shared_capital_policies WHERE account_id=p_account;
    SELECT o.* INTO STRICT cap FROM life_financial.shared_capital_heads sh JOIN life_financial.operations o USING(account_id,operation_id) WHERE sh.account_id=p_account;
    IF (cap.command#>>'{command,snapshot,spot_life_base}')::numeric<>(view#>>'{balances,LIFE,total}')::numeric
        OR (cap.command#>>'{command,snapshot,spot_cash_quote}')::numeric<>(view#>>'{balances,USDT,total}')::numeric THEN
        RAISE EXCEPTION 'LIFE_CARRY_ACCOUNT_CONTEXT_STALE' USING ERRCODE='P0001'; END IF;
    SELECT * INTO h FROM life_financial.carry_heads WHERE account_id=p_account;
    now_ms:=floor(extract(epoch FROM clock_timestamp())*1000)::bigint;
    IF now_ms<(p.body->>'anchor_ms')::bigint OR h.account_id IS NOT NULL AND now_ms<h.last_checked_at_ms THEN RAISE EXCEPTION 'LIFE_CARRY_CLOCK_ROLLBACK' USING ERRCODE='P0001'; END IF;
    IF candidate IS DISTINCT FROM 'null'::jsonb AND (NOT life_financial.shared_claim_valid(candidate,cp.body)
        OR candidate->>'session_id' IS DISTINCT FROM a.current_session::text OR (candidate->>'epoch')::bigint IS DISTINCT FROM a.owner_epoch
        OR (candidate->>'risk_epoch')::bigint IS DISTINCT FROM a.risk_epoch OR NOT EXISTS(SELECT 1 FROM life_financial.sessions s
            WHERE s.account_id=p_account AND s.session_id=a.current_session AND s.config_version=(candidate->>'config_version')::bigint)) THEN
        RAISE EXCEPTION 'LIFE_CARRY_CLAIM_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT bundle INTO previous FROM life_financial.carry_checks WHERE account_id=p_account AND operation_id=h.qualified_operation_id;
    reason:=life_financial.carry_bundle_reason(p.body,cp.body,b,previous,cap.command#>'{command,snapshot}',now_ms,p.max_payments);
    SELECT COALESCE(jsonb_agg(life_financial.shared_claim_body(c) ORDER BY c.intent_id),'[]'::jsonb) INTO claims FROM life_financial.shared_capital_claims c WHERE c.account_id=p_account AND life_financial.shared_claim_active_at(p_account,c.intent_id,9223372036854775807);
    decision:=CASE WHEN reason='CARRY_QUALIFIED' THEN life_financial.carry_evaluate(p.body,cp.body,b,claims,candidate) ELSE life_financial.carry_denial(reason) END;
    INSERT INTO life_financial.carry_checks VALUES(p_account,op,b,candidate,now_ms,cap.operation_id,h.qualified_operation_id,reason='CARRY_QUALIFIED',reason);
    IF reason='CARRY_QUALIFIED' THEN
        FOR pay IN SELECT x FROM jsonb_array_elements(b#>'{funding,payments}') x LOOP
            INSERT INTO life_financial.carry_payments VALUES(p_account,pay->>'bill_id',(pay->>'settled_at_ms')::bigint,(pay->>'signed_payment_quote')::numeric,op) ON CONFLICT DO NOTHING;
        END LOOP;
    END IF;
    INSERT INTO life_financial.carry_heads VALUES(p_account,now_ms,1,op,CASE WHEN reason='CARRY_QUALIFIED' THEN op ELSE NULL END)
        ON CONFLICT(account_id) DO UPDATE SET last_checked_at_ms=EXCLUDED.last_checked_at_ms,
            check_count=life_financial.carry_heads.check_count+1,operation_id=op,
            qualified_operation_id=CASE WHEN reason='CARRY_QUALIFIED' THEN op ELSE life_financial.carry_heads.qualified_operation_id END;
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    result:=jsonb_build_object('kind','CARRY_CHECK','operation_id',op,'version',p_version+1,'decision',decision,'checked_at_ms',now_ms,'replayed',false,'trading_permission',false);
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version) VALUES(p_account,op,p_fingerprint,request,result,p_version+1);
    RETURN result;
END; $$;
CREATE OR REPLACE FUNCTION life_financial.carry_current_ready(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE h life_financial.carry_heads;r life_financial.carry_checks;op life_financial.operations;p life_financial.carry_policies;cp jsonb;previous jsonb;
BEGIN
    SELECT * INTO p FROM life_financial.carry_policies WHERE account_id=p_account;
    IF NOT FOUND THEN RETURN true; END IF;
    IF NOT life_financial.carry_verified(p_account) THEN RETURN false; END IF;
    SELECT * INTO STRICT h FROM life_financial.carry_heads WHERE account_id=p_account;
    SELECT * INTO STRICT r FROM life_financial.carry_checks WHERE account_id=p_account AND operation_id=h.operation_id;
    SELECT * INTO STRICT op FROM life_financial.operations WHERE account_id=p_account AND operation_id=h.operation_id;
    SELECT body INTO STRICT cp FROM life_financial.shared_capital_policies WHERE account_id=p_account;
    SELECT bundle INTO previous FROM life_financial.carry_checks WHERE account_id=p_account AND operation_id=r.previous_qualified;
    RETURN life_financial.funding_carry_current(p_account,r.bundle) AND life_financial.swap_snapshot_current(p_account,r.bundle#>'{account,snapshot}') AND r.qualified AND op.receipt#>'{decision,allowed}'='true'::jsonb
        AND (r.bundle#>>'{account,snapshot,spot_life_base}')::numeric=(SELECT total FROM life_financial.balances WHERE account_id=p_account AND asset='LIFE')
        AND (r.bundle#>>'{account,snapshot,spot_cash_quote}')::numeric=(SELECT total FROM life_financial.balances WHERE account_id=p_account AND asset='USDT')
        AND r.capital_operation_id=(SELECT operation_id FROM life_financial.shared_capital_heads WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.shared_capital_claims c JOIN life_financial.operations o USING(account_id,operation_id)
            WHERE c.account_id=p_account AND o.committed_version>op.committed_version)
        AND NOT EXISTS(SELECT 1 FROM life_financial.shared_claim_releases release JOIN life_financial.operations origin USING(account_id,operation_id)
            WHERE release.account_id=p_account AND origin.committed_version>op.committed_version)
        AND life_financial.carry_bundle_reason(p.body,cp,r.bundle,previous,r.bundle#>'{account,snapshot}',
            floor(extract(epoch FROM clock_timestamp())*1000)::bigint,p.max_payments)='CARRY_QUALIFIED';
EXCEPTION WHEN OTHERS THEN RETURN false;
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
    IF op.command->>'kind' IN ('SHARED_RELEASE','SWAP_TERMINAL','PORTFOLIO_NAV','PAID_FUNDING','SWAP_FILL','SWAP_ACK','CARRY_CHECK','RECORDER_POSITION_STAGE','RECORDER_POSITION_APPLY','RECORDER_EVENT_STAGE','RECORDER_EVENT_APPLY','RECORDER_CONFIG_STAGE','RECORDER_CONFIG_APPLY','RECORDER_STAGE','RECORDER_APPLY','SPOT_MARKOUT','SPOT_INVENTORY_SETTLE','SPOT_ACK','SPOT_FILL','SPOT_CASHFLOW','SPOT_NAV','SPOT_SESSION_TICK','SPOT_LIFECYCLE','SPOT_TERMINAL') OR op.command->>'kind' IN ('SPOT_ACTION_TRANSITION','SPOT_ACTION_ABANDON')
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
CREATE FUNCTION life_financial.spot_state_before_shared_release(p_account uuid) RETURNS jsonb
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
CREATE FUNCTION life_financial.shared_release_read(p_account uuid,p_rows integer,p_bytes integer,p_operation text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_ACCESS_DENIED' USING ERRCODE='P0001';END IF;
    IF p_rows IS NULL OR p_bytes IS NULL OR p_rows NOT BETWEEN 1 AND 4096 OR p_bytes NOT BETWEEN 1 AND 16777216
        OR (SELECT count(*) FROM life_financial.shared_claim_releases WHERE account_id=p_account)>p_rows THEN
        RAISE EXCEPTION 'LIFE_SHARED_RELEASE_READ_BOUND' USING ERRCODE='P0001';END IF;
    IF NOT life_financial.shared_release_verified(p_account) OR NOT life_financial.shared_capital_verified(p_account)
        OR NOT life_financial.portfolio_verified(p_account) THEN RAISE EXCEPTION 'LIFE_SHARED_RELEASE_UNVERIFIED' USING ERRCODE='P0001';END IF;
    IF p_operation IS NULL THEN result:=jsonb_build_object('releases',COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY operation_id)
        FROM life_financial.shared_claim_releases r WHERE r.account_id=p_account),'[]'::jsonb),'trading_permission',false);
    ELSE SELECT jsonb_build_object('command',o.command,'receipt',o.receipt) INTO result FROM life_financial.operations o
        WHERE o.account_id=p_account AND o.operation_id=p_operation AND o.command->>'kind'='SHARED_RELEASE';END IF;
    IF octet_length(result::text)>p_bytes THEN RAISE EXCEPTION 'LIFE_SHARED_RELEASE_READ_BOUND' USING ERRCODE='P0001';END IF;
    RETURN result;
END; $$;
CREATE OR REPLACE FUNCTION life_financial.spot_state(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b jsonb;verified boolean;
BEGIN
    b:=life_financial.spot_state_before_shared_release(p_account)-'digest'-'observed_at';verified:=life_financial.shared_release_verified(p_account);
    b:=b||jsonb_build_object('shared_release',jsonb_build_object('verified',verified,
        'policy',(SELECT to_jsonb(p) FROM life_financial.shared_release_policies p WHERE p.account_id=p_account),
        'releases',COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY operation_id) FROM life_financial.shared_claim_releases r WHERE r.account_id=p_account),'[]'::jsonb)),
        'financial_recovery_required',(b->>'financial_recovery_required')::boolean OR NOT verified,
        'recovery_required',(b->>'recovery_required')::boolean OR NOT verified,'unverified',(b->>'unverified')::boolean OR NOT verified);
    RETURN b||jsonb_build_object('digest',encode(sha256(convert_to(b::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
END; $$;
CREATE FUNCTION life_financial.shared_release_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE o life_financial.operations;r life_financial.shared_claim_releases;
BEGIN
    IF NOT life_financial.shared_release_verified(NEW.account_id) THEN RAISE EXCEPTION 'LIFE_SHARED_RELEASE_UNVERIFIED_AT_COMMIT' USING ERRCODE='P0001';END IF;
    SELECT * INTO o FROM life_financial.operations WHERE account_id=NEW.account_id AND committed_version=NEW.version;
    IF o.command->>'kind'='SHARED_RELEASE' THEN
        SELECT * INTO STRICT r FROM life_financial.shared_claim_releases WHERE account_id=NEW.account_id AND operation_id=o.operation_id;
        PERFORM life_financial.shared_release_proof(NEW.account_id,r.intent_id,r.terminal_operation_id,NEW.version-1,floor(extract(epoch FROM clock_timestamp())*1000)::bigint);
    END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER shared_release_account_commit AFTER UPDATE ON life_financial.accounts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN(NEW.version<>OLD.version) EXECUTE FUNCTION life_financial.shared_release_commit_guard();
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
