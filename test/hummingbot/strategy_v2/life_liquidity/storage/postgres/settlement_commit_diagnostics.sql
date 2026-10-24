-- Test-only overlay for the frozen schema-134 settlement capacity diagnostic.
-- Preserve the public refusal code, OID, ACL, policy threshold and verifier.
-- Expose only a fixed reason code plus numeric fact age; never requests or IDs.
DO $$ DECLARE p record;BEGIN
    SELECT f.*,l.lanname INTO p FROM pg_proc f JOIN pg_language l ON l.oid=f.prolang
        WHERE f.oid=to_regprocedure('life_financial.spot_inventory_commit_guard(uuid,jsonb)');
    IF NOT FOUND OR p.lanname<>'plpgsql' OR NOT p.prosecdef OR p.provolatile<>'v' OR p.proisstrict
        OR p.proleakproof OR p.proparallel<>'u' OR p.prokind<>'f' OR p.pronargdefaults<>0
        OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog'] OR p.prorettype<>'void'::regtype
        OR p.proowner<>(SELECT nspowner FROM pg_namespace WHERE nspname='life_financial')
        OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')<>'b4349d0eab72466de885410da34bd895900a91b2005b7dd459486769ec50c569' THEN
        RAISE EXCEPTION 'LIFE_SETTLEMENT_DIAGNOSTICS_UNKNOWN_PREDECESSOR';END IF;
END; $$;

-- Pin the exact freshness predicate's shape in the test database, then add a
-- reason to its existing refusal. All values are sampled at the original
-- decision point; the age policy and statement ordering remain unchanged.
DO $$ DECLARE p record;definition text;needle text;replacement text;BEGIN
    SELECT f.*,l.lanname INTO p FROM pg_proc f JOIN pg_language l ON l.oid=f.prolang
        WHERE f.oid=to_regprocedure('life_financial.spot_settle_inventory(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb,jsonb,boolean)');
    IF NOT FOUND OR p.lanname<>'plpgsql' OR NOT p.prosecdef OR p.provolatile<>'v' OR p.proisstrict
        OR p.proleakproof OR p.proparallel<>'u' OR p.prokind<>'f' OR p.pronargdefaults<>0
        OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog'] OR p.prorettype<>'jsonb'::regtype
        OR p.proowner<>(SELECT nspowner FROM pg_namespace WHERE nspname='life_financial') THEN
        RAISE EXCEPTION 'LIFE_SETTLEMENT_DIAGNOSTICS_UNKNOWN_PREDECESSOR';END IF;
    definition:=pg_get_functiondef(p.oid);
    needle:=$needle$observed:=(p_facts->>'observed_at')::timestamptz; at:=clock_timestamp();
    IF NOT isfinite(observed) OR observed>at OR extract(epoch FROM at-observed)*1000>policy.max_fact_age_ms
        OR observed<COALESCE((SELECT max((o.command#>>'{command,observed_at}')::timestamptz) FROM life_financial.operations o
            WHERE o.account_id=p_account AND o.command->>'kind' IN ('SPOT_FILL','SPOT_CASHFLOW')),(p_owner->>'valid_from')::timestamptz) THEN
        RAISE EXCEPTION 'LIFE_SETTLEMENT_FACTS_UNQUALIFIED' USING ERRCODE='P0001'; END IF;$needle$;
    IF (length(p.prosrc)-length(replace(p.prosrc,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_SETTLEMENT_DIAGNOSTICS_UNKNOWN_PREDECESSOR';END IF;
    replacement:=$replacement$observed:=(p_facts->>'observed_at')::timestamptz; at:=clock_timestamp();
    IF NOT isfinite(observed) THEN reason:='SETTLEMENT_FACTS_NONFINITE';
    ELSE
        age_ms:=extract(epoch FROM at-observed)*1000;
        IF observed>at THEN reason:='SETTLEMENT_FACTS_FUTURE';
        ELSIF age_ms>policy.max_fact_age_ms THEN reason:='SETTLEMENT_FACTS_EXPIRED';
        ELSIF observed<COALESCE((SELECT max((o.command#>>'{command,observed_at}')::timestamptz)
            FROM life_financial.operations o WHERE o.account_id=p_account
            AND o.command->>'kind' IN ('SPOT_FILL','SPOT_CASHFLOW')),(p_owner->>'valid_from')::timestamptz) THEN
            reason:='SETTLEMENT_FACTS_OLDER_THAN_LATEST';
        END IF;
    END IF;
    IF reason IS NOT NULL THEN
        RAISE EXCEPTION 'LIFE_SETTLEMENT_FACTS_UNQUALIFIED' USING ERRCODE='P0001',
            DETAIL=jsonb_build_object('format','LIFE_SETTLEMENT_FACTS_PRECHECK_V1','reason',reason,
                'age_ms',age_ms,'max_fact_age_ms',policy.max_fact_age_ms)::text;
    END IF;$replacement$;
    definition:=replace(definition,'observed timestamptz; at timestamptz;',
        'observed timestamptz; at timestamptz; age_ms numeric; reason text;');
    IF (length(definition)-length(replace(definition,'age_ms numeric; reason text;','')))/length('age_ms numeric; reason text;')<>1 THEN
        RAISE EXCEPTION 'LIFE_SETTLEMENT_DIAGNOSTICS_UNKNOWN_PREDECESSOR';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;

CREATE OR REPLACE FUNCTION life_financial.spot_inventory_commit_guard(p_account uuid,p_request jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE policy life_financial.spot_settlement_policies;observed timestamptz;at timestamptz;age_ms numeric;reason text;
BEGIN
    SELECT p.* INTO STRICT policy FROM life_financial.spot_settlement_policies p JOIN life_financial.accounts a USING(account_id,policy_hash) WHERE a.account_id=p_account;
    observed:=(p_request#>>'{facts,observed_at}')::timestamptz;at:=clock_timestamp();
    age_ms:=CASE WHEN isfinite(observed) THEN extract(epoch FROM at-observed)*1000 END;
    -- Keep the schema-134 predicate and three-valued NULL semantics. Its
    -- lifecycle verifier already includes settlement (migration 127).
    IF NOT isfinite(observed) OR observed>at OR age_ms>policy.max_fact_age_ms
        OR NOT life_financial.spot_lifecycle_verified(p_account) THEN
        IF NOT isfinite(observed) THEN reason:='SETTLEMENT_FACTS_NONFINITE';
        ELSIF observed>at THEN reason:='SETTLEMENT_FACTS_FUTURE';
        ELSIF age_ms>policy.max_fact_age_ms THEN reason:='SETTLEMENT_FACTS_EXPIRED';
        ELSE reason:='SETTLEMENT_PROJECTION_INVALID';END IF;
        RAISE EXCEPTION 'LIFE_SETTLEMENT_FACTS_OR_PROJECTION_INVALID_AT_COMMIT'
            USING ERRCODE='P0001',DETAIL=jsonb_build_object('format','LIFE_SETTLEMENT_COMMIT_V1','reason',reason,
                'age_ms',age_ms,'max_fact_age_ms',policy.max_fact_age_ms)::text;
    END IF;
END; $$;
