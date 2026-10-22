-- Explicit offline observation delegation; no policy/account/key bootstrap.
-- A recovery grant is NOT a signed native ACTIVE record. The shared grant
-- registry stores its lifetime; this immutable subtype restricts it to facts.
CREATE TABLE life_financial.process_recovery_grants (
    account_id uuid NOT NULL REFERENCES life_financial.process_lease_policies,
    operation_id life_financial.identity NOT NULL,grant_id uuid NOT NULL,predecessor_grant uuid NOT NULL,
    predecessor_epoch bigint NOT NULL CHECK(predecessor_epoch>0 AND predecessor_epoch<9223372036854775807),
    body jsonb NOT NULL CHECK(jsonb_typeof(body)='object' AND octet_length(body::text)<=131072),
    source_hash life_financial.digest NOT NULL,registered_at timestamptz NOT NULL CHECK(isfinite(registered_at)),
    PRIMARY KEY(account_id,operation_id),UNIQUE(account_id,grant_id),CHECK(grant_id<>predecessor_grant),
    FOREIGN KEY(account_id,grant_id) REFERENCES life_financial.process_lease_registrations DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(account_id,predecessor_grant) REFERENCES life_financial.process_lease_registrations
);
ALTER TABLE life_financial.process_recovery_grants ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped_read ON life_financial.process_recovery_grants FOR SELECT USING(life_financial.can_access(account_id));
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.process_recovery_grants
    FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record();

