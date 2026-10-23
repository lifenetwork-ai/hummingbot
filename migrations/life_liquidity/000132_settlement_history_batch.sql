-- Batch member provenance and original subsidy-window joins. Retain exact
-- ordered numeric arithmetic/canonical proof bytes and independent COMMIT audit.
DO $$ DECLARE p record;item jsonb;BEGIN
    FOR item IN SELECT value FROM jsonb_array_elements('[{"signature":"life_financial.spot_inventory_evidence(uuid,jsonb)","sha256":"889d76165d9fb91caa88f2015d6ede76fdad51feea1ed978a6c9932cd786b963"},{"signature":"life_financial.spot_inventory_settlements_verified(uuid)","sha256":"64a2a95cbfdd1277c5811922acc8bb0a0e94b32d90c03168ca8bcfef2dd083e9"}]'::jsonb) LOOP
        SELECT f.*,l.lanname INTO p FROM pg_proc f JOIN pg_language l ON l.oid=f.prolang
            WHERE f.oid=to_regprocedure(item->>'signature');
        IF NOT FOUND OR p.lanname<>'plpgsql' OR NOT p.prosecdef OR p.provolatile<>'s' OR p.proisstrict
            OR p.proleakproof OR p.proparallel<>'u' OR p.proretset OR p.prokind<>'f' OR p.pronargdefaults<>0
            OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR p.proowner<>(SELECT nspowner FROM pg_namespace WHERE nspname='life_financial')
            OR p.prorettype<>(CASE p.proname WHEN 'spot_inventory_evidence' THEN 'jsonb'::regtype ELSE 'boolean'::regtype END)
            OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')<>item->>'sha256' THEN
            RAISE EXCEPTION 'LIFE_SETTLEMENT_BATCH_UNKNOWN_PREDECESSOR';END IF;
    END LOOP;
