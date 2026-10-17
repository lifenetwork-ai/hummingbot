-- Dedicated single-currency USDT cross-margin scope only. Collateral is a
-- constraint on the SAME settled wallet, not a second wallet or LIFE NAV.
-- A conservative haircut is allowed; unaccounted credit/loans are not. Funding
-- liability remains a separate deduction in shared_evaluate (never added here).
CREATE FUNCTION life_financial.joint_collateral_qualified(money jsonb,legs jsonb,source jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE cash numeric;collateral numeric;long_size numeric;short_size numeric;
    long_entry numeric;short_entry numeric;mark numeric;unrealized numeric;equity numeric;value numeric;k text;
BEGIN
    IF source->>'account_mode' IS DISTINCT FROM '2' OR source->>'collateral_currency' IS DISTINCT FROM 'USDT'
        OR source->>'margin_mode' IS DISTINCT FROM 'cross' OR source->'scope_complete' IS DISTINCT FROM 'true'::jsonb
        OR jsonb_typeof(money) IS DISTINCT FROM 'object' OR jsonb_typeof(legs) IS DISTINCT FROM 'object' THEN RETURN false;END IF;
    FOREACH k IN ARRAY ARRAY['quote','base'] LOOP
        IF jsonb_typeof(money->k) IS DISTINCT FROM 'string' THEN RETURN false;END IF;
        PERFORM (money->>k)::life_financial.amount;
    END LOOP;
    FOREACH k IN ARRAY ARRAY['long_base','short_base','long_entry','short_entry'] LOOP
        IF jsonb_typeof(legs->k) IS DISTINCT FROM 'string' THEN RETURN false;END IF;
        -- Weighted entries are decimal28, not 18-place wallet amounts.
        value:=(legs->>k)::numeric;
        IF value IS NULL OR NOT (value>'-Infinity'::numeric AND value<'Infinity'::numeric)
            OR value<0 OR value>=1e100::numeric OR scale(value)>200 THEN RETURN false;END IF;
    END LOOP;
    FOREACH k IN ARRAY ARRAY['spot_cash_quote','spot_life_base','long_base','short_base','collateral_quote','mark_price_usdt'] LOOP
        IF jsonb_typeof(source->k) IS DISTINCT FROM 'string' THEN RETURN false;END IF;
        PERFORM (source->>k)::life_financial.amount;
    END LOOP;
    cash:=(money->>'quote')::numeric;collateral:=(source->>'collateral_quote')::numeric;
    long_size:=(legs->>'long_base')::numeric;short_size:=(legs->>'short_base')::numeric;
    long_entry:=(legs->>'long_entry')::numeric;short_entry:=(legs->>'short_entry')::numeric;
    mark:=(source->>'mark_price_usdt')::numeric;
    IF (source->>'spot_cash_quote')::numeric<>cash OR (source->>'spot_life_base')::numeric<>(money->>'base')::numeric
        OR (source->>'long_base')::numeric<>long_size OR (source->>'short_base')::numeric<>short_size
        OR mark<=0 OR long_size>0 AND long_entry=0 OR short_size>0 AND short_entry=0 THEN RETURN false;END IF;
    -- Identical per-operation decimal28 half-even arithmetic to portfolio_measure
    -- and File JointRecovery. Neither LIFE value nor expected funding enters it.
    unrealized:=life_financial.joint_decimal28(
        life_financial.joint_decimal28(long_size*life_financial.joint_decimal28(mark-long_entry))
        +life_financial.joint_decimal28(short_size*life_financial.joint_decimal28(short_entry-mark)));
    -- A risk ceiling may round down, never invent collateral by rounding up.
    equity:=least(cash+long_size*(mark-long_entry)+short_size*(short_entry-mark),
                  life_financial.joint_decimal28(cash+unrealized));
    RETURN collateral<=greatest(0,equity);
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
REVOKE ALL ON FUNCTION life_financial.joint_collateral_qualified(jsonb,jsonb,jsonb) FROM PUBLIC;

-- Current admission is distinct from checking a final source during monetary
-- application: funding/fills must be able to change cash BEFORE it matches that
-- source. Do not add a cash-equality check to swap_snapshot_current, which is
-- also used inside those pre-application accounting paths.
CREATE FUNCTION life_financial.current_collateral_qualified(p_account uuid,snapshot jsonb) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM life_financial.portfolio_policies WHERE account_id=p_account) THEN RETURN true;END IF;
    RETURN NOT EXISTS(SELECT 1 FROM life_financial.balances WHERE account_id=p_account AND liability<>0)
        AND life_financial.joint_collateral_qualified(jsonb_build_object(
            'base',(SELECT total::text FROM life_financial.balances WHERE account_id=p_account AND asset='LIFE'),
            'quote',(SELECT total::text FROM life_financial.balances WHERE account_id=p_account AND asset='USDT')),
            life_financial.swap_fill_replay(p_account),snapshot);
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
REVOKE ALL ON FUNCTION life_financial.current_collateral_qualified(uuid,jsonb) FROM PUBLIC;

