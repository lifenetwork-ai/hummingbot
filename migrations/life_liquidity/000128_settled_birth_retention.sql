-- Explicit opt-in settled-history retention. Original configurations retain
-- their original total-ticket/byte caps. No account/policy seed or deletion.
DO $migration$ DECLARE expected jsonb:='[{"signature":"life_financial.quote_birth_before_retirement_verified(uuid)","sha256":"2796273ffa4a8a773ed54cbc23acc980df048f4e5bf2e20216f2cc8d63552aaf"},{"signature":"life_financial.quote_birth_issue(uuid,jsonb,jsonb,bigint,jsonb)","sha256":"1295b7f5bba10c331fe0da3875cd8d2afa3687fc26e44b5499a3def6420592d1"},{"signature":"life_financial.quote_birth_verified(uuid)","sha256":"d9ffc8b3749a346298adb7d8fafc95f664de5f13aefc5319ea23d7c6c87bdc38"},{"signature":"life_financial.spot_body(uuid)","sha256":"9e93868d4c9d376f37e95a3b9891aba1e9811650b8b1ab9c22a741bcfec53b2c"}]'::jsonb;item jsonb;p pg_proc;BEGIN
    FOR item IN SELECT value FROM jsonb_array_elements(expected) LOOP
        SELECT * INTO p FROM pg_proc WHERE oid=to_regprocedure(item->>'signature');
        IF NOT FOUND OR NOT p.prosecdef OR p.provolatile<>(CASE WHEN p.proname='quote_birth_issue' THEN 'v' ELSE 's' END)
            OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog'] OR p.proisstrict OR p.proleakproof
            OR p.proparallel<>'u' OR p.proretset OR p.prokind<>'f' OR p.pronargdefaults<>0
            OR p.proowner<>(SELECT nspowner FROM pg_namespace WHERE nspname='life_financial')
            OR p.prolang<>(SELECT oid FROM pg_language WHERE lanname='plpgsql')
            OR p.prorettype<>(CASE WHEN p.proname IN ('quote_birth_verified','quote_birth_before_retirement_verified')
                THEN 'boolean'::regtype ELSE 'jsonb'::regtype END)
            OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')<>item->>'sha256' THEN
            RAISE EXCEPTION 'LIFE_BIRTH_RETENTION_UNKNOWN_PREDECESSOR';END IF;
    END LOOP;
END; $migration$;
CREATE TABLE life_financial.quote_birth_retention_policies (
    account_id uuid PRIMARY KEY REFERENCES life_financial.accounts,
    policy_hash life_financial.digest NOT NULL,
    model_hash life_financial.digest NOT NULL,
    max_retained_tickets integer NOT NULL CHECK(max_retained_tickets BETWEEN 1 AND 100000),
    max_retained_body_bytes bigint NOT NULL CHECK(max_retained_body_bytes BETWEEN 1 AND 536870912),
    FOREIGN KEY(account_id,policy_hash) REFERENCES life_financial.policies
);
ALTER TABLE life_financial.quote_birth_retention_policies ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped_read ON life_financial.quote_birth_retention_policies FOR SELECT USING(life_financial.can_access(account_id));
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.quote_birth_retention_policies
    FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record();

