-- Permanent metadata retirement for a birth that has never been prepared.
-- Serializes against prepare on the account row. No monetary claim, hold,
-- budget, operation, account version or sender is created/released here.
DO $$ DECLARE p record;BEGIN
    FOR p IN SELECT f.*,l.lanname FROM pg_proc f JOIN pg_language l ON l.oid=f.prolang
        WHERE f.oid IN ('life_financial.quote_birth_verified(uuid)'::regprocedure,
            'life_financial.quote_birth_issue(uuid,jsonb,jsonb,bigint,jsonb)'::regprocedure,
            'life_financial.quote_birth_prepare(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'::regprocedure,
            'life_financial.spot_body(uuid)'::regprocedure) LOOP
        IF p.lanname<>'plpgsql' OR NOT p.prosecdef OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR p.proisstrict OR p.proleakproof OR p.proparallel<>'u' OR p.proretset OR p.prokind<>'f' OR p.pronargdefaults<>0
            OR p.provolatile<>(CASE WHEN p.proname IN ('quote_birth_issue','quote_birth_prepare') THEN 'v' ELSE 's' END)
            OR p.prorettype<>(CASE WHEN p.proname='quote_birth_verified' THEN 'boolean'::regtype ELSE 'jsonb'::regtype END)
            OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') IS DISTINCT FROM (CASE p.proname
                WHEN 'quote_birth_verified' THEN '2796273ffa4a8a773ed54cbc23acc980df048f4e5bf2e20216f2cc8d63552aaf'
                WHEN 'quote_birth_issue' THEN '21f84bdb8050ae2e75cf304f1a3e76fc74cef8627133c4a5f07706c324492335'
                WHEN 'quote_birth_prepare' THEN 'fe32089ecce2c96241203d62a015da0d54a11e3e114bcc57e90124c79bd15ccf'
                WHEN 'spot_body' THEN '53ba94f61de421cdf6bb0ccc300f3f4a3712641cfb1ebcbcf734b6ab39b503da' END) THEN
            RAISE EXCEPTION 'LIFE_BIRTH_RETIREMENT_MIGRATION_MISMATCH';END IF;
    END LOOP;
END; $$;
CREATE TABLE life_financial.quote_birth_retirements (
    account_id uuid NOT NULL,ticket_id life_financial.identity NOT NULL,
    retirement_id life_financial.identity NOT NULL,body jsonb NOT NULL CHECK(jsonb_typeof(body)='object'),
    body_hash life_financial.digest NOT NULL,
    PRIMARY KEY(account_id,ticket_id),UNIQUE(account_id,retirement_id),
    FOREIGN KEY(account_id,ticket_id) REFERENCES life_financial.quote_births
);
ALTER TABLE life_financial.quote_birth_retirements ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped_read ON life_financial.quote_birth_retirements FOR SELECT USING(life_financial.can_access(account_id));
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.quote_birth_retirements
    FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record();
DO $$ DECLARE definition text;BEGIN
    definition:=pg_get_functiondef('life_financial.quote_birth_verified(uuid)'::regprocedure);
    EXECUTE replace(definition,'CREATE OR REPLACE FUNCTION life_financial.quote_birth_verified(p_account uuid)',
        'CREATE FUNCTION life_financial.quote_birth_before_retirement_verified(p_account uuid)');
