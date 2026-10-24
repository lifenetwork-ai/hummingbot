-- Disposable schema-134 candidate. Keep key lookups parameterized before
-- JSON equality predicates distort join cardinality. OFFSET 0 is a planner
-- boundary, not a row limit; all rows, predicates and COMMIT callers remain.
DO $$ DECLARE p pg_proc;definition text;needle text;replacement text;BEGIN
    SELECT * INTO p FROM pg_proc WHERE oid='life_financial.spot_actions_verified(uuid)'::regprocedure;
    IF p.prolang<>(SELECT oid FROM pg_language WHERE lanname='plpgsql') OR NOT p.prosecdef
        OR p.provolatile<>'s' OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
        OR p.proisstrict OR p.proleakproof OR p.proparallel<>'u' OR p.proretset OR p.prokind<>'f'
        OR p.pronargdefaults<>0 OR p.prorettype<>'boolean'::regtype
        OR p.proowner<>(SELECT nspowner FROM pg_namespace WHERE nspname='life_financial')
        OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')<>'8ea8ea77f2abba890795dd1978a54e016cde126f9edd889e79d02dd6d565ba5c' THEN
        RAISE EXCEPTION 'LIFE_ACTION_PROOF_BATCH_UNKNOWN_PREDECESSOR';END IF;
    definition:=pg_get_functiondef(p.oid);
    EXECUTE replace(definition,'FUNCTION life_financial.spot_actions_verified(',
        'FUNCTION life_financial.spot_actions_before_key_joins(');
    REVOKE ALL ON FUNCTION life_financial.spot_actions_before_key_joins(uuid) FROM PUBLIC;
    needle:=$old$life_financial.spot_action_descriptors d JOIN life_financial.spot_action_events e USING(account_id,action_id)
        JOIN life_financial.operations o USING(account_id,operation_id)$old$;
    replacement:=$new$life_financial.spot_action_descriptors d
        JOIN LATERAL (SELECT e.* FROM life_financial.spot_action_events e
            WHERE e.account_id=d.account_id AND e.action_id=d.action_id AND e.action_version=q.version OFFSET 0) e ON true
        JOIN LATERAL (SELECT o.* FROM life_financial.operations o
            WHERE o.account_id=e.account_id AND o.operation_id=e.operation_id OFFSET 0) o ON true$new$;
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_ACTION_PROOF_BATCH_UNKNOWN_PREDECESSOR';END IF;
    definition:=replace(definition,needle,replacement);
    needle:=$old$life_financial.send_attempts t JOIN life_financial.spot_preparations d USING(account_id,intent_id)
        JOIN life_financial.operations o ON o.account_id=t.account_id AND o.operation_id=t.operation_id
        JOIN life_financial.spot_action_descriptors x ON x.account_id=q.account_id AND x.action_id=q.action_id$old$;
    replacement:=$new$life_financial.send_attempts t
        JOIN LATERAL (SELECT d.* FROM life_financial.spot_preparations d
            WHERE d.account_id=t.account_id AND d.intent_id=t.intent_id OFFSET 0) d ON true
        JOIN LATERAL (SELECT o.* FROM life_financial.operations o
            WHERE o.account_id=t.account_id AND o.operation_id=t.operation_id OFFSET 0) o ON true
        JOIN LATERAL (SELECT x.* FROM life_financial.spot_action_descriptors x
            WHERE x.account_id=q.account_id AND x.action_id=q.action_id OFFSET 0) x ON true$new$;
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_ACTION_PROOF_BATCH_UNKNOWN_PREDECESSOR';END IF;
    definition:=replace(definition,needle,replacement);
    -- A different join order can reach a malformed JSON cast before a
    -- predecessor predicate rejected that row. Preserve the original result
    -- or data-error class by re-running the private original STABLE query.
    -- OTHERS/query cancellation/timeouts are deliberately not caught.
    IF (length(definition)-length(replace(definition,'END;','')))/length('END;')<>1 THEN
        RAISE EXCEPTION 'LIFE_ACTION_PROOF_BATCH_UNKNOWN_PREDECESSOR';END IF;
    EXECUTE replace(definition,'END;',E'EXCEPTION WHEN data_exception THEN\n    RETURN life_financial.spot_actions_before_key_joins(p_account);\nEND;');
END; $$;
