-- Extend signed source repair to retained standalone funding observations.
-- The original operation, receipt, bill identities/amounts/times and HALT stay
-- immutable. This is a normalization for the existing verifier, NOT a synthetic
-- pending batch or a grant to bypass unrelated pending financial facts.
ALTER TABLE life_financial.joint_repair_approvals
    DROP CONSTRAINT joint_repair_approvals_account_id_pending_operation_id_fkey,
    ADD FOREIGN KEY(account_id,pending_operation_id) REFERENCES life_financial.operations;
ALTER TABLE life_financial.joint_repair_facts
    DROP CONSTRAINT joint_repair_facts_account_id_pending_operation_id_fkey,
    ADD FOREIGN KEY(account_id,pending_operation_id) REFERENCES life_financial.operations;
-- Bind the private capability to ONE approval/parent, not merely replacement
-- JSON that another approved command can also contain. Deferred commit guards
-- already require this private staging table to be empty at every commit.
ALTER TABLE life_financial.joint_repair_staging
    ADD COLUMN repair_operation_id life_financial.identity NOT NULL,
    ADD COLUMN pending_operation_id life_financial.identity NOT NULL,
    ADD FOREIGN KEY(account_id,repair_operation_id) REFERENCES life_financial.joint_repair_approvals,
    ADD FOREIGN KEY(account_id,pending_operation_id) REFERENCES life_financial.operations;

CREATE FUNCTION life_financial.joint_repair_origin(p_account uuid,p_operation text)
RETURNS life_financial.joint_financial_batches
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result life_financial.joint_financial_batches;o life_financial.operations;f life_financial.funding_observations;
BEGIN
    SELECT * INTO result FROM life_financial.joint_financial_batches WHERE account_id=p_account AND operation_id=p_operation;
    IF FOUND THEN RETURN result;END IF;
    SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=p_operation;
    SELECT * INTO STRICT f FROM life_financial.funding_observations WHERE account_id=p_account AND operation_id=p_operation;
    IF o.command->>'kind' IS DISTINCT FROM 'PAID_FUNDING' OR f.state<>'RECOVERY_REQUIRED'
        OR o.receipt->>'state' IS DISTINCT FROM f.state OR NOT life_financial.funding_verified(p_account) THEN
        RAISE EXCEPTION 'LIFE_JOINT_REPAIR_ORIGIN_INVALID' USING ERRCODE='P0001';END IF;
    result.account_id:=p_account;result.operation_id:=p_operation;result.state:=f.state;
    result.reason:=f.reason;result.start_version:=o.committed_version-1;
    result.command:=jsonb_build_object('operation_id',p_operation,'spot_fills','[]'::jsonb,
        'swap_fills','[]'::jsonb,'cashflows','[]'::jsonb,'funding',o.command->'command');
    RETURN result;
END; $$;

-- Historical gates must use the repair's committed version, never current
-- resolution state, when verifying an older terminal/release observation.
-- Staging is considered ONLY by the private joint monetary kernels. Snapshot
-- cuts retain pending until the signed root and repair fact actually commit.
CREATE FUNCTION life_financial.funding_pending_unresolved(p_account uuid,p_operation text,
    p_version bigint DEFAULT NULL,p_staged boolean DEFAULT false) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT NOT EXISTS(SELECT 1 FROM life_financial.joint_repair_facts r
    JOIN life_financial.operations o USING(account_id,operation_id)
    WHERE r.account_id=p_account AND r.pending_operation_id=p_operation
        AND o.command->>'kind'='JOINT_REPAIR' AND o.receipt->>'state'='REPAIRED'
        AND (p_version IS NULL OR o.committed_version<=p_version))
    AND NOT (p_staged AND EXISTS(SELECT 1 FROM life_financial.joint_repair_staging s
        JOIN life_financial.joint_repair_approvals g ON g.account_id=s.account_id
            AND g.operation_id=s.repair_operation_id AND g.pending_operation_id=s.pending_operation_id
            AND g.body#>'{payload,replacement}'=s.command
        WHERE s.account_id=p_account AND s.transaction_id=pg_current_xact_id()
            AND g.pending_operation_id=p_operation));
$$;

