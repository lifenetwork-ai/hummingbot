-- Reuse only proofs already built by this exact STABLE primary snapshot.
-- Standalone verifiers, write commands and deferred COMMIT proofs are unchanged.
-- No stored verdict, caller-supplied permission or cross-statement cache.
DO $$ DECLARE p record;definition text;needle text;replacement text;r record;BEGIN
    FOR p IN SELECT oid,proname,prosrc,provolatile,prosecdef,proconfig FROM pg_proc WHERE oid IN (
        'life_financial.portfolio_after_carry_verification(uuid,boolean)'::regprocedure,
        'life_financial.spot_body_before_swap_terminal(uuid)'::regprocedure,
        'life_financial.spot_body_before_joint_batch(uuid)'::regprocedure,
        'life_financial.shared_capital_verified(uuid)'::regprocedure) LOOP
        IF p.provolatile<>'s' OR NOT p.prosecdef OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') IS DISTINCT FROM (CASE p.proname
                WHEN 'portfolio_after_carry_verification' THEN 'c419f3a303bafd5aab793432e3a6cfaf48bdbb1bde9534d78d478a939a3d10f0'
                WHEN 'spot_body_before_swap_terminal' THEN '8e4c18907d203add5168132e5592372efd2ba109336f9a1f020ea1e360f720c9'
                WHEN 'spot_body_before_joint_batch' THEN '0ffec9ba8eb7ec6a9e58944ec9dbcd0f3ceb77e76c5bdd8c5fb63b25b432d64b'
                WHEN 'shared_capital_verified' THEN '23705077bbe8105dafbafc9ff8985d22648b623ff3b46b0bdca30d5cc1bf74a3' END) THEN
            RAISE EXCEPTION 'LIFE_SNAPSHOT_SHARED_REUSE_MIGRATION_MISMATCH';END IF;
    END LOOP;
    definition:=pg_get_functiondef('life_financial.portfolio_after_carry_verification(uuid,boolean)'::regprocedure);
    needle:='CREATE OR REPLACE FUNCTION life_financial.portfolio_after_carry_verification(p_account uuid, p_verified boolean)';
    replacement:='CREATE FUNCTION life_financial.portfolio_after_snapshot_verification(p_account uuid, p_verified boolean, p_shared boolean)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1
        OR (length(definition)-length(replace(definition,'NOT life_financial.shared_capital_verified(p_account)','')))
            /length('NOT life_financial.shared_capital_verified(p_account)')<>1 THEN
        RAISE EXCEPTION 'LIFE_SNAPSHOT_SHARED_REUSE_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(replace(definition,needle,replacement),
        'NOT life_financial.shared_capital_verified(p_account)','p_shared IS DISTINCT FROM true');
    FOR r IN SELECT DISTINCT x.grantee FROM pg_proc f
        CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
        WHERE f.oid='life_financial.portfolio_after_snapshot_verification(uuid,boolean,boolean)'::regprocedure
        AND x.grantee<>f.proowner LOOP
        IF r.grantee=0 THEN
            REVOKE ALL ON FUNCTION life_financial.portfolio_after_snapshot_verification(uuid,boolean,boolean) FROM PUBLIC;
        ELSE
            EXECUTE format('REVOKE ALL ON FUNCTION life_financial.portfolio_after_snapshot_verification(uuid,boolean,boolean) FROM %I',pg_get_userbyid(r.grantee));
        END IF;
    END LOOP;
    definition:=pg_get_functiondef('life_financial.spot_body_before_swap_terminal(uuid)'::regprocedure);
    needle:='verified:=life_financial.portfolio_after_carry_verification(p_account,(b#>>''{carry,verified}'')::boolean);';
    replacement:='verified:=life_financial.portfolio_after_snapshot_verification(p_account,(b#>>''{carry,verified}'')::boolean,(b#>>''{shared_capital,verified}'')::boolean);';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_SNAPSHOT_SHARED_REUSE_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
    definition:=pg_get_functiondef('life_financial.spot_body_before_joint_batch(uuid)'::regprocedure);
    needle:='verified:=life_financial.shared_release_verified(p_account);';
    -- With an existing head, shared_capital_verified can return true only
    -- through its final shared_release_verified call. The no-head/no-model
    -- early returns do NOT prove release history: retain an independent check.
    replacement:='verified:=CASE WHEN (b#>>''{shared_capital,verified}'')::boolean IS TRUE AND jsonb_typeof(b#>''{shared_capital,head}'')=''object'' THEN true ELSE life_financial.shared_release_verified(p_account) END;';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_SNAPSHOT_SHARED_REUSE_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;
