-- Reuse only a verified, account-locked pre-write repair view. No session cache
-- or post-mutation/COMMIT proof is removed. The new helper is owner-only and
-- additionally requires the exact private, transaction-local signed repair.
DO $$
DECLARE definition text;needle text;replacement text;header text;new_header text;region text;r record;
BEGIN
    definition:=pg_get_functiondef('life_financial.joint_batch_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'::regprocedure);
    -- Reuse is valid only for this audited predecessor: it performs no money
    -- write before checking the supplied cut. Preserve every later guard.
    IF encode(sha256(convert_to(definition,'UTF8')),'hex')<>'c32ec29a39a50a16a423ebf232092e8382491a27e7ab0d68eb2b204d8951a493' THEN
        RAISE EXCEPTION 'LIFE_REPAIR_VIEW_BODY_MISMATCH';END IF;
    header:='CREATE OR REPLACE FUNCTION life_financial.joint_batch_command(p_account uuid, p_binding jsonb, p_owner jsonb, p_version bigint, p_cut life_financial.digest, p_fingerprint life_financial.digest, cmd jsonb)';
    new_header:='CREATE FUNCTION life_financial.joint_batch_from_repair_view(p_account uuid, p_binding jsonb, p_owner jsonb, p_version bigint, p_cut life_financial.digest, p_fingerprint life_financial.digest, cmd jsonb, p_view jsonb)';
    IF (length(definition)-length(replace(definition,header,'')))/length(header)<>1
        OR position('SECURITY DEFINER' IN definition)=0 THEN
        RAISE EXCEPTION 'LIFE_REPAIR_VIEW_HEADER_MISMATCH';END IF;
    definition:=replace(definition,header,new_header);
    needle:='BEGIN
    IF NOT life_financial.can_access(p_account,true)';
    replacement:='BEGIN
    IF jsonb_typeof(p_view) IS DISTINCT FROM ''object''
        OR p_view->>''digest'' IS DISTINCT FROM p_cut
        OR (p_view#>>''{account,account_id}'')::uuid IS DISTINCT FROM p_account
        OR (p_view#>>''{account,version}'')::bigint IS DISTINCT FROM p_version
        OR p_view#>>''{joint_batches,verified}'' IS DISTINCT FROM ''true''
        OR p_view->>''unverified'' IS DISTINCT FROM ''false''
        OR encode(sha256(convert_to((p_view-''digest''-''observed_at'')::text,''UTF8'')),''hex'') IS DISTINCT FROM p_cut
        OR NOT EXISTS(SELECT 1 FROM life_financial.joint_repair_staging s
            JOIN life_financial.joint_repair_approvals g ON g.account_id=s.account_id
                AND g.operation_id=s.repair_operation_id AND g.pending_operation_id=s.pending_operation_id
            WHERE s.account_id=p_account AND s.transaction_id=pg_current_xact_id() AND s.command=cmd
                AND g.body#>''{payload,replacement}''=cmd
                AND g.body#>>''{cut,checkpoint,state_digest}''=p_cut
                AND (g.body#>>''{cut,checkpoint,revision}'')::bigint=p_version
                AND g.body#>''{approval,binding}''=p_binding
                AND COALESCE(g.body#>''{payload,additional_pending_operation_ids}'',''[]''::jsonb)=s.additional_pending_operation_ids
                AND g.source_hash=encode(sha256(convert_to(g.body::text,''UTF8'')),''hex'')) THEN
        RAISE EXCEPTION ''LIFE_JOINT_REPAIR_VIEW_UNQUALIFIED'' USING ERRCODE=''P0001'';END IF;
    IF NOT life_financial.can_access(p_account,true)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_REPAIR_VIEW_CONTEXT_MISMATCH';END IF;
    definition:=replace(definition,needle,replacement);
    needle:='IF NOT life_financial.joint_batch_verified(p_account) THEN RAISE EXCEPTION ''LIFE_JOINT_BATCH_UNVERIFIED'' USING ERRCODE=''P0001'';END IF;';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_REPAIR_VIEW_PROOF_MISMATCH';END IF;
    definition:=replace(definition,needle,'-- The exact pre-write snapshot already contains this original proof.');
    needle:='view:=life_financial.spot_state(p_account);';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_REPAIR_VIEW_SNAPSHOT_MISMATCH';END IF;
    definition:=replace(definition,needle,'view:=p_view;');
    EXECUTE definition;
    REVOKE ALL ON FUNCTION life_financial.joint_batch_from_repair_view(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb,jsonb) FROM PUBLIC;
    FOR r IN SELECT x.grantee FROM pg_proc p
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE p.oid='life_financial.joint_batch_from_repair_view(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb,jsonb)'::regprocedure
            AND x.grantee<>p.proowner AND x.grantee<>0 LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION life_financial.joint_batch_from_repair_view(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb,jsonb) FROM %I',pg_get_userbyid(r.grantee));
    END LOOP;
    definition:=pg_get_functiondef('life_financial.joint_repair_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'::regprocedure);
    -- Between the original snapshot and child call, only the exact private
    -- staging row may be written. Refuse any altered/intervening program.
    region:=substring(definition FROM position('view:=life_financial.spot_state(p_account);' IN definition)
        FOR position('    child_request:=' IN definition)-position('view:=life_financial.spot_state(p_account);' IN definition));
    IF encode(sha256(convert_to(region,'UTF8')),'hex')<>'71128be231ce9c267aa68e20dd1f22deaffcbb9ece17e0766720a6263a0f6131' THEN
        RAISE EXCEPTION 'LIFE_REPAIR_VIEW_PREWRITE_MISMATCH';END IF;
    needle:='child:=life_financial.joint_batch_command(p_account,p_binding,p_owner,p_version,p_cut,
        encode(sha256(convert_to(life_financial.canonical_json_v1(child_request),''UTF8'')),''hex''),cmd->''replacement'');';
    replacement:='child:=life_financial.joint_batch_from_repair_view(p_account,p_binding,p_owner,p_version,p_cut,
        encode(sha256(convert_to(life_financial.canonical_json_v1(child_request),''UTF8'')),''hex''),cmd->''replacement'',view);';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_REPAIR_VIEW_CALL_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;

-- Fresh capital/carry commands already compute a complete locked snapshot.
-- Read its exact proof flags instead of evaluating the same proofs twice.
-- Original-operation replay retains its independent full proof and identity.
DO $$
DECLARE definition text;signature text;proof text;needle text;replacement text;name text;
BEGIN
    FOREACH name IN ARRAY ARRAY['shared_capital_command','carry_check'] LOOP
        signature:='life_financial.'||name||'(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)';
        definition:=pg_get_functiondef(to_regprocedure(signature));
        IF name='shared_capital_command' THEN
            proof:='IF NOT life_financial.shared_capital_verified(p_account) THEN RAISE EXCEPTION ''LIFE_CAPITAL_UNVERIFIED'' USING ERRCODE=''P0001''; END IF;';
            needle:='IF FOUND THEN
        IF NOT life_financial.shared_operation_verified(p_account,op)';
            replacement:='IF FOUND THEN
        '||proof||'
        IF NOT life_financial.shared_operation_verified(p_account,op)';
        ELSE
            proof:='IF NOT life_financial.carry_verified(p_account) OR NOT life_financial.shared_capital_verified(p_account) THEN RAISE EXCEPTION ''LIFE_CARRY_UNVERIFIED'' USING ERRCODE=''P0001''; END IF;';
            needle:='IF FOUND THEN
        IF prior.command<>request';
            replacement:='IF FOUND THEN
        '||proof||'
        IF prior.command<>request';
        END IF;
        IF (length(definition)-length(replace(definition,proof,'')))/length(proof)<>1
            OR (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
            RAISE EXCEPTION 'LIFE_ADMISSION_VIEW_PROOF_MISMATCH';END IF;
        definition:=replace(definition,proof,'-- New commands use the complete snapshot proof below.');
        definition:=replace(definition,needle,replacement);
        needle:='view:=life_financial.spot_state(p_account);';
        replacement:=needle||'
    IF view#>>''{shared_capital,verified}'' IS DISTINCT FROM ''true''';
        IF name='carry_check' THEN
            replacement:=replacement||' OR view#>>''{carry,verified}'' IS DISTINCT FROM ''true''';
        END IF;
        replacement:=replacement||' THEN RAISE EXCEPTION '||quote_literal(CASE WHEN name='carry_check' THEN 'LIFE_CARRY_UNVERIFIED' ELSE 'LIFE_CAPITAL_UNVERIFIED' END)||' USING ERRCODE=''P0001'';END IF;';
        IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
            RAISE EXCEPTION 'LIFE_ADMISSION_VIEW_SNAPSHOT_MISMATCH';END IF;
        EXECUTE replace(definition,needle,replacement);
    END LOOP;
END; $$;
