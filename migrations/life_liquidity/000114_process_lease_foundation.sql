-- Opt-in OFFLINE first-generation lease foundation. No account/policy/grant
-- bootstrap, secret distribution, successor takeover or exchange exclusion.
-- An original signed native ACTIVATE grant plus a private process capability
-- is required. Public owner JSON and database readback are not credentials.
CREATE TABLE life_financial.process_lease_policies (
    account_id uuid PRIMARY KEY REFERENCES life_financial.accounts,
    policy_hash life_financial.digest NOT NULL,
    lease_duration_ms integer NOT NULL CHECK(lease_duration_ms BETWEEN 1 AND 60000),
    max_registrations integer NOT NULL CHECK(max_registrations BETWEEN 1 AND 1024),
    FOREIGN KEY(account_id,policy_hash) REFERENCES life_financial.policies
);
CREATE TABLE life_financial.process_lease_registrations (
    account_id uuid NOT NULL REFERENCES life_financial.process_lease_policies,
    grant_id uuid NOT NULL,principal text NOT NULL CHECK(principal~'^[a-z_][a-z0-9_]{0,62}$'),owner jsonb NOT NULL CHECK(jsonb_typeof(owner)='object'),
    capability_hash life_financial.digest NOT NULL,
    registered_at timestamptz NOT NULL CHECK(isfinite(registered_at)),
    control_operation life_financial.identity NOT NULL,
    PRIMARY KEY(account_id,grant_id),UNIQUE(account_id,capability_hash),
    FOREIGN KEY(account_id,grant_id) REFERENCES life_financial.activation_grants,
    FOREIGN KEY(account_id,control_operation) REFERENCES life_financial.control_records
);
CREATE TABLE life_financial.process_lease_commands (
    account_id uuid NOT NULL,operation_id life_financial.identity NOT NULL,
    grant_id uuid NOT NULL,kind text NOT NULL CHECK(kind IN ('CLAIM','HEARTBEAT')),
    lease_epoch bigint NOT NULL CHECK(lease_epoch>0),
    observed_at timestamptz NOT NULL CHECK(isfinite(observed_at)),
    expires_at timestamptz NOT NULL CHECK(isfinite(expires_at) AND expires_at>observed_at),
    receipt jsonb NOT NULL CHECK(jsonb_typeof(receipt)='object'),
    PRIMARY KEY(account_id,operation_id),
    FOREIGN KEY(account_id,grant_id) REFERENCES life_financial.process_lease_registrations
);
CREATE UNIQUE INDEX process_lease_grant_consumed_once
    ON life_financial.process_lease_commands(account_id,grant_id) WHERE kind='CLAIM';
CREATE TABLE life_financial.process_leases (
    account_id uuid PRIMARY KEY REFERENCES life_financial.process_lease_policies,
    grant_id uuid NOT NULL,lease_epoch bigint NOT NULL CHECK(lease_epoch>0),
    observed_at timestamptz NOT NULL CHECK(isfinite(observed_at)),
    expires_at timestamptz NOT NULL CHECK(isfinite(expires_at)),
    last_operation life_financial.identity NOT NULL,
    FOREIGN KEY(account_id,grant_id) REFERENCES life_financial.process_lease_registrations,
    FOREIGN KEY(account_id,last_operation) REFERENCES life_financial.process_lease_commands DEFERRABLE INITIALLY DEFERRED
);
DO $$ DECLARE t text;BEGIN
    FOREACH t IN ARRAY ARRAY['process_lease_policies','process_lease_registrations','process_lease_commands','process_leases'] LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY scoped_read ON life_financial.%I FOR SELECT USING(life_financial.can_access(account_id))',t);
        IF t<>'process_leases' THEN
            EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',t);
        END IF;
    END LOOP;
END; $$;

