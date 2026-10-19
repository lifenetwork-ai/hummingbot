-- Native markout permission derives only from immutable accounted fill/horizon
-- facts. Optional bootstrap has explicit sequential and gross campaign limits.
CREATE TABLE life_financial.risk_markout_probe_policies (
    account_id uuid PRIMARY KEY REFERENCES life_financial.risk_service_policies,
    policy_hash life_financial.digest NOT NULL,
    max_quote_base life_financial.positive_amount NOT NULL,
    max_campaign_base life_financial.positive_amount NOT NULL,
    CHECK(max_quote_base<=max_campaign_base),
    FOREIGN KEY(account_id) REFERENCES life_financial.spot_markout_policies,
    FOREIGN KEY(account_id,policy_hash) REFERENCES life_financial.policies
);
ALTER TABLE life_financial.risk_markout_probe_policies ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped_read ON life_financial.risk_markout_probe_policies FOR SELECT USING(life_financial.can_access(account_id));
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.risk_markout_probe_policies
    FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record();

CREATE FUNCTION life_financial.risk_markout_status(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.risk_service_policies;frame jsonb;cohort jsonb;f jsonb;h bigint;counted bigint;total numeric;mean numeric;
    missing boolean:=false;pending boolean:=false;insufficient boolean:=false;adverse boolean:=false;summaries jsonb:='[]'::jsonb;
BEGIN
    SELECT * INTO STRICT p FROM life_financial.risk_service_policies WHERE account_id=p_account;
    IF NOT EXISTS(SELECT 1 FROM life_financial.spot_markout_policies WHERE account_id=p_account) THEN
        RETURN jsonb_build_object('reason_code','MARKOUT_NOT_CONFIGURED','summaries','[]'::jsonb,'trading_permission',false);END IF;
    frame:=life_financial.spot_markout_frame(p_account,p.max_events,p.max_total_bytes);
    FOR f IN SELECT value FROM jsonb_array_elements(frame->'fills') LOOP
        FOR h IN SELECT value::bigint FROM jsonb_array_elements_text(frame#>'{policy,horizons_ms}') LOOP
            IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(frame->'observations') o
                WHERE o->>'trade_id'=f->>'trade_id' AND (o->>'horizon_ms')::bigint=h) THEN
                IF (frame->>'now_ms')::bigint<(f->>'fill_at_ms')::bigint+h THEN pending:=true;ELSE missing:=true;END IF;
            END IF;
        END LOOP;
    END LOOP;
    IF missing OR pending THEN RETURN jsonb_build_object('reason_code',CASE WHEN missing THEN 'MARKOUT_OBSERVATION_MISSING' ELSE 'MARKOUT_HORIZON_PENDING' END,
        'summaries',summaries,'trading_permission',false);END IF;
    FOR cohort IN SELECT value FROM jsonb_array_elements(frame#>'{policy,monitored_cohorts}') LOOP
        SELECT count(*),sum((CASE WHEN original_fill->>'side'='BUY' THEN 1 ELSE -1 END)*
            ((o->>'independent_price_usdt')::numeric-(original_fill->>'price_usdt')::numeric)*(original_fill->>'quantity_base')::numeric)
        INTO counted,total FROM jsonb_array_elements(frame->'fills') original_fill JOIN jsonb_array_elements(frame->'observations') o
            ON o->>'trade_id'=original_fill->>'trade_id' WHERE original_fill->>'side'=cohort->>0 AND (o->>'horizon_ms')::bigint=(cohort->>2)::bigint
                AND CASE WHEN (original_fill->>'quantity_base')::numeric<=(frame#>>'{policy,size_cutoff_base}')::numeric THEN 'small' ELSE 'large' END=cohort->>1;
        mean:=CASE WHEN counted=0 THEN NULL ELSE total/counted END;
        IF counted<(frame#>>'{policy,min_samples}')::bigint THEN insufficient:=true;
        ELSIF total<(frame#>>'{policy,min_mean_markout_quote}')::numeric*counted THEN adverse:=true;END IF;
        summaries:=summaries||jsonb_build_array(jsonb_build_object('side',cohort->0,'bucket',cohort->1,'horizon_ms',cohort->2,
            'sample_count',counted,'insufficient',counted<(frame#>>'{policy,min_samples}')::bigint,'mean_markout_quote',mean::text));
    END LOOP;
    RETURN jsonb_build_object('reason_code',CASE WHEN adverse THEN 'MARKOUT_ADVERSE' WHEN insufficient THEN 'MARKOUT_INSUFFICIENT_SAMPLES' ELSE 'MARKOUT_READY' END,
        'summaries',summaries,'trading_permission',false);
END; $$;

CREATE FUNCTION life_financial.risk_markout_admit(p_account uuid,op life_financial.operations) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE status jsonb;reason text;t life_financial.send_attempts;p life_financial.risk_markout_probe_policies;bucket text;filled numeric;
BEGIN
    IF NOT EXISTS(SELECT 1 FROM life_financial.risk_service_policies WHERE account_id=p_account) THEN RETURN;END IF;
    status:=life_financial.risk_markout_status(p_account);reason:=status->>'reason_code';
    IF reason IN ('MARKOUT_NOT_CONFIGURED','MARKOUT_READY') THEN RETURN;END IF;
    IF reason<>'MARKOUT_INSUFFICIENT_SAMPLES' OR COALESCE(op.receipt->>'kind','') NOT IN ('SPOT_PREPARE','SPOT_ARM','SPOT_QUOTE_ARM') THEN
        RAISE EXCEPTION 'LIFE_MARKOUT_PERMISSION_REQUIRED' USING ERRCODE='P0001';END IF;
    SELECT * INTO p FROM life_financial.risk_markout_probe_policies WHERE account_id=p_account;
    IF NOT FOUND OR p.policy_hash<>(SELECT policy_hash FROM life_financial.accounts WHERE account_id=p_account) THEN
        RAISE EXCEPTION 'LIFE_MARKOUT_PROBE_POLICY_REQUIRED' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT t FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=op.receipt->>'intent_id';
    SELECT CASE WHEN t.quantity<=size_cutoff_base THEN 'small' ELSE 'large' END INTO STRICT bucket
        FROM life_financial.spot_markout_policies WHERE account_id=p_account;
    SELECT COALESCE(sum(quantity),0) INTO filled FROM life_financial.fills WHERE account_id=p_account AND product='SPOT';
    IF t.quantity>p.max_quote_base OR filled+t.quantity>p.max_campaign_base OR EXISTS(
        SELECT 1 FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id<>t.intent_id AND state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND'))
        OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(status->'summaries') c
            WHERE c->>'side'=t.side AND c->>'bucket'=bucket AND c->'insufficient'='true'::jsonb) THEN
        RAISE EXCEPTION 'LIFE_MARKOUT_PROBE_CAPACITY' USING ERRCODE='P0001';END IF;
END; $$;
CREATE FUNCTION life_financial.risk_markout_frame(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_ACCESS_DENIED' USING ERRCODE='P0001';END IF;
    RETURN life_financial.risk_markout_status(p_account);
END; $$;
DO $$ DECLARE f record;definition text;needle text;BEGIN
    SELECT * INTO STRICT f FROM pg_proc WHERE oid='life_financial.risk_commit_guard()'::regprocedure;
    IF NOT f.prosecdef OR f.provolatile<>'v' OR f.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
        OR encode(sha256(convert_to(f.prosrc,'UTF8')),'hex')<>'bc44818388ff3f4fb3b6b9197e428cd3a7dc2fd10bab5aafc05e4f6d7616794e' THEN
        RAISE EXCEPTION 'LIFE_RISK_MARKOUT_MIGRATION_MISMATCH';END IF;
    definition:=pg_get_functiondef(f.oid);needle:='        PERFORM life_financial.risk_admit(NEW.account_id,op);';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_RISK_MARKOUT_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,needle||E'\n        PERFORM life_financial.risk_markout_admit(NEW.account_id,op);');
END; $$;
DO $$ DECLARE p regprocedure;r record;BEGIN
    FOREACH p IN ARRAY ARRAY['life_financial.risk_markout_status(uuid)'::regprocedure,
        'life_financial.risk_markout_admit(uuid,life_financial.operations)'::regprocedure,
        'life_financial.risk_markout_frame(uuid)'::regprocedure] LOOP
        FOR r IN SELECT DISTINCT x.grantee FROM pg_proc f CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
            WHERE f.oid=p AND x.grantee<>f.proowner LOOP
            IF r.grantee=0 THEN EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',p);
            ELSE EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',p,pg_get_userbyid(r.grantee));END IF;
        END LOOP;
    END LOOP;
END; $$;