-- Classification affects metadata capacity only. Every original birth,
-- consumption, retirement, operation and monetary/recorder row stays retained
-- and is still covered by the complete independent verifiers and backup.
-- A read of this private set grants no sender, lease or economic release.
CREATE FUNCTION life_financial.quote_birth_cold_tickets(p_account uuid) RETURNS SETOF life_financial.identity
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT b.ticket_id FROM life_financial.quote_births b
    LEFT JOIN life_financial.quote_birth_retirements r USING(account_id,ticket_id)
    LEFT JOIN life_financial.quote_birth_consumptions c USING(account_id,ticket_id)
    LEFT JOIN life_financial.send_attempts t ON t.account_id=b.account_id AND t.intent_id=b.intent_id AND t.product='SPOT'
    LEFT JOIN life_financial.quote_actions q ON q.account_id=b.account_id AND q.action_id=b.intent_id
    LEFT JOIN life_financial.spot_inventory_members m ON m.account_id=b.account_id AND m.intent_id=b.intent_id
    LEFT JOIN life_financial.spot_inventory_settlements s ON s.account_id=m.account_id AND s.operation_id=m.operation_id
    WHERE b.account_id=p_account AND (r.ticket_id IS NOT NULL OR (
        c.operation_id IS NOT NULL AND t.state='TERMINAL' AND q.state='RECONCILED' AND s.operation_id IS NOT NULL
        AND EXISTS(SELECT 1 FROM life_financial.spot_terminal_facts f WHERE f.account_id=b.account_id AND f.intent_id=b.intent_id)
        AND NOT EXISTS(SELECT 1 FROM life_financial.reservations h WHERE h.account_id=b.account_id AND h.intent_id=b.intent_id
            AND (h.state<>'TERMINAL' OR h.remaining<>0))
        AND NOT EXISTS(SELECT 1 FROM life_financial.capital_claims h WHERE h.account_id=b.account_id AND h.intent_id=b.intent_id AND h.state='HELD')
        AND NOT EXISTS(SELECT 1 FROM life_financial.shared_capital_claims h WHERE h.account_id=b.account_id AND h.intent_id=b.intent_id
            AND NOT EXISTS(SELECT 1 FROM life_financial.shared_claim_releases x WHERE x.account_id=h.account_id AND x.intent_id=h.intent_id))
        AND EXISTS(SELECT 1 FROM life_financial.recorder_outbox o JOIN life_financial.projection_receipts d USING(account_id,projection_id)
            WHERE o.account_id=b.account_id AND o.stream='executor:'||encode(sha256(convert_to(b.intent_id,'UTF8')),'hex')
                AND d.applied_hash=o.source_hash AND (o.body#>>'{command,payload_json}')::jsonb->>'status'='4'
                AND (o.body#>>'{command,payload_json}')::jsonb->>'id'=b.intent_id
                AND NOT EXISTS(SELECT 1 FROM life_financial.recorder_outbox later WHERE later.account_id=o.account_id
                    AND later.stream=o.stream AND later.sequence>o.sequence))
    ));
$$;

CREATE FUNCTION life_financial.quote_birth_capacity_valid(p_account uuid,p_total_count bigint,p_total_bytes bigint,p_new_bytes bigint)
RETURNS boolean LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.quote_birth_policies;h life_financial.quote_birth_retention_policies;a life_financial.accounts;
    model jsonb;declared jsonb;hot_count bigint;hot_bytes bigint;
BEGIN
    SELECT * INTO STRICT p FROM life_financial.quote_birth_policies WHERE account_id=p_account;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account;
    SELECT body->'spot_quote_producer' INTO STRICT model FROM life_financial.configurations
        WHERE account_id=p_account AND config_hash=a.config_hash AND policy_hash=a.policy_hash;
    SELECT * INTO h FROM life_financial.quote_birth_retention_policies WHERE account_id=p_account;
    IF p_total_count IS NULL OR p_total_bytes IS NULL OR p_new_bytes IS NULL
        OR p_total_count<0 OR p_total_bytes<0 OR p_new_bytes<0 OR p_new_bytes>p_total_bytes THEN RETURN false;END IF;
    IF h.account_id IS NULL THEN
        RETURN NOT COALESCE(model ? 'birth_retention',false)
            AND p_total_count<=p.max_tickets AND p_total_bytes<=p.max_total_bytes;
    END IF;
    declared:=model->'birth_retention';
    IF h.policy_hash IS DISTINCT FROM a.policy_hash OR p.policy_hash IS DISTINCT FROM a.policy_hash
        OR h.model_hash IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(model),'UTF8')),'hex')
        OR declared IS DISTINCT FROM jsonb_build_object('mode','SETTLED_NATIVE_V1',
            'max_retained_tickets',h.max_retained_tickets,'max_retained_body_bytes',h.max_retained_body_bytes)
        OR p_total_count>h.max_retained_tickets OR p_total_bytes>h.max_retained_body_bytes THEN RETURN false;END IF;
    WITH cold AS MATERIALIZED (SELECT life_financial.quote_birth_cold_tickets(p_account) AS ticket_id)
        SELECT count(*)+CASE WHEN p_new_bytes>0 THEN 1 ELSE 0 END,
            COALESCE(sum(octet_length(b.body::text)),0)+p_new_bytes INTO hot_count,hot_bytes
        FROM life_financial.quote_births b LEFT JOIN cold c USING(ticket_id)
        WHERE b.account_id=p_account AND c.ticket_id IS NULL;
    RETURN hot_count<=p.max_tickets AND hot_bytes<=p.max_total_bytes;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

