-- One broker signing identity is bound by the ORIGINAL private process lease.
-- Public owner/hash/readback cannot enroll a replacement broker client.
CREATE TABLE life_financial.process_broker_bindings (
    account_id uuid NOT NULL,grant_id uuid NOT NULL,client_id life_financial.digest NOT NULL,
    operation_id life_financial.identity NOT NULL,lease_epoch bigint NOT NULL CHECK(lease_epoch>0),
    registered_at timestamptz NOT NULL CHECK(isfinite(registered_at)),receipt jsonb NOT NULL,
    PRIMARY KEY(account_id,grant_id),UNIQUE(account_id,client_id),UNIQUE(account_id,operation_id),
    FOREIGN KEY(account_id,grant_id) REFERENCES life_financial.process_lease_registrations
);
ALTER TABLE life_financial.process_broker_bindings ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped_read ON life_financial.process_broker_bindings FOR SELECT USING(life_financial.can_access(account_id));
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.process_broker_bindings
    FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record();
CREATE FUNCTION life_financial.process_bind_broker(p_account uuid,p_operation text,p_client text) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE h life_financial.process_leases;r life_financial.process_broker_bindings;at timestamptz;result jsonb;
BEGIN
    IF p_operation IS NULL OR p_operation!~'^[A-Za-z0-9_.:-]{1,128}$' OR p_client IS NULL OR p_client!~'^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'LIFE_BROKER_BINDING_INVALID';END IF;
    PERFORM life_financial.process_lease_guard(p_account,false);
    SELECT * INTO STRICT h FROM life_financial.process_leases WHERE account_id=p_account;
    SELECT * INTO r FROM life_financial.process_broker_bindings WHERE account_id=p_account AND grant_id=h.grant_id;
    IF FOUND THEN
        IF r.client_id<>p_client OR r.operation_id<>p_operation OR r.lease_epoch<>h.lease_epoch THEN
            RAISE EXCEPTION 'LIFE_BROKER_BINDING_ALREADY_CONSUMED';END IF;
        RETURN r.receipt||jsonb_build_object('replayed',true);
    END IF;
    at:=clock_timestamp();result:=jsonb_build_object('operation_id',p_operation,'client_id',p_client,'grant_id',h.grant_id,
        'lease_epoch',h.lease_epoch,'observed_at',at,'replayed',false,'trading_permission',false);
    INSERT INTO life_financial.process_broker_bindings VALUES(p_account,h.grant_id,p_client,p_operation,h.lease_epoch,at,result);
    RETURN result;
END; $$;
CREATE FUNCTION life_financial.process_broker_binding_commit() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE h life_financial.process_leases;BEGIN
    PERFORM life_financial.process_lease_guard(NEW.account_id,false);
    SELECT * INTO STRICT h FROM life_financial.process_leases WHERE account_id=NEW.account_id;
    IF NEW.grant_id<>h.grant_id OR NEW.lease_epoch<>h.lease_epoch THEN RAISE EXCEPTION 'LIFE_BROKER_BINDING_CHANGED_AT_COMMIT';END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER process_broker_at_commit AFTER INSERT ON life_financial.process_broker_bindings
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.process_broker_binding_commit();
DO $$ DECLARE r record;p regprocedure;BEGIN
    FOR r IN SELECT DISTINCT x.grantee FROM pg_class c CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) x
        WHERE c.oid='life_financial.process_broker_bindings'::regclass AND x.grantee<>c.relowner LOOP
        IF r.grantee=0 THEN REVOKE ALL ON life_financial.process_broker_bindings FROM PUBLIC;
        ELSE EXECUTE format('REVOKE ALL ON life_financial.process_broker_bindings FROM %I',pg_get_userbyid(r.grantee));END IF;
    END LOOP;
    FOREACH p IN ARRAY ARRAY['life_financial.process_bind_broker(uuid,text,text)'::regprocedure,'life_financial.process_broker_binding_commit()'::regprocedure] LOOP
        FOR r IN SELECT DISTINCT x.grantee FROM pg_proc f CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x WHERE f.oid=p AND x.grantee<>f.proowner LOOP
            IF r.grantee=0 THEN EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',p);
            ELSE EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',p,pg_get_userbyid(r.grantee));END IF;
        END LOOP;
    END LOOP;
    FOR r IN SELECT DISTINCT x.grantee FROM pg_class c CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) x
        WHERE c.oid='life_financial.accounts'::regclass AND x.grantee<>0 AND x.grantee<>c.relowner AND x.privilege_type='SELECT' LOOP
        EXECUTE format('GRANT SELECT ON life_financial.process_broker_bindings TO %I',pg_get_userbyid(r.grantee));
    END LOOP;
    FOR r IN SELECT DISTINCT x.grantee FROM pg_proc f CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
        WHERE f.oid='life_financial.process_lease_command(uuid,text,text,uuid)'::regprocedure AND x.grantee<>0 AND x.grantee<>f.proowner AND x.privilege_type='EXECUTE' LOOP
        EXECUTE format('GRANT EXECUTE ON FUNCTION life_financial.process_bind_broker(uuid,text,text) TO %I',pg_get_userbyid(r.grantee));
    END LOOP;
END; $$;
