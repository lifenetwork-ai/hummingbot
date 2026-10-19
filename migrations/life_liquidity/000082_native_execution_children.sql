DO $$ DECLARE item record;f record;BEGIN
    FOR item IN SELECT * FROM (VALUES
        ('life_financial.execution_admit(uuid,life_financial.operations)','d7ace3d3ad34261b5e4cc099eb5c2e7a938291688c3cf59ee3ce7638ad456b9e','v'),
        ('life_financial.execution_policy_qualified(uuid)','8e071f31f08953b47ddd9c0d4faa890930596a7add68f9f933e07c2246c6d3e4','s'),
        ('life_financial.commit_owner_guard()','39cdbd7c8a2bf1aa6ac7e19bdbc758b8b9f1b2c481c8d89f73655cb33871746d','v'),
        ('life_financial.spot_prepare_action_core(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb,jsonb)','640fcf2eb7529b12af23c8971f1bbe8a85059b8e4c7938ebc88c5445514bf27c','v'),
        ('life_financial.risk_admit(uuid,life_financial.operations)','c679d5aec7504c4c19683313a893c56f44ed31df61c0c2d874430768acc16f28','v'),
        ('life_financial.risk_markout_admit(uuid,life_financial.operations)','563d584cb24ccee828a0ffb9ae511d9ee44ad6fdbdc5f48e4fb708161e1424be','v')
    ) x(signature,source_hash,volatility) LOOP
        SELECT * INTO STRICT f FROM pg_proc WHERE oid=item.signature::regprocedure;
        IF NOT f.prosecdef OR f.provolatile::text<>item.volatility OR f.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR encode(sha256(convert_to(f.prosrc,'UTF8')),'hex')<>item.source_hash THEN RAISE EXCEPTION 'LIFE_EXECUTION_CHILD_MIGRATION_MISMATCH';END IF;
    END LOOP;
