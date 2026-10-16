-- Original independent SPOT horizon observations. No balances, activation or probe grants.
CREATE FUNCTION life_financial.spot_markout_policy_valid(p_horizons bigint[],p_window bigint,p_cohorts jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE cohort jsonb;
BEGIN
    IF p_horizons IS NULL OR array_ndims(p_horizons)<>1 OR array_lower(p_horizons,1)<>1
        OR cardinality(p_horizons) NOT BETWEEN 1 AND 16 OR array_position(p_horizons,NULL) IS NOT NULL
        OR NOT 0<ALL(p_horizons) OR NOT 253402300799999>=ALL(p_horizons)
        OR cardinality(p_horizons)<>(SELECT count(DISTINCT h) FROM unnest(p_horizons) h)
        OR p_window IS NULL OR p_window<=(SELECT max(h) FROM unnest(p_horizons) h)
        OR p_window>253402300799999 OR p_cohorts IS NULL OR jsonb_typeof(p_cohorts)<>'array'
        OR jsonb_array_length(p_cohorts) NOT BETWEEN 1 AND 64
        OR jsonb_array_length(p_cohorts)<>(SELECT count(DISTINCT c) FROM jsonb_array_elements(p_cohorts) c) THEN RETURN false; END IF;
    FOR cohort IN SELECT * FROM jsonb_array_elements(p_cohorts) LOOP
        IF jsonb_typeof(cohort)<>'array' OR jsonb_array_length(cohort)<>3
            OR jsonb_typeof(cohort->0)<>'string' OR cohort->>0 NOT IN ('BUY','SELL')
            OR jsonb_typeof(cohort->1)<>'string' OR cohort->>1 NOT IN ('small','large')
            OR jsonb_typeof(cohort->2)<>'number' OR NOT (cohort->>2 ~ '^[1-9][0-9]*$')
            OR NOT (cohort->>2)::bigint=ANY(p_horizons) THEN RETURN false; END IF;
    END LOOP;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.spot_markout_command_valid(p_command jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE name text; stamp bigint; price life_financial.positive_amount; observed timestamptz;
BEGIN
    IF p_command IS NULL OR jsonb_typeof(p_command)<>'object'
        OR (SELECT count(*) FROM jsonb_object_keys(p_command))<>8
        OR NOT p_command ?& ARRAY['operation_id','trade_id','horizon_ms','independent_price_usdt','value_at_ms','reference_observed_at_ms','observed_at','source_kind']
        OR EXISTS(SELECT 1 FROM jsonb_each(p_command) WHERE value='null'::jsonb) OR octet_length(p_command::text)>65536 THEN RETURN false; END IF;
    FOREACH name IN ARRAY ARRAY['operation_id','trade_id','independent_price_usdt','observed_at','source_kind'] LOOP
        IF jsonb_typeof(p_command->name)<>'string' THEN RETURN false; END IF;
    END LOOP;
    IF NOT (p_command->>'operation_id' ~ '^[A-Za-z0-9_.:-]{1,96}$')
        OR NOT (p_command->>'trade_id' ~ '^[A-Za-z0-9_.:-]{1,128}$')
        OR p_command->>'source_kind'<>'independent_market'
        OR NOT (p_command->>'observed_at' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]{1,6})?([+]00:00|Z)$') THEN RETURN false; END IF;
    FOREACH name IN ARRAY ARRAY['horizon_ms','value_at_ms','reference_observed_at_ms'] LOOP
        IF jsonb_typeof(p_command->name)<>'number' OR NOT (p_command->>name ~ '^[1-9][0-9]*$') THEN RETURN false; END IF;
        stamp:=(p_command->>name)::bigint;
        IF stamp NOT BETWEEN 1 AND 253402300799999 THEN RETURN false; END IF;
    END LOOP;
    price:=(p_command->>'independent_price_usdt')::life_financial.positive_amount;
    observed:=(p_command->>'observed_at')::timestamptz;
    RETURN isfinite(observed) AND (p_command->>'reference_observed_at_ms')::bigint>=(p_command->>'value_at_ms')::bigint;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE TABLE life_financial.spot_markout_policies (
    account_id uuid PRIMARY KEY REFERENCES life_financial.accounts,
    policy_hash life_financial.digest NOT NULL,
    horizons_ms bigint[] NOT NULL CHECK(cardinality(horizons_ms) BETWEEN 1 AND 16 AND 0<ALL(horizons_ms)),
    min_samples integer NOT NULL CHECK(min_samples>0),
    size_cutoff_base life_financial.positive_amount NOT NULL,
    cohort_window_ms bigint NOT NULL CHECK(cohort_window_ms>0),
    max_horizon_lag_ms bigint NOT NULL CHECK(max_horizon_lag_ms>=0),
    max_fact_age_ms integer NOT NULL CHECK(max_fact_age_ms>0),
    min_mean_markout_quote life_financial.signed_amount NOT NULL,
    monitored_cohorts jsonb NOT NULL CHECK(jsonb_typeof(monitored_cohorts)='array' AND jsonb_array_length(monitored_cohorts)>0),
    FOREIGN KEY(account_id,policy_hash) REFERENCES life_financial.policies,
    CHECK(life_financial.spot_markout_policy_valid(horizons_ms,cohort_window_ms,monitored_cohorts))
);
CREATE TABLE life_financial.spot_markout_facts (
    account_id uuid NOT NULL,trade_id life_financial.identity NOT NULL,horizon_ms bigint NOT NULL CHECK(horizon_ms>0),
    operation_id life_financial.identity NOT NULL,command jsonb NOT NULL CHECK(jsonb_typeof(command)='object'),
    fill_hash life_financial.digest NOT NULL,markout_usdt life_financial.signed_amount NOT NULL,
    PRIMARY KEY(account_id,trade_id,horizon_ms),UNIQUE(account_id,operation_id),
    FOREIGN KEY(account_id,trade_id) REFERENCES life_financial.spot_fill_facts,
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED
);

CREATE FUNCTION life_financial.spot_markouts_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE fact life_financial.spot_markout_facts; original life_financial.spot_fill_facts; policy life_financial.spot_markout_policies;
    op life_financial.operations; expected numeric; side text; target bigint;
BEGIN
    IF EXISTS(SELECT 1 FROM life_financial.spot_markout_policies p WHERE p.account_id=p_account
        AND NOT life_financial.spot_markout_policy_valid(p.horizons_ms,p.cohort_window_ms,p.monitored_cohorts)) THEN RETURN false; END IF;
    IF NOT EXISTS(SELECT 1 FROM life_financial.spot_markout_facts WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.attribution WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=p_account AND command->>'kind'='SPOT_MARKOUT') THEN RETURN true; END IF;
    SELECT p.* INTO STRICT policy FROM life_financial.spot_markout_policies p JOIN life_financial.accounts a USING(account_id,policy_hash) WHERE p.account_id=p_account;
    IF EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=p_account AND o.command->>'kind'='SPOT_MARKOUT' AND NOT EXISTS(
        SELECT 1 FROM life_financial.spot_markout_facts f WHERE f.account_id=o.account_id AND f.operation_id=o.operation_id))
        OR EXISTS(SELECT 1 FROM life_financial.attribution a WHERE a.account_id=p_account AND (a.product<>'SPOT' OR NOT EXISTS(
            SELECT 1 FROM life_financial.spot_markout_facts f WHERE f.account_id=a.account_id AND f.trade_id=a.trade_id AND f.horizon_ms=a.horizon_ms))) THEN RETURN false; END IF;
    FOR fact IN SELECT * FROM life_financial.spot_markout_facts WHERE account_id=p_account LOOP
        SELECT * INTO STRICT original FROM life_financial.spot_fill_facts WHERE account_id=p_account AND trade_id=fact.trade_id;
        SELECT * INTO STRICT op FROM life_financial.operations WHERE account_id=p_account AND operation_id=fact.operation_id;
        SELECT t.side INTO STRICT side FROM life_financial.send_attempts t WHERE t.account_id=p_account AND t.intent_id=original.intent_id;
        target:=(original.observation->>'fill_at_ms')::bigint+fact.horizon_ms;
        expected:=(CASE side WHEN 'BUY' THEN 1 ELSE -1 END)*((fact.command->>'independent_price_usdt')::numeric-(original.observation->>'price_usdt')::numeric)
            *(original.observation->>'quantity_base_life')::numeric;
        IF (NOT life_financial.spot_markout_command_valid(fact.command) OR fact.fill_hash<>encode(sha256(convert_to(original.observation::text,'UTF8')),'hex')
            OR NOT fact.horizon_ms=ANY(policy.horizons_ms) OR fact.command->>'operation_id'<>fact.operation_id OR fact.command->>'trade_id'<>fact.trade_id
            OR (fact.command->>'horizon_ms')::bigint<>fact.horizon_ms OR fact.command->>'source_kind'<>'independent_market'
            OR (fact.command->>'value_at_ms')::bigint<target OR (fact.command->>'reference_observed_at_ms')::bigint<(fact.command->>'value_at_ms')::bigint
            OR (fact.command->>'reference_observed_at_ms')::bigint>target+policy.max_horizon_lag_ms
            OR (fact.command->>'observed_at')::timestamptz<to_timestamp((fact.command->>'reference_observed_at_ms')::double precision/1000)
            OR op.command->>'kind'<>'SPOT_MARKOUT' OR op.command->'command'<>fact.command
            OR op.fingerprint<>encode(sha256(convert_to(life_financial.canonical_json_v1(op.command),'UTF8')),'hex')
            OR op.receipt->>'trade_id' IS DISTINCT FROM fact.trade_id OR (op.receipt->>'horizon_ms')::bigint IS DISTINCT FROM fact.horizon_ms
            OR op.receipt->'replayed' IS DISTINCT FROM 'false'::jsonb
            OR op.receipt->>'kind' IS DISTINCT FROM 'SPOT_MARKOUT' OR op.receipt->>'operation_id'<>fact.operation_id OR op.receipt->>'state'<>'RECORDED'
            OR (op.receipt->>'version')::bigint<>op.committed_version OR op.receipt->'trading_permission' IS DISTINCT FROM 'false'::jsonb
            OR (op.receipt->>'markout_usdt')::numeric<>expected OR fact.markout_usdt<>expected
            OR NOT EXISTS(SELECT 1 FROM life_financial.attribution a WHERE a.account_id=p_account AND a.product='SPOT' AND a.trade_id=fact.trade_id AND a.horizon_ms=fact.horizon_ms
                AND a.markout_usdt=expected AND a.value_at=to_timestamp((fact.command->>'value_at_ms')::double precision/1000) AND a.source_hash=fact.fill_hash)) IS DISTINCT FROM false THEN RETURN false; END IF;
    END LOOP;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.spot_markout_commit_guard(p_account uuid,p_request jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE policy life_financial.spot_markout_policies; observed timestamptz; at timestamptz;
BEGIN
    SELECT p.* INTO STRICT policy FROM life_financial.spot_markout_policies p JOIN life_financial.accounts a USING(account_id,policy_hash) WHERE p.account_id=p_account;
    observed:=(p_request#>>'{command,observed_at}')::timestamptz; at:=clock_timestamp();
    IF NOT isfinite(observed) OR observed>at OR extract(epoch FROM at-observed)*1000>policy.max_fact_age_ms THEN
        RAISE EXCEPTION 'LIFE_MARKOUT_FACTS_STALE_AT_COMMIT' USING ERRCODE='P0001'; END IF;
    IF NOT life_financial.spot_markouts_verified(p_account) THEN RAISE EXCEPTION 'LIFE_MARKOUT_UNVERIFIED_AT_COMMIT' USING ERRCODE='P0001'; END IF;
END; $$;

CREATE FUNCTION life_financial.spot_observe_markout(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,p_command jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts; policy life_financial.spot_markout_policies; previous life_financial.operations;
    original life_financial.spot_fill_facts; intent life_financial.send_attempts; view jsonb; request jsonb; result jsonb;
    op life_financial.identity; trade life_financial.identity; horizon bigint; target bigint; value_at bigint; reference_at bigint;
    independent life_financial.positive_amount; markout life_financial.signed_amount; source_hash life_financial.digest;
    observed timestamptz; at timestamptz; now_ms bigint;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR p_cut IS NULL OR p_fingerprint IS NULL OR p_command IS NULL
        OR NOT life_financial.spot_markout_command_valid(p_command) THEN RAISE EXCEPTION 'LIFE_MARKOUT_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    op:=p_command->>'operation_id';trade:=p_command->>'trade_id';horizon:=(p_command->>'horizon_ms')::bigint;
    IF length(op)>96 OR horizon<=0 THEN RAISE EXCEPTION 'LIFE_MARKOUT_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    request:=jsonb_build_object('kind','SPOT_MARKOUT','binding',p_binding,'owner',p_owner,'command',p_command);
    IF p_fingerprint<>encode(sha256(convert_to(life_financial.canonical_json_v1(request),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'LIFE_MARKOUT_FINGERPRINT_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT * INTO previous FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF previous.command<>request OR previous.fingerprint<>p_fingerprint THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001'; END IF;
        IF NOT life_financial.spot_markouts_verified(p_account) THEN RAISE EXCEPTION 'LIFE_MARKOUT_UNVERIFIED' USING ERRCODE='P0001'; END IF;
        RETURN previous.receipt||'{"replayed":true}'::jsonb;
    END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    view:=life_financial.spot_state(p_account);
    IF view->>'digest'<>p_cut THEN RAISE EXCEPTION 'LIFE_SPOT_CUT_CONFLICT' USING ERRCODE='P0001'; END IF;
    IF (view->>'unverified')::boolean THEN RAISE EXCEPTION 'LIFE_MARKOUT_FINANCIAL_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO policy FROM life_financial.spot_markout_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
    IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_MARKOUT_POLICY_REQUIRED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO original FROM life_financial.spot_fill_facts WHERE account_id=p_account AND trade_id=trade;
    IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_MARKOUT_FILL_NOT_ACCOUNTED' USING ERRCODE='P0001'; END IF;
    IF EXISTS(SELECT 1 FROM life_financial.spot_markout_facts WHERE account_id=p_account AND trade_id=trade AND horizon_ms=horizon) THEN
        RAISE EXCEPTION 'LIFE_MARKOUT_HORIZON_IDENTITY_CONFLICT' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT intent FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=original.intent_id;
    value_at:=(p_command->>'value_at_ms')::bigint;reference_at:=(p_command->>'reference_observed_at_ms')::bigint;
    independent:=(p_command->>'independent_price_usdt')::life_financial.positive_amount;
    observed:=(p_command->>'observed_at')::timestamptz;at:=clock_timestamp();now_ms:=floor(extract(epoch FROM at)*1000)::bigint;
    target:=(original.observation->>'fill_at_ms')::bigint+horizon;
    IF NOT horizon=ANY(policy.horizons_ms) OR value_at<target OR value_at>target+policy.max_horizon_lag_ms
        OR reference_at<value_at OR reference_at>target+policy.max_horizon_lag_ms OR reference_at>now_ms
        OR NOT isfinite(observed) OR observed<to_timestamp(reference_at::double precision/1000) OR observed>at OR extract(epoch FROM at-observed)*1000>policy.max_fact_age_ms THEN
        RAISE EXCEPTION 'LIFE_MARKOUT_VALUE_UNQUALIFIED' USING ERRCODE='P0001'; END IF;
    markout:=(CASE intent.side WHEN 'BUY' THEN 1 ELSE -1 END)*(independent-(original.observation->>'price_usdt')::numeric)
        *(original.observation->>'quantity_base_life')::numeric;
    source_hash:=encode(sha256(convert_to(original.observation::text,'UTF8')),'hex');
    INSERT INTO life_financial.spot_markout_facts VALUES(p_account,trade,horizon,op,p_command,source_hash,markout);
    INSERT INTO life_financial.attribution VALUES(p_account,'SPOT',trade,horizon,to_timestamp(value_at::double precision/1000),markout,source_hash);
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    result:=jsonb_build_object('kind','SPOT_MARKOUT','operation_id',op,'trade_id',trade,'horizon_ms',horizon,'markout_usdt',markout::text,
        'state','RECORDED','version',p_version+1,'replayed',false,'trading_permission',false);
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version)
        VALUES(p_account,op,p_fingerprint,request,result,p_version+1);
    PERFORM life_financial.spot_markout_commit_guard(p_account,request);
    RETURN result;
END; $$;

CREATE INDEX spot_fill_markout_window ON life_financial.spot_fill_facts(account_id,((observation->>'fill_at_ms')::bigint));

CREATE FUNCTION life_financial.spot_markout_frame(p_account uuid,p_rows integer,p_bytes integer) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE view jsonb; policy life_financial.spot_markout_policies; now_ms bigint; count_rows bigint; size_bytes bigint;
    body jsonb; original_fills jsonb; horizons jsonb; earliest bigint;
BEGIN
    IF NOT life_financial.can_access(p_account) OR p_rows IS NULL OR p_rows NOT BETWEEN 1 AND 4096
        OR p_bytes IS NULL OR p_bytes NOT BETWEEN 1 AND 16777216 THEN RAISE EXCEPTION 'LIFE_MARKOUT_READ_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
    view:=life_financial.spot_state(p_account);
    IF (view->>'unverified')::boolean OR NOT life_financial.spot_markouts_verified(p_account) THEN
        RAISE EXCEPTION 'LIFE_MARKOUT_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    SELECT p.* INTO policy FROM life_financial.spot_markout_policies p JOIN life_financial.accounts a USING(account_id,policy_hash) WHERE p.account_id=p_account;
    IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_MARKOUT_POLICY_REQUIRED' USING ERRCODE='P0001'; END IF;
    now_ms:=floor(extract(epoch FROM clock_timestamp())*1000)::bigint;earliest:=now_ms-policy.cohort_window_ms;
    IF EXISTS(SELECT 1 FROM life_financial.spot_markout_facts WHERE account_id=p_account AND (command->>'observed_at')::timestamptz>clock_timestamp()) THEN
        RAISE EXCEPTION 'LIFE_MARKOUT_CLOCK_ROLLBACK' USING ERRCODE='P0001'; END IF;
    WITH bounded AS MATERIALIZED(
        SELECT octet_length(f.observation::text)+512 AS bytes FROM life_financial.spot_fill_facts f
            WHERE f.account_id=p_account AND (f.observation->>'fill_at_ms')::bigint>=earliest LIMIT p_rows+1
    ),marked AS MATERIALIZED(
        SELECT octet_length(m.command::text)+512 AS bytes FROM life_financial.spot_markout_facts m
            JOIN life_financial.spot_fill_facts f USING(account_id,trade_id) WHERE m.account_id=p_account
                AND (f.observation->>'fill_at_ms')::bigint>=earliest LIMIT p_rows+1)
    SELECT count(*),COALESCE(sum(bytes),0) INTO count_rows,size_bytes FROM (SELECT * FROM bounded UNION ALL SELECT * FROM marked) all_rows;
    IF count_rows>p_rows OR size_bytes>p_bytes THEN RAISE EXCEPTION 'LIFE_MARKOUT_READ_BOUND' USING ERRCODE='P0001'; END IF;
    SELECT COALESCE(jsonb_agg(jsonb_build_object('trade_id',f.trade_id,'side',t.side,'quantity_base',f.observation->'quantity_base_life',
        'price_usdt',f.observation->'price_usdt','fill_at_ms',f.observation->'fill_at_ms') ORDER BY f.trade_id),'[]'::jsonb)
        INTO original_fills FROM life_financial.spot_fill_facts f JOIN life_financial.send_attempts t USING(account_id,intent_id)
        WHERE f.account_id=p_account AND (f.observation->>'fill_at_ms')::bigint>=earliest;
    SELECT COALESCE(jsonb_agg(m.command ORDER BY m.trade_id,m.horizon_ms),'[]'::jsonb) INTO horizons FROM life_financial.spot_markout_facts m
        JOIN life_financial.spot_fill_facts f USING(account_id,trade_id) WHERE m.account_id=p_account AND (f.observation->>'fill_at_ms')::bigint>=earliest;
    body:=jsonb_build_object('snapshot',view,'policy',jsonb_build_object('horizons_ms',to_jsonb(policy.horizons_ms),'min_samples',policy.min_samples,
        'size_cutoff_base',policy.size_cutoff_base::text,'cohort_window_ms',policy.cohort_window_ms,'max_horizon_lag_ms',policy.max_horizon_lag_ms,
        'min_mean_markout_quote',policy.min_mean_markout_quote::text,'monitored_cohorts',policy.monitored_cohorts),'now_ms',now_ms,
        'fills',original_fills,'observations',horizons,'trading_permission',false);
    IF octet_length(body::text)>p_bytes THEN RAISE EXCEPTION 'LIFE_MARKOUT_READ_BOUND' USING ERRCODE='P0001'; END IF;
    RETURN body;
END; $$;

CREATE OR REPLACE FUNCTION life_financial.spot_state(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE body jsonb; verified boolean; pending boolean; ingress_verified boolean; ingress_pending boolean; financial_verified boolean; financial_pending boolean; markout_verified boolean;
BEGIN
    body:=life_financial.spot_state_v15(p_account)-'digest'-'observed_at';
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
    RETURN body||jsonb_build_object('digest',encode(sha256(convert_to(body::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
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
    IF op.command->>'kind' IN ('RECORDER_POSITION_STAGE','RECORDER_POSITION_APPLY','RECORDER_EVENT_STAGE','RECORDER_EVENT_APPLY','RECORDER_CONFIG_STAGE','RECORDER_CONFIG_APPLY','RECORDER_STAGE','RECORDER_APPLY','SPOT_MARKOUT','SPOT_INVENTORY_SETTLE','SPOT_ACK','SPOT_FILL','SPOT_CASHFLOW','SPOT_NAV','SPOT_SESSION_TICK','SPOT_LIFECYCLE','SPOT_TERMINAL') OR op.command->>'kind' IN ('SPOT_ACTION_TRANSITION','SPOT_ACTION_ABANDON')
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


DO $scope$
DECLARE name text;
BEGIN
    FOREACH name IN ARRAY ARRAY['spot_markout_policies','spot_markout_facts'] LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',name);
        EXECUTE format('CREATE POLICY scoped_read ON life_financial.%I FOR SELECT USING(life_financial.can_access(account_id))',name);
        EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',name);
    END LOOP;
END; $scope$;
CREATE FUNCTION life_financial.spot_markout_projection_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF NOT life_financial.spot_markouts_verified(NEW.account_id) THEN RAISE EXCEPTION 'LIFE_MARKOUT_UNVERIFIED_AT_COMMIT' USING ERRCODE='P0001'; END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER markout_fact_commit AFTER INSERT ON life_financial.spot_markout_facts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.spot_markout_projection_guard();
CREATE CONSTRAINT TRIGGER markout_account_commit AFTER UPDATE ON life_financial.accounts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.spot_markout_projection_guard();
REVOKE ALL ON life_financial.spot_markout_policies,life_financial.spot_markout_facts FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
