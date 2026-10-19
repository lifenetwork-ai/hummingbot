-- A zero-fill original SWAP uses its unique independently admitted native
-- preparation/ARM/ACK. No target preparation ID, ACK, fill, release or money
-- is inferred from the File snapshot. Existing filled/SPOT proofs are retained.
-- Audit the complete predecessor program, then preserve its OID/ACL/attributes.
DO $$ DECLARE definition text;needle text;source text;BEGIN
    SELECT prosrc INTO source FROM pg_proc
        WHERE oid='life_financial.legacy_joint_basis_valid(uuid,jsonb,bigint)'::regprocedure
            AND provolatile='s' AND prosecdef AND proconfig=ARRAY['search_path=pg_catalog'];
    IF NOT FOUND OR encode(sha256(convert_to(source,'UTF8')),'hex')<>'c0722fa8fc870fbb9adebb40c935485492fdd83803e17af57b33667f6ddacc02' THEN
        RAISE EXCEPTION 'LIFE_UNFILLED_SWAP_PREFIX_MIGRATION_MISMATCH';END IF;
    definition:=pg_get_functiondef('life_financial.legacy_joint_basis_valid(uuid,jsonb,bigint)'::regprocedure);
    needle:='    FOR r IN SELECT value FROM jsonb_array_elements(b->''claims'') LOOP';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_UNFILLED_SWAP_PREFIX_MIGRATION_MISMATCH';END IF;
    definition:=replace(definition,needle,'    IF EXISTS(SELECT 1 FROM jsonb_array_elements(b->''claims'') tagged WHERE tagged ? ''unfilled'') THEN
        IF NOT life_financial.swap_verified(p_account) OR NOT life_financial.swap_accounting_verified(p_account)
            OR EXISTS(SELECT 1 FROM jsonb_array_elements(b->''claims'') tagged WHERE tagged ? ''unfilled''
                AND (tagged->''unfilled'' IS DISTINCT FROM ''true''::jsonb OR tagged#>>''{claim,product}'' IS DISTINCT FROM ''SWAP''
                    OR NOT tagged ?& ARRAY[''claim'',''exchange_order_id'',''unfilled'']
                    OR tagged-ARRAY[''claim'',''exchange_order_id'',''unfilled'']<>''{}''::jsonb)) THEN RETURN false;END IF;
    END IF;
'||needle);
    needle:='SELECT * INTO STRICT d FROM life_financial.swap_preparations WHERE account_id=p_account AND operation_id=r->>''prepare_operation_id'';';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_UNFILLED_SWAP_PREFIX_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,'IF r ? ''unfilled'' THEN
            SELECT * INTO STRICT d FROM life_financial.swap_preparations WHERE account_id=p_account AND intent_id=c.intent_id;
            IF EXISTS(SELECT 1 FROM life_financial.swap_fill_facts f JOIN life_financial.operations o
                    ON o.account_id=f.account_id AND o.operation_id=f.source_operation_id
                    WHERE f.account_id=p_account AND f.intent_id=c.intent_id AND o.committed_version<=p_version)
                OR EXISTS(SELECT 1 FROM life_financial.swap_pending_fills f JOIN life_financial.operations o USING(account_id,operation_id)
                    WHERE f.account_id=p_account AND f.intent_id=c.intent_id AND o.committed_version<=p_version)
                THEN RETURN false;END IF;
        ELSE
            SELECT * INTO STRICT d FROM life_financial.swap_preparations WHERE account_id=p_account AND operation_id=r->>''prepare_operation_id'';
        END IF;');
END; $$;
