-- Exact native SWAP fill observations can share a signed repair with retained
-- funding observations. No original financial fact, receipt or HALT is changed.
-- Only private monetary kernels see the selected current-transaction capability.
DO $$ DECLARE definition text;BEGIN
    definition:=pg_get_functiondef('life_financial.funding_pending_unresolved(uuid,text,bigint,boolean)'::regprocedure);
    EXECUTE replace(definition,'FUNCTION life_financial.funding_pending_unresolved(',
        'FUNCTION life_financial.joint_source_unresolved(');
END; $$;
CREATE OR REPLACE FUNCTION life_financial.funding_pending_unresolved(p_account uuid,p_operation text,
    p_version bigint DEFAULT NULL,p_staged boolean DEFAULT false) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT life_financial.joint_source_unresolved(p_account,p_operation,p_version,p_staged);
$$;

DO $$ DECLARE definition text;needle text;replacement text;BEGIN
    definition:=pg_get_functiondef('life_financial.joint_repair_origin(uuid,text)'::regprocedure);
    needle:='SELECT * INTO STRICT f FROM life_financial.funding_observations WHERE account_id=p_account AND operation_id=p_operation;';
    replacement:='IF o.command->>''kind''=''SWAP_FILL'' THEN
        IF o.receipt->>''state'' IS DISTINCT FROM ''RECOVERY_REQUIRED'' OR NOT life_financial.swap_accounting_verified(p_account)
            OR NOT EXISTS(SELECT 1 FROM life_financial.swap_pending_fills pending WHERE pending.account_id=p_account
                AND pending.operation_id=p_operation AND pending.command=o.command->''command'') THEN
            RAISE EXCEPTION ''LIFE_JOINT_REPAIR_ORIGIN_INVALID'' USING ERRCODE=''P0001'';END IF;
        result.account_id:=p_account;result.operation_id:=p_operation;result.state:=''RECOVERY_REQUIRED'';
        result.reason:=o.receipt->>''reason'';result.start_version:=o.committed_version-1;
        result.command:=jsonb_build_object(''operation_id'',p_operation,''spot_fills'',''[]''::jsonb,
            ''swap_fills'',jsonb_build_array(o.command->''command''),''cashflows'',''[]''::jsonb,
            ''funding'',jsonb_build_object(''operation_id'',p_operation,''bundle'',jsonb_build_object(''funding'',jsonb_build_object(''payments'',''[]''::jsonb))));
        RETURN result;
    END IF;
    '||needle;
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_SWAP_REPAIR_ORIGIN_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);

    -- Keep the established batch/funding profiles and old signed JSON intact.
    definition:=pg_get_functiondef('life_financial.joint_repair_profile(uuid,jsonb,jsonb,bigint)'::regprocedure);
    EXECUTE replace(definition,'FUNCTION life_financial.joint_repair_profile(',
        'FUNCTION life_financial.joint_repair_profile_before_swap(');
    -- Reuse the complete account, order, trade, bill and coverage proof after
    -- the new profile has compared every selected source without its op ID.
    definition:=pg_get_functiondef('life_financial.joint_repair_profile_single(uuid,jsonb,jsonb,bigint)'::regprocedure);
    definition:=replace(definition,'FUNCTION life_financial.joint_repair_profile_single(p_account uuid, cmd jsonb, facts jsonb, p_version bigint)',
        'FUNCTION life_financial.joint_repair_profile_sources(p_account uuid, cmd jsonb, facts jsonb, p_version bigint, p_sources jsonb)');
    needle:='SELECT * INTO STRICT raw FROM life_financial.joint_repair_origin(p_account,cmd->>''pending_operation_id'');';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_SWAP_REPAIR_COMMON_MISMATCH';END IF;
    EXECUTE replace(definition,needle,needle||' raw.command:=p_sources;');
END; $$;

CREATE OR REPLACE FUNCTION life_financial.joint_repair_profile(p_account uuid,cmd jsonb,facts jsonb,p_version bigint) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE id text;raw life_financial.joint_financial_batches;targets jsonb;limit_count integer;
    original_fills jsonb:='[]';original_payments jsonb:='[]';expected jsonb;actual jsonb;sources jsonb;