END; $$;
-- Native execution observations and original immutable child membership.
CREATE TABLE life_financial.execution_market_facts (
    account_id uuid NOT NULL REFERENCES life_financial.execution_policies,
    operation_id life_financial.identity NOT NULL,event_version bigint NOT NULL CHECK(event_version>0),
    body jsonb NOT NULL CHECK(jsonb_typeof(body)='object'),source_hash life_financial.digest NOT NULL,
    PRIMARY KEY(account_id,operation_id),UNIQUE(account_id,event_version),
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE life_financial.execution_children (
    account_id uuid NOT NULL,intent_id life_financial.identity NOT NULL,prepare_operation_id life_financial.identity NOT NULL,
    mode text NOT NULL CHECK(mode IN ('INVENTORY','EXIT')),mode_operation_id life_financial.identity NOT NULL,
    market_operation_id life_financial.identity NOT NULL,estimated_loss_quote life_financial.amount NOT NULL,
    body jsonb NOT NULL,source_hash life_financial.digest NOT NULL,
    PRIMARY KEY(account_id,intent_id),UNIQUE(account_id,prepare_operation_id),
    FOREIGN KEY(account_id,prepare_operation_id) REFERENCES life_financial.spot_preparations DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(account_id,mode_operation_id) REFERENCES life_financial.control_records DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(account_id,market_operation_id) REFERENCES life_financial.execution_market_facts DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE life_financial.execution_exit_settlements (
    account_id uuid NOT NULL,intent_id life_financial.identity NOT NULL,operation_id life_financial.identity NOT NULL,
    held_before_usdt life_financial.amount NOT NULL,actual_usdt life_financial.amount NOT NULL,body jsonb NOT NULL,source_hash life_financial.digest NOT NULL,
    PRIMARY KEY(account_id,intent_id),UNIQUE(account_id,operation_id),
    FOREIGN KEY(account_id,intent_id) REFERENCES life_financial.execution_children DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED
);
DO $$ DECLARE t text;BEGIN
    FOREACH t IN ARRAY ARRAY['execution_market_facts','execution_children','execution_exit_settlements'] LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY scoped_read ON life_financial.%I FOR SELECT USING(life_financial.can_access(account_id))',t);
        EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',t);
    END LOOP;
END; $$;
CREATE FUNCTION life_financial.execution_market_valid(j jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE k text;n numeric;id life_financial.identity;
BEGIN
    IF jsonb_typeof(j) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(j))<>14
        OR octet_length(j::text)>16384 OR NOT j ?& ARRAY['operation_id','intent_id','value_at_ms','observed_at_ms','source_kind','volume_anchor','best_bid_usdt','best_ask_usdt',
            'public_volume_base','known_own_volume_base','independent_depth_base','expected_impact_quote','fee_rate','independent_value_usdt']
        OR j->>'source_kind' IS DISTINCT FROM 'independent_market' THEN RETURN false;END IF;
    FOREACH k IN ARRAY ARRAY['operation_id','intent_id','volume_anchor'] LOOP
        IF jsonb_typeof(j->k) IS DISTINCT FROM 'string' THEN RETURN false;END IF;
        id:=j->>k;IF id IS NULL OR length(id)>96 THEN RETURN false;END IF;
    END LOOP;
    FOREACH k IN ARRAY ARRAY['value_at_ms','observed_at_ms'] LOOP
        IF jsonb_typeof(j->k) IS DISTINCT FROM 'number' OR j->>k !~ '^[1-9][0-9]*$' OR (j->>k)::bigint>253402300799999 THEN RETURN false;END IF;
    END LOOP;
    FOREACH k IN ARRAY ARRAY['best_bid_usdt','best_ask_usdt','public_volume_base','known_own_volume_base','independent_depth_base','expected_impact_quote','fee_rate','independent_value_usdt'] LOOP
        IF jsonb_typeof(j->k) IS DISTINCT FROM 'string' THEN RETURN false;END IF;
        n:=(j->>k)::life_financial.amount;
        IF k IN ('best_bid_usdt','best_ask_usdt','independent_value_usdt') AND n<=0 THEN RETURN false;END IF;
    END LOOP;
    RETURN (j->>'value_at_ms')::bigint<=(j->>'observed_at_ms')::bigint
        AND (j->>'best_bid_usdt')::numeric<(j->>'best_ask_usdt')::numeric
        AND (j->>'known_own_volume_base')::numeric<=(j->>'public_volume_base')::numeric;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
CREATE FUNCTION life_financial.execution_market_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE p life_financial.control_policies;e life_financial.execution_market_facts;o life_financial.operations;prior jsonb;prior_hash jsonb;ordinal bigint:=0;
BEGIN
    SELECT * INTO STRICT p FROM life_financial.control_policies WHERE account_id=p_account;
    IF (SELECT count(*) FROM life_financial.execution_market_facts WHERE account_id=p_account)>p.max_records
        OR COALESCE((SELECT sum(octet_length(body::text)) FROM life_financial.execution_market_facts WHERE account_id=p_account),0)>p.max_total_bytes
        OR EXISTS(SELECT 1 FROM life_financial.operations original_op WHERE original_op.account_id=p_account AND original_op.command->>'kind'='EXECUTION_OBSERVE'
            AND NOT EXISTS(SELECT 1 FROM life_financial.execution_market_facts fact WHERE fact.account_id=p_account AND fact.operation_id=original_op.operation_id)) THEN RETURN false;END IF;
    FOR e IN SELECT * FROM life_financial.execution_market_facts WHERE account_id=p_account ORDER BY event_version LOOP
        SELECT * INTO o FROM life_financial.operations WHERE account_id=p_account AND operation_id=e.operation_id;
        IF NOT FOUND OR e.event_version<>ordinal+1 OR e.source_hash<>encode(sha256(convert_to(e.body::text,'UTF8')),'hex')
            OR (SELECT count(*) FROM jsonb_object_keys(e.body))<>10
            OR NOT e.body ?& ARRAY['request','receipt','mode_operation_id','observer_id','risk_epoch','at_ms','last_seen_ms','rollback_latched','previous','policy_digest']
            OR jsonb_typeof(e.body->'rollback_latched') IS DISTINCT FROM 'boolean'
            OR jsonb_typeof(e.body->'risk_epoch') IS DISTINCT FROM 'number' OR e.body->>'risk_epoch' !~ '^[1-9][0-9]*$'
            OR jsonb_typeof(e.body->'at_ms') IS DISTINCT FROM 'number' OR e.body->>'at_ms' !~ '^[1-9][0-9]*$'
            OR jsonb_typeof(e.body->'observer_id') IS DISTINCT FROM 'string' OR (e.body->>'observer_id')::uuid IS NULL
            OR e.body->'previous' IS DISTINCT FROM COALESCE(prior_hash,'null'::jsonb)
            OR e.body#>>'{request,owner,session_id}' IS DISTINCT FROM (SELECT body#>>'{before_account,current_session}' FROM life_financial.control_records WHERE account_id=p_account AND operation_id=e.body->>'mode_operation_id')
            OR e.body->>'policy_digest' IS DISTINCT FROM (SELECT policy_digest FROM life_financial.execution_policies WHERE account_id=p_account)
            OR NOT EXISTS(SELECT 1 FROM life_financial.control_records mode WHERE mode.account_id=p_account AND mode.operation_id=e.body->>'mode_operation_id'
                AND mode.body#>>'{payload,kind}'='EXECUTION_MODE' AND mode.body#>>'{payload,mode}' IN ('INVENTORY','EXIT')
                AND mode.body#>'{payload,observer_id}'=e.body->'observer_id' AND mode.body->'after_account'->'owner_generation'=o.command->'owner'->'generation'
                AND (mode.body#>>'{receipt,version}')::bigint<=o.committed_version)
            OR NOT life_financial.execution_market_valid(o.command->'command')
            OR e.body->'request' IS DISTINCT FROM o.command OR e.body->'receipt' IS DISTINCT FROM o.receipt
            OR o.receipt->>'kind' IS DISTINCT FROM 'EXECUTION_OBSERVE' OR o.receipt->>'operation_id' IS DISTINCT FROM e.operation_id
            OR o.receipt->'replayed' IS DISTINCT FROM 'false'::jsonb OR (o.receipt->>'version')::bigint IS DISTINCT FROM o.committed_version
            OR o.command->>'kind' IS DISTINCT FROM 'EXECUTION_OBSERVE' OR o.command#>>'{command,operation_id}' IS DISTINCT FROM e.operation_id
            OR (o.receipt->>'event_version')::bigint IS DISTINCT FROM e.event_version OR o.receipt->'trading_permission' IS DISTINCT FROM 'false'::jsonb
            OR (e.body->>'last_seen_ms')::bigint IS DISTINCT FROM greatest((e.body->>'at_ms')::bigint,(prior->>'last_seen_ms')::bigint)
            OR (e.body->>'rollback_latched')::boolean IS DISTINCT FROM (COALESCE((prior->>'rollback_latched')::boolean,false)
                OR (e.body->>'at_ms')::bigint<(e.body->>'last_seen_ms')::bigint) THEN RETURN false;END IF;
        ordinal:=e.event_version;prior:=e.body;prior_hash:=to_jsonb(e.source_hash);
    END LOOP;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
CREATE FUNCTION life_financial.execution_market_command(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,p_command jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE a life_financial.accounts;p life_financial.control_policies;old life_financial.operations;r life_financial.control_records;
    prior life_financial.execution_market_facts;request jsonb;result jsonb;body jsonb;op life_financial.identity;ordinal bigint;at_ms bigint;last_seen bigint;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR NOT life_financial.execution_market_valid(p_command)
        OR p_cut IS NULL OR p_fingerprint IS NULL THEN RAISE EXCEPTION 'LIFE_EXECUTION_MARKET_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    SELECT * INTO STRICT p FROM life_financial.control_policies WHERE account_id=p_account;
    op:=p_command->>'operation_id';
    request:=jsonb_build_object('kind','EXECUTION_OBSERVE','binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',p_command);
    SELECT * INTO old FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF old.command IS DISTINCT FROM request OR old.fingerprint<>p_fingerprint OR NOT life_financial.execution_market_verified(p_account) THEN
            RAISE EXCEPTION 'LIFE_EXECUTION_OPERATION_CONFLICT' USING ERRCODE='P0001';END IF;
        RETURN old.receipt||'{"replayed":true}'::jsonb;
    END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    IF NOT life_financial.control_verified(p_account) OR NOT life_financial.execution_market_verified(p_account) THEN
        RAISE EXCEPTION 'LIFE_EXECUTION_UNVERIFIED' USING ERRCODE='P0001';END IF;
    IF (life_financial.spot_state(p_account)->>'digest') IS DISTINCT FROM p_cut THEN RAISE EXCEPTION 'LIFE_EXECUTION_CUT_STALE' USING ERRCODE='P0001';END IF;
    SELECT * INTO r FROM life_financial.control_records WHERE account_id=p_account AND body#>>'{payload,kind}'='EXECUTION_MODE' ORDER BY sequence DESC LIMIT 1;
    IF NOT FOUND OR r.body#>>'{payload,mode}' NOT IN ('INVENTORY','EXIT') OR NOT life_financial.execution_record_qualified(p_account,r.operation_id)
        OR r.body#>>'{payload,observer_id}' IS DISTINCT FROM current_setting('life.risk_observer',true)
        OR r.body#>>'{before_account,owner_generation}' IS DISTINCT FROM a.owner_generation::text
        OR r.body#>>'{before_account,current_session}' IS DISTINCT FROM a.current_session::text THEN
        RAISE EXCEPTION 'LIFE_EXECUTION_FRESH_MODE_REQUIRED' USING ERRCODE='P0001';END IF;
    SELECT * INTO prior FROM life_financial.execution_market_facts WHERE account_id=p_account ORDER BY event_version DESC LIMIT 1;
    ordinal:=COALESCE(prior.event_version,0)+1;at_ms:=floor(extract(epoch FROM clock_timestamp())*1000)::bigint;last_seen:=greatest(at_ms,(prior.body->>'last_seen_ms')::bigint);
    result:=jsonb_build_object('kind','EXECUTION_OBSERVE','operation_id',op,'event_version',ordinal,'version',a.version+1,'replayed',false,'trading_permission',false);
    body:=jsonb_build_object('request',request,'receipt',result,'mode_operation_id',r.operation_id,'observer_id',current_setting('life.risk_observer',true),
        'previous',prior.source_hash,'policy_digest',(SELECT policy_digest FROM life_financial.execution_policies WHERE account_id=p_account),'risk_epoch',a.risk_epoch,'at_ms',at_ms,'last_seen_ms',last_seen,'rollback_latched',COALESCE((prior.body->>'rollback_latched')::boolean,false) OR at_ms<last_seen);
    IF ordinal>p.max_records OR COALESCE((SELECT sum(octet_length(fact.body::text)) FROM life_financial.execution_market_facts fact WHERE account_id=p_account),0)+octet_length(body::text)>p.max_total_bytes THEN
        RAISE EXCEPTION 'LIFE_EXECUTION_HISTORY_BOUND' USING ERRCODE='P0001';END IF;
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    INSERT INTO life_financial.execution_market_facts VALUES(p_account,op,ordinal,body,encode(sha256(convert_to(body::text,'UTF8')),'hex'));
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version) VALUES(p_account,op,p_fingerprint,request,result,a.version+1);
    RETURN result;
END; $$;
CREATE FUNCTION life_financial.execution_budget_frame(p_account uuid,p_scope text,p_exclude text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE p life_financial.execution_policies;k text;s uuid;today text;held numeric;spent numeric;cap numeric;available numeric;rows jsonb:='[]'::jsonb;
BEGIN
    IF p_scope IS NULL OR p_scope NOT IN ('QUOTE','EXIT') THEN RAISE EXCEPTION 'LIFE_EXECUTION_BUDGET_SCOPE_INVALID' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT p FROM life_financial.execution_policies WHERE account_id=p_account;
    SELECT current_session INTO STRICT s FROM life_financial.accounts WHERE account_id=p_account;
    today:=to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD');
    FOREACH k IN ARRAY ARRAY['campaign','day','session'] LOOP
        SELECT COALESCE(sum(CASE WHEN d.intent_id=p_exclude OR m.intent_id IS NOT NULL OR x.intent_id IS NOT NULL
            OR t.state='ABORTED_BEFORE_SEND' OR t.state='TERMINAL' AND COALESCE(g.filled_base,0)=0 THEN 0
            ELSE greatest(d.subsidy,COALESCE(g.execution_loss,0)) END),0),COALESCE(sum(COALESCE(m.actual_usdt,x.actual_usdt,0)),0)
        INTO held,spent FROM life_financial.spot_preparations d JOIN life_financial.send_attempts t USING(account_id,intent_id)
            LEFT JOIN life_financial.spot_accounting_progress g USING(account_id,intent_id)
            LEFT JOIN life_financial.spot_inventory_members m USING(account_id,intent_id)
            LEFT JOIN life_financial.execution_exit_settlements x USING(account_id,intent_id)
        WHERE d.account_id=p_account AND COALESCE(d.request->>'budget_scope','QUOTE')=p_scope
            AND (k<>'day' OR to_char(d.prepared_at AT TIME ZONE 'UTC','YYYY-MM-DD')=today) AND (k<>'session' OR t.session_id=s);
        cap:=(p.body#>>ARRAY[lower(p_scope)||'_budget',k])::numeric;
        available:=least(COALESCE(available,cap),cap-held-spent);
        rows:=rows||jsonb_build_array(jsonb_build_object('scope',k,'limit',cap::text,'committed',held::text,'incurred',spent::text));
    END LOOP;
    RETURN jsonb_build_object('scope',p_scope,'available',greatest(0,available)::text,'windows',rows,'trading_permission',false);
END; $$;
CREATE FUNCTION life_financial.execution_terms_valid(j jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE ip jsonb;pol jsonb;k text;n numeric;started timestamptz;deadline timestamptz;
BEGIN
    IF jsonb_typeof(j) IS DISTINCT FROM 'object' THEN RETURN false;END IF;
    FOREACH k IN ARRAY ARRAY['quote_budget','exit_budget'] LOOP
        IF jsonb_typeof(j->k) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(j->k))<>3 OR NOT j->k ?& ARRAY['campaign','day','session']
            OR EXISTS(SELECT 1 FROM jsonb_each(j->k) WHERE jsonb_typeof(value)<>'string') THEN RETURN false;END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM jsonb_object_keys(j) name WHERE name NOT IN ('campaign_id','quote_budget','exit_budget','inventory','exit','rules')) THEN RETURN false;END IF;
    ip:=j->'inventory';
    IF ip IS NOT NULL AND ip<>'null'::jsonb THEN
        IF jsonb_typeof(ip) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(ip))<>4 OR NOT ip ?& ARRAY['campaign_id','started_at','policy','max_age_ms'] OR ip->>'campaign_id' IS DISTINCT FROM j->>'campaign_id'
            OR jsonb_typeof(ip->'max_age_ms') IS DISTINCT FROM 'number' OR ip->>'max_age_ms' !~ '^[1-9][0-9]*$' OR (ip->>'max_age_ms')::integer NOT BETWEEN 1 AND 60000 THEN RETURN false;END IF;
        pol:=ip->'policy';IF jsonb_typeof(pol) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(pol))<>14 OR pol->>'side' IS NULL OR pol->>'side' NOT IN ('BUY','SELL') THEN RETURN false;END IF;
        IF jsonb_typeof(ip->'started_at') IS DISTINCT FROM 'string' OR jsonb_typeof(pol->'deadline_utc') IS DISTINCT FROM 'string'
            OR ip->>'started_at' !~ '(Z|\+00:00)$' OR pol->>'deadline_utc' !~ '(Z|\+00:00)$' THEN RETURN false;END IF;
        started:=(ip->>'started_at')::timestamptz;deadline:=(pol->>'deadline_utc')::timestamptz;
        IF started IS NULL OR deadline IS NULL OR NOT isfinite(started) OR NOT isfinite(deadline) OR started>=deadline THEN RETURN false;END IF;
        FOREACH k IN ARRAY ARRAY['target_base','benchmark_price_usdt','max_child_base','max_total_notional_quote','participation_fraction','min_independent_volume_base','tick_size','lot_size','min_size_base'] LOOP
            IF jsonb_typeof(pol->k) IS DISTINCT FROM 'string' THEN RETURN false;END IF;n:=(pol->>k)::life_financial.positive_amount;IF n IS NULL THEN RETURN false;END IF;
        END LOOP;
        FOREACH k IN ARRAY ARRAY['max_slippage_bps','max_impact_quote','max_exit_loss_quote'] LOOP
            IF jsonb_typeof(pol->k) IS DISTINCT FROM 'string' THEN RETURN false;END IF;n:=(pol->>k)::life_financial.amount;IF n IS NULL THEN RETURN false;END IF;
        END LOOP;
        IF (pol->>'participation_fraction')::numeric>1 THEN RETURN false;END IF;
    END IF;
    ip:=j->'exit';
    IF ip IS NOT NULL AND ip<>'null'::jsonb THEN
        IF jsonb_typeof(ip) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(ip))<>4
            OR jsonb_typeof(ip->'max_age_ms') IS DISTINCT FROM 'number' OR ip->>'max_age_ms' !~ '^[1-9][0-9]*$' OR (ip->>'max_age_ms')::integer NOT BETWEEN 1 AND 60000 THEN RETURN false;END IF;
        IF NOT ip ?& ARRAY['target_base','max_child_base','max_slippage_bps','max_age_ms'] OR EXISTS(SELECT 1 FROM jsonb_each(ip) WHERE key<>'max_age_ms' AND jsonb_typeof(value)<>'string') THEN RETURN false;END IF;
        n:=(ip->>'target_base')::life_financial.amount;IF n IS NULL THEN RETURN false;END IF;
        n:=(ip->>'max_child_base')::life_financial.positive_amount;IF n IS NULL THEN RETURN false;END IF;
        n:=(ip->>'max_slippage_bps')::life_financial.amount;IF n IS NULL THEN RETURN false;END IF;
        IF jsonb_typeof(j->'rules') IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(j->'rules'))<>3 THEN RETURN false;END IF;
        FOREACH k IN ARRAY ARRAY['tick_size','lot_size','min_size_base'] LOOP
            IF jsonb_typeof(j->'rules'->k) IS DISTINCT FROM 'string' THEN RETURN false;END IF;n:=(j#>>ARRAY['rules',k])::life_financial.positive_amount;IF n IS NULL THEN RETURN false;END IF;
        END LOOP;
    END IF;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
CREATE FUNCTION life_financial.execution_decision(p_account uuid,source jsonb,p_mode text,p_intent text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE p life_financial.execution_policies;ip jsonb;pol jsonb;rule jsonb;reason text;reference numeric;at_ms bigint;fee numeric;bid numeric;ask numeric;
    tick numeric;lot numeric;min_size numeric;price numeric;qty numeric;remain numeric;filled numeric;pending numeric;filled_notional numeric;pending_notional numeric;
    used numeric;pending_cost numeric;own numeric;volume numeric;depth numeric;impact numeric;base numeric;quote numeric;subsidy numeric;estimated numeric;
    participation numeric;notional_room numeric;balance_lots numeric;budget jsonb;prep life_financial.spot_preparations;started_ms bigint;
BEGIN
    SELECT * INTO STRICT p FROM life_financial.execution_policies WHERE account_id=p_account;
    ip:=p.body->lower(p_mode);pol:=CASE WHEN p_mode='INVENTORY' THEN ip->'policy' ELSE ip END;
    at_ms:=floor(extract(epoch FROM clock_timestamp())*1000)::bigint;
    IF NOT life_financial.execution_terms_valid(p.body) OR ip IS NULL OR ip='null'::jsonb THEN reason:='EXECUTION_MODE_DISABLED';
    ELSIF NOT life_financial.execution_market_valid(source) OR (source->>'observed_at_ms')::bigint>at_ms
        OR at_ms-(source->>'value_at_ms')::bigint>(ip->>'max_age_ms')::integer THEN reason:='EXECUTION_MARKET_UNQUALIFIED';END IF;
    IF reason IS NOT NULL THEN RETURN jsonb_build_object('allowed',false,'reason_code',reason,'trading_permission',false);END IF;
        SELECT (command->>'independent_price_usdt')::numeric INTO reference FROM life_financial.spot_nav_observations
            WHERE account_id=p_account ORDER BY sequence DESC LIMIT 1;
    IF EXISTS(SELECT 1 FROM life_financial.shared_capital_policies WHERE account_id=p_account) THEN
        SELECT (n.basis->>'index')::numeric INTO reference FROM life_financial.portfolio_observations n JOIN life_financial.operations o USING(account_id,operation_id)
            WHERE n.account_id=p_account ORDER BY o.committed_version DESC LIMIT 1;
    END IF;
    IF reference IS NULL OR reference IS DISTINCT FROM (source->>'independent_value_usdt')::numeric THEN
        RETURN jsonb_build_object('allowed',false,'reason_code','EXECUTION_REFERENCE_MISMATCH','trading_permission',false);END IF;
    SELECT total-reserved-liability INTO STRICT base FROM life_financial.balances WHERE account_id=p_account AND asset='LIFE';
    SELECT total-reserved-liability INTO STRICT quote FROM life_financial.balances WHERE account_id=p_account AND asset='USDT';
    SELECT * INTO prep FROM life_financial.spot_preparations WHERE account_id=p_account AND intent_id=p_intent;
    IF FOUND THEN
        base:=base+COALESCE((SELECT sum(remaining) FROM life_financial.reservations WHERE account_id=p_account AND intent_id=p_intent AND asset='LIFE' AND state='HELD'),0);
        quote:=quote+COALESCE((SELECT sum(remaining) FROM life_financial.reservations WHERE account_id=p_account AND intent_id=p_intent AND asset='USDT' AND state='HELD'),0);
    END IF;
    fee:=(source->>'fee_rate')::numeric;bid:=(source->>'best_bid_usdt')::numeric;ask:=(source->>'best_ask_usdt')::numeric;
    depth:=(source->>'independent_depth_base')::numeric;impact:=(source->>'expected_impact_quote')::numeric;
    rule:=CASE WHEN p_mode='INVENTORY' THEN pol ELSE p.body->'rules' END;
    tick:=(rule->>'tick_size')::numeric;lot:=(rule->>'lot_size')::numeric;min_size:=(rule->>'min_size_base')::numeric;
    budget:=life_financial.execution_budget_frame(p_account,CASE WHEN p_mode='EXIT' THEN 'EXIT' ELSE 'QUOTE' END,p_intent);
    IF p_mode='INVENTORY' THEN
        started_ms:=floor(extract(epoch FROM (ip->>'started_at')::timestamptz)*1000)::bigint;
        IF clock_timestamp()<(ip->>'started_at')::timestamptz OR clock_timestamp()>=(pol->>'deadline_utc')::timestamptz THEN reason:='INVENTORY_DEADLINE_EXPIRED';END IF;
        SELECT COALESCE(sum(f.quantity),0),COALESCE(sum(f.quantity*f.price),0),COALESCE(sum(
            greatest(0,CASE pol->>'side' WHEN 'BUY' THEN f.price-(pol->>'benchmark_price_usdt')::numeric ELSE (pol->>'benchmark_price_usdt')::numeric-f.price END*f.quantity)
            +greatest(0,-(fact.observation->>'signed_fee')::numeric*CASE fact.observation->>'fee_currency' WHEN 'LIFE' THEN f.independent_price ELSE 1 END)),0)
        INTO filled,filled_notional,used FROM life_financial.execution_children c JOIN life_financial.send_attempts t USING(account_id,intent_id)
            JOIN life_financial.fills f USING(account_id,product,wire_id) JOIN life_financial.spot_fill_facts fact USING(account_id,trade_id)
            WHERE c.account_id=p_account AND c.mode='INVENTORY' AND f.product='SPOT';
        SELECT COALESCE(sum(greatest(0,t.quantity-COALESCE(g.filled_base,0))),0),
            COALESCE(sum(greatest(0,t.quantity-COALESCE(g.filled_base,0))*t.price),0),
            COALESCE(sum(c.estimated_loss_quote),0)
        INTO pending,pending_notional,pending_cost FROM life_financial.execution_children c JOIN life_financial.send_attempts t USING(account_id,intent_id)
            LEFT JOIN life_financial.spot_accounting_progress g USING(account_id,intent_id)
            WHERE c.account_id=p_account AND c.mode='INVENTORY' AND c.intent_id IS DISTINCT FROM p_intent AND t.state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND');
        SELECT COALESCE(sum(quantity),0) INTO own FROM life_financial.fills WHERE account_id=p_account AND product='SPOT' AND floor(extract(epoch FROM fill_at)*1000)::bigint>=started_ms;
        own:=greatest(own,(source->>'known_own_volume_base')::numeric);volume:=(source->>'public_volume_base')::numeric-own;
        remain:=greatest(0,(pol->>'target_base')::numeric-filled-pending);
        price:=CASE pol->>'side' WHEN 'BUY' THEN div(bid,tick)*tick ELSE (div(ask,tick)+CASE WHEN mod(ask,tick)=0 THEN 0 ELSE 1 END)*tick END;
        IF price<=0 OR (CASE pol->>'side' WHEN 'BUY' THEN price>=ask ELSE price<=bid END) THEN RETURN jsonb_build_object('allowed',false,'reason_code','EXECUTION_POST_ONLY_INVALID','trading_permission',false);END IF;
        participation:=volume*(pol->>'participation_fraction')::numeric-filled-pending;
        notional_room:=(pol->>'max_total_notional_quote')::numeric-filled_notional-pending_notional;
        balance_lots:=CASE pol->>'side' WHEN 'BUY' THEN div(quote,price*(1+fee)*lot) ELSE div(base,lot) END;
        qty:=greatest(0,least(div(remain,lot),div((pol->>'max_child_base')::numeric,lot),div(participation,lot),div(depth,lot),div(notional_room,price*lot),balance_lots))*lot;
        estimated:=greatest(0,CASE pol->>'side' WHEN 'BUY' THEN price-(pol->>'benchmark_price_usdt')::numeric ELSE (pol->>'benchmark_price_usdt')::numeric-price END*qty)+impact+qty*price*fee;
        IF reason IS NULL THEN
            IF source->>'volume_anchor' IS DISTINCT FROM ip->>'campaign_id' OR own>(source->>'public_volume_base')::numeric THEN reason:='INVENTORY_SNAPSHOT_INVALID';
            ELSIF remain<=0 THEN reason:='INVENTORY_TARGET_FILLED_OR_PENDING';
            ELSIF volume<(pol->>'min_independent_volume_base')::numeric THEN reason:='INDEPENDENT_VOLUME_INSUFFICIENT';
            ELSIF depth<=0 THEN reason:='INDEPENDENT_DEPTH_UNAVAILABLE';
            ELSIF impact>(pol->>'max_impact_quote')::numeric THEN reason:='IMPACT_LIMIT_BREACHED';
            ELSIF used+pending_cost>=(pol->>'max_exit_loss_quote')::numeric THEN reason:='EXIT_BUDGET_EXHAUSTED';
            ELSIF (CASE pol->>'side' WHEN 'BUY' THEN price-(pol->>'benchmark_price_usdt')::numeric ELSE (pol->>'benchmark_price_usdt')::numeric-price END)*10000
                >(pol->>'benchmark_price_usdt')::numeric*(pol->>'max_slippage_bps')::numeric THEN reason:='PRICE_LIMIT_BREACHED';
            ELSIF participation<=0 THEN reason:='PARTICIPATION_CAP_REACHED';
            ELSIF notional_room<=0 THEN reason:='INVENTORY_NOTIONAL_CAP_REACHED';
            ELSIF balance_lots<=0 THEN reason:='INVENTORY_BALANCE_UNAVAILABLE';
            ELSIF qty<min_size THEN reason:='INVENTORY_CHILD_BELOW_MINIMUM';
            ELSIF used+pending_cost+estimated>(pol->>'max_exit_loss_quote')::numeric THEN reason:='EXIT_BUDGET_EXHAUSTED';END IF;
        END IF;
    ELSE
        remain:=greatest(0,base-(pol->>'target_base')::numeric-COALESCE(prep.fee_base,0));
        qty:=greatest(0,least(div((pol->>'max_child_base')::numeric,lot),div(remain,(1+fee)*lot)))*lot;
        price:=(div(ask,tick)+CASE WHEN mod(ask,tick)=0 THEN 0 ELSE 1 END)*tick;
        estimated:=greatest(0,reference-price)*qty+qty*price*fee+impact;
        IF qty<min_size OR qty>depth THEN reason:='EXIT_TARGET_OR_DEPTH_BLOCKED';
        ELSIF greatest(0,reference-price)*10000>reference*(pol->>'max_slippage_bps')::numeric THEN reason:='EXIT_SLIPPAGE_EXCEEDED';END IF;
    END IF;
    IF reason IS NULL AND estimated>(budget->>'available')::numeric THEN reason:='EXECUTION_ACCOUNT_BUDGET_DENIED';END IF;
    RETURN jsonb_build_object('allowed',reason IS NULL,'reason_code',COALESCE(reason,CASE WHEN p_mode='INVENTORY' THEN 'INVENTORY_CHILD_READY' ELSE 'EXIT_CHILD_READY' END),
        'price_usdt',price::text,'quantity_base',qty::text,'remaining_base',remain::text,'estimated_loss_quote',estimated::text,
        'side',CASE WHEN p_mode='INVENTORY' THEN pol->>'side' ELSE 'SELL' END,'trading_permission',false);
END; $$;

CREATE FUNCTION life_financial.execution_child_cost(p_account uuid,p_mode text,request jsonb,source jsonb) RETURNS numeric
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE benchmark numeric;price numeric;qty numeric;
BEGIN
    price:=(request->>'limit_price_usdt')::numeric;qty:=(request->>'quantity_base')::numeric;
    IF p_mode='INVENTORY' THEN
        SELECT (body#>>'{inventory,policy,benchmark_price_usdt}')::numeric INTO STRICT benchmark FROM life_financial.execution_policies WHERE account_id=p_account;
    ELSE benchmark:=(source->>'independent_value_usdt')::numeric;END IF;
    RETURN greatest(0,CASE request->>'side' WHEN 'BUY' THEN price-benchmark ELSE benchmark-price END*qty)
        +(source->>'expected_impact_quote')::numeric+qty*price*(source->>'fee_rate')::numeric;
END; $$;
CREATE FUNCTION life_financial.execution_attach_child() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE r life_financial.control_records;m life_financial.execution_market_facts;body jsonb;estimated numeric;
BEGIN
    IF NEW.receipt->>'kind' IS DISTINCT FROM 'SPOT_PREPARE' OR NOT EXISTS(SELECT 1 FROM life_financial.execution_policies WHERE account_id=NEW.account_id) THEN RETURN NULL;END IF;
    SELECT * INTO r FROM life_financial.control_records WHERE account_id=NEW.account_id AND body#>>'{payload,kind}'='EXECUTION_MODE' ORDER BY sequence DESC LIMIT 1;
    IF r.body#>>'{payload,mode}' NOT IN ('INVENTORY','EXIT') OR r.account_id IS NULL THEN RETURN NULL;END IF;
    SELECT * INTO m FROM life_financial.execution_market_facts WHERE account_id=NEW.account_id
        AND body#>>'{request,command,intent_id}'=NEW.receipt->>'intent_id' ORDER BY event_version DESC LIMIT 1;
    IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_EXECUTION_MARKET_REQUIRED' USING ERRCODE='P0001';END IF;
    estimated:=life_financial.execution_child_cost(NEW.account_id,r.body#>>'{payload,mode}',NEW.command->'command',m.body#>'{request,command}');
    body:=jsonb_build_object('prepare',NEW.command,'mode',r.body->'payload','market',m.body->'request','estimated_loss_quote',estimated::text);
    INSERT INTO life_financial.execution_children VALUES(NEW.account_id,NEW.receipt->>'intent_id',NEW.operation_id,r.body#>>'{payload,mode}',r.operation_id,m.operation_id,
        estimated,body,encode(sha256(convert_to(body::text,'UTF8')),'hex'));
    RETURN NULL;
END; $$;
CREATE TRIGGER attach_native_execution_child AFTER INSERT ON life_financial.operations FOR EACH ROW EXECUTE FUNCTION life_financial.execution_attach_child();
CREATE FUNCTION life_financial.execution_children_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE c life_financial.execution_children;o life_financial.operations;r life_financial.control_records;m life_financial.execution_market_facts;expected jsonb;
BEGIN
    FOR c IN SELECT * FROM life_financial.execution_children WHERE account_id=p_account LOOP
        SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=c.prepare_operation_id;
        SELECT * INTO STRICT r FROM life_financial.control_records WHERE account_id=p_account AND operation_id=c.mode_operation_id;
        SELECT * INTO STRICT m FROM life_financial.execution_market_facts WHERE account_id=p_account AND operation_id=c.market_operation_id;
        expected:=jsonb_build_object('prepare',o.command,'mode',r.body->'payload','market',m.body->'request','estimated_loss_quote',c.estimated_loss_quote::text);
        IF c.source_hash<>encode(sha256(convert_to(c.body::text,'UTF8')),'hex') OR c.body IS DISTINCT FROM expected
            OR o.receipt->>'kind' IS DISTINCT FROM 'SPOT_PREPARE' OR o.receipt->>'intent_id' IS DISTINCT FROM c.intent_id
            OR r.body#>>'{payload,mode}' IS DISTINCT FROM c.mode OR m.body->>'mode_operation_id' IS DISTINCT FROM r.operation_id
            OR m.body#>>'{request,command,intent_id}' IS DISTINCT FROM c.intent_id
            OR NOT life_financial.execution_record_qualified(p_account,r.operation_id)
            OR c.estimated_loss_quote IS DISTINCT FROM life_financial.execution_child_cost(p_account,c.mode,o.command->'command',m.body#>'{request,command}')
            OR NOT EXISTS(SELECT 1 FROM life_financial.spot_preparations d WHERE d.account_id=p_account AND d.operation_id=o.operation_id AND d.intent_id=c.intent_id AND d.request=o.command->'command')
            OR (SELECT original.committed_version FROM life_financial.operations original WHERE original.account_id=p_account AND original.operation_id=m.operation_id)>=o.committed_version
            THEN RETURN false;END IF;
    END LOOP;
    RETURN NOT EXISTS(SELECT 1 FROM life_financial.spot_preparations d JOIN life_financial.operations original USING(account_id,operation_id)
        CROSS JOIN LATERAL (SELECT mode.body#>>'{payload,mode}' AS name FROM life_financial.control_records mode WHERE mode.account_id=p_account
            AND mode.body#>>'{payload,kind}'='EXECUTION_MODE' AND (mode.body#>>'{receipt,version}')::bigint<original.committed_version ORDER BY mode.sequence DESC LIMIT 1) prior
        WHERE d.account_id=p_account AND prior.name IN ('INVENTORY','EXIT') AND NOT EXISTS(SELECT 1 FROM life_financial.execution_children child WHERE child.account_id=p_account AND child.intent_id=d.intent_id));
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
CREATE OR REPLACE FUNCTION life_financial.execution_admit(p_account uuid,op life_financial.operations) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE a life_financial.accounts;r life_financial.control_records;m life_financial.execution_market_facts;d life_financial.spot_preparations;
    mode text;decision jsonb;source jsonb;budget jsonb;item jsonb;candidate numeric;price numeric;fee numeric;state text;
BEGIN
    IF NOT EXISTS(SELECT 1 FROM life_financial.execution_policies WHERE account_id=p_account) THEN RETURN;END IF;
    IF NOT life_financial.control_verified(p_account) OR NOT life_financial.execution_policy_qualified(p_account)
        OR NOT life_financial.execution_market_verified(p_account) OR NOT life_financial.execution_children_verified(p_account)
        OR NOT life_financial.execution_exit_verified(p_account) THEN
        RAISE EXCEPTION 'LIFE_EXECUTION_UNVERIFIED' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account;
    SELECT * INTO r FROM life_financial.control_records WHERE account_id=p_account AND body#>>'{payload,kind}'='EXECUTION_MODE' ORDER BY sequence DESC LIMIT 1;
    IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_EXECUTION_MODE_REQUIRED' USING ERRCODE='P0001';END IF;
    IF NOT life_financial.execution_record_qualified(p_account,r.operation_id)
        OR r.body#>>'{before_account,owner_generation}' IS DISTINCT FROM a.owner_generation::text
        OR r.body#>>'{before_account,current_session}' IS DISTINCT FROM a.current_session::text
        OR r.body#>>'{payload,observer_id}' IS DISTINCT FROM current_setting('life.risk_observer',true) THEN
        RAISE EXCEPTION 'LIFE_EXECUTION_FRESH_MODE_REQUIRED' USING ERRCODE='P0001';END IF;
    mode:=r.body#>>'{payload,mode}';
    IF mode='MM' THEN
        IF op.receipt->>'kind' IN ('SPOT_PREPARE','SPOT_ARM','SPOT_QUOTE_ARM') THEN
            IF EXISTS(SELECT 1 FROM life_financial.spot_preparations WHERE account_id=p_account AND intent_id=op.receipt->>'intent_id' AND request->>'budget_scope'='EXIT') THEN
                RAISE EXCEPTION 'LIFE_EXECUTION_SCOPE_MISMATCH' USING ERRCODE='P0001';END IF;
            PERFORM life_financial.execution_quote_budget_check(p_account);
        END IF;
        RETURN;
    END IF;
    IF COALESCE(op.receipt->>'kind','') NOT IN ('SPOT_PREPARE','SPOT_ARM','SPOT_QUOTE_ARM') THEN
        RAISE EXCEPTION 'LIFE_EXECUTION_MODE_ROUTE_DENIED' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT d FROM life_financial.spot_preparations WHERE account_id=p_account AND intent_id=op.receipt->>'intent_id';
    IF COALESCE(d.request->>'budget_scope','QUOTE') IS DISTINCT FROM (CASE mode WHEN 'EXIT' THEN 'EXIT' ELSE 'QUOTE' END)
        OR NOT EXISTS(SELECT 1 FROM life_financial.execution_children c WHERE c.account_id=p_account AND c.intent_id=d.intent_id AND c.mode_operation_id=r.operation_id)
        OR EXISTS(SELECT 1 FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id<>d.intent_id AND state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND')) THEN
        RAISE EXCEPTION 'LIFE_EXECUTION_SCOPE_OR_PENDING_MISMATCH' USING ERRCODE='P0001';END IF;
    SELECT * INTO m FROM life_financial.execution_market_facts WHERE account_id=p_account AND body#>>'{request,command,intent_id}'=d.intent_id ORDER BY event_version DESC LIMIT 1;
    IF NOT FOUND OR m.body->>'mode_operation_id' IS DISTINCT FROM r.operation_id OR m.body->'rollback_latched' IS DISTINCT FROM 'false'::jsonb
        OR m.body->>'observer_id' IS DISTINCT FROM current_setting('life.risk_observer',true)
        OR (m.body->>'risk_epoch')::bigint IS DISTINCT FROM a.risk_epoch
        OR m.body#>>'{request,owner,generation}' IS DISTINCT FROM a.owner_generation::text
        OR m.body#>>'{request,owner,session_id}' IS DISTINCT FROM a.current_session::text THEN
        RAISE EXCEPTION 'LIFE_EXECUTION_FRESH_MARKET_REQUIRED' USING ERRCODE='P0001';END IF;
    SELECT body#>>'{decision,state}' INTO state FROM life_financial.risk_events WHERE account_id=p_account ORDER BY event_version DESC LIMIT 1;
    IF mode='INVENTORY' AND (state IS DISTINCT FROM 'NORMAL' OR life_financial.risk_markout_status(p_account)->>'reason_code' NOT IN ('MARKOUT_NOT_CONFIGURED','MARKOUT_READY')) THEN
        RAISE EXCEPTION 'LIFE_EXECUTION_NORMAL_READY_REQUIRED' USING ERRCODE='P0001';END IF;
    source:=m.body#>'{request,command}';decision:=life_financial.execution_decision(p_account,source,mode,d.intent_id);
    IF decision->'allowed' IS DISTINCT FROM 'true'::jsonb THEN RAISE EXCEPTION 'LIFE_%',decision->>'reason_code' USING ERRCODE='P0001';END IF;
    candidate:=(d.request->>'quantity_base')::numeric;price:=(d.request->>'limit_price_usdt')::numeric;fee:=(source->>'fee_rate')::numeric;
    IF candidate>(decision->>'quantity_base')::numeric
        OR mod(candidate,(SELECT (body#>>CASE mode WHEN 'EXIT' THEN ARRAY['rules','lot_size'] ELSE ARRAY['inventory','policy','lot_size'] END)::numeric FROM life_financial.execution_policies WHERE account_id=p_account))<>0
        OR candidate<(SELECT (body#>>CASE mode WHEN 'EXIT' THEN ARRAY['rules','min_size_base'] ELSE ARRAY['inventory','policy','min_size_base'] END)::numeric FROM life_financial.execution_policies WHERE account_id=p_account) OR price<>(decision->>'price_usdt')::numeric
        OR d.request->>'side' IS DISTINCT FROM decision->>'side' OR (d.request->>'reference_price_usdt')::numeric<>(source->>'independent_value_usdt')::numeric
        OR d.fee_base<candidate*fee OR d.fee_quote<candidate*price*fee
        OR d.subsidy<life_financial.execution_child_cost(p_account,mode,d.request,source) THEN
        RAISE EXCEPTION 'LIFE_EXECUTION_CHILD_PRICE_SIZE_OR_COST_DENIED' USING ERRCODE='P0001';END IF;
    budget:=life_financial.execution_budget_frame(p_account,CASE mode WHEN 'EXIT' THEN 'EXIT' ELSE 'QUOTE' END);
    FOR item IN SELECT * FROM jsonb_array_elements(budget->'windows') LOOP
        IF (item->>'committed')::numeric+(item->>'incurred')::numeric>(item->>'limit')::numeric THEN
            RAISE EXCEPTION 'LIFE_EXECUTION_ACCOUNT_BUDGET_DENIED' USING ERRCODE='P0001';END IF;
    END LOOP;
END; $$;
CREATE FUNCTION life_financial.execution_market_frame(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE r life_financial.control_records;m life_financial.execution_market_facts;decision jsonb;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_ACCESS_DENIED' USING ERRCODE='P0001';END IF;
    IF NOT life_financial.control_verified(p_account) OR NOT life_financial.execution_policy_qualified(p_account)
        OR NOT life_financial.execution_market_verified(p_account) OR NOT life_financial.execution_children_verified(p_account)
        OR NOT life_financial.execution_exit_verified(p_account) THEN
        RAISE EXCEPTION 'LIFE_EXECUTION_UNVERIFIED' USING ERRCODE='P0001';END IF;
    SELECT * INTO r FROM life_financial.control_records WHERE account_id=p_account AND body#>>'{payload,kind}'='EXECUTION_MODE' ORDER BY sequence DESC LIMIT 1;
    SELECT * INTO m FROM life_financial.execution_market_facts WHERE account_id=p_account ORDER BY event_version DESC LIMIT 1;
    IF m.account_id IS NULL OR m.body->>'mode_operation_id' IS DISTINCT FROM r.operation_id OR m.body->'rollback_latched' IS DISTINCT FROM 'false'::jsonb THEN
        decision:=jsonb_build_object('allowed',false,'reason_code','EXECUTION_FRESH_MARKET_REQUIRED','trading_permission',false);
    ELSE decision:=life_financial.execution_decision(p_account,m.body#>'{request,command}',r.body#>>'{payload,mode}',m.body#>>'{request,command,intent_id}');END IF;
    RETURN jsonb_build_object('decision',decision,'quote_budget',life_financial.execution_budget_frame(p_account,'QUOTE'),
        'exit_budget',life_financial.execution_budget_frame(p_account,'EXIT'),'trading_permission',false);
END; $$;
CREATE FUNCTION life_financial.execution_enabled(p_account uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT life_financial.can_access(p_account) AND EXISTS(SELECT 1 FROM life_financial.execution_policies WHERE account_id=p_account);
$$;
CREATE FUNCTION life_financial.execution_market_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE op life_financial.operations;m life_financial.execution_market_facts;p life_financial.execution_policies;mode text;at_ms bigint;
BEGIN
    IF NOT EXISTS(SELECT 1 FROM life_financial.execution_policies WHERE account_id=NEW.account_id) THEN RETURN NULL;END IF;
    SELECT * INTO STRICT op FROM life_financial.operations WHERE account_id=NEW.account_id AND committed_version=NEW.version;
    IF op.command->>'kind'='SPOT_EXIT_SETTLE' THEN PERFORM life_financial.execution_exit_commit_check(NEW.account_id,op.command);RETURN NULL;END IF;
    IF op.command->>'kind'<>'EXECUTION_OBSERVE' OR op.command->>'kind' IS NULL THEN RETURN NULL;END IF;
    IF NOT life_financial.execution_market_verified(NEW.account_id) THEN RAISE EXCEPTION 'LIFE_EXECUTION_UNVERIFIED' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT m FROM life_financial.execution_market_facts WHERE account_id=NEW.account_id AND operation_id=op.operation_id;
    SELECT * INTO STRICT p FROM life_financial.execution_policies WHERE account_id=NEW.account_id;
    SELECT body#>>'{payload,mode}' INTO STRICT mode FROM life_financial.control_records WHERE account_id=NEW.account_id AND operation_id=m.body->>'mode_operation_id';
    at_ms:=floor(extract(epoch FROM clock_timestamp())*1000)::bigint;
    IF (m.body#>>'{request,command,observed_at_ms}')::bigint>at_ms OR at_ms-(m.body#>>'{request,command,value_at_ms}')::bigint>(p.body#>>ARRAY[lower(mode),'max_age_ms'])::integer THEN
        RAISE EXCEPTION 'LIFE_EXECUTION_MARKET_STALE_AT_COMMIT' USING ERRCODE='P0001';END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER zzzzzz_native_execution_market_commit AFTER UPDATE ON life_financial.accounts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN(NEW.version<>OLD.version) EXECUTE FUNCTION life_financial.execution_market_commit_guard();

CREATE OR REPLACE FUNCTION life_financial.execution_policy_qualified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE e life_financial.execution_policies;p life_financial.spot_policies;k text;q numeric;x numeric;
BEGIN
    SELECT s.* INTO STRICT e FROM life_financial.execution_policies s JOIN life_financial.accounts a USING(account_id,policy_hash) WHERE s.account_id=p_account;
    SELECT * INTO STRICT p FROM life_financial.spot_policies WHERE account_id=p_account AND policy_hash=e.policy_hash;
    IF e.policy_digest<>encode(sha256(convert_to(life_financial.canonical_json_v1(e.body),'UTF8')),'hex')
        OR jsonb_typeof(e.body->'campaign_id') IS DISTINCT FROM 'string' OR length(e.body->>'campaign_id') NOT BETWEEN 1 AND 96 THEN RETURN false;END IF;
    FOREACH k IN ARRAY ARRAY['campaign','day','session'] LOOP
        q:=(e.body#>>ARRAY['quote_budget',k])::life_financial.positive_amount;
        x:=(e.body#>>ARRAY['exit_budget',k])::life_financial.positive_amount;
        IF q IS NULL OR x IS NULL OR q+x IS DISTINCT FROM (CASE k WHEN 'campaign' THEN p.subsidy_campaign WHEN 'day' THEN p.subsidy_day ELSE p.subsidy_session END) THEN RETURN false;END IF;
    END LOOP;
    IF NOT life_financial.execution_terms_valid(e.body) THEN RETURN false;END IF;
    RETURN (e.body#>>'{quote_budget,session}')::numeric<=(e.body#>>'{quote_budget,day}')::numeric
        AND (e.body#>>'{quote_budget,day}')::numeric<=(e.body#>>'{quote_budget,campaign}')::numeric
        AND (e.body#>>'{exit_budget,session}')::numeric<=(e.body#>>'{exit_budget,day}')::numeric
        AND (e.body#>>'{exit_budget,day}')::numeric<=(e.body#>>'{exit_budget,campaign}')::numeric;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
DO $$ DECLARE f record;definition text;needle text;replacement text;BEGIN
    SELECT * INTO STRICT f FROM pg_proc WHERE oid='life_financial.commit_owner_guard()'::regprocedure;
    definition:=pg_get_functiondef(f.oid);needle:='(''STRESS_OBSERVE'',''RISK_OBSERVE''';replacement:='(''SPOT_EXIT_SETTLE'',''EXECUTION_OBSERVE'',''STRESS_OBSERVE'',''RISK_OBSERVE''';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_EXECUTION_CHILD_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
    SELECT * INTO STRICT f FROM pg_proc WHERE oid='life_financial.spot_prepare_action_core(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb,jsonb)'::regprocedure;
    definition:=pg_get_functiondef(f.oid);needle:='THEN 16 ELSE 15 END)';replacement:='THEN 16 ELSE 15 END)+(CASE WHEN p_command->>''budget_scope''=''EXIT'' AND EXISTS(SELECT 1 FROM life_financial.execution_policies WHERE account_id=p_account) AND p_command->>''side''=''SELL'' THEN 1 ELSE 0 END)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_EXECUTION_CHILD_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
    SELECT * INTO STRICT f FROM pg_proc WHERE oid='life_financial.risk_admit(uuid,life_financial.operations)'::regprocedure;
    definition:=pg_get_functiondef(f.oid);needle:='IF d->>''state''=''DEGRADED'' THEN';replacement:='IF d->>''state''=''DEGRADED'' AND NOT (prep.request->>''budget_scope'' IS NOT DISTINCT FROM ''EXIT'' AND EXISTS(SELECT 1 FROM life_financial.execution_policies WHERE account_id=p_account)) THEN';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_EXECUTION_CHILD_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
    SELECT * INTO STRICT f FROM pg_proc WHERE oid='life_financial.risk_markout_admit(uuid,life_financial.operations)'::regprocedure;
    definition:=pg_get_functiondef(f.oid);needle:='    status:=life_financial.risk_markout_status(p_account);';replacement:='    IF op.receipt->>''kind'' IN (''SPOT_PREPARE'',''SPOT_ARM'',''SPOT_QUOTE_ARM'') AND EXISTS(SELECT 1 FROM life_financial.spot_preparations d WHERE d.account_id=p_account AND d.intent_id=op.receipt->>''intent_id'' AND d.request->>''budget_scope''=''EXIT'') AND EXISTS(SELECT 1 FROM life_financial.execution_policies WHERE account_id=p_account) THEN RETURN;END IF;
    status:=life_financial.risk_markout_status(p_account);';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_EXECUTION_CHILD_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;
CREATE FUNCTION life_financial.execution_exit_evidence(p_account uuid,p_intent text) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE c life_financial.execution_children;d life_financial.spot_preparations;t life_financial.send_attempts;g life_financial.spot_accounting_progress;
    terminal life_financial.spot_terminal_facts;fills jsonb;actual numeric;held numeric;
BEGIN
    SELECT * INTO STRICT c FROM life_financial.execution_children WHERE account_id=p_account AND intent_id=p_intent AND mode='EXIT';
    SELECT * INTO STRICT d FROM life_financial.spot_preparations WHERE account_id=p_account AND intent_id=p_intent AND request->>'budget_scope'='EXIT';
    SELECT * INTO STRICT t FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=p_intent AND product='SPOT' AND side='SELL' AND state='TERMINAL';
    SELECT * INTO STRICT terminal FROM life_financial.spot_terminal_facts WHERE account_id=p_account AND intent_id=p_intent;
    SELECT * INTO g FROM life_financial.spot_accounting_progress WHERE account_id=p_account AND intent_id=p_intent;
    IF t.exchange_order_id IS NULL OR EXISTS(SELECT 1 FROM life_financial.reservations WHERE account_id=p_account AND intent_id=p_intent AND (remaining<>0 OR state<>'TERMINAL'))
        OR (terminal.command#>>'{facts,cumulative_base_life}')::numeric IS DISTINCT FROM COALESCE(g.filled_base,0)
        OR EXISTS(SELECT 1 FROM life_financial.spot_inventory_members WHERE account_id=p_account AND intent_id=p_intent) THEN
        RAISE EXCEPTION 'LIFE_EXIT_SETTLEMENT_TERMINAL_UNPROVEN' USING ERRCODE='P0001';END IF;
    SELECT COALESCE(jsonb_agg(jsonb_build_array(f.trade_id,f.observation) ORDER BY (f.observation->>'fill_at_ms')::bigint,f.trade_id COLLATE "C"),'[]'::jsonb),
        COALESCE(sum(greatest(0,-(f.observation->>'signed_fee')::numeric*CASE f.observation->>'fee_currency' WHEN 'LIFE' THEN (f.observation->>'independent_price_usdt')::numeric ELSE 1 END
            +((f.observation->>'independent_price_usdt')::numeric-(f.observation->>'price_usdt')::numeric)*(f.observation->>'quantity_base_life')::numeric)),0)
        INTO fills,actual FROM life_financial.spot_fill_facts f WHERE f.account_id=p_account AND f.intent_id=p_intent;
    IF actual IS DISTINCT FROM COALESCE(g.execution_loss,0) THEN RAISE EXCEPTION 'LIFE_EXIT_SETTLEMENT_COST_UNPROVEN' USING ERRCODE='P0001';END IF;
    held:=CASE WHEN COALESCE(g.filled_base,0)=0 THEN 0 ELSE greatest(d.subsidy,g.execution_loss) END;
    RETURN jsonb_build_object('intent_id',p_intent,'child',c.source_hash,'prepare',d.request,'terminal',terminal.command,'fills',fills,'actual_usdt',actual::text,'held_before_usdt',held::text);
END; $$;
CREATE FUNCTION life_financial.execution_exit_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE x life_financial.execution_exit_settlements;o life_financial.operations;evidence jsonb;member record;scope_name text;key text;
BEGIN
    IF EXISTS(SELECT 1 FROM life_financial.operations original WHERE original.account_id=p_account AND original.command->>'kind'='SPOT_EXIT_SETTLE' AND NOT EXISTS(
        SELECT 1 FROM life_financial.execution_exit_settlements settled WHERE settled.account_id=p_account AND settled.operation_id=original.operation_id)) THEN RETURN false;END IF;
    FOR x IN SELECT * FROM life_financial.execution_exit_settlements WHERE account_id=p_account LOOP
        SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=x.operation_id;
        evidence:=life_financial.execution_exit_evidence(p_account,x.intent_id);
        IF x.source_hash<>encode(sha256(convert_to(x.body::text,'UTF8')),'hex') OR x.body IS DISTINCT FROM evidence
            OR x.actual_usdt IS DISTINCT FROM (evidence->>'actual_usdt')::numeric OR x.held_before_usdt IS DISTINCT FROM (evidence->>'held_before_usdt')::numeric
            OR o.command->>'kind' IS DISTINCT FROM 'SPOT_EXIT_SETTLE' OR o.command#>>'{command,intent_id}' IS DISTINCT FROM x.intent_id
            OR o.command#>>'{command,operation_id}' IS DISTINCT FROM x.operation_id
            OR o.receipt IS DISTINCT FROM jsonb_build_object('kind','SPOT_EXIT_SETTLE','operation_id',x.operation_id,'intent_id',x.intent_id,'actual_usdt',x.actual_usdt::text,
                'held_before_usdt',x.held_before_usdt::text,'state','SETTLED','version',o.committed_version,'replayed',false,'trading_permission',false)
            THEN RETURN false;END IF;
        SELECT d.prepared_at,t.session_id,p.campaign_id INTO STRICT member FROM life_financial.spot_preparations d
            JOIN life_financial.send_attempts t USING(account_id,intent_id) JOIN life_financial.spot_policies p USING(account_id) WHERE d.account_id=p_account AND d.intent_id=x.intent_id;
        FOREACH scope_name IN ARRAY ARRAY['CAMPAIGN','DAY','SESSION'] LOOP
            key:=CASE scope_name WHEN 'CAMPAIGN' THEN member.campaign_id WHEN 'DAY' THEN to_char(member.prepared_at AT TIME ZONE 'UTC','YYYY-MM-DD') ELSE member.session_id::text END;
            IF NOT EXISTS(SELECT 1 FROM life_financial.budget_events WHERE account_id=p_account AND family='SUBSIDY' AND scope=scope_name AND window_key=key
                AND event_id='exit-release:'||encode(sha256(convert_to(x.operation_id||':'||scope_name,'UTF8')),'hex') AND kind='RELEASE' AND amount=x.held_before_usdt)
                OR NOT EXISTS(SELECT 1 FROM life_financial.budget_events WHERE account_id=p_account AND family='SUBSIDY' AND scope=scope_name AND window_key=key
                AND event_id='exit-spend:'||encode(sha256(convert_to(x.operation_id||':'||scope_name,'UTF8')),'hex') AND kind='SPEND' AND amount=x.actual_usdt) THEN RETURN false;END IF;
        END LOOP;
    END LOOP;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
CREATE FUNCTION life_financial.execution_exit_commit_check(p_account uuid,request jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE at timestamptz;observed timestamptz;age integer;
BEGIN
    SELECT (body#>>'{exit,max_age_ms}')::integer INTO STRICT age FROM life_financial.execution_policies WHERE account_id=p_account;
    at:=clock_timestamp();observed:=(request#>>'{facts,observed_at}')::timestamptz;
    IF observed IS NULL OR age IS NULL OR NOT isfinite(observed) OR observed>at OR extract(epoch FROM at-observed)*1000>age
        OR NOT life_financial.execution_facts_qualified(p_account,request->'facts') OR NOT life_financial.execution_exit_verified(p_account)
        OR NOT life_financial.spot_lifecycle_verified(p_account) THEN RAISE EXCEPTION 'LIFE_EXIT_SETTLEMENT_FACTS_OR_PROJECTION_INVALID' USING ERRCODE='P0001';END IF;
END; $$;
CREATE FUNCTION life_financial.execution_exit_settle(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,p_cut life_financial.digest,
    p_fingerprint life_financial.digest,p_command jsonb,p_facts jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE a life_financial.accounts;old life_financial.operations;request jsonb;evidence jsonb;result jsonb;op life_financial.identity;intent life_financial.identity;
    held numeric;actual numeric;scope_name text;key text;member record;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR p_cut IS NULL OR p_fingerprint IS NULL OR jsonb_typeof(p_command) IS DISTINCT FROM 'object'
        OR (SELECT count(*) FROM jsonb_object_keys(p_command))<>2 OR NOT p_command ?& ARRAY['operation_id','intent_id']
        OR EXISTS(SELECT 1 FROM jsonb_each(p_command) WHERE jsonb_typeof(value)<>'string') OR octet_length(p_facts::text)>1048576 THEN
        RAISE EXCEPTION 'LIFE_EXIT_SETTLEMENT_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
    op:=p_command->>'operation_id';intent:=p_command->>'intent_id';
    IF op IS NULL OR intent IS NULL OR length(op)>96 OR length(intent)>96 THEN RAISE EXCEPTION 'LIFE_EXIT_SETTLEMENT_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    request:=jsonb_build_object('kind','SPOT_EXIT_SETTLE','binding',p_binding,'owner',p_owner,'command',p_command,'facts',p_facts);
    SELECT * INTO old FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF old.command->>'kind' IS DISTINCT FROM 'SPOT_EXIT_SETTLE' OR old.command->'binding' IS DISTINCT FROM p_binding OR old.command->'owner' IS DISTINCT FROM p_owner
            OR old.command->'command' IS DISTINCT FROM p_command OR old.fingerprint<>p_fingerprint OR NOT life_financial.execution_exit_verified(p_account) THEN
            RAISE EXCEPTION 'LIFE_EXIT_SETTLEMENT_OPERATION_CONFLICT' USING ERRCODE='P0001';END IF;
        RETURN old.receipt||'{"replayed":true}'::jsonb;
    END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    IF (life_financial.spot_state(p_account)->>'digest') IS DISTINCT FROM p_cut OR NOT life_financial.execution_children_verified(p_account)
        OR NOT life_financial.execution_exit_verified(p_account) OR NOT life_financial.execution_policy_qualified(p_account)
        OR NOT life_financial.execution_facts_qualified(p_account,p_facts) THEN RAISE EXCEPTION 'LIFE_EXIT_SETTLEMENT_FACTS_UNQUALIFIED' USING ERRCODE='P0001';END IF;
    IF EXISTS(SELECT 1 FROM life_financial.execution_exit_settlements WHERE account_id=p_account AND intent_id=intent) THEN
        RAISE EXCEPTION 'LIFE_EXIT_SETTLEMENT_ALREADY_SETTLED' USING ERRCODE='P0001';END IF;
    evidence:=life_financial.execution_exit_evidence(p_account,intent);held:=(evidence->>'held_before_usdt')::numeric;actual:=(evidence->>'actual_usdt')::numeric;
    SELECT d.prepared_at,t.session_id,p.campaign_id INTO STRICT member FROM life_financial.spot_preparations d
        JOIN life_financial.send_attempts t USING(account_id,intent_id) JOIN life_financial.spot_policies p USING(account_id) WHERE d.account_id=p_account AND d.intent_id=intent;
    FOREACH scope_name IN ARRAY ARRAY['CAMPAIGN','DAY','SESSION'] LOOP
        key:=CASE scope_name WHEN 'CAMPAIGN' THEN member.campaign_id WHEN 'DAY' THEN to_char(member.prepared_at AT TIME ZONE 'UTC','YYYY-MM-DD') ELSE member.session_id::text END;
        UPDATE life_financial.budget_buckets SET committed=committed-held,incurred=incurred+actual,version=version+1
            WHERE account_id=p_account AND family='SUBSIDY' AND scope=scope_name AND window_key=key AND committed>=held;
        IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_EXIT_SETTLEMENT_HOLD_UNPROVEN' USING ERRCODE='P0001';END IF;
        INSERT INTO life_financial.budget_events VALUES(p_account,'exit-release:'||encode(sha256(convert_to(op||':'||scope_name,'UTF8')),'hex'),'SUBSIDY',scope_name,key,'RELEASE',held);
        INSERT INTO life_financial.budget_events VALUES(p_account,'exit-spend:'||encode(sha256(convert_to(op||':'||scope_name,'UTF8')),'hex'),'SUBSIDY',scope_name,key,'SPEND',actual);
    END LOOP;
    INSERT INTO life_financial.execution_exit_settlements VALUES(p_account,intent,op,held,actual,evidence,encode(sha256(convert_to(evidence::text,'UTF8')),'hex'));
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    result:=jsonb_build_object('kind','SPOT_EXIT_SETTLE','operation_id',op,'intent_id',intent,'actual_usdt',actual::text,'held_before_usdt',held::text,
        'state','SETTLED','version',a.version+1,'replayed',false,'trading_permission',false);
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version) VALUES(p_account,op,p_fingerprint,request,result,a.version+1);
    PERFORM life_financial.execution_exit_commit_check(p_account,request);
    RETURN result;
END; $$;
CREATE FUNCTION life_financial.execution_quote_budget_frame(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_ACCESS_DENIED' USING ERRCODE='P0001';END IF;
    IF NOT EXISTS(SELECT 1 FROM life_financial.execution_policies WHERE account_id=p_account) THEN RETURN NULL;END IF;
    IF NOT life_financial.execution_policy_qualified(p_account) OR NOT life_financial.execution_children_verified(p_account)
        OR NOT life_financial.execution_exit_verified(p_account) THEN RAISE EXCEPTION 'LIFE_EXECUTION_UNVERIFIED' USING ERRCODE='P0001';END IF;
    RETURN life_financial.execution_budget_frame(p_account,'QUOTE');
END; $$;
DO $$ DECLARE f record;r record;BEGIN
    FOR f IN SELECT p.oid::regprocedure AS signature,p.proowner FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='life_financial' AND p.proname LIKE 'execution_%' LOOP
        FOR r IN SELECT DISTINCT x.grantee FROM pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
            WHERE p.oid=f.signature AND x.grantee<>f.proowner LOOP
            IF r.grantee=0 THEN EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',f.signature);
            ELSE EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',f.signature,pg_get_userbyid(r.grantee));END IF;
        END LOOP;
    END LOOP;
    FOR r IN SELECT DISTINCT principal FROM life_financial.access_bindings WHERE has_function_privilege(principal,'life_financial.spot_state(uuid)','EXECUTE') LOOP
        EXECUTE format('GRANT EXECUTE ON FUNCTION life_financial.execution_enabled(uuid) TO %I',r.principal);
    END LOOP;
END; $$;
DO $$ DECLARE f record;definition text;needle text;BEGIN
    SELECT * INTO STRICT f FROM pg_proc WHERE oid='life_financial.spot_lifecycle_verified(uuid)'::regprocedure;
    IF NOT f.prosecdef OR f.provolatile<>'s' OR f.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog'] OR encode(sha256(convert_to(f.prosrc,'UTF8')),'hex')<>'eb95c4de5cff4929dec9acba374488f94b95c2f1915f94efaea128226c37efb1' THEN
        RAISE EXCEPTION 'LIFE_EXECUTION_CHILD_MIGRATION_MISMATCH';END IF;
    definition:=pg_get_functiondef(f.oid);needle:='SELECT life_financial.spot_inventory_settlements_verified(p_account) AND NOT EXISTS(';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_EXECUTION_CHILD_MIGRATION_MISMATCH';END IF;
    definition:=replace(definition,needle,'SELECT life_financial.execution_exit_verified(p_account) AND life_financial.spot_inventory_settlements_verified(p_account) AND NOT EXISTS(');
    needle:='AND NOT EXISTS(SELECT 1 FROM life_financial.spot_inventory_members m WHERE m.account_id=d.account_id AND m.intent_id=d.intent_id)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_EXECUTION_CHILD_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,needle||' AND NOT EXISTS(SELECT 1 FROM life_financial.execution_exit_settlements settled WHERE settled.account_id=d.account_id AND settled.intent_id=d.intent_id)');
END; $$;
