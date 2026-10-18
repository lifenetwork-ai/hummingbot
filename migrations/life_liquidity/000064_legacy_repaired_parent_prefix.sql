-- A signed repaired original stays RECOVERY_REQUIRED forever. The committed
-- resolution link clears its barrier only at/after the original root version;
-- it never rewrites the pending operation, money, members or receipt.
DO $$ DECLARE definition text;needle text;replacement text;BEGIN
    definition:=pg_get_functiondef('life_financial.legacy_parent_prefix_valid(uuid,jsonb,bigint)'::regprocedure);
    needle:='BEGIN
    FOR r IN SELECT x.*,o.command AS original,o.receipt,o.fingerprint,o.committed_version FROM life_financial.joint_financial_batches x';
    replacement:='BEGIN
    IF EXISTS(SELECT 1 FROM life_financial.joint_financial_batches x LEFT JOIN life_financial.operations o
        ON o.account_id=x.account_id AND o.operation_id=x.operation_id WHERE x.account_id=p_account
        AND x.state=''RECOVERY_REQUIRED'' AND (o.committed_version<=p_version OR o.operation_id IS NULL))
        AND NOT life_financial.joint_repair_verified(p_account) THEN RETURN false;END IF;
    FOR r IN SELECT x.*,o.command AS original,o.receipt,o.fingerprint,o.committed_version FROM life_financial.joint_financial_batches x';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_REPAIRED_PREFIX_MISMATCH';END IF;
    definition:=replace(definition,needle,replacement);
    needle:='        IF r.state IS DISTINCT FROM ''APPLIED'' OR r.reason IS NOT NULL';
    replacement:='        IF r.state=''RECOVERY_REQUIRED'' THEN
            IF life_financial.funding_pending_unresolved(p_account,r.operation_id,p_version,false)
                OR r.original->>''kind'' IS DISTINCT FROM ''JOINT_BATCH'' OR r.original->''command'' IS DISTINCT FROM r.command
                OR r.original#>>''{command,operation_id}'' IS DISTINCT FROM r.operation_id::text
                OR (r.original->>''version'')::bigint IS DISTINCT FROM r.start_version
                OR r.committed_version IS DISTINCT FROM r.start_version+1
                OR r.fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(r.original),''UTF8'')),''hex'')
                OR r.money_before IS DISTINCT FROM life_financial.portfolio_money_at(p_account,r.start_version)
                OR r.money_after IS DISTINCT FROM r.money_before OR r.members IS DISTINCT FROM ''[]''::jsonb
                OR r.receipt IS DISTINCT FROM (jsonb_build_object(''kind'',''JOINT_BATCH'',''operation_id'',r.operation_id,''state'',''RECOVERY_REQUIRED'',
                    ''cash_delta_usdt'',((r.money_after->>''quote'')::numeric-(r.money_before->>''quote'')::numeric)::text,
                    ''base_delta_life'',((r.money_after->>''base'')::numeric-(r.money_before->>''base'')::numeric)::text,
                    ''version'',r.committed_version,''replayed'',false,''trading_permission'',false)
                    ||CASE WHEN r.reason IS NULL THEN ''{}''::jsonb ELSE jsonb_build_object(''reason'',r.reason) END)
                THEN RETURN false;END IF;
            -- Every APPLIED replacement parent is still checked by the same
            -- loop against the complete target leaves, children and receipts.
            CONTINUE;
        END IF;
        IF r.state IS DISTINCT FROM ''APPLIED'' OR r.reason IS NOT NULL';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_REPAIRED_PREFIX_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;
