-- Preserve original APPLIED native joint parents, their exact children and
-- complete economic leaves. This creates no accounting fact or permission.
CREATE FUNCTION life_financial.legacy_parent_prefix_valid(p_account uuid,b jsonb,p_version bigint) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r record;m jsonb;c life_financial.operations;k text;matches bigint;
BEGIN
    FOR r IN SELECT x.*,o.command AS original,o.receipt,o.fingerprint,o.committed_version FROM life_financial.joint_financial_batches x
        LEFT JOIN life_financial.operations o ON o.account_id=x.account_id AND o.operation_id=x.operation_id
        WHERE x.account_id=p_account AND (o.committed_version<=p_version OR o.operation_id IS NULL) LOOP
        IF r.state IS DISTINCT FROM 'APPLIED' OR r.reason IS NOT NULL
            OR r.original->>'kind' IS DISTINCT FROM 'JOINT_BATCH' OR r.original->'command' IS DISTINCT FROM r.command
            OR r.original#>>'{command,operation_id}' IS DISTINCT FROM r.operation_id::text
            OR r.fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(r.original),'UTF8')),'hex')
            OR r.receipt IS DISTINCT FROM jsonb_build_object('kind','JOINT_BATCH','operation_id',r.operation_id,'state','APPLIED',
                'cash_delta_usdt',((r.money_after->>'quote')::numeric-(r.money_before->>'quote')::numeric)::text,
                'base_delta_life',((r.money_after->>'base')::numeric-(r.money_before->>'base')::numeric)::text,
                'version',r.committed_version,'replayed',false,'trading_permission',false)
            OR (SELECT jsonb_agg(value-ARRAY['request','receipt'] ORDER BY ord) FROM jsonb_array_elements(r.members) WITH ORDINALITY x(value,ord))
                IS DISTINCT FROM life_financial.joint_batch_members(r.command) THEN RETURN false;END IF;
        FOR m IN SELECT value FROM jsonb_array_elements(r.members) LOOP
            SELECT * INTO STRICT c FROM life_financial.operations WHERE account_id=p_account AND operation_id=m#>>'{command,operation_id}';
            IF c.command IS DISTINCT FROM m->'request' OR c.receipt IS DISTINCT FROM m->'receipt'
                OR c.command->>'kind' IS DISTINCT FROM m->>'kind' OR c.command->'command' IS DISTINCT FROM m->'command'
                OR c.command->'binding' IS DISTINCT FROM r.original->'binding' OR c.command->'owner' IS DISTINCT FROM r.original->'owner'
                OR c.receipt->>'state' IS DISTINCT FROM 'APPLIED' OR c.committed_version>=r.committed_version
                OR c.fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(c.command),'UTF8')),'hex') THEN RETURN false;END IF;
            CASE m->>'kind'
            WHEN 'SPOT_CASHFLOW' THEN
                SELECT count(*) INTO matches FROM jsonb_array_elements(b->'cashflows') target
                    WHERE target-ARRAY['operation_id','observed_at']=(m->'command')-ARRAY['operation_id','observed_at'];
                IF matches<>1 THEN RETURN false;END IF;
            WHEN 'SPOT_FILL','SWAP_FILL' THEN
                k:=CASE m->>'kind' WHEN 'SPOT_FILL' THEN 'spot_fills' ELSE 'swap_fills' END;
                SELECT count(*) INTO matches FROM jsonb_array_elements(b->k) target
                    WHERE target-ARRAY['operation_id','fills','cumulative_base_life','observed_at']
                        =(m->'command')-ARRAY['operation_id','fills','cumulative_base_life','observed_at']
                        AND (target->>'cumulative_base_life')::numeric>=(m#>>'{command,cumulative_base_life}')::numeric
                        AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(m#>'{command,fills}') leaf WHERE
                            (SELECT count(*) FROM jsonb_array_elements(target->'fills') fact WHERE fact=leaf)<>1);
                IF matches<>1 THEN RETURN false;END IF;
            WHEN 'PAID_FUNDING' THEN
                IF EXISTS(SELECT 1 FROM jsonb_array_elements(m#>'{command,bundle,funding,payments}') item WHERE
                    (SELECT count(*) FROM jsonb_array_elements(b#>'{funding,bundle,funding,payments}') target WHERE target=item)<>1)
                    THEN RETURN false;END IF;
            ELSE RETURN false;
            END CASE;
        END LOOP;
    END LOOP;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

DO $$ DECLARE definition text;needle text;BEGIN
    definition:=pg_get_functiondef('life_financial.legacy_spot_prefix_valid(uuid,jsonb,bigint)'::regprocedure);
    needle:='OR NOT life_financial.legacy_derivative_prefix_valid(p_account,b,p_version)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_PARENT_PREFIX_MISMATCH';END IF;
    EXECUTE replace(definition,needle,needle||' OR NOT life_financial.legacy_parent_prefix_valid(p_account,b,p_version)');
    definition:=pg_get_functiondef('life_financial.legacy_applying_pending(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'::regprocedure);
    needle:='OR EXISTS(SELECT 1 FROM life_financial.joint_financial_batches WHERE account_id=p_account)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_PARENT_PREFIX_MISMATCH';END IF;
    EXECUTE replace(definition,needle,'OR NOT life_financial.legacy_parent_prefix_valid(p_account,cmd,p_version)');
END; $$;
DO $$ DECLARE r record;BEGIN
    FOR r IN SELECT p.oid::regprocedure AS signature,x.grantee FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE n.nspname='life_financial' AND p.proname='legacy_parent_prefix_valid' AND x.grantee<>p.proowner LOOP
        IF r.grantee=0 THEN EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',r.signature);
        ELSE EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',r.signature,pg_get_userbyid(r.grantee));END IF;
    END LOOP;
END; $$;
