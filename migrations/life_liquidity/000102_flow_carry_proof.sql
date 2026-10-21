-- Reuse only a positive carry proof from this exact STABLE invocation to
-- avoid verifying its referenced shared-capital operation a second time.
-- Independent shared-capital calls, invalid carry states and unreferenced
-- capital heads retain the complete original path. No cross-statement cache.
DO $$ DECLARE p record;BEGIN
    FOR p IN SELECT f.*,l.lanname FROM pg_proc f JOIN pg_language l ON l.oid=f.prolang
        WHERE f.oid IN ('life_financial.shared_capital_verified(uuid)'::regprocedure,
            'life_financial.carry_verified(uuid)'::regprocedure,
            'life_financial.carry_check_verified(uuid,text)'::regprocedure,
            'life_financial.spot_body_before_carry(uuid)'::regprocedure,
            'life_financial.spot_body_before_swap(uuid)'::regprocedure,
            'life_financial.capital_carry_portfolio_commit_check(uuid,bigint)'::regprocedure) LOOP
        IF p.lanname<>'plpgsql' OR p.provolatile<>'s' OR NOT p.prosecdef OR p.proisstrict
            OR p.proleakproof OR p.proparallel<>'u' OR p.proretset OR p.prokind<>'f' OR p.pronargdefaults<>0
            OR p.prorettype<>(CASE WHEN p.proname IN ('spot_body_before_carry','spot_body_before_swap') THEN 'jsonb'::regtype
                WHEN p.proname='capital_carry_portfolio_commit_check' THEN 'void'::regtype ELSE 'boolean'::regtype END)
            OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') IS DISTINCT FROM (CASE p.proname
                WHEN 'shared_capital_verified' THEN '01dba619f86bfccbd15903138b7fe6e1e275dee4243d4388afb1fb93861991c2'
                WHEN 'carry_verified' THEN '88cdb8ede90ba0f9c315ddd40845b10ff5e7b4c7ac87ac0ccdb5a4e09faea2fb'
                WHEN 'carry_check_verified' THEN '5d32415bb65a6fb1f57d4932c7b063f44eecc2ed16887939d674af88d18f99db'
                WHEN 'spot_body_before_carry' THEN '4deb0a381a58e33368dab9472961c8e2745b06454ec2af2a2f806429782431ef'
                WHEN 'spot_body_before_swap' THEN '138275b1c8b0cc25037d5572df7d1a1b0a3f6392e666c295083b8e702227004e'
                WHEN 'capital_carry_portfolio_commit_check' THEN '92193768987b5c67e384483296eed677ed837a8bcbdd71dbbfec45f7ba45b500' END) THEN
            RAISE EXCEPTION 'LIFE_FLOW_CARRY_REUSE_MIGRATION_MISMATCH';END IF;
    END LOOP;
END; $$;

