-- Explicit opt-in metadata/atomic-prepare foundation. No policy bootstrap and
-- no service/runner/HTTP capability. Applied predecessor files remain immutable.
DO $$ DECLARE p record;BEGIN
    FOR p IN SELECT f.*,l.lanname FROM pg_proc f JOIN pg_language l ON l.oid=f.prolang WHERE f.oid IN (
        'life_financial.spot_body(uuid)'::regprocedure,
        'life_financial.spot_action_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)'::regprocedure,
        'life_financial.spot_prepare_action_core(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb,jsonb)'::regprocedure,
        'life_financial.spot_actions_verified(uuid)'::regprocedure,
        'life_financial.spot_prepare_receipts_verified(uuid)'::regprocedure) LOOP
        IF p.lanname<>'plpgsql' OR NOT p.prosecdef OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR p.proisstrict OR p.proleakproof OR p.proparallel<>'u' OR p.proretset OR p.prokind<>'f' OR p.pronargdefaults<>0
            OR p.provolatile<>(CASE WHEN p.proname IN ('spot_action_command','spot_prepare_action_core') THEN 'v' ELSE 's' END)
            OR p.prorettype<>(CASE WHEN p.proname IN ('spot_actions_verified','spot_prepare_receipts_verified') THEN 'boolean'::regtype ELSE 'jsonb'::regtype END)
            OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') IS DISTINCT FROM (CASE p.proname
                WHEN 'spot_body' THEN 'a937c042ca49aae72cba2635f53c480fc5389dd80f059d72558a60580410131a'
                WHEN 'spot_action_command' THEN 'e4e24eba88c8ca49df9f75ebad9ec4e678ef0a5307864f7dd4142d66f6f2286e'
                WHEN 'spot_prepare_action_core' THEN 'd704afb3948163ebbb3502eab24f69f7fe002883135b35cd24b7b8c35be4c99a'
                WHEN 'spot_actions_verified' THEN 'eba036f199794fd21f1aa53f5e437deb56b7a450c40b17cbf20ccdd93247beae'
                WHEN 'spot_prepare_receipts_verified' THEN '33eaad35b6e7223ca5b006646b8d7db7c92224054ff9b438a6f2cfae4563fb79' END) THEN
            RAISE EXCEPTION 'LIFE_QUOTE_BIRTH_MIGRATION_MISMATCH';END IF;
    END LOOP;
END; $$;
CREATE TABLE life_financial.quote_birth_policies (
    account_id uuid PRIMARY KEY REFERENCES life_financial.accounts,
    policy_hash life_financial.digest NOT NULL,
    max_tickets integer NOT NULL CHECK(max_tickets BETWEEN 1 AND 4096),
    max_total_bytes bigint NOT NULL CHECK(max_total_bytes BETWEEN 1 AND 16777216),
    FOREIGN KEY(account_id,policy_hash) REFERENCES life_financial.policies
);
CREATE TABLE life_financial.quote_births (
    account_id uuid NOT NULL REFERENCES life_financial.accounts,
    ticket_id life_financial.identity NOT NULL,
    intent_id life_financial.identity NOT NULL,
    body jsonb NOT NULL CHECK(jsonb_typeof(body)='object'),
    body_hash life_financial.digest NOT NULL,
    PRIMARY KEY(account_id,ticket_id), UNIQUE(account_id,intent_id)
);
CREATE TABLE life_financial.quote_birth_consumptions (
    account_id uuid NOT NULL,
    ticket_id life_financial.identity NOT NULL,
    operation_id life_financial.identity NOT NULL,
    dispatch jsonb NOT NULL CHECK(jsonb_typeof(dispatch)='object'),
    PRIMARY KEY(account_id,ticket_id), UNIQUE(account_id,operation_id),
    FOREIGN KEY(account_id,ticket_id) REFERENCES life_financial.quote_births,
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED
);
DO $$ DECLARE t text;BEGIN
    FOREACH t IN ARRAY ARRAY['quote_birth_policies','quote_births','quote_birth_consumptions'] LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY scoped_read ON life_financial.%I FOR SELECT USING(life_financial.can_access(account_id))',t);
        EXECUTE format('CREATE TRIGGER retained_identity BEFORE DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',t);
        EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',t);
    END LOOP;
END; $$;

