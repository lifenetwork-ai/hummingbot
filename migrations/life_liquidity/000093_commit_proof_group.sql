-- One fresh post-write STABLE invocation for the related capital/carry/NAV/
-- release constraints. Every original predicate and real-clock check remains.
-- No pre-write verdict, persistent cache, GUC verdict or caller boolean is used.
DO $$
DECLARE metadata record;source text;label text;body text:='';name text;signature text;r record;
BEGIN
    FOR metadata IN SELECT p.*,l.lanname FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang WHERE p.oid IN (
        'life_financial.carry_commit_guard()'::regprocedure,
        'life_financial.portfolio_commit_guard()'::regprocedure,
        'life_financial.shared_capital_commit_guard()'::regprocedure,
        'life_financial.shared_release_commit_guard()'::regprocedure,
        'life_financial.carry_verified(uuid)'::regprocedure,
        'life_financial.shared_capital_verified(uuid)'::regprocedure,
        'life_financial.portfolio_after_snapshot_verification(uuid,boolean,boolean)'::regprocedure,
        'life_financial.carry_ready_after_verification(uuid,boolean)'::regprocedure) LOOP
        IF metadata.lanname<>'plpgsql' OR NOT metadata.prosecdef
            OR metadata.provolatile<>(CASE WHEN metadata.proname LIKE '%commit_guard' THEN 'v' ELSE 's' END)
            OR metadata.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR encode(sha256(convert_to(metadata.prosrc,'UTF8')),'hex') IS DISTINCT FROM (CASE metadata.proname
                WHEN 'carry_commit_guard' THEN '5083136161e0c710e479741991f5e511183e0ed26731c272b61ac2d6656ce8b8'
                WHEN 'portfolio_commit_guard' THEN '04bda9bf126cb776e60770eaa408a10017b9d423fa178c7ecb4fa64d473c6a2f'
                WHEN 'shared_capital_commit_guard' THEN '677c66fb7b676dc0131d0e0c9fa7380561bfa0598d9aed4191e60e5f1b83ae2b'
                WHEN 'shared_release_commit_guard' THEN 'e70fb31e17e66ff99262add3c3ff6b8a2cf725c8d2d357dddc5defb8fb16bf40'
                WHEN 'carry_verified' THEN '88cdb8ede90ba0f9c315ddd40845b10ff5e7b4c7ac87ac0ccdb5a4e09faea2fb'
                WHEN 'shared_capital_verified' THEN '23705077bbe8105dafbafc9ff8985d22648b623ff3b46b0bdca30d5cc1bf74a3'
                WHEN 'portfolio_after_snapshot_verification' THEN '7db3a255289f69dbc9fe132eaaa4f07a93bfdfadd6b23e85012bef029abde71c'
                WHEN 'carry_ready_after_verification' THEN 'f2710c5b0a1f02349479988c587312c400dd7faa2f3297678b36cafc8418594f' END) THEN
            RAISE EXCEPTION 'LIFE_COMMIT_PROOF_GROUP_MIGRATION_MISMATCH';
        END IF;
    END LOOP;
    FOR name,signature IN SELECT * FROM (VALUES
        ('carry_account_commit','life_financial.carry_commit_guard()'),
        ('portfolio_account_commit','life_financial.portfolio_commit_guard()'),
        ('shared_capital_account_commit','life_financial.shared_capital_commit_guard()'),
        ('shared_release_account_commit','life_financial.shared_release_commit_guard()')) AS required(name,signature) LOOP
        IF NOT EXISTS(SELECT 1 FROM pg_trigger t WHERE t.tgrelid='life_financial.accounts'::regclass AND t.tgname=name
            AND t.tgfoid=to_regprocedure(signature) AND t.tgtype=17 AND t.tgenabled='O' AND t.tgconstraint<>0
            AND t.tgdeferrable AND t.tginitdeferred AND NOT t.tgisinternal AND t.tgnargs=0
            AND pg_get_triggerdef(t.oid) LIKE '% WHEN ((new.version <> old.version)) EXECUTE FUNCTION %') THEN
            RAISE EXCEPTION 'LIFE_COMMIT_PROOF_GROUP_TRIGGER_MISMATCH';END IF;
    END LOOP;
    FOREACH name IN ARRAY ARRAY['carry_commit_guard','portfolio_commit_guard','shared_capital_commit_guard','shared_release_commit_guard'] LOOP
        SELECT prosrc INTO STRICT source FROM pg_proc WHERE oid=to_regprocedure('life_financial.'||name||'()');
        label:=name||'_block';
        source:=replace(replace(replace(source,'NEW.account_id','p_account'),'NEW.version','p_version'),'RETURN NULL;','EXIT '||label||';');
        IF name='carry_commit_guard' THEN
            source:=replace(source,E'BEGIN\n',E'BEGIN\n    carry_ok:=life_financial.carry_verified(p_account);\n');
            source:=replace(source,'NOT life_financial.carry_verified(p_account)','NOT carry_ok');
            source:=replace(source,'life_financial.carry_current_ready(p_account)','life_financial.carry_ready_after_verification(p_account,carry_ok)');
        ELSIF name='portfolio_commit_guard' THEN
            source:=replace(source,E'BEGIN\n',E'BEGIN\n    shared_ok:=life_financial.shared_capital_verified(p_account);\n');
            source:=replace(source,'life_financial.portfolio_verified(p_account)',
                'life_financial.portfolio_after_snapshot_verification(p_account,carry_ok,shared_ok)');
        ELSIF name='shared_capital_commit_guard' THEN
            source:=replace(source,'life_financial.shared_capital_verified(p_account)','shared_ok');
        ELSE
            -- A true shared verdict with a head already proves release history.
            -- No-head/no-model shortcuts do not: keep their independent proof.
            source:=replace(source,'life_financial.shared_release_verified(p_account)',
                '(CASE WHEN shared_ok IS TRUE AND EXISTS(SELECT 1 FROM life_financial.shared_capital_heads WHERE account_id=p_account) THEN true ELSE life_financial.shared_release_verified(p_account) END)');
        END IF;
        body:=body||'<<'||label||'>>'||chr(10)||source||chr(10);
    END LOOP;
    EXECUTE 'CREATE FUNCTION life_financial.capital_carry_portfolio_commit_check(p_account uuid,p_version bigint) RETURNS void
        LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $fn$
        DECLARE carry_ok boolean;shared_ok boolean;BEGIN '||body||E'\nEND;$fn$';
    -- The caller cannot supply or recover intermediate verdicts. This wrapper
    -- is invoked only by the actual deferred account-row constraint trigger.
    CREATE FUNCTION life_financial.capital_carry_portfolio_commit_guard() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$ BEGIN
        IF TG_RELID<>'life_financial.accounts'::regclass OR TG_OP<>'UPDATE' OR TG_LEVEL<>'ROW' THEN
            RAISE EXCEPTION 'LIFE_COMMIT_PROOF_GROUP_CONTEXT_INVALID';END IF;
        PERFORM life_financial.capital_carry_portfolio_commit_check(NEW.account_id,NEW.version);
        RETURN NULL;
    END; $fn$;
    FOR r IN SELECT DISTINCT x.grantee,p.oid::regprocedure::text AS signature FROM pg_proc p
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x WHERE p.oid IN (
            'life_financial.capital_carry_portfolio_commit_check(uuid,bigint)'::regprocedure,
            'life_financial.capital_carry_portfolio_commit_guard()'::regprocedure) AND x.grantee<>p.proowner LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %s',r.signature,
            CASE WHEN r.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(r.grantee)) END);
    END LOOP;
    DROP TRIGGER carry_account_commit ON life_financial.accounts;
    DROP TRIGGER portfolio_account_commit ON life_financial.accounts;
    DROP TRIGGER shared_capital_account_commit ON life_financial.accounts;
    DROP TRIGGER shared_release_account_commit ON life_financial.accounts;
    CREATE CONSTRAINT TRIGGER capital_carry_portfolio_at_commit AFTER UPDATE ON life_financial.accounts
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN(NEW.version<>OLD.version)
        EXECUTE FUNCTION life_financial.capital_carry_portfolio_commit_guard();
END; $$;
