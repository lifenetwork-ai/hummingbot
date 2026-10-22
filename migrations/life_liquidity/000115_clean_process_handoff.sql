-- Explicit offline clean handoff. No account, key, grant or policy bootstrap.
-- Only the owner-side Ed25519 verifier may import a signed HANDOFF record and
-- independently pinned credential-exclusion assertion. Runtime receives no DML.
CREATE TABLE life_financial.process_handoffs (
    account_id uuid NOT NULL REFERENCES life_financial.process_lease_policies,
    operation_id life_financial.identity NOT NULL,grant_id uuid NOT NULL,predecessor_grant uuid NOT NULL,
    predecessor_epoch bigint NOT NULL CHECK(predecessor_epoch>0 AND predecessor_epoch<9223372036854775807),
    body jsonb NOT NULL CHECK(jsonb_typeof(body)='object' AND octet_length(body::text)<=131072),
    source_hash life_financial.digest NOT NULL,expires_at timestamptz NOT NULL CHECK(isfinite(expires_at)),
    registered_at timestamptz NOT NULL CHECK(isfinite(registered_at) AND registered_at<expires_at),
    PRIMARY KEY(account_id,operation_id),UNIQUE(account_id,grant_id),
    FOREIGN KEY(account_id,grant_id) REFERENCES life_financial.process_lease_registrations DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(account_id,predecessor_grant) REFERENCES life_financial.process_lease_registrations,
    CHECK(grant_id<>predecessor_grant)
);
ALTER TABLE life_financial.process_handoffs ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped_read ON life_financial.process_handoffs FOR SELECT USING(life_financial.can_access(account_id));
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.process_handoffs
    FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record();

CREATE FUNCTION life_financial.process_handoff_guard(p_account uuid,p_grant uuid,p_predecessor uuid,p_epoch bigint,p_claim boolean DEFAULT true) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE h life_financial.process_handoffs;a life_financial.accounts;r life_financial.process_lease_registrations;
    config jsonb;state jsonb;cert jsonb;payload jsonb;at timestamptz;