CREATE FUNCTION life_financial.process_generation_identity(p_account uuid,p_grant uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT COALESCE((SELECT body#>'{exclusion,body,target}' FROM life_financial.process_handoffs WHERE account_id=p_account AND grant_id=p_grant),
        (SELECT body#>'{exclusion,body,target}' FROM life_financial.process_recovery_grants WHERE account_id=p_account AND grant_id=p_grant));
$$;

CREATE FUNCTION life_financial.process_recovery_origin(p_account uuid,p_grant uuid,p_fresh boolean) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE h life_financial.process_recovery_grants;a life_financial.accounts;r life_financial.process_lease_registrations;
    g life_financial.activation_grants;head life_financial.process_leases;
    config jsonb;state jsonb;cert jsonb;payload jsonb;native jsonb;source_owner jsonb;at timestamptz;
BEGIN
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    SELECT * INTO h FROM life_financial.process_recovery_grants WHERE account_id=p_account AND grant_id=p_grant;
    IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_RECOVERY_DELEGATION_REQUIRED';END IF;
    SELECT * INTO STRICT r FROM life_financial.process_lease_registrations WHERE account_id=p_account AND grant_id=p_grant;
    SELECT * INTO STRICT g FROM life_financial.activation_grants WHERE account_id=p_account AND grant_id=p_grant;
    SELECT body INTO config FROM life_financial.configurations WHERE account_id=p_account AND config_hash=a.config_hash;
    SELECT body INTO native FROM life_financial.control_records WHERE account_id=p_account AND operation_id=h.body->>'control_operation';
    at:=clock_timestamp();cert:=h.body#>'{exclusion,body}';payload:=h.body->'payload';source_owner:=h.body->'source_owner';
    IF p_fresh IS NULL OR a.environment<>'OFFLINE_SIMULATION' OR a.mode<>'RECOVERY_ONLY'
        OR h.source_hash<>encode(sha256(convert_to(h.body::text,'UTF8')),'hex')
        OR (SELECT count(*) FROM jsonb_object_keys(h.body))<>10
        OR NOT h.body ?& ARRAY['payload','approval','signature','cut','facts','operator_policy','credential_policy','exclusion','source_owner','control_operation']
        OR h.body->'operator_policy' IS DISTINCT FROM config->'operator_verifier'
        OR h.body->'credential_policy' IS DISTINCT FROM config->'credential_authority'
        OR h.body#>>'{credential_policy,protocol}' IS DISTINCT FROM 'OFFLINE_FAKE_V1'
        OR cert->>'protocol' IS DISTINCT FROM 'OFFLINE_FAKE_V1'
        OR cert->>'provider_id' IS DISTINCT FROM config#>>'{credential_authority,provider_id}'
        OR cert->>'database_lineage' IS DISTINCT FROM a.database_lineage::text
        OR cert->>'predecessor_grant' IS DISTINCT FROM h.predecessor_grant::text
        OR cert->>'predecessor_epoch' IS DISTINCT FROM h.predecessor_epoch::text
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
        OR payload->>'kind' IS DISTINCT FROM 'PROCESS_RECOVERY_V1' OR payload->>'mode' IS DISTINCT FROM 'RECOVERY_ONLY'
        OR payload->>'operation_id' IS DISTINCT FROM h.operation_id::text OR payload->>'grant_id' IS DISTINCT FROM p_grant::text
        OR payload->>'account_id' IS DISTINCT FROM p_account::text OR payload->>'database_lineage' IS DISTINCT FROM a.database_lineage::text
        OR payload->>'principal' IS DISTINCT FROM r.principal OR payload->'owner' IS DISTINCT FROM r.owner
        OR payload->>'capability_hash' IS DISTINCT FROM r.capability_hash::text OR payload->'source_owner' IS DISTINCT FROM source_owner
        OR h.body#>>'{approval,role}' IS DISTINCT FROM 'HANDOFF'
        OR (config#>ARRAY['operator_verifier',h.body#>>'{approval,operator_id}','roles'] ? 'HANDOFF') IS NOT TRUE
        OR h.body#>>'{approval,request_digest}' IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(
            jsonb_build_object('command',payload,'cut',h.body->'cut','facts',h.body->'facts')),'UTF8')),'hex')
        OR payload->>'exclusion_digest' IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(h.body->'exclusion'),'UTF8')),'hex')
        OR h.body#>'{approval,binding}' IS DISTINCT FROM cert->'binding'
        OR cert->'binding' IS DISTINCT FROM h.body#>'{cut,checkpoint,binding}' OR cert->'binding' IS DISTINCT FROM h.body#>'{facts,binding}'
        OR source_owner->>'process_id' IS DISTINCT FROM a.owner_process::text OR source_owner->>'generation' IS DISTINCT FROM a.owner_generation::text
        OR source_owner->>'epoch' IS DISTINCT FROM a.owner_epoch::text OR source_owner->>'session_id' IS DISTINCT FROM a.current_session::text
        OR (source_owner->>'valid_from')::timestamptz IS DISTINCT FROM a.owner_since
        OR (source_owner->>'expires_at')::timestamptz IS DISTINCT FROM a.owner_until
        OR r.owner->>'session_id' IS DISTINCT FROM source_owner->>'session_id' OR r.owner->>'epoch' IS DISTINCT FROM source_owner->>'epoch'
        OR native#>>'{payload,mode}' IS DISTINCT FROM 'RECOVERY_ONLY' OR native#>'{payload,token}' IS DISTINCT FROM source_owner
        OR r.control_operation IS DISTINCT FROM h.body->>'control_operation'
        OR g.process_id::text IS DISTINCT FROM r.owner->>'process_id' OR g.generation::text IS DISTINCT FROM r.owner->>'generation'
        OR g.epoch::text IS DISTINCT FROM r.owner->>'epoch' OR g.session_id::text IS DISTINCT FROM r.owner->>'session_id'
        OR g.issued_at IS DISTINCT FROM (r.owner->>'valid_from')::timestamptz OR g.expires_at IS DISTINCT FROM (r.owner->>'expires_at')::timestamptz
        OR g.signature IS DISTINCT FROM decode(h.body->>'signature','hex')
        OR NOT EXISTS(SELECT 1 FROM life_financial.grant_consumptions WHERE account_id=p_account AND grant_id=p_grant AND operation_id=h.operation_id)
        OR cert#>'{source,owner}' IS DISTINCT FROM (SELECT owner FROM life_financial.process_lease_registrations WHERE account_id=p_account AND grant_id=h.predecessor_grant)
        OR h.predecessor_epoch>1 AND cert->'source' IS DISTINCT FROM life_financial.process_generation_identity(p_account,h.predecessor_grant)
        OR NOT EXISTS(SELECT 1 FROM life_financial.process_lease_commands WHERE account_id=p_account AND grant_id=h.predecessor_grant AND lease_epoch=h.predecessor_epoch AND kind='CLAIM') THEN
        RAISE EXCEPTION 'LIFE_RECOVERY_ORIGIN_UNQUALIFIED';END IF;
    IF NOT p_fresh THEN RETURN;END IF;
    SELECT * INTO STRICT head FROM life_financial.process_leases WHERE account_id=p_account;
    IF NOT ((head.grant_id=h.predecessor_grant AND head.lease_epoch=h.predecessor_epoch)
        OR (head.grant_id=h.grant_id AND head.lease_epoch=h.predecessor_epoch+1 AND (SELECT kind FROM life_financial.process_lease_commands WHERE account_id=p_account AND operation_id=head.last_operation)='CLAIM'))
        OR at<h.registered_at OR at<g.issued_at OR at>=g.expires_at
        OR ((h.body#>>'{approval,issued_at}')::timestamptz<=h.registered_at) IS NOT TRUE
        OR at>=(h.body#>>'{approval,expires_at}')::timestamptz OR at>=(cert->>'expires_at')::timestamptz
        OR extract(epoch FROM(at-(h.body#>>'{facts,observed_at}')::timestamptz))*1000>
            (SELECT max_fact_age_ms FROM life_financial.control_policies WHERE account_id=p_account)
        OR g.expires_at>h.registered_at+make_interval(secs=>(SELECT successor_duration_ms/1000.0 FROM life_financial.control_policies WHERE account_id=p_account))
        OR NOT life_financial.control_verified(p_account) THEN RAISE EXCEPTION 'LIFE_RECOVERY_CLAIM_UNQUALIFIED';END IF;
    state:=life_financial.spot_state(p_account);
    IF (h.body#>>'{cut,checkpoint,revision}')::bigint<>a.version OR h.body#>>'{cut,checkpoint,state_digest}' IS DISTINCT FROM state->>'digest' THEN
        RAISE EXCEPTION 'LIFE_RECOVERY_CUT_CHANGED';END IF;
END; $$;

DO $$ DECLARE item record;p record;BEGIN
 FOR item IN SELECT * FROM (VALUES
('life_financial.process_lease_command(uuid,text,text,uuid)','6ec5e575600ed6025b169d7c2e030e37cc3f3ead0b51f80404069a8de69f9b9c'),
('life_financial.process_lease_guard(uuid,boolean)','4a0c22c25503e0e285fc7b470dad7094eed0f8b32d7ad39b49a307856c6dde85'),
('life_financial.process_lease_commit()','7139c06a8e26a0d8f8da0091199d8c8f68074c5388851ad8945e9717f2e01da9'),
('life_financial.process_registration_commit()','c51d292a08e9d081cd10ee27ff3c298174b64f69a8c8ebc8795bb0be7a80458d'),
('life_financial.process_handoff_guard(uuid,uuid,uuid,bigint,boolean)','a3f9ac0960c39c5daaa53e9c97a3b14a3308538c3da794f6f0851c3271c5a3ac') ) AS t(signature,source_hash) LOOP
 SELECT f.*,l.lanname INTO STRICT p FROM pg_proc f JOIN pg_language l ON l.oid=f.prolang WHERE f.oid=item.signature::regprocedure;
 IF p.lanname<>'plpgsql' OR NOT p.prosecdef OR p.provolatile<>'v' OR p.proisstrict OR p.proleakproof OR p.proparallel<>'u' OR p.proretset OR p.prokind<>'f' OR p.pronargdefaults<>(CASE WHEN item.signature LIKE '%handoff_guard(%' THEN 1 ELSE 0 END)
 OR pg_get_expr(p.proargdefaults,0) IS DISTINCT FROM (CASE WHEN item.signature LIKE '%handoff_guard(%' THEN 'true'::text ELSE NULL::text END)
 OR p.prorettype<>(CASE WHEN item.signature LIKE '%command(%' THEN 'jsonb'::regtype WHEN item.signature LIKE '%commit()' THEN 'trigger'::regtype ELSE 'void'::regtype END)
 OR p.proowner<>(SELECT nspowner FROM pg_namespace WHERE nspname='life_financial') OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
 OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')<>item.source_hash THEN RAISE EXCEPTION 'LIFE_RECOVERY_ACTOR_MIGRATION_MISMATCH';END IF;
 END LOOP;END; $$;

CREATE FUNCTION life_financial.process_recovery_lease_guard(p_account uuid,p_risk boolean) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $guard$
DECLARE a life_financial.accounts;p life_financial.process_lease_policies;r life_financial.process_lease_registrations;
    h life_financial.process_leases;c life_financial.process_lease_commands;g life_financial.activation_grants;
    at timestamptz;secret text;policy jsonb;source_owner jsonb;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR p_risk IS NULL OR pg_is_in_recovery()
        OR current_setting('transaction_read_only')<>'off' OR current_setting('synchronous_commit')<>'on'
        OR current_setting('fsync')<>'on' OR current_setting('full_page_writes')<>'on' THEN
        RAISE EXCEPTION 'LIFE_PROCESS_LEASE_SCOPE_INVALID';END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    SELECT * INTO STRICT p FROM life_financial.process_lease_policies WHERE account_id=p_account;
    SELECT body->'process_lease' INTO policy FROM life_financial.configurations WHERE account_id=p_account AND config_hash=a.config_hash;
    IF a.environment<>'OFFLINE_SIMULATION' OR p.policy_hash<>a.policy_hash
        OR policy IS DISTINCT FROM jsonb_build_object('lease_duration_ms',p.lease_duration_ms,'max_registrations',p.max_registrations) THEN
        RAISE EXCEPTION 'LIFE_PROCESS_LEASE_POLICY_INVALID';END IF;
    SELECT * INTO h FROM life_financial.process_leases WHERE account_id=p_account;
    IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_PROCESS_LEASE_UNARMED';END IF;
    SELECT * INTO STRICT r FROM life_financial.process_lease_registrations WHERE account_id=p_account AND grant_id=h.grant_id;
    SELECT * INTO STRICT g FROM life_financial.activation_grants WHERE account_id=p_account AND grant_id=h.grant_id;
    SELECT * INTO STRICT c FROM life_financial.process_lease_commands WHERE account_id=p_account AND operation_id=h.last_operation;
    PERFORM life_financial.process_recovery_origin(p_account,h.grant_id,false); SELECT body->'source_owner' INTO STRICT source_owner FROM life_financial.process_recovery_grants WHERE account_id=p_account AND grant_id=h.grant_id; secret:=current_setting('life.process_capability',true);at:=clock_timestamp();
    IF session_user<>r.principal OR secret IS NULL OR secret!~'^[0-9a-f]{64}$' OR r.capability_hash<>encode(sha256(convert_to(secret,'UTF8')),'hex')
        OR h.lease_epoch<>c.lease_epoch OR h.grant_id<>c.grant_id OR h.observed_at<>c.observed_at OR h.expires_at<>c.expires_at
        OR c.receipt IS DISTINCT FROM jsonb_build_object('operation_id',c.operation_id,'grant_id',c.grant_id,
            'lease_epoch',c.lease_epoch,'observed_at',c.observed_at,'expires_at',c.expires_at,'replayed',false,'trading_permission',false)
        OR at<r.registered_at OR at<h.observed_at OR at>=h.expires_at OR at<g.issued_at OR at>=g.expires_at OR h.expires_at>g.expires_at
        OR a.owner_process IS DISTINCT FROM (source_owner->>'process_id')::uuid
            OR a.owner_generation IS DISTINCT FROM (source_owner->>'generation')::uuid
            OR a.owner_epoch IS DISTINCT FROM (source_owner->>'epoch')::bigint
            OR a.current_session IS DISTINCT FROM (source_owner->>'session_id')::uuid
            OR a.owner_since IS DISTINCT FROM (source_owner->>'valid_from')::timestamptz
            OR a.owner_until IS DISTINCT FROM (source_owner->>'expires_at')::timestamptz
        OR a.mode NOT IN ('ACTIVE','DRAINING','RECOVERY_ONLY') OR p_risk AND a.mode<>'ACTIVE' THEN
        RAISE EXCEPTION 'LIFE_PROCESS_LEASE_EXPIRED_OR_UNQUALIFIED';END IF;
END; $guard$;

DO $$ DECLARE definition text;needle text;replacement text;pos integer;BEGIN
    definition:=pg_get_functiondef('life_financial.process_lease_guard(uuid,boolean)'::regprocedure);
    needle:=E'BEGIN\n';replacement:=needle||' IF EXISTS(SELECT 1 FROM life_financial.process_leases active_head JOIN life_financial.process_recovery_grants delegate USING(account_id,grant_id) WHERE active_head.account_id=p_account) THEN PERFORM life_financial.process_recovery_lease_guard(p_account,p_risk);RETURN;END IF; ';
    IF strpos(definition,needle)=0 THEN RAISE EXCEPTION 'LIFE_RECOVERY_ACTOR_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
    definition:=pg_get_functiondef('life_financial.process_lease_command(uuid,text,text,uuid)'::regprocedure);
    needle:='IF p_kind=''CLAIM'' THEN';pos:=strpos(definition,needle);
    replacement:='IF p_kind=''CLAIM'' AND EXISTS(SELECT 1 FROM life_financial.process_recovery_grants WHERE account_id=p_account AND grant_id=p_grant) THEN PERFORM life_financial.process_recovery_origin(p_account,p_grant,true); SELECT predecessor_epoch+1 INTO h.lease_epoch FROM life_financial.process_recovery_grants WHERE account_id=p_account AND grant_id=p_grant; ELSIF p_kind=''CLAIM'' THEN';
    IF pos=0 THEN RAISE EXCEPTION 'LIFE_RECOVERY_ACTOR_MIGRATION_MISMATCH';END IF;
    definition:=substr(definition,1,pos-1)||replacement||substr(definition,pos+length(needle));
    needle:='g.expires_at,a.owner_until';replacement:='g.expires_at,CASE WHEN EXISTS(SELECT 1 FROM life_financial.process_recovery_grants WHERE account_id=p_account AND grant_id=p_grant) THEN g.expires_at ELSE a.owner_until END';
    IF strpos(definition,needle)=0 THEN RAISE EXCEPTION 'LIFE_RECOVERY_ACTOR_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
    definition:=pg_get_functiondef('life_financial.process_registration_commit()'::regprocedure);
    needle:=E'BEGIN\n';replacement:=needle||' IF EXISTS(SELECT 1 FROM life_financial.process_recovery_grants WHERE account_id=NEW.account_id AND grant_id=NEW.grant_id) THEN PERFORM life_financial.process_recovery_origin(NEW.account_id,NEW.grant_id,true);RETURN NULL;END IF; ';
    IF strpos(definition,needle)=0 THEN RAISE EXCEPTION 'LIFE_RECOVERY_ACTOR_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
    definition:=pg_get_functiondef('life_financial.process_lease_commit()'::regprocedure);
    needle:='IF NEW.lease_epoch>1 AND';replacement:='IF EXISTS(SELECT 1 FROM life_financial.process_recovery_grants WHERE account_id=NEW.account_id AND grant_id=NEW.grant_id) AND (SELECT kind FROM life_financial.process_lease_commands WHERE account_id=NEW.account_id AND operation_id=NEW.last_operation)=''CLAIM'' THEN PERFORM life_financial.process_recovery_origin(NEW.account_id,NEW.grant_id,true); ELSIF NEW.lease_epoch>1 AND NOT EXISTS(SELECT 1 FROM life_financial.process_recovery_grants WHERE account_id=NEW.account_id AND grant_id=NEW.grant_id) AND';
    IF strpos(definition,needle)=0 THEN RAISE EXCEPTION 'LIFE_RECOVERY_ACTOR_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
    definition:=pg_get_functiondef('life_financial.process_handoff_guard(uuid,uuid,uuid,bigint,boolean)'::regprocedure);
    needle:='(SELECT body#>''{exclusion,body,target}'' FROM life_financial.process_handoffs'||E'\n            WHERE account_id=p_account AND grant_id=p_predecessor)';
    IF strpos(definition,needle)=0 THEN RAISE EXCEPTION 'LIFE_RECOVERY_ACTOR_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,'life_financial.process_generation_identity(p_account,p_predecessor)');
END; $$;

DO $$ DECLARE r record;p regprocedure;BEGIN
    FOR r IN SELECT DISTINCT x.grantee FROM pg_class c CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) x
        WHERE c.oid='life_financial.process_recovery_grants'::regclass AND x.grantee<>c.relowner LOOP
        IF r.grantee=0 THEN REVOKE ALL ON life_financial.process_recovery_grants FROM PUBLIC;
        ELSE EXECUTE format('REVOKE ALL ON life_financial.process_recovery_grants FROM %I',pg_get_userbyid(r.grantee));END IF;
    END LOOP;
    FOREACH p IN ARRAY ARRAY['life_financial.process_generation_identity(uuid,uuid)'::regprocedure,'life_financial.process_recovery_origin(uuid,uuid,boolean)'::regprocedure,'life_financial.process_recovery_lease_guard(uuid,boolean)'::regprocedure] LOOP
        FOR r IN SELECT DISTINCT x.grantee FROM pg_proc f CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x WHERE f.oid=p AND x.grantee<>f.proowner LOOP
            IF r.grantee=0 THEN EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',p);
            ELSE EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',p,pg_get_userbyid(r.grantee));END IF;
        END LOOP;
    END LOOP;
    FOR r IN SELECT DISTINCT x.grantee FROM pg_class c CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) x WHERE c.oid='life_financial.accounts'::regclass AND x.grantee<>0 AND x.grantee<>c.relowner AND x.privilege_type='SELECT' LOOP
        EXECUTE format('GRANT SELECT ON life_financial.process_recovery_grants TO %I',pg_get_userbyid(r.grantee));
    END LOOP;
END; $$;
