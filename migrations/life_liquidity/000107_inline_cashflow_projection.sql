-- Expose the raw effective-cashflow relation to the planner. No history proof,
-- verdict, financial command, receipt, timestamp or privilege is cached/removed.
-- The private helper inherits its calling security-definer owner's access.
-- Every relation, operator and cast is pinned without a SET clause that would
-- prevent SQL table-function inlining. Existing OID/owner/ACL stay unchanged.
DO $$ DECLARE p record;BEGIN
    SELECT f.*,l.lanname INTO STRICT p FROM pg_proc f JOIN pg_language l ON l.oid=f.prolang
        WHERE f.oid='life_financial.cashflow_effective(uuid)'::regprocedure;
    IF p.lanname<>'sql' OR p.provolatile<>'s' OR NOT p.prosecdef OR p.proisstrict
        OR p.proleakproof OR p.proparallel<>'u' OR NOT p.proretset OR p.prokind<>'f'
        OR p.pronargdefaults<>0 OR p.proargnames IS DISTINCT FROM ARRAY['p_account']
        OR p.prorettype<>'life_financial.spot_cashflow_facts'::regtype
        OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
        OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') IS DISTINCT FROM
            'b84c2547472f3a8a75cb49f27bf1dd6ec80623857265b581fb8012cb789b011e' THEN
        RAISE EXCEPTION 'LIFE_CASHFLOW_PROJECTION_MIGRATION_MISMATCH';END IF;
END; $$;

CREATE OR REPLACE FUNCTION life_financial.cashflow_effective(p_account pg_catalog.uuid)
RETURNS SETOF life_financial.spot_cashflow_facts
LANGUAGE sql STABLE SECURITY INVOKER AS $$
SELECT f.* FROM life_financial.spot_cashflow_facts f
    WHERE f.account_id OPERATOR(pg_catalog.=) p_account AND NOT EXISTS(
        SELECT 1 FROM life_financial.spot_cashflow_applications a
        WHERE a.account_id OPERATOR(pg_catalog.=) f.account_id
            AND a.bill_id OPERATOR(pg_catalog.=) f.bill_id)
UNION ALL
SELECT a.account_id,a.bill_id,a.command,'APPLIED'::pg_catalog.text,NULL::life_financial.identity,
    a.source_operation_id,a.source_hash
FROM life_financial.spot_cashflow_applications a WHERE a.account_id OPERATOR(pg_catalog.=) p_account;
$$;
ALTER FUNCTION life_financial.cashflow_effective(pg_catalog.uuid) RESET ALL;