CREATE FUNCTION life_financial.process_lease_guard(p_account uuid,p_risk boolean) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts;p life_financial.process_lease_policies;r life_financial.process_lease_registrations;
    h life_financial.process_leases;c life_financial.process_lease_commands;g life_financial.activation_grants;
    at timestamptz;secret text;policy jsonb;
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
    secret:=current_setting('life.process_capability',true);at:=clock_timestamp();
    IF session_user<>r.principal OR secret IS NULL OR secret!~'^[0-9a-f]{64}$' OR r.capability_hash<>encode(sha256(convert_to(secret,'UTF8')),'hex')
        OR h.lease_epoch<>c.lease_epoch OR h.grant_id<>c.grant_id OR h.observed_at<>c.observed_at OR h.expires_at<>c.expires_at
        OR c.receipt IS DISTINCT FROM jsonb_build_object('operation_id',c.operation_id,'grant_id',c.grant_id,
            'lease_epoch',c.lease_epoch,'observed_at',c.observed_at,'expires_at',c.expires_at,'replayed',false,'trading_permission',false)
        OR at<r.registered_at OR at<h.observed_at OR at>=h.expires_at OR at<g.issued_at OR at>=g.expires_at OR h.expires_at>g.expires_at
        OR a.owner_process IS DISTINCT FROM (r.owner->>'process_id')::uuid
            OR a.owner_generation IS DISTINCT FROM (r.owner->>'generation')::uuid
            OR a.owner_epoch IS DISTINCT FROM (r.owner->>'epoch')::bigint
            OR a.current_session IS DISTINCT FROM (r.owner->>'session_id')::uuid
            OR a.owner_since IS DISTINCT FROM (r.owner->>'valid_from')::timestamptz
            OR a.owner_until IS DISTINCT FROM (r.owner->>'expires_at')::timestamptz
        OR a.mode NOT IN ('ACTIVE','DRAINING','RECOVERY_ONLY') OR p_risk AND a.mode<>'ACTIVE' THEN
        RAISE EXCEPTION 'LIFE_PROCESS_LEASE_EXPIRED_OR_UNQUALIFIED';END IF;
END; $$;

