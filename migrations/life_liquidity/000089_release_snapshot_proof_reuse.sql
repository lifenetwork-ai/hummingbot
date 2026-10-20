-- The release command already verified portfolio within its own local snapshot.
-- Reuse that verdict before the FIRST write; keep source freshness and every
-- independent deferred COMMIT proof. No cross-command cache or new privilege.
DO $$ DECLARE p record;definition text;needle text;replacement text;BEGIN
    SELECT oid,prosrc,provolatile,prosecdef,proconfig INTO STRICT p FROM pg_proc WHERE
        oid='life_financial.shared_release_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'::regprocedure;
    IF p.provolatile<>'v' OR NOT p.prosecdef OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
        OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') IS DISTINCT FROM
            '9aa5bc4212c953a9fdbffbbf377ed778cb33f88b70a01b09c5fbc383c4703f13' THEN
        RAISE EXCEPTION 'LIFE_RELEASE_REUSE_MIGRATION_MISMATCH';END IF;
    definition:=pg_get_functiondef(p.oid);
    needle:='IF NOT life_financial.portfolio_verified(p_account) OR NOT life_financial.portfolio_source_current';
    replacement:='IF (view#>>''{portfolio,verified}'')::boolean IS DISTINCT FROM true OR NOT life_financial.portfolio_source_current';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_RELEASE_REUSE_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;
