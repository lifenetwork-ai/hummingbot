-- Preserve raw pending external bills; publish one immutable application per
-- bill only inside the existing exact signed account-atomic repair capability.
CREATE TABLE life_financial.spot_cashflow_applications (
    account_id uuid NOT NULL,bill_id life_financial.identity NOT NULL,
    pending_operation_id life_financial.identity NOT NULL,
    repair_operation_id life_financial.identity NOT NULL,
    command jsonb NOT NULL CHECK(jsonb_typeof(command)='object'),
    source_operation_id life_financial.identity NOT NULL,source_hash life_financial.digest NOT NULL,
    PRIMARY KEY(account_id,bill_id),UNIQUE(account_id,source_operation_id),
    FOREIGN KEY(account_id,bill_id) REFERENCES life_financial.spot_cashflow_facts,
    FOREIGN KEY(account_id,pending_operation_id) REFERENCES life_financial.operations,
    FOREIGN KEY(account_id,repair_operation_id) REFERENCES life_financial.joint_repair_approvals,
    FOREIGN KEY(account_id,source_operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED
);
ALTER TABLE life_financial.spot_cashflow_applications ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped_read ON life_financial.spot_cashflow_applications FOR SELECT USING(life_financial.can_access(account_id));
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.spot_cashflow_applications
    FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record();

-- Read projection only: original raw diagnostics remain in their own table.
-- No pending fact is represented as booked before an application exists.
CREATE FUNCTION life_financial.cashflow_effective(p_account uuid) RETURNS SETOF life_financial.spot_cashflow_facts
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT f.* FROM life_financial.spot_cashflow_facts f WHERE f.account_id=p_account AND NOT EXISTS(
    SELECT 1 FROM life_financial.spot_cashflow_applications r WHERE r.account_id=f.account_id AND r.bill_id=f.bill_id)
UNION ALL
SELECT account_id,bill_id,command,'APPLIED'::text,NULL::life_financial.identity,source_operation_id,source_hash
    FROM life_financial.spot_cashflow_applications WHERE account_id=p_account;
$$;

CREATE FUNCTION life_financial.cashflow_history_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE f life_financial.spot_cashflow_facts;r life_financial.spot_cashflow_applications;
    o life_financial.operations;b life_financial.bill_approvals;g life_financial.joint_repair_approvals;
BEGIN
    FOR f IN SELECT * FROM life_financial.spot_cashflow_facts WHERE account_id=p_account LOOP
        SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=f.source_operation_id;
        SELECT * INTO STRICT b FROM life_financial.bill_approvals WHERE account_id=p_account AND bill_id=f.bill_id;
        IF o.command->>'kind' IS DISTINCT FROM 'SPOT_CASHFLOW' OR o.command->'command' IS DISTINCT FROM f.command
            OR f.command->>'bill_id' IS DISTINCT FROM f.bill_id OR f.command->>'operation_id' IS DISTINCT FROM f.source_operation_id
            OR f.source_hash IS DISTINCT FROM o.fingerprint
            OR o.fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(o.command),'UTF8')),'hex')
            OR b.asset IS DISTINCT FROM f.command->>'currency' OR b.signed_amount IS DISTINCT FROM (f.command->>'signed_amount')::numeric
            OR floor(extract(epoch FROM b.transfer_at)*1000) IS DISTINCT FROM (f.command->>'transfer_at_ms')::bigint OR octet_length(b.signature)=0
            OR o.receipt IS DISTINCT FROM jsonb_build_object('kind','SPOT_CASHFLOW','operation_id',f.source_operation_id,
                'bill_id',f.bill_id,'state',f.state,'version',o.committed_version,'replayed',false,'trading_permission',false)
            OR f.state='RECOVERY_REQUIRED' AND f.reason NOT IN ('LIFE_CAPITAL_CAUSAL_INVALID','LIFE_CAPITAL_REPLAY_BOUND') THEN RETURN false;END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=p_account AND o.command->>'kind'='SPOT_CASHFLOW'
        AND o.receipt->>'state'='RECOVERY_REQUIRED' AND NOT EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts f
            WHERE f.account_id=p_account AND f.bill_id=o.command#>>'{command,bill_id}' AND f.state='RECOVERY_REQUIRED'
                AND f.command-ARRAY['operation_id','observed_at']=(o.command->'command')-ARRAY['operation_id','observed_at'])) THEN RETURN false;END IF;
    FOR r IN SELECT * FROM life_financial.spot_cashflow_applications WHERE account_id=p_account LOOP
        SELECT * INTO STRICT f FROM life_financial.spot_cashflow_facts WHERE account_id=p_account AND bill_id=r.bill_id;
        SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=r.source_operation_id;
        SELECT * INTO STRICT g FROM life_financial.joint_repair_approvals WHERE account_id=p_account AND operation_id=r.repair_operation_id;
        IF f.state<>'RECOVERY_REQUIRED' OR f.source_operation_id<>r.pending_operation_id
            OR r.command-'operation_id' IS DISTINCT FROM f.command-'operation_id'
            OR r.source_operation_id IS DISTINCT FROM r.command->>'operation_id'
            OR o.command->>'kind' IS DISTINCT FROM 'SPOT_CASHFLOW' OR o.command->'command' IS DISTINCT FROM r.command
            OR o.command->'binding' IS DISTINCT FROM g.body#>'{approval,binding}'
            OR r.source_hash IS DISTINCT FROM o.fingerprint
            OR o.fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(o.command),'UTF8')),'hex')
            OR o.receipt IS DISTINCT FROM jsonb_build_object('kind','SPOT_CASHFLOW','operation_id',r.source_operation_id,
                'bill_id',r.bill_id,'state','APPLIED','version',o.committed_version,'replayed',false,'trading_permission',false)
            OR NOT life_financial.joint_repair_targets(g.body->'payload') @> jsonb_build_array(r.pending_operation_id)
            OR NOT g.body#>'{payload,replacement,cashflows}' @> jsonb_build_array(r.command)
            OR life_financial.joint_source_unresolved(p_account,r.pending_operation_id,NULL,true)
            OR NOT (EXISTS(SELECT 1 FROM life_financial.joint_repair_facts repaired JOIN life_financial.operations root USING(account_id,operation_id)
                WHERE repaired.account_id=p_account AND repaired.operation_id=r.repair_operation_id
                    AND repaired.replacement_operation_id=g.body#>>'{payload,replacement,operation_id}'
                    AND root.command->>'kind'='JOINT_REPAIR' AND root.receipt->>'state'='REPAIRED'
                    AND root.command->'owner'=o.command->'owner' AND root.committed_version>o.committed_version)
                OR EXISTS(SELECT 1 FROM life_financial.joint_repair_staging staged WHERE staged.account_id=p_account
                    AND staged.transaction_id=pg_current_xact_id() AND staged.repair_operation_id=r.repair_operation_id
                    AND staged.command=g.body#>'{payload,replacement}'
                    AND staged.pending_operation_id=g.pending_operation_id
                    AND staged.additional_pending_operation_ids=COALESCE(g.body#>'{payload,additional_pending_operation_ids}','[]'::jsonb))) THEN RETURN false;END IF;
    END LOOP;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

DO $$ DECLARE definition text;needle text;replacement text;BEGIN
    definition:=pg_get_functiondef('life_financial.joint_repair_origin(uuid,text)'::regprocedure);
    needle:='SELECT * INTO STRICT f FROM life_financial.funding_observations WHERE account_id=p_account AND operation_id=p_operation;';
    replacement:='IF o.command->>''kind''=''SPOT_CASHFLOW'' THEN
        IF o.receipt->>''state'' IS DISTINCT FROM ''RECOVERY_REQUIRED'' OR NOT life_financial.cashflow_history_verified(p_account)
            OR NOT EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts pending WHERE pending.account_id=p_account
                AND pending.source_operation_id=p_operation AND pending.state=''RECOVERY_REQUIRED'' AND pending.command=o.command->''command'') THEN
            RAISE EXCEPTION ''LIFE_JOINT_REPAIR_ORIGIN_INVALID'' USING ERRCODE=''P0001'';END IF;
        result.account_id:=p_account;result.operation_id:=p_operation;result.state:=''RECOVERY_REQUIRED'';
        result.reason:=o.receipt->>''reason'';result.start_version:=o.committed_version-1;
        result.command:=jsonb_build_object(''operation_id'',p_operation,''spot_fills'',''[]''::jsonb,
            ''swap_fills'',''[]''::jsonb,''cashflows'',jsonb_build_array(o.command->''command''),
            ''funding'',jsonb_build_object(''operation_id'',p_operation,''bundle'',jsonb_build_object(''funding'',jsonb_build_object(''payments'',''[]''::jsonb))));
        RETURN result;
    END IF;
    '||needle;
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_CASHFLOW_ORIGIN_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;


DO $$ DECLARE definition text;needle text;replacement text;BEGIN
    definition:=pg_get_functiondef('life_financial.spot_capital_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)'::regprocedure);
    needle:='WHERE account_id=p_account AND asset=b.asset;
            IF NOT life_financial.spot_capital_verified(p_account) THEN RAISE EXCEPTION ''LIFE_CAPITAL_CAUSAL_INVALID'' USING ERRCODE=''P0001''; END IF;';
    replacement:='WHERE account_id=p_account AND asset=b.asset;
            IF (r->>''base'')::numeric IS DISTINCT FROM (SELECT total FROM life_financial.balances WHERE account_id=p_account AND asset=''LIFE'')
                OR (r->>''quote'')::numeric IS DISTINCT FROM (SELECT total FROM life_financial.balances WHERE account_id=p_account AND asset=''USDT'') THEN
                RAISE EXCEPTION ''LIFE_CAPITAL_CAUSAL_INVALID'' USING ERRCODE=''P0001'';END IF;';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_CASHFLOW_INTERMEDIATE_PROOF_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;

-- Checked read/proof changes; monetary writers still target original tables.
DO $$ DECLARE definition text;BEGIN
    definition:=pg_get_functiondef('life_financial.spot_order_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb,boolean)'::regprocedure);
    IF (length(definition)-length(replace(definition,'FROM life_financial.spot_cashflow_facts','')))/length('FROM life_financial.spot_cashflow_facts')<>1 THEN RAISE EXCEPTION 'LIFE_CASHFLOW_MIGRATION_MISMATCH %','life_financial.spot_order_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb,boolean)';END IF;
    EXECUTE replace(definition,'FROM life_financial.spot_cashflow_facts','FROM life_financial.cashflow_effective(p_account)');
    definition:=pg_get_functiondef('life_financial.spot_state_v7(uuid)'::regprocedure);
    IF (length(definition)-length(replace(definition,'FROM life_financial.spot_cashflow_facts','')))/length('FROM life_financial.spot_cashflow_facts')<>1 THEN RAISE EXCEPTION 'LIFE_CASHFLOW_MIGRATION_MISMATCH %','life_financial.spot_state_v7(uuid)';END IF;
    EXECUTE replace(definition,'FROM life_financial.spot_cashflow_facts','FROM life_financial.cashflow_effective(p_account)');
    definition:=pg_get_functiondef('life_financial.spot_capital_digest(uuid)'::regprocedure);
    IF (length(definition)-length(replace(definition,'FROM life_financial.spot_cashflow_facts','')))/length('FROM life_financial.spot_cashflow_facts')<>2 THEN RAISE EXCEPTION 'LIFE_CASHFLOW_MIGRATION_MISMATCH %','life_financial.spot_capital_digest(uuid)';END IF;
    EXECUTE replace(definition,'FROM life_financial.spot_cashflow_facts','FROM life_financial.cashflow_effective(p_account)');
    definition:=pg_get_functiondef('life_financial.spot_observation_context(uuid,integer,integer)'::regprocedure);
    IF (length(definition)-length(replace(definition,'FROM life_financial.spot_cashflow_facts','')))/length('FROM life_financial.spot_cashflow_facts')<>3 THEN RAISE EXCEPTION 'LIFE_CASHFLOW_MIGRATION_MISMATCH %','life_financial.spot_observation_context(uuid,integer,integer)';END IF;
    EXECUTE replace(definition,'FROM life_financial.spot_cashflow_facts','FROM life_financial.cashflow_effective(p_account)');
    definition:=pg_get_functiondef('life_financial.spot_settle_inventory(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb,jsonb,boolean)'::regprocedure);
    IF (length(definition)-length(replace(definition,'FROM life_financial.spot_cashflow_facts','')))/length('FROM life_financial.spot_cashflow_facts')<>1 THEN RAISE EXCEPTION 'LIFE_CASHFLOW_MIGRATION_MISMATCH %','life_financial.spot_settle_inventory(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb,jsonb,boolean)';END IF;
    EXECUTE replace(definition,'FROM life_financial.spot_cashflow_facts','FROM life_financial.cashflow_effective(p_account)');
    definition:=pg_get_functiondef('life_financial.spot_financial_ingress_matches(uuid,jsonb,text)'::regprocedure);
    IF (length(definition)-length(replace(definition,'FROM life_financial.spot_cashflow_facts','')))/length('FROM life_financial.spot_cashflow_facts')<>1 THEN RAISE EXCEPTION 'LIFE_CASHFLOW_MIGRATION_MISMATCH %','life_financial.spot_financial_ingress_matches(uuid,jsonb,text)';END IF;
    EXECUTE replace(definition,'FROM life_financial.spot_cashflow_facts','FROM life_financial.cashflow_effective(p_account)');
    definition:=pg_get_functiondef('life_financial.portfolio_money_at(uuid,bigint)'::regprocedure);
    IF (length(definition)-length(replace(definition,'FROM life_financial.spot_cashflow_facts','')))/length('FROM life_financial.spot_cashflow_facts')<>2 THEN RAISE EXCEPTION 'LIFE_CASHFLOW_MIGRATION_MISMATCH %','life_financial.portfolio_money_at(uuid,bigint)';END IF;
    EXECUTE replace(definition,'FROM life_financial.spot_cashflow_facts','FROM life_financial.cashflow_effective(p_account)');
    definition:=pg_get_functiondef('life_financial.spot_capital_replay(uuid)'::regprocedure);
    IF (length(definition)-length(replace(definition,'FROM life_financial.spot_cashflow_facts','')))/length('FROM life_financial.spot_cashflow_facts')<>2 THEN RAISE EXCEPTION 'LIFE_CASHFLOW_MIGRATION_MISMATCH %','life_financial.spot_capital_replay(uuid)';END IF;
    EXECUTE replace(definition,'FROM life_financial.spot_cashflow_facts','FROM life_financial.cashflow_effective(p_account)');
    definition:=pg_get_functiondef('life_financial.portfolio_basis(uuid,bigint)'::regprocedure);
    IF (length(definition)-length(replace(definition,'FROM life_financial.spot_cashflow_facts','')))/length('FROM life_financial.spot_cashflow_facts')<>1 THEN RAISE EXCEPTION 'LIFE_CASHFLOW_MIGRATION_MISMATCH %','life_financial.portfolio_basis(uuid,bigint)';END IF;
    EXECUTE replace(definition,'FROM life_financial.spot_cashflow_facts','FROM life_financial.cashflow_effective(p_account)');
    definition:=pg_get_functiondef('life_financial.joint_repair_profile_single(uuid,jsonb,jsonb,bigint)'::regprocedure);
    IF (length(definition)-length(replace(definition,'FROM life_financial.spot_cashflow_facts','')))/length('FROM life_financial.spot_cashflow_facts')<>1 THEN RAISE EXCEPTION 'LIFE_CASHFLOW_MIGRATION_MISMATCH %','life_financial.joint_repair_profile_single(uuid,jsonb,jsonb,bigint)';END IF;
    EXECUTE replace(definition,'FROM life_financial.spot_cashflow_facts','FROM life_financial.cashflow_effective(p_account)');
    definition:=pg_get_functiondef('life_financial.joint_repair_profile_sources(uuid,jsonb,jsonb,bigint,jsonb)'::regprocedure);
    IF (length(definition)-length(replace(definition,'FROM life_financial.spot_cashflow_facts','')))/length('FROM life_financial.spot_cashflow_facts')<>1 THEN RAISE EXCEPTION 'LIFE_CASHFLOW_MIGRATION_MISMATCH %','life_financial.joint_repair_profile_sources(uuid,jsonb,jsonb,bigint,jsonb)';END IF;
    EXECUTE replace(definition,'FROM life_financial.spot_cashflow_facts','FROM life_financial.cashflow_effective(p_account)');
    definition:=pg_get_functiondef('life_financial.spot_capital_verified_v19(uuid)'::regprocedure);
    IF (length(definition)-length(replace(definition,'IF EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts WHERE account_id=p_account AND state<>''APPLIED'')','')))/length('IF EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts WHERE account_id=p_account AND state<>''APPLIED'')')<>1 THEN RAISE EXCEPTION 'LIFE_CASHFLOW_MIGRATION_MISMATCH %','life_financial.spot_capital_verified_v19(uuid)';END IF;
    EXECUTE replace(definition,'IF EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts WHERE account_id=p_account AND state<>''APPLIED'')','IF NOT life_financial.cashflow_history_verified(p_account)');
    definition:=pg_get_functiondef('life_financial.spot_capital_verified_v19(uuid)'::regprocedure);
    IF (length(definition)-length(replace(definition,'FULL JOIN life_financial.spot_cashflow_facts x USING(account_id,bill_id)','')))/length('FULL JOIN life_financial.spot_cashflow_facts x USING(account_id,bill_id)')<>1 THEN RAISE EXCEPTION 'LIFE_CASHFLOW_MIGRATION_MISMATCH %','life_financial.spot_capital_verified_v19(uuid)';END IF;
    EXECUTE replace(definition,'FULL JOIN life_financial.spot_cashflow_facts x USING(account_id,bill_id)','FULL JOIN (SELECT * FROM life_financial.cashflow_effective(p_account) WHERE state=''APPLIED'') x USING(account_id,bill_id)');
    definition:=pg_get_functiondef('life_financial.joint_financial_pending(uuid)'::regprocedure);
    IF (length(definition)-length(replace(definition,'FROM life_financial.spot_cashflow_facts WHERE account_id=p_account AND state=''RECOVERY_REQUIRED'')','')))/length('FROM life_financial.spot_cashflow_facts WHERE account_id=p_account AND state=''RECOVERY_REQUIRED'')')<>1 THEN RAISE EXCEPTION 'LIFE_CASHFLOW_MIGRATION_MISMATCH %','life_financial.joint_financial_pending(uuid)';END IF;
    EXECUTE replace(definition,'FROM life_financial.spot_cashflow_facts WHERE account_id=p_account AND state=''RECOVERY_REQUIRED'')','FROM life_financial.spot_cashflow_facts WHERE account_id=p_account AND state=''RECOVERY_REQUIRED'' AND life_financial.joint_source_unresolved(p_account,source_operation_id,NULL,true))');
    definition:=pg_get_functiondef('life_financial.swap_terminal_reason(uuid,jsonb,bigint,bigint)'::regprocedure);
    IF (length(definition)-length(replace(definition,'FROM life_financial.spot_cashflow_facts f JOIN life_financial.operations o ON o.account_id=f.account_id AND o.operation_id=f.source_operation_id
            WHERE f.account_id=p_account AND f.state=''RECOVERY_REQUIRED'' AND o.committed_version<=p_version)','')))/length('FROM life_financial.spot_cashflow_facts f JOIN life_financial.operations o ON o.account_id=f.account_id AND o.operation_id=f.source_operation_id
            WHERE f.account_id=p_account AND f.state=''RECOVERY_REQUIRED'' AND o.committed_version<=p_version)')<>1 THEN RAISE EXCEPTION 'LIFE_CASHFLOW_MIGRATION_MISMATCH %','life_financial.swap_terminal_reason(uuid,jsonb,bigint,bigint)';END IF;
    EXECUTE replace(definition,'FROM life_financial.spot_cashflow_facts f JOIN life_financial.operations o ON o.account_id=f.account_id AND o.operation_id=f.source_operation_id
            WHERE f.account_id=p_account AND f.state=''RECOVERY_REQUIRED'' AND o.committed_version<=p_version)','FROM life_financial.spot_cashflow_facts f JOIN life_financial.operations o ON o.account_id=f.account_id AND o.operation_id=f.source_operation_id
            WHERE f.account_id=p_account AND f.state=''RECOVERY_REQUIRED'' AND o.committed_version<=p_version AND life_financial.joint_source_unresolved(p_account,f.source_operation_id,p_version,false))');
    definition:=pg_get_functiondef('life_financial.shared_release_proof(uuid,text,text,bigint,bigint)'::regprocedure);
    IF (length(definition)-length(replace(definition,'FROM life_financial.spot_cashflow_facts f JOIN life_financial.operations op ON op.account_id=f.account_id AND op.operation_id=f.source_operation_id
            WHERE f.account_id=p_account AND f.state=''RECOVERY_REQUIRED'' AND op.committed_version<=p_version)','')))/length('FROM life_financial.spot_cashflow_facts f JOIN life_financial.operations op ON op.account_id=f.account_id AND op.operation_id=f.source_operation_id
            WHERE f.account_id=p_account AND f.state=''RECOVERY_REQUIRED'' AND op.committed_version<=p_version)')<>1 THEN RAISE EXCEPTION 'LIFE_CASHFLOW_MIGRATION_MISMATCH %','life_financial.shared_release_proof(uuid,text,text,bigint,bigint)';END IF;
    EXECUTE replace(definition,'FROM life_financial.spot_cashflow_facts f JOIN life_financial.operations op ON op.account_id=f.account_id AND op.operation_id=f.source_operation_id
            WHERE f.account_id=p_account AND f.state=''RECOVERY_REQUIRED'' AND op.committed_version<=p_version)','FROM life_financial.spot_cashflow_facts f JOIN life_financial.operations op ON op.account_id=f.account_id AND op.operation_id=f.source_operation_id
            WHERE f.account_id=p_account AND f.state=''RECOVERY_REQUIRED'' AND op.committed_version<=p_version AND life_financial.joint_source_unresolved(p_account,f.source_operation_id,p_version,false))');
    definition:=pg_get_functiondef('life_financial.spot_capital_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)'::regprocedure);
    IF (length(definition)-length(replace(definition,'FROM life_financial.spot_cashflow_facts WHERE account_id=p_account AND bill_id=bill;','')))/length('FROM life_financial.spot_cashflow_facts WHERE account_id=p_account AND bill_id=bill;')<>1 THEN RAISE EXCEPTION 'LIFE_CASHFLOW_MIGRATION_MISMATCH %','life_financial.spot_capital_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)';END IF;
    EXECUTE replace(definition,'FROM life_financial.spot_cashflow_facts WHERE account_id=p_account AND bill_id=bill;','FROM life_financial.cashflow_effective(p_account) WHERE account_id=p_account AND bill_id=bill;');
    definition:=pg_get_functiondef('life_financial.spot_capital_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)'::regprocedure);
    IF (length(definition)-length(replace(definition,'NOT life_financial.spot_capital_verified(p_account)','')))/length('NOT life_financial.spot_capital_verified(p_account)')<>2 THEN RAISE EXCEPTION 'LIFE_CASHFLOW_MIGRATION_MISMATCH %','life_financial.spot_capital_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)';END IF;
    EXECUTE replace(definition,'NOT life_financial.spot_capital_verified(p_account)','NOT life_financial.spot_capital_verified(p_account) OR EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts pending WHERE pending.account_id=p_account AND pending.state=''RECOVERY_REQUIRED'' AND life_financial.joint_source_unresolved(p_account,pending.source_operation_id,NULL,false))');
    definition:=pg_get_functiondef('life_financial.spot_state_v7(uuid)'::regprocedure);
    IF (length(definition)-length(replace(definition,'''recovery_required'',unknown OR unverified','')))/length('''recovery_required'',unknown OR unverified')<>1 THEN RAISE EXCEPTION 'LIFE_CASHFLOW_MIGRATION_MISMATCH %','life_financial.spot_state_v7(uuid)';END IF;
    EXECUTE replace(definition,'''recovery_required'',unknown OR unverified','''recovery_required'',unknown OR unverified OR EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts pending WHERE pending.account_id=p_account AND pending.state=''RECOVERY_REQUIRED'' AND life_financial.joint_source_unresolved(p_account,pending.source_operation_id,NULL,false))');
END; $$;

-- A raw pending bill is consumed only by its signed selected replacement. No
-- runtime role can call this kernel or manufacture the private staging rows.
DO $$ DECLARE definition text;needle text;replacement text;BEGIN
    definition:=pg_get_functiondef('life_financial.joint_stage_cashflow(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)'::regprocedure);
    definition:=replace(definition,'fact life_financial.spot_cashflow_facts;',
        'fact life_financial.spot_cashflow_facts; selected life_financial.joint_repair_staging;');
    needle:='SELECT * INTO fact FROM life_financial.spot_cashflow_facts WHERE account_id=p_account AND bill_id=bill;';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_CASHFLOW_WRITER_LOOKUP_MISMATCH';END IF;
    definition:=replace(definition,needle,replace(needle,'life_financial.spot_cashflow_facts','life_financial.cashflow_effective(p_account)'));
    needle:='state:=fact.state;';
    replacement:=needle||'
            IF state=''RECOVERY_REQUIRED'' AND NOT life_financial.joint_source_unresolved(p_account,fact.source_operation_id,NULL,true) THEN
                SELECT * INTO STRICT selected FROM life_financial.joint_repair_staging s WHERE s.account_id=p_account
                    AND s.transaction_id=pg_current_xact_id() AND s.command->''cashflows'' @> jsonb_build_array(p_command);
                IF fact.command-''operation_id'' IS DISTINCT FROM p_command-''operation_id'' THEN
                    RAISE EXCEPTION ''LIFE_CASHFLOW_REPAIR_IDENTITY_CONFLICT'' USING ERRCODE=''P0001'';END IF;
                INSERT INTO life_financial.spot_cashflow_applications VALUES(p_account,bill,fact.source_operation_id,
                    selected.repair_operation_id,p_command,op,p_fingerprint);
                INSERT INTO life_financial.cashflows VALUES(p_account,bill,reference,amount*reference,p_fingerprint);
                INSERT INTO life_financial.financial_events(account_id,event_id,product,kind,exchange_identity,occurred_at,source_hash,base_delta,quote_delta)
                    VALUES(p_account,op||'':cash'',''ACCOUNT'',''CASHFLOW'',bill,b.transfer_at,p_fingerprint,
                        CASE WHEN b.asset=''LIFE'' THEN amount ELSE 0 END,CASE WHEN b.asset=''USDT'' THEN amount ELSE 0 END);
                state:=''APPLIED'';
            END IF;';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_CASHFLOW_WRITER_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;

-- The strict direct-source profile now includes exact external bills alongside
-- SPOT, SWAP and funding. Observed times, valuations and signatures are retained.
DO $$ DECLARE definition text;needle text;replacement text;BEGIN
    definition:=pg_get_functiondef('life_financial.joint_repair_profile(uuid,jsonb,jsonb,bigint)'::regprocedure);
    needle:='original_spot jsonb:=''[]'';';replacement:='original_cashflows jsonb:=''[]'';'||needle;
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_CASHFLOW_PROFILE_VARIABLE_MISMATCH';END IF;
    definition:=replace(definition,needle,replacement);
    definition:=replace(definition,'IN (''SPOT_FILL'',''SWAP_FILL'')','IN (''SPOT_FILL'',''SWAP_FILL'',''SPOT_CASHFLOW'')');
    definition:=replace(definition,'IN (''PAID_FUNDING'',''SWAP_FILL'',''SPOT_FILL'')','IN (''PAID_FUNDING'',''SWAP_FILL'',''SPOT_FILL'',''SPOT_CASHFLOW'')');
    needle:='original_spot:=original_spot||(raw.command->''spot_fills'');';
    replacement:=needle||' original_cashflows:=original_cashflows||(raw.command->''cashflows'');';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_CASHFLOW_PROFILE_SOURCE_MISMATCH';END IF;
    definition:=replace(definition,needle,replacement);
    needle:='OR cmd#>''{replacement,cashflows}'' IS DISTINCT FROM ''[]''::jsonb';
    replacement:='THEN RETURN false;END IF;
    SELECT COALESCE(jsonb_agg(v ORDER BY v::text),''[]'') INTO expected FROM (
        SELECT DISTINCT value-''operation_id'' v FROM jsonb_array_elements(original_cashflows)) x;
    SELECT COALESCE(jsonb_agg(value-''operation_id'' ORDER BY (value-''operation_id'')::text),''[]'') INTO actual
        FROM jsonb_array_elements(cmd#>''{replacement,cashflows}'');
    IF expected IS DISTINCT FROM actual';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_CASHFLOW_PROFILE_EXACT_MISMATCH';END IF;
    definition:=replace(definition,needle,replacement);
    needle:='''cashflows'',''[]''::jsonb,';replacement:='''cashflows'',cmd#>''{replacement,cashflows}'',';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_CASHFLOW_PROFILE_COMMON_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;
REVOKE ALL ON life_financial.spot_cashflow_applications FROM PUBLIC;
REVOKE ALL ON FUNCTION life_financial.cashflow_effective(uuid),life_financial.cashflow_history_verified(uuid) FROM PUBLIC;

-- Check complete original/appended provenance only after every child/root has
-- been published; no transient pre-operation state can escape physical COMMIT.
CREATE FUNCTION life_financial.cashflow_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE booked boolean;
BEGIN
    IF TG_TABLE_NAME='operations' THEN booked:=NEW.receipt->>'state'='APPLIED';
    ELSIF TG_TABLE_NAME='spot_cashflow_facts' THEN booked:=NEW.state='APPLIED';
    ELSE booked:=true;END IF;
    -- Pending capacity denials retain raw evidence even when replay cannot run
    -- within the configured bound. They never publish provisional money. Every
    -- booked application additionally proves its final cashbook/events/balance.
    IF NOT life_financial.cashflow_history_verified(NEW.account_id)
        OR booked AND NOT life_financial.spot_capital_verified(NEW.account_id) THEN
        RAISE EXCEPTION 'LIFE_CASHFLOW_HISTORY_UNVERIFIED_AT_COMMIT' USING ERRCODE='P0001';END IF;
    RETURN NULL;
END; $$;
-- Scope the deferred proof to its actual publication writes, not every account
-- version change (e.g. a SPOT arm with no external bills). All three insert
-- boundaries must see the final original/application operation at COMMIT.
CREATE CONSTRAINT TRIGGER cashflow_history_commit AFTER INSERT ON life_financial.spot_cashflow_facts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.cashflow_commit_guard();
CREATE CONSTRAINT TRIGGER cashflow_application_commit AFTER INSERT ON life_financial.spot_cashflow_applications
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.cashflow_commit_guard();
CREATE CONSTRAINT TRIGGER cashflow_operation_commit AFTER INSERT ON life_financial.operations
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN(NEW.command->>'kind'='SPOT_CASHFLOW') EXECUTE FUNCTION life_financial.cashflow_commit_guard();
REVOKE ALL ON FUNCTION life_financial.cashflow_commit_guard() FROM PUBLIC;

-- Recording pending is allowed under HALT; ordinary money writers cannot use
-- the verified-history distinction to bypass an unresolved external bill.
DO $$ DECLARE definition text;signature text;needle text;replacement text;BEGIN
    FOREACH signature IN ARRAY ARRAY[
        'life_financial.spot_observe(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)',
        'life_financial.swap_fill_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
        'life_financial.funding_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'] LOOP
        definition:=pg_get_functiondef(signature::regprocedure);
        IF signature LIKE '%spot_observe%' THEN
            needle:='IF EXISTS(SELECT 1 FROM life_financial.spot_pending_observations WHERE account_id=p_account AND intent_id=t.intent_id AND life_financial.joint_source_unresolved(p_account,operation_id,NULL,false))';
        ELSIF signature LIKE '%swap_fill_command%' THEN
            needle:='IF EXISTS(SELECT 1 FROM life_financial.swap_pending_fills WHERE account_id=p_account AND intent_id=t.intent_id AND life_financial.joint_source_unresolved(p_account,operation_id,NULL,false))';
        ELSE
            needle:='IF EXISTS(SELECT 1 FROM life_financial.funding_observations WHERE account_id=p_account AND state=''RECOVERY_REQUIRED'' AND life_financial.funding_pending_unresolved(p_account,operation_id,NULL,false))';
        END IF;
        replacement:=needle||' OR EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts pending WHERE pending.account_id=p_account
            AND pending.state=''RECOVERY_REQUIRED'' AND life_financial.joint_source_unresolved(p_account,pending.source_operation_id,NULL,false))';
        IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_CASHFLOW_PRIOR_PENDING_MISMATCH %',signature;END IF;
        EXECUTE replace(definition,needle,replacement);
    END LOOP;
END; $$;
