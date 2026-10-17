-- Only original current-owner OBSERVE may refresh account facts after HALT or
-- expiry. RESERVE, prepare/arm and final sends retain their active-risk guards.
-- No money, claims, budgets, session deadline, owner or HALT is reset.
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
    IF cmd->>'kind'='OBSERVE' THEN
        -- Current original-owner accounting facts may remain available after
        -- HALT/expiry. This branch cannot create a claim or restore admission.
        PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    ELSE
        PERFORM life_financial.guard_account(p_account,p_binding,p_owner,p_version);
        PERFORM life_financial.guard_spot_session(p_account);
    END IF;
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


CREATE OR REPLACE FUNCTION life_financial.commit_owner_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE a life_financial.accounts; expiry timestamptz; at timestamptz; op life_financial.operations;
BEGIN
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=NEW.account_id;
    SELECT * INTO op FROM life_financial.operations WHERE account_id=NEW.account_id AND committed_version=NEW.version;
    IF a.owner_generation IS DISTINCT FROM NEW.owner_generation OR a.owner_process IS DISTINCT FROM NEW.owner_process
        OR a.owner_epoch<>NEW.owner_epoch OR a.current_session IS DISTINCT FROM NEW.current_session
        OR a.lineage<>NEW.lineage OR a.policy_hash<>NEW.policy_hash OR a.config_hash<>NEW.config_hash THEN
        RAISE EXCEPTION 'LIFE_OWNER_EXPIRED_OR_CHANGED_AT_COMMIT' USING ERRCODE='P0001'; END IF;
    IF op.command->>'kind' IN ('JOINT_BATCH','SHARED_RELEASE','SWAP_TERMINAL','PORTFOLIO_NAV','PAID_FUNDING','SWAP_FILL','SWAP_ACK','CARRY_CHECK','RECORDER_POSITION_STAGE','RECORDER_POSITION_APPLY','RECORDER_EVENT_STAGE','RECORDER_EVENT_APPLY','RECORDER_CONFIG_STAGE','RECORDER_CONFIG_APPLY','RECORDER_STAGE','RECORDER_APPLY','SPOT_MARKOUT','SPOT_INVENTORY_SETTLE','SPOT_ACK','SPOT_FILL','SPOT_CASHFLOW','SPOT_NAV','SPOT_SESSION_TICK','SPOT_LIFECYCLE','SPOT_TERMINAL') OR op.command->>'kind' IN ('SPOT_ACTION_TRANSITION','SPOT_ACTION_ABANDON')
        AND op.command#>>'{command,state}'<>'DISPATCHED'
        OR op.command->>'kind'='SHARED_CAPITAL' AND op.command#>>'{command,kind}'='OBSERVE' THEN
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

REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