END; $$;
CREATE OR REPLACE FUNCTION life_financial.quote_birth_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r record;q jsonb;cmd jsonb;token jsonb;stamp bigint;version_value bigint;BEGIN
    IF NOT life_financial.quote_birth_before_retirement_verified(p_account) THEN RETURN false;END IF;
    FOR r IN SELECT t.*,b.body AS birth,b.body_hash AS birth_hash,s.started_at,s.expires_at,s.epoch,a.version
        FROM life_financial.quote_birth_retirements t
        LEFT JOIN life_financial.quote_births b USING(account_id,ticket_id)
        LEFT JOIN life_financial.accounts a USING(account_id)
        LEFT JOIN life_financial.sessions s ON s.account_id=t.account_id AND s.session_id=(t.body#>>'{request,owner,session_id}')::uuid
        WHERE t.account_id=p_account LOOP
        q:=r.body->'request';cmd:=q->'command';token:=q->'owner';stamp:=(r.body->>'retired_at_ms')::bigint;
        version_value:=(q->>'version')::bigint;
        IF r.birth IS NULL OR r.started_at IS NULL OR r.version IS NULL OR (SELECT count(*) FROM jsonb_object_keys(r.body))<>4
            OR NOT r.body ?& ARRAY['request','receipt','birth_hash','retired_at_ms']
            OR r.body_hash IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(r.body),'UTF8')),'hex')
            OR r.body->>'birth_hash' IS DISTINCT FROM r.birth_hash
            OR jsonb_typeof(q) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(q))<>6
            OR NOT q ?& ARRAY['kind','binding','owner','version','cut','command']
            OR q->>'kind' IS DISTINCT FROM 'QUOTE_BIRTH_RETIRE' OR q->'binding' IS DISTINCT FROM r.birth->'binding'
            OR jsonb_typeof(q->'cut') IS DISTINCT FROM 'string' OR q->>'cut' !~ '^[0-9a-f]{64}$'
            OR jsonb_typeof(q->'version') IS DISTINCT FROM 'number' OR q->>'version' !~ '^(0|[1-9][0-9]*)$'
            OR version_value NOT BETWEEN (r.birth->>'version')::bigint AND r.version
            OR jsonb_typeof(token) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(token))<>6
            OR NOT token ?& ARRAY['process_id','generation','epoch','session_id','valid_from','expires_at']
            OR EXISTS(SELECT 1 FROM jsonb_each(token) WHERE value='null'::jsonb)
            OR (token->>'process_id')::uuid IS NULL OR (token->>'generation')::uuid IS NULL
            OR (token->>'epoch')::bigint IS DISTINCT FROM r.epoch
            OR NOT isfinite((token->>'valid_from')::timestamptz) OR NOT isfinite((token->>'expires_at')::timestamptz)
            OR (token->>'valid_from')::timestamptz<r.started_at OR (token->>'expires_at')::timestamptz>r.expires_at
            OR (token->>'valid_from')::timestamptz>=(token->>'expires_at')::timestamptz
            OR jsonb_typeof(r.body->'retired_at_ms') IS DISTINCT FROM 'number'
            OR r.body->>'retired_at_ms' !~ '^[1-9][0-9]*$'
            OR stamp NOT BETWEEN (r.birth->>'born_at_ms')::bigint AND 253402300799000
            OR to_timestamp(stamp::numeric/1000)<(token->>'valid_from')::timestamptz-interval '1 millisecond'
            OR cmd IS DISTINCT FROM jsonb_build_object('retirement_id',r.retirement_id,'ticket_id',r.ticket_id,'reason','UNCONSUMED_BIRTH_RETIRED')
            OR r.body->'receipt' IS DISTINCT FROM jsonb_build_object('kind','QUOTE_BIRTH_RETIRE','retirement_id',r.retirement_id,
                'ticket_id',r.ticket_id,'retired',true,'version',version_value,'replayed',false,'trading_permission',false)
            OR EXISTS(SELECT 1 FROM life_financial.quote_birth_consumptions WHERE account_id=p_account AND ticket_id=r.ticket_id)
            OR EXISTS(SELECT 1 FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=r.birth->>'intent_id')
            OR EXISTS(SELECT 1 FROM life_financial.quote_actions WHERE account_id=p_account AND action_id=r.birth->>'intent_id')
            OR EXISTS(SELECT 1 FROM life_financial.reservations WHERE account_id=p_account AND intent_id=r.birth->>'intent_id') THEN RETURN false;END IF;
    END LOOP;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