CREATE FUNCTION life_financial.process_lease_command(p_account uuid,p_operation text,p_kind text,p_grant uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts;p life_financial.process_lease_policies;r life_financial.process_lease_registrations;
    h life_financial.process_leases;old life_financial.process_lease_commands;g life_financial.activation_grants;
    at timestamptz;until timestamptz;secret text;result jsonb;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR p_operation IS NULL OR p_operation!~'^[A-Za-z0-9_.:-]{1,128}$'
        OR p_kind IS NULL OR p_kind NOT IN ('CLAIM','HEARTBEAT') OR p_grant IS NULL
        OR pg_is_in_recovery() OR current_setting('transaction_read_only')<>'off'
        OR current_setting('synchronous_commit')<>'on' OR current_setting('fsync')<>'on' OR current_setting('full_page_writes')<>'on' THEN
        RAISE EXCEPTION 'LIFE_PROCESS_LEASE_SCOPE_INVALID';END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    SELECT * INTO STRICT p FROM life_financial.process_lease_policies WHERE account_id=p_account;
    SELECT * INTO r FROM life_financial.process_lease_registrations WHERE account_id=p_account AND grant_id=p_grant;
    secret:=current_setting('life.process_capability',true);
    IF NOT FOUND OR session_user<>r.principal OR secret IS NULL OR secret!~'^[0-9a-f]{64}$'
        OR r.capability_hash<>encode(sha256(convert_to(secret,'UTF8')),'hex') THEN
        RAISE EXCEPTION 'LIFE_PROCESS_LEASE_CAPABILITY_INVALID';END IF;
    SELECT * INTO old FROM life_financial.process_lease_commands WHERE account_id=p_account AND operation_id=p_operation;
    IF FOUND THEN
        IF old.kind<>p_kind OR old.grant_id<>p_grant THEN RAISE EXCEPTION 'LIFE_PROCESS_LEASE_OPERATION_CONFLICT';END IF;
        RETURN old.receipt||jsonb_build_object('replayed',true);END IF;
    SELECT * INTO g FROM life_financial.activation_grants WHERE account_id=p_account AND grant_id=p_grant;
    at:=clock_timestamp();
    IF a.environment<>'OFFLINE_SIMULATION' OR p.policy_hash<>a.policy_hash OR at<r.registered_at OR at<g.issued_at OR at>=g.expires_at
        OR NOT EXISTS(SELECT 1 FROM life_financial.configurations WHERE account_id=p_account AND config_hash=a.config_hash
            AND body->'process_lease'=jsonb_build_object('lease_duration_ms',p.lease_duration_ms,'max_registrations',p.max_registrations)) THEN
        RAISE EXCEPTION 'LIFE_PROCESS_LEASE_POLICY_OR_GRANT_INVALID';END IF;
    SELECT * INTO h FROM life_financial.process_leases WHERE account_id=p_account;
    IF p_kind='CLAIM' THEN
        -- Explicit successor/external-exclusion protocol is a separate PG4
        -- increment. Even an expired original lease cannot be replaced here.
        IF FOUND THEN RAISE EXCEPTION 'LIFE_PROCESS_LEASE_HANDOFF_REQUIRED';END IF;
        IF NOT life_financial.control_verified(p_account) OR NOT EXISTS(
            SELECT 1 FROM life_financial.control_records control JOIN life_financial.grant_consumptions consumption
                ON consumption.account_id=control.account_id AND consumption.operation_id=control.operation_id
            WHERE control.account_id=p_account AND control.operation_id=r.control_operation AND consumption.grant_id=p_grant
                AND control.body#>>'{payload,mode}'='ACTIVE' AND control.body#>'{payload,token}'=r.owner)
            OR jsonb_build_object('process_id',a.owner_process,'generation',a.owner_generation,'epoch',a.owner_epoch,
                'session_id',a.current_session,'valid_from',r.owner->'valid_from','expires_at',r.owner->'expires_at')<>r.owner
            OR a.owner_since IS DISTINCT FROM (r.owner->>'valid_from')::timestamptz
            OR a.owner_until IS DISTINCT FROM (r.owner->>'expires_at')::timestamptz OR a.mode<>'ACTIVE' THEN
            RAISE EXCEPTION 'LIFE_PROCESS_LEASE_ACTIVATION_UNQUALIFIED';END IF;
        h.lease_epoch:=1;
    ELSE
        PERFORM life_financial.process_lease_guard(p_account,false);
        IF h.grant_id<>p_grant THEN RAISE EXCEPTION 'LIFE_PROCESS_LEASE_GENERATION_CHANGED';END IF;
    END IF;
    until:=least(at+make_interval(secs=>p.lease_duration_ms::double precision/1000),g.expires_at,a.owner_until);
    IF until<=at THEN RAISE EXCEPTION 'LIFE_PROCESS_LEASE_EXPIRED';END IF;
    result:=jsonb_build_object('operation_id',p_operation,'grant_id',p_grant,'lease_epoch',h.lease_epoch,
        'observed_at',at,'expires_at',until,'replayed',false,'trading_permission',false);
    INSERT INTO life_financial.process_lease_commands VALUES(p_account,p_operation,p_grant,p_kind,h.lease_epoch,at,until,result);
    IF p_kind='CLAIM' THEN
        INSERT INTO life_financial.process_leases VALUES(p_account,p_grant,h.lease_epoch,at,until,p_operation);
    ELSE
        UPDATE life_financial.process_leases SET observed_at=at,expires_at=until,last_operation=p_operation WHERE account_id=p_account;
    END IF;
    PERFORM life_financial.process_lease_guard(p_account,false);
    RETURN result;
END; $$;
CREATE FUNCTION life_financial.process_lease_commit() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    PERFORM life_financial.process_lease_guard(NEW.account_id,false);RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER process_lease_at_commit AFTER INSERT OR UPDATE ON life_financial.process_leases
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.process_lease_commit();

-- Preserve both original guards, OIDs and ACLs. Their callers include the
-- independent physical financial-COMMIT constraints. Unmanaged profiles retain
-- every predecessor check; opted-in profiles additionally require a live actor.
DO $$ DECLARE p record;definition text;needle text;replacement text;BEGIN
    FOR p IN SELECT f.*,l.lanname FROM pg_proc f JOIN pg_language l ON l.oid=f.prolang
        WHERE f.oid IN ('life_financial.guard_account(uuid,jsonb,jsonb,bigint)'::regprocedure,
            'life_financial.guard_spot_observation(uuid,jsonb,jsonb,bigint)'::regprocedure) LOOP
        IF p.lanname<>'plpgsql' OR NOT p.prosecdef OR p.provolatile<>'v' OR p.proisstrict OR p.proleakproof
            OR p.proparallel<>'u' OR p.proretset OR p.prokind<>'f' OR p.pronargdefaults<>0
            OR p.proowner<>(SELECT nspowner FROM pg_namespace WHERE nspname='life_financial')
            OR p.prorettype<>'void'::regtype OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') IS DISTINCT FROM (CASE p.proname
                WHEN 'guard_account' THEN '0e1a6a391f77ba525abbdb6bb6feaeea4002482876b2209ebb82c2810b84dbcb'
                WHEN 'guard_spot_observation' THEN '852218ab21a4924e4ea6f3b4f7a92dd06800292feb7f29903c084015df7418fb'
                END) THEN RAISE EXCEPTION 'LIFE_PROCESS_LEASE_MIGRATION_MISMATCH';END IF;
        definition:=pg_get_functiondef(p.oid);needle:=E'BEGIN\n';
        replacement:=needle||E'    IF EXISTS(SELECT 1 FROM life_financial.process_lease_policies WHERE account_id=p_account) THEN\n        PERFORM life_financial.process_lease_guard(p_account,'||
            CASE WHEN p.proname='guard_account' THEN 'true' ELSE 'false' END||E');END IF;\n';
        IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
            RAISE EXCEPTION 'LIFE_PROCESS_LEASE_MIGRATION_MISMATCH';END IF;
        EXECUTE replace(definition,needle,replacement);
    END LOOP;
END; $$;
REVOKE ALL ON FUNCTION life_financial.process_lease_guard(uuid,boolean),life_financial.process_lease_command(uuid,text,text,uuid),life_financial.process_lease_commit() FROM PUBLIC;

CREATE FUNCTION life_financial.process_registration_commit() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts;g life_financial.activation_grants;at timestamptz;
BEGIN
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=NEW.account_id;
    SELECT * INTO STRICT g FROM life_financial.activation_grants WHERE account_id=NEW.account_id AND grant_id=NEW.grant_id;
    at:=clock_timestamp();
    IF a.environment<>'OFFLINE_SIMULATION' OR at<NEW.registered_at OR at<g.issued_at OR at>=g.expires_at
        OR a.owner_process IS DISTINCT FROM (NEW.owner->>'process_id')::uuid
        OR a.owner_generation IS DISTINCT FROM (NEW.owner->>'generation')::uuid
        OR a.owner_epoch IS DISTINCT FROM (NEW.owner->>'epoch')::bigint OR a.mode<>'ACTIVE'
        OR a.current_session IS DISTINCT FROM (NEW.owner->>'session_id')::uuid
        OR a.owner_since IS DISTINCT FROM (NEW.owner->>'valid_from')::timestamptz
        OR a.owner_until IS DISTINCT FROM (NEW.owner->>'expires_at')::timestamptz THEN
        RAISE EXCEPTION 'LIFE_PROCESS_LEASE_REGISTRATION_EXPIRED_AT_COMMIT';END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER process_registration_at_commit AFTER INSERT ON life_financial.process_lease_registrations
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.process_registration_commit();
REVOKE ALL ON FUNCTION life_financial.process_registration_commit() FROM PUBLIC;

-- Hostile default ACLs must not create a second issuer or grant private helpers.
DO $$ DECLARE t regclass;p regprocedure;r record;BEGIN
    FOREACH t IN ARRAY ARRAY['life_financial.process_lease_policies'::regclass,'life_financial.process_lease_registrations'::regclass,
        'life_financial.process_lease_commands'::regclass,'life_financial.process_leases'::regclass] LOOP
        FOR r IN SELECT DISTINCT x.grantee FROM pg_class c
            CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) x WHERE c.oid=t AND x.grantee<>c.relowner LOOP
            IF r.grantee=0 THEN EXECUTE format('REVOKE ALL ON TABLE %s FROM PUBLIC',t);
            ELSE EXECUTE format('REVOKE ALL ON TABLE %s FROM %I',t,pg_get_userbyid(r.grantee));END IF;
        END LOOP;
    END LOOP;
    FOREACH p IN ARRAY ARRAY['life_financial.process_lease_guard(uuid,boolean)'::regprocedure,
        'life_financial.process_lease_command(uuid,text,text,uuid)'::regprocedure,
        'life_financial.process_lease_commit()'::regprocedure,'life_financial.process_registration_commit()'::regprocedure] LOOP
        FOR r IN SELECT DISTINCT x.grantee FROM pg_proc f
            CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x WHERE f.oid=p AND x.grantee<>f.proowner LOOP
            IF r.grantee=0 THEN EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',p);
            ELSE EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',p,pg_get_userbyid(r.grantee));END IF;
        END LOOP;
    END LOOP;
