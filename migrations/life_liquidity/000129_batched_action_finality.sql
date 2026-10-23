-- Verify retained RECONCILED actions with one account-wide closure relation.
-- Original standalone closure APIs, lifecycle checks and COMMIT clocks remain.
DO $$ DECLARE p record;BEGIN
    FOR p IN SELECT f.*,l.lanname FROM pg_proc f JOIN pg_language l ON l.oid=f.prolang
        WHERE f.oid IN ('life_financial.spot_actions_verified(uuid)'::regprocedure,
            'life_financial.spot_action_closed_at(uuid,life_financial.identity)'::regprocedure,
            'life_financial.spot_action_closure_verified(uuid,life_financial.identity)'::regprocedure) LOOP
        IF NOT p.prosecdef OR p.provolatile<>'s' OR p.proisstrict OR p.proleakproof OR p.proparallel<>'u'
            OR p.proretset OR p.prokind<>'f' OR p.pronargdefaults<>0
            OR p.proowner<>(SELECT nspowner FROM pg_namespace WHERE nspname='life_financial')
            OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR p.lanname<>(CASE WHEN p.proname='spot_actions_verified' THEN 'plpgsql' ELSE 'sql' END)
            OR p.prorettype<>(CASE WHEN p.proname='spot_action_closed_at' THEN 'timestamptz'::regtype ELSE 'boolean'::regtype END)
            OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') IS DISTINCT FROM (CASE p.proname
                WHEN 'spot_actions_verified' THEN 'eba036f199794fd21f1aa53f5e437deb56b7a450c40b17cbf20ccdd93247beae'
                WHEN 'spot_action_closed_at' THEN 'a1b80c9eb19b228d13b6a647a0136a4f280eaae72d1d3c2555da600dd69014e1'
                WHEN 'spot_action_closure_verified' THEN '4ed5ad93b55220d5d75eab5f7d71dcf6bb8931ad45e025c0dac082aa31bf9b7f' END) THEN
            RAISE EXCEPTION 'LIFE_ACTION_FINALITY_UNKNOWN_PREDECESSOR';END IF;
    END LOOP;
END; $$;
DO $$ DECLARE definition text;needle text;replacement text;BEGIN
    definition:=pg_get_functiondef('life_financial.spot_actions_verified(uuid)'::regprocedure);
    needle:=$old$NOT EXISTS(SELECT 1 FROM life_financial.quote_actions q WHERE q.account_id=p_account AND q.state='RECONCILED'
    AND NOT life_financial.spot_action_closure_verified(q.account_id,q.action_id))$old$;
    replacement:=$new$NOT EXISTS(
    WITH closing AS MATERIALIZED (
        SELECT origin.intent_id,max(o.recorded_at) AS closed_at FROM life_financial.operations o JOIN (
            SELECT e.account_id,e.operation_id,e.intent_id FROM life_financial.spot_lifecycle_events e
                JOIN life_financial.send_attempts t USING(account_id,intent_id)
                WHERE e.account_id=p_account AND e.result_state='ABORTED_BEFORE_SEND'
                    AND e.command->>'action'='ABORT_PREPARED' AND t.state='ABORTED_BEFORE_SEND'
            UNION ALL
            SELECT f.account_id,f.operation_id,f.intent_id FROM life_financial.spot_terminal_facts f
                JOIN life_financial.send_attempts t USING(account_id,intent_id)
                WHERE f.account_id=p_account AND t.state='TERMINAL'
        ) origin USING(account_id,operation_id) WHERE o.account_id=p_account GROUP BY origin.intent_id
    )
    SELECT 1 FROM life_financial.quote_actions q
    LEFT JOIN life_financial.send_attempts t ON t.account_id=q.account_id AND t.intent_id=q.action_id
        AND t.state IN ('TERMINAL','ABORTED_BEFORE_SEND')
    LEFT JOIN closing c ON c.intent_id=q.action_id
    WHERE q.account_id=p_account AND q.state='RECONCILED' AND (t.intent_id IS NULL
        OR EXISTS(SELECT 1 FROM life_financial.reservations r WHERE r.account_id=p_account AND r.intent_id=q.action_id
            AND (r.state<>'TERMINAL' OR r.remaining<>0))
        OR NOT COALESCE(isfinite(c.closed_at),false)))$new$;
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_ACTION_FINALITY_UNKNOWN_PREDECESSOR';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;
