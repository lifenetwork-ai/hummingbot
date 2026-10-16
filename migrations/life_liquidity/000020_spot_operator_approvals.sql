-- Privileged operator approval import; runtime receives no approval write API.
CREATE TABLE life_financial.spot_operator_approvals (
    account_id uuid NOT NULL, operation_id life_financial.identity NOT NULL,
    bill_id life_financial.identity NOT NULL, body jsonb NOT NULL CHECK(jsonb_typeof(body)='object'),
    source_hash life_financial.digest NOT NULL, captured_at timestamptz NOT NULL CHECK(isfinite(captured_at)),
    max_fact_age_ms integer NOT NULL CHECK(max_fact_age_ms BETWEEN 1 AND 60000),
    PRIMARY KEY(account_id,operation_id), UNIQUE(account_id,bill_id),
    FOREIGN KEY(account_id,bill_id) REFERENCES life_financial.bill_approvals
);
ALTER TABLE life_financial.spot_operator_approvals ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped_read ON life_financial.spot_operator_approvals FOR SELECT USING(life_financial.can_access(account_id));
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.spot_operator_approvals
    FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record();

-- Cryptographic verification happens in the separate privileged importer with
-- externally supplied public keys pinned to the registered immutable policy.
-- This is a consistency proof under the PG0 trusted database-owner contract.
CREATE FUNCTION life_financial.spot_operator_approval_integrity(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r life_financial.spot_operator_approvals; b life_financial.bill_approvals; audit jsonb; payload jsonb; auth jsonb; policy jsonb;
BEGIN
    FOR r IN SELECT * FROM life_financial.spot_operator_approvals WHERE account_id=p_account LOOP
        audit:=r.body; payload:=audit->'payload'; auth:=audit->'approval';
        SELECT * INTO b FROM life_financial.bill_approvals WHERE account_id=p_account AND bill_id=r.bill_id;
        IF NOT FOUND THEN RETURN false; END IF;
        SELECT c.body->'operator_verifier' INTO policy FROM life_financial.configurations c
            WHERE c.account_id=p_account AND c.config_hash=auth#>>'{binding,config_hash}' AND c.policy_hash=auth#>>'{binding,policy_hash}';
        IF policy IS NULL OR policy IS DISTINCT FROM audit->'verifier_policy'
            OR NOT COALESCE(policy#>ARRAY[auth->>'operator_id','roles'] @> '["APPROVE_CASHFLOW"]'::jsonb,false)
            OR auth->>'role' IS DISTINCT FROM 'APPROVE_CASHFLOW' OR payload->>'kind' IS DISTINCT FROM 'APPROVE_CASHFLOW'
            OR payload->>'operation_id' IS DISTINCT FROM r.operation_id OR payload#>>'{bill,bill_id}' IS DISTINCT FROM r.bill_id
            OR payload#>>'{bill,currency}' IS DISTINCT FROM b.asset OR (payload#>>'{bill,signed_amount}')::numeric IS DISTINCT FROM b.signed_amount
            OR (payload#>>'{bill,transfer_at_ms}')::bigint IS DISTINCT FROM floor(extract(epoch FROM b.transfer_at)*1000)::bigint
            OR audit->'signature' IS NULL OR decode(audit->>'signature','hex') IS DISTINCT FROM b.signature OR octet_length(b.signature)<>64
            OR audit#>'{cut,checkpoint,binding}' IS DISTINCT FROM auth->'binding'
            OR audit#>'{facts,binding}' IS DISTINCT FROM auth->'binding'
            OR r.captured_at<(auth->>'issued_at')::timestamptz OR r.captured_at>=(auth->>'expires_at')::timestamptz
            OR r.captured_at<(audit#>>'{facts,observed_at}')::timestamptz
            OR extract(epoch FROM (r.captured_at-(audit#>>'{facts,observed_at}')::timestamptz))*1000>r.max_fact_age_ms
            OR audit#>'{facts,order_scope_complete}' IS DISTINCT FROM 'true'::jsonb
            OR audit#>'{facts,bill_scope_complete}' IS DISTINCT FROM 'true'::jsonb
            OR audit#>'{facts,runner_scope_complete}' IS DISTINCT FROM 'true'::jsonb
            OR NOT (audit#>'{facts,account_cashflow_ids}') @> jsonb_build_array(r.bill_id)
            OR r.source_hash IS DISTINCT FROM encode(sha256(convert_to(audit::text,'UTF8')),'hex') THEN RETURN false; END IF;
    END LOOP;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

ALTER FUNCTION life_financial.spot_capital_verified(uuid) RENAME TO spot_capital_verified_v19;
CREATE FUNCTION life_financial.spot_capital_verified(p_account uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT life_financial.spot_capital_verified_v19(p_account) AND life_financial.spot_operator_approval_integrity(p_account);
$$;

CREATE FUNCTION life_financial.spot_operator_approval_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE at timestamptz:=clock_timestamp();
BEGIN
    IF NOT life_financial.spot_operator_approval_integrity(NEW.account_id) THEN
        RAISE EXCEPTION 'LIFE_OPERATOR_APPROVAL_HISTORY_INVALID' USING ERRCODE='P0001'; END IF;
    IF TG_TABLE_NAME='spot_operator_approvals' THEN
        IF (SELECT to_jsonb(a) FROM life_financial.accounts a WHERE account_id=NEW.account_id) IS DISTINCT FROM NEW.body->'account'
            OR at>=(NEW.body#>>'{approval,expires_at}')::timestamptz
            OR at<(NEW.body#>>'{facts,observed_at}')::timestamptz
            OR extract(epoch FROM (at-(NEW.body#>>'{facts,observed_at}')::timestamptz))*1000>NEW.max_fact_age_ms THEN
            RAISE EXCEPTION 'LIFE_OPERATOR_APPROVAL_CONTEXT_CHANGED' USING ERRCODE='P0001'; END IF;
    END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER operator_approval_at_commit AFTER INSERT ON life_financial.spot_operator_approvals
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.spot_operator_approval_commit_guard();
CREATE CONSTRAINT TRIGGER operator_approval_financial_commit AFTER UPDATE ON life_financial.accounts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.spot_operator_approval_commit_guard();
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
