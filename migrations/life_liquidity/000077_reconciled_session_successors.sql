-- Exact expired-parent successor publication; no owner or sender activation.
CREATE TABLE life_financial.session_successors (
    account_id uuid NOT NULL,parent_session uuid NOT NULL,session_id uuid NOT NULL,
    operation_id life_financial.identity NOT NULL,observer_id uuid NOT NULL,
    PRIMARY KEY(account_id,parent_session),UNIQUE(account_id,session_id),UNIQUE(account_id,operation_id),
    FOREIGN KEY(account_id,parent_session) REFERENCES life_financial.sessions,
    FOREIGN KEY(account_id,session_id) REFERENCES life_financial.sessions,
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.control_records DEFERRABLE INITIALLY DEFERRED
);
ALTER TABLE life_financial.session_successors ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped_read ON life_financial.session_successors FOR SELECT USING(life_financial.can_access(account_id));
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.session_successors
    FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record();

CREATE FUNCTION life_financial.control_successor_verified(p_account uuid,p_operation text) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r life_financial.control_records;c life_financial.session_successors;parent life_financial.sessions;
    child life_financial.sessions;p life_financial.control_policies;j jsonb;before_row jsonb;after_row jsonb;
BEGIN
    SELECT * INTO STRICT r FROM life_financial.control_records WHERE account_id=p_account AND operation_id=p_operation;
    SELECT * INTO STRICT c FROM life_financial.session_successors WHERE account_id=p_account AND operation_id=p_operation;
    SELECT * INTO STRICT parent FROM life_financial.sessions WHERE account_id=p_account AND session_id=c.parent_session;
    SELECT * INTO STRICT child FROM life_financial.sessions WHERE account_id=p_account AND session_id=c.session_id;
    SELECT * INTO STRICT p FROM life_financial.control_policies WHERE account_id=p_account;
    j:=r.body;before_row:=j->'before_account';after_row:=j->'after_account';
    RETURN j#>>'{payload,kind}'='SESSION_SUCCESSOR' AND j#>>'{approval,role}'='OBSERVE_SESSION'
        AND (j#>>'{payload,observer_id}')::uuid=c.observer_id
        AND (j#>>'{payload,owner,session_id}')::uuid=c.parent_session
        AND life_financial.control_owner(before_row)-'mode'=life_financial.control_owner(after_row)-'mode'
        AND after_row->>'mode'='RECOVERY_ONLY'
        AND (j#>>'{cut,checkpoint,revision}')::bigint=(before_row->>'version')::bigint
        AND child.epoch=parent.epoch+1 AND child.started_at=parent.expires_at
        AND child.expires_at=parent.expires_at+make_interval(secs=>p.successor_duration_ms/1000.0)
        AND child.config_version=parent.config_version AND child.model_version=parent.model_version AND child.reference_mode='market'
        AND child.anchors=jsonb_build_object('LIFE-USDT',j#>'{payload,market_anchor_usdt}')
        AND (j#>>'{payload,market_anchor_usdt}')::numeric>0
        AND (j#>>'{receipt,parent_session_id}')::uuid=c.parent_session
        AND (j#>>'{receipt,session_id}')::uuid=c.session_id AND (j#>>'{receipt,epoch}')::bigint=child.epoch
        AND (j#>>'{receipt,started_at}')::timestamptz=child.started_at AND (j#>>'{receipt,expires_at}')::timestamptz=child.expires_at
        AND EXISTS(SELECT 1 FROM life_financial.spot_session_policies WHERE account_id=p_account AND session_id=c.parent_session AND on_expiry='switch_to_market_reference')
        AND EXISTS(SELECT 1 FROM life_financial.spot_session_policies WHERE account_id=p_account AND session_id=c.session_id
            AND policy_hash=p.policy_hash AND initial_last_seen_at=r.applied_at AND on_expiry='pause_quotes');
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.control_successor_commit(p_account uuid,p_operation text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r life_financial.control_records;c life_financial.session_successors;parent life_financial.sessions;
    g life_financial.spot_session_progress;p life_financial.control_policies;at timestamptz;reference_at timestamptz;maximum integer;
BEGIN
    SELECT * INTO STRICT r FROM life_financial.control_records WHERE account_id=p_account AND operation_id=p_operation;
    SELECT * INTO STRICT c FROM life_financial.session_successors WHERE account_id=p_account AND operation_id=p_operation;
    SELECT * INTO STRICT parent FROM life_financial.sessions WHERE account_id=p_account AND session_id=c.parent_session;
    SELECT * INTO STRICT g FROM life_financial.spot_session_progress WHERE account_id=p_account AND session_id=c.parent_session;
    SELECT * INTO STRICT p FROM life_financial.control_policies WHERE account_id=p_account;
    SELECT least(p.max_fact_age_ms,max_reconciliation_age_ms) INTO STRICT maximum FROM life_financial.spot_session_policies
        WHERE account_id=p_account AND session_id=c.parent_session;
    at:=clock_timestamp();reference_at:=(r.body#>>'{payload,reference_observed_at}')::timestamptz;
    IF NOT life_financial.control_successor_verified(p_account,p_operation)
        OR NOT life_financial.control_facts_qualified(p_account,r.body->'facts')
        OR NOT life_financial.spot_session_verified(p_account)
        OR (SELECT halted FROM life_financial.accounts WHERE account_id=p_account)
        OR at<parent.expires_at OR g.state<>'TRANSITIONING' OR g.rollback_latched OR at<g.last_seen_at
        OR reference_at<g.last_seen_at OR reference_at>at OR at>reference_at+make_interval(secs=>maximum/1000.0)
        OR (r.body#>>'{facts,observed_at}')::timestamptz<g.last_seen_at THEN
        RAISE EXCEPTION 'LIFE_SUCCESSOR_SOURCE_UNQUALIFIED_AT_COMMIT' USING ERRCODE='P0001';END IF;
END; $$;

DO $$ DECLARE p record;definition text;needle text;replacement text;BEGIN
    SELECT * INTO STRICT p FROM pg_proc WHERE oid='life_financial.control_verified(uuid)'::regprocedure;
    IF NOT p.prosecdef OR p.provolatile<>'s' OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
        OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')<>'94dea33d7452e4a5160525f7ed0c60beffe09b75860e514ae6e966a9017bd413' THEN
        RAISE EXCEPTION 'LIFE_SUCCESSOR_CONTROL_MIGRATION_MISMATCH';END IF;
    definition:=pg_get_functiondef(p.oid);
    needle:=$before$        IF (r.body#>>'{cut,checkpoint,revision}')::bigint<>(before_row->>'version')::bigint
            OR r.body#>>'{receipt,mode}' IS DISTINCT FROM r.body#>>'{payload,mode}'
            OR r.body#>'{receipt,token}' IS DISTINCT FROM r.body#>'{payload,token}'
            OR after_row->>'mode' IS DISTINCT FROM (CASE r.body#>>'{payload,mode}' WHEN 'UNARMED' THEN 'RECOVERY_ONLY' ELSE r.body#>>'{payload,mode}' END) THEN RETURN false;END IF;
        IF r.body#>>'{payload,mode}'='ACTIVE' THEN
            IF jsonb_build_object('process_id',after_row->'owner_process','generation',after_row->'owner_generation',
                'epoch',after_row->'owner_epoch','session_id',after_row->'current_session')
                    IS DISTINCT FROM (r.body#>'{payload,token}')-ARRAY['valid_from','expires_at']
                OR (after_row->>'owner_since')::timestamptz IS DISTINCT FROM (r.body#>>'{payload,token,valid_from}')::timestamptz
                OR (after_row->>'owner_until')::timestamptz IS DISTINCT FROM (r.body#>>'{payload,token,expires_at}')::timestamptz THEN RETURN false;END IF;
        ELSIF life_financial.control_owner(before_row)-'mode' IS DISTINCT FROM life_financial.control_owner(after_row)-'mode' THEN RETURN false;
        END IF;
$before$;
    replacement:=$after$        IF r.body->'payload' ? 'mode' THEN
        IF (r.body#>>'{cut,checkpoint,revision}')::bigint<>(before_row->>'version')::bigint
            OR r.body#>>'{receipt,mode}' IS DISTINCT FROM r.body#>>'{payload,mode}'
            OR r.body#>'{receipt,token}' IS DISTINCT FROM r.body#>'{payload,token}'
            OR after_row->>'mode' IS DISTINCT FROM (CASE r.body#>>'{payload,mode}' WHEN 'UNARMED' THEN 'RECOVERY_ONLY' ELSE r.body#>>'{payload,mode}' END) THEN RETURN false;END IF;
        IF r.body#>>'{payload,mode}'='ACTIVE' THEN
            IF jsonb_build_object('process_id',after_row->'owner_process','generation',after_row->'owner_generation',
                'epoch',after_row->'owner_epoch','session_id',after_row->'current_session')
                    IS DISTINCT FROM (r.body#>'{payload,token}')-ARRAY['valid_from','expires_at']
                OR (after_row->>'owner_since')::timestamptz IS DISTINCT FROM (r.body#>>'{payload,token,valid_from}')::timestamptz
                OR (after_row->>'owner_until')::timestamptz IS DISTINCT FROM (r.body#>>'{payload,token,expires_at}')::timestamptz THEN RETURN false;END IF;
        ELSIF life_financial.control_owner(before_row)-'mode' IS DISTINCT FROM life_financial.control_owner(after_row)-'mode' THEN RETURN false;
        END IF;
        ELSIF r.body#>>'{payload,kind}' IS DISTINCT FROM 'SESSION_SUCCESSOR'
            OR NOT life_financial.control_successor_verified(p_account,r.operation_id) THEN RETURN false;END IF;
$after$;
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_SUCCESSOR_CONTROL_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
    SELECT * INTO STRICT p FROM pg_proc WHERE oid='life_financial.control_commit_check(uuid,text)'::regprocedure;
    IF NOT p.prosecdef OR p.provolatile<>'v' OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
        OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')<>'5c1748d13805ae0ea31091b619c98ef83728649e908be3fba02eda121c4e111f' THEN
        RAISE EXCEPTION 'LIFE_SUCCESSOR_CONTROL_MIGRATION_MISMATCH';END IF;
    definition:=pg_get_functiondef(p.oid);
    needle:=$before$    IF j#>>'{payload,mode}'='ACTIVE' THEN$before$;
    replacement:=$after$    IF j#>>'{payload,kind}'='SESSION_SUCCESSOR' THEN
        PERFORM life_financial.control_successor_commit(p_account,p_operation);
    END IF;
    IF j#>>'{payload,mode}'='ACTIVE' AND EXISTS(
        SELECT 1 FROM life_financial.session_successors x
        WHERE x.account_id=p_account AND x.session_id=a.current_session AND NOT EXISTS(
            SELECT 1 FROM life_financial.spot_session_progress parent_progress
            WHERE parent_progress.account_id=x.account_id AND parent_progress.session_id=x.parent_session
                AND parent_progress.state='TRANSITIONING' AND NOT parent_progress.rollback_latched
                AND parent_progress.last_seen_at<=at)) THEN
        RAISE EXCEPTION 'LIFE_SUCCESSOR_ACTIVATION_UNQUALIFIED_AT_COMMIT' USING ERRCODE='P0001';
    END IF;
    IF j#>>'{payload,mode}'='ACTIVE' THEN$after$;
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_SUCCESSOR_CONTROL_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,replacement);
END; $$;
DO $$ DECLARE p regprocedure;r record;BEGIN
    FOREACH p IN ARRAY ARRAY['life_financial.control_successor_verified(uuid,text)'::regprocedure,
        'life_financial.control_successor_commit(uuid,text)'::regprocedure] LOOP
        FOR r IN SELECT DISTINCT x.grantee FROM pg_proc f
            CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x WHERE f.oid=p AND x.grantee<>f.proowner LOOP
            IF r.grantee=0 THEN EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',p);
            ELSE EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',p,pg_get_userbyid(r.grantee));END IF;
        END LOOP;
    END LOOP;
END; $$;
