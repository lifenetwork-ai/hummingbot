-- One account-wide terminal proof per product in each retained-release audit.
-- Every release still checks its own original operation, terminal, source,
-- version and pending history. Standalone proofs and later commands recheck.
DO $$ DECLARE p record;definition text;needle text;replacement text;r record;BEGIN
    FOR p IN SELECT f.*,l.lanname FROM pg_proc f JOIN pg_language l ON l.oid=f.prolang
        WHERE f.oid IN ('life_financial.shared_release_verified(uuid)'::regprocedure,
            'life_financial.shared_release_proof(uuid,text,text,bigint,bigint)'::regprocedure) LOOP
        IF p.lanname<>'plpgsql' OR p.provolatile<>'s' OR NOT p.prosecdef OR p.proisstrict
            OR p.proleakproof OR p.proparallel<>'u' OR p.proretset OR p.prokind<>'f' OR p.pronargdefaults<>0
            OR p.prorettype<>(CASE WHEN p.proname='shared_release_verified' THEN 'boolean'::regtype ELSE 'jsonb'::regtype END)
            OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') IS DISTINCT FROM (CASE p.proname
                WHEN 'shared_release_verified' THEN 'f3411dad952a6d7e8e3037d67ca75b29fccf81b09e40f1ed9248cb45effa5a6f'
                WHEN 'shared_release_proof' THEN '88cc403bc9d3539947ffb8b708020c7be4397aee418cf5e51723eca03f28295b' END) THEN
            RAISE EXCEPTION 'LIFE_RELEASE_TERMINAL_REUSE_MIGRATION_MISMATCH';END IF;
    END LOOP;
    definition:=pg_get_functiondef('life_financial.shared_release_proof(uuid,text,text,bigint,bigint)'::regprocedure);
    needle:='CREATE OR REPLACE FUNCTION life_financial.shared_release_proof(p_account uuid, p_intent text, p_terminal text, p_version bigint, at_ms bigint)';
    replacement:='CREATE FUNCTION life_financial.shared_release_after_terminal_proofs(p_account uuid, p_intent text, p_terminal text, p_version bigint, at_ms bigint, p_spot boolean, p_swap boolean)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1
        OR (length(definition)-length(replace(definition,'NOT life_financial.spot_lifecycle_verified(p_account)','')))/length('NOT life_financial.spot_lifecycle_verified(p_account)')<>1
        OR (length(definition)-length(replace(definition,'NOT life_financial.swap_terminal_verified(p_account)','')))/length('NOT life_financial.swap_terminal_verified(p_account)')<>1 THEN
        RAISE EXCEPTION 'LIFE_RELEASE_TERMINAL_REUSE_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(replace(replace(definition,needle,replacement),
        'NOT life_financial.spot_lifecycle_verified(p_account)','p_spot IS DISTINCT FROM true'),
        'NOT life_financial.swap_terminal_verified(p_account)','p_swap IS DISTINCT FROM true');
    -- A caller cannot supply terminal verdicts, including through hostile
    -- deployment default privileges. Only the original owner audit uses this.
    FOR r IN SELECT DISTINCT x.grantee FROM pg_proc f
        CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
        WHERE f.oid='life_financial.shared_release_after_terminal_proofs(uuid,text,text,bigint,bigint,boolean,boolean)'::regprocedure
            AND x.grantee<>f.proowner LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION life_financial.shared_release_after_terminal_proofs(uuid,text,text,bigint,bigint,boolean,boolean) FROM %s',
            CASE WHEN r.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(r.grantee)) END);
    END LOOP;
    definition:=pg_get_functiondef('life_financial.shared_release_verified(uuid)'::regprocedure);
    needle:='r life_financial.shared_claim_releases;';replacement:='r record;';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_RELEASE_TERMINAL_REUSE_MIGRATION_MISMATCH';END IF;
    definition:=replace(definition,needle,replacement);
    needle:='expected jsonb;';replacement:='expected jsonb;spot_ok boolean;swap_ok boolean;';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_RELEASE_TERMINAL_REUSE_MIGRATION_MISMATCH';END IF;
    definition:=replace(definition,needle,replacement);
    needle:='FOR r IN SELECT * FROM life_financial.shared_claim_releases WHERE account_id=p_account LOOP';
    replacement:=$replacement$FOR r IN SELECT releases.*,claims.product AS claim_product FROM life_financial.shared_claim_releases releases
        LEFT JOIN life_financial.shared_capital_claims claims USING(account_id,intent_id)
        WHERE releases.account_id=p_account LOOP
        -- Local variables belong to this STABLE invocation and this primary
        -- MVCC cut only. Missing claims remain in the loop and fail STRICT
        -- origin checks in the original per-release proof.
        IF r.claim_product='SPOT' AND spot_ok IS NULL THEN
            spot_ok:=life_financial.spot_lifecycle_verified(p_account);
        ELSIF r.claim_product='SWAP' AND swap_ok IS NULL THEN
            swap_ok:=life_financial.swap_terminal_verified(p_account);
        END IF;$replacement$;
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_RELEASE_TERMINAL_REUSE_MIGRATION_MISMATCH';END IF;
    definition:=replace(definition,needle,replacement);
    needle:='life_financial.shared_release_proof(p_account,r.intent_id,r.terminal_operation_id,o.committed_version-1,r.received_at_ms)';
    replacement:='life_financial.shared_release_after_terminal_proofs(p_account,r.intent_id,r.terminal_operation_id,o.committed_version-1,r.received_at_ms,spot_ok,swap_ok)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_RELEASE_TERMINAL_REUSE_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;
