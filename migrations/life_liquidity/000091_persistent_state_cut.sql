-- A persisted state cut must not change when an unchanged carry feed expires.
-- Keep the complete returned readiness/recovery flags and every native
-- admission/COMMIT freshness guard. Only the hash input excludes the computed
-- carry.ready value and the redundant top-level recovery_required summary.
-- All durable inputs, verification flags, pending flags and actual money stay
-- in the cut. Old original-operation cuts remain immutable replay arguments.
DO $$ DECLARE p record;definition text;needle text;replacement text;BEGIN
    SELECT * INTO STRICT p FROM pg_proc WHERE oid='life_financial.spot_state(uuid)'::regprocedure;
    IF p.provolatile<>'s' OR NOT p.prosecdef OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
        OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') IS DISTINCT FROM
            'db6421c07ccb3bac8f85bb4a53d28301e6c1a612fa22372fa4847e4e03685f0f' THEN
        RAISE EXCEPTION 'LIFE_PERSISTENT_CUT_MIGRATION_MISMATCH';END IF;
    definition:=pg_get_functiondef(p.oid);
    needle:=$old$convert_to(b::text,'UTF8')$old$;
    replacement:=$new$convert_to('life-persistent-cut-v2:'||((b-'recovery_required')#-'{carry,ready}')::text,'UTF8')$new$;
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_PERSISTENT_CUT_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
    -- The owner-only, transaction-local repair helper independently rehashes
    -- the exact supplied snapshot. It must use the same versioned cut protocol.
    SELECT * INTO STRICT p FROM pg_proc WHERE oid=
        'life_financial.joint_batch_from_repair_view(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb,jsonb)'::regprocedure;
    IF p.provolatile<>'v' OR NOT p.prosecdef OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
        OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') IS DISTINCT FROM
            'e6bd9842176f7a36aa44ab59275ad3477a49f11f13a11bb763646ab2d5fc3215' THEN
        RAISE EXCEPTION 'LIFE_PERSISTENT_CUT_MIGRATION_MISMATCH';END IF;
    definition:=pg_get_functiondef(p.oid);
    needle:=$old$convert_to((p_view-'digest'-'observed_at')::text,'UTF8')$old$;
    replacement:=$new$convert_to('life-persistent-cut-v2:'||((p_view-ARRAY['digest','observed_at','recovery_required'])#-'{carry,ready}')::text,'UTF8')$new$;
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_PERSISTENT_CUT_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;
