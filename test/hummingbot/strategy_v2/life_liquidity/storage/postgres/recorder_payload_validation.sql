-- Disposable candidate: validate EVERY historical payload, materialize the
-- normalized projection only for an applied head. No history verdict is saved.
DO $$ DECLARE p pg_proc;definition text;needle text;replacement text;BEGIN
    SELECT * INTO p FROM pg_proc WHERE oid='life_financial.recorder_executor_row(jsonb)'::regprocedure;
    IF p.provolatile<>'i' OR p.prosecdef OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
        OR p.prolang<>(SELECT oid FROM pg_language WHERE lanname='plpgsql')
        OR p.proowner<>(SELECT nspowner FROM pg_namespace WHERE nspname='life_financial')
        OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')<>'72a16c4680f6d0ec8b42ad083733a1c2d3d0eaab5ec41f23f20fa8a9bfffc30c' THEN
        RAISE EXCEPTION 'LIFE_RECORDER_PAYLOAD_UNKNOWN_PREDECESSOR';END IF;
    definition:=pg_get_functiondef(p.oid);
    needle:='FUNCTION life_financial.recorder_executor_row(p jsonb)';
    replacement:='FUNCTION life_financial.recorder_executor_checked_row(p jsonb, p_materialize boolean)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_RECORDER_PAYLOAD_UNKNOWN_PREDECESSOR';END IF;
    definition:=replace(definition,needle,replacement);
    definition:=replace(definition,'result jsonb:=p','result jsonb:=''{}''');
    needle:='result:=jsonb_set(result,ARRAY[k],to_jsonb(n::float8));';
    replacement:='IF p_materialize THEN result:=jsonb_set(result,ARRAY[k],to_jsonb(n::float8)); ELSE PERFORM n::float8; END IF;';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_RECORDER_PAYLOAD_UNKNOWN_PREDECESSOR';END IF;
    definition:=replace(definition,needle,replacement);
    needle:='result:=jsonb_set(result,''{close_timestamp}'',to_jsonb(n::bigint));';
    replacement:='IF p_materialize THEN result:=jsonb_set(result,''{close_timestamp}'',to_jsonb(n::bigint)); ELSE PERFORM n::bigint; END IF;';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_RECORDER_PAYLOAD_UNKNOWN_PREDECESSOR';END IF;
    definition:=replace(definition,needle,replacement);
    definition:=replace(definition,'RETURN result;','RETURN CASE WHEN p_materialize THEN p||result ELSE NULL END;');
    EXECUTE definition;
    REVOKE ALL ON FUNCTION life_financial.recorder_executor_checked_row(jsonb,boolean) FROM PUBLIC;
END; $$;

DO $$ DECLARE p pg_proc;definition text;needle text;replacement text;BEGIN
    SELECT * INTO p FROM pg_proc WHERE oid='life_financial.recorder_executor_only_integrity(uuid)'::regprocedure;
    IF p.provolatile<>'s' OR NOT p.prosecdef OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
        OR p.proowner<>(SELECT nspowner FROM pg_namespace WHERE nspname='life_financial')
        OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')<>'c53c2c7e7477f45a89fc71c6d345b5e1cb411719735031c98a80206b06ec41cf' THEN
        RAISE EXCEPTION 'LIFE_RECORDER_PAYLOAD_UNKNOWN_PREDECESSOR';END IF;
    definition:=pg_get_functiondef(p.oid);
    needle:='parsed.payload,life_financial.recorder_executor_row(parsed.payload) AS target';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_RECORDER_PAYLOAD_UNKNOWN_PREDECESSOR';END IF;
    definition:=replace(definition,needle,'parsed.payload');
    needle:='n.payload,n.target,';
    replacement:='n.payload,life_financial.recorder_executor_checked_row(n.payload,h.projection_id IS NOT NULL) AS target,';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_RECORDER_PAYLOAD_UNKNOWN_PREDECESSOR';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;
