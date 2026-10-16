-- No runtime DML grants. Security-definer functions have fixed search paths,
-- scoped session_user mappings and exact expected-owner/namespace/version checks.
CREATE FUNCTION life_financial.can_access(p_account uuid, p_write boolean DEFAULT false)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT EXISTS (SELECT 1 FROM life_financial.access_bindings
        WHERE principal=session_user AND account_id=p_account AND (NOT p_write OR can_write));
$$;
DO $$ DECLARE t text; BEGIN
    FOR t IN SELECT tablename FROM pg_tables WHERE schemaname='life_financial'
        AND tablename NOT IN ('access_bindings','schema_contract') LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY scoped_read ON life_financial.%I FOR SELECT USING (life_financial.can_access(account_id))',t);
    END LOOP;
END; $$;

CREATE FUNCTION life_financial.guard_account(p_account uuid, p_binding jsonb, p_owner jsonb, p_version bigint)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts; s life_financial.sessions; actual jsonb; at timestamptz;
BEGIN
    IF p_binding IS NULL OR p_owner IS NULL OR p_version IS NULL
        OR NOT life_financial.can_access(p_account,true) OR pg_is_in_recovery() OR current_setting('transaction_read_only')<>'off'
        OR current_setting('synchronous_commit')<>'on' OR current_setting('fsync')<>'on' OR current_setting('full_page_writes')<>'on' THEN
        RAISE EXCEPTION 'LIFE_PRIMARY_DURABILITY_OR_SCOPE_INVALID' USING ERRCODE='P0001';
    END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    at := clock_timestamp(); -- after the account-row wait, not transaction start
    actual := jsonb_build_object('environment',a.environment,'exchange',a.exchange,'account_uid',a.account_uid,
        'risk_pool',a.risk_pool,'lineage',a.lineage,'schema_version',a.schema_version,'policy_hash',a.policy_hash,'config_hash',a.config_hash);
    SELECT * INTO STRICT s FROM life_financial.sessions WHERE account_id=a.account_id AND session_id=a.current_session;
    IF p_binding<>actual OR jsonb_typeof(p_owner)<>'object' OR (SELECT count(*) FROM jsonb_object_keys(p_owner))<>6
        OR a.owner_process IS DISTINCT FROM (p_owner->>'process_id')::uuid
        OR a.owner_generation IS DISTINCT FROM (p_owner->>'generation')::uuid
        OR a.owner_epoch IS DISTINCT FROM (p_owner->>'epoch')::bigint
        OR a.current_session IS DISTINCT FROM (p_owner->>'session_id')::uuid
        OR a.owner_since IS DISTINCT FROM (p_owner->>'valid_from')::timestamptz
        OR a.owner_until IS DISTINCT FROM (p_owner->>'expires_at')::timestamptz
        OR a.mode<>'ACTIVE' OR a.halted OR at<a.owner_since OR at>=a.owner_until
        OR at<s.started_at OR at>=s.expires_at OR a.owner_until>s.expires_at OR s.epoch<>a.owner_epoch
        OR NOT EXISTS (SELECT 1 FROM life_financial.configurations WHERE account_id=a.account_id AND config_version=s.config_version
            AND config_hash=a.config_hash AND policy_hash=a.policy_hash) THEN
        RAISE EXCEPTION 'LIFE_OWNER_OR_BINDING_INVALID' USING ERRCODE='P0001';
    END IF;
    IF a.version<>p_version THEN RAISE EXCEPTION 'LIFE_EXPECTED_VERSION_CONFLICT' USING ERRCODE='P0001'; END IF;
END; $$;

CREATE FUNCTION life_financial.commit_owner_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts; expiry timestamptz; at timestamptz;
BEGIN
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=NEW.account_id;
    SELECT expires_at INTO STRICT expiry FROM life_financial.sessions WHERE account_id=a.account_id AND session_id=a.current_session;
    at := clock_timestamp();
    IF a.owner_generation IS DISTINCT FROM NEW.owner_generation OR a.owner_process IS DISTINCT FROM NEW.owner_process
        OR a.owner_epoch<>NEW.owner_epoch OR a.current_session IS DISTINCT FROM NEW.current_session
        OR a.lineage<>NEW.lineage OR a.policy_hash<>NEW.policy_hash OR a.config_hash<>NEW.config_hash
        OR a.mode<>'ACTIVE' OR a.halted OR at<a.owner_since OR at>=a.owner_until OR at>=expiry
        OR current_setting('synchronous_commit')<>'on' OR current_setting('fsync')<>'on'
        OR current_setting('full_page_writes')<>'on' OR pg_is_in_recovery() THEN
        RAISE EXCEPTION 'LIFE_OWNER_EXPIRED_OR_CHANGED_AT_COMMIT' USING ERRCODE='P0001';
    END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER owner_at_commit AFTER UPDATE ON life_financial.accounts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.version<>OLD.version)
    EXECUTE FUNCTION life_financial.commit_owner_guard();

