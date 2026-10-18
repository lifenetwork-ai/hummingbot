-- Explicit, privileged source import. Runtime may only retain the exact
-- approved batch as pending. Accounting and permission never come from import.
CREATE TABLE life_financial.legacy_applying_policies (
    account_id uuid PRIMARY KEY REFERENCES life_financial.joint_batch_policies,
    policy_hash life_financial.digest NOT NULL,
    max_imports integer NOT NULL CHECK(max_imports BETWEEN 1 AND 16),
    max_total_bytes integer NOT NULL CHECK(max_total_bytes BETWEEN 1 AND 16777216),
    max_fact_age_ms integer NOT NULL CHECK(max_fact_age_ms BETWEEN 1 AND 60000),
    FOREIGN KEY(account_id,policy_hash) REFERENCES life_financial.policies
);
CREATE TABLE life_financial.legacy_applying_imports (
    account_id uuid NOT NULL REFERENCES life_financial.legacy_applying_policies,
    operation_id life_financial.identity NOT NULL,
    source_digest life_financial.digest NOT NULL,
    body jsonb NOT NULL CHECK(jsonb_typeof(body)='object' AND octet_length(body::text)<=4194304),
    body_hash life_financial.digest NOT NULL,captured_at timestamptz NOT NULL,
    PRIMARY KEY(account_id,operation_id)
);
DO $$ DECLARE t text;BEGIN
    FOREACH t IN ARRAY ARRAY['legacy_applying_policies','legacy_applying_imports'] LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',t);
    END LOOP;
END; $$;

CREATE FUNCTION life_financial.legacy_applying_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE p life_financial.legacy_applying_policies;r life_financial.legacy_applying_imports;
    b life_financial.joint_financial_batches;o life_financial.operations;cfg jsonb;