-- Preserve entrypoint OIDs/ACLs, original monetary replay and physical COMMIT
-- proofs. Reject drift instead of installing a partly patched financial gate.
DO $$ DECLARE definition text;needle text;replacement text;BEGIN
    definition:=pg_get_functiondef('life_financial.joint_batch_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'::regprocedure);
    needle:='        UPDATE life_financial.balances b SET total=CASE b.asset';
    replacement:='        IF EXISTS(SELECT 1 FROM life_financial.balances WHERE account_id=p_account AND liability<>0)
            OR NOT life_financial.joint_collateral_qualified(after_money,positions,source) THEN
            RAISE EXCEPTION ''LIFE_JOINT_COLLATERAL_UNQUALIFIED'' USING ERRCODE=''P0001'';END IF;
'||needle;
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_COLLATERAL_BATCH_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);

    definition:=pg_get_functiondef('life_financial.joint_batch_verified(uuid)'::regprocedure);
    needle:='IF b.money_after IS DISTINCT FROM money OR';
    replacement:='IF NOT life_financial.joint_collateral_qualified(money,positions,source)
                OR b.money_after IS DISTINCT FROM money OR';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_COLLATERAL_PROOF_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);

    definition:=pg_get_functiondef('life_financial.portfolio_basis(uuid,bigint)'::regprocedure);
    needle:='    RETURN jsonb_build_object(''money'',money,''positions'',legs';
    replacement:='    IF EXISTS(SELECT 1 FROM life_financial.balances WHERE account_id=p_account AND liability<>0)
        OR NOT life_financial.joint_collateral_qualified(money,legs,snapshot) THEN
        RAISE EXCEPTION ''LIFE_PORTFOLIO_COLLATERAL_UNQUALIFIED'' USING ERRCODE=''P0001'';END IF;
'||needle;
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_COLLATERAL_PORTFOLIO_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);

    definition:=pg_get_functiondef('life_financial.shared_capital_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'::regprocedure);
    needle:='IF NOT life_financial.swap_snapshot_current(p_account,snapshot) THEN';
    replacement:='IF NOT life_financial.swap_snapshot_current(p_account,snapshot)
        OR NOT life_financial.current_collateral_qualified(p_account,snapshot) THEN';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_COLLATERAL_ADMISSION_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);

    definition:=pg_get_functiondef('life_financial.carry_current_ready(uuid)'::regprocedure);
    needle:='RETURN life_financial.funding_carry_current(p_account,r.bundle)';
    replacement:=needle||' AND life_financial.current_collateral_qualified(p_account,r.bundle#>''{account,snapshot}'')';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_COLLATERAL_CURRENT_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);

    definition:=pg_get_functiondef('life_financial.shared_capital_commit_guard()'::regprocedure);
    needle:='IF NOT life_financial.shared_snapshot_valid(s,p.body,at_ms,p.max_claims)';
    replacement:=needle||' OR NOT life_financial.current_collateral_qualified(NEW.account_id,s)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_COLLATERAL_COMMIT_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;
