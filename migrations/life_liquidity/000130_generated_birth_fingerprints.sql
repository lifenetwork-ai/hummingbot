-- Deterministic current-body digests, not saved validity or permission.
-- Like operations.canonical_fingerprint, PostgreSQL recomputes each GENERATED
-- digest whenever body changes, even with user triggers disabled. Full origin,
-- policy, identity, structure, time, economic and COMMIT checks still run.
DO $migration$ DECLARE expected jsonb:='[{"signature":"life_financial.quote_birth_before_retirement_verified(uuid)","sha256":"f17514673a4edccd72745b483b2fe6710d2b3b66f5fb2bd30c01bcb75680e16c"},{"signature":"life_financial.quote_birth_verified(uuid)","sha256":"d9ffc8b3749a346298adb7d8fafc95f664de5f13aefc5319ea23d7c6c87bdc38"},{"signature":"life_financial.command_fingerprint_v1(jsonb)","sha256":"0f9ed04b4bc4e8953687b210b58d20b8223b99e9109758adedba51f0abb8639c"},{"signature":"life_financial.canonical_json_v1(jsonb)","sha256":"1ed0433191b81f7b4f54b6bab26c585bd0802cbec96335dd7afdf7238fd3eb7a"}]'::jsonb;item jsonb;p pg_proc;BEGIN
    FOR item IN SELECT value FROM jsonb_array_elements(expected) LOOP
        SELECT * INTO p FROM pg_proc WHERE oid=to_regprocedure(item->>'signature');
        IF NOT FOUND OR p.prolang<>(SELECT oid FROM pg_language WHERE lanname='plpgsql')
            OR p.provolatile<>(CASE WHEN p.proname IN ('command_fingerprint_v1','canonical_json_v1') THEN 'i' ELSE 's' END)
            OR p.prosecdef<>(p.proname NOT IN ('command_fingerprint_v1','canonical_json_v1'))
            OR p.proisstrict<>(p.proname IN ('command_fingerprint_v1','canonical_json_v1'))
            OR p.proowner<>(SELECT nspowner FROM pg_namespace WHERE nspname='life_financial')
            OR p.proleakproof OR p.proparallel<>'u' OR p.proretset OR p.prokind<>'f' OR p.pronargdefaults<>0
            OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR p.prorettype<>(CASE p.proname WHEN 'command_fingerprint_v1' THEN 'life_financial.digest'::regtype
                WHEN 'canonical_json_v1' THEN 'text'::regtype ELSE 'boolean'::regtype END)
            OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')<>item->>'sha256' THEN
            RAISE EXCEPTION 'LIFE_BIRTH_DIGEST_UNKNOWN_PREDECESSOR';END IF;
    END LOOP;
END; $migration$;
ALTER TABLE life_financial.quote_births ADD COLUMN canonical_body_fingerprint life_financial.digest
    GENERATED ALWAYS AS (life_financial.command_fingerprint_v1(body)) STORED;
ALTER TABLE life_financial.quote_birth_retirements ADD COLUMN canonical_body_fingerprint life_financial.digest
    GENERATED ALWAYS AS (life_financial.command_fingerprint_v1(body)) STORED;
DO $$ DECLARE signature text;definition text;needle text;BEGIN
    FOREACH signature IN ARRAY ARRAY['life_financial.quote_birth_before_retirement_verified(uuid)',
        'life_financial.quote_birth_verified(uuid)'] LOOP
        definition:=pg_get_functiondef(to_regprocedure(signature));
        needle:='encode(sha256(convert_to(life_financial.canonical_json_v1(b),''UTF8'')),''hex'')';
        IF signature='life_financial.quote_birth_verified(uuid)' THEN
            needle:='encode(sha256(convert_to(life_financial.canonical_json_v1(r.body),''UTF8'')),''hex'')';END IF;
        IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
            RAISE EXCEPTION 'LIFE_BIRTH_DIGEST_UNKNOWN_PREDECESSOR';END IF;
        EXECUTE replace(definition,needle,'r.canonical_body_fingerprint');
    END LOOP;
END; $$;
