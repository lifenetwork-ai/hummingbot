CREATE FUNCTION life_financial.risk_stress_source_valid(p_command jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE key text;number numeric;op life_financial.identity;
BEGIN
    IF p_command IS NULL OR jsonb_typeof(p_command)<>'object'
        OR (SELECT count(*) FROM jsonb_object_keys(p_command))<>11 OR NOT p_command ?& ARRAY['operation_id','value_at_ms','observed_at_ms','source_kind',
            'reference_usdt','stressed_exit_usdt','stressed_high_usdt','exit_depth_base','collateral_quote','maker_fee_rate','exit_fee_rate'] THEN RETURN false;END IF;
    IF p_command->>'source_kind' IS DISTINCT FROM 'independent_market' THEN RETURN false;END IF;
    FOREACH key IN ARRAY ARRAY['value_at_ms','observed_at_ms'] LOOP
        IF jsonb_typeof(p_command->key) IS DISTINCT FROM 'number' OR p_command->>key !~ '^[1-9][0-9]*$'
            OR (p_command->>key)::bigint>253402300799999 THEN RETURN false;END IF;
    END LOOP;
    FOREACH key IN ARRAY ARRAY['reference_usdt','stressed_exit_usdt','stressed_high_usdt','exit_depth_base','collateral_quote','maker_fee_rate','exit_fee_rate'] LOOP
        IF jsonb_typeof(p_command->key) IS DISTINCT FROM 'string' THEN RETURN false;END IF;
        number:=(p_command->>key)::life_financial.amount;
        IF key IN ('reference_usdt','stressed_exit_usdt','stressed_high_usdt') AND number<=0 THEN RETURN false;END IF;
    END LOOP;
    IF (p_command->>'stressed_exit_usdt')::numeric>(p_command->>'reference_usdt')::numeric
        OR (p_command->>'stressed_high_usdt')::numeric<(p_command->>'reference_usdt')::numeric THEN
        RAISE EXCEPTION 'LIFE_STRESS_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
    op:=p_command->>'operation_id';
    RETURN op IS NOT NULL AND length(op)<=96;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
-- Native exact reservation/fill-derived stress, no caller inventory/netting.
CREATE TABLE life_financial.risk_stress_policies (
    account_id uuid PRIMARY KEY REFERENCES life_financial.risk_service_policies,
    policy_hash life_financial.digest NOT NULL,window_ms bigint NOT NULL CHECK(window_ms BETWEEN 1 AND 253402300799999),
    max_filled_base life_financial.positive_amount NOT NULL,target_inventory_base life_financial.amount NOT NULL,
    max_stress_loss_quote life_financial.positive_amount NOT NULL,
    max_observation_age_ms integer NOT NULL CHECK(max_observation_age_ms BETWEEN 1 AND 60000),
    FOREIGN KEY(account_id,policy_hash) REFERENCES life_financial.policies
);
CREATE TABLE life_financial.risk_stress_events (
    account_id uuid NOT NULL REFERENCES life_financial.risk_stress_policies,operation_id life_financial.identity NOT NULL,
    event_version bigint NOT NULL CHECK(event_version>0),body jsonb NOT NULL CHECK(jsonb_typeof(body)='object'),source_hash life_financial.digest NOT NULL,
    PRIMARY KEY(account_id,operation_id),UNIQUE(account_id,event_version),
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED
);
CREATE FUNCTION life_financial.risk_stress_evaluate(p_account uuid,source jsonb,p_candidate text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.risk_stress_policies;t life_financial.send_attempts;inventory numeric;cash numeric;buys numeric;sells numeric;
    buy_price numeric;sell_price numeric;fees numeric;worst numeric;filled numeric;recent numeric;pending numeric;reason text:='STRESS_WITHIN_LIMITS';
    reference numeric;exit_price numeric;high_price numeric;at_ms bigint;
BEGIN
    SELECT s.* INTO STRICT p FROM life_financial.risk_stress_policies s JOIN life_financial.accounts a USING(account_id,policy_hash) WHERE s.account_id=p_account;
    at_ms:=floor(extract(epoch FROM clock_timestamp())*1000)::bigint;
    IF source IS NULL OR source->>'source_kind'<>'independent_market'
        OR NOT 0<(source->>'value_at_ms')::bigint OR (source->>'value_at_ms')::bigint>(source->>'observed_at_ms')::bigint
        OR (source->>'observed_at_ms')::bigint>at_ms OR at_ms-(source->>'value_at_ms')::bigint>p.max_observation_age_ms
        OR EXISTS(SELECT 1 FROM life_financial.fills WHERE account_id=p_account AND fill_at>clock_timestamp()) THEN
        RETURN jsonb_build_object('allowed',false,'reason_code','SPOT_RISK_UNAVAILABLE','worst_loss_quote',NULL,'trading_permission',false);END IF;
    reference:=(source->>'reference_usdt')::numeric;exit_price:=(source->>'stressed_exit_usdt')::numeric;high_price:=(source->>'stressed_high_usdt')::numeric;
    SELECT total INTO STRICT inventory FROM life_financial.balances WHERE account_id=p_account AND asset='LIFE';
    SELECT total-liability INTO STRICT cash FROM life_financial.balances WHERE account_id=p_account AND asset='USDT';
    WITH unresolved AS (
        SELECT x.side,greatest(0,x.quantity-COALESCE((SELECT sum(f.quantity) FROM life_financial.fills f
            WHERE f.account_id=x.account_id AND f.product=x.product AND f.wire_id=x.wire_id),0)) AS remaining,x.price
        FROM life_financial.send_attempts x WHERE x.account_id=p_account AND x.product='SPOT' AND x.state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND')
    ) SELECT COALESCE(sum(remaining) FILTER(WHERE side='BUY'),0),COALESCE(sum(remaining) FILTER(WHERE side='SELL'),0),
        COALESCE(max(price) FILTER(WHERE side='BUY'),reference),COALESCE(min(price) FILTER(WHERE side='SELL'),reference)
        INTO buys,sells,buy_price,sell_price FROM unresolved;
    IF p_candidate IS NOT NULL THEN
        SELECT * INTO STRICT t FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=p_candidate AND product='SPOT';
        SELECT COALESCE(sum(f.quantity),0) INTO recent FROM life_financial.fills f JOIN life_financial.send_attempts x USING(account_id,product,wire_id)
            WHERE f.account_id=p_account AND f.product='SPOT' AND x.side=t.side AND f.fill_at<=clock_timestamp()
                AND at_ms-floor(extract(epoch FROM f.fill_at)*1000)::bigint<p.window_ms;
        pending:=CASE t.side WHEN 'BUY' THEN buys ELSE sells END;
        IF (CASE t.side WHEN 'BUY' THEN inventory+buys>p.target_inventory_base ELSE inventory-sells<p.target_inventory_base END)
            AND recent+pending>p.max_filled_base THEN reason:='ROLLING_FILL_CAPACITY_EXHAUSTED';END IF;
    END IF;
    IF reason='STRESS_WITHIN_LIMITS' AND inventory+buys>(source->>'exit_depth_base')::numeric THEN reason:='STRESS_EXIT_DEPTH_UNAVAILABLE';END IF;
    IF reference<=0 OR exit_price<=0 OR high_price<reference OR exit_price>reference OR sells>inventory THEN reason:='STRESS_INPUT_UNAVAILABLE';END IF;
    fees:=greatest(buys*buy_price,sells*sell_price)*(source->>'maker_fee_rate')::numeric
        +(inventory+buys)*exit_price*(source->>'exit_fee_rate')::numeric;
    worst:=greatest(0,inventory*reference-((inventory+buys)*exit_price-buys*buy_price),
        inventory*high_price-((inventory-sells)*high_price+sells*sell_price))+fees;
    IF reason='STRESS_WITHIN_LIMITS' THEN
        IF worst>p.max_stress_loss_quote THEN reason:='STRESS_LOSS_EXCEEDED';
        ELSIF worst>least((source->>'collateral_quote')::numeric,cash) THEN reason:='COLLATERAL_BUFFER_EXCEEDED';END IF;
    END IF;
    RETURN jsonb_build_object('allowed',reason='STRESS_WITHIN_LIMITS','reason_code',reason,'worst_loss_quote',worst::text,'trading_permission',false);
END; $$;
DO $$ DECLARE t text;BEGIN
    FOREACH t IN ARRAY ARRAY['risk_stress_policies','risk_stress_events'] LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY scoped_read ON life_financial.%I FOR SELECT USING(life_financial.can_access(account_id))',t);
        EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',t);
    END LOOP;
END; $$;

CREATE FUNCTION life_financial.risk_stress_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.risk_service_policies;e life_financial.risk_stress_events;prior jsonb;prior_hash jsonb;ordinal bigint:=0;o life_financial.operations;
BEGIN
    IF NOT EXISTS(SELECT 1 FROM life_financial.risk_stress_policies WHERE account_id=p_account) THEN
        RETURN NOT EXISTS(SELECT 1 FROM life_financial.risk_stress_events WHERE account_id=p_account);END IF;
    SELECT * INTO STRICT p FROM life_financial.risk_service_policies WHERE account_id=p_account;
    IF NOT EXISTS(SELECT 1 FROM life_financial.risk_stress_policies s JOIN life_financial.accounts a USING(account_id,policy_hash) WHERE s.account_id=p_account)
        OR EXISTS(SELECT 1 FROM life_financial.operations original_op WHERE original_op.account_id=p_account AND original_op.command->>'kind'='STRESS_OBSERVE'
            AND NOT EXISTS(SELECT 1 FROM life_financial.risk_stress_events hist WHERE hist.account_id=p_account AND hist.operation_id=original_op.operation_id)) THEN RETURN false;END IF;
    IF (SELECT count(*) FROM life_financial.risk_stress_events WHERE account_id=p_account)>p.max_events
        OR COALESCE((SELECT sum(octet_length(body::text)) FROM life_financial.risk_stress_events WHERE account_id=p_account),0)>p.max_total_bytes THEN RETURN false;END IF;
    FOR e IN SELECT * FROM life_financial.risk_stress_events WHERE account_id=p_account ORDER BY event_version LOOP
        SELECT * INTO o FROM life_financial.operations WHERE account_id=p_account AND operation_id=e.operation_id;
        IF NOT FOUND OR e.event_version<>ordinal+1
            OR (SELECT count(*) FROM jsonb_object_keys(e.body))<>9
            OR NOT e.body ?& ARRAY['request','receipt','previous','risk_epoch','observer_id','last_seen_ms','rollback_latched','policy','observed_clock_ms']
            OR jsonb_typeof(e.body->'risk_epoch') IS DISTINCT FROM 'number' OR e.body->>'risk_epoch' !~ '^[0-9]+$'
            OR jsonb_typeof(e.body->'observer_id') IS DISTINCT FROM 'string' OR (e.body->>'observer_id')::uuid IS NULL
            OR jsonb_typeof(e.body->'rollback_latched') IS DISTINCT FROM 'boolean'
            OR (e.body->>'last_seen_ms')::bigint IS DISTINCT FROM greatest((e.body->>'observed_clock_ms')::bigint,(prior->>'last_seen_ms')::bigint)
            OR (e.body->>'rollback_latched')::boolean IS DISTINCT FROM (COALESCE((prior->>'rollback_latched')::boolean,false) OR (e.body->>'observed_clock_ms')::bigint<(e.body->>'last_seen_ms')::bigint) OR e.source_hash<>encode(sha256(convert_to(e.body::text,'UTF8')),'hex')
            OR e.body->'previous' IS DISTINCT FROM COALESCE(prior_hash,'null'::jsonb)
            OR NOT life_financial.risk_stress_source_valid(o.command->'command') OR e.body->'request' IS DISTINCT FROM o.command OR e.body->'receipt' IS DISTINCT FROM o.receipt
            OR e.body->'policy' IS DISTINCT FROM (SELECT to_jsonb(s) FROM life_financial.risk_stress_policies s WHERE account_id=p_account)
            OR o.command->>'kind' IS DISTINCT FROM 'STRESS_OBSERVE' OR o.command#>>'{command,operation_id}' IS DISTINCT FROM e.operation_id
            OR (o.receipt->>'event_version')::bigint<>e.event_version OR o.receipt->'trading_permission' IS DISTINCT FROM 'false'::jsonb
            OR (e.body->>'last_seen_ms')::bigint<(prior->>'last_seen_ms')::bigint
            OR prior->'rollback_latched'='true'::jsonb AND e.body->'rollback_latched'<>'true'::jsonb THEN RETURN false;END IF;
        ordinal:=e.event_version;prior:=e.body;prior_hash:=to_jsonb(e.source_hash);
    END LOOP;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.risk_stress_command(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,p_command jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts;p life_financial.risk_service_policies;sp life_financial.risk_stress_policies;old life_financial.operations;
    prior life_financial.risk_stress_events;request jsonb;result jsonb;body jsonb;decision jsonb;op life_financial.identity;
    at_ms bigint;ordinal bigint;last_seen bigint;rollback boolean;observer uuid;key text;number numeric;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR p_cut IS NULL OR p_fingerprint IS NULL
        OR NOT life_financial.risk_stress_source_valid(p_command) THEN
        RAISE EXCEPTION 'LIFE_STRESS_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
    op:=p_command->>'operation_id';IF length(op)>96 THEN RAISE EXCEPTION 'LIFE_STRESS_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    SELECT * INTO STRICT p FROM life_financial.risk_service_policies WHERE account_id=p_account;
    SELECT * INTO STRICT sp FROM life_financial.risk_stress_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
    request:=jsonb_build_object('kind','STRESS_OBSERVE','binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',p_command);
    SELECT * INTO old FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF old.command IS DISTINCT FROM request OR old.fingerprint<>p_fingerprint OR NOT life_financial.risk_stress_verified(p_account) THEN
            RAISE EXCEPTION 'LIFE_STRESS_OPERATION_CONFLICT' USING ERRCODE='P0001';END IF;
        RETURN old.receipt||'{"replayed":true}'::jsonb;
    END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    IF NOT life_financial.risk_stress_verified(p_account) THEN RAISE EXCEPTION 'LIFE_STRESS_UNVERIFIED' USING ERRCODE='P0001';END IF;
    IF (life_financial.spot_state(p_account)->>'digest') IS DISTINCT FROM p_cut THEN RAISE EXCEPTION 'LIFE_STRESS_CUT_STALE' USING ERRCODE='P0001';END IF;
    SELECT * INTO prior FROM life_financial.risk_stress_events WHERE account_id=p_account ORDER BY event_version DESC LIMIT 1;
    ordinal:=COALESCE(prior.event_version,0)+1;at_ms:=floor(extract(epoch FROM clock_timestamp())*1000)::bigint;
    last_seen:=greatest(at_ms,(prior.body->>'last_seen_ms')::bigint);rollback:=COALESCE((prior.body->>'rollback_latched')::boolean,false) OR at_ms<last_seen;
    observer:=current_setting('life.risk_observer',true)::uuid;
    IF observer IS NULL THEN RAISE EXCEPTION 'LIFE_STRESS_OBSERVER_REQUIRED' USING ERRCODE='P0001';END IF;
    decision:=life_financial.risk_stress_evaluate(p_account,p_command,NULL);
    IF rollback THEN decision:=jsonb_build_object('allowed',false,'reason_code','SPOT_RISK_CLOCK_ROLLBACK','worst_loss_quote',NULL,'trading_permission',false);END IF;
    result:=jsonb_build_object('kind','STRESS_OBSERVE','operation_id',op,'event_version',ordinal,'decision',decision,'version',a.version+1,
        'replayed',false,'trading_permission',false);
    -- Retain a fixed-size predecessor hash, never nest the full prior body.
    body:=jsonb_build_object('request',request,'receipt',result,'previous',prior.source_hash,'risk_epoch',a.risk_epoch,
        'observer_id',observer,'observed_clock_ms',at_ms,'last_seen_ms',last_seen,'rollback_latched',rollback,'policy',to_jsonb(sp));
    IF ordinal>p.max_events OR COALESCE((SELECT sum(octet_length(e.body::text)) FROM life_financial.risk_stress_events e WHERE account_id=p_account),0)+octet_length(body::text)>p.max_total_bytes THEN
        RAISE EXCEPTION 'LIFE_STRESS_HISTORY_BOUND' USING ERRCODE='P0001';END IF;
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    INSERT INTO life_financial.risk_stress_events VALUES(p_account,op,ordinal,body,encode(sha256(convert_to(body::text,'UTF8')),'hex'));
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version)
        VALUES(p_account,op,p_fingerprint,request,result,a.version+1);
    RETURN result;
END; $$;
CREATE FUNCTION life_financial.risk_stress_admit(p_account uuid,op life_financial.operations) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts;e life_financial.risk_stress_events;reason text;decision jsonb;source jsonb;reference numeric;
BEGIN
    IF NOT EXISTS(SELECT 1 FROM life_financial.risk_stress_policies WHERE account_id=p_account) THEN RETURN;END IF;
    IF NOT life_financial.risk_stress_verified(p_account) THEN RAISE EXCEPTION 'LIFE_STRESS_UNVERIFIED' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account;
    SELECT * INTO e FROM life_financial.risk_stress_events WHERE account_id=p_account ORDER BY event_version DESC LIMIT 1;
    IF e.account_id IS NULL OR e.body#>>'{request,owner,generation}'<>a.owner_generation::text
        OR e.body#>>'{request,owner,session_id}'<>a.current_session::text
        OR e.body->>'observer_id' IS DISTINCT FROM current_setting('life.risk_observer',true)
        OR e.body->'rollback_latched'='true'::jsonb OR floor(extract(epoch FROM clock_timestamp())*1000)::bigint<(e.body->>'last_seen_ms')::bigint
        OR (e.body->>'risk_epoch')::bigint IS DISTINCT FROM a.risk_epoch THEN RAISE EXCEPTION 'LIFE_STRESS_FRESH_OBSERVATION_REQUIRED' USING ERRCODE='P0001';END IF;
    source:=e.body#>'{request,command}';
    IF EXISTS(SELECT 1 FROM life_financial.shared_capital_policies WHERE account_id=p_account) THEN
        SELECT (n.basis->>'index')::numeric INTO reference FROM life_financial.portfolio_observations n JOIN life_financial.operations o USING(account_id,operation_id)
            WHERE n.account_id=p_account ORDER BY o.committed_version DESC LIMIT 1;
    ELSE SELECT (command->>'independent_price_usdt')::numeric INTO reference FROM life_financial.spot_nav_observations
        WHERE account_id=p_account ORDER BY sequence DESC LIMIT 1;END IF;
    IF reference IS NULL OR reference IS DISTINCT FROM (source->>'reference_usdt')::numeric THEN
        RAISE EXCEPTION 'LIFE_STRESS_REFERENCE_MISMATCH' USING ERRCODE='P0001';END IF;
    decision:=life_financial.risk_stress_evaluate(p_account,source,op.receipt->>'intent_id');reason:=decision->>'reason_code';
    IF decision->'allowed' IS DISTINCT FROM 'true'::jsonb THEN
        RAISE EXCEPTION 'LIFE_%',reason USING ERRCODE='P0001';END IF;
END; $$;

CREATE FUNCTION life_financial.risk_stress_enabled(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_ACCESS_DENIED' USING ERRCODE='P0001';END IF;
    RETURN EXISTS(SELECT 1 FROM life_financial.risk_stress_policies WHERE account_id=p_account);
END; $$;
CREATE FUNCTION life_financial.risk_stress_frame(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE e life_financial.risk_stress_events;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_ACCESS_DENIED' USING ERRCODE='P0001';END IF;
    IF NOT life_financial.risk_stress_verified(p_account) THEN RAISE EXCEPTION 'LIFE_STRESS_UNVERIFIED' USING ERRCODE='P0001';END IF;
    SELECT * INTO e FROM life_financial.risk_stress_events WHERE account_id=p_account ORDER BY event_version DESC LIMIT 1;
    IF NOT FOUND THEN RETURN jsonb_build_object('kind','STRESS_OBSERVE','operation_id',NULL,'event_version',0,'replayed',true,
        'decision',jsonb_build_object('allowed',false,'reason_code','SPOT_RISK_REVALIDATION','worst_loss_quote',NULL),'trading_permission',false);END IF;
    RETURN e.body->'receipt'||jsonb_build_object('replayed',true);
END; $$;
CREATE FUNCTION life_financial.risk_stress_commit_check(p_account uuid,op life_financial.operations) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE e life_financial.risk_stress_events;p life_financial.risk_stress_policies;now_ms bigint;
BEGIN
    IF NOT life_financial.risk_stress_verified(p_account) THEN RAISE EXCEPTION 'LIFE_STRESS_UNVERIFIED' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT e FROM life_financial.risk_stress_events WHERE account_id=p_account AND operation_id=op.operation_id;
    SELECT * INTO STRICT p FROM life_financial.risk_stress_policies WHERE account_id=p_account;
    now_ms:=floor(extract(epoch FROM clock_timestamp())*1000)::bigint;
    IF e.body#>'{receipt,decision,allowed}'='true'::jsonb AND (now_ms<(e.body->>'last_seen_ms')::bigint
        OR now_ms-(e.body#>>'{request,command,value_at_ms}')::bigint>p.max_observation_age_ms) THEN
        RAISE EXCEPTION 'LIFE_STRESS_SOURCE_STALE_AT_COMMIT' USING ERRCODE='P0001';END IF;
END; $$;
CREATE FUNCTION life_financial.risk_stress_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE op life_financial.operations;
BEGIN
    IF NOT EXISTS(SELECT 1 FROM life_financial.risk_stress_policies WHERE account_id=NEW.account_id) THEN RETURN NULL;END IF;
    SELECT * INTO STRICT op FROM life_financial.operations WHERE account_id=NEW.account_id AND committed_version=NEW.version;
    IF op.command->>'kind'='STRESS_OBSERVE' THEN PERFORM life_financial.risk_stress_commit_check(NEW.account_id,op);
    ELSIF op.receipt->>'kind' IN ('SPOT_PREPARE','SPOT_ARM','SPOT_QUOTE_ARM') THEN
        PERFORM life_financial.risk_stress_admit(NEW.account_id,op);
    END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER zzzz_native_stress_commit AFTER UPDATE ON life_financial.accounts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN(NEW.version<>OLD.version) EXECUTE FUNCTION life_financial.risk_stress_commit_guard();
DO $$ DECLARE f record;definition text;needle text;BEGIN
    SELECT * INTO STRICT f FROM pg_proc WHERE oid='life_financial.commit_owner_guard()'::regprocedure;
    IF NOT f.prosecdef OR f.provolatile<>'v' OR f.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
        OR encode(sha256(convert_to(f.prosrc,'UTF8')),'hex')<>'6dd62fe7b425bc7aa21acff92289d3c0753a323dc3f8b9bca8cd168124f70bbf' THEN
        RAISE EXCEPTION 'LIFE_STRESS_MIGRATION_MISMATCH';END IF;
    definition:=pg_get_functiondef(f.oid);needle:='(''RISK_OBSERVE'',''TERMINAL_REPAIR''';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_STRESS_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,'(''STRESS_OBSERVE'',''RISK_OBSERVE'',''TERMINAL_REPAIR''');
END; $$;
DO $$ DECLARE f record;r record;BEGIN
    FOR f IN SELECT p.oid::regprocedure AS signature,p.proowner FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='life_financial' AND p.proname LIKE 'risk_stress_%' LOOP
        FOR r IN SELECT DISTINCT x.grantee FROM pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
            WHERE p.oid=f.signature AND x.grantee<>f.proowner LOOP
            IF r.grantee=0 THEN EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',f.signature);
            ELSE EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',f.signature,pg_get_userbyid(r.grantee));END IF;
        END LOOP;
    END LOOP;
    FOR r IN SELECT DISTINCT principal FROM life_financial.access_bindings WHERE has_function_privilege(principal,'life_financial.spot_state(uuid)','EXECUTE') LOOP
        EXECUTE format('GRANT EXECUTE ON FUNCTION life_financial.risk_stress_enabled(uuid) TO %I',r.principal);
    END LOOP;
END; $$;