END; $$;

-- Risk commands have an independent inline native COMMIT branch. It must
-- check the process lease there too; a client-side final check is insufficient.
DO $$ DECLARE p record;definition text;needle text;replacement text;BEGIN
    SELECT f.*,l.lanname INTO STRICT p FROM pg_proc f JOIN pg_language l ON l.oid=f.prolang
        WHERE f.oid='life_financial.commit_owner_guard()'::regprocedure;
    IF p.lanname<>'plpgsql' OR NOT p.prosecdef OR p.provolatile<>'v' OR p.proisstrict OR p.proleakproof
        OR p.proparallel<>'u' OR p.proretset OR p.prokind<>'f' OR p.pronargdefaults<>0
        OR p.proowner<>(SELECT nspowner FROM pg_namespace WHERE nspname='life_financial')
        OR p.prorettype<>'trigger'::regtype OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
        OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')<>'e5e5d88112a1e175787a132d83e6b7a159433406a8d9cfe9c8f6ac5475f044d4' THEN
        RAISE EXCEPTION 'LIFE_PROCESS_LEASE_MIGRATION_MISMATCH';END IF;
    definition:=pg_get_functiondef(p.oid);
    needle:=E'    ELSE\n        SELECT expires_at INTO STRICT expiry FROM life_financial.sessions';
    replacement:=E'    ELSE\n        IF EXISTS(SELECT 1 FROM life_financial.process_lease_policies WHERE account_id=NEW.account_id) THEN\n            PERFORM life_financial.process_lease_guard(NEW.account_id,true);END IF;\n        SELECT expires_at INTO STRICT expiry FROM life_financial.sessions';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_PROCESS_LEASE_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;