CREATE FUNCTION life_financial.quote_birth_retire(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,cmd jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b life_financial.quote_births;old life_financial.quote_birth_retirements;request jsonb;receipt jsonb;body jsonb;view jsonb;BEGIN
    IF NOT life_financial.can_access(p_account,true) OR jsonb_typeof(cmd) IS DISTINCT FROM 'object'
        OR (SELECT count(*) FROM jsonb_object_keys(cmd))<>3 OR octet_length(cmd::text)>1024
        OR NOT cmd ?& ARRAY['retirement_id','ticket_id','reason'] OR cmd->>'reason' IS DISTINCT FROM 'UNCONSUMED_BIRTH_RETIRED'
        OR jsonb_typeof(cmd->'retirement_id') IS DISTINCT FROM 'string' OR jsonb_typeof(cmd->'ticket_id') IS DISTINCT FROM 'string'
        OR length(cmd->>'retirement_id') NOT BETWEEN 1 AND 96 OR length(cmd->>'ticket_id') NOT BETWEEN 1 AND 96 THEN
        RAISE EXCEPTION 'LIFE_BIRTH_RETIREMENT_REQUEST_INVALID';END IF;
    PERFORM (cmd->>'retirement_id')::life_financial.identity;PERFORM (cmd->>'ticket_id')::life_financial.identity;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    request:=jsonb_build_object('kind','QUOTE_BIRTH_RETIRE','binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',cmd);
    IF p_cut IS NULL OR p_fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(request),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT';END IF;
    IF NOT life_financial.quote_birth_verified(p_account) THEN RAISE EXCEPTION 'LIFE_BIRTH_UNVERIFIED';END IF;
    SELECT * INTO old FROM life_financial.quote_birth_retirements WHERE account_id=p_account AND retirement_id=cmd->>'retirement_id';
    IF FOUND THEN
        IF old.body->'request' IS DISTINCT FROM request THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT';END IF;
        RETURN old.body->'receipt'||jsonb_build_object('replayed',true);END IF;
    view:=life_financial.spot_state(p_account);
    IF view->>'digest' IS DISTINCT FROM p_cut THEN RAISE EXCEPTION 'LIFE_BIRTH_RETIREMENT_CUT_STALE';END IF;
    IF view->'unverified' IS DISTINCT FROM 'false'::jsonb OR view->'financial_recovery_required' IS DISTINCT FROM 'false'::jsonb THEN
        RAISE EXCEPTION 'LIFE_BIRTH_RETIREMENT_NATIVE_UNVERIFIED';END IF;
    SELECT * INTO b FROM life_financial.quote_births WHERE account_id=p_account AND ticket_id=cmd->>'ticket_id';
    IF NOT FOUND OR b.body->'binding' IS DISTINCT FROM p_binding THEN RAISE EXCEPTION 'LIFE_BIRTH_ORIGIN_REQUIRED';END IF;
    IF EXISTS(SELECT 1 FROM life_financial.quote_birth_consumptions WHERE account_id=p_account AND ticket_id=b.ticket_id)
        OR EXISTS(SELECT 1 FROM life_financial.quote_birth_retirements WHERE account_id=p_account AND ticket_id=b.ticket_id)
        OR EXISTS(SELECT 1 FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=b.intent_id)
        OR EXISTS(SELECT 1 FROM life_financial.quote_actions WHERE account_id=p_account AND action_id=b.intent_id)
        OR EXISTS(SELECT 1 FROM life_financial.reservations WHERE account_id=p_account AND intent_id=b.intent_id) THEN
        RAISE EXCEPTION 'LIFE_BIRTH_RETIREMENT_REQUIRES_UNCONSUMED';END IF;
    receipt:=jsonb_build_object('kind','QUOTE_BIRTH_RETIRE','retirement_id',cmd->>'retirement_id','ticket_id',b.ticket_id,
        'retired',true,'version',p_version,'replayed',false,'trading_permission',false);
    body:=jsonb_build_object('request',request,'receipt',receipt,'birth_hash',b.body_hash,
        'retired_at_ms',floor(extract(epoch FROM clock_timestamp())*1000)::bigint);
    INSERT INTO life_financial.quote_birth_retirements VALUES(p_account,b.ticket_id,cmd->>'retirement_id',body,
        encode(sha256(convert_to(life_financial.canonical_json_v1(body),'UTF8')),'hex'));
    RETURN receipt;
END; $$;
CREATE FUNCTION life_financial.quote_birth_retire_read(p_account uuid,p_retirement text) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID';END IF;
    IF NOT life_financial.quote_birth_verified(p_account) THEN RAISE EXCEPTION 'LIFE_BIRTH_UNVERIFIED';END IF;
    RETURN (SELECT body->'receipt'||jsonb_build_object('replayed',true) FROM life_financial.quote_birth_retirements
        WHERE account_id=p_account AND retirement_id=p_retirement);
END; $$;
CREATE FUNCTION life_financial.quote_birth_retirement_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
    PERFORM life_financial.guard_spot_observation(NEW.account_id,NEW.body#>'{request,binding}',NEW.body#>'{request,owner}',(NEW.body#>>'{request,version}')::bigint);
    IF NOT life_financial.quote_birth_verified(NEW.account_id) THEN RAISE EXCEPTION 'LIFE_BIRTH_UNVERIFIED';END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER retirement_at_commit AFTER INSERT ON life_financial.quote_birth_retirements
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.quote_birth_retirement_commit_guard();
CREATE FUNCTION life_financial.quote_birth_retired_identity_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE retired boolean;BEGIN
    IF TG_TABLE_NAME='quote_birth_consumptions' THEN
        SELECT EXISTS(SELECT 1 FROM life_financial.quote_birth_retirements WHERE account_id=NEW.account_id AND ticket_id=NEW.ticket_id) INTO retired;
    ELSE
        SELECT EXISTS(SELECT 1 FROM life_financial.quote_birth_retirements r JOIN life_financial.quote_births b USING(account_id,ticket_id)
            WHERE r.account_id=NEW.account_id AND b.intent_id=CASE WHEN TG_TABLE_NAME='quote_actions' THEN to_jsonb(NEW)->>'action_id' ELSE to_jsonb(NEW)->>'intent_id' END) INTO retired;
    END IF;
    IF retired THEN RAISE EXCEPTION 'LIFE_BIRTH_IDENTITY_RETIRED';END IF;
    RETURN NEW;
END; $$;
DO $$ DECLARE t text;definition text;needle text;replacement text;BEGIN
    FOREACH t IN ARRAY ARRAY['quote_birth_consumptions','send_attempts','quote_actions'] LOOP
        EXECUTE format('CREATE TRIGGER retired_birth_identity BEFORE INSERT ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.quote_birth_retired_identity_guard()',t);
    END LOOP;
    definition:=pg_get_functiondef('life_financial.quote_birth_issue(uuid,jsonb,jsonb,bigint,jsonb)'::regprocedure);
    needle:='SELECT 1 FROM life_financial.quote_birth_consumptions x WHERE x.account_id=b.account_id AND x.ticket_id=b.ticket_id))';
    replacement:='SELECT 1 FROM life_financial.quote_birth_consumptions x WHERE x.account_id=b.account_id AND x.ticket_id=b.ticket_id) AND NOT EXISTS(SELECT 1 FROM life_financial.quote_birth_retirements r WHERE r.account_id=b.account_id AND r.ticket_id=b.ticket_id))';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_BIRTH_RETIREMENT_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
    definition:=pg_get_functiondef('life_financial.spot_body(uuid)'::regprocedure);
    needle:='SELECT 1 FROM life_financial.quote_birth_consumptions x WHERE x.account_id=t.account_id AND x.ticket_id=t.ticket_id))';
    replacement:='SELECT 1 FROM life_financial.quote_birth_consumptions x WHERE x.account_id=t.account_id AND x.ticket_id=t.ticket_id) AND NOT EXISTS(SELECT 1 FROM life_financial.quote_birth_retirements r WHERE r.account_id=t.account_id AND r.ticket_id=t.ticket_id))';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_BIRTH_RETIREMENT_MIGRATION_MISMATCH';END IF;
    definition:=replace(definition,needle,replacement);
    needle:='''hashes'',COALESCE(';replacement:='''retirement_hashes'',COALESCE((SELECT jsonb_agg(body_hash ORDER BY ticket_id) FROM life_financial.quote_birth_retirements WHERE account_id=p_account),''[]''::jsonb),''hashes'',COALESCE(';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_BIRTH_RETIREMENT_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;
-- Strip hostile defaults before preserving only the matching old public API
-- recipients. No direct DML or private audit/trigger execution is inherited.
DO $$ DECLARE r record;BEGIN
    FOR r IN SELECT DISTINCT x.grantee,p.oid::regprocedure::text AS signature FROM pg_proc p
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE p.pronamespace='life_financial'::regnamespace AND p.proname IN ('quote_birth_before_retirement_verified',
            'quote_birth_retire','quote_birth_retire_read','quote_birth_retirement_commit_guard','quote_birth_retired_identity_guard') AND x.grantee<>p.proowner LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %s',r.signature,CASE WHEN r.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(r.grantee)) END);
    END LOOP;
    FOR r IN SELECT DISTINCT x.grantee FROM pg_class t CROSS JOIN LATERAL aclexplode(COALESCE(t.relacl,acldefault('r',t.relowner))) x
        WHERE t.oid='life_financial.quote_birth_retirements'::regclass AND x.grantee<>t.relowner LOOP
        EXECUTE format('REVOKE ALL ON life_financial.quote_birth_retirements FROM %s',CASE WHEN r.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(r.grantee)) END);
    END LOOP;
    FOR r IN SELECT DISTINCT x.grantee FROM pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE p.oid='life_financial.quote_birth_issue(uuid,jsonb,jsonb,bigint,jsonb)'::regprocedure AND x.grantee<>p.proowner AND x.privilege_type='EXECUTE' AND x.grantee<>0 LOOP
        EXECUTE format('GRANT EXECUTE ON FUNCTION life_financial.quote_birth_retire(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb) TO %I',pg_get_userbyid(r.grantee));
    END LOOP;
    FOR r IN SELECT DISTINCT x.grantee FROM pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE p.oid='life_financial.quote_birth_read(uuid,text)'::regprocedure AND x.grantee<>p.proowner AND x.privilege_type='EXECUTE' AND x.grantee<>0 LOOP
        EXECUTE format('GRANT EXECUTE ON FUNCTION life_financial.quote_birth_retire_read(uuid,text) TO %I',pg_get_userbyid(r.grantee));
    END LOOP;
END; $$;
