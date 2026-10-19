-- An authenticated application's source deadline must also hold during deferred
-- COMMIT work. This does not authenticate a GUC or decoded journal, grant any
-- permission, or replace existing native terminal/shared financial proofs.
CREATE FUNCTION life_financial.check_joint_source_deadline(p_account uuid,p_operation text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE j jsonb;o life_financial.operations;observed timestamptz;expires timestamptz;age integer;maximum integer;at timestamptz;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN
        RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID' USING ERRCODE='P0001';END IF;
    j:=current_setting('life.joint_source_deadline',true)::jsonb;
    IF j IS NULL OR jsonb_typeof(j)<>'object' OR (SELECT count(*) FROM jsonb_object_keys(j))<>6
        OR NOT j ?& ARRAY['operation_id','collection_id','source_hash','observed_at','expires_at','max_age_ms']
        OR j->>'operation_id' IS DISTINCT FROM p_operation
        OR jsonb_typeof(j->'max_age_ms')<>'number' OR j->>'max_age_ms' !~ '^[1-9][0-9]{0,4}$'
        OR EXISTS(SELECT 1 FROM jsonb_each(j) x WHERE x.key<>'max_age_ms' AND jsonb_typeof(x.value)<>'string')
        OR j->>'source_hash' !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'LIFE_JOINT_SOURCE_DEADLINE_INVALID' USING ERRCODE='P0001';END IF;
    PERFORM (j->>'collection_id')::life_financial.identity;
    observed:=(j->>'observed_at')::timestamptz;expires:=(j->>'expires_at')::timestamptz;age:=(j->>'max_age_ms')::integer;
    SELECT max_fact_age_ms INTO STRICT maximum FROM life_financial.spot_order_policies
        WHERE account_id=p_account AND policy_hash=(SELECT policy_hash FROM life_financial.accounts WHERE account_id=p_account);
    IF NOT isfinite(observed) OR NOT isfinite(expires) OR age NOT BETWEEN 1 AND least(maximum,60000)
        OR expires<>observed+make_interval(secs=>age/1000.0) THEN
        RAISE EXCEPTION 'LIFE_JOINT_SOURCE_DEADLINE_INVALID' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=p_operation;
    IF o.command->>'kind' IS NULL OR o.command->>'kind' NOT IN ('SPOT_TERMINAL','SWAP_TERMINAL','SHARED_RELEASE') THEN
        RAISE EXCEPTION 'LIFE_JOINT_SOURCE_CONSUMER_INVALID' USING ERRCODE='P0001';END IF;
    at:=clock_timestamp();
    IF at<observed OR at>=expires THEN
        RAISE EXCEPTION 'LIFE_JOINT_SOURCE_EXPIRED_AT_COMMIT' USING ERRCODE='P0001';END IF;
END; $$;

CREATE FUNCTION life_financial.joint_source_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE setting text;j jsonb;
BEGIN
    setting:=current_setting('life.joint_source_deadline',true);
    IF setting IS NULL OR setting='' THEN RETURN NEW;END IF;
    j:=setting::jsonb;
    IF NOT EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=NEW.account_id
        AND operation_id=j->>'operation_id' AND committed_version=NEW.version) THEN
        RAISE EXCEPTION 'LIFE_JOINT_SOURCE_CONSUMER_INVALID' USING ERRCODE='P0001';END IF;
    PERFORM life_financial.check_joint_source_deadline(NEW.account_id,j->>'operation_id');
    RETURN NEW;
END; $$;
CREATE CONSTRAINT TRIGGER zz_joint_source_account_commit AFTER UPDATE ON life_financial.accounts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN(NEW.version<>OLD.version)
    EXECUTE FUNCTION life_financial.joint_source_commit_guard();

-- Provision the read-only checker explicitly; neither function is public.
DO $$ DECLARE p regprocedure;r record;BEGIN
    FOREACH p IN ARRAY ARRAY['life_financial.check_joint_source_deadline(uuid,text)'::regprocedure,
        'life_financial.joint_source_commit_guard()'::regprocedure] LOOP
        FOR r IN SELECT DISTINCT x.grantee FROM pg_proc f
            CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
            WHERE f.oid=p AND x.grantee<>f.proowner LOOP
            IF r.grantee=0 THEN EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',p);
            ELSE EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',p,pg_get_userbyid(r.grantee));END IF;
        END LOOP;
    END LOOP;
END; $$;
