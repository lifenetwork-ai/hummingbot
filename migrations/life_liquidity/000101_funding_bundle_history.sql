-- A funding observation validates the same current bundle against two prior
-- histories. Keep the first complete validation, then check only the second
-- predecessor relation. All predecessor membership, coverage, sequence, time
-- and same-sequence mutation checks remain, inside the same function call.
-- No saved verdict, cross-statement reuse, deadline or permission change.
DO $$ DECLARE p record;BEGIN
    FOR p IN SELECT f.*,l.lanname FROM pg_proc f JOIN pg_language l ON l.oid=f.prolang
        WHERE f.oid IN (
            'life_financial.carry_bundle_reason(jsonb,jsonb,jsonb,jsonb,jsonb,bigint,integer)'::regprocedure,
            'life_financial.funding_bundle_reason(uuid,jsonb,text,text,bigint)'::regprocedure) LOOP
        IF p.lanname<>'plpgsql' OR p.provolatile<>(CASE WHEN p.proname='carry_bundle_reason' THEN 'i' ELSE 's' END)
            OR p.prosecdef<>(p.proname='funding_bundle_reason') OR p.proisstrict OR p.proleakproof
            OR p.proparallel<>'u' OR p.proretset OR p.prokind<>'f' OR p.pronargdefaults<>0
            OR p.prorettype<>'text'::regtype OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') IS DISTINCT FROM (CASE p.proname
                WHEN 'carry_bundle_reason' THEN 'fff2552e2e86b4195408e0c0f5481f807d674b33642867d6f94f9891317671f4'
                WHEN 'funding_bundle_reason' THEN 'efdf7b915bec038da5cd0cfd88a1ed0a02e08eabf4111e7733008f5a7150972f' END) THEN
            RAISE EXCEPTION 'LIFE_FUNDING_HISTORY_MIGRATION_MISMATCH';END IF;
    END LOOP;
END; $$;

CREATE FUNCTION life_financial.funding_prior_bundle_reason(b jsonb,previous jsonb,at_ms bigint) RETURNS text
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE k text;value jsonb;past jsonb;stamp jsonb;old jsonb;
BEGIN
    IF EXISTS(SELECT 1 FROM jsonb_array_elements(previous#>'{funding,payments}') x WHERE NOT EXISTS(
        SELECT 1 FROM jsonb_array_elements(b#>'{funding,payments}') y WHERE y=x)) THEN
        RETURN 'CARRY_FUNDING_HISTORY_OR_ACCOUNT_MISMATCH';END IF;
    IF previous IS NOT NULL AND ((b#>>'{funding,coverage_through_ms}')::bigint<(previous#>>'{funding,coverage_through_ms}')::bigint
        OR (previous#>>'{funding,settlement_ms}')::bigint<=at_ms AND (b#>>'{funding,coverage_through_ms}')::bigint<(previous#>>'{funding,settlement_ms}')::bigint) THEN
        RETURN 'CARRY_SETTLEMENT_COVERAGE_GAP';END IF;
    FOREACH k IN ARRAY ARRAY['account','mark','index','funding','terms'] LOOP
        value:=b->k;past:=previous->k;
        stamp:=CASE WHEN k='account' THEN value->'snapshot' ELSE value END;
        old:=CASE WHEN k='account' THEN past->'snapshot' ELSE past END;
        IF past IS NOT NULL AND ((stamp->>'sequence')::bigint<(old->>'sequence')::bigint
            OR (stamp->>'observed_at_ms')::bigint<(old->>'observed_at_ms')::bigint
            OR stamp->'sequence'=old->'sequence' AND value<>past) THEN RETURN 'CARRY_STREAM_REPLAY_OR_MUTATION';END IF;
    END LOOP;
    RETURN 'CARRY_QUALIFIED';
EXCEPTION WHEN OTHERS THEN RETURN 'CARRY_FUNDING_OR_TERMS_UNQUALIFIED';
END; $$;

DO $$ DECLARE r record;definition text;needle text;replacement text;BEGIN
    -- Even deployment-wide default EXECUTE grants cannot expose this partial
    -- validator. Runtime/reporters still enter through the full original API.
    FOR r IN SELECT DISTINCT x.grantee FROM pg_proc f
        CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
        WHERE f.oid='life_financial.funding_prior_bundle_reason(jsonb,jsonb,bigint)'::regprocedure
            AND x.grantee<>f.proowner LOOP
        IF r.grantee=0 THEN
            REVOKE ALL ON FUNCTION life_financial.funding_prior_bundle_reason(jsonb,jsonb,bigint) FROM PUBLIC;
        ELSE
            EXECUTE format('REVOKE ALL ON FUNCTION life_financial.funding_prior_bundle_reason(jsonb,jsonb,bigint) FROM %I',pg_get_userbyid(r.grantee));
        END IF;
    END LOOP;
    definition:=pg_get_functiondef('life_financial.funding_bundle_reason(uuid,jsonb,text,text,bigint)'::regprocedure);
    needle:=$needle$IF previous_carry IS NOT NULL THEN
        SELECT bundle INTO STRICT prior FROM life_financial.carry_checks WHERE account_id=p_account AND operation_id=previous_carry AND qualified;
        result:=life_financial.carry_bundle_reason(carry_policy.body,capital_policy,b,prior,b#>'{account,snapshot}',at_ms,policy_row.max_payments);$needle$;
    replacement:=$replacement$IF previous_carry IS NOT NULL THEN
        SELECT bundle INTO STRICT prior FROM life_financial.carry_checks WHERE account_id=p_account AND operation_id=previous_carry AND qualified;
        result:=life_financial.funding_prior_bundle_reason(b,prior,at_ms);$replacement$;
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_FUNDING_HISTORY_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;