CREATE FUNCTION life_financial.account_version() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
    IF NEW.account_id<>OLD.account_id OR NEW.database_lineage<>OLD.database_lineage OR NEW.environment<>OLD.environment
        OR NEW.exchange<>OLD.exchange OR NEW.account_uid<>OLD.account_uid OR NEW.risk_pool<>OLD.risk_pool OR NEW.lineage<>OLD.lineage
        OR NEW.owner_epoch<OLD.owner_epoch OR (NEW.version<>OLD.version AND NEW.version<>OLD.version+1) THEN
        RAISE EXCEPTION 'LIFE_ACCOUNT_IDENTITY_OR_VERSION_INVALID' USING ERRCODE='P0001';
    END IF;
    RETURN NEW;
END; $$;
CREATE TRIGGER account_identity BEFORE UPDATE ON life_financial.accounts FOR EACH ROW EXECUTE FUNCTION life_financial.account_version();

CREATE FUNCTION life_financial.attempt_transition() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
    IF (to_jsonb(NEW)-ARRAY['state','exchange_order_id','version'])<>(to_jsonb(OLD)-ARRAY['state','exchange_order_id','version'])
        OR OLD.state IN ('TERMINAL','ABORTED_BEFORE_SEND')
        OR (OLD.exchange_order_id IS NOT NULL AND NEW.exchange_order_id IS DISTINCT FROM OLD.exchange_order_id)
        OR NOT (NEW.state=OLD.state OR
            OLD.state='PREPARED' AND NEW.state IN ('ARMED','ABORTED_BEFORE_SEND') OR
            OLD.state='ARMED' AND NEW.state IN ('SEND_UNKNOWN','ACKED') OR
            OLD.state='SEND_UNKNOWN' AND NEW.state IN ('ACKED','TERMINAL') OR
            OLD.state='ACKED' AND NEW.state='TERMINAL') THEN
        RAISE EXCEPTION 'LIFE_ATTEMPT_TRANSITION_INVALID' USING ERRCODE='P0001';
    END IF;
    RETURN NEW;
END; $$;
CREATE TRIGGER attempt_state BEFORE UPDATE ON life_financial.send_attempts FOR EACH ROW EXECUTE FUNCTION life_financial.attempt_transition();
CREATE FUNCTION life_financial.reservation_transition() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
    IF (to_jsonb(NEW)-ARRAY['remaining','state','version'])<>(to_jsonb(OLD)-ARRAY['remaining','state','version'])
        OR NEW.remaining>OLD.remaining OR OLD.state='TERMINAL'
        OR OLD.state='CANCEL_REQUESTED' AND NEW.state='HELD' THEN
        RAISE EXCEPTION 'LIFE_RESERVATION_TRANSITION_INVALID' USING ERRCODE='P0001';
    END IF;
    RETURN NEW;
END; $$;
CREATE TRIGGER reservation_state BEFORE UPDATE ON life_financial.reservations FOR EACH ROW EXECUTE FUNCTION life_financial.reservation_transition();

