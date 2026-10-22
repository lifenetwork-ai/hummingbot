-- Inline the exact historical-release predicate in its two hot history walks.
-- The planner can anti-join once instead of executing a SECURITY DEFINER SQL
-- function for each claim in each retained carry/capital operation. Every row,
-- original inclusive version cutoff and independent COMMIT proof remains.
-- No saved verdict, runtime grant, owner/source clock or accounting change.
DO $$ DECLARE p record;definition text;needle text;replacement text;BEGIN
    needle:='life_financial.shared_claim_active_at(p_account,c.intent_id,op.committed_version-1)';
    replacement:=$r$NOT EXISTS(SELECT 1 FROM life_financial.shared_claim_releases released
            JOIN life_financial.operations release_op USING(account_id,operation_id)
            WHERE released.account_id=p_account AND released.intent_id=c.intent_id
                AND release_op.committed_version<=op.committed_version-1)$r$;
    FOR p IN SELECT f.*,l.lanname FROM pg_proc f JOIN pg_language l ON l.oid=f.prolang
        WHERE f.oid IN ('life_financial.shared_operation_verified(uuid,text)'::regprocedure,
            'life_financial.carry_check_verified(uuid,text)'::regprocedure,
            'life_financial.shared_claim_active_at(uuid,text,bigint)'::regprocedure) LOOP
        IF p.lanname<>(CASE WHEN p.proname='shared_claim_active_at' THEN 'sql' ELSE 'plpgsql' END)
            OR p.provolatile<>'s' OR NOT p.prosecdef OR p.proisstrict OR p.proleakproof
            OR p.proparallel<>'u' OR p.proretset OR p.prokind<>'f' OR p.pronargdefaults<>0
            OR p.proowner<>(SELECT proowner FROM pg_proc WHERE oid='life_financial.shared_claim_active_at(uuid,text,bigint)'::regprocedure)
            OR p.prorettype<>'boolean'::regtype OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') IS DISTINCT FROM (CASE p.proname
                WHEN 'shared_operation_verified' THEN '842953e21691c26481426635411d0719775474416f4148bcf33cfe362de7f2af'
                WHEN 'carry_check_verified' THEN '5d32415bb65a6fb1f57d4932c7b063f44eecc2ed16887939d674af88d18f99db'
                WHEN 'shared_claim_active_at' THEN '338e22e1d1b55a68ecaf610d37210e273f66ef13ab80aa4af802485a77e93640' END) THEN
            RAISE EXCEPTION 'LIFE_HISTORY_MEMBERSHIP_MIGRATION_MISMATCH';END IF;
        IF p.proname<>'shared_claim_active_at' THEN
            definition:=pg_get_functiondef(p.oid);
            IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
                RAISE EXCEPTION 'LIFE_HISTORY_MEMBERSHIP_MIGRATION_MISMATCH';END IF;
            EXECUTE replace(definition,needle,replacement);
        END IF;
    END LOOP;
END; $$;