BEGIN
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    SELECT * INTO h FROM life_financial.process_handoffs WHERE account_id=p_account AND grant_id=p_grant;
    IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_PROCESS_HANDOFF_REQUIRED';END IF;
    SELECT * INTO STRICT r FROM life_financial.process_lease_registrations WHERE account_id=p_account AND grant_id=p_grant;
    SELECT body INTO config FROM life_financial.configurations WHERE account_id=p_account AND config_hash=a.config_hash;
    at:=clock_timestamp();cert:=h.body#>'{exclusion,body}';payload:=h.body->'payload';
    IF p_claim IS FALSE THEN
        -- An accepted claim consumes the fresh assertion once. Ongoing guards
        -- verify its immutable origin at constant cost; they neither rescan
        -- all handoffs nor re-authorize from an expired operator assertion.
        IF a.environment<>'OFFLINE_SIMULATION' OR h.predecessor_grant IS DISTINCT FROM p_predecessor OR h.predecessor_epoch IS DISTINCT FROM p_epoch
            OR h.source_hash<>encode(sha256(convert_to(h.body::text,'UTF8')),'hex')
            OR h.body->'operator_policy' IS DISTINCT FROM config->'operator_verifier'
            OR h.body->'credential_policy' IS DISTINCT FROM config->'credential_authority'
            OR h.body#>>'{credential_policy,protocol}' IS DISTINCT FROM 'OFFLINE_FAKE_V1'
            OR cert->>'target_capability_hash' IS DISTINCT FROM r.capability_hash::text
            OR cert#>'{target,owner}' IS DISTINCT FROM r.owner OR payload->>'target_grant' IS DISTINCT FROM p_grant::text
            OR payload->>'principal' IS DISTINCT FROM r.principal OR payload->'owner' IS DISTINCT FROM r.owner
            OR NOT EXISTS(SELECT 1 FROM life_financial.process_lease_commands WHERE account_id=p_account
                AND grant_id=p_predecessor AND lease_epoch=p_epoch AND kind='CLAIM') THEN
            RAISE EXCEPTION 'LIFE_PROCESS_HANDOFF_ORIGIN_UNQUALIFIED';END IF;
        RETURN;
    END IF;
    IF p_claim IS NULL OR a.environment<>'OFFLINE_SIMULATION' OR h.predecessor_grant<>p_predecessor OR h.predecessor_epoch<>p_epoch
        OR p_claim AND (at<h.registered_at OR at>=h.expires_at OR at>=(h.body#>>'{approval,expires_at}')::timestamptz)
        OR (SELECT count(*) FROM jsonb_object_keys(h.body))<>8
        OR NOT h.body ?& ARRAY['payload','approval','signature','cut','facts','operator_policy','credential_policy','exclusion']
        OR ((h.body#>>'{approval,issued_at}')::timestamptz<=h.registered_at) IS NOT TRUE
        OR (h.registered_at<(h.body#>>'{approval,expires_at}')::timestamptz) IS NOT TRUE
        OR (config#>ARRAY['operator_verifier',h.body#>>'{approval,operator_id}','roles'] ? 'HANDOFF') IS NOT TRUE
        OR h.source_hash<>encode(sha256(convert_to(h.body::text,'UTF8')),'hex')
        OR h.body->'operator_policy' IS DISTINCT FROM config->'operator_verifier'
        OR h.body->'credential_policy' IS DISTINCT FROM config->'credential_authority'
        OR h.body#>>'{credential_policy,protocol}' IS DISTINCT FROM 'OFFLINE_FAKE_V1'
        OR cert->>'protocol' IS DISTINCT FROM 'OFFLINE_FAKE_V1'
        OR cert->>'provider_id' IS DISTINCT FROM config#>>'{credential_authority,provider_id}'
        OR cert->>'database_lineage' IS DISTINCT FROM a.database_lineage::text
        OR cert->>'predecessor_grant' IS DISTINCT FROM p_predecessor::text
        OR cert->>'predecessor_epoch' IS DISTINCT FROM p_epoch::text
        OR cert->>'target_capability_hash' IS DISTINCT FROM r.capability_hash::text
        OR cert#>'{target,owner}' IS DISTINCT FROM r.owner
        OR cert#>>'{source,owner,process_id}' IS NOT DISTINCT FROM cert#>>'{target,owner,process_id}'
        OR cert#>>'{source,owner,generation}' IS NOT DISTINCT FROM cert#>>'{target,owner,generation}'
        OR cert#>>'{source,service_identity}' IS NOT DISTINCT FROM cert#>>'{target,service_identity}'
        OR cert#>>'{source,credential_resource}' IS NOT DISTINCT FROM cert#>>'{target,credential_resource}'
        OR cert->'rest_denied' IS DISTINCT FROM 'true'::jsonb OR cert->'ws_denied' IS DISTINCT FROM 'true'::jsonb
        OR cert->'caa_disabled' IS DISTINCT FROM 'true'::jsonb OR cert->'inflight_reconciled' IS DISTINCT FROM 'true'::jsonb
        OR ((cert->>'revocation_requested_at')::timestamptz<=(cert->>'revocation_effective_at')::timestamptz) IS NOT TRUE
        OR ((cert->>'revocation_effective_at')::timestamptz<=(cert->>'observed_at')::timestamptz) IS NOT TRUE
        OR ((cert->>'observed_at')::timestamptz<=h.registered_at) IS NOT TRUE
        OR ((h.body#>>'{facts,observed_at}')::timestamptz>=(cert->>'observed_at')::timestamptz) IS NOT TRUE
        OR ((h.body#>>'{facts,observed_at}')::timestamptz<=h.registered_at) IS NOT TRUE
        OR p_claim AND extract(epoch FROM(at-(h.body#>>'{facts,observed_at}')::timestamptz))*1000>
            (SELECT max_fact_age_ms FROM life_financial.control_policies WHERE account_id=p_account)
        OR (cert->>'expires_at')::timestamptz IS DISTINCT FROM h.expires_at
        OR payload->>'kind' IS DISTINCT FROM 'PROCESS_HANDOFF_V1' OR payload->>'operation_id' IS DISTINCT FROM h.operation_id::text
        OR payload->>'target_grant' IS DISTINCT FROM p_grant::text OR payload->>'account_id' IS DISTINCT FROM p_account::text
        OR payload->>'database_lineage' IS DISTINCT FROM a.database_lineage::text
        OR payload->>'principal' IS DISTINCT FROM r.principal OR payload->'owner' IS DISTINCT FROM r.owner
        OR payload->>'capability_hash' IS DISTINCT FROM r.capability_hash::text
        OR h.body#>>'{approval,role}' IS DISTINCT FROM 'HANDOFF'
        OR h.body#>>'{approval,request_digest}' IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(
            jsonb_build_object('command',payload,'cut',h.body->'cut','facts',h.body->'facts')),'UTF8')),'hex')
        OR payload->>'exclusion_digest' IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(h.body->'exclusion'),'UTF8')),'hex')
        OR h.body#>'{approval,binding}' IS DISTINCT FROM cert->'binding'
        OR cert->'binding' IS DISTINCT FROM h.body#>'{cut,checkpoint,binding}'
        OR cert#>'{source,owner}' IS DISTINCT FROM (SELECT owner FROM life_financial.process_lease_registrations
            WHERE account_id=p_account AND grant_id=p_predecessor)
        OR p_epoch>1 AND cert->'source' IS DISTINCT FROM (SELECT body#>'{exclusion,body,target}' FROM life_financial.process_handoffs
            WHERE account_id=p_account AND grant_id=p_predecessor)
        OR NOT EXISTS(SELECT 1 FROM life_financial.process_lease_commands WHERE account_id=p_account
            AND grant_id=p_predecessor AND lease_epoch=p_epoch AND kind='CLAIM')
        OR NOT life_financial.control_verified(p_account) THEN RAISE EXCEPTION 'LIFE_PROCESS_HANDOFF_UNQUALIFIED';END IF;
    IF NOT p_claim THEN RETURN;END IF;
    state:=life_financial.spot_state(p_account);
    IF (h.body#>>'{cut,checkpoint,revision}')::bigint<>a.version
        OR h.body#>>'{cut,checkpoint,state_digest}' IS DISTINCT FROM state->>'digest'
        OR NOT life_financial.control_facts_qualified(p_account,h.body->'facts') THEN
        RAISE EXCEPTION 'LIFE_PROCESS_HANDOFF_CUT_OR_FACTS_UNQUALIFIED';END IF;
END; $$;

-- Patch only exact immutable predecessor bodies; keep entry OIDs and ACLs.
DO $$ DECLARE item record;p record;BEGIN
    FOR item IN SELECT * FROM (VALUES
        ('life_financial.process_lease_command(uuid,text,text,uuid)','5aca1dcd4f0607d947a5c1ed2b7b211f3316d7f6045615e01e60e280821ba9c8','jsonb'),
        ('life_financial.process_lease_commit()','e85c1fc445c163a7d6706876c43f1887e63b0bccb67e6955670d485e919de71a','trigger'),
        ('life_financial.process_lease_guard(uuid,boolean)','f4b4dd158621e0de0a8c35ea8b4f9ff3ed3c2507d5e0ac3677cc08460747e6d8','void')) AS x(signature,source_hash,result_type) LOOP
        SELECT f.*,l.lanname INTO STRICT p FROM pg_proc f JOIN pg_language l ON l.oid=f.prolang WHERE f.oid=item.signature::regprocedure;
        IF p.lanname<>'plpgsql' OR NOT p.prosecdef OR p.provolatile<>'v' OR p.proisstrict OR p.proleakproof
            OR p.proparallel<>'u' OR p.proretset OR p.prokind<>'f' OR p.pronargdefaults<>0
            OR p.proowner<>(SELECT nspowner FROM pg_namespace WHERE nspname='life_financial')
            OR p.prorettype<>item.result_type::regtype OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') IS DISTINCT FROM item.source_hash THEN
            RAISE EXCEPTION 'LIFE_PROCESS_HANDOFF_MIGRATION_MISMATCH';END IF;
    END LOOP;
END; $$;
DO $$ DECLARE p record;definition text;needle text;replacement text;BEGIN
    SELECT * INTO STRICT p FROM pg_proc WHERE oid='life_financial.process_lease_command(uuid,text,text,uuid)'::regprocedure;
    IF NOT p.prosecdef OR p.provolatile<>'v' OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
        OR p.proowner<>(SELECT nspowner FROM pg_namespace WHERE nspname='life_financial')
        OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')<>'5aca1dcd4f0607d947a5c1ed2b7b211f3316d7f6045615e01e60e280821ba9c8' THEN
        RAISE EXCEPTION 'LIFE_PROCESS_HANDOFF_MIGRATION_MISMATCH';END IF;
    definition:=pg_get_functiondef(p.oid);
    needle:='IF FOUND THEN RAISE EXCEPTION ''LIFE_PROCESS_LEASE_HANDOFF_REQUIRED'';END IF;';
    replacement:='IF FOUND THEN PERFORM life_financial.process_handoff_guard(p_account,p_grant,h.grant_id,h.lease_epoch);';
    replacement:=replacement||'h.lease_epoch:=h.lease_epoch+1;ELSE h.lease_epoch:=1;END IF;';
    IF strpos(definition,needle)=0 OR strpos(definition,'h.lease_epoch:=1;')=0 THEN RAISE EXCEPTION 'LIFE_PROCESS_HANDOFF_MIGRATION_MISMATCH';END IF;
    definition:=replace(definition,'h.lease_epoch:=1;','NULL; -- Epoch chosen by the explicit first/successor branch.');
    definition:=replace(definition,needle,replacement);
    needle:='INSERT INTO life_financial.process_leases VALUES(p_account,p_grant,h.lease_epoch,at,until,p_operation);';
    replacement:='INSERT INTO life_financial.process_leases VALUES(p_account,p_grant,h.lease_epoch,at,until,p_operation) ON CONFLICT(account_id) DO UPDATE '
        ||'SET grant_id=EXCLUDED.grant_id,lease_epoch=EXCLUDED.lease_epoch,observed_at=EXCLUDED.observed_at,expires_at=EXCLUDED.expires_at,last_operation=EXCLUDED.last_operation;';
    IF strpos(definition,needle)=0 THEN RAISE EXCEPTION 'LIFE_PROCESS_HANDOFF_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
    SELECT * INTO STRICT p FROM pg_proc WHERE oid='life_financial.process_lease_commit()'::regprocedure;
    IF NOT p.prosecdef OR p.provolatile<>'v' OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
        OR p.proowner<>(SELECT nspowner FROM pg_namespace WHERE nspname='life_financial')
        OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')<>'e85c1fc445c163a7d6706876c43f1887e63b0bccb67e6955670d485e919de71a' THEN
        RAISE EXCEPTION 'LIFE_PROCESS_HANDOFF_MIGRATION_MISMATCH';END IF;
    definition:=pg_get_functiondef(p.oid);
    needle:='PERFORM life_financial.process_lease_guard(NEW.account_id,false);';
    replacement:='IF NEW.lease_epoch>1 AND (SELECT kind FROM life_financial.process_lease_commands WHERE account_id=NEW.account_id AND operation_id=NEW.last_operation)=''CLAIM'' THEN '
        ||'PERFORM life_financial.process_handoff_guard(NEW.account_id,NEW.grant_id,'
        ||'(SELECT predecessor_grant FROM life_financial.process_handoffs WHERE account_id=NEW.account_id AND grant_id=NEW.grant_id),NEW.lease_epoch-1); END IF; '||needle;
    IF strpos(definition,needle)=0 THEN RAISE EXCEPTION 'LIFE_PROCESS_HANDOFF_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
    definition:=pg_get_functiondef('life_financial.process_lease_guard(uuid,boolean)'::regprocedure);
    needle:='secret:=current_setting(''life.process_capability'',true);at:=clock_timestamp();';
    replacement:='IF h.lease_epoch>1 THEN PERFORM life_financial.process_handoff_guard(p_account,h.grant_id,'
        ||'(SELECT predecessor_grant FROM life_financial.process_handoffs WHERE account_id=p_account AND grant_id=h.grant_id),h.lease_epoch-1,false); END IF; '||needle;
    IF strpos(definition,needle)=0 THEN RAISE EXCEPTION 'LIFE_PROCESS_HANDOFF_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;

-- Hostile default privileges must not turn a runtime/reporter into an issuer.
DO $$ DECLARE r record;BEGIN
    FOR r IN SELECT DISTINCT x.grantee FROM pg_class c CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) x
        WHERE c.oid='life_financial.process_handoffs'::regclass AND x.grantee<>c.relowner LOOP
        IF r.grantee=0 THEN REVOKE ALL ON life_financial.process_handoffs FROM PUBLIC;
        ELSE EXECUTE format('REVOKE ALL ON life_financial.process_handoffs FROM %I',pg_get_userbyid(r.grantee));END IF;
    END LOOP;
    FOR r IN SELECT DISTINCT x.grantee FROM pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE p.oid='life_financial.process_handoff_guard(uuid,uuid,uuid,bigint,boolean)'::regprocedure AND x.grantee<>p.proowner LOOP
        IF r.grantee=0 THEN REVOKE ALL ON FUNCTION life_financial.process_handoff_guard(uuid,uuid,uuid,bigint,boolean) FROM PUBLIC;
        ELSE EXECUTE format('REVOKE ALL ON FUNCTION life_financial.process_handoff_guard(uuid,uuid,uuid,bigint,boolean) FROM %I',pg_get_userbyid(r.grantee));END IF;
    END LOOP;
    FOR r IN SELECT DISTINCT x.grantee FROM pg_class c CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) x
        WHERE c.oid='life_financial.accounts'::regclass AND x.grantee<>0 AND x.grantee<>c.relowner AND x.privilege_type='SELECT' LOOP
        EXECUTE format('GRANT SELECT ON life_financial.process_handoffs TO %I',pg_get_userbyid(r.grantee));
    END LOOP;
END; $$;