CREATE FUNCTION life_financial.quote_birth_template_valid(t jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE cfg jsonb;k text;n numeric;BEGIN
    IF jsonb_typeof(t) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(t))<>9
        OR NOT t ?& ARRAY['intent_id','controller_id','session_id','epoch','config_version','market','side','level','payload_json']
        OR EXISTS(SELECT 1 FROM jsonb_each(t) WHERE value='null'::jsonb)
        OR EXISTS(SELECT 1 FROM jsonb_each(t) WHERE key IN ('intent_id','controller_id','session_id','market','side','payload_json') AND jsonb_typeof(value)<>'string')
        OR EXISTS(SELECT 1 FROM jsonb_each(t) WHERE key IN ('epoch','config_version','level') AND (jsonb_typeof(value)<>'number' OR value::text !~ '^(0|[1-9][0-9]*)$'))
        OR t->>'market'<>'LIFE-USDT' OR t->>'side' NOT IN ('BUY','SELL')
        OR (t->>'epoch')::bigint<=0 OR (t->>'config_version')::bigint<=0 OR (t->>'level')::bigint>2147483647
        OR octet_length(t->>'payload_json') NOT BETWEEN 1 AND 65536 THEN RETURN false;END IF;
    PERFORM (t->>'intent_id')::life_financial.identity;PERFORM (t->>'controller_id')::life_financial.identity;
    PERFORM (t->>'session_id')::uuid;
    cfg:=(t->>'payload_json')::jsonb;
    IF jsonb_typeof(cfg) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(cfg))<>17
        OR NOT cfg ?& ARRAY['id','type','timestamp','controller_id','trading_pair','connector_name','side','amount','position_action',
            'price','chaser_config','execution_strategy','leverage','level_id','slippage_pct','slippage_multiplier','max_slippage_pct']
        OR cfg->'timestamp' IS DISTINCT FROM '0'::jsonb OR cfg->>'id' IS DISTINCT FROM t->>'intent_id'
        OR cfg->>'controller_id' IS DISTINCT FROM t->>'controller_id' OR cfg->>'connector_name' IS DISTINCT FROM 'okx'
        OR cfg->>'trading_pair' IS DISTINCT FROM 'LIFE-USDT' OR cfg->>'type' IS DISTINCT FROM 'order_executor'
        OR cfg->>'execution_strategy' IS DISTINCT FROM 'LIMIT_MAKER' OR cfg->>'position_action' IS DISTINCT FROM 'OPEN'
        OR cfg->'leverage' IS DISTINCT FROM '1'::jsonb OR cfg->'chaser_config' IS DISTINCT FROM 'null'::jsonb
        OR cfg->>'side' IS DISTINCT FROM (CASE t->>'side' WHEN 'BUY' THEN '1' ELSE '2' END)
        OR cfg->>'level_id' IS DISTINCT FROM t->>'level' THEN RETURN false;END IF;
    FOREACH k IN ARRAY ARRAY['amount','price','slippage_pct','slippage_multiplier','max_slippage_pct'] LOOP
        n:=(cfg->>k)::numeric;
        IF n IS NULL OR n::text IN ('NaN','Infinity','-Infinity') OR n<=0 THEN RETURN false;END IF;
    END LOOP;
    RETURN (cfg->>'slippage_multiplier')::numeric>1 AND (cfg->>'slippage_pct')::numeric<=(cfg->>'max_slippage_pct')::numeric;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.quote_birth_dispatch_valid(d jsonb,b jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE observed timestamptz;v text;BEGIN
    IF jsonb_typeof(d) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(d))<>5
        OR NOT d ?& ARRAY['binding','observed_at','controller_id','executor_ids','scope_complete']
        OR d->'binding' IS DISTINCT FROM b->'binding' OR d->'scope_complete' IS DISTINCT FROM 'true'::jsonb
        OR d->>'controller_id' IS DISTINCT FROM b#>>'{action,controller_id}'
        OR jsonb_typeof(d->'observed_at') IS DISTINCT FROM 'string'
        OR jsonb_typeof(d->'executor_ids') IS DISTINCT FROM 'array'
        OR jsonb_array_length(d->'executor_ids') NOT BETWEEN 1 AND 4096
        OR EXISTS(SELECT 1 FROM jsonb_array_elements(d->'executor_ids') WHERE jsonb_typeof(value)<>'string')
        OR (SELECT count(*)<>count(DISTINCT value) FROM jsonb_array_elements(d->'executor_ids'))
        OR NOT d->'executor_ids' @> jsonb_build_array(b->>'intent_id') THEN RETURN false;END IF;
    FOR v IN SELECT jsonb_array_elements_text(d->'executor_ids') LOOP PERFORM v::life_financial.identity;END LOOP;
    observed:=(d->>'observed_at')::timestamptz;
    RETURN isfinite(observed) AND observed>=to_timestamp((b->>'born_at_ms')::numeric/1000)
        AND observed<(b#>>'{owner,expires_at}')::timestamptz;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.quote_birth_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE p life_financial.quote_birth_policies;a life_financial.accounts;r record;x record;
    b jsonb;original jsonb;actual jsonb;binding jsonb;account_binding jsonb;observed timestamptz;
    ticket_count bigint;total_bytes bigint;
BEGIN
    SELECT * INTO p FROM life_financial.quote_birth_policies WHERE account_id=p_account;
    IF NOT FOUND THEN RETURN NOT EXISTS(SELECT 1 FROM life_financial.quote_births WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.quote_birth_consumptions WHERE account_id=p_account);END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account;
    SELECT count(*),COALESCE(sum(octet_length(body::text)),0) INTO ticket_count,total_bytes
        FROM life_financial.quote_births WHERE account_id=p_account;
    IF p.policy_hash IS DISTINCT FROM a.policy_hash OR ticket_count>p.max_tickets OR total_bytes>p.max_total_bytes THEN RETURN false;END IF;
    account_binding:=jsonb_build_object('environment',a.environment,'exchange',a.exchange,'account_uid',a.account_uid,
        'risk_pool',a.risk_pool,'lineage',a.lineage,'schema_version',a.schema_version,'policy_hash',a.policy_hash);
    -- Hash each referenced configuration once in this invocation. LEFT JOINs
    -- retain broken references so they are refused, never silently filtered.
    -- No result survives this STABLE call or substitutes for a later audit.
    FOR r IN
        WITH models AS MATERIALIZED (
            SELECT c.account_id,c.config_version,c.config_hash,c.policy_hash,c.body->'spot_quote_producer' AS model,
                encode(sha256(convert_to(life_financial.canonical_json_v1(c.body->'spot_quote_producer'),'UTF8')),'hex') AS model_hash
            FROM life_financial.configurations c WHERE c.account_id=p_account AND EXISTS(
                SELECT 1 FROM life_financial.quote_births t WHERE t.account_id=p_account
                    AND t.body#>>'{binding,config_hash}'=c.config_hash AND t.body#>>'{binding,policy_hash}'=c.policy_hash
                    AND t.body#>>'{action,config_version}'=c.config_version::text))
        SELECT t.*,s.account_id AS session_account,s.config_version AS session_config,s.epoch AS session_epoch,
            s.started_at AS session_started,s.expires_at AS session_expires,
            m.account_id AS model_account,m.model,m.model_hash
        FROM life_financial.quote_births t
        LEFT JOIN life_financial.sessions s ON s.account_id=t.account_id AND s.session_id=(t.body#>>'{action,session_id}')::uuid
        LEFT JOIN models m ON m.config_version=s.config_version
            AND m.config_hash=t.body#>>'{binding,config_hash}' AND m.policy_hash=t.body#>>'{binding,policy_hash}'
        WHERE t.account_id=p_account LOOP
        b:=r.body; original:=b->'template';actual:=b->'action';
        IF NOT life_financial.quote_birth_template_valid(original)
            OR (SELECT count(*) FROM jsonb_object_keys(b))<>9 OR NOT b ?& ARRAY[
            'ticket_id','intent_id','version','model_hash','template','action','owner','binding','born_at_ms']
            OR r.ticket_id IS DISTINCT FROM b->>'ticket_id' OR r.intent_id IS DISTINCT FROM b->>'intent_id'
            OR original->>'intent_id' IS DISTINCT FROM r.intent_id OR actual->>'intent_id' IS DISTINCT FROM r.intent_id
            OR r.body_hash IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(b),'UTF8')),'hex')
            OR original-'payload_json' IS DISTINCT FROM actual-'payload_json'
            OR (original->>'payload_json')::jsonb->'timestamp' IS DISTINCT FROM '0'::jsonb
            OR ((actual->>'payload_json')::jsonb-'timestamp') IS DISTINCT FROM ((original->>'payload_json')::jsonb-'timestamp')
            OR ((actual->>'payload_json')::jsonb->>'timestamp')::numeric*1000 IS DISTINCT FROM (b->>'born_at_ms')::numeric
            OR (b->>'born_at_ms')::bigint NOT BETWEEN 1 AND 253402300799000
            OR (b->>'version')::bigint<0 OR (b->>'version')::bigint>a.version
            OR (b#>>'{owner,session_id}') IS DISTINCT FROM actual->>'session_id'
            OR b#>'{owner,epoch}' IS DISTINCT FROM actual->'epoch' THEN RETURN false;END IF;
        IF r.session_account IS NULL OR r.session_config<>(actual->>'config_version')::bigint
            OR r.session_epoch<>(actual->>'epoch')::bigint THEN RETURN false;END IF;
        binding:=account_binding||jsonb_build_object('config_hash',b#>'{binding,config_hash}');
        IF b->'binding' IS DISTINCT FROM binding OR jsonb_typeof(b->'owner') IS DISTINCT FROM 'object'
            OR (SELECT count(*) FROM jsonb_object_keys(b->'owner'))<>6
            OR EXISTS(SELECT 1 FROM jsonb_each(b->'owner') WHERE value='null'::jsonb)
            OR (b#>>'{owner,process_id}')::uuid IS NULL OR (b#>>'{owner,generation}')::uuid IS NULL
            OR NOT isfinite((b#>>'{owner,valid_from}')::timestamptz) OR NOT isfinite((b#>>'{owner,expires_at}')::timestamptz)
            OR (b#>>'{owner,valid_from}')::timestamptz<r.session_started
            OR (b#>>'{owner,expires_at}')::timestamptz>r.session_expires
            OR to_timestamp((b->>'born_at_ms')::numeric/1000)<(b#>>'{owner,valid_from}')::timestamptz-interval '1 millisecond'
            OR to_timestamp((b->>'born_at_ms')::numeric/1000)>=(b#>>'{owner,expires_at}')::timestamptz THEN RETURN false;END IF;
        IF r.model_account IS NULL OR r.model IS NULL OR jsonb_typeof(r.model)<>'object'
            OR b->>'model_hash' IS DISTINCT FROM r.model_hash THEN RETURN false;END IF;
    END LOOP;
    FOR x IN
        SELECT c.*,t.body,t.body_hash,t.account_id AS birth_account,
            o.account_id AS operation_account,o.command,o.fingerprint,o.canonical_fingerprint,o.committed_version,o.recorded_at,
            e.action_id AS origin_action,e.after_state,e.descriptor
        FROM life_financial.quote_birth_consumptions c
        LEFT JOIN life_financial.quote_births t ON t.account_id=c.account_id AND t.ticket_id=c.ticket_id
        LEFT JOIN life_financial.operations o ON o.account_id=c.account_id AND o.operation_id=c.operation_id
        LEFT JOIN life_financial.spot_action_events e ON e.account_id=c.account_id AND e.action_id=t.intent_id
            AND e.action_version=0 AND e.operation_id=c.operation_id
        WHERE c.account_id=p_account LOOP
        b:=x.body;
        observed:=(x.dispatch->>'observed_at')::timestamptz;
        IF x.birth_account IS NULL OR x.operation_account IS NULL OR x.origin_action IS NULL
            OR NOT life_financial.quote_birth_dispatch_valid(x.dispatch,b)
            OR x.command->>'kind' IS DISTINCT FROM 'SPOT_PREPARE' OR x.command->'action' IS DISTINCT FROM b->'action'
            OR x.command->'binding' IS DISTINCT FROM b->'binding' OR x.command->'owner' IS DISTINCT FROM b->'owner'
            OR x.command->'compound' IS DISTINCT FROM jsonb_build_object('ticket_id',x.ticket_id,'birth_hash',x.body_hash,'dispatch',x.dispatch)
            OR x.fingerprint IS DISTINCT FROM x.canonical_fingerprint
            OR (x.command->>'version')::bigint<>x.committed_version-1
            OR x.dispatch->'binding' IS DISTINCT FROM b->'binding' OR x.dispatch->'scope_complete' IS DISTINCT FROM 'true'::jsonb
            OR x.dispatch->>'controller_id' IS DISTINCT FROM b#>>'{action,controller_id}'
            OR NOT x.dispatch->'executor_ids' @> jsonb_build_array(b->>'intent_id')
            OR observed<to_timestamp((b->>'born_at_ms')::numeric/1000)
            OR observed>x.recorded_at OR (x.command#>>'{admission,observed_at}')::timestamptz<observed
            OR x.after_state->>'state' IS DISTINCT FROM 'PREPARED' OR x.descriptor IS DISTINCT FROM b->'action' THEN RETURN false;END IF;
    END LOOP;
    RETURN NOT EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=p_account AND o.command ? 'compound'
        AND NOT EXISTS(SELECT 1 FROM life_financial.quote_birth_consumptions x WHERE x.account_id=p_account AND x.operation_id=o.operation_id));
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.quote_birth_issue(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,p_command jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts;s life_financial.sessions;p life_financial.quote_birth_policies;
    policy life_financial.spot_action_policies;t jsonb;cfg jsonb;model jsonb;body jsonb;actual text;born bigint;stamp text;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR jsonb_typeof(p_command) IS DISTINCT FROM 'object'
        OR octet_length(p_command::text)>131072 OR (SELECT count(*) FROM jsonb_object_keys(p_command))<>3
        OR NOT p_command ?& ARRAY['ticket_id','template','model_hash'] THEN RAISE EXCEPTION 'LIFE_BIRTH_REQUEST_INVALID';END IF;
    PERFORM life_financial.guard_account(p_account,p_binding,p_owner,p_version);
    PERFORM life_financial.guard_spot_session(p_account);
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account;
    SELECT * INTO STRICT s FROM life_financial.sessions WHERE account_id=p_account AND session_id=a.current_session;
    SELECT * INTO p FROM life_financial.quote_birth_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
    SELECT * INTO policy FROM life_financial.spot_action_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
    IF p.account_id IS NULL OR policy.account_id IS NULL THEN RAISE EXCEPTION 'LIFE_BIRTH_POLICY_REQUIRED';END IF;
    t:=p_command->'template';cfg:=(t->>'payload_json')::jsonb;
    IF NOT life_financial.quote_birth_template_valid(t)
        OR jsonb_typeof(t) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(t))<>9
        OR NOT t ?& ARRAY['intent_id','controller_id','session_id','epoch','config_version','market','side','level','payload_json']
        OR EXISTS(SELECT 1 FROM jsonb_each(t) WHERE value='null'::jsonb)
        OR EXISTS(SELECT 1 FROM jsonb_each(t) WHERE key IN ('intent_id','controller_id','session_id','market','side','payload_json') AND jsonb_typeof(value)<>'string')
        OR EXISTS(SELECT 1 FROM jsonb_each(t) WHERE key IN ('epoch','config_version','level') AND (jsonb_typeof(value)<>'number' OR value::text !~ '^(0|[1-9][0-9]*)$'))
        OR t->>'controller_id' IS DISTINCT FROM policy.controller_id OR t->>'market' IS DISTINCT FROM 'LIFE-USDT'
        OR t->>'side' NOT IN ('BUY','SELL') OR (t->>'level')::bigint NOT BETWEEN 0 AND 2147483647
        OR t->>'session_id' IS DISTINCT FROM a.current_session::text OR (t->>'epoch')::bigint<>a.owner_epoch
        OR (t->>'config_version')::bigint<>s.config_version OR cfg->'timestamp' IS DISTINCT FROM '0'::jsonb
        OR cfg->>'id' IS DISTINCT FROM t->>'intent_id' OR cfg->>'controller_id' IS DISTINCT FROM t->>'controller_id'
        OR cfg->>'connector_name' IS DISTINCT FROM 'okx' OR cfg->>'trading_pair' IS DISTINCT FROM 'LIFE-USDT'
        OR cfg->>'type' IS DISTINCT FROM 'order_executor' OR cfg->>'execution_strategy' IS DISTINCT FROM 'LIMIT_MAKER'
        OR cfg->>'position_action' IS DISTINCT FROM 'OPEN' OR cfg->'leverage' IS DISTINCT FROM '1'::jsonb
        OR cfg->'chaser_config' IS DISTINCT FROM 'null'::jsonb
        OR (cfg->>'amount')::numeric<=0 OR (cfg->>'price')::numeric<=0
        OR cfg->>'side' IS DISTINCT FROM (CASE t->>'side' WHEN 'BUY' THEN '1' ELSE '2' END)
        OR cfg->>'level_id' IS DISTINCT FROM t->>'level'
        OR octet_length(t->>'payload_json') NOT BETWEEN 1 AND policy.max_payload_bytes
        OR length(p_command->>'ticket_id') NOT BETWEEN 1 AND 96
        OR (length(t->>'payload_json')-length(replace(t->>'payload_json','"timestamp":0.0,','')))/length('"timestamp":0.0,')<>1
        THEN RAISE EXCEPTION 'LIFE_BIRTH_TEMPLATE_INVALID';END IF;
    PERFORM (t->>'intent_id')::life_financial.identity;PERFORM (p_command->>'ticket_id')::life_financial.identity;
    IF EXISTS(SELECT 1 FROM life_financial.quote_births WHERE account_id=p_account AND (intent_id=t->>'intent_id' OR ticket_id=p_command->>'ticket_id'))
        OR EXISTS(SELECT 1 FROM life_financial.quote_actions WHERE account_id=p_account AND action_id=t->>'intent_id')
        OR EXISTS(SELECT 1 FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=t->>'intent_id') THEN RAISE EXCEPTION 'LIFE_BIRTH_IDENTITY_USED';END IF;
    IF EXISTS(SELECT 1 FROM life_financial.quote_births b WHERE b.account_id=p_account AND NOT EXISTS(
        SELECT 1 FROM life_financial.quote_birth_consumptions x WHERE x.account_id=b.account_id AND x.ticket_id=b.ticket_id)) THEN
        RAISE EXCEPTION 'LIFE_BIRTH_PENDING_RECOVERY';END IF;
    IF EXISTS(SELECT 1 FROM life_financial.shared_capital_policies WHERE account_id=p_account) AND NOT EXISTS(
        SELECT 1 FROM life_financial.shared_capital_claims c JOIN life_financial.operations o USING(account_id,operation_id)
        WHERE c.account_id=p_account AND c.intent_id=t->>'intent_id' AND c.product='SPOT'
            AND c.side=t->>'side' AND c.quantity_base=(cfg->>'amount')::numeric AND c.price_usdt=(cfg->>'price')::numeric
            AND c.session_id=a.current_session AND c.epoch=a.owner_epoch AND o.command->'owner'=p_owner
            AND life_financial.shared_claim_active_at(p_account,c.intent_id,p_version)) THEN
        RAISE EXCEPTION 'LIFE_BIRTH_PREALLOCATION_REQUIRED';END IF;
    SELECT c.body->'spot_quote_producer' INTO model FROM life_financial.configurations c WHERE c.account_id=p_account
        AND c.config_version=s.config_version AND c.config_hash=a.config_hash AND c.policy_hash=a.policy_hash;
    IF model IS NULL OR jsonb_typeof(model)<>'object' OR p_command->>'model_hash' IS DISTINCT FROM
        encode(sha256(convert_to(life_financial.canonical_json_v1(model),'UTF8')),'hex') THEN RAISE EXCEPTION 'LIFE_BIRTH_MODEL_CHANGED';END IF;
    IF NOT life_financial.quote_birth_verified(p_account) THEN RAISE EXCEPTION 'LIFE_BIRTH_UNVERIFIED';END IF;
    IF (SELECT count(*) FROM life_financial.quote_births WHERE account_id=p_account)>=p.max_tickets THEN RAISE EXCEPTION 'LIFE_BIRTH_CAPACITY';END IF;
    -- This is the ticket's only birth; source observations must come later.
    born:=floor(extract(epoch FROM clock_timestamp())*1000)::bigint;
    stamp:=trim_scale(born::numeric/1000)::text;IF strpos(stamp,'.')=0 THEN stamp:=stamp||'.0';END IF;
    actual:=replace(t->>'payload_json','"timestamp":0.0,','"timestamp":'||stamp||',');
    body:=jsonb_build_object('ticket_id',p_command->>'ticket_id','intent_id',t->>'intent_id','version',p_version,
        'model_hash',p_command->>'model_hash','template',t,'action',jsonb_set(t,'{payload_json}',to_jsonb(actual)),
        'owner',p_owner,'binding',p_binding,'born_at_ms',born);
    IF (SELECT COALESCE(sum(octet_length(b.body::text)),0) FROM life_financial.quote_births b WHERE account_id=p_account)
        +octet_length(body::text)>p.max_total_bytes THEN RAISE EXCEPTION 'LIFE_BIRTH_CAPACITY';END IF;
    INSERT INTO life_financial.quote_births VALUES(p_account,p_command->>'ticket_id',t->>'intent_id',body,
        encode(sha256(convert_to(life_financial.canonical_json_v1(body),'UTF8')),'hex'));
    RETURN jsonb_build_object('body',body,'version',p_version,'replayed',false,'trading_permission',false);
END; $$;

CREATE FUNCTION life_financial.quote_birth_read(p_account uuid,p_ticket text) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID';END IF;
    IF NOT life_financial.quote_birth_verified(p_account) THEN RAISE EXCEPTION 'LIFE_BIRTH_UNVERIFIED';END IF;
    RETURN (SELECT body FROM life_financial.quote_births WHERE account_id=p_account AND ticket_id=p_ticket);
END; $$;

CREATE FUNCTION life_financial.quote_birth_prepare(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,p_command jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r life_financial.quote_births;action jsonb;prep jsonb;facts jsonb;dispatch jsonb;context jsonb;result jsonb;
    policy life_financial.spot_action_policies;observed timestamptz;at timestamptz;q life_financial.quote_actions;v text;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR jsonb_typeof(p_command) IS DISTINCT FROM 'object'
        OR octet_length(p_command::text)>1048576 OR (SELECT count(*) FROM jsonb_object_keys(p_command))<>5
        OR NOT p_command ?& ARRAY['ticket_id','action','prepare','admission','dispatch'] THEN RAISE EXCEPTION 'LIFE_BIRTH_PREPARATION_INVALID';END IF;
    IF p_fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(jsonb_build_object(
        'kind','QUOTE_BIRTH_PREPARE','request',p_command,'binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut)),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT';END IF;
    PERFORM life_financial.guard_account(p_account,p_binding,p_owner,p_version);
    PERFORM life_financial.guard_spot_session(p_account);
    IF NOT life_financial.quote_birth_verified(p_account) THEN RAISE EXCEPTION 'LIFE_BIRTH_UNVERIFIED';END IF;
    SELECT * INTO r FROM life_financial.quote_births WHERE account_id=p_account AND ticket_id=p_command->>'ticket_id';
    IF NOT FOUND OR r.body->'owner' IS DISTINCT FROM p_owner OR r.body->'binding' IS DISTINCT FROM p_binding
        OR r.body->'action' IS DISTINCT FROM p_command->'action' THEN RAISE EXCEPTION 'LIFE_BIRTH_ORIGIN_REQUIRED';END IF;
    IF EXISTS(SELECT 1 FROM life_financial.quote_birth_consumptions WHERE account_id=p_account AND ticket_id=r.ticket_id)
        OR EXISTS(SELECT 1 FROM life_financial.quote_actions WHERE account_id=p_account AND action_id=r.intent_id)
        OR EXISTS(SELECT 1 FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=r.intent_id) THEN RAISE EXCEPTION 'LIFE_BIRTH_IDENTITY_USED';END IF;
    action:=p_command->'action';prep:=p_command->'prepare';facts:=p_command->'admission';dispatch:=p_command->'dispatch';
    SELECT * INTO STRICT policy FROM life_financial.spot_action_policies WHERE account_id=p_account;
    IF NOT life_financial.quote_birth_dispatch_valid(dispatch,r.body)
        OR jsonb_typeof(dispatch) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(dispatch))<>5
        OR NOT dispatch ?& ARRAY['binding','observed_at','controller_id','executor_ids','scope_complete']
        OR dispatch->'binding' IS DISTINCT FROM p_binding OR dispatch->'scope_complete' IS DISTINCT FROM 'true'::jsonb
        OR dispatch->>'controller_id' IS DISTINCT FROM policy.controller_id
        OR jsonb_typeof(dispatch->'executor_ids') IS DISTINCT FROM 'array'
        OR jsonb_array_length(dispatch->'executor_ids') NOT BETWEEN 1 AND 4096
        OR EXISTS(SELECT 1 FROM jsonb_array_elements(dispatch->'executor_ids') WHERE jsonb_typeof(value)<>'string')
        OR (SELECT count(*)<>count(DISTINCT value) FROM jsonb_array_elements(dispatch->'executor_ids'))
        OR NOT dispatch->'executor_ids' @> jsonb_build_array(r.intent_id) THEN RAISE EXCEPTION 'LIFE_BIRTH_RUNNER_SCOPE_INVALID';END IF;
    FOR v IN SELECT jsonb_array_elements_text(dispatch->'executor_ids') LOOP PERFORM v::life_financial.identity;END LOOP;
    observed:=(dispatch->>'observed_at')::timestamptz;at:=clock_timestamp();
    IF NOT isfinite(observed) OR observed>at OR observed<to_timestamp((r.body->>'born_at_ms')::numeric/1000)
        OR extract(epoch FROM at-observed)*1000>policy.max_fact_age_ms
        OR (facts->>'observed_at')::timestamptz<observed THEN RAISE EXCEPTION 'LIFE_BIRTH_RUNNER_FACTS_STALE';END IF;
    FOREACH v IN ARRAY ARRAY['intent_id','session_id','epoch','config_version','market','side'] LOOP
        IF action->v IS DISTINCT FROM prep->v THEN RAISE EXCEPTION 'LIFE_ACTION_PREPARATION_SCOPE_INVALID';END IF;
    END LOOP;
    IF action->'level' IS DISTINCT FROM prep->'quote_level' THEN RAISE EXCEPTION 'LIFE_ACTION_PREPARATION_SCOPE_INVALID';END IF;
    PERFORM life_financial.guard_quote_admission(p_account,p_binding,action,prep,facts);
    context:=jsonb_build_object('action',action,'admission',facts,'compound',jsonb_build_object(
        'ticket_id',r.ticket_id,'birth_hash',r.body_hash,'dispatch',dispatch));
    -- The existing financial core performs the fresh full-state/cut/economic
    -- checks, one version increment and all monetary writes. No provisional
    -- action is visible to that pre-write audit, and no old verdict crosses it.
    result:=life_financial.spot_prepare_action_core(p_account,p_binding,p_owner,p_version,p_cut,
        encode(sha256(convert_to(life_financial.canonical_json_v1(jsonb_build_object(
            'kind','SPOT_PREPARE','binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',prep)||context),'UTF8')),'hex'),prep,context);
    INSERT INTO life_financial.quote_actions VALUES(p_account,r.intent_id,(action->>'session_id')::uuid,action->>'side',(action->>'level')::integer,'PREPARED',0) RETURNING * INTO q;
    INSERT INTO life_financial.spot_action_descriptors VALUES(p_account,r.intent_id,action);
    INSERT INTO life_financial.spot_action_events VALUES(p_account,r.intent_id,0,prep->>'operation_id',prep,action,to_jsonb(q));
    INSERT INTO life_financial.quote_birth_consumptions VALUES(p_account,r.ticket_id,prep->>'operation_id',dispatch);
    RETURN result;
END; $$;

CREATE FUNCTION life_financial.quote_birth_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
    IF TG_TABLE_NAME='quote_births' THEN
        PERFORM life_financial.guard_account(NEW.account_id,NEW.body->'binding',NEW.body->'owner',(NEW.body->>'version')::bigint);
    END IF;
    IF NOT life_financial.quote_birth_verified(NEW.account_id) THEN RAISE EXCEPTION 'LIFE_BIRTH_UNVERIFIED';END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER birth_at_commit AFTER INSERT ON life_financial.quote_births
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.quote_birth_commit_guard();
CREATE CONSTRAINT TRIGGER birth_consumption_at_commit AFTER INSERT ON life_financial.quote_birth_consumptions
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.quote_birth_commit_guard();

-- An atomic PREPARE's version-zero event replaces the separate CLAIM/DISPATCH
-- origins. Keep the old transition checks and require its retained consumption.
DO $$ DECLARE definition text;needle text;BEGIN
    definition:=pg_get_functiondef('life_financial.spot_action_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)'::regprocedure);
    needle:='AND o.command->>''kind''=''SPOT_ACTION_CLAIM'';';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_QUOTE_BIRTH_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,$r$AND (o.command->>'kind'='SPOT_ACTION_CLAIM' OR o.command->>'kind'='SPOT_PREPARE'
        AND EXISTS(SELECT 1 FROM life_financial.quote_birth_consumptions birth_origin WHERE birth_origin.account_id=p_account AND birth_origin.operation_id=o.operation_id));$r$);
END; $$;

-- A new wrapper audits metadata on every snapshot, including same-version
-- corruption. Pending birth blocks new generic publication, but grants no money.
ALTER FUNCTION life_financial.spot_body(uuid) RENAME TO spot_body_before_quote_birth;
CREATE FUNCTION life_financial.spot_body(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b jsonb;verified boolean;pending boolean;BEGIN
    b:=life_financial.spot_body_before_quote_birth(p_account);
    verified:=life_financial.quote_birth_verified(p_account);
    IF verified IS TRUE AND NOT EXISTS(SELECT 1 FROM life_financial.quote_birth_policies WHERE account_id=p_account) THEN RETURN b;END IF;
    pending:=EXISTS(SELECT 1 FROM life_financial.quote_births t WHERE t.account_id=p_account AND NOT EXISTS(
        SELECT 1 FROM life_financial.quote_birth_consumptions x WHERE x.account_id=t.account_id AND x.ticket_id=t.ticket_id));
    RETURN b||jsonb_build_object('quote_births',jsonb_build_object('verified',verified,'pending',pending,
        'hashes',COALESCE((SELECT jsonb_agg(body_hash ORDER BY ticket_id) FROM life_financial.quote_births WHERE account_id=p_account),'[]'::jsonb)),
        'unverified',(b->>'unverified')::boolean OR NOT verified,
        'financial_recovery_required',(b->>'financial_recovery_required')::boolean OR NOT verified,
        'recovery_required',(b->>'recovery_required')::boolean OR NOT verified OR pending);
END; $$;

DO $$ DECLARE r record;sig text;BEGIN
    FOR r IN SELECT DISTINCT x.grantee,t.oid::regclass::text AS name FROM pg_class t
        CROSS JOIN LATERAL aclexplode(COALESCE(t.relacl,acldefault('r',t.relowner))) x
        WHERE t.oid IN ('life_financial.quote_birth_policies'::regclass,'life_financial.quote_births'::regclass,'life_financial.quote_birth_consumptions'::regclass)
        AND x.grantee<>t.relowner LOOP
        EXECUTE format('REVOKE ALL ON TABLE %s FROM %s',r.name,CASE WHEN r.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(r.grantee)) END);
    END LOOP;
    FOR r IN SELECT DISTINCT x.grantee,p.oid::regprocedure::text AS signature,p.proowner FROM pg_proc p
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE p.pronamespace='life_financial'::regnamespace AND (p.proname LIKE 'quote_birth_%' OR p.proname IN ('spot_body','spot_body_before_quote_birth'))
        AND x.grantee<>p.proowner LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %s',r.signature,CASE WHEN r.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(r.grantee)) END);
    END LOOP;
    -- Upgrade only previously explicit writers/readers; helpers remain private.
    FOR r IN SELECT DISTINCT x.grantee FROM pg_proc p
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE p.oid='life_financial.spot_capture_quote_action(uuid,jsonb,jsonb,bigint,life_financial.digest,jsonb)'::regprocedure
        AND x.grantee NOT IN (0,p.proowner) AND x.privilege_type='EXECUTE' LOOP
        EXECUTE format('GRANT EXECUTE ON FUNCTION life_financial.quote_birth_issue(uuid,jsonb,jsonb,bigint,jsonb),life_financial.quote_birth_prepare(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb) TO %I',pg_get_userbyid(r.grantee));
    END LOOP;
    FOR r IN SELECT DISTINCT x.grantee FROM pg_proc p
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE p.oid='life_financial.spot_state(uuid)'::regprocedure AND x.grantee NOT IN (0,p.proowner) AND x.privilege_type='EXECUTE' LOOP
        EXECUTE format('GRANT EXECUTE ON FUNCTION life_financial.quote_birth_read(uuid,text) TO %I',pg_get_userbyid(r.grantee));
    END LOOP;
END; $$;
