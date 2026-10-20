-- A false aggregate unverified flag proves every earlier financial predicate
-- succeeded in this exact STABLE snapshot. Reuse only that positive proof;
-- invalid/NULL states still run the original independent verifier. No saved
-- verdict, runtime boolean, time-based readiness or write/COMMIT check changes.
DO $$ DECLARE p record;definition text;needle text;replacement text;r record;BEGIN
    FOR p IN SELECT f.*,l.lanname FROM pg_proc f JOIN pg_language l ON l.oid=f.prolang
        WHERE f.oid IN (
            'life_financial.spot_body_v7(uuid)'::regprocedure,
            'life_financial.spot_body_v13(uuid)'::regprocedure,
            'life_financial.spot_body_v14(uuid)'::regprocedure,
            'life_financial.spot_body_v15(uuid)'::regprocedure,
            'life_financial.spot_body_before_carry(uuid)'::regprocedure,
            'life_financial.spot_body_before_swap(uuid)'::regprocedure,
            'life_financial.spot_body_before_swap_accounting(uuid)'::regprocedure,
            'life_financial.spot_body_before_paid_funding(uuid)'::regprocedure,
            'life_financial.spot_body_before_portfolio(uuid)'::regprocedure,
            'life_financial.spot_body_before_swap_terminal(uuid)'::regprocedure,
            'life_financial.spot_accounting_verified(uuid)'::regprocedure,
            'life_financial.spot_accounting_verified_before_repair(uuid)'::regprocedure,
            'life_financial.portfolio_after_snapshot_verification(uuid,boolean,boolean)'::regprocedure) LOOP
        IF p.lanname<>(CASE WHEN p.proname='spot_accounting_verified' THEN 'sql' ELSE 'plpgsql' END)
            OR p.provolatile<>'s' OR NOT p.prosecdef OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') IS DISTINCT FROM (CASE p.proname
                WHEN 'spot_body_v7' THEN '3981bc3ce0400d6b17f7ac569b09ce1ccc49fdfa57e8b16c37d2a81fce561d52'
                WHEN 'spot_body_v13' THEN 'eb0c728ed4783fecfe03183e7186ef6c08b420d3b643571d79e04be2b1b37ce1'
                WHEN 'spot_body_v14' THEN 'f4a0c9f91aec90369d4a02d167f47e5f4261d648c349447f97251e5dbbde3f42'
                WHEN 'spot_body_v15' THEN '1dd88baeaf5f5c2faf2a89fa8619647745c2f0ffbc966f630d1750c4d56f24a8'
                WHEN 'spot_body_before_carry' THEN '4deb0a381a58e33368dab9472961c8e2745b06454ec2af2a2f806429782431ef'
                WHEN 'spot_body_before_swap' THEN '138275b1c8b0cc25037d5572df7d1a1b0a3f6392e666c295083b8e702227004e'
                WHEN 'spot_body_before_swap_accounting' THEN 'eeac85130747ae46bb13cc7c34ca56ec859bebae7ae399428160cca6b82d1cd4'
                WHEN 'spot_body_before_paid_funding' THEN '2c35791bf25b0ce846fc9b22bfe22c71590c8806ea7739d2e530b703b35ff500'
                WHEN 'spot_body_before_portfolio' THEN 'c733561294878468dad4b155dd291caa442ae35d63615077cad3451f5a2bc233'
                WHEN 'spot_body_before_swap_terminal' THEN '21de8250c01d281654066811ee9bc47713b63dfcaa3b906c334b7888ec9d5e95'
                WHEN 'spot_accounting_verified' THEN 'bac5c8986e4e816af48ecfaf76addff2d82e5a8d6bf6b7726426afb4813e03d4'
                WHEN 'spot_accounting_verified_before_repair' THEN '86b244fe058186093f8c39d65d89758e12683d66912d623f859ec21ed83f1a8a'
                WHEN 'portfolio_after_snapshot_verification' THEN '7db3a255289f69dbc9fe132eaaa4f07a93bfdfadd6b23e85012bef029abde71c'
            END) THEN RAISE EXCEPTION 'LIFE_SNAPSHOT_FINANCIAL_REUSE_MIGRATION_MISMATCH';END IF;
    END LOOP;
    definition:=pg_get_functiondef('life_financial.portfolio_after_snapshot_verification(uuid,boolean,boolean)'::regprocedure);
    needle:='CREATE OR REPLACE FUNCTION life_financial.portfolio_after_snapshot_verification(p_account uuid, p_verified boolean, p_shared boolean)';
    replacement:='CREATE FUNCTION life_financial.portfolio_after_financial_verification(p_account uuid, p_verified boolean, p_shared boolean, p_capital boolean)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1
        OR (length(definition)-length(replace(definition,'NOT life_financial.spot_capital_verified(p_account)','')))
            /length('NOT life_financial.spot_capital_verified(p_account)')<>1 THEN
        RAISE EXCEPTION 'LIFE_SNAPSHOT_FINANCIAL_REUSE_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(replace(definition,needle,replacement),'NOT life_financial.spot_capital_verified(p_account)','p_capital IS DISTINCT FROM true');
    FOR r IN SELECT DISTINCT x.grantee FROM pg_proc f
        CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
        WHERE f.oid='life_financial.portfolio_after_financial_verification(uuid,boolean,boolean,boolean)'::regprocedure AND x.grantee<>f.proowner LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION life_financial.portfolio_after_financial_verification(uuid,boolean,boolean,boolean) FROM %s',
            CASE WHEN r.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(r.grantee)) END);
    END LOOP;
    definition:=pg_get_functiondef('life_financial.spot_body_v13(uuid)'::regprocedure);
    needle:='life_financial.spot_actions_verified(p_account)';replacement:='(CASE WHEN body->''unverified'' IS NOT DISTINCT FROM ''false''::jsonb THEN true ELSE life_financial.spot_actions_verified(p_account) END)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_SNAPSHOT_FINANCIAL_REUSE_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
    definition:=pg_get_functiondef('life_financial.spot_body_before_swap_accounting(uuid)'::regprocedure);
    needle:='life_financial.swap_verified(p_account)';replacement:='(CASE WHEN b->''unverified'' IS NOT DISTINCT FROM ''false''::jsonb THEN true ELSE life_financial.swap_verified(p_account) END)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_SNAPSHOT_FINANCIAL_REUSE_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
    definition:=pg_get_functiondef('life_financial.spot_body_before_paid_funding(uuid)'::regprocedure);
    needle:='life_financial.swap_accounting_verified(p_account)';replacement:='(CASE WHEN b->''unverified'' IS NOT DISTINCT FROM ''false''::jsonb THEN true ELSE life_financial.swap_accounting_verified(p_account) END)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_SNAPSHOT_FINANCIAL_REUSE_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
    definition:=pg_get_functiondef('life_financial.spot_body_before_portfolio(uuid)'::regprocedure);
    needle:='life_financial.funding_verified(p_account)';replacement:='(CASE WHEN body->''unverified'' IS NOT DISTINCT FROM ''false''::jsonb THEN true ELSE life_financial.funding_verified(p_account) END)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_SNAPSHOT_FINANCIAL_REUSE_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
    definition:=pg_get_functiondef('life_financial.spot_body_before_swap_terminal(uuid)'::regprocedure);
    needle:='life_financial.portfolio_after_snapshot_verification(p_account,(b#>>''{carry,verified}'')::boolean,(b#>>''{shared_capital,verified}'')::boolean)';
    -- Choose the whole original helper on invalid/NULL states, preserving its
    -- early-return/short-circuit order rather than evaluating a capital argument
    -- before it can reject failed carry or an absent portfolio policy.
    replacement:='CASE WHEN b->''unverified'' IS NOT DISTINCT FROM ''false''::jsonb THEN life_financial.portfolio_after_financial_verification(p_account,(b#>>''{carry,verified}'')::boolean,(b#>>''{shared_capital,verified}'')::boolean,true) ELSE life_financial.portfolio_after_snapshot_verification(p_account,(b#>>''{carry,verified}'')::boolean,(b#>>''{shared_capital,verified}'')::boolean) END';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_SNAPSHOT_FINANCIAL_REUSE_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;
