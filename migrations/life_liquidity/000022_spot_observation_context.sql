-- Diagnose inconsistent preparation receipts before further financial writes.
-- This does not independently anchor history against a privileged DB owner.
CREATE OR REPLACE FUNCTION life_financial.spot_prepare_receipts_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    -- Audit both directions, including retained terminal/aborted identities.
    -- A missing retired descriptor must not become a smaller "complete" scope.
    RETURN NOT EXISTS(
        SELECT 1 FROM life_financial.send_attempts t
        LEFT JOIN life_financial.spot_preparations d USING(account_id,intent_id)
        WHERE t.account_id=p_account AND t.product='SPOT'
            AND (d.operation_id IS NULL OR d.operation_id IS DISTINCT FROM t.operation_id))
    AND NOT EXISTS(
        SELECT 1 FROM life_financial.operations o
        LEFT JOIN life_financial.spot_preparations d USING(account_id,operation_id)
        WHERE o.account_id=p_account AND o.command->>'kind'='SPOT_PREPARE' AND d.operation_id IS NULL)
    AND NOT EXISTS(
        SELECT 1 FROM life_financial.spot_preparations d
        LEFT JOIN life_financial.operations o USING(account_id,operation_id)
        WHERE d.account_id=p_account AND (
            o.command->>'kind' IS DISTINCT FROM 'SPOT_PREPARE'
            OR o.command->'command' IS DISTINCT FROM d.request
            OR o.receipt IS DISTINCT FROM (jsonb_build_object(
                'kind','SPOT_PREPARE','operation_id',d.operation_id,'intent_id',d.intent_id,
                'state','PREPARED','version',o.committed_version,'replayed',false,'trading_permission',false)
                ||CASE WHEN o.command ? 'action' THEN jsonb_build_object(
                    'states',jsonb_build_array(jsonb_build_array(d.intent_id,'PREPARED'))) ELSE '{}'::jsonb END)));
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