DO $$ DECLARE definition text;needle text;replacement text;r record;BEGIN
    definition:=pg_get_functiondef('life_financial.quote_birth_before_retirement_verified(uuid)'::regprocedure);
    needle:='ticket_count>p.max_tickets OR total_bytes>p.max_total_bytes';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_BIRTH_RETENTION_UNKNOWN_PREDECESSOR';END IF;
    EXECUTE replace(definition,needle,'NOT life_financial.quote_birth_capacity_valid(p_account,ticket_count,total_bytes,0)');
    definition:=pg_get_functiondef('life_financial.quote_birth_issue(uuid,jsonb,jsonb,bigint,jsonb)'::regprocedure);
    needle:='(SELECT count(*) FROM life_financial.quote_births WHERE account_id=p_account)>=p.max_tickets';
    replacement:='(SELECT count(*) FROM life_financial.quote_births WHERE account_id=p_account)>=COALESCE(
        (SELECT max_retained_tickets FROM life_financial.quote_birth_retention_policies WHERE account_id=p_account),p.max_tickets)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_BIRTH_RETENTION_UNKNOWN_PREDECESSOR';END IF;
    definition:=replace(definition,needle,replacement);
    needle:=$old$(SELECT COALESCE(sum(octet_length(b.body::text)),0) FROM life_financial.quote_births b WHERE account_id=p_account)
        +octet_length(body::text)>p.max_total_bytes$old$;
    replacement:=$new$NOT life_financial.quote_birth_capacity_valid(p_account,
        (SELECT count(*)+1 FROM life_financial.quote_births WHERE account_id=p_account),
        (SELECT COALESCE(sum(octet_length(b.body::text)),0) FROM life_financial.quote_births b WHERE account_id=p_account)+octet_length(body::text),
        octet_length(body::text))$new$;
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_BIRTH_RETENTION_UNKNOWN_PREDECESSOR';END IF;
    EXECUTE replace(definition,needle,replacement);
    definition:=pg_get_functiondef('life_financial.spot_body(uuid)'::regprocedure);
    needle:='b:=life_financial.spot_body_before_quote_birth(p_account);';
    replacement:='b:=life_financial.spot_body_before_quote_birth(p_account);
    IF EXISTS(SELECT 1 FROM life_financial.quote_birth_retention_policies WHERE account_id=p_account) THEN
        b:=b||jsonb_build_object(''quote_birth_retention_policy'',
            (SELECT to_jsonb(h) FROM life_financial.quote_birth_retention_policies h WHERE account_id=p_account));END IF;';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_BIRTH_RETENTION_UNKNOWN_PREDECESSOR';END IF;
    EXECUTE replace(definition,needle,replacement);
    -- Scrub hostile defaults. The ordinary access provisioner may grant scoped
    -- SELECT; runtime/reporters receive no policy writes or private helper API.
    FOR r IN SELECT DISTINCT x.grantee FROM pg_class t
        CROSS JOIN LATERAL aclexplode(COALESCE(t.relacl,acldefault('r',t.relowner))) x
        WHERE t.oid='life_financial.quote_birth_retention_policies'::regclass AND x.grantee<>t.relowner LOOP
        EXECUTE format('REVOKE ALL ON life_financial.quote_birth_retention_policies FROM %s',
            CASE WHEN r.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(r.grantee)) END);
    END LOOP;
    FOR r IN SELECT DISTINCT x.grantee,p.oid::regprocedure::text AS signature FROM pg_proc p
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE p.oid IN ('life_financial.quote_birth_cold_tickets(uuid)'::regprocedure,
            'life_financial.quote_birth_capacity_valid(uuid,bigint,bigint,bigint)'::regprocedure) AND x.grantee<>p.proowner LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %s',r.signature,
            CASE WHEN r.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(r.grantee)) END);
    END LOOP;
END; $$;
