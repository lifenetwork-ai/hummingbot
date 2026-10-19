-- Reuse only the carry verdict built by this exact STABLE snapshot invocation.
-- Independent portfolio verification, mutations and COMMIT guards stay intact.
-- No session cache, stored permission, cross-command reuse or new runtime grant.
DO $$ DECLARE p record;definition text;needle text;replacement text;r record;BEGIN
    FOR p IN SELECT oid,proname,prosrc,provolatile,prosecdef,proconfig FROM pg_proc WHERE oid IN (
        'life_financial.portfolio_verified(uuid)'::regprocedure,
        'life_financial.spot_body_before_swap_terminal(uuid)'::regprocedure) LOOP
        IF p.provolatile<>'s' OR NOT p.prosecdef OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') IS DISTINCT FROM (CASE p.proname
                WHEN 'portfolio_verified' THEN '0029fb012806fc1b5128e863d6ebf1bb365f23e383a70a0165dce89c2ad6de27'
                WHEN 'spot_body_before_swap_terminal' THEN '1dc5fbb5b3045f0afdf7999e2479f0a15c08ff84e729a879491fdde9f48e0fc1' END) THEN
            RAISE EXCEPTION 'LIFE_PORTFOLIO_REUSE_MIGRATION_MISMATCH';END IF;
    END LOOP;
    definition:=pg_get_functiondef('life_financial.portfolio_verified(uuid)'::regprocedure);
    needle:='CREATE OR REPLACE FUNCTION life_financial.portfolio_verified(p_account uuid)';
    replacement:='CREATE FUNCTION life_financial.portfolio_after_carry_verification(p_account uuid, p_verified boolean)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1
        OR (length(definition)-length(replace(definition,'NOT life_financial.carry_verified(p_account)','')))
            /length('NOT life_financial.carry_verified(p_account)')<>1 THEN
        RAISE EXCEPTION 'LIFE_PORTFOLIO_REUSE_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(replace(definition,needle,replacement),
        'NOT life_financial.carry_verified(p_account)','p_verified IS DISTINCT FROM true');
    -- Only the schema owner may supply the intermediate verdict. Default
    -- privileges from deployment must not expose this helper to any caller.
    FOR r IN SELECT DISTINCT x.grantee FROM pg_proc f
        CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
        WHERE f.oid='life_financial.portfolio_after_carry_verification(uuid,boolean)'::regprocedure
        AND x.grantee<>f.proowner LOOP
        IF r.grantee=0 THEN
            REVOKE ALL ON FUNCTION life_financial.portfolio_after_carry_verification(uuid,boolean) FROM PUBLIC;
        ELSE
            EXECUTE format('REVOKE ALL ON FUNCTION life_financial.portfolio_after_carry_verification(uuid,boolean) FROM %I',pg_get_userbyid(r.grantee));
        END IF;
    END LOOP;
    definition:=pg_get_functiondef('life_financial.spot_body_before_swap_terminal(uuid)'::regprocedure);
    needle:='verified:=life_financial.portfolio_verified(p_account);';
    replacement:='verified:=life_financial.portfolio_after_carry_verification(p_account,(b#>>''{carry,verified}'')::boolean);';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_PORTFOLIO_REUSE_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;
