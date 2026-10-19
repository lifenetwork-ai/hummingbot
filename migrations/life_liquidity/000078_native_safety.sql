-- Explicit native safety policy and immutable decisions. No bootstrap or live defaults.
CREATE TABLE life_financial.risk_service_policies (
    account_id uuid PRIMARY KEY REFERENCES life_financial.accounts,policy_hash life_financial.digest NOT NULL,
    opening_risk_epoch bigint NOT NULL CHECK(opening_risk_epoch>=0),
    max_drawdown_bps numeric NOT NULL CHECK(max_drawdown_bps>0 AND max_drawdown_bps<=10000),
    min_margin_buffer_quote life_financial.amount NOT NULL,
    stable_data_ms integer NOT NULL CHECK(stable_data_ms BETWEEN 0 AND 60000),
    recovery_probe_base life_financial.positive_amount NOT NULL,
    recovery_probe_campaign_base life_financial.positive_amount,
    max_observation_age_ms integer NOT NULL CHECK(max_observation_age_ms BETWEEN 1 AND 60000),
    max_value_age_ms integer NOT NULL CHECK(max_value_age_ms BETWEEN 1 AND 60000),
    max_events integer NOT NULL CHECK(max_events BETWEEN 1 AND 4096),
    max_total_bytes integer NOT NULL CHECK(max_total_bytes BETWEEN 1 AND 16777216),
    CHECK(recovery_probe_base<=recovery_probe_campaign_base),
    FOREIGN KEY(account_id,policy_hash) REFERENCES life_financial.policies
);
CREATE TABLE life_financial.risk_events (
    account_id uuid NOT NULL REFERENCES life_financial.risk_service_policies,
    operation_id life_financial.identity NOT NULL,event_version bigint NOT NULL CHECK(event_version>0),
    risk_epoch bigint NOT NULL CHECK(risk_epoch>0),
    body jsonb NOT NULL CHECK(jsonb_typeof(body)='object'),source_hash life_financial.digest NOT NULL,
    PRIMARY KEY(account_id,operation_id),UNIQUE(account_id,event_version),
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED
);
DO $$ DECLARE t text;BEGIN
    FOREACH t IN ARRAY ARRAY['risk_service_policies','risk_events'] LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY scoped_read ON life_financial.%I FOR SELECT USING(life_financial.can_access(account_id))',t);
        EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',t);
    END LOOP;
END; $$;

