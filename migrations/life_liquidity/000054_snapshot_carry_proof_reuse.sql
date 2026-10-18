-- Reuse the carry proof already evaluated by this same STABLE snapshot call.
-- No session cache, stored proof, cross-command reuse or COMMIT guard change.
-- The original carry_current_ready remains independent for every other caller.
DO $$
DECLARE definition text;needle text;replacement text;original_header text;new_header text;r record;
BEGIN
    definition:=pg_get_functiondef('life_financial.carry_current_ready(uuid)'::regprocedure);
    original_header:='CREATE OR REPLACE FUNCTION life_financial.carry_current_ready(p_account uuid)';
    new_header:='CREATE FUNCTION life_financial.carry_ready_after_verification(p_account uuid, p_verified boolean)';
    needle:='IF NOT life_financial.carry_verified(p_account) THEN RETURN false; END IF;';
    replacement:='IF p_verified IS DISTINCT FROM true THEN RETURN false; END IF;';
    IF (length(definition)-length(replace(definition,original_header,'')))/length(original_header)<>1
        OR (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1
        OR position('STABLE' IN definition)=0 OR position('SECURITY DEFINER' IN definition)=0 THEN
        RAISE EXCEPTION 'LIFE_CARRY_REUSE_MIGRATION_MISMATCH';
    END IF;
    EXECUTE replace(replace(definition,original_header,new_header),needle,replacement);
    -- Only the schema owner can supply this already verified intermediate.
    -- Runtime/reporting users still enter through the complete snapshot proof.
    REVOKE ALL ON FUNCTION life_financial.carry_ready_after_verification(uuid,boolean) FROM PUBLIC;
    FOR r IN SELECT x.grantee FROM pg_proc p
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE p.oid='life_financial.carry_ready_after_verification(uuid,boolean)'::regprocedure
            AND x.grantee<>p.proowner AND x.grantee<>0 LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION life_financial.carry_ready_after_verification(uuid,boolean) FROM %I',
            pg_get_userbyid(r.grantee));
    END LOOP;

    definition:=pg_get_functiondef('life_financial.spot_state_before_swap(uuid)'::regprocedure);
    needle:='verified:=life_financial.carry_verified(p_account); ready:=life_financial.carry_current_ready(p_account);';
    replacement:='verified:=life_financial.carry_verified(p_account); ready:=life_financial.carry_ready_after_verification(p_account,verified);';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1
        OR position('STABLE' IN definition)=0 OR position('SECURITY DEFINER' IN definition)=0 THEN
        RAISE EXCEPTION 'LIFE_CARRY_REUSE_SNAPSHOT_MISMATCH';
    END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;
