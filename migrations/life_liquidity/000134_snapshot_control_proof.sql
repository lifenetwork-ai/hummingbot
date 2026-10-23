-- The same STABLE primary-state invocation currently audits signed logical
-- control separately for its result and three aggregate flags. Share that
-- exact fresh result; no saved proof crosses a SQL statement or COMMIT.
DO $$
DECLARE signature text;p pg_proc;definition text;needle text;replacement text;
BEGIN
    FOREACH signature IN ARRAY ARRAY['life_financial.spot_state(uuid)','life_financial.control_verified(uuid)'] LOOP
        SELECT * INTO p FROM pg_proc WHERE oid=to_regprocedure(signature);
        IF NOT FOUND OR p.prolang<>(SELECT oid FROM pg_language WHERE lanname='plpgsql')
            OR NOT p.prosecdef OR p.proisstrict OR p.proleakproof OR p.proparallel<>'u'
            OR p.proretset OR p.prokind<>'f' OR p.pronargdefaults<>0 OR p.provolatile<>'s'
            OR p.proowner<>(SELECT nspowner FROM pg_namespace WHERE nspname='life_financial')
            OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR p.prorettype<>(CASE WHEN p.proname='spot_state' THEN 'jsonb'::regtype ELSE 'boolean'::regtype END)
            OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') IS DISTINCT FROM (CASE p.proname
                WHEN 'spot_state' THEN '6e8521a2ce1436fb5c33fbca2b4b33bfd82ed5463e11a6f95f76ee68bcc96fd7'
                ELSE '156e82303cf755619e0b5d5647d2e59f822aa71e6c64b54091a2da9498771932' END) THEN
            RAISE EXCEPTION 'LIFE_CONTROL_VIEW_UNKNOWN_PREDECESSOR';END IF;
    END LOOP;
    definition:=pg_get_functiondef('life_financial.spot_state(uuid)'::regprocedure);
    needle:='DECLARE b jsonb;';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_CONTROL_VIEW_UNKNOWN_PREDECESSOR';END IF;
    definition:=replace(definition,needle,'DECLARE b jsonb;control_proof boolean;');
    needle:='life_financial.control_verified(p_account)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>4 THEN
        RAISE EXCEPTION 'LIFE_CONTROL_VIEW_UNKNOWN_PREDECESSOR';END IF;
    definition:=replace(definition,needle,'control_proof');
    needle:='IF EXISTS(SELECT 1 FROM life_financial.control_policies WHERE account_id=p_account) THEN';
    replacement:=needle||E'\n        control_proof:=life_financial.control_verified(p_account);';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_CONTROL_VIEW_UNKNOWN_PREDECESSOR';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;
