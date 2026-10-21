-- Group two post-write account constraints in one fresh STABLE invocation.
-- Every original funding and swap-accounting verifier still runs. Only a true
-- result from the exact same spot_capital_verified call is reused within this
-- helper. Non-account history/operation triggers and all source clocks remain.
DO $$ DECLARE p record;name text;signature text;BEGIN
    FOR p IN SELECT f.*,l.lanname FROM pg_proc f JOIN pg_language l ON l.oid=f.prolang
        WHERE f.oid IN ('life_financial.funding_commit_guard()'::regprocedure,
            'life_financial.swap_fill_commit_guard()'::regprocedure,
            'life_financial.spot_capital_verified(uuid)'::regprocedure) LOOP
        IF p.lanname<>(CASE WHEN p.proname='spot_capital_verified' THEN 'sql' ELSE 'plpgsql' END)
            OR p.provolatile<>(CASE WHEN p.proname='spot_capital_verified' THEN 's' ELSE 'v' END)
            OR NOT p.prosecdef OR p.proisstrict OR p.proleakproof OR p.proparallel<>'u' OR p.proretset OR p.prokind<>'f'
            OR p.prorettype<>(CASE WHEN p.proname='spot_capital_verified' THEN 'boolean'::regtype ELSE 'trigger'::regtype END)
            OR p.pronargdefaults<>0 OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') IS DISTINCT FROM (CASE p.proname
                WHEN 'funding_commit_guard' THEN '3393d68f4d552a9296b3605f7b40d0157c43d9817f9b835e7d00dd0595cfd528'
                WHEN 'swap_fill_commit_guard' THEN '3b92f1b4c454f02da29aabaf188469d0048ddabb89f896cd772b76a84a08d883'
                WHEN 'spot_capital_verified' THEN '04bf3781547f69877b725a92d1489b59be785960fd2eaff15337fd94912b65b1' END) THEN
            RAISE EXCEPTION 'LIFE_ACCOUNT_MONEY_GROUP_MIGRATION_MISMATCH';END IF;
    END LOOP;
    FOR name,signature IN SELECT * FROM (VALUES
        ('funding_account_commit','life_financial.funding_commit_guard()'),
        ('swap_fill_at_commit','life_financial.swap_fill_commit_guard()')) required(name,signature) LOOP
        IF NOT EXISTS(SELECT 1 FROM pg_trigger t WHERE t.tgrelid='life_financial.accounts'::regclass
            AND t.tgname=name AND t.tgfoid=to_regprocedure(signature) AND t.tgtype=17 AND t.tgenabled='O'
            AND t.tgconstraint<>0 AND t.tgdeferrable AND t.tginitdeferred AND NOT t.tgisinternal AND t.tgnargs=0
            AND pg_get_triggerdef(t.oid) LIKE '% WHEN ((new.version <> old.version)) EXECUTE FUNCTION %') THEN
            RAISE EXCEPTION 'LIFE_ACCOUNT_MONEY_GROUP_TRIGGER_MISMATCH';END IF;
    END LOOP;
END; $$;

CREATE FUNCTION life_financial.account_money_commit_check(p_account uuid) RETURNS void
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE capital_ok boolean;
BEGIN
    IF NOT life_financial.funding_verified(p_account) THEN
        RAISE EXCEPTION 'LIFE_FUNDING_UNVERIFIED_AT_COMMIT' USING ERRCODE='P0001';END IF;
    IF EXISTS(SELECT 1 FROM life_financial.funding_accounting_policies WHERE account_id=p_account) THEN
        capital_ok:=life_financial.spot_capital_verified(p_account);
        IF NOT capital_ok THEN RAISE EXCEPTION 'LIFE_FUNDING_UNVERIFIED_AT_COMMIT' USING ERRCODE='P0001';END IF;
    END IF;
    IF NOT life_financial.swap_accounting_verified(p_account)
        OR EXISTS(SELECT 1 FROM life_financial.swap_fill_policies WHERE account_id=p_account)
            AND NOT (CASE WHEN capital_ok IS TRUE THEN true ELSE life_financial.spot_capital_verified(p_account) END) THEN
        RAISE EXCEPTION 'LIFE_SWAP_ACCOUNTING_UNVERIFIED_AT_COMMIT' USING ERRCODE='P0001';END IF;
END; $$;

CREATE FUNCTION life_financial.account_money_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
    IF TG_RELID<>'life_financial.accounts'::regclass OR TG_OP<>'UPDATE' OR TG_LEVEL<>'ROW' THEN
        RAISE EXCEPTION 'LIFE_ACCOUNT_MONEY_GROUP_CONTEXT_INVALID';END IF;
    PERFORM life_financial.account_money_commit_check(NEW.account_id);
    RETURN NULL;
END; $$;

DO $$ DECLARE r record;BEGIN
    FOR r IN SELECT DISTINCT x.grantee,p.oid::regprocedure::text AS signature FROM pg_proc p
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE p.oid IN ('life_financial.account_money_commit_check(uuid)'::regprocedure,
            'life_financial.account_money_commit_guard()'::regprocedure) AND x.grantee<>p.proowner LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %s',r.signature,
            CASE WHEN r.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(r.grantee)) END);
    END LOOP;
    DROP TRIGGER funding_account_commit ON life_financial.accounts;
    DROP TRIGGER swap_fill_at_commit ON life_financial.accounts;
    CREATE CONSTRAINT TRIGGER funding_account_commit AFTER UPDATE ON life_financial.accounts
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN(NEW.version<>OLD.version)
        EXECUTE FUNCTION life_financial.account_money_commit_guard();
END; $$;
