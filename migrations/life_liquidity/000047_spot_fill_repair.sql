-- Exact direct SPOT snapshots join the existing signed financial recovery.
-- Retain original pending rows, receipts, fees, holds, subsidy and HALT.
CREATE FUNCTION life_financial.spot_pending_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE pending life_financial.spot_pending_observations;o life_financial.operations;prior_loss numeric;
BEGIN
    FOR pending IN SELECT * FROM life_financial.spot_pending_observations WHERE account_id=p_account LOOP
        SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=pending.operation_id;
        SELECT COALESCE(sum(f.execution_loss),0) INTO prior_loss FROM life_financial.spot_fill_facts f
            JOIN life_financial.operations applied ON applied.account_id=f.account_id AND applied.operation_id=f.source_operation_id
            WHERE f.account_id=p_account AND f.intent_id=pending.intent_id AND applied.committed_version<o.committed_version;
        IF o.command->>'kind' IS DISTINCT FROM 'SPOT_FILL' OR o.command->'command' IS DISTINCT FROM pending.command
            OR pending.intent_id IS DISTINCT FROM pending.command->>'intent_id'
            OR pending.exchange_order_id IS DISTINCT FROM pending.command->>'exchange_order_id'
            OR pending.operation_id IS DISTINCT FROM pending.command->>'operation_id'
            OR pending.source_hash IS DISTINCT FROM encode(sha256(convert_to(pending.command::text,'UTF8')),'hex')
            OR o.fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(o.command),'UTF8')),'hex')
            OR o.receipt IS DISTINCT FROM jsonb_build_object('kind','SPOT_FILL','operation_id',pending.operation_id,
                'intent_id',pending.intent_id,'state','RECOVERY_REQUIRED',
                'cumulative_base_life',((pending.command->>'cumulative_base_life')::numeric)::text,
                'execution_loss_usdt',prior_loss::text,'version',o.committed_version,'replayed',false,'trading_permission',false) THEN RETURN false;END IF;
    END LOOP;
    RETURN NOT EXISTS(SELECT 1 FROM life_financial.operations op WHERE op.account_id=p_account AND op.command->>'kind'='SPOT_FILL'
        AND op.receipt->>'state'='RECOVERY_REQUIRED' AND NOT EXISTS(SELECT 1 FROM life_financial.spot_pending_observations p
            WHERE p.account_id=p_account AND p.operation_id=op.operation_id));
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
DO $$ DECLARE definition text;needle text;replacement text;BEGIN
    definition:=pg_get_functiondef('life_financial.spot_accounting_verified(uuid)'::regprocedure);
    EXECUTE replace(definition,'FUNCTION life_financial.spot_accounting_verified(',
        'FUNCTION life_financial.spot_accounting_verified_before_repair(');
    definition:=pg_get_functiondef('life_financial.joint_repair_origin(uuid,text)'::regprocedure);
    needle:='SELECT * INTO STRICT f FROM life_financial.funding_observations WHERE account_id=p_account AND operation_id=p_operation;';
    replacement:='IF o.command->>''kind''=''SPOT_FILL'' THEN
        IF o.receipt->>''state'' IS DISTINCT FROM ''RECOVERY_REQUIRED'' OR NOT life_financial.spot_accounting_verified(p_account)
            OR NOT EXISTS(SELECT 1 FROM life_financial.spot_pending_observations pending WHERE pending.account_id=p_account
                AND pending.operation_id=p_operation AND pending.command=o.command->''command'') THEN
            RAISE EXCEPTION ''LIFE_JOINT_REPAIR_ORIGIN_INVALID'' USING ERRCODE=''P0001'';END IF;
        result.account_id:=p_account;result.operation_id:=p_operation;result.state:=''RECOVERY_REQUIRED'';
        result.reason:=o.receipt->>''reason'';result.start_version:=o.committed_version-1;
        result.command:=jsonb_build_object(''operation_id'',p_operation,''spot_fills'',jsonb_build_array(o.command->''command''),
            ''swap_fills'',''[]''::jsonb,''cashflows'',''[]''::jsonb,
            ''funding'',jsonb_build_object(''operation_id'',p_operation,''bundle'',jsonb_build_object(''funding'',jsonb_build_object(''payments'',''[]''::jsonb))));
        RETURN result;
    END IF;
    '||needle;
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_SPOT_REPAIR_ORIGIN_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;
CREATE OR REPLACE FUNCTION life_financial.spot_accounting_verified(p_account uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT life_financial.spot_accounting_verified_before_repair(p_account) AND life_financial.spot_pending_verified(p_account);
$$;

-- Extend the exact multi-product profile, preserving old funding/joint/SWAP
-- signed shapes and source hashes. A financial snapshot differs only in its
-- new application operation ID; neither product can hide the other's fills.
DO $$ DECLARE definition text;needle text;replacement text;BEGIN
    definition:=pg_get_functiondef('life_financial.joint_repair_profile(uuid,jsonb,jsonb,bigint)'::regprocedure);
    needle:='original_fills jsonb:=''[]'';';replacement:='original_spot jsonb:=''[]'';original_fills jsonb:=''[]'';';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_SPOT_REPAIR_VARIABLE_MISMATCH';END IF;
    definition:=replace(definition,needle,replacement);
    needle:='o.command->>''kind''=''SWAP_FILL''';replacement:='o.command->>''kind'' IN (''SPOT_FILL'',''SWAP_FILL'')';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_SPOT_REPAIR_SELECTION_MISMATCH';END IF;
    definition:=replace(definition,needle,replacement);
    needle:='IN (''PAID_FUNDING'',''SWAP_FILL'')';replacement:='IN (''PAID_FUNDING'',''SWAP_FILL'',''SPOT_FILL'')';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_SPOT_REPAIR_KIND_MISMATCH';END IF;
    definition:=replace(definition,needle,replacement);
    needle:='original_fills:=original_fills||(raw.command->''swap_fills'');';
    replacement:=needle||' original_spot:=original_spot||(raw.command->''spot_fills'');';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_SPOT_REPAIR_SOURCE_MISMATCH';END IF;
    definition:=replace(definition,needle,replacement);
    needle:='IF expected IS DISTINCT FROM actual OR cmd#>''{replacement,spot_fills}'' IS DISTINCT FROM ''[]''::jsonb';
    replacement:='IF expected IS DISTINCT FROM actual THEN RETURN false;END IF;
    SELECT COALESCE(jsonb_agg(v ORDER BY v::text),''[]'') INTO expected FROM (
        SELECT DISTINCT value-''operation_id'' v FROM jsonb_array_elements(original_spot)) x;
    SELECT COALESCE(jsonb_agg(value-''operation_id'' ORDER BY (value-''operation_id'')::text),''[]'') INTO actual
        FROM jsonb_array_elements(cmd#>''{replacement,spot_fills}'');
    IF expected IS DISTINCT FROM actual';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_SPOT_REPAIR_EXACT_MISMATCH';END IF;
    definition:=replace(definition,needle,replacement);
    needle:='''spot_fills'',''[]''::jsonb,';replacement:='''spot_fills'',cmd#>''{replacement,spot_fills}'',';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_SPOT_REPAIR_COMMON_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;

DO $$ DECLARE definition text;needle text;replacement text;signature text;tail text;suffix text;BEGIN
    FOREACH signature IN ARRAY ARRAY[
        'life_financial.joint_financial_pending(uuid)',
        'life_financial.spot_state_v7(uuid)',
        'life_financial.spot_observation_context(uuid,integer,integer)',
        'life_financial.spot_order_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb,boolean)',
        'life_financial.spot_settle_inventory(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb,jsonb,boolean)'] LOOP
        definition:=pg_get_functiondef(signature::regprocedure);
        needle:='FROM life_financial.spot_pending_observations WHERE account_id=p_account)';
        replacement:=left(needle,length(needle)-1)||' AND life_financial.joint_source_unresolved(p_account,operation_id,NULL,'
            ||CASE WHEN signature LIKE '%joint_financial_pending%' THEN 'true' ELSE 'false' END||'))';
        IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_SPOT_REPAIR_PENDING_MISMATCH %',signature;END IF;
        definition:=replace(definition,needle,replacement);
        IF signature LIKE '%spot_order_command%' THEN
            -- Apply the account-wide barrier only to terminal qualification;
            -- cancellation/observation must remain possible while HALTed.
            needle:='OR EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts WHERE account_id=p_account AND state<>''APPLIED'')';
            replacement:=needle||' OR life_financial.joint_financial_pending(p_account)';
            IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_SPOT_REPAIR_TERMINAL_MISMATCH';END IF;
            definition:=replace(definition,needle,replacement);
        END IF;
        EXECUTE definition;
    END LOOP;
    FOREACH signature IN ARRAY ARRAY[
        'life_financial.joint_stage_spot(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)',
        'life_financial.spot_observe(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)'] LOOP
        definition:=pg_get_functiondef(signature::regprocedure);
        needle:='FROM life_financial.spot_pending_observations WHERE account_id=p_account AND intent_id=t.intent_id)';
        replacement:=left(needle,length(needle)-1)||' AND life_financial.joint_source_unresolved(p_account,operation_id,NULL,'
            ||CASE WHEN signature LIKE '%joint_stage_spot%' THEN 'true' ELSE 'false' END||'))';
        IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_SPOT_REPAIR_WRITER_MISMATCH %',signature;END IF;
        EXECUTE replace(definition,needle,replacement);
    END LOOP;
    FOREACH signature IN ARRAY ARRAY['life_financial.swap_terminal_reason(uuid,jsonb,bigint,bigint)',
        'life_financial.shared_release_proof(uuid,text,text,bigint,bigint)'] LOOP
        definition:=pg_get_functiondef(signature::regprocedure);
        needle:='FROM life_financial.spot_pending_observations f JOIN life_financial.operations ';
        tail:=substring(definition FROM position(needle IN definition));
        suffix:=CASE WHEN signature LIKE '%swap_terminal_reason%' THEN 'o.committed_version<=p_version)' ELSE 'op.committed_version<=p_version)' END;
        IF position(needle IN definition)=0 OR position(suffix IN tail)=0 THEN RAISE EXCEPTION 'LIFE_SPOT_REPAIR_HISTORY_MISMATCH %',signature;END IF;
        needle:=substring(tail FOR position(suffix IN tail)+length(suffix)-1);
        replacement:=left(needle,length(needle)-1)||' AND life_financial.joint_source_unresolved(p_account,f.operation_id,p_version,false))';
        IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_SPOT_REPAIR_VERSION_MISMATCH %',signature;END IF;
        EXECUTE replace(definition,needle,replacement);
    END LOOP;
END; $$;
REVOKE ALL ON FUNCTION life_financial.spot_pending_verified(uuid),life_financial.spot_accounting_verified_before_repair(uuid) FROM PUBLIC;
