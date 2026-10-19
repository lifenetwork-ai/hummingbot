-- Explicit execution policy; signed mode history lives in the original control journal.
CREATE TABLE life_financial.execution_policies (
    account_id uuid PRIMARY KEY REFERENCES life_financial.risk_service_policies,
    policy_hash life_financial.digest NOT NULL,policy_digest life_financial.digest NOT NULL,
    body jsonb NOT NULL CHECK(jsonb_typeof(body)='object'),
    CHECK(policy_digest=encode(sha256(convert_to(life_financial.canonical_json_v1(body),'UTF8')),'hex')),
    FOREIGN KEY(account_id,policy_hash) REFERENCES life_financial.policies
);
ALTER TABLE life_financial.execution_policies ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped_read ON life_financial.execution_policies FOR SELECT USING(life_financial.can_access(account_id));
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.execution_policies
    FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record();
CREATE FUNCTION life_financial.execution_policy_qualified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
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
    RETURN (e.body#>>'{quote_budget,session}')::numeric<=(e.body#>>'{quote_budget,day}')::numeric
        AND (e.body#>>'{quote_budget,day}')::numeric<=(e.body#>>'{quote_budget,campaign}')::numeric
        AND (e.body#>>'{exit_budget,session}')::numeric<=(e.body#>>'{exit_budget,day}')::numeric
        AND (e.body#>>'{exit_budget,day}')::numeric<=(e.body#>>'{exit_budget,campaign}')::numeric;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
CREATE FUNCTION life_financial.execution_record_qualified(p_account uuid,p_operation text) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r life_financial.control_records;p life_financial.execution_policies;
BEGIN
    SELECT * INTO STRICT r FROM life_financial.control_records WHERE account_id=p_account AND operation_id=p_operation;
    SELECT * INTO STRICT p FROM life_financial.execution_policies WHERE account_id=p_account;
    RETURN COALESCE(life_financial.execution_policy_qualified(p_account)
        AND r.body#>>'{payload,kind}'='EXECUTION_MODE'
        AND r.body#>>'{payload,mode}' IN ('MM','INVENTORY','EXIT')
        AND r.body#>>'{payload,policy_digest}'=p.policy_digest AND r.body#>>'{payload,campaign_id}'=p.body->>'campaign_id'
        AND r.body#>>'{approval,role}'='INVENTORY'
        AND (r.body#>>'{cut,checkpoint,revision}')::bigint=(r.body#>>'{before_account,version}')::bigint
        AND (r.body#>>'{payload,observer_id}')::uuid IS NOT NULL
        AND r.body#>>'{before_account,mode}'='ACTIVE'
        AND (r.body->'before_account')-'version'=(r.body->'after_account')-'version'
        AND r.body#>>'{receipt,kind}'='EXECUTION_MODE'
        AND r.body#>>'{receipt,mode}'=r.body#>>'{payload,mode}'
        AND r.body#>'{receipt,observer_id}'=r.body#>'{payload,observer_id}'
        AND r.body#>'{receipt,trading_permission}'='false'::jsonb
        AND (r.body#>>'{receipt,version}')::bigint=(r.body#>>'{after_account,version}')::bigint,false);
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
CREATE FUNCTION life_financial.execution_facts_qualified(p_account uuid,f jsonb) RETURNS boolean
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

CREATE FUNCTION life_financial.execution_quote_budget_check(p_account uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.execution_policies;scope_name text;used numeric;cap numeric;s uuid;today text;
BEGIN
    SELECT * INTO STRICT p FROM life_financial.execution_policies WHERE account_id=p_account;
    SELECT current_session INTO STRICT s FROM life_financial.accounts WHERE account_id=p_account;
    today:=to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD');
    FOREACH scope_name IN ARRAY ARRAY['campaign','day','session'] LOOP
        SELECT COALESCE(sum(CASE WHEN m.intent_id IS NOT NULL THEN m.actual_usdt
            WHEN t.state='ABORTED_BEFORE_SEND' OR t.state='TERMINAL' AND COALESCE(g.filled_base,0)=0 THEN 0
            ELSE greatest(d.subsidy,COALESCE(g.execution_loss,0)) END),0) INTO used
        FROM life_financial.spot_preparations d JOIN life_financial.send_attempts t USING(account_id,intent_id)
        LEFT JOIN life_financial.spot_accounting_progress g USING(account_id,intent_id)
        LEFT JOIN life_financial.spot_inventory_members m USING(account_id,intent_id)
        WHERE d.account_id=p_account AND d.request->>'budget_scope' IS DISTINCT FROM 'EXIT'
            AND (scope_name<>'day' OR to_char(d.prepared_at AT TIME ZONE 'UTC','YYYY-MM-DD')=today)
            AND (scope_name<>'session' OR t.session_id=s);
        cap:=(p.body#>>ARRAY['quote_budget',scope_name])::numeric;
        IF used>cap THEN RAISE EXCEPTION 'LIFE_EXECUTION_QUOTE_BUDGET_EXCEEDED' USING ERRCODE='P0001';END IF;
    END LOOP;
END; $$;
CREATE FUNCTION life_financial.execution_admit(p_account uuid,op life_financial.operations) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts;r life_financial.control_records;
BEGIN
    IF NOT EXISTS(SELECT 1 FROM life_financial.execution_policies WHERE account_id=p_account) THEN RETURN;END IF;
    IF NOT life_financial.control_verified(p_account) OR NOT life_financial.execution_policy_qualified(p_account) THEN
        RAISE EXCEPTION 'LIFE_EXECUTION_UNVERIFIED' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account;
    SELECT * INTO r FROM life_financial.control_records WHERE account_id=p_account AND body#>>'{payload,kind}'='EXECUTION_MODE' ORDER BY sequence DESC LIMIT 1;
    IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_EXECUTION_MODE_REQUIRED' USING ERRCODE='P0001';END IF;
    IF NOT life_financial.execution_record_qualified(p_account,r.operation_id)
        OR r.body#>>'{before_account,owner_generation}' IS DISTINCT FROM a.owner_generation::text
        OR r.body#>>'{before_account,current_session}' IS DISTINCT FROM a.current_session::text
        OR r.body#>>'{payload,observer_id}' IS DISTINCT FROM current_setting('life.risk_observer',true) THEN
        RAISE EXCEPTION 'LIFE_EXECUTION_FRESH_MODE_REQUIRED' USING ERRCODE='P0001';END IF;
    IF r.body#>>'{payload,mode}'<>'MM' THEN RAISE EXCEPTION 'LIFE_EXECUTION_MARKET_REQUIRED' USING ERRCODE='P0001';END IF;
    IF op.receipt->>'kind' IN ('SPOT_PREPARE','SPOT_ARM','SPOT_QUOTE_ARM') THEN PERFORM life_financial.execution_quote_budget_check(p_account);END IF;
END; $$;
CREATE FUNCTION life_financial.execution_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE op life_financial.operations;
BEGIN
    IF NOT EXISTS(SELECT 1 FROM life_financial.execution_policies WHERE account_id=NEW.account_id) THEN RETURN NULL;END IF;
    SELECT * INTO STRICT op FROM life_financial.operations WHERE account_id=NEW.account_id AND committed_version=NEW.version;
    IF op.receipt->>'kind' IN ('SPOT_PREPARE','SPOT_ARM','SPOT_QUOTE_ARM')
        OR op.command->>'kind' IN ('SWAP_PREPARE','SWAP_ARM','SWAP_BOUNDARY','HEDGE_PLAN','HEDGE_CHILD')
        OR op.command->>'kind' IS NULL AND op.command ?& ARRAY['asset','amount','binding','owner']
        OR op.command->>'kind'='SHARED_CAPITAL' AND op.command#>>'{command,kind}'='RESERVE'
        OR op.command->>'kind'='SPOT_ACTION_TRANSITION' AND op.command#>>'{command,state}'='DISPATCHED' THEN
        PERFORM life_financial.execution_admit(NEW.account_id,op);
    END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER zzzzz_native_execution_commit AFTER UPDATE ON life_financial.accounts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN(NEW.version<>OLD.version) EXECUTE FUNCTION life_financial.execution_commit_guard();
DO $$ DECLARE item record;f record;definition text;needle text;replacement text;BEGIN
    FOR item IN SELECT * FROM (VALUES
        ('life_financial.control_verified(uuid)','95b8591b442a401e8ec0acf401a252a826378f6854953cfe85d3df301d60db3a','s'),
        ('life_financial.control_commit_check(uuid,text)','d32eb3f046cbc8d67aa21c55f612053c58a51b2931956316afdd0b018c41c934','v')) x(signature,source_hash,volatility) LOOP
        SELECT * INTO STRICT f FROM pg_proc WHERE oid=item.signature::regprocedure;
        IF NOT f.prosecdef OR f.provolatile::text<>item.volatility OR f.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR encode(sha256(convert_to(f.prosrc,'UTF8')),'hex')<>item.source_hash THEN RAISE EXCEPTION 'LIFE_EXECUTION_MIGRATION_MISMATCH';END IF;
        definition:=pg_get_functiondef(f.oid);
        IF f.proname='control_verified' THEN
            needle:='        IF (r.body#>>''{cut,checkpoint,revision}'')::bigint';
            replacement:=E'        IF r.body#>>''{payload,kind}''=''EXECUTION_MODE'' THEN\n            IF NOT life_financial.execution_record_qualified(p_account,r.operation_id) THEN RETURN false;END IF;\n            previous_owner:=life_financial.control_owner(after_row);latest_owner:=previous_owner;CONTINUE;END IF;\n'||needle;
        ELSE
            needle:='    IF j#>>''{payload,mode}''=''ACTIVE'' THEN';
            replacement:=E'    IF j#>>''{payload,kind}''=''EXECUTION_MODE'' THEN\n        IF NOT life_financial.execution_record_qualified(p_account,p_operation) OR NOT life_financial.execution_facts_qualified(p_account,j->''facts'')\n            OR a.mode<>''ACTIVE'' OR at<a.owner_since OR at>=a.owner_until THEN\n            RAISE EXCEPTION ''LIFE_EXECUTION_MODE_UNQUALIFIED_AT_COMMIT'' USING ERRCODE=''P0001'';END IF;\n        RETURN;END IF;\n'||needle;
        END IF;
        IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_EXECUTION_MIGRATION_MISMATCH';END IF;
        EXECUTE replace(definition,needle,replacement);
    END LOOP;
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
END; $$;