CREATE FUNCTION life_financial.product_transition() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE before jsonb := to_jsonb(OLD); after jsonb := to_jsonb(NEW); allowed boolean;
BEGIN
    IF TG_TABLE_NAME='budget_buckets' AND (after->>'incurred')::numeric<(before->>'incurred')::numeric THEN
        RAISE EXCEPTION 'LIFE_INCURRED_COST_REGRESSION' USING ERRCODE='P0001';
    ELSIF TG_TABLE_NAME='inventory_campaigns' AND (after->>'deadline'<>before->>'deadline'
        OR after->>'turnover_limit_base'<>before->>'turnover_limit_base'
        OR (after->>'gross_turnover_base')::numeric<(before->>'gross_turnover_base')::numeric) THEN
        RAISE EXCEPTION 'LIFE_CAMPAIGN_TERMS_OR_TURNOVER_REGRESSION' USING ERRCODE='P0001';
    ELSIF TG_TABLE_NAME='hedge_plans' AND ((after->>'gross_attempts')::integer<(before->>'gross_attempts')::integer
        OR (after->>'incurred_usdt')::numeric<(before->>'incurred_usdt')::numeric) THEN
        RAISE EXCEPTION 'LIFE_HEDGE_COST_OR_ATTEMPT_REGRESSION' USING ERRCODE='P0001';
    END IF;
    IF before ? 'state' THEN
        IF before->>'state' IN ('TERMINAL','REJECTED','RECONCILED','APPLIED','FAILED') THEN
            RAISE EXCEPTION 'LIFE_TERMINAL_PRODUCT_IMMUTABLE' USING ERRCODE='P0001';
        END IF;
        allowed := after->>'state'=before->>'state';
        IF TG_TABLE_NAME='capital_claims' THEN allowed := allowed OR after->>'state'='TERMINAL';
        ELSIF TG_TABLE_NAME='joint_receipts' THEN allowed := allowed OR (before->>'state'='APPLYING' AND after->>'state'='APPLIED');
        ELSIF TG_TABLE_NAME='quote_actions' THEN allowed := allowed OR
            (before->>'state'='PROPOSED' AND after->>'state' IN ('DISPATCHED','REJECTED','RECONCILED')) OR
            (before->>'state' IN ('DISPATCHED','PREPARED') AND after->>'state' IN ('PREPARED','REJECTED','RECONCILED'));
        ELSIF TG_TABLE_NAME='hedge_plans' THEN allowed := allowed OR
            (before->>'state'='PLANNING' AND after->>'state'='PLANNED') OR
            (before->>'state' IN ('PLANNED','APPLYING') AND after->>'state' IN ('PLANNED','APPLYING','TERMINAL'));
        ELSIF TG_TABLE_NAME='import_runs' THEN allowed := allowed OR
            (before->>'state'='STAGING' AND after->>'state' IN ('APPLYING','FAILED')) OR
            (before->>'state'='APPLYING' AND after->>'state' IN ('APPLIED','FAILED'));
        END IF;
        IF NOT allowed THEN RAISE EXCEPTION 'LIFE_PRODUCT_TRANSITION_INVALID' USING ERRCODE='P0001'; END IF;
    END IF;
    RETURN NEW;
END; $$;
DO $$ DECLARE t text; r record; BEGIN
    FOREACH t IN ARRAY ARRAY['capital_claims','quote_actions','hedge_plans','joint_receipts','import_runs','budget_buckets','inventory_campaigns'] LOOP
        EXECUTE format('CREATE TRIGGER product_state BEFORE UPDATE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.product_transition()',t);
    END LOOP;
    FOR r IN SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='life_financial' AND udt_name='uuid' LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ADD CONSTRAINT %I CHECK (%I<>''00000000-0000-0000-0000-000000000000''::uuid)',
            r.table_name,'nonzero_'||r.column_name,r.column_name);
    END LOOP;
END; $$;

-- Infrastructure allocation primitive for PG2 concurrency/idempotency proof.
-- It does not create an intent, arm a send, port financial commands or release funds.
CREATE FUNCTION life_financial.allocate_balance(p_account uuid, p_binding jsonb, p_owner jsonb, p_version bigint,
    p_operation life_financial.identity, p_fingerprint life_financial.digest, p_asset text, p_amount life_financial.positive_amount)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE previous life_financial.operations; b life_financial.balances; result jsonb; request jsonb;
BEGIN
    IF p_fingerprint IS NULL OR p_operation IS NULL OR NOT life_financial.can_access(p_account,true) THEN
        RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
    PERFORM 1 FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    request := jsonb_build_object('binding',p_binding,'owner',p_owner,'version',p_version,'asset',p_asset,'amount',p_amount::text);
    SELECT * INTO previous FROM life_financial.operations WHERE account_id=p_account AND operation_id=p_operation;
    IF FOUND THEN
        IF previous.fingerprint<>p_fingerprint OR previous.command<>request THEN
            RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001';
        END IF;
        RETURN previous.receipt || '{"replayed":true}'::jsonb;
    END IF;
    PERFORM life_financial.guard_account(p_account,p_binding,p_owner,p_version);
    SELECT * INTO STRICT b FROM life_financial.balances WHERE account_id=p_account AND asset=p_asset FOR UPDATE;
    IF b.total-b.liability-b.reserved<p_amount THEN RAISE EXCEPTION 'LIFE_INSUFFICIENT_BALANCE' USING ERRCODE='P0001'; END IF;
    UPDATE life_financial.balances SET reserved=reserved+p_amount,version=version+1 WHERE account_id=p_account AND asset=p_asset;
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    result := jsonb_build_object('account_id',p_account,'operation_id',p_operation,'version',p_version+1,
        'asset',p_asset,'amount',p_amount::text,'replayed',false,'trading_permission',false);
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version)
        VALUES(p_account,p_operation,p_fingerprint,request,result,p_version+1);
    INSERT INTO life_financial.reservations(account_id,reservation_id,operation_id,asset,amount,remaining,state)
        VALUES(p_account,p_operation,p_operation,p_asset,p_amount,p_amount,'HELD');
    RETURN result;
END; $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