CREATE FUNCTION life_financial.shared_capital_after_carry_proof(p_account uuid, p_carry boolean)
 RETURNS boolean
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
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
        AND origin.fingerprint=origin.canonical_fingerprint) THEN RETURN false; END IF;
    IF NOT (CASE WHEN p_carry IS TRUE AND EXISTS(SELECT 1 FROM life_financial.carry_checks carry_origin WHERE carry_origin.account_id=p_account AND carry_origin.capital_operation_id=op.operation_id) THEN true ELSE life_financial.shared_operation_verified(p_account,op.operation_id) END) OR op.command#>'{command,snapshot}'<>obs.body
        OR op.receipt->'trading_permission' IS DISTINCT FROM 'false'::jsonb OR op.receipt->'replayed' IS DISTINCT FROM 'false'::jsonb
        OR op.receipt->>'kind' IS DISTINCT FROM 'SHARED_CAPITAL' OR op.receipt->>'operation_id' IS DISTINCT FROM op.operation_id
        OR (op.receipt->>'version')::bigint IS DISTINCT FROM op.committed_version
        OR op.fingerprint<>op.canonical_fingerprint THEN RETURN false; END IF;
    FOR c IN SELECT * FROM life_financial.shared_capital_claims WHERE account_id=p_account LOOP
        data:=life_financial.shared_claim_body(c);
        SELECT * INTO STRICT op FROM life_financial.operations WHERE account_id=p_account AND operation_id=c.operation_id;
        IF NOT life_financial.shared_claim_valid(data,p.body) OR op.command->>'kind'<>'SHARED_CAPITAL'
            OR NOT life_financial.shared_claim_equal(op.command#>'{command,claim}',data) OR op.receipt#>'{decision,allowed}' IS DISTINCT FROM 'true'::jsonb
            OR op.receipt->'changed' IS DISTINCT FROM 'true'::jsonb OR op.receipt->'trading_permission' IS DISTINCT FROM 'false'::jsonb
            OR op.fingerprint<>op.canonical_fingerprint THEN RETURN false; END IF;
    END LOOP;
    RETURN life_financial.shared_release_verified(p_account);
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $function$
;

CREATE FUNCTION life_financial.spot_body_with_carry_proof(p_account uuid, p_carry boolean)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE body jsonb; verified boolean; pending boolean; ingress_verified boolean; ingress_pending boolean; financial_verified boolean; financial_pending boolean; markout_verified boolean; shared_verified boolean;
BEGIN
    body:=life_financial.spot_body_v15(p_account);
    verified:=life_financial.recorder_executor_integrity(p_account);
    pending:=EXISTS(SELECT 1 FROM life_financial.recorder_outbox o WHERE o.account_id=p_account
        AND NOT EXISTS(SELECT 1 FROM life_financial.projection_receipts r WHERE r.account_id=p_account AND r.projection_id=o.projection_id));
    body:=body||jsonb_build_object('recorder_verified',verified,'recorder_pending',pending,
        'financial_recovery_required',(body->>'financial_recovery_required')::boolean OR NOT verified OR pending,
        'recovery_required',(body->>'recovery_required')::boolean OR NOT verified OR pending,
        'unverified',(body->>'unverified')::boolean OR NOT verified);
    ingress_verified:=life_financial.spot_ack_ingress_verified(p_account);
    ingress_pending:=EXISTS(SELECT 1 FROM life_financial.spot_ack_ingress r WHERE r.account_id=p_account AND NOT EXISTS(
        SELECT 1 FROM life_financial.spot_ack_ingress_receipts d WHERE d.account_id=p_account AND d.ingress_id=r.ingress_id));
    body:=body||jsonb_build_object('ack_ingress_verified',ingress_verified,'ack_ingress_pending',ingress_pending,
        'ack_ingress_version',(SELECT count(*) FROM life_financial.spot_ack_ingress WHERE account_id=p_account)
            +(SELECT count(*) FROM life_financial.spot_ack_ingress_receipts WHERE account_id=p_account),
        'financial_recovery_required',(body->>'financial_recovery_required')::boolean OR NOT ingress_verified OR ingress_pending,
        'recovery_required',(body->>'recovery_required')::boolean OR NOT ingress_verified OR ingress_pending,
        'unverified',(body->>'unverified')::boolean OR NOT ingress_verified);
    financial_verified:=life_financial.spot_financial_ingress_verified(p_account);
    financial_pending:=EXISTS(SELECT 1 FROM life_financial.spot_financial_ingress r WHERE r.account_id=p_account AND NOT EXISTS(
        SELECT 1 FROM life_financial.spot_financial_ingress_receipts d WHERE d.account_id=p_account AND d.ingress_id=r.ingress_id));
    body:=body||jsonb_build_object('financial_ingress_verified',financial_verified,'financial_ingress_pending',financial_pending,
        'ingress_progress',(SELECT to_jsonb(g) FROM life_financial.spot_ingress_progress g WHERE g.account_id=p_account),
        'financial_recovery_required',(body->>'financial_recovery_required')::boolean OR NOT financial_verified OR financial_pending,
        'recovery_required',(body->>'recovery_required')::boolean OR NOT financial_verified OR financial_pending,
        'unverified',(body->>'unverified')::boolean OR NOT financial_verified);
    markout_verified:=life_financial.spot_markouts_verified(p_account);
    body:=body||jsonb_build_object('markout_verified',markout_verified,
        'recovery_required',(body->>'recovery_required')::boolean OR NOT markout_verified,
        'unverified',(body->>'unverified')::boolean OR NOT markout_verified);
    shared_verified:=life_financial.shared_capital_after_carry_proof(p_account,p_carry);
    body:=body||jsonb_build_object('shared_capital',jsonb_build_object('verified',shared_verified,
        'head',(SELECT to_jsonb(h) FROM life_financial.shared_capital_heads h WHERE account_id=p_account),
        'model_hash',(SELECT model_hash FROM life_financial.shared_capital_policies WHERE account_id=p_account)),
        'recovery_required',(body->>'recovery_required')::boolean OR NOT shared_verified,
        'unverified',(body->>'unverified')::boolean OR NOT shared_verified);
    RETURN body;
END; $function$
;

-- Private proof-carrying helpers cannot inherit deployment EXECUTE grants.
DO $$ DECLARE r record;definition text;needle text;replacement text;BEGIN
    FOR r IN SELECT DISTINCT x.grantee,p.oid::regprocedure::text AS signature FROM pg_proc p
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE p.oid IN ('life_financial.shared_capital_after_carry_proof(uuid,boolean)'::regprocedure,
            'life_financial.spot_body_with_carry_proof(uuid,boolean)'::regprocedure) AND x.grantee<>p.proowner LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %s',r.signature,
            CASE WHEN r.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(r.grantee)) END);
    END LOOP;
    definition:=pg_get_functiondef('life_financial.spot_body_before_swap(uuid)'::regprocedure);
    needle:=$n$b:=life_financial.spot_body_before_carry(p_account);
    verified:=life_financial.carry_verified(p_account); ready:=life_financial.carry_ready_after_verification(p_account,verified);$n$;
    replacement:=$r$IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID' USING ERRCODE='P0001';END IF;
    verified:=life_financial.carry_verified(p_account);
    b:=life_financial.spot_body_with_carry_proof(p_account,verified);
    ready:=life_financial.carry_ready_after_verification(p_account,verified);$r$;
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_FLOW_CARRY_REUSE_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
    definition:=pg_get_functiondef('life_financial.capital_carry_portfolio_commit_check(uuid,bigint)'::regprocedure);
    needle:='shared_ok:=life_financial.shared_capital_verified(p_account);';
    replacement:='shared_ok:=life_financial.shared_capital_after_carry_proof(p_account,carry_ok);';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_FLOW_CARRY_REUSE_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;