END; $$;
DO $$ DECLARE definition text;needle text;replacement text;BEGIN
    definition:=pg_get_functiondef('life_financial.spot_inventory_evidence(uuid,jsonb)'::regprocedure);
    needle:=$old$    FOR key IN SELECT value FROM jsonb_array_elements_text(p_members) ORDER BY value COLLATE "C" LOOP
        PERFORM key::life_financial.identity;
        SELECT * INTO t FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=key;
        IF NOT FOUND OR t.product<>'SPOT' OR t.state<>'TERMINAL' OR t.exchange_order_id IS NULL THEN
            RAISE EXCEPTION 'LIFE_SETTLEMENT_TERMINAL_REQUIRED' USING ERRCODE='P0001'; END IF;
        SELECT * INTO STRICT d FROM life_financial.spot_preparations WHERE account_id=p_account AND intent_id=key;
        SELECT * INTO g FROM life_financial.spot_accounting_progress WHERE account_id=p_account AND intent_id=key;
        IF NOT FOUND OR g.filled_base<=0 OR d.request->>'budget_scope'='EXIT'
            OR NOT EXISTS(SELECT 1 FROM life_financial.spot_terminal_facts WHERE account_id=p_account AND intent_id=key)
            OR EXISTS(SELECT 1 FROM life_financial.reservations WHERE account_id=p_account AND intent_id=key AND (state<>'TERMINAL' OR remaining<>0))
            OR g.filled_base<>(SELECT COALESCE(sum((observation->>'quantity_base_life')::numeric),0)
                FROM life_financial.spot_fill_facts WHERE account_id=p_account AND intent_id=key)
            OR EXISTS(SELECT 1 FROM life_financial.spot_fill_facts WHERE account_id=p_account AND intent_id=key AND exchange_order_id<>t.exchange_order_id) THEN
            RAISE EXCEPTION 'LIFE_SETTLEMENT_MEMBER_UNVERIFIED' USING ERRCODE='P0001'; END IF;$old$;
    replacement:=$new$    FOR row IN
        WITH selected AS MATERIALIZED (SELECT value AS key FROM jsonb_array_elements_text(p_members)),
        fill_totals AS MATERIALIZED (
            SELECT f.intent_id,sum((f.observation->>'quantity_base_life')::numeric) AS filled,
                bool_or(f.exchange_order_id<>a.exchange_order_id) AS wrong_order
            FROM life_financial.spot_fill_facts f JOIN selected x ON x.key=f.intent_id
            JOIN life_financial.send_attempts a ON a.account_id=p_account AND a.intent_id=f.intent_id
            WHERE f.account_id=p_account GROUP BY f.intent_id
        )
        SELECT x.key,a AS attempt,prep AS preparation,progress AS accounting,
            terminal.account_id IS NOT NULL AS has_terminal,COALESCE(f.filled,0) AS filled,
            COALESCE(f.wrong_order,false) AS wrong_order,
            EXISTS(SELECT 1 FROM life_financial.reservations h WHERE h.account_id=p_account AND h.intent_id=x.key
                AND (h.state<>'TERMINAL' OR h.remaining<>0)) AS held_reservation
        FROM selected x
        LEFT JOIN life_financial.send_attempts a ON a.account_id=p_account AND a.intent_id=x.key
        LEFT JOIN life_financial.spot_preparations prep ON prep.account_id=p_account AND prep.intent_id=x.key
        LEFT JOIN life_financial.spot_accounting_progress progress ON progress.account_id=p_account AND progress.intent_id=x.key
        LEFT JOIN life_financial.spot_terminal_facts terminal ON terminal.account_id=p_account AND terminal.intent_id=x.key
        LEFT JOIN fill_totals f ON f.intent_id=x.key ORDER BY x.key COLLATE "C" LOOP
        key:=row.key;t:=row.attempt;d:=row.preparation;g:=row.accounting;
        PERFORM key::life_financial.identity;
        IF t.account_id IS NULL OR t.product<>'SPOT' OR t.state<>'TERMINAL' OR t.exchange_order_id IS NULL THEN
            RAISE EXCEPTION 'LIFE_SETTLEMENT_TERMINAL_REQUIRED' USING ERRCODE='P0001';END IF;
        IF d.account_id IS NULL THEN RAISE EXCEPTION 'query returned no rows' USING ERRCODE='P0002';END IF;
        IF g.account_id IS NULL OR g.filled_base<=0 OR d.request->>'budget_scope'='EXIT'
            OR NOT row.has_terminal OR row.held_reservation OR g.filled_base<>row.filled OR row.wrong_order THEN
            RAISE EXCEPTION 'LIFE_SETTLEMENT_MEMBER_UNVERIFIED' USING ERRCODE='P0001';END IF;$new$;
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_SETTLEMENT_BATCH_UNKNOWN_PREDECESSOR';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;

