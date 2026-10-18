-- Retired outer SWAP replay requires already qualified ORIGINAL native basis
-- and order/claim/ACK history. This migration creates or reprices none of it.
-- One initial source per namespace; expired unpublished approvals may have
-- explicit signed successors without overwriting history or branching grants.
ALTER TABLE life_financial.legacy_applying_imports ADD COLUMN supersedes_operation_id life_financial.identity;
ALTER TABLE life_financial.legacy_applying_imports ADD FOREIGN KEY(account_id,supersedes_operation_id)
    REFERENCES life_financial.legacy_applying_imports(account_id,operation_id);
CREATE UNIQUE INDEX legacy_applying_one_source ON life_financial.legacy_applying_imports(account_id) WHERE supersedes_operation_id IS NULL;
CREATE UNIQUE INDEX legacy_applying_successor_source ON life_financial.legacy_applying_imports(account_id,supersedes_operation_id) WHERE supersedes_operation_id IS NOT NULL;
CREATE FUNCTION life_financial.legacy_joint_basis_valid(p_account uuid,b jsonb,p_version bigint) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.swap_fill_policies;r jsonb;c life_financial.shared_capital_claims;
    d life_financial.swap_preparations;cp jsonb;expected jsonb;
BEGIN
    SELECT * INTO STRICT p FROM life_financial.swap_fill_policies WHERE account_id=p_account;
    IF b->'swap_opening' IS DISTINCT FROM jsonb_build_object('opening_long_base',p.opening_long_base::text,
        'opening_short_base',p.opening_short_base::text,'opening_long_entry',p.opening_long_entry::text,
        'opening_short_entry',p.opening_short_entry::text) THEN
        -- Equivalent numeric spellings are allowed; quantities are never remapped.
        IF (b#>>'{swap_opening,opening_long_base}')::numeric IS DISTINCT FROM p.opening_long_base
            OR (b#>>'{swap_opening,opening_short_base}')::numeric IS DISTINCT FROM p.opening_short_base
            OR (b#>>'{swap_opening,opening_long_entry}')::numeric IS DISTINCT FROM p.opening_long_entry
            OR (b#>>'{swap_opening,opening_short_entry}')::numeric IS DISTINCT FROM p.opening_short_entry THEN RETURN false;END IF;
    END IF;
    SELECT body INTO STRICT cp FROM life_financial.shared_capital_policies WHERE account_id=p_account;
    IF jsonb_typeof(b->'claims') IS DISTINCT FROM 'array' OR jsonb_array_length(b->'claims') NOT BETWEEN 1 AND 256
        OR (SELECT count(DISTINCT value#>>'{claim,intent_id}') FROM jsonb_array_elements(b->'claims'))<>jsonb_array_length(b->'claims') THEN RETURN false;END IF;
    FOR r IN SELECT value FROM jsonb_array_elements(b->'claims') LOOP
        SELECT * INTO STRICT c FROM life_financial.shared_capital_claims WHERE account_id=p_account AND intent_id=r#>>'{claim,intent_id}';
        IF NOT life_financial.shared_claim_equal(to_jsonb(c)-ARRAY['account_id','operation_id'],r->'claim')
            OR c.product<>'SWAP' OR NOT EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=p_account
                AND o.operation_id=c.operation_id AND o.committed_version<=p_version) THEN RETURN false;END IF;
        SELECT * INTO STRICT d FROM life_financial.swap_preparations WHERE account_id=p_account AND operation_id=r->>'prepare_operation_id';
        IF d.intent_id<>c.intent_id OR d.permit IS DISTINCT FROM life_financial.swap_permit(d.request->'config',c,cp)
            OR NOT EXISTS(SELECT 1 FROM life_financial.swap_intent_events e JOIN life_financial.operations o USING(account_id,operation_id)
                WHERE e.account_id=p_account AND e.prepare_operation_id=d.operation_id AND e.kind='SWAP_ACK'
                    AND e.exchange_order_id=r->>'exchange_order_id' AND o.committed_version<=p_version) THEN RETURN false;END IF;
    END LOOP;
    RETURN jsonb_array_length(b->'claims')=(SELECT count(*) FROM life_financial.shared_capital_claims retained
        JOIN life_financial.operations o ON o.account_id=retained.account_id AND o.operation_id=retained.operation_id
        WHERE retained.account_id=p_account AND o.committed_version<=p_version);
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
CREATE FUNCTION life_financial.legacy_joint_verified(p_account uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT (SELECT count(DISTINCT source_digest)<=1 FROM life_financial.legacy_applying_imports WHERE account_id=p_account)
    AND NOT EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports i WHERE i.account_id=p_account
    AND (i.body#>>'{payload,supersedes_operation_id}' IS DISTINCT FROM i.supersedes_operation_id::text
        OR (i.supersedes_operation_id IS NOT NULL AND NOT EXISTS(
            SELECT 1 FROM life_financial.legacy_applying_imports previous WHERE previous.account_id=p_account
                AND previous.operation_id=i.supersedes_operation_id AND previous.captured_at<i.captured_at
                AND previous.source_digest=i.source_digest AND previous.body->'source'=i.body->'source'
                AND previous.body->'retirement'=i.body->'retirement' AND previous.body->'retired_at'=i.body->'retired_at'
                AND previous.body->'source_verifier_policy'=i.body->'source_verifier_policy'
                AND NOT EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=p_account AND o.operation_id=previous.operation_id)))
        OR CASE WHEN i.body->'source_basis' ? 'swap_opening' THEN
        NOT life_financial.legacy_joint_basis_valid(p_account,i.body->'source_basis',(i.body#>>'{cut,checkpoint,revision}')::bigint)
        ELSE jsonb_array_length(i.body#>'{payload,batch,swap_fills}')<>0 END));
$$;
DO $$ DECLARE definition text;BEGIN
    definition:=pg_get_functiondef('life_financial.legacy_applying_verified(uuid)'::regprocedure);
    IF position('legacy_joint_verified' IN definition)>0 THEN RAISE EXCEPTION 'LIFE_LEGACY_JOINT_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,'BEGIN','BEGIN
    IF NOT life_financial.legacy_joint_verified(p_account) THEN RETURN false;END IF;');
END; $$;

-- Only the unsuperseded leaf is an outstanding publication barrier. A fresh
-- successor does not clear that barrier; it must still publish native pending.
DO $$ DECLARE definition text;needle text:='AND NOT EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=i.account_id AND o.operation_id=i.operation_id AND o.command->>''kind''=''JOINT_BATCH'')';BEGIN
    definition:=pg_get_functiondef('life_financial.legacy_applying_publication_guard()'::regprocedure);
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_SUCCESSOR_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,'AND NOT EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports successor WHERE successor.account_id=i.account_id AND successor.supersedes_operation_id=i.operation_id) '||needle);
END; $$;
DO $$ DECLARE definition text;needle text:='IF NOT life_financial.legacy_applying_verified(p_account) OR NOT a.halted';BEGIN
    definition:=pg_get_functiondef('life_financial.legacy_applying_fresh(uuid,text)'::regprocedure);
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_SUCCESSOR_FRESH_MISMATCH';END IF;
    EXECUTE replace(definition,needle,'IF EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports successor WHERE successor.account_id=p_account AND successor.supersedes_operation_id=p_operation) OR NOT life_financial.legacy_applying_verified(p_account) OR NOT a.halted');
END; $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
