-- One signed funding cohort, one account transaction. Original observations
-- and receipts remain immutable; a member outside the approved set still blocks.
CREATE TABLE life_financial.joint_repair_members (
    account_id uuid NOT NULL,operation_id life_financial.identity NOT NULL,
    pending_operation_id life_financial.identity NOT NULL,
    PRIMARY KEY(account_id,operation_id,pending_operation_id),UNIQUE(account_id,pending_operation_id),
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.joint_repair_facts,
    FOREIGN KEY(account_id,pending_operation_id) REFERENCES life_financial.operations
);
ALTER TABLE life_financial.joint_repair_members ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped_read ON life_financial.joint_repair_members FOR SELECT USING(life_financial.can_access(account_id));
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.joint_repair_members
    FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record();
ALTER TABLE life_financial.joint_repair_staging ADD COLUMN additional_pending_operation_ids jsonb NOT NULL DEFAULT '[]'
    CHECK(jsonb_typeof(additional_pending_operation_ids)='array');

CREATE FUNCTION life_financial.joint_repair_targets(cmd jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
SELECT jsonb_build_array(cmd->>'pending_operation_id')||COALESCE(cmd->'additional_pending_operation_ids','[]'::jsonb);
$$;
CREATE FUNCTION life_financial.joint_repair_sources(p_account uuid,cmd jsonb) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb;id text;raw life_financial.joint_financial_batches;
BEGIN
    IF NOT cmd ? 'additional_pending_operation_ids' THEN
        SELECT * INTO STRICT raw FROM life_financial.joint_repair_origin(p_account,cmd->>'pending_operation_id');
        RETURN raw.command;END IF;
    result:='{}';
    FOR id IN SELECT value FROM jsonb_array_elements_text(life_financial.joint_repair_targets(cmd)) LOOP
        SELECT * INTO STRICT raw FROM life_financial.joint_repair_origin(p_account,id);
        result:=result||jsonb_build_object(id,raw.command);
    END LOOP;
    RETURN result;
END; $$;

CREATE OR REPLACE FUNCTION life_financial.funding_pending_unresolved(p_account uuid,p_operation text,
    p_version bigint DEFAULT NULL,p_staged boolean DEFAULT false) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT NOT EXISTS(SELECT 1 FROM (
        SELECT account_id,operation_id,pending_operation_id FROM life_financial.joint_repair_facts
        UNION ALL SELECT account_id,operation_id,pending_operation_id FROM life_financial.joint_repair_members) r
    JOIN life_financial.operations o USING(account_id,operation_id)
    WHERE r.account_id=p_account AND r.pending_operation_id=p_operation
        AND o.command->>'kind'='JOINT_REPAIR' AND o.receipt->>'state'='REPAIRED'
        AND (p_version IS NULL OR o.committed_version<=p_version))
    AND NOT (p_staged AND EXISTS(SELECT 1 FROM life_financial.joint_repair_staging s
        JOIN life_financial.joint_repair_approvals g ON g.account_id=s.account_id
            AND g.operation_id=s.repair_operation_id AND g.pending_operation_id=s.pending_operation_id
            AND g.body#>'{payload,replacement}'=s.command
            AND COALESCE(g.body#>'{payload,additional_pending_operation_ids}','[]'::jsonb)=s.additional_pending_operation_ids
        WHERE s.account_id=p_account AND s.transaction_id=pg_current_xact_id()
            AND (s.pending_operation_id=p_operation OR s.additional_pending_operation_ids @> jsonb_build_array(p_operation))));
$$;

-- Preserve the single-parent profile and its OID/ACL. A cohort may contain only
-- direct funding pending operations, all covered by the unchanged final source.
DO $$ DECLARE definition text;needle text;BEGIN
    definition:=pg_get_functiondef('life_financial.joint_repair_profile(uuid,jsonb,jsonb,bigint)'::regprocedure);
    needle:='CREATE OR REPLACE FUNCTION life_financial.joint_repair_profile(';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_COHORT_PROFILE_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,'CREATE OR REPLACE FUNCTION life_financial.joint_repair_profile_single(');
END; $$;
CREATE OR REPLACE FUNCTION life_financial.joint_repair_profile(p_account uuid,cmd jsonb,facts jsonb,p_version bigint) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE id text;raw life_financial.joint_financial_batches;targets jsonb;limit_count integer;
BEGIN
    IF NOT life_financial.joint_repair_profile_single(p_account,cmd-'additional_pending_operation_ids',facts,p_version) THEN RETURN false;END IF;
    IF NOT cmd ? 'additional_pending_operation_ids' THEN RETURN true;END IF;
    SELECT max_members INTO STRICT limit_count FROM life_financial.joint_batch_policies WHERE account_id=p_account;
    IF jsonb_typeof(cmd->'additional_pending_operation_ids') IS DISTINCT FROM 'array'
        OR jsonb_array_length(cmd->'additional_pending_operation_ids') NOT BETWEEN 1 AND limit_count-1
        OR EXISTS(SELECT 1 FROM jsonb_array_elements(cmd->'additional_pending_operation_ids') v
            WHERE jsonb_typeof(v.value) IS DISTINCT FROM 'string' OR (v.value#>>'{}') !~ '^[A-Za-z0-9_.:-]{1,96}$') THEN RETURN false;END IF;
    targets:=life_financial.joint_repair_targets(cmd);
    IF EXISTS(SELECT 1 FROM jsonb_array_elements_text(targets) v GROUP BY v.value HAVING count(*)>1)
        OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(targets) v
            WHERE v.value=cmd->>'operation_id' OR v.value=cmd#>>'{replacement,operation_id}'
                OR EXISTS(SELECT 1 FROM jsonb_array_elements(life_financial.joint_batch_members(cmd->'replacement')) m
                    WHERE m#>>'{command,operation_id}'=v.value)) THEN RETURN false;END IF;
    FOR id IN SELECT value FROM jsonb_array_elements_text(targets) LOOP
        IF NOT EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=p_account AND operation_id=id AND command->>'kind'='PAID_FUNDING')
            OR NOT life_financial.funding_pending_unresolved(p_account,id,p_version,false) THEN RETURN false;END IF;
        SELECT * INTO STRICT raw FROM life_financial.joint_repair_origin(p_account,id);
        IF raw.state<>'RECOVERY_REQUIRED' OR raw.start_version>=p_version OR EXISTS(
            SELECT 1 FROM jsonb_array_elements(raw.command#>'{funding,bundle,funding,payments}') old
            WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(cmd#>'{replacement,funding,bundle,funding,payments}') fresh
                WHERE fresh->>'bill_id'=old->>'bill_id' AND (fresh->>'settled_at_ms')::bigint=(old->>'settled_at_ms')::bigint
                    AND (fresh->>'signed_payment_quote')::numeric=(old->>'signed_payment_quote')::numeric)) THEN RETURN false;END IF;
    END LOOP;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.joint_repair_cohort_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE expected jsonb;actual jsonb;p life_financial.joint_repair_policies;
BEGIN
    SELECT * INTO p FROM life_financial.joint_repair_policies WHERE account_id=p_account;
    IF NOT FOUND THEN RETURN NOT EXISTS(SELECT 1 FROM life_financial.joint_repair_members WHERE account_id=p_account);END IF;
    SELECT COALESCE(jsonb_agg(jsonb_build_array(r.operation_id,v.value) ORDER BY r.operation_id,v.value),'[]') INTO expected
        FROM life_financial.joint_repair_facts r JOIN life_financial.joint_repair_approvals g USING(account_id,operation_id)
        CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE(g.body#>'{payload,additional_pending_operation_ids}','[]'::jsonb)) v
        WHERE r.account_id=p_account;
    SELECT COALESCE(jsonb_agg(jsonb_build_array(operation_id,pending_operation_id) ORDER BY operation_id,pending_operation_id),'[]') INTO actual
        FROM life_financial.joint_repair_members WHERE account_id=p_account;
    RETURN expected=actual AND NOT EXISTS(SELECT 1 FROM (
        SELECT pending_operation_id FROM life_financial.joint_repair_facts WHERE account_id=p_account
        UNION ALL SELECT pending_operation_id FROM life_financial.joint_repair_members WHERE account_id=p_account) ids
        GROUP BY pending_operation_id HAVING count(*)>1)
        AND (SELECT COALESCE(sum(octet_length(to_jsonb(g)::text)),0) FROM life_financial.joint_repair_approvals g WHERE account_id=p_account)
            +(SELECT COALESCE(sum(octet_length(to_jsonb(r)::text)),0) FROM life_financial.joint_repair_facts r WHERE account_id=p_account)
            +(SELECT COALESCE(sum(octet_length(to_jsonb(m)::text)),0) FROM life_financial.joint_repair_members m WHERE account_id=p_account)<=p.max_total_bytes;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
CREATE FUNCTION life_financial.joint_repair_targets_available(p_account uuid,cmd jsonb) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT NOT EXISTS(SELECT 1 FROM (
    SELECT pending_operation_id FROM life_financial.joint_repair_facts WHERE account_id=p_account
    UNION ALL SELECT pending_operation_id FROM life_financial.joint_repair_members WHERE account_id=p_account) consumed
    WHERE life_financial.joint_repair_targets(cmd) @> jsonb_build_array(consumed.pending_operation_id));
$$;
CREATE FUNCTION life_financial.joint_repair_receipt_members(cmd jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
SELECT CASE WHEN cmd ? 'additional_pending_operation_ids' THEN jsonb_build_object(
    'additional_pending_operation_ids',cmd->'additional_pending_operation_ids') ELSE '{}'::jsonb END;
$$;

DO $$ DECLARE definition text;needle text;replacement text;BEGIN
    definition:=pg_get_functiondef('life_financial.joint_repair_approval_integrity(uuid)'::regprocedure);
    needle:='SELECT command INTO STRICT raw FROM life_financial.joint_repair_origin(p_account,r.pending_operation_id);';
    replacement:='SELECT life_financial.joint_repair_sources(p_account,b->''payload'') INTO STRICT raw;';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_COHORT_SOURCE_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);

    definition:=pg_get_functiondef('life_financial.joint_repair_verified(uuid)'::regprocedure);
    needle:='IF NOT life_financial.joint_repair_approval_integrity(p_account) THEN';
    replacement:='IF NOT life_financial.joint_repair_cohort_verified(p_account) OR NOT life_financial.joint_repair_approval_integrity(p_account) THEN';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_COHORT_PROOF_MIGRATION_MISMATCH';END IF;
    definition:=replace(definition,needle,replacement);
    needle:='OR o.receipt IS DISTINCT FROM jsonb_build_object(''kind'',''JOINT_REPAIR''';
    replacement:='OR o.receipt IS DISTINCT FROM (jsonb_build_object(''kind'',''JOINT_REPAIR''';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_COHORT_RECEIPT_MIGRATION_MISMATCH';END IF;
    definition:=replace(definition,needle,replacement);
    needle:='''version'',o.committed_version,''replayed'',false,''trading_permission'',false) THEN RETURN false;END IF;';
    replacement:='''version'',o.committed_version,''replayed'',false,''trading_permission'',false)||life_financial.joint_repair_receipt_members(g.body->''payload'')) THEN RETURN false;END IF;';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_COHORT_RECEIPT_END_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);

    definition:=pg_get_functiondef('life_financial.joint_repair_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'::regprocedure);
    needle:='INSERT INTO life_financial.joint_repair_staging VALUES(p_account,pg_current_xact_id(),cmd->''replacement'',cmd->>''operation_id'',cmd->>''pending_operation_id'');';
    replacement:='IF NOT life_financial.joint_repair_targets_available(p_account,cmd) THEN RAISE EXCEPTION ''LIFE_JOINT_REPAIR_APPROVAL_STALE'' USING ERRCODE=''P0001'';END IF;
    INSERT INTO life_financial.joint_repair_staging VALUES(p_account,pg_current_xact_id(),cmd->''replacement'',cmd->>''operation_id'',cmd->>''pending_operation_id'',COALESCE(cmd->''additional_pending_operation_ids'',''[]''::jsonb));';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_COHORT_CAPABILITY_MIGRATION_MISMATCH';END IF;
    definition:=replace(definition,needle,replacement);
    needle:='INSERT INTO life_financial.joint_repair_facts VALUES(p_account,cmd->>''operation_id'',cmd->>''pending_operation_id'',cmd#>>''{replacement,operation_id}'',floor(extract(epoch FROM at)*1000)::bigint);';
    replacement:=needle||'
    INSERT INTO life_financial.joint_repair_members SELECT p_account,cmd->>''operation_id'',value
        FROM jsonb_array_elements_text(COALESCE(cmd->''additional_pending_operation_ids'',''[]''::jsonb));
    IF NOT life_financial.joint_repair_cohort_verified(p_account) THEN RAISE EXCEPTION ''LIFE_JOINT_REPAIR_BOUND'' USING ERRCODE=''P0001'';END IF;';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_COHORT_MEMBERS_MIGRATION_MISMATCH';END IF;
    definition:=replace(definition,needle,replacement);
    needle:='''version'',v,''replayed'',false,''trading_permission'',false);';
    replacement:='''version'',v,''replayed'',false,''trading_permission'',false)||life_financial.joint_repair_receipt_members(cmd);';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_COHORT_COMMAND_RECEIPT_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;
CREATE CONSTRAINT TRIGGER joint_repair_member_commit AFTER INSERT ON life_financial.joint_repair_members
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.joint_repair_commit_guard();
REVOKE ALL ON life_financial.joint_repair_members FROM PUBLIC;
REVOKE ALL ON FUNCTION life_financial.joint_repair_targets(jsonb),life_financial.joint_repair_sources(uuid,jsonb),
    life_financial.joint_repair_profile_single(uuid,jsonb,jsonb,bigint),life_financial.joint_repair_cohort_verified(uuid),
    life_financial.joint_repair_targets_available(uuid,jsonb),life_financial.joint_repair_receipt_members(jsonb) FROM PUBLIC;
