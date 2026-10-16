-- Diagnose inconsistent preparation receipts before further financial writes.
-- This does not independently anchor history against a privileged DB owner.
CREATE FUNCTION life_financial.spot_prepare_receipts_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    RETURN NOT EXISTS(
        SELECT 1 FROM life_financial.spot_preparations d
        LEFT JOIN life_financial.operations o USING(account_id,operation_id)
        WHERE d.account_id=p_account AND (
            o.command->>'kind' IS DISTINCT FROM 'SPOT_PREPARE'
            OR o.command->'command' IS DISTINCT FROM d.request
            OR o.receipt IS DISTINCT FROM (jsonb_build_object(
                'kind','SPOT_PREPARE','operation_id',d.operation_id,'intent_id',d.intent_id,
                'state','PREPARED','version',o.committed_version,'replayed',false,'trading_permission',false)
                ||CASE WHEN o.command ? 'action' THEN jsonb_build_object(
                    'states',jsonb_build_array(jsonb_build_array(d.intent_id,'PREPARED'))) ELSE '{}'::jsonb END)));
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

ALTER FUNCTION life_financial.spot_state(uuid) RENAME TO spot_state_v14;
CREATE FUNCTION life_financial.spot_state(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE body jsonb; verified boolean;
BEGIN
    -- The previous entrypoint enforces account access before this private proof.
    body:=life_financial.spot_state_v14(p_account)-'digest'-'observed_at';
    verified:=life_financial.spot_prepare_receipts_verified(p_account);
    body:=body||jsonb_build_object('prepare_receipts_verified',verified,
        'financial_recovery_required',(body->>'financial_recovery_required')::boolean OR NOT verified,
        'recovery_required',(body->>'recovery_required')::boolean OR NOT verified,
        'unverified',(body->>'unverified')::boolean OR NOT verified);
    RETURN body||jsonb_build_object('digest',encode(sha256(convert_to(body::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
END; $$;

-- Preserve only previously approved current-snapshot EXECUTE rights across an
-- automatic upgrade. No role/account mapping or private proof is granted.
DO $$ DECLARE r record; BEGIN
    FOR r IN SELECT p.oid::regprocedure AS signature,x.grantee FROM pg_proc p
        JOIN pg_namespace n ON n.oid=p.pronamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE n.nspname='life_financial' AND p.proname='spot_state_v14' AND x.grantee<>p.proowner LOOP
        IF r.grantee=0 THEN EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',r.signature);
        ELSE
            EXECUTE format('GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid) TO %I',pg_get_userbyid(r.grantee));
            EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',r.signature,pg_get_userbyid(r.grantee));
        END IF;
    END LOOP;
END; $$;

CREATE FUNCTION life_financial.spot_prepare_receipt_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF NOT life_financial.spot_prepare_receipts_verified(NEW.account_id) THEN
        RAISE EXCEPTION 'LIFE_SPOT_PREPARE_RECEIPTS_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER spot_prepare_receipts_at_commit AFTER UPDATE ON life_financial.accounts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.spot_prepare_receipt_commit_guard();
-- Proof and old snapshot stay private. Only the current scoped snapshot is
-- provisioned to runtime/reporting by the central access provisioning routine.
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