BEGIN
    targets:=life_financial.joint_repair_targets(cmd);
    IF NOT EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=p_account
        AND targets @> jsonb_build_array(o.operation_id) AND o.command->>'kind'='SWAP_FILL') THEN
        RETURN life_financial.joint_repair_profile_before_swap(p_account,cmd,facts,p_version);END IF;
    SELECT max_members INTO STRICT limit_count FROM life_financial.joint_batch_policies WHERE account_id=p_account;
    IF jsonb_typeof(cmd) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(cmd))<>
            (CASE WHEN cmd ? 'additional_pending_operation_ids' THEN 4 ELSE 3 END)
        OR cmd ? 'additional_pending_operation_ids' AND (jsonb_typeof(cmd->'additional_pending_operation_ids') IS DISTINCT FROM 'array'
            OR jsonb_array_length(cmd->'additional_pending_operation_ids')<1)
        OR jsonb_array_length(targets)>limit_count
        OR EXISTS(SELECT 1 FROM jsonb_array_elements(targets) v WHERE jsonb_typeof(v.value) IS DISTINCT FROM 'string'
            OR (v.value#>>'{}') !~ '^[A-Za-z0-9_.:-]{1,96}$')
        OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(targets) v GROUP BY v.value HAVING count(*)>1)
        OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(targets) v WHERE v.value=cmd->>'operation_id'
            OR v.value=cmd#>>'{replacement,operation_id}' OR EXISTS(
                SELECT 1 FROM jsonb_array_elements(life_financial.joint_batch_members(cmd->'replacement')) m WHERE m#>>'{command,operation_id}'=v.value)) THEN RETURN false;END IF;
    FOR id IN SELECT value FROM jsonb_array_elements_text(targets) LOOP
        IF NOT EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=p_account AND o.operation_id=id
            AND o.command->>'kind' IN ('PAID_FUNDING','SWAP_FILL'))
            OR NOT life_financial.joint_source_unresolved(p_account,id,p_version,false) THEN RETURN false;END IF;
        SELECT * INTO STRICT raw FROM life_financial.joint_repair_origin(p_account,id);
        IF raw.state<>'RECOVERY_REQUIRED' OR raw.start_version>=p_version THEN RETURN false;END IF;
        original_fills:=original_fills||(raw.command->'swap_fills');
        original_payments:=original_payments||(raw.command#>'{funding,bundle,funding,payments}');
    END LOOP;
    -- Duplicate retained observations of the same complete trade snapshot may
    -- share one application; no fill, fee, PnL, timestamp, wire or scope changes.
    SELECT COALESCE(jsonb_agg(v ORDER BY v::text),'[]') INTO expected FROM (
        SELECT DISTINCT value-'operation_id' v FROM jsonb_array_elements(original_fills)) x;
    SELECT COALESCE(jsonb_agg(value-'operation_id' ORDER BY (value-'operation_id')::text),'[]') INTO actual
        FROM jsonb_array_elements(cmd#>'{replacement,swap_fills}');
    IF expected IS DISTINCT FROM actual OR cmd#>'{replacement,spot_fills}' IS DISTINCT FROM '[]'::jsonb
        OR cmd#>'{replacement,cashflows}' IS DISTINCT FROM '[]'::jsonb THEN RETURN false;END IF;
    sources:=jsonb_build_object('operation_id',cmd->>'pending_operation_id','spot_fills','[]'::jsonb,
        'swap_fills',cmd#>'{replacement,swap_fills}','cashflows','[]'::jsonb,
        'funding',jsonb_build_object('operation_id',cmd->>'pending_operation_id',
            'bundle',jsonb_build_object('funding',jsonb_build_object('payments',original_payments))));
    RETURN life_financial.joint_repair_profile_sources(p_account,cmd-'additional_pending_operation_ids',facts,p_version,sources);
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

DO $$ DECLARE definition text;needle text;replacement text;signature text;tail text;suffix text;BEGIN
    FOREACH signature IN ARRAY ARRAY[
        'life_financial.joint_financial_pending(uuid)',
        'life_financial.swap_snapshot_current(uuid,jsonb)',
        'life_financial.spot_state_before_paid_funding(uuid)'] LOOP
        definition:=pg_get_functiondef(signature::regprocedure);
        needle:='FROM life_financial.swap_pending_fills WHERE account_id=p_account)';
        replacement:=left(needle,length(needle)-1)||' AND life_financial.joint_source_unresolved(p_account,operation_id,NULL,'
            ||CASE WHEN signature LIKE '%spot_state_before_paid_funding%' THEN 'false' ELSE 'true' END||'))';
        IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_SWAP_REPAIR_PENDING_MISMATCH %',signature;END IF;
        EXECUTE replace(definition,needle,replacement);
    END LOOP;
    FOREACH signature IN ARRAY ARRAY[
        'life_financial.joint_stage_swap(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
        'life_financial.swap_fill_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'] LOOP
        definition:=pg_get_functiondef(signature::regprocedure);
        needle:='FROM life_financial.swap_pending_fills WHERE account_id=p_account AND intent_id=t.intent_id)';
        replacement:=left(needle,length(needle)-1)||' AND life_financial.joint_source_unresolved(p_account,operation_id,NULL,'
            ||CASE WHEN signature LIKE '%joint_stage_swap%' THEN 'true' ELSE 'false' END||'))';
        IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_SWAP_REPAIR_WRITER_MISMATCH %',signature;END IF;
        EXECUTE replace(definition,needle,replacement);
    END LOOP;
    -- Terminal and release require every pending joint parent at THEIR cut
    -- to be resolved. A later parent must not invalidate an older terminal.
    FOREACH signature IN ARRAY ARRAY['life_financial.swap_terminal_reason(uuid,jsonb,bigint,bigint)',
        'life_financial.shared_release_proof(uuid,text,text,bigint,bigint)'] LOOP
        definition:=pg_get_functiondef(signature::regprocedure);
        needle:='FROM life_financial.swap_pending_fills f JOIN life_financial.operations ';
        tail:=substring(definition FROM position(needle IN definition));
        suffix:=CASE WHEN signature LIKE '%swap_terminal_reason%' THEN 'o.committed_version<=p_version)' ELSE 'op.committed_version<=p_version)' END;
        IF position(needle IN definition)=0 OR position(suffix IN tail)=0 THEN RAISE EXCEPTION 'LIFE_SWAP_REPAIR_HISTORY_MISMATCH %',signature;END IF;
        needle:=substring(tail FOR position(suffix IN tail)+length(suffix)-1);
        replacement:=left(needle,length(needle)-1)||' AND life_financial.joint_source_unresolved(p_account,f.operation_id,p_version,false))';
        IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_SWAP_REPAIR_VERSION_MISMATCH %',signature;END IF;
        definition:=replace(definition,needle,replacement);
        needle:='IF EXISTS(SELECT 1 FROM life_financial.spot_pending_observations f JOIN life_financial.operations ';
        replacement:='IF EXISTS(SELECT 1 FROM life_financial.joint_financial_batches pending_batch JOIN life_financial.operations pending_operation USING(account_id,operation_id)
            WHERE pending_batch.account_id=p_account AND pending_batch.state=''RECOVERY_REQUIRED'' AND pending_operation.committed_version<=p_version
                AND life_financial.joint_source_unresolved(p_account,pending_batch.operation_id,p_version,false))
            OR EXISTS(SELECT 1 FROM life_financial.spot_pending_observations f JOIN life_financial.operations ';
        IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_SWAP_REPAIR_JOINT_TERMINAL_MISMATCH %',signature;END IF;
        EXECUTE replace(definition,needle,replacement);
    END LOOP;
END; $$;
REVOKE ALL ON FUNCTION life_financial.joint_source_unresolved(uuid,text,bigint,boolean),
    life_financial.joint_repair_profile_before_swap(uuid,jsonb,jsonb,bigint),
    life_financial.joint_repair_profile_sources(uuid,jsonb,jsonb,bigint,jsonb) FROM PUBLIC;
