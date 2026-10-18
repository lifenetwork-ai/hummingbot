-- A mixed outer account source retains independently admitted native SPOT
-- claims alongside SWAP claims. No claim, preparation, ARM or release is made.
DO $$ DECLARE definition text;needle text;BEGIN
    definition:=pg_get_functiondef('life_financial.legacy_joint_basis_valid(uuid,jsonb,bigint)'::regprocedure);
    needle:='OR c.product<>''SWAP'' OR NOT EXISTS';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_JOINT_SPOT_MISMATCH';END IF;
    definition:=replace(definition,needle,'OR c.product NOT IN (''SPOT'',''SWAP'') OR NOT EXISTS');
    needle:='AND o.operation_id=c.operation_id AND o.committed_version<=p_version)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_JOINT_SPOT_MISMATCH';END IF;
    definition:=replace(definition,needle,'AND o.operation_id=c.operation_id AND o.committed_version<=p_version
                AND life_financial.shared_operation_verified(p_account,o.operation_id))');
    needle:='FOR r IN SELECT value FROM jsonb_array_elements(b->''claims'') LOOP';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_JOINT_SPOT_MISMATCH';END IF;
    definition:=replace(definition,needle,'IF EXISTS(SELECT 1 FROM jsonb_array_elements(b->''claims'') original WHERE original#>>''{claim,product}''=''SPOT'')
        AND NOT life_financial.legacy_spot_basis_valid(p_account,b,p_version) THEN RETURN false;END IF;
    '||needle);
    needle:='SELECT * INTO STRICT d FROM life_financial.swap_preparations';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_JOINT_SPOT_MISMATCH';END IF;
    EXECUTE replace(definition,needle,'IF c.product=''SPOT'' THEN
            IF NOT EXISTS(SELECT 1 FROM life_financial.spot_preparations prepared
                    WHERE prepared.account_id=p_account AND prepared.operation_id=r->>''prepare_operation_id''
                        AND prepared.intent_id=c.intent_id AND prepared.request->>''client_order_id''=c.wire_id
                        AND prepared.request->>''side''=c.side AND prepared.request->>''session_id''=c.session_id::text
                        AND (prepared.request->>''epoch'')::bigint=c.epoch
                        AND (prepared.request->>''quantity_base'')::numeric=c.quantity_base
                        AND (prepared.request->>''limit_price_usdt'')::numeric=c.price_usdt)
                THEN RETURN false;END IF;
            CONTINUE;
        END IF;
        '||needle);
    -- Readback must also reject altered preparation/ARM bytes, even when a
    -- separate current-state diagnostic has already marked the account unsafe.
    definition:=pg_get_functiondef('life_financial.legacy_spot_basis_valid(uuid,jsonb,bigint)'::regprocedure);
    needle:='AND o.command->>''kind''=''SPOT_PREPARE'' AND o.command->''command''=j AND o.committed_version<=p_version)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_JOINT_SPOT_MISMATCH';END IF;
    definition:=replace(definition,needle,'AND o.command->>''kind''=''SPOT_PREPARE'' AND o.command->''command''=j AND o.committed_version<=p_version
                AND life_financial.legacy_spot_unsent_valid(p_account,j,o.committed_version))');
    needle:='AND o.command->>''prepare''=d.operation_id::text AND o.committed_version<=p_version';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_JOINT_SPOT_MISMATCH';END IF;
    EXECUTE replace(definition,needle,needle||'
                AND o.fingerprint=encode(sha256(convert_to(life_financial.canonical_json_v1(jsonb_build_object(
                    ''kind'',''SPOT_ARM'',''prepare'',d.operation_id,''owner'',o.command->''owner'',
                    ''cut'',jsonb_build_object(''checkpoint'',jsonb_build_object(''binding'',o.command->''binding'',
                        ''revision'',o.committed_version-1,''event_version'',o.committed_version-1,
                        ''resource_versions'',jsonb_build_array(jsonb_build_object(''resource'',''account'',''version'',o.committed_version-1)),
                        ''state_digest'',o.command->''cut''),
                        ''components'',jsonb_build_array(jsonb_build_array(''postgres_account'',o.command->''cut''))))),''UTF8'')),''hex'')');
END; $$;