-- One bounded primary snapshot for provider reconciliation. Read only; no grant.
CREATE FUNCTION life_financial.spot_observation_context(p_account uuid,p_rows integer,p_bytes integer) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE view jsonb; result jsonb; n bigint; bytes bigint;
    p life_financial.spot_capital_policies; f life_financial.spot_fill_policies;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
    IF p_rows IS NULL OR p_rows NOT BETWEEN 1 AND 4096 OR p_bytes IS NULL OR p_bytes NOT BETWEEN 1 AND 16777216 THEN
        RAISE EXCEPTION 'LIFE_SPOT_CONTEXT_READ_BOUND_INVALID' USING ERRCODE='P0001'; END IF;
    -- Materialize only bounded identities/sizes before constructing payloads.
    SELECT count(*),COALESCE(sum(x.bytes),0) INTO n,bytes FROM (
            SELECT * FROM (
                SELECT 'COMMAND'::text AS kind,operation_id::text AS id,octet_length(request::text)::bigint+256 AS bytes
                    FROM life_financial.spot_preparations WHERE account_id=p_account
                UNION ALL SELECT 'FILL',trade_id,octet_length(observation::text)::bigint+512
                    FROM life_financial.spot_fill_facts WHERE account_id=p_account
                UNION ALL SELECT 'CASHFLOW',bill_id,octet_length(command::text)::bigint+256
                    FROM life_financial.spot_cashflow_facts WHERE account_id=p_account
                UNION ALL SELECT 'APPROVAL',bill_id,512::bigint FROM life_financial.bill_approvals WHERE account_id=p_account
                UNION ALL SELECT 'ACK',r.ingress_id,octet_length(r.body::text)::bigint+256 FROM life_financial.spot_ack_ingress r
                    WHERE r.account_id=p_account AND NOT EXISTS(SELECT 1 FROM life_financial.spot_ack_ingress_receipts d
                        WHERE d.account_id=p_account AND d.ingress_id=r.ingress_id)
                UNION ALL SELECT 'POLICY',policy_hash,1024::bigint FROM life_financial.spot_capital_policies WHERE account_id=p_account
                UNION ALL SELECT 'FILL_POLICY',policy_hash,128::bigint FROM life_financial.spot_fill_policies
                    WHERE account_id=p_account AND policy_hash=(SELECT policy_hash FROM life_financial.accounts WHERE account_id=p_account)
            ) candidates LIMIT p_rows+1
        ) x;
    IF n>p_rows OR bytes>p_bytes THEN RAISE EXCEPTION 'LIFE_SPOT_CONTEXT_READ_BOUND' USING ERRCODE='P0001'; END IF;
    view:=life_financial.spot_state(p_account);
    IF (view->>'unverified')::boolean THEN RAISE EXCEPTION 'LIFE_SPOT_CONTEXT_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    -- A known pending financial command must not disappear into an empty DTO.
    -- Its original-price, signed resolution port is a separate operation.
    IF EXISTS(SELECT 1 FROM life_financial.spot_pending_observations WHERE account_id=p_account)
        OR EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts WHERE account_id=p_account AND state<>'APPLIED') THEN
        RAISE EXCEPTION 'LIFE_SPOT_CONTEXT_PENDING_RESOLUTION_REQUIRED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO p FROM life_financial.spot_capital_policies WHERE account_id=p_account;
    IF NOT FOUND OR p.policy_hash IS DISTINCT FROM view#>>'{account,policy_hash}' THEN
        RAISE EXCEPTION 'LIFE_SPOT_CONTEXT_POLICY_REQUIRED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO f FROM life_financial.spot_fill_policies WHERE account_id=p_account AND policy_hash=p.policy_hash;
    IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_SPOT_CONTEXT_POLICY_REQUIRED' USING ERRCODE='P0001'; END IF;
    result:=jsonb_build_object('snapshot',view,
        'policy',jsonb_build_object('opening_base_life',p.opening_base::text,'opening_quote_usdt',p.opening_quote::text,
            'opening_independent_price_usdt',p.opening_price::text,'opened_at',p.opened_at,'anchor_bill_id',p.anchor_bill),
        'fill_policy',jsonb_build_object('max_reference_skew_ms',f.max_reference_skew_ms),
        'commands',COALESCE((SELECT jsonb_agg(request ORDER BY operation_id COLLATE "C")
            FROM life_financial.spot_preparations WHERE account_id=p_account),'[]'::jsonb),
        'fills',COALESCE((SELECT jsonb_agg(jsonb_build_object('prepare_operation_id',d.operation_id,'intent_id',x.intent_id,
            'exchange_order_id',x.exchange_order_id,'fill',x.observation) ORDER BY x.trade_id COLLATE "C")
            FROM life_financial.spot_fill_facts x JOIN life_financial.spot_preparations d USING(account_id,intent_id)
            WHERE x.account_id=p_account),'[]'::jsonb),
        'cashflows',COALESCE((SELECT jsonb_agg(command ORDER BY bill_id COLLATE "C")
            FROM life_financial.spot_cashflow_facts WHERE account_id=p_account),'[]'::jsonb),
        'approvals',COALESCE((SELECT jsonb_agg(jsonb_build_object('bill_id',bill_id,'currency',asset,'signed_amount',signed_amount::text,
            'transfer_at_ms',floor(extract(epoch FROM transfer_at)*1000)::bigint) ORDER BY bill_id::numeric)
            FROM life_financial.bill_approvals WHERE account_id=p_account),'[]'::jsonb),
        'ack_pending',COALESCE((SELECT jsonb_agg(r.body ORDER BY r.ingress_id COLLATE "C") FROM life_financial.spot_ack_ingress r
            WHERE r.account_id=p_account AND NOT EXISTS(SELECT 1 FROM life_financial.spot_ack_ingress_receipts d
                WHERE d.account_id=p_account AND d.ingress_id=r.ingress_id)),'[]'::jsonb));
    IF octet_length(result::text)>p_bytes THEN RAISE EXCEPTION 'LIFE_SPOT_CONTEXT_READ_BOUND' USING ERRCODE='P0001'; END IF;
    RETURN result;
END; $$;
REVOKE ALL ON FUNCTION life_financial.spot_observation_context(uuid,integer,integer) FROM PUBLIC;
