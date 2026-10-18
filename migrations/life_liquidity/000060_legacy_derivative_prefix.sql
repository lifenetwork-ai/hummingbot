-- Already booked derivative leaves must be an exact subset of the retired
-- original File history. Import does not book, refund or activate anything.
CREATE FUNCTION life_financial.legacy_derivative_prefix_valid(p_account uuid,b jsonb,p_version bigint) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE f record;matches bigint;payment jsonb;loss numeric;
BEGIN
    IF jsonb_typeof(b->'swap_fills') IS DISTINCT FROM 'array'
        OR jsonb_typeof(b#>'{funding,bundle,funding,payments}') IS DISTINCT FROM 'array' THEN RETURN false;END IF;
    FOR f IN SELECT x.*,o.command AS original,o.receipt,o.fingerprint,o.committed_version FROM life_financial.swap_fill_facts x LEFT JOIN life_financial.operations o
        ON o.account_id=x.account_id AND o.operation_id=x.source_operation_id
        WHERE x.account_id=p_account AND (o.committed_version<=p_version OR o.operation_id IS NULL) LOOP
        SELECT count(*) INTO matches FROM jsonb_array_elements(b->'swap_fills') snapshot
            CROSS JOIN LATERAL jsonb_array_elements(snapshot.value->'fills') leaf
            WHERE leaf.value=f.observation AND leaf.value->>'trade_id'=f.trade_id::text
                AND snapshot.value->>'intent_id'=f.intent_id::text
                AND snapshot.value->>'exchange_order_id'=f.exchange_order_id::text
                AND snapshot.value-ARRAY['operation_id','fills','cumulative_base_life','observed_at']
                    =(f.original->'command')-ARRAY['operation_id','fills','cumulative_base_life','observed_at'];
        SELECT sum(greatest(0,-(item->>'signed_fee')::numeric)+greatest(0,-(item->>'realized_pnl_usdt')::numeric))
            INTO loss FROM jsonb_array_elements(f.original#>'{command,fills}') item;
        IF matches<>1 OR f.original->>'kind' IS DISTINCT FROM 'SWAP_FILL'
            OR f.original#>>'{command,operation_id}' IS DISTINCT FROM f.source_operation_id::text
            OR f.fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(f.original),'UTF8')),'hex')
            OR f.source_hash IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(f.observation),'UTF8')),'hex')
            OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(f.original#>'{command,fills}') item WHERE item=f.observation)
            OR (f.receipt->>'execution_loss_usdt')::numeric IS DISTINCT FROM loss
            OR f.receipt IS DISTINCT FROM jsonb_build_object('kind','SWAP_FILL','operation_id',f.source_operation_id,
                'intent_id',f.intent_id,'state','APPLIED','cumulative_base_life',f.original#>>'{command,cumulative_base_life}',
                'execution_loss_usdt',f.receipt->>'execution_loss_usdt','version',f.committed_version,'replayed',false,'trading_permission',false)
            THEN RETURN false;END IF;
    END LOOP;
    FOR f IN SELECT x.*,s.bundle,s.state,s.reason,s.cash_delta_usdt,s.gross_debits_usdt,
        o.command AS original,o.receipt,o.fingerprint,o.committed_version FROM life_financial.funding_cash_payments x
        LEFT JOIN life_financial.funding_observations s ON s.account_id=x.account_id AND s.operation_id=x.source_operation_id
        LEFT JOIN life_financial.operations o ON o.account_id=x.account_id AND o.operation_id=x.source_operation_id
        WHERE x.account_id=p_account AND (o.committed_version<=p_version OR o.operation_id IS NULL) LOOP
        SELECT count(*),jsonb_agg(item)->0 INTO matches,payment FROM jsonb_array_elements(f.bundle#>'{funding,payments}') item
            WHERE item->>'bill_id'=f.bill_id AND (item->>'settled_at_ms')::bigint=f.settled_at_ms
                AND (item->>'signed_payment_quote')::numeric=f.signed_payment_usdt;
        IF matches<>1 OR f.state IS DISTINCT FROM 'APPLIED' OR f.reason IS NOT NULL
            OR f.original->>'kind' IS DISTINCT FROM 'PAID_FUNDING'
            OR f.original#>>'{command,operation_id}' IS DISTINCT FROM f.source_operation_id::text
            OR f.original#>'{command,bundle}' IS DISTINCT FROM f.bundle
            OR f.fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(f.original),'UTF8')),'hex')
            OR f.source_hash IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(payment),'UTF8')),'hex')
            OR (SELECT count(*) FROM jsonb_array_elements(b#>'{funding,bundle,funding,payments}') target WHERE target=payment)<>1
            OR f.receipt IS DISTINCT FROM jsonb_build_object('kind','PAID_FUNDING','operation_id',f.source_operation_id,'state','APPLIED',
                'cash_delta_usdt',f.cash_delta_usdt::text,'gross_debits_usdt',f.gross_debits_usdt::text,
                'version',f.committed_version,'replayed',false,'trading_permission',false) THEN RETURN false;END IF;
    END LOOP;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

DO $$ DECLARE definition text;needle text;replacement text;BEGIN
    definition:=pg_get_functiondef('life_financial.legacy_spot_prefix_valid(uuid,jsonb,bigint)'::regprocedure);
    needle:='OR EXISTS(SELECT 1 FROM life_financial.swap_fill_facts x JOIN life_financial.operations o
        ON o.account_id=x.account_id AND o.operation_id=x.source_operation_id WHERE x.account_id=p_account AND o.committed_version<=p_version)
        OR EXISTS(SELECT 1 FROM life_financial.funding_cash_payments x JOIN life_financial.operations o
        ON o.account_id=x.account_id AND o.operation_id=x.source_operation_id WHERE x.account_id=p_account AND o.committed_version<=p_version)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_DERIVATIVE_PREFIX_MISMATCH';END IF;
    EXECUTE replace(definition,needle,'OR NOT life_financial.legacy_derivative_prefix_valid(p_account,b,p_version)');

    definition:=pg_get_functiondef('life_financial.legacy_applying_pending(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'::regprocedure);
    needle:='ELSE true END';
    replacement:='WHEN e.product=''SWAP'' AND e.kind=''FILL'' THEN NOT EXISTS(SELECT 1 FROM life_financial.swap_fill_facts x
                JOIN life_financial.operations o ON o.account_id=x.account_id AND o.operation_id=x.source_operation_id
                WHERE x.account_id=p_account AND x.trade_id=e.exchange_identity AND o.committed_version<=p_version)
            WHEN e.product=''ACCOUNT'' AND e.kind=''FUNDING'' THEN NOT EXISTS(SELECT 1 FROM life_financial.funding_cash_payments x
                JOIN life_financial.operations o ON o.account_id=x.account_id AND o.operation_id=x.source_operation_id
                WHERE x.account_id=p_account AND x.bill_id=e.exchange_identity AND o.committed_version<=p_version)
            ELSE true END';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_DERIVATIVE_PREFIX_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;
-- Private proof only, including under permissive deployment-owner defaults.
DO $$ DECLARE r record;BEGIN
    FOR r IN SELECT p.oid::regprocedure AS signature,x.grantee FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE n.nspname='life_financial' AND p.proname='legacy_derivative_prefix_valid' AND x.grantee<>p.proowner LOOP
        IF r.grantee=0 THEN EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',r.signature);
        ELSE EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',r.signature,pg_get_userbyid(r.grantee));END IF;
    END LOOP;
END; $$;