-- Checked replacements preserve every existing public OID/ACL and deferred
-- commit fence. Earlier numbered scripts/checksums are unchanged.
DO $$ DECLARE definition text;needle text;replacement text;signature text;tail text;suffix text;BEGIN
    definition:=pg_get_functiondef('life_financial.joint_repair_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'::regprocedure);
    needle:='INSERT INTO life_financial.joint_repair_staging VALUES(p_account,pg_current_xact_id(),cmd->''replacement'');';
    replacement:='INSERT INTO life_financial.joint_repair_staging VALUES(p_account,pg_current_xact_id(),cmd->''replacement'',cmd->>''operation_id'',cmd->>''pending_operation_id'');';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_DIRECT_FUNDING_CAPABILITY_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);

    definition:=pg_get_functiondef('life_financial.joint_repair_profile(uuid,jsonb,jsonb,bigint)'::regprocedure);
    needle:='SELECT * INTO STRICT raw FROM life_financial.joint_financial_batches WHERE account_id=p_account AND operation_id=cmd->>''pending_operation_id'';';
    replacement:='SELECT * INTO STRICT raw FROM life_financial.joint_repair_origin(p_account,cmd->>''pending_operation_id'');';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_DIRECT_FUNDING_PROFILE_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);

    definition:=pg_get_functiondef('life_financial.joint_repair_approval_integrity(uuid)'::regprocedure);
    needle:='SELECT command INTO STRICT raw FROM life_financial.joint_financial_batches WHERE account_id=p_account AND operation_id=r.pending_operation_id;';
    replacement:='SELECT command INTO STRICT raw FROM life_financial.joint_repair_origin(p_account,r.pending_operation_id);';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_DIRECT_FUNDING_APPROVAL_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);

    -- Keep raw pending rows in readback; change only the unresolved predicates.
    FOREACH signature IN ARRAY ARRAY[
        'life_financial.joint_financial_pending(uuid)',
        'life_financial.joint_stage_funding(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
        'life_financial.funding_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
        'life_financial.funding_carry_current(uuid,jsonb)',
        'life_financial.spot_state_before_portfolio(uuid)'] LOOP
        definition:=pg_get_functiondef(signature::regprocedure);
        needle:='FROM life_financial.funding_observations WHERE account_id=p_account AND state=''RECOVERY_REQUIRED''';
        replacement:=needle||' AND life_financial.funding_pending_unresolved(p_account,operation_id,NULL,'
            ||CASE WHEN signature LIKE '%joint_stage_funding%' OR signature LIKE '%joint_financial_pending%' THEN 'true' ELSE 'false' END||')';
        IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_DIRECT_FUNDING_PENDING_MISMATCH %',signature;END IF;
        EXECUTE replace(definition,needle,replacement);
    END LOOP;

    FOREACH signature IN ARRAY ARRAY['life_financial.swap_terminal_reason(uuid,jsonb,bigint,bigint)',
        'life_financial.shared_release_proof(uuid,text,text,bigint,bigint)'] LOOP
        definition:=pg_get_functiondef(signature::regprocedure);
        needle:='FROM life_financial.funding_observations f JOIN life_financial.operations ';
        -- Restrict the replacement to the funding clause, not the other three
        -- financial pending families with the same historical version guard.
        IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_DIRECT_FUNDING_HISTORY_MISMATCH %',signature;END IF;
        tail:=substring(definition FROM position(needle IN definition));
        suffix:=CASE WHEN signature LIKE '%swap_terminal_reason%' THEN
            'f.state=''RECOVERY_REQUIRED'' AND o.committed_version<=p_version)' ELSE
            'f.state=''RECOVERY_REQUIRED'' AND op.committed_version<=p_version)' END;
        IF position(suffix IN tail)=0 THEN RAISE EXCEPTION 'LIFE_DIRECT_FUNDING_VERSION_MISMATCH %',signature;END IF;
        needle:=substring(tail FOR position(suffix IN tail)+length(suffix)-1);
        replacement:=left(needle,length(needle)-1)||' AND life_financial.funding_pending_unresolved(p_account,f.operation_id,p_version,false))';
        IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_DIRECT_FUNDING_CLAUSE_MISMATCH %',signature;END IF;
        EXECUTE replace(definition,needle,replacement);
    END LOOP;

    definition:=pg_get_functiondef('life_financial.joint_repair_read(uuid,integer,integer,text)'::regprocedure);
    needle:='''pending'',life_financial.joint_unresolved_pending(p_account)';
    replacement:='''pending'',life_financial.joint_financial_pending(p_account)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_DIRECT_FUNDING_READ_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;
REVOKE ALL ON FUNCTION life_financial.joint_repair_origin(uuid,text),
    life_financial.funding_pending_unresolved(uuid,text,bigint,boolean) FROM PUBLIC;