BEGIN
    SELECT * INTO p FROM life_financial.legacy_applying_policies WHERE account_id=p_account;
    IF NOT FOUND THEN RETURN NOT EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.joint_financial_batches WHERE account_id=p_account AND reason='LIFE_LEGACY_APPLYING');END IF;
    SELECT c.body INTO STRICT cfg FROM life_financial.configurations c JOIN life_financial.accounts a USING(account_id)
        WHERE c.account_id=p_account AND c.config_hash=a.config_hash AND c.policy_hash=p.policy_hash;
    IF (SELECT count(*) FROM life_financial.legacy_applying_imports WHERE account_id=p_account)>p.max_imports
        OR (SELECT COALESCE(sum(octet_length(to_jsonb(i)::text)),0) FROM life_financial.legacy_applying_imports i WHERE account_id=p_account)>p.max_total_bytes THEN RETURN false;END IF;
    FOR r IN SELECT * FROM life_financial.legacy_applying_imports WHERE account_id=p_account LOOP
        IF r.body_hash IS DISTINCT FROM encode(sha256(convert_to(r.body::text,'UTF8')),'hex')
            OR r.body#>>'{payload,kind}' IS DISTINCT FROM 'IMPORT_LEGACY_APPLYING'
            OR r.body#>>'{payload,operation_id}' IS DISTINCT FROM r.operation_id::text
            OR r.body#>>'{payload,batch,operation_id}' IS DISTINCT FROM r.operation_id::text
            OR r.body#>>'{payload,source_digest}' IS DISTINCT FROM r.source_digest::text
            OR r.body#>>'{approval,role}' IS DISTINCT FROM 'IMPORT'
            OR r.body#>>'{retirement,approval,role}' IS DISTINCT FROM 'IMPORT'
            OR r.body->'verifier_policy' IS DISTINCT FROM cfg->'operator_verifier'
            OR r.body->'source_verifier_policy' IS DISTINCT FROM cfg->'legacy_source_verifier'
            OR r.body#>'{approval,binding}' IS DISTINCT FROM r.body#>'{cut,checkpoint,binding}'
            OR r.body#>'{approval,binding}' IS DISTINCT FROM r.body#>'{facts,binding}'
            OR r.body#>'{approval,binding}' IS DISTINCT FROM r.body#>'{retirement,payload,target_binding}'
            OR (r.body#>>'{retirement,payload,target_database_lineage}')::uuid IS DISTINCT FROM (SELECT lineage FROM life_storage.storage_metadata WHERE singleton)
            OR r.captured_at<(r.body#>>'{approval,issued_at}')::timestamptz
            OR r.captured_at>=(r.body#>>'{approval,expires_at}')::timestamptz
            OR r.captured_at<(r.body#>>'{facts,observed_at}')::timestamptz
            OR extract(epoch FROM (r.captured_at-(r.body#>>'{facts,observed_at}')::timestamptz))*1000>p.max_fact_age_ms
            OR (r.body#>>'{account,halted}')::boolean IS DISTINCT FROM true
            OR NOT EXISTS(SELECT 1 FROM life_financial.spot_capital_policies x WHERE x.account_id=p_account
                AND x.opening_base=(r.body#>>'{source_basis,opening_base_life}')::numeric
                AND x.opening_quote=(r.body#>>'{source_basis,opening_quote_usdt}')::numeric
                AND x.opening_price=(r.body#>>'{source_basis,opening_independent_price_usdt}')::numeric
                AND x.opened_at=(r.body#>>'{source_basis,opened_at}')::timestamptz
                AND x.anchor_bill=r.body#>>'{source_basis,anchor_bill_id}') THEN RETURN false;END IF;
        SELECT * INTO o FROM life_financial.operations WHERE account_id=p_account AND operation_id=r.operation_id;
        IF FOUND THEN
            SELECT * INTO STRICT b FROM life_financial.joint_financial_batches WHERE account_id=p_account AND operation_id=r.operation_id;
            IF o.command->>'kind' IS DISTINCT FROM 'JOINT_BATCH' OR b.state<>'RECOVERY_REQUIRED' OR b.reason<>'LIFE_LEGACY_APPLYING'
                OR b.command IS DISTINCT FROM r.body#>'{payload,batch}' OR o.command->'command' IS DISTINCT FROM b.command
                OR o.command->'binding' IS DISTINCT FROM r.body#>'{approval,binding}'
                OR o.command->>'cut' IS DISTINCT FROM r.body#>>'{cut,checkpoint,state_digest}'
                OR (o.command->>'version')::bigint IS DISTINCT FROM (r.body#>>'{cut,checkpoint,revision}')::bigint
                OR o.committed_version<>b.start_version+1 OR b.money_before IS DISTINCT FROM b.money_after
                OR b.members<>'[]'::jsonb THEN RETURN false;END IF;
        END IF;
    END LOOP;
    RETURN NOT EXISTS(SELECT 1 FROM life_financial.joint_financial_batches b WHERE b.account_id=p_account AND b.reason='LIFE_LEGACY_APPLYING'
        AND NOT EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports i WHERE i.account_id=p_account AND i.operation_id=b.operation_id));
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

-- Preserve the existing OID/ACL and history checks. Broken provenance must
-- block ordinary account reads/repair/release, not only importer lookup.
DO $$ DECLARE definition text;BEGIN
    definition:=pg_get_functiondef('life_financial.joint_batch_verified(uuid)'::regprocedure);
    IF position('legacy_applying_verified' IN definition)>0 THEN RAISE EXCEPTION 'LIFE_LEGACY_APPLYING_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,'BEGIN','BEGIN
    IF NOT life_financial.legacy_applying_verified(p_account) THEN RETURN false;END IF;');
END; $$;

CREATE FUNCTION life_financial.legacy_applying_fresh(p_account uuid,p_operation text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r life_financial.legacy_applying_imports;p life_financial.legacy_applying_policies;a life_financial.accounts;at timestamptz:=clock_timestamp();
BEGIN
    SELECT * INTO STRICT r FROM life_financial.legacy_applying_imports WHERE account_id=p_account AND operation_id=p_operation;
    SELECT * INTO STRICT p FROM life_financial.legacy_applying_policies WHERE account_id=p_account;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account;
    IF NOT life_financial.legacy_applying_verified(p_account) OR NOT a.halted
        OR a.mode NOT IN ('ACTIVE','DRAINING','RECOVERY_ONLY') OR a.mode IS DISTINCT FROM r.body#>>'{account,mode}'
        OR at<r.captured_at OR at>=(r.body#>>'{approval,expires_at}')::timestamptz
        OR at<(r.body#>>'{facts,observed_at}')::timestamptz
        OR extract(epoch FROM (at-(r.body#>>'{facts,observed_at}')::timestamptz))*1000>p.max_fact_age_ms
        OR a.owner_process IS DISTINCT FROM (r.body#>>'{account,owner_process}')::uuid
        OR a.owner_generation IS DISTINCT FROM (r.body#>>'{account,owner_generation}')::uuid
        OR a.current_session IS DISTINCT FROM (r.body#>>'{account,current_session}')::uuid
        OR a.owner_epoch IS DISTINCT FROM (r.body#>>'{account,owner_epoch}')::bigint
        OR a.owner_since IS DISTINCT FROM (r.body#>>'{account,owner_since}')::timestamptz
        OR a.owner_until IS DISTINCT FROM (r.body#>>'{account,owner_until}')::timestamptz
        OR a.lineage IS DISTINCT FROM (r.body#>>'{account,lineage}')::uuid
        OR a.config_hash IS DISTINCT FROM r.body#>>'{account,config_hash}'
        OR a.policy_hash IS DISTINCT FROM r.body#>>'{account,policy_hash}' THEN
        RAISE EXCEPTION 'LIFE_LEGACY_APPLYING_STALE_AT_COMMIT' USING ERRCODE='P0001';END IF;
END; $$;

CREATE FUNCTION life_financial.legacy_applying_pending(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,cmd jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE a life_financial.accounts;r life_financial.legacy_applying_imports;p life_financial.joint_batch_policies;
    o life_financial.operations;request jsonb;view jsonb;money jsonb;result jsonb;at timestamptz;
BEGIN
    request:=jsonb_build_object('kind','JOINT_BATCH','binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',cmd);
    IF p_fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(request),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'LIFE_LEGACY_APPLYING_FINGERPRINT_INVALID' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,a.version);
    IF NOT life_financial.joint_batch_verified(p_account) THEN RAISE EXCEPTION 'LIFE_LEGACY_APPLYING_UNVERIFIED' USING ERRCODE='P0001';END IF;
    SELECT * INTO o FROM life_financial.operations WHERE account_id=p_account AND operation_id=cmd->>'operation_id';
    IF FOUND THEN
        IF o.command IS DISTINCT FROM request OR o.fingerprint IS DISTINCT FROM p_fingerprint
            OR NOT EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports WHERE account_id=p_account AND operation_id=o.operation_id) THEN
            RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001';END IF;
        RETURN o.receipt||'{"replayed":true}'::jsonb;END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    view:=life_financial.spot_state(p_account);
    IF view->>'digest' IS DISTINCT FROM p_cut OR (view->>'unverified')::boolean THEN
        RAISE EXCEPTION 'LIFE_LEGACY_APPLYING_CUT_STALE' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT r FROM life_financial.legacy_applying_imports WHERE account_id=p_account AND operation_id=cmd->>'operation_id';
    IF r.body#>'{payload,batch}' IS DISTINCT FROM cmd OR r.body#>'{approval,binding}' IS DISTINCT FROM p_binding
        OR r.body#>>'{cut,checkpoint,state_digest}' IS DISTINCT FROM p_cut
        OR (r.body#>>'{cut,checkpoint,revision}')::bigint<>p_version
        OR EXISTS(SELECT 1 FROM life_financial.financial_events WHERE account_id=p_account)
        OR EXISTS(SELECT 1 FROM life_financial.joint_financial_batches WHERE account_id=p_account)
        OR life_financial.joint_financial_pending(p_account) THEN
        RAISE EXCEPTION 'LIFE_LEGACY_APPLYING_ORIGINAL_BASIS_REQUIRED' USING ERRCODE='P0001';END IF;
    PERFORM life_financial.legacy_applying_fresh(p_account,cmd->>'operation_id');
    SELECT * INTO STRICT p FROM life_financial.joint_batch_policies WHERE account_id=p_account;
    IF jsonb_array_length(life_financial.joint_batch_members(cmd))>p.max_members THEN
        RAISE EXCEPTION 'LIFE_JOINT_BATCH_BOUND' USING ERRCODE='P0001';END IF;
    money:=life_financial.portfolio_money_at(p_account,p_version);at:=clock_timestamp();
    result:=jsonb_build_object('kind','JOINT_BATCH','operation_id',cmd->>'operation_id','state','RECOVERY_REQUIRED',
        'cash_delta_usdt','0','base_delta_life','0','version',p_version+1,'replayed',false,'trading_permission',false,'reason','LIFE_LEGACY_APPLYING');
    INSERT INTO life_financial.joint_financial_batches VALUES(p_account,cmd->>'operation_id',cmd,'RECOVERY_REQUIRED','LIFE_LEGACY_APPLYING',
        p_version,floor(extract(epoch FROM at)*1000)::bigint,money,money,'[]');
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version,recorded_at)
        VALUES(p_account,cmd->>'operation_id',p_fingerprint,request,result,p_version+1,at);
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    RETURN result;
END; $$;

CREATE FUNCTION life_financial.legacy_applying_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF TG_TABLE_NAME='operations' THEN
        IF NEW.command->>'kind' IS DISTINCT FROM 'JOINT_BATCH' OR NOT EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports WHERE account_id=NEW.account_id AND operation_id=NEW.operation_id) THEN RETURN NULL;END IF;
    END IF;
    PERFORM life_financial.legacy_applying_fresh(NEW.account_id,NEW.operation_id);
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER legacy_applying_source_commit AFTER INSERT ON life_financial.legacy_applying_imports
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.legacy_applying_commit_guard();
CREATE CONSTRAINT TRIGGER legacy_applying_pending_commit AFTER INSERT ON life_financial.operations
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN(NEW.command->>'kind'='JOINT_BATCH') EXECUTE FUNCTION life_financial.legacy_applying_commit_guard();

-- Source approval COMMIT and runtime pending COMMIT are separate physical
-- boundaries. An orphan approval must not let an ordinary observation writer
-- book its facts without publishing the diagnostic root and signed RECOVER.
-- Late ACK/recorder observations remain recordable; financial/new-risk/release
-- operations roll back entirely, including any facts staged before this guard.
CREATE FUNCTION life_financial.legacy_applying_publication_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports i WHERE i.account_id=NEW.account_id
        AND NOT EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=i.account_id AND o.operation_id=i.operation_id AND o.command->>'kind'='JOINT_BATCH'))
        AND NOT (NEW.command->>'kind'='JOINT_BATCH' AND NEW.receipt->>'state'='RECOVERY_REQUIRED'
            AND NEW.receipt->>'reason'='LIFE_LEGACY_APPLYING'
            AND EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports i WHERE i.account_id=NEW.account_id
                AND i.operation_id=NEW.operation_id AND i.body#>'{payload,batch}'=NEW.command->'command')) THEN
        RAISE EXCEPTION 'LIFE_LEGACY_APPLYING_PENDING_REQUIRED' USING ERRCODE='P0001';END IF;
    RETURN NEW;
END; $$;
CREATE TRIGGER legacy_applying_publication BEFORE INSERT ON life_financial.operations FOR EACH ROW
    WHEN(NEW.command->>'kind' IN ('JOINT_BATCH','JOINT_REPAIR','SPOT_PREPARE','SPOT_ARM','SPOT_FILL','SPOT_CASHFLOW',
        'SWAP_PREPARE','SWAP_ARM','SWAP_FILL','PAID_FUNDING','SWAP_TERMINAL','SPOT_TERMINAL','SHARED_RELEASE',
        'SPOT_INVENTORY_SETTLE','HEDGE_PLAN')) EXECUTE FUNCTION life_financial.legacy_applying_publication_guard();
REVOKE ALL ON life_financial.legacy_applying_policies,life_financial.legacy_applying_imports FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
