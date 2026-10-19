-- Signed native logical control. No leases, credential exclusion or live grant
-- issuer is installed here; the privileged Python importer verifies Ed25519.
CREATE TABLE life_financial.control_policies (
    account_id uuid PRIMARY KEY REFERENCES life_financial.accounts,
    policy_hash life_financial.digest NOT NULL,
    max_fact_age_ms integer NOT NULL CHECK(max_fact_age_ms BETWEEN 1 AND 60000),
    max_records integer NOT NULL CHECK(max_records BETWEEN 1 AND 4096),
    max_total_bytes integer NOT NULL CHECK(max_total_bytes BETWEEN 1 AND 16777216),
    successor_duration_ms bigint NOT NULL CHECK(successor_duration_ms BETWEEN 1 AND 31536000000),
    FOREIGN KEY(account_id,policy_hash) REFERENCES life_financial.policies
);
CREATE TABLE life_financial.control_records (
    account_id uuid NOT NULL REFERENCES life_financial.control_policies,
    operation_id life_financial.identity NOT NULL,body jsonb NOT NULL CHECK(jsonb_typeof(body)='object'),
    source_hash life_financial.digest NOT NULL,applied_at timestamptz NOT NULL CHECK(isfinite(applied_at)),
    sequence bigint GENERATED ALWAYS AS IDENTITY,
    PRIMARY KEY(account_id,operation_id),UNIQUE(account_id,sequence),
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX control_latest ON life_financial.control_records(account_id,sequence DESC);
DO $$ DECLARE t text;BEGIN
    FOREACH t IN ARRAY ARRAY['control_policies','control_records'] LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY scoped_read ON life_financial.%I FOR SELECT USING(life_financial.can_access(account_id))',t);
        EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',t);
    END LOOP;
END; $$;

CREATE FUNCTION life_financial.control_owner(j jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
    SELECT jsonb_build_object('mode',j->'mode','owner_process',j->'owner_process','owner_generation',j->'owner_generation',
        'owner_epoch',j->'owner_epoch','current_session',j->'current_session','owner_since',j->'owner_since','owner_until',j->'owner_until');
$$;

CREATE FUNCTION life_financial.control_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.control_policies;r life_financial.control_records;o life_financial.operations;
    previous_owner jsonb;latest_owner jsonb;before_row jsonb;after_row jsonb;counted bigint;bytes bigint;key_policy jsonb;
BEGIN
    SELECT * INTO p FROM life_financial.control_policies WHERE account_id=p_account;
    IF NOT FOUND THEN RETURN NOT EXISTS(SELECT 1 FROM life_financial.control_records WHERE account_id=p_account);END IF;
    IF p.policy_hash<>(SELECT policy_hash FROM life_financial.accounts WHERE account_id=p_account) THEN RETURN false;END IF;
    SELECT count(*),COALESCE(sum(octet_length(body::text)),0) INTO counted,bytes FROM life_financial.control_records WHERE account_id=p_account;
    IF counted>p.max_records OR bytes>p.max_total_bytes THEN RETURN false;END IF;
    SELECT body->'operator_verifier' INTO key_policy FROM life_financial.configurations
        WHERE account_id=p_account AND config_hash=(SELECT config_hash FROM life_financial.accounts WHERE account_id=p_account);
    FOR r IN SELECT * FROM life_financial.control_records WHERE account_id=p_account ORDER BY sequence LOOP
        before_row:=r.body->'before_account';after_row:=r.body->'after_account';
        SELECT * INTO o FROM life_financial.operations WHERE account_id=p_account AND operation_id=r.operation_id;
        IF NOT FOUND OR (SELECT count(*) FROM jsonb_object_keys(r.body))<>10
            OR NOT r.body ?& ARRAY['payload','approval','signature','cut','facts','verifier_policy','before_account','after_account','receipt','grant_id']
            OR r.source_hash<>encode(sha256(convert_to(r.body::text,'UTF8')),'hex')
            OR r.body->'verifier_policy' IS DISTINCT FROM key_policy
            OR r.body#>>'{payload,operation_id}' IS DISTINCT FROM r.operation_id
            OR o.command IS DISTINCT FROM jsonb_build_object('kind','NATIVE_CONTROL','record_hash',r.source_hash)
            OR o.receipt IS DISTINCT FROM r.body->'receipt'
            OR o.committed_version<>(after_row->>'version')::bigint
            OR (after_row->>'version')::bigint<>(before_row->>'version')::bigint+1
            OR before_row-ARRAY['mode','owner_process','owner_generation','owner_epoch','current_session','owner_since','owner_until','version']
                IS DISTINCT FROM after_row-ARRAY['mode','owner_process','owner_generation','owner_epoch','current_session','owner_since','owner_until','version']
            OR previous_owner IS NOT NULL AND previous_owner<>life_financial.control_owner(before_row) THEN RETURN false;END IF;
        IF (r.body#>>'{cut,checkpoint,revision}')::bigint<>(before_row->>'version')::bigint
            OR r.body#>>'{receipt,mode}' IS DISTINCT FROM r.body#>>'{payload,mode}'
            OR r.body#>'{receipt,token}' IS DISTINCT FROM r.body#>'{payload,token}'
            OR after_row->>'mode' IS DISTINCT FROM (CASE r.body#>>'{payload,mode}' WHEN 'UNARMED' THEN 'RECOVERY_ONLY' ELSE r.body#>>'{payload,mode}' END) THEN RETURN false;END IF;
        IF r.body#>>'{payload,mode}'='ACTIVE' THEN
            IF jsonb_build_object('process_id',after_row->'owner_process','generation',after_row->'owner_generation',
                'epoch',after_row->'owner_epoch','session_id',after_row->'current_session')
                    IS DISTINCT FROM (r.body#>'{payload,token}')-ARRAY['valid_from','expires_at']
                OR (after_row->>'owner_since')::timestamptz IS DISTINCT FROM (r.body#>>'{payload,token,valid_from}')::timestamptz
                OR (after_row->>'owner_until')::timestamptz IS DISTINCT FROM (r.body#>>'{payload,token,expires_at}')::timestamptz THEN RETURN false;END IF;
        ELSIF life_financial.control_owner(before_row)-'mode' IS DISTINCT FROM life_financial.control_owner(after_row)-'mode' THEN RETURN false;
        END IF;
        previous_owner:=life_financial.control_owner(after_row);latest_owner:=previous_owner;
        IF r.body#>>'{payload,mode}'='ACTIVE' AND NOT EXISTS(
            SELECT 1 FROM life_financial.activation_grants g JOIN life_financial.grant_consumptions c USING(account_id,grant_id)
            WHERE g.account_id=p_account AND g.grant_id=(r.body->>'grant_id')::uuid AND c.operation_id=r.operation_id
                AND g.process_id=(after_row->>'owner_process')::uuid AND g.generation=(after_row->>'owner_generation')::uuid
                AND g.epoch=(after_row->>'owner_epoch')::bigint AND g.session_id=(after_row->>'current_session')::uuid
                AND g.issued_at=(after_row->>'owner_since')::timestamptz AND g.expires_at=(after_row->>'owner_until')::timestamptz
                AND g.signature=decode(r.body->>'signature','hex')) THEN RETURN false;END IF;
    END LOOP;
    RETURN latest_owner IS NULL OR latest_owner=(SELECT life_financial.control_owner(to_jsonb(a)) FROM life_financial.accounts a WHERE account_id=p_account);
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.control_facts_qualified(p_account uuid,f jsonb) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v jsonb;ids jsonb;
BEGIN
    IF jsonb_typeof(f) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(f))<>13
        OR NOT f ?& ARRAY['binding','observed_at','account_base_life','account_quote_usdt','liability_base_life','liability_quote_usdt',
            'account_trade_ids','account_cashflow_ids','accepted_wire_ids','runner_wire_ids','order_scope_complete','bill_scope_complete','runner_scope_complete']
        OR EXISTS(SELECT 1 FROM jsonb_each(f) x WHERE x.key IN ('account_base_life','account_quote_usdt','liability_base_life','liability_quote_usdt')
            AND jsonb_typeof(x.value)<>'string') THEN RETURN false;END IF;
    v:=life_financial.spot_state(p_account);
    IF (v->>'unverified')::boolean OR (v->>'financial_recovery_required')::boolean
        OR f->'binding'<>jsonb_build_object('environment',v#>'{account,environment}','exchange',v#>'{account,exchange}',
            'account_uid',v#>'{account,account_uid}','risk_pool',v#>'{account,risk_pool}','lineage',v#>'{account,lineage}',
            'schema_version',v#>'{account,schema_version}','policy_hash',v#>'{account,policy_hash}','config_hash',v#>'{account,config_hash}')
        OR f->'order_scope_complete' IS DISTINCT FROM 'true'::jsonb OR f->'bill_scope_complete' IS DISTINCT FROM 'true'::jsonb
        OR f->'runner_scope_complete' IS DISTINCT FROM 'true'::jsonb
        OR f->'accepted_wire_ids' IS DISTINCT FROM '[]'::jsonb OR f->'runner_wire_ids' IS DISTINCT FROM '[]'::jsonb
        OR (f->>'liability_base_life')::numeric<>0 OR (f->>'liability_quote_usdt')::numeric<>0
        OR (f->>'account_base_life')::numeric<>(v#>>'{balances,LIFE,total}')::numeric
        OR (f->>'account_quote_usdt')::numeric<>(v#>>'{balances,USDT,total}')::numeric
        OR EXISTS(SELECT 1 FROM life_financial.balances WHERE account_id=p_account AND (liability<>0 OR reserved<>0))
        OR EXISTS(SELECT 1 FROM life_financial.send_attempts WHERE account_id=p_account AND state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND'))
        OR EXISTS(SELECT 1 FROM life_financial.reservations WHERE account_id=p_account AND remaining<>0)
        OR EXISTS(SELECT 1 FROM life_financial.budget_buckets WHERE account_id=p_account AND committed<>0)
        OR EXISTS(SELECT 1 FROM life_financial.quote_actions WHERE account_id=p_account AND state NOT IN ('REJECTED','RECONCILED'))
        OR EXISTS(SELECT 1 FROM life_financial.capital_claims WHERE account_id=p_account AND state='HELD')
        OR EXISTS(SELECT 1 FROM life_financial.shared_capital_claims c WHERE account_id=p_account AND NOT EXISTS(
            SELECT 1 FROM life_financial.shared_claim_releases r WHERE r.account_id=c.account_id AND r.intent_id=c.intent_id))
        OR EXISTS(SELECT 1 FROM life_financial.hedge_plans WHERE account_id=p_account AND state<>'TERMINAL') THEN RETURN false;END IF;
    SELECT COALESCE(jsonb_agg(DISTINCT trade_id COLLATE "C" ORDER BY trade_id COLLATE "C"),'[]'::jsonb) INTO ids
        FROM life_financial.fills WHERE account_id=p_account;
    IF f->'account_trade_ids' IS DISTINCT FROM ids THEN RETURN false;END IF;
    SELECT COALESCE(jsonb_agg(bill_id ORDER BY bill_id COLLATE "C"),'[]'::jsonb) INTO ids FROM life_financial.cashflows WHERE account_id=p_account;
    RETURN f->'account_cashflow_ids'=ids;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.control_commit_check(p_account uuid,p_operation text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r life_financial.control_records;p life_financial.control_policies;a life_financial.accounts;at timestamptz;j jsonb;
BEGIN
    SELECT * INTO STRICT r FROM life_financial.control_records WHERE account_id=p_account AND operation_id=p_operation;
    SELECT * INTO STRICT p FROM life_financial.control_policies WHERE account_id=p_account;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account;
    j:=r.body;at:=clock_timestamp();
    IF NOT life_financial.control_verified(p_account) OR j->'after_account'<>to_jsonb(a)
        OR at<r.applied_at OR at<(j#>>'{approval,issued_at}')::timestamptz OR at>=(j#>>'{approval,expires_at}')::timestamptz
        OR at<(j#>>'{facts,observed_at}')::timestamptz
        OR at>(j#>>'{facts,observed_at}')::timestamptz+make_interval(secs=>p.max_fact_age_ms/1000.0)
        OR pg_is_in_recovery() OR current_setting('synchronous_commit')<>'on' OR current_setting('fsync')<>'on'
        OR current_setting('full_page_writes')<>'on' THEN
        RAISE EXCEPTION 'LIFE_CONTROL_EXPIRED_OR_CHANGED_AT_COMMIT' USING ERRCODE='P0001';END IF;
    IF j#>>'{payload,mode}'='ACTIVE' THEN
        IF a.halted OR at<a.owner_since OR at>=a.owner_until OR NOT life_financial.control_facts_qualified(p_account,j->'facts') THEN
            RAISE EXCEPTION 'LIFE_CONTROL_ACTIVATION_UNQUALIFIED_AT_COMMIT' USING ERRCODE='P0001';END IF;
        PERFORM life_financial.guard_spot_session(p_account);
    END IF;
END; $$;

-- Preserve existing OIDs/ACLs and every existing financial guard. Only an exact
-- owner-written signed control record can use the new deferred COMMIT branch.
DO $$ DECLARE item record;p record;definition text;needle text;replacement text;BEGIN
    FOR item IN SELECT * FROM (VALUES
        ('life_financial.commit_owner_guard()', 'e85f17bfa785aedfc88e487540615457b7e52f203779222c33b021733ec477a6'),
        ('life_financial.guard_account(uuid,jsonb,jsonb,bigint)', '78235d5ad0fdc535a192d5905dd7b4924f4213d90b7a46cb242adcabc55fcc9c'),
        ('life_financial.guard_spot_observation(uuid,jsonb,jsonb,bigint)', '16290bf4ea83ad4aca3acb73cc52b08e3d7dfd3864988a57a5df02730be8f53f'),
        ('life_financial.spot_state(uuid)', 'bd565b7210543146a34c6936a639d3017b37abac0b8d063dcf4c3e4a403c035b')) AS x(signature,source_hash) LOOP
        SELECT * INTO STRICT p FROM pg_proc WHERE oid=item.signature::regprocedure;
        IF NOT p.prosecdef OR p.provolatile<>(CASE WHEN p.proname='spot_state' THEN 's' ELSE 'v' END)
            OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')<>item.source_hash THEN
            RAISE EXCEPTION 'LIFE_CONTROL_MIGRATION_MISMATCH';END IF;
        definition:=pg_get_functiondef(p.oid);
        IF p.proname='commit_owner_guard' THEN
            needle:='    SELECT * INTO op FROM life_financial.operations WHERE account_id=NEW.account_id AND committed_version=NEW.version;';
            replacement:=needle||E'\n    IF op.command->>''kind''=''NATIVE_CONTROL'' THEN\n        PERFORM life_financial.control_commit_check(NEW.account_id,op.operation_id);RETURN NULL;END IF;';
        ELSIF p.proname='spot_state' THEN
            needle:='    b:=life_financial.spot_body(p_account);';
            replacement:=needle||E'\n    IF EXISTS(SELECT 1 FROM life_financial.control_policies WHERE account_id=p_account) THEN\n        b:=b||jsonb_build_object(''control_verified'',life_financial.control_verified(p_account),\n            ''unverified'',(b->>''unverified'')::boolean OR NOT life_financial.control_verified(p_account),\n            ''financial_recovery_required'',(b->>''financial_recovery_required'')::boolean OR NOT life_financial.control_verified(p_account),\n            ''recovery_required'',(b->>''recovery_required'')::boolean OR NOT life_financial.control_verified(p_account));END IF;';
        ELSE
            needle:=E'BEGIN\n';
            replacement:=needle||E'    IF NOT life_financial.control_verified(p_account) THEN\n        RAISE EXCEPTION ''LIFE_CONTROL_UNVERIFIED'' USING ERRCODE=''P0001'';END IF;\n';
        END IF;
        IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
            RAISE EXCEPTION 'LIFE_CONTROL_MIGRATION_MISMATCH';END IF;
        EXECUTE replace(definition,needle,replacement);
    END LOOP;
END; $$;
DO $$ DECLARE p regprocedure;r record;BEGIN
    FOREACH p IN ARRAY ARRAY['life_financial.control_owner(jsonb)'::regprocedure,'life_financial.control_verified(uuid)'::regprocedure,
        'life_financial.control_facts_qualified(uuid,jsonb)'::regprocedure,'life_financial.control_commit_check(uuid,text)'::regprocedure] LOOP
        FOR r IN SELECT DISTINCT x.grantee FROM pg_proc f
            CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x WHERE f.oid=p AND x.grantee<>f.proowner LOOP
            IF r.grantee=0 THEN EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',p);
            ELSE EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',p,pg_get_userbyid(r.grantee));END IF;
        END LOOP;
    END LOOP;
END; $$;
