-- Keep the exact STABLE lifecycle/action/accounting queries and its independent callers/COMMIT
-- guards. PL/pgSQL retains the query plan per backend; no verdict is cached.
DO $$ DECLARE p record;definition text;body text;BEGIN
    FOR p IN SELECT f.*,l.lanname FROM pg_proc f JOIN pg_language l ON l.oid=f.prolang
        WHERE f.oid IN (
            'life_financial.spot_lifecycle_verified(uuid)'::regprocedure,
            'life_financial.spot_actions_verified(uuid)'::regprocedure,
            'life_financial.spot_accounting_verified_before_repair(uuid)'::regprocedure) LOOP
    IF p.lanname<>'sql' OR p.provolatile<>'s' OR NOT p.prosecdef
        OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
        OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') IS DISTINCT FROM
            (CASE p.proname
                WHEN 'spot_lifecycle_verified' THEN '6be16b6382158a49fea7f66a93ecf74f5e629e594a5ecb84f6578d9bfe0c357c'
                WHEN 'spot_actions_verified' THEN '5f32dc4e818a99b8d10466f423127c0f843ed0dafe5fa59af0739b88a09006e4'
                WHEN 'spot_accounting_verified_before_repair' THEN 'b529d6e430d6616d1f3a39b0f2370024a958ba0faf0653151abfda8330a0eb34' END) THEN
        RAISE EXCEPTION 'LIFE_LIFECYCLE_PLAN_MIGRATION_MISMATCH';END IF;
    definition:=pg_get_functiondef(p.oid);
    body:='BEGIN'||chr(10)||'RETURN ('||rtrim(btrim(p.prosrc,E'\n\r\t '),';')||');'||chr(10)||'END;';
    IF (length(definition)-length(replace(definition,p.prosrc,'')))/length(p.prosrc)<>1
        OR (length(definition)-length(replace(definition,'LANGUAGE sql','')))/length('LANGUAGE sql')<>1 THEN
        RAISE EXCEPTION 'LIFE_LIFECYCLE_PLAN_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(replace(definition,p.prosrc,body),'LANGUAGE sql','LANGUAGE plpgsql');
END LOOP;
END; $$;