CREATE OR REPLACE FUNCTION life_financial.spot_inventory_settlements_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE policy life_financial.spot_policies;valid boolean;
BEGIN
    IF NOT EXISTS(SELECT 1 FROM life_financial.spot_inventory_settlements WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.spot_inventory_members WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=p_account AND command->>'kind'='SPOT_INVENTORY_SETTLE') THEN RETURN true;END IF;
    SELECT p.* INTO STRICT policy FROM life_financial.spot_policies p JOIN life_financial.accounts a USING(account_id,policy_hash) WHERE a.account_id=p_account;
    IF EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=p_account AND o.command->>'kind'='SPOT_INVENTORY_SETTLE'
        AND NOT EXISTS(SELECT 1 FROM life_financial.spot_inventory_settlements registered WHERE registered.account_id=o.account_id AND registered.operation_id=o.operation_id))
        OR EXISTS(SELECT 1 FROM life_financial.spot_inventory_members m WHERE m.account_id=p_account AND NOT EXISTS(
            SELECT 1 FROM life_financial.spot_inventory_settlements registered WHERE registered.account_id=m.account_id AND registered.operation_id=m.operation_id AND registered.command->'intent_ids' ? m.intent_id)) THEN RETURN false;END IF;
    WITH cycles AS MATERIALIZED (
        SELECT s.*,life_financial.spot_inventory_evidence(p_account,s.command->'intent_ids') AS fresh,
            op.account_id AS original_account,op.command AS original_command,op.receipt,op.committed_version
        FROM life_financial.spot_inventory_settlements s LEFT JOIN life_financial.operations op USING(account_id,operation_id)
        WHERE s.account_id=p_account
    ), member_counts AS MATERIALIZED (
        SELECT m.operation_id,count(*) AS n FROM life_financial.spot_inventory_members m WHERE m.account_id=p_account GROUP BY m.operation_id
    ), members AS MATERIALIZED (
        SELECT c.operation_id,x.key,c.fresh,m.account_id,m.operation_id AS member_operation,m.actual_usdt,m.held_before_usdt,
            d.account_id AS preparation_account,d.prepared_at,t.account_id AS attempt_account,t.session_id
        FROM cycles c CROSS JOIN LATERAL jsonb_array_elements_text(c.command->'intent_ids') x(key)
        LEFT JOIN life_financial.spot_inventory_members m ON m.account_id=p_account AND m.intent_id=x.key
        LEFT JOIN life_financial.spot_preparations d ON d.account_id=p_account AND d.intent_id=x.key
        LEFT JOIN life_financial.send_attempts t ON t.account_id=p_account AND t.intent_id=x.key
    ), windows AS MATERIALIZED (
        SELECT m.*,scope.name AS scope_name,CASE scope.name WHEN 'CAMPAIGN' THEN policy.campaign_id
            WHEN 'DAY' THEN to_char(m.prepared_at AT TIME ZONE 'UTC','YYYY-MM-DD') ELSE m.session_id::text END AS bucket_key,
            encode(sha256(convert_to(m.operation_id||':'||m.key||':'||scope.name,'UTF8')),'hex') AS suffix
        FROM members m CROSS JOIN (VALUES('CAMPAIGN'),('DAY'),('SESSION')) scope(name)
    )
    SELECT NOT EXISTS(
        SELECT 1 FROM cycles c LEFT JOIN member_counts n USING(operation_id)
        WHERE c.original_account IS NULL OR c.original_command->>'kind' IS DISTINCT FROM 'SPOT_INVENTORY_SETTLE'
            OR c.original_command->'command' IS DISTINCT FROM c.command
            OR c.receipt->>'kind' IS DISTINCT FROM 'SPOT_INVENTORY_SETTLE' OR c.receipt->>'state' IS DISTINCT FROM 'SETTLED'
            OR c.receipt->>'operation_id' IS DISTINCT FROM c.operation_id OR c.receipt->>'cycle_id' IS DISTINCT FROM c.cycle_id
            OR c.command->>'operation_id' IS DISTINCT FROM c.operation_id OR c.command->>'cycle_id' IS DISTINCT FROM c.cycle_id
            OR c.receipt->'trading_permission' IS DISTINCT FROM 'false'::jsonb OR c.receipt->'replayed' IS DISTINCT FROM 'false'::jsonb
            OR (c.receipt->>'version')::bigint IS DISTINCT FROM c.committed_version
            OR c.fresh IS DISTINCT FROM c.evidence OR c.receipt->'actuals_usdt' IS DISTINCT FROM c.fresh->'actuals'
            OR c.receipt->>'proof_digest' IS DISTINCT FROM c.fresh->>'proof'
            OR COALESCE(n.n,0)<>jsonb_array_length(c.command->'intent_ids')
    ) AND NOT EXISTS(
        SELECT 1 FROM members m WHERE m.account_id IS NULL OR m.preparation_account IS NULL OR m.attempt_account IS NULL
            OR m.member_operation<>m.operation_id OR m.actual_usdt<>(m.fresh#>>ARRAY['actuals',m.key])::numeric
            OR m.held_before_usdt<>(m.fresh#>>ARRAY['held_before',m.key])::numeric
    ) AND NOT EXISTS(
        SELECT 1 FROM windows w
        LEFT JOIN life_financial.budget_events r ON r.account_id=p_account AND r.event_id='settle-release:'||w.suffix
            AND r.family='SUBSIDY' AND r.scope=w.scope_name AND r.window_key=w.bucket_key AND r.kind='RELEASE' AND r.amount=w.held_before_usdt
        LEFT JOIN life_financial.budget_events s ON s.account_id=p_account AND s.event_id='settle-spend:'||w.suffix
            AND s.family='SUBSIDY' AND s.scope=w.scope_name AND s.window_key=w.bucket_key AND s.kind='SPEND' AND s.amount=w.actual_usdt
        WHERE r.account_id IS NULL OR s.account_id IS NULL
    ) INTO valid;
    RETURN valid;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