CREATE FUNCTION life_financial.risk_source(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.risk_service_policies;n life_financial.spot_nav_observations;v jsonb;now_ms bigint;stamp bigint;
    portfolio jsonb;carry life_financial.carry_checks;terms jsonb;claims jsonb;capital jsonb;margin numeric;
    latest life_financial.portfolio_observations;account_version bigint;
BEGIN
    SELECT * INTO STRICT p FROM life_financial.risk_service_policies WHERE account_id=p_account;
    now_ms:=floor(extract(epoch FROM clock_timestamp())*1000)::bigint;
    IF EXISTS(SELECT 1 FROM life_financial.shared_capital_policies WHERE account_id=p_account) THEN
        -- A shared account cannot substitute SPOT-only NAV for the portfolio.
        -- A reservation changes holds/capital operation IDs, not settled NAV.
        -- Recheck original native money, positions and qualified source instead
        -- of requiring a new NAV/risk epoch for the reservation's own COMMIT.
        IF NOT life_financial.portfolio_verified(p_account) THEN RETURN NULL;END IF;
        SELECT x.* INTO latest FROM life_financial.portfolio_observations x JOIN life_financial.operations o USING(account_id,operation_id)
            WHERE x.account_id=p_account ORDER BY o.committed_version DESC LIMIT 1;
        IF NOT FOUND THEN RETURN NULL;END IF;
        SELECT version INTO STRICT account_version FROM life_financial.accounts WHERE account_id=p_account;
        IF latest.basis->'money' IS DISTINCT FROM life_financial.portfolio_money_at(p_account,account_version)
            OR latest.basis->'positions' IS DISTINCT FROM life_financial.portfolio_positions_at(p_account,account_version)
            OR NOT life_financial.portfolio_source_current(p_account,latest.basis,now_ms) THEN RETURN NULL;END IF;
        portfolio:=jsonb_build_object('operation_id',latest.operation_id,'basis',latest.basis,'measurement',latest.measurement);
        stamp:=(portfolio#>>'{basis,observed_at_ms}')::bigint;
        IF stamp>now_ms OR now_ms-stamp>p.max_value_age_ms THEN RETURN NULL;END IF;
        SELECT * INTO STRICT carry FROM life_financial.carry_checks WHERE account_id=p_account
            AND operation_id=portfolio#>>'{basis,carry_operation_id}';
        SELECT body INTO STRICT terms FROM life_financial.shared_capital_policies WHERE account_id=p_account;
        SELECT COALESCE(jsonb_agg(life_financial.shared_claim_body(c) ORDER BY c.intent_id),'[]'::jsonb) INTO claims
            FROM life_financial.shared_capital_claims c WHERE c.account_id=p_account
                AND life_financial.shared_claim_active_at(p_account,c.intent_id,9223372036854775807);
        capital:=life_financial.shared_evaluate(terms,carry.bundle#>'{account,snapshot}',claims);
        IF NOT capital ?& ARRAY['initial_buffer_quote','maintenance_buffer_quote'] THEN RETURN NULL;END IF;
        margin:=least((capital->>'initial_buffer_quote')::numeric,(capital->>'maintenance_buffer_quote')::numeric);
        RETURN jsonb_build_object('kind','PORTFOLIO','operation_id',portfolio->'operation_id','basis',portfolio->'basis',
            'measurement',portfolio->'measurement','drawdown_bps',portfolio#>>'{measurement,drawdown_bps}',
            'margin_buffer_quote',margin::text,'value_at_ms',stamp);
    END IF;
    SELECT * INTO n FROM life_financial.spot_nav_observations WHERE account_id=p_account ORDER BY sequence DESC LIMIT 1;
    IF NOT FOUND THEN RETURN NULL;END IF;
    v:=life_financial.spot_capital_read(p_account,NULL);
    IF v->'current' IS DISTINCT FROM 'true'::jsonb OR (v#>>'{snapshot,unverified}')::boolean THEN RETURN NULL;END IF;
    FOREACH stamp IN ARRAY ARRAY[(n.command->>'value_at_ms')::bigint,(n.command->>'reference_observed_at_ms')::bigint] LOOP
        IF stamp>now_ms OR now_ms-stamp>p.max_value_age_ms THEN RETURN NULL;END IF;
    END LOOP;
    -- Principal holds must not invalidate their own preparation before arm.
    -- Existing financial guards separately enforce available cash and fees.
    SELECT total-liability INTO STRICT margin FROM life_financial.balances WHERE account_id=p_account AND asset='USDT';
    RETURN jsonb_build_object('kind','SPOT','operation_id',n.operation_id,'money_digest',n.money_digest,
        'measurement',n.measurement,'drawdown_bps',n.measurement->>'drawdown_bps','margin_buffer_quote',margin::text,
        'value_at_ms',(n.command->>'value_at_ms')::bigint);
EXCEPTION WHEN no_data_found THEN RETURN NULL;
END; $$;

CREATE FUNCTION life_financial.risk_decide(p jsonb,previous jsonb,source jsonb,command jsonb,at_ms bigint,owner jsonb,observer text,halted boolean)
RETURNS jsonb LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE state text:='PAUSED';reason text:='STARTUP_REVALIDATION';good_since bigint;last_seen bigint;
    manual boolean:=false;rollback boolean:=false;stamp bigint;pair record;
BEGIN
    IF previous IS NOT NULL THEN
        last_seen:=(previous->>'last_seen_ms')::bigint;
        rollback:=COALESCE((previous->>'rollback_latched')::boolean,false) OR at_ms<last_seen;
        IF previous->>'generation'=owner->>'generation' AND previous->>'session_id'=owner->>'session_id' THEN
            state:=previous->>'state';good_since:=(previous->>'good_since_ms')::bigint;
            manual:=COALESCE((previous->>'manual_rearm_required')::boolean,false) OR previous->>'observer_id'<>observer;
            IF at_ms-last_seen>(p->>'max_observation_age_ms')::bigint THEN state:='PAUSED';good_since:=NULL;END IF;
        END IF;
    END IF;
    stamp:=floor(extract(epoch FROM (command->>'observed_at')::timestamptz)*1000)::bigint;
    IF halted OR previous->>'state'='HALTED' THEN state:='HALTED';
        reason:=CASE WHEN previous->>'state'='HALTED' THEN previous->>'reason_code' ELSE 'HALT_LATCHED' END;good_since:=NULL;
    ELSIF rollback THEN state:='PAUSED';reason:='SAFETY_CLOCK_INVALID';good_since:=NULL;
    ELSIF manual THEN state:='PAUSED';reason:='MANUAL_REARM_REQUIRED';good_since:=NULL;
    ELSIF source IS NULL OR source='null'::jsonb OR stamp>at_ms OR at_ms-stamp>(p->>'max_observation_age_ms')::bigint THEN
        state:='PAUSED';reason:='RISK_DATA_UNAVAILABLE';good_since:=NULL;
    ELSIF (source->>'drawdown_bps')::numeric>(p->>'max_drawdown_bps')::numeric THEN
        state:='HALTED';reason:='DRAWDOWN_LIMIT_BREACHED';good_since:=NULL;
    ELSIF (source->>'margin_buffer_quote')::numeric<(p->>'min_margin_buffer_quote')::numeric THEN
        state:='HALTED';reason:='MARGIN_BUFFER_BREACHED';good_since:=NULL;
    ELSE
        FOR pair IN SELECT * FROM (VALUES ('account_ready','ACCOUNT_DATA_UNAVAILABLE'),('market_data_fresh','MARKET_DATA_STALE'),
            ('latency_ok','LATENCY_EXCEEDED'),('model_ready','MODEL_DIVERGENCE')) x(flag,failure) LOOP
            IF command->pair.flag IS DISTINCT FROM 'true'::jsonb THEN reason:=pair.failure;EXIT;END IF;
        END LOOP;
        IF reason<>'STARTUP_REVALIDATION' THEN state:='PAUSED';good_since:=NULL;
        ELSIF state='PAUSED' THEN
            good_since:=COALESCE(good_since,at_ms);
            IF at_ms-good_since<(p->>'stable_data_ms')::bigint THEN reason:='STABLE_DATA_WAIT';
            ELSE state:='DEGRADED';reason:='RECOVERY_PROBE';END IF;
        ELSIF state='DEGRADED' AND at_ms-good_since>=2*(p->>'stable_data_ms')::bigint THEN state:='NORMAL';reason:='SAFETY_READY';
        ELSE reason:=CASE state WHEN 'NORMAL' THEN 'SAFETY_READY' ELSE 'RECOVERY_PROBE' END;END IF;
    END IF;
    RETURN jsonb_build_object('state',state,'reason_code',reason,'good_since_ms',good_since,'last_seen_ms',greatest(at_ms,last_seen),
        'rollback_latched',rollback,'manual_rearm_required',manual,'observer_id',observer,
        'generation',owner->'generation','session_id',owner->'session_id');
END; $$;

CREATE FUNCTION life_financial.risk_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.risk_service_policies;e life_financial.risk_events;o life_financial.operations;prior jsonb;expected jsonb;
    n bigint;bytes bigint;epoch bigint:=0;ordinal bigint:=0;
BEGIN
    SELECT * INTO p FROM life_financial.risk_service_policies WHERE account_id=p_account;
    IF NOT FOUND THEN RETURN NOT EXISTS(SELECT 1 FROM life_financial.risk_events WHERE account_id=p_account);END IF;
    epoch:=p.opening_risk_epoch;
    IF p.policy_hash<>(SELECT policy_hash FROM life_financial.accounts WHERE account_id=p_account) THEN RETURN false;END IF;
    SELECT count(*),COALESCE(sum(octet_length(body::text)),0) INTO n,bytes FROM life_financial.risk_events WHERE account_id=p_account;
    IF n>p.max_events OR bytes>p.max_total_bytes THEN RETURN false;END IF;
    FOR e IN SELECT * FROM life_financial.risk_events WHERE account_id=p_account ORDER BY event_version LOOP
        SELECT * INTO o FROM life_financial.operations WHERE account_id=p_account AND operation_id=e.operation_id;
        IF NOT FOUND OR e.event_version<>ordinal+1 OR e.source_hash<>encode(sha256(convert_to(e.body::text,'UTF8')),'hex')
            OR e.body->'policy'<>to_jsonb(p) OR e.body->'previous' IS DISTINCT FROM COALESCE(prior,'null'::jsonb)
            OR o.command IS DISTINCT FROM e.body->'request' OR o.receipt IS DISTINCT FROM e.body->'receipt'
            OR o.command->>'kind' IS DISTINCT FROM 'RISK_OBSERVE' OR o.command#>>'{command,operation_id}'<>e.operation_id
            OR o.committed_version<>(o.receipt->>'version')::bigint THEN RETURN false;END IF;
        expected:=life_financial.risk_decide(to_jsonb(p),prior,e.body->'source',e.body#>'{request,command}',
            (e.body->>'at_ms')::bigint,e.body#>'{request,owner}',e.body->>'observer_id',(e.body->>'halted_before')::boolean);
        epoch:=epoch+CASE WHEN prior IS NULL OR prior->'state' IS DISTINCT FROM expected->'state'
            OR prior->'generation' IS DISTINCT FROM expected->'generation' OR prior->'observer_id' IS DISTINCT FROM expected->'observer_id' THEN 1 ELSE 0 END;
        IF e.risk_epoch<>epoch OR expected IS DISTINCT FROM e.body->'decision' OR o.receipt->'decision' IS DISTINCT FROM expected
            OR (o.receipt->>'risk_epoch')::bigint<>e.risk_epoch OR (o.receipt->>'event_version')::bigint<>e.event_version OR o.receipt->'trading_permission'<>'false'::jsonb THEN RETURN false;END IF;
        prior:=expected;ordinal:=e.event_version;
    END LOOP;
    RETURN epoch=(SELECT risk_epoch FROM life_financial.accounts WHERE account_id=p_account)
        AND (prior->>'state' IS DISTINCT FROM 'HALTED' OR (SELECT halted FROM life_financial.accounts WHERE account_id=p_account));
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.risk_command(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,p_command jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts;p life_financial.risk_service_policies;old life_financial.operations;op life_financial.identity;
    request jsonb;prior jsonb;source jsonb;decision jsonb;result jsonb;body jsonb;at_ms bigint;observer uuid;next_epoch bigint;ordinal bigint;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR p_command IS NULL OR jsonb_typeof(p_command)<>'object'
        OR (SELECT count(*) FROM jsonb_object_keys(p_command))<>6
        OR NOT p_command ?& ARRAY['operation_id','observed_at','market_data_fresh','latency_ok','account_ready','model_ready']
        OR EXISTS(SELECT 1 FROM jsonb_each(p_command) x WHERE CASE WHEN x.key IN ('operation_id','observed_at')
            THEN jsonb_typeof(x.value)<>'string' ELSE jsonb_typeof(x.value)<>'boolean' END)
        OR p_cut IS NULL OR p_fingerprint IS NULL THEN RAISE EXCEPTION 'LIFE_RISK_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
    op:=p_command->>'operation_id';IF length(op)>96
        OR p_command->>'observed_at' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]{1,6})?([+]00:00|Z)$'
        OR NOT isfinite((p_command->>'observed_at')::timestamptz) THEN
        RAISE EXCEPTION 'LIFE_RISK_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    SELECT * INTO STRICT p FROM life_financial.risk_service_policies WHERE account_id=p_account;
    request:=jsonb_build_object('kind','RISK_OBSERVE','binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',p_command);
    SELECT * INTO old FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF old.command IS DISTINCT FROM request OR old.fingerprint<>p_fingerprint OR NOT life_financial.risk_verified(p_account) THEN
            RAISE EXCEPTION 'LIFE_RISK_OPERATION_CONFLICT' USING ERRCODE='P0001';END IF;
        RETURN old.receipt||'{"replayed":true}'::jsonb;
    END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    IF NOT life_financial.risk_verified(p_account) THEN RAISE EXCEPTION 'LIFE_RISK_UNVERIFIED' USING ERRCODE='P0001';END IF;
    IF (life_financial.spot_state(p_account)->>'digest') IS DISTINCT FROM p_cut THEN RAISE EXCEPTION 'LIFE_RISK_CUT_STALE' USING ERRCODE='P0001';END IF;
    observer:=current_setting('life.risk_observer',true)::uuid;
    IF observer IS NULL THEN RAISE EXCEPTION 'LIFE_RISK_OBSERVER_REQUIRED' USING ERRCODE='P0001';END IF;
    SELECT e.body->'decision' INTO prior FROM life_financial.risk_events e WHERE account_id=p_account ORDER BY event_version DESC LIMIT 1;
    source:=life_financial.risk_source(p_account);at_ms:=floor(extract(epoch FROM clock_timestamp())*1000)::bigint;
    decision:=life_financial.risk_decide(to_jsonb(p),prior,source,p_command,at_ms,p_owner,observer::text,a.halted);
    next_epoch:=a.risk_epoch+CASE WHEN prior IS NULL OR prior->'state' IS DISTINCT FROM decision->'state'
        OR prior->'generation' IS DISTINCT FROM decision->'generation' OR prior->'observer_id' IS DISTINCT FROM decision->'observer_id' THEN 1 ELSE 0 END;
    SELECT COALESCE(max(event_version),0)+1 INTO ordinal FROM life_financial.risk_events WHERE account_id=p_account;
    UPDATE life_financial.accounts SET version=version+1,risk_epoch=next_epoch,halted=halted OR decision->>'state'='HALTED' WHERE account_id=p_account;
    result:=jsonb_build_object('kind','RISK_OBSERVE','operation_id',op,'decision',decision,'risk_epoch',next_epoch,'event_version',ordinal,
        'version',a.version+1,'replayed',false,'trading_permission',false);
    body:=jsonb_build_object('request',request,'policy',to_jsonb(p),'source',source,'previous',prior,'at_ms',at_ms,
        'observer_id',observer,'halted_before',a.halted,'decision',decision,'receipt',result);
    IF (SELECT count(*) FROM life_financial.risk_events WHERE account_id=p_account)>=p.max_events
        OR COALESCE((SELECT sum(octet_length(e.body::text)) FROM life_financial.risk_events e WHERE account_id=p_account),0)+octet_length(body::text)>p.max_total_bytes THEN
        RAISE EXCEPTION 'LIFE_RISK_HISTORY_BOUND' USING ERRCODE='P0001';END IF;
    INSERT INTO life_financial.risk_events VALUES(p_account,op,ordinal,next_epoch,body,encode(sha256(convert_to(body::text,'UTF8')),'hex'));
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version)
        VALUES(p_account,op,p_fingerprint,request,result,a.version+1);
    RETURN result;
END; $$;

CREATE FUNCTION life_financial.risk_enabled(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_ACCESS_DENIED' USING ERRCODE='P0001';END IF;
    RETURN EXISTS(SELECT 1 FROM life_financial.risk_service_policies WHERE account_id=p_account);
END; $$;

CREATE FUNCTION life_financial.risk_frame(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE e life_financial.risk_events;valid boolean;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_ACCESS_DENIED' USING ERRCODE='P0001';END IF;
    valid:=life_financial.risk_verified(p_account);
    IF NOT valid THEN RAISE EXCEPTION 'LIFE_RISK_UNVERIFIED' USING ERRCODE='P0001';END IF;
    SELECT * INTO e FROM life_financial.risk_events WHERE account_id=p_account ORDER BY event_version DESC LIMIT 1;
    RETURN jsonb_build_object('operation_id',e.operation_id,'risk_epoch',e.risk_epoch,'decision',e.body->'decision',
        'replayed',true,'kind','RISK_OBSERVE','trading_permission',false);
END; $$;

CREATE FUNCTION life_financial.risk_admit(p_account uuid,op life_financial.operations) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.risk_service_policies;a life_financial.accounts;e life_financial.risk_events;d jsonb;source jsonb;at_ms bigint;
    t life_financial.send_attempts;prep life_financial.spot_preparations;remaining numeric;filled numeric;qty numeric;
BEGIN
    SELECT * INTO p FROM life_financial.risk_service_policies WHERE account_id=p_account;
    IF NOT FOUND THEN RETURN;END IF;
    IF NOT life_financial.risk_verified(p_account) THEN RAISE EXCEPTION 'LIFE_RISK_UNVERIFIED' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account;
    SELECT * INTO e FROM life_financial.risk_events WHERE account_id=p_account ORDER BY event_version DESC LIMIT 1;
    d:=e.body->'decision';at_ms:=floor(extract(epoch FROM clock_timestamp())*1000)::bigint;
    IF e.account_id IS NULL OR d->>'generation'<>a.owner_generation::text OR d->>'session_id'<>a.current_session::text
        OR d->>'observer_id' IS DISTINCT FROM current_setting('life.risk_observer',true)
        OR a.halted OR d->>'state' NOT IN ('NORMAL','DEGRADED') OR at_ms<(d->>'last_seen_ms')::bigint
        OR at_ms-(d->>'last_seen_ms')::bigint>p.max_observation_age_ms THEN
        RAISE EXCEPTION 'LIFE_RISK_PERMISSION_REQUIRED' USING ERRCODE='P0001';END IF;
    source:=life_financial.risk_source(p_account);
    IF source IS NULL OR source-'margin_buffer_quote' IS DISTINCT FROM (e.body->'source')-'margin_buffer_quote'
        OR (source->>'margin_buffer_quote')::numeric<p.min_margin_buffer_quote THEN RAISE EXCEPTION 'LIFE_RISK_VALUE_CHANGED_OR_STALE' USING ERRCODE='P0001';END IF;
    IF op.receipt->>'kind' IN ('SPOT_PREPARE','SPOT_ARM','SPOT_QUOTE_ARM') THEN
        SELECT * INTO STRICT t FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=op.receipt->>'intent_id';
        SELECT * INTO STRICT prep FROM life_financial.spot_preparations WHERE account_id=p_account AND intent_id=t.intent_id;
        IF (prep.request->>'risk_epoch')::bigint IS DISTINCT FROM a.risk_epoch THEN RAISE EXCEPTION 'LIFE_RISK_EPOCH_CHANGED' USING ERRCODE='P0001';END IF;
        qty:=t.quantity;
        IF d->>'state'='DEGRADED' THEN
            SELECT COALESCE(sum(quantity),0) INTO filled FROM life_financial.fills WHERE account_id=p_account AND product='SPOT';
            IF p.recovery_probe_campaign_base IS NULL OR qty>p.recovery_probe_base OR filled+qty>p.recovery_probe_campaign_base OR EXISTS(
                SELECT 1 FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id<>t.intent_id
                    AND state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND')) THEN
                RAISE EXCEPTION 'LIFE_RISK_PROBE_CAPACITY' USING ERRCODE='P0001';END IF;
        END IF;
    ELSIF d->>'state'<>'NORMAL' AND op.command->>'kind' IS DISTINCT FROM 'SPOT_ACTION_TRANSITION' THEN RAISE EXCEPTION 'LIFE_RISK_NORMAL_REQUIRED' USING ERRCODE='P0001';END IF;
END; $$;

CREATE FUNCTION life_financial.risk_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE op life_financial.operations;e life_financial.risk_events;p life_financial.risk_service_policies;at_ms bigint;
BEGIN
    SELECT * INTO p FROM life_financial.risk_service_policies WHERE account_id=NEW.account_id;
    IF NOT FOUND THEN RETURN NULL;END IF;
    SELECT * INTO STRICT op FROM life_financial.operations WHERE account_id=NEW.account_id AND committed_version=NEW.version;
    IF op.command->>'kind'='RISK_OBSERVE' THEN
        IF NOT life_financial.risk_verified(NEW.account_id) THEN RAISE EXCEPTION 'LIFE_RISK_UNVERIFIED' USING ERRCODE='P0001';END IF;
        SELECT * INTO STRICT e FROM life_financial.risk_events WHERE account_id=NEW.account_id AND operation_id=op.operation_id;
        at_ms:=floor(extract(epoch FROM clock_timestamp())*1000)::bigint;
        IF e.body#>>'{decision,state}' IN ('NORMAL','DEGRADED') AND (
            life_financial.risk_source(NEW.account_id) IS DISTINCT FROM e.body->'source'
            OR at_ms<(e.body->>'at_ms')::bigint OR at_ms-(e.body->>'at_ms')::bigint>p.max_observation_age_ms) THEN
            RAISE EXCEPTION 'LIFE_RISK_SOURCE_STALE_AT_COMMIT' USING ERRCODE='P0001';END IF;
    ELSIF op.receipt->>'kind' IN ('SPOT_PREPARE','SPOT_ARM','SPOT_QUOTE_ARM')
        OR op.command->>'kind' IN ('SWAP_PREPARE','SWAP_ARM','SWAP_BOUNDARY','HEDGE_PLAN','HEDGE_CHILD')
        OR op.command->>'kind' IS NULL AND op.command ?& ARRAY['asset','amount','binding','owner']
        OR op.command->>'kind'='SHARED_CAPITAL' AND op.command#>>'{command,kind}'='RESERVE'
        OR op.command->>'kind'='SPOT_ACTION_TRANSITION' AND op.command#>>'{command,state}'='DISPATCHED' THEN
        PERFORM life_financial.risk_admit(NEW.account_id,op);
    END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER zzz_native_risk_commit AFTER UPDATE ON life_financial.accounts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN(NEW.version<>OLD.version) EXECUTE FUNCTION life_financial.risk_commit_guard();

DO $$ DECLARE f record;definition text;needle text;BEGIN
    SELECT * INTO STRICT f FROM pg_proc WHERE oid='life_financial.commit_owner_guard()'::regprocedure;
    IF NOT f.prosecdef OR f.provolatile<>'v' OR f.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
        OR encode(sha256(convert_to(f.prosrc,'UTF8')),'hex')<>'222c5964971b54266d17dc8a8af2c2ed217d5ee3695a76dbee1f3456fc9f9350' THEN RAISE EXCEPTION 'LIFE_RISK_MIGRATION_MISMATCH';END IF;
    definition:=pg_get_functiondef(f.oid);needle:='(''TERMINAL_REPAIR'',''HEDGE_PLAN'',''HEDGE_SETTLE''';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_RISK_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,'(''RISK_OBSERVE'',''TERMINAL_REPAIR'',''HEDGE_PLAN'',''HEDGE_SETTLE''');
END; $$;
DO $$ DECLARE f record;r record;BEGIN
    FOR f IN SELECT p.oid::regprocedure AS signature,p.proowner FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='life_financial' AND p.proname IN ('risk_source','risk_decide','risk_verified','risk_command','risk_frame','risk_admit','risk_commit_guard','risk_enabled') LOOP
        FOR r IN SELECT DISTINCT x.grantee FROM pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
            WHERE p.oid=f.signature AND x.grantee<>f.proowner LOOP
            IF r.grantee=0 THEN EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',f.signature);
            ELSE EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',f.signature,pg_get_userbyid(r.grantee));END IF;
        END LOOP;
    END LOOP;
END; $$;

-- Existing scoped reader/writer logins can inspect only service enablement
-- after upgrade. This gives no command, policy-table DML or global account read.
DO $$ DECLARE r record;BEGIN
    FOR r IN SELECT DISTINCT b.principal FROM life_financial.access_bindings b JOIN pg_roles p ON p.rolname=b.principal
        WHERE has_function_privilege(p.oid,'life_financial.spot_state(uuid)','EXECUTE') LOOP
        EXECUTE format('GRANT EXECUTE ON FUNCTION life_financial.risk_enabled(uuid) TO %I',r.principal);
    END LOOP;
END; $$;

-- Accept an explicit positive risk epoch for SPOT-only safety, preserving the
-- complete original shared-account validation, command bytes and financial core.
DO $$ DECLARE f record;definition text;needle text;replacement text;BEGIN
    SELECT * INTO STRICT f FROM pg_proc WHERE oid='life_financial.spot_prepare_action_core(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb,jsonb)'::regprocedure;
    IF NOT f.prosecdef OR f.provolatile<>'v' OR f.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
        OR encode(sha256(convert_to(f.prosrc,'UTF8')),'hex')<>'7ec844c9ab699708d378159861455cb62dd215f15a399ac02c7d9b52bf05f443' THEN
        RAISE EXCEPTION 'LIFE_RISK_MIGRATION_MISMATCH';END IF;
    definition:=pg_get_functiondef(f.oid);
    needle:='EXISTS(SELECT 1 FROM life_financial.shared_capital_policies WHERE account_id=p_account)';
    replacement:='(EXISTS(SELECT 1 FROM life_financial.shared_capital_policies WHERE account_id=p_account) OR EXISTS(SELECT 1 FROM life_financial.risk_service_policies WHERE account_id=p_account))';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>2 THEN RAISE EXCEPTION 'LIFE_RISK_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;
