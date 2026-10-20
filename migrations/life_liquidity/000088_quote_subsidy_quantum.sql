-- Prospective subsidy holds round UP to the existing scale-18 money contract.
-- Preserve exact inputs, budgets, recorded history, OIDs/ACLs and all age gates.
DO $$ DECLARE p record;definition text;needle text;replacement text;seen integer:=0;BEGIN
    needle:='subsidy:=CASE WHEN p.objective=''liquidity_service'' THEN greatest(0,-net) ELSE 0 END;';
    replacement:='subsidy:=CASE WHEN p.objective=''liquidity_service'' THEN ceil(greatest(0,-net)*1000000000000000000)/1000000000000000000 ELSE 0 END;';
    FOR p IN SELECT oid,proname,prosrc,provolatile,prosecdef,proconfig FROM pg_proc WHERE oid IN (
        'life_financial.guard_quote_admission(uuid,jsonb,jsonb,jsonb,jsonb)'::regprocedure,
        'life_financial.guard_quote_admission_core(uuid,jsonb,jsonb,jsonb,jsonb,boolean)'::regprocedure) LOOP
        seen:=seen+1;
        IF p.provolatile<>'v' OR NOT p.prosecdef OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') IS DISTINCT FROM (CASE p.proname
                WHEN 'guard_quote_admission' THEN 'bf29eff7460e3feea115b59fb5e986d22cedde34f35370939bde7904b5997da7'
                WHEN 'guard_quote_admission_core' THEN 'ec7616705cbdd7a20d136b0c2c28aeb07b48be33676e29c7b7a0eac343585878' END) THEN
            RAISE EXCEPTION 'LIFE_QUOTE_QUANTUM_MIGRATION_MISMATCH';END IF;
        definition:=pg_get_functiondef(p.oid);
        IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
            RAISE EXCEPTION 'LIFE_QUOTE_QUANTUM_MIGRATION_MISMATCH';END IF;
        EXECUTE replace(definition,needle,replacement);
    END LOOP;
    IF seen<>2 THEN RAISE EXCEPTION 'LIFE_QUOTE_QUANTUM_MIGRATION_MISMATCH';END IF;
END; $$;