-- Upgrade already provisioned profiles as well as empty-database bootstrap.
-- Inherit only explicit predecessor permissions, never hostile defaults/PUBLIC.
-- Account RLS remains mandatory; no new direct table write is granted.
DO $$ DECLARE r record;t text;BEGIN
    FOR r IN SELECT DISTINCT x.grantee FROM pg_proc f
        CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
        WHERE f.oid='life_financial.guard_account(uuid,jsonb,jsonb,bigint)'::regprocedure
            AND x.grantee<>0 AND x.grantee<>f.proowner AND x.privilege_type='EXECUTE' LOOP
        EXECUTE format('GRANT EXECUTE ON FUNCTION life_financial.process_lease_command(uuid,text,text,uuid) TO %I',pg_get_userbyid(r.grantee));
    END LOOP;
    FOR r IN SELECT DISTINCT x.grantee FROM pg_class c
        CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) x
        WHERE c.oid='life_financial.accounts'::regclass AND x.grantee<>0 AND x.grantee<>c.relowner AND x.privilege_type='SELECT' LOOP
        FOREACH t IN ARRAY ARRAY['process_lease_policies','process_lease_registrations','process_lease_commands','process_leases'] LOOP
            EXECUTE format('GRANT SELECT ON life_financial.%I TO %I',t,pg_get_userbyid(r.grantee));
        END LOOP;
    END LOOP;
END; $$;
