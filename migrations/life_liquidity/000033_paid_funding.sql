-- Settled funding is actual account cash, not expected income, an external
-- transfer, an execution loss or an automatic release/activation grant.
CREATE TABLE life_financial.funding_accounting_policies (
    account_id uuid PRIMARY KEY REFERENCES life_financial.swap_fill_policies,
    policy_hash life_financial.digest NOT NULL,
    max_payments integer NOT NULL CHECK(max_payments BETWEEN 1 AND 4096),
    max_observations integer NOT NULL CHECK(max_observations BETWEEN 1 AND 4096),
    max_total_bytes integer NOT NULL CHECK(max_total_bytes BETWEEN 1 AND 16777216),
    FOREIGN KEY(account_id,policy_hash) REFERENCES life_financial.policies
);
CREATE TABLE life_financial.funding_observations (
    account_id uuid NOT NULL REFERENCES life_financial.funding_accounting_policies,
    operation_id life_financial.identity NOT NULL,bundle jsonb NOT NULL,
    previous_funding_operation life_financial.identity,previous_carry_operation life_financial.identity,
    received_at_ms bigint NOT NULL CHECK(received_at_ms>0),state text NOT NULL CHECK(state IN ('APPLIED','RECOVERY_REQUIRED')),
    reason life_financial.identity,cash_delta_usdt life_financial.signed_amount NOT NULL,gross_debits_usdt life_financial.amount NOT NULL,
    PRIMARY KEY(account_id,operation_id),
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(account_id,previous_funding_operation) REFERENCES life_financial.funding_observations,
    FOREIGN KEY(account_id,previous_carry_operation) REFERENCES life_financial.carry_checks,
    CHECK(state='APPLIED' AND reason IS NULL OR state='RECOVERY_REQUIRED' AND reason IS NOT NULL AND cash_delta_usdt=0)
);
CREATE TABLE life_financial.funding_cash_payments (
    account_id uuid NOT NULL REFERENCES life_financial.funding_accounting_policies,
    bill_id text NOT NULL CHECK(bill_id ~ '^[0-9]{1,64}$'),settled_at_ms bigint NOT NULL CHECK(settled_at_ms>0),
    signed_payment_usdt life_financial.signed_amount NOT NULL,source_operation_id life_financial.identity NOT NULL,
    source_hash life_financial.digest NOT NULL,PRIMARY KEY(account_id,bill_id),
    FOREIGN KEY(account_id,source_operation_id) REFERENCES life_financial.funding_observations
);
CREATE INDEX funding_observation_history ON life_financial.operations(account_id,committed_version) WHERE command->>'kind'='PAID_FUNDING';
DO $$ DECLARE table_name text;BEGIN
    FOREACH table_name IN ARRAY ARRAY['funding_accounting_policies','funding_observations','funding_cash_payments'] LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',table_name);
        EXECUTE format('CREATE POLICY scoped_read ON life_financial.%I FOR SELECT USING(life_financial.can_access(account_id))',table_name);
        EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',table_name);
    END LOOP;
END; $$;

CREATE FUNCTION life_financial.funding_bundle_reason(p_account uuid,b jsonb,previous_funding text,previous_carry text,at_ms bigint) RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE policy_row life_financial.funding_accounting_policies;carry_policy life_financial.carry_policies;
    capital_policy jsonb;prior jsonb;result text;opening_ms bigint;
BEGIN
    SELECT * INTO STRICT policy_row FROM life_financial.funding_accounting_policies WHERE account_id=p_account;
    SELECT * INTO STRICT carry_policy FROM life_financial.carry_policies WHERE account_id=p_account AND policy_hash=policy_row.policy_hash;
    SELECT body INTO STRICT capital_policy FROM life_financial.shared_capital_policies WHERE account_id=p_account AND policy_hash=policy_row.policy_hash;
    SELECT floor(extract(epoch FROM opened_at)*1000)::bigint INTO STRICT opening_ms FROM life_financial.spot_capital_policies
        WHERE account_id=p_account AND policy_hash=policy_row.policy_hash;
    IF policy_row.max_payments>carry_policy.max_payments OR (b#>>'{funding,coverage_from_ms}')::bigint>opening_ms THEN
        RETURN 'FUNDING_OPENING_COVERAGE_INVALID';END IF;
    IF previous_funding IS NOT NULL THEN
        SELECT bundle INTO STRICT prior FROM life_financial.funding_observations WHERE account_id=p_account AND operation_id=previous_funding AND state='APPLIED';
    END IF;
    result:=life_financial.carry_bundle_reason(carry_policy.body,capital_policy,b,prior,b#>'{account,snapshot}',at_ms,policy_row.max_payments);
    IF result<>'CARRY_QUALIFIED' THEN RETURN result;END IF;
    IF previous_carry IS NOT NULL THEN
        SELECT bundle INTO STRICT prior FROM life_financial.carry_checks WHERE account_id=p_account AND operation_id=previous_carry AND qualified;
        result:=life_financial.carry_bundle_reason(carry_policy.body,capital_policy,b,prior,b#>'{account,snapshot}',at_ms,policy_row.max_payments);
        IF result<>'CARRY_QUALIFIED' THEN RETURN result;END IF;
    END IF;
    IF EXISTS(SELECT 1 FROM jsonb_array_elements(b#>'{funding,payments}') payment WHERE payment->>'bill_id' !~ '^[0-9]{1,64}$') THEN
        RETURN 'FUNDING_BILL_ID_INVALID';END IF;
    RETURN 'FUNDING_QUALIFIED';
EXCEPTION WHEN OTHERS THEN RETURN 'FUNDING_BUNDLE_INVALID';
END; $$;

CREATE FUNCTION life_financial.funding_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE policy_row life_financial.funding_accounting_policies;observation_row life_financial.funding_observations;
    payment_row life_financial.funding_cash_payments;operation_row life_financial.operations;original jsonb;item jsonb;
    expected jsonb;opening_ms bigint;total_debits numeric;delta numeric;prior_version bigint;
BEGIN
    SELECT * INTO policy_row FROM life_financial.funding_accounting_policies WHERE account_id=p_account;
    IF NOT FOUND THEN RETURN NOT EXISTS(SELECT 1 FROM life_financial.funding_observations WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.funding_cash_payments WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.financial_events WHERE account_id=p_account AND kind='FUNDING')
        AND NOT EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=p_account AND command->>'kind'='PAID_FUNDING');END IF;
    IF policy_row.policy_hash IS DISTINCT FROM (SELECT policy_hash FROM life_financial.accounts WHERE account_id=p_account)
        OR (SELECT count(*) FROM life_financial.funding_observations WHERE account_id=p_account)>policy_row.max_observations
        OR (SELECT count(*) FROM life_financial.funding_cash_payments WHERE account_id=p_account)>policy_row.max_payments
        OR (SELECT COALESCE(sum(octet_length(bundle::text)),0) FROM life_financial.funding_observations WHERE account_id=p_account)>policy_row.max_total_bytes THEN RETURN false;END IF;
    SELECT floor(extract(epoch FROM opened_at)*1000)::bigint INTO STRICT opening_ms FROM life_financial.spot_capital_policies WHERE account_id=p_account;
    FOR observation_row IN SELECT * FROM life_financial.funding_observations WHERE account_id=p_account LOOP
        SELECT * INTO STRICT operation_row FROM life_financial.operations WHERE account_id=p_account AND operation_id=observation_row.operation_id;
        original:=operation_row.command;
        IF original->>'kind' IS DISTINCT FROM 'PAID_FUNDING' OR original#>>'{command,operation_id}' IS DISTINCT FROM observation_row.operation_id
            OR original#>'{command,bundle}' IS DISTINCT FROM observation_row.bundle
            OR operation_row.fingerprint<>encode(sha256(convert_to(life_financial.canonical_json_v1(original),'UTF8')),'hex')
            OR observation_row.received_at_ms<>floor(extract(epoch FROM operation_row.recorded_at)*1000)::bigint THEN RETURN false;END IF;
        SELECT max(op.committed_version) INTO prior_version FROM life_financial.funding_observations prev
            JOIN life_financial.operations op USING(account_id,operation_id) WHERE prev.account_id=p_account AND prev.state='APPLIED'
                AND op.committed_version<operation_row.committed_version;
        IF observation_row.previous_funding_operation IS DISTINCT FROM (SELECT operation_id FROM life_financial.operations
            WHERE account_id=p_account AND committed_version=prior_version) THEN RETURN false;END IF;
        IF observation_row.previous_carry_operation IS DISTINCT FROM (SELECT check_row.operation_id FROM life_financial.carry_checks check_row
            JOIN life_financial.operations op USING(account_id,operation_id) WHERE check_row.account_id=p_account AND check_row.qualified
                AND op.committed_version<operation_row.committed_version ORDER BY op.committed_version DESC LIMIT 1) THEN RETURN false;END IF;
        delta:=0;total_debits:=0;
        IF observation_row.state='APPLIED' THEN
            IF life_financial.funding_bundle_reason(p_account,observation_row.bundle,observation_row.previous_funding_operation,
                observation_row.previous_carry_operation,observation_row.received_at_ms)<>'FUNDING_QUALIFIED' THEN RETURN false;END IF;
            FOR item IN SELECT value FROM jsonb_array_elements(observation_row.bundle#>'{funding,payments}') LOOP
                total_debits:=total_debits+greatest(0,-(item->>'signed_payment_quote')::numeric);
                IF (item->>'settled_at_ms')::bigint<opening_ms THEN CONTINUE;END IF;
                SELECT * INTO STRICT payment_row FROM life_financial.funding_cash_payments WHERE account_id=p_account AND bill_id=item->>'bill_id';
                IF payment_row.settled_at_ms<>(item->>'settled_at_ms')::bigint OR payment_row.signed_payment_usdt<>(item->>'signed_payment_quote')::numeric
                    OR payment_row.source_hash<>encode(sha256(convert_to(life_financial.canonical_json_v1(item),'UTF8')),'hex') THEN RETURN false;END IF;
                IF payment_row.source_operation_id=observation_row.operation_id THEN delta:=delta+payment_row.signed_payment_usdt;
                ELSIF (SELECT committed_version FROM life_financial.operations WHERE account_id=p_account AND operation_id=payment_row.source_operation_id)
                    >=operation_row.committed_version THEN RETURN false;END IF;
            END LOOP;
            IF observation_row.cash_delta_usdt<>delta OR observation_row.gross_debits_usdt<>total_debits OR observation_row.reason IS NOT NULL THEN RETURN false;END IF;
        ELSIF observation_row.reason IS NULL OR observation_row.cash_delta_usdt<>0 OR observation_row.gross_debits_usdt<>0 THEN RETURN false;END IF;
        expected:=jsonb_build_object('kind','PAID_FUNDING','operation_id',observation_row.operation_id,'state',observation_row.state,
            'cash_delta_usdt',observation_row.cash_delta_usdt::text,'gross_debits_usdt',observation_row.gross_debits_usdt::text,
            'version',operation_row.committed_version,'replayed',false,'trading_permission',false);
        IF observation_row.reason IS NOT NULL THEN expected:=expected||jsonb_build_object('reason',observation_row.reason);END IF;
        IF operation_row.receipt IS DISTINCT FROM expected THEN RETURN false;END IF;
    END LOOP;
    FOR payment_row IN SELECT * FROM life_financial.funding_cash_payments WHERE account_id=p_account LOOP
        IF payment_row.settled_at_ms<opening_ms OR NOT EXISTS(SELECT 1 FROM life_financial.funding_observations src
            WHERE src.account_id=p_account AND src.operation_id=payment_row.source_operation_id AND src.state='APPLIED'
                AND EXISTS(SELECT 1 FROM jsonb_array_elements(src.bundle#>'{funding,payments}') value WHERE value->>'bill_id'=payment_row.bill_id
                    AND (value->>'settled_at_ms')::bigint=payment_row.settled_at_ms AND (value->>'signed_payment_quote')::numeric=payment_row.signed_payment_usdt))
            OR NOT EXISTS(SELECT 1 FROM life_financial.financial_events event WHERE event.account_id=p_account AND event.product='ACCOUNT' AND event.kind='FUNDING'
                AND event.event_id='funding:'||encode(sha256(convert_to(payment_row.bill_id,'UTF8')),'hex') AND event.exchange_identity=payment_row.bill_id
                AND event.occurred_at=to_timestamp(payment_row.settled_at_ms/1000.0) AND event.source_hash=payment_row.source_hash
                AND event.base_delta=0 AND event.quote_delta=payment_row.signed_payment_usdt) THEN RETURN false;END IF;
    END LOOP;
    RETURN NOT EXISTS(SELECT 1 FROM life_financial.financial_events event WHERE event.account_id=p_account AND event.kind='FUNDING'
        AND (event.product<>'ACCOUNT' OR NOT EXISTS(SELECT 1 FROM life_financial.funding_cash_payments pay WHERE pay.account_id=p_account AND pay.bill_id=event.exchange_identity)))
        AND NOT EXISTS(SELECT 1 FROM life_financial.operations op WHERE op.account_id=p_account AND op.command->>'kind'='PAID_FUNDING'
            AND NOT EXISTS(SELECT 1 FROM life_financial.funding_observations obs WHERE obs.account_id=p_account AND obs.operation_id=op.operation_id));
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.funding_read(p_account uuid,p_rows integer,p_bytes integer,p_operation text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID' USING ERRCODE='P0001';END IF;
    IF p_rows IS NULL OR p_rows NOT BETWEEN 1 AND 4096 OR p_bytes IS NULL OR p_bytes NOT BETWEEN 1 AND 16777216 THEN
        RAISE EXCEPTION 'LIFE_FUNDING_READ_BOUND_INVALID' USING ERRCODE='P0001';END IF;
    IF (SELECT count(*) FROM (SELECT 1 FROM life_financial.funding_observations WHERE account_id=p_account LIMIT p_rows+1) limited)>p_rows
        OR (SELECT count(*) FROM (SELECT 1 FROM life_financial.funding_cash_payments WHERE account_id=p_account LIMIT p_rows+1) limited)>p_rows THEN
        RAISE EXCEPTION 'LIFE_FUNDING_READ_BOUND' USING ERRCODE='P0001';END IF;
    IF NOT life_financial.funding_verified(p_account) OR NOT life_financial.spot_capital_verified(p_account) THEN
        RAISE EXCEPTION 'LIFE_FUNDING_UNVERIFIED' USING ERRCODE='P0001';END IF;
    IF p_operation IS NULL THEN
        result:=jsonb_build_object('payments',COALESCE((SELECT jsonb_agg(jsonb_build_object('bill_id',bill_id,'settled_at_ms',settled_at_ms,
            'signed_payment_usdt',signed_payment_usdt::text,'source_operation_id',source_operation_id) ORDER BY settled_at_ms,bill_id)
                FROM life_financial.funding_cash_payments WHERE account_id=p_account),'[]'::jsonb),
            'pending',COALESCE((SELECT jsonb_agg(jsonb_build_object('operation_id',operation_id,'command',jsonb_build_object('operation_id',operation_id,'bundle',bundle),'reason',reason)
                ORDER BY operation_id) FROM life_financial.funding_observations WHERE account_id=p_account AND state='RECOVERY_REQUIRED'),'[]'::jsonb),
            'gross_debits_usdt',COALESCE((SELECT gross_debits_usdt::text FROM life_financial.funding_observations obs JOIN life_financial.operations op USING(account_id,operation_id)
                WHERE obs.account_id=p_account AND obs.state='APPLIED' ORDER BY op.committed_version DESC LIMIT 1),'0'),'trading_permission',false);
    ELSE SELECT jsonb_build_object('command',command,'receipt',receipt) INTO result FROM life_financial.operations
        WHERE account_id=p_account AND operation_id=p_operation AND command->>'kind'='PAID_FUNDING';END IF;
    IF octet_length(result::text)>p_bytes THEN RAISE EXCEPTION 'LIFE_FUNDING_READ_BOUND' USING ERRCODE='P0001';END IF;
    RETURN result;
END; $$;

CREATE FUNCTION life_financial.funding_command(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,cmd jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE account_row life_financial.accounts;policy_row life_financial.funding_accounting_policies;old_operation life_financial.operations;
    prior_payment life_financial.funding_cash_payments;previous_funding text;previous_carry text;op text;request jsonb;result jsonb;
    b jsonb;payment jsonb;snapshot jsonb;view jsonb;reason text;result_state text:='APPLIED';at_ms bigint;opening_ms bigint;
    delta numeric:=0;debits numeric:=0;source_hash text;wallet numeric;source_time timestamptz;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR jsonb_typeof(cmd) IS DISTINCT FROM 'object'
        OR (SELECT count(*) FROM jsonb_object_keys(cmd))<>2 OR NOT cmd ?& ARRAY['operation_id','bundle'] OR octet_length(cmd::text)>1048576
        OR jsonb_typeof(cmd->'operation_id') IS DISTINCT FROM 'string' OR cmd->>'operation_id' !~ '^[A-Za-z0-9_.:-]{1,96}$' THEN
        RAISE EXCEPTION 'LIFE_FUNDING_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
    op:=cmd->>'operation_id';b:=cmd->'bundle';snapshot:=b#>'{account,snapshot}';
    request:=jsonb_build_object('kind','PAID_FUNDING','binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',cmd);
    IF p_fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(request),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'LIFE_FUNDING_FINGERPRINT_INVALID' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT account_row FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,account_row.version);
    IF NOT life_financial.funding_verified(p_account) THEN RAISE EXCEPTION 'LIFE_FUNDING_UNVERIFIED' USING ERRCODE='P0001';END IF;
    SELECT * INTO old_operation FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF old_operation.command<>request OR old_operation.fingerprint<>p_fingerprint THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001';END IF;
        RETURN old_operation.receipt||'{"replayed":true}'::jsonb;END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    view:=life_financial.spot_state(p_account);
    IF view->>'digest' IS DISTINCT FROM p_cut THEN RAISE EXCEPTION 'LIFE_SPOT_CUT_CONFLICT' USING ERRCODE='P0001';END IF;
    IF (view->>'unverified')::boolean THEN RAISE EXCEPTION 'LIFE_FUNDING_UNVERIFIED' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT policy_row FROM life_financial.funding_accounting_policies WHERE account_id=p_account AND policy_hash=account_row.policy_hash;
    IF (SELECT count(*) FROM life_financial.funding_observations WHERE account_id=p_account)>=policy_row.max_observations
        OR (SELECT COALESCE(sum(octet_length(bundle::text)),0) FROM life_financial.funding_observations WHERE account_id=p_account)+octet_length(b::text)>policy_row.max_total_bytes THEN
        RAISE EXCEPTION 'LIFE_FUNDING_OBSERVATION_BOUND' USING ERRCODE='P0001';END IF;
    SELECT obs.operation_id INTO previous_funding FROM life_financial.funding_observations obs JOIN life_financial.operations src USING(account_id,operation_id)
        WHERE obs.account_id=p_account AND obs.state='APPLIED' ORDER BY src.committed_version DESC LIMIT 1;
    SELECT qualified_operation_id INTO previous_carry FROM life_financial.carry_heads WHERE account_id=p_account;
    SELECT floor(extract(epoch FROM opened_at)*1000)::bigint INTO STRICT opening_ms FROM life_financial.spot_capital_policies WHERE account_id=p_account;
    source_time:=clock_timestamp();at_ms:=floor(extract(epoch FROM source_time)*1000)::bigint;
    BEGIN
        reason:=life_financial.funding_bundle_reason(p_account,b,previous_funding,previous_carry,at_ms);
        IF reason<>'FUNDING_QUALIFIED' THEN RAISE EXCEPTION 'LIFE_FUNDING_SOURCE_UNQUALIFIED' USING ERRCODE='PZ001';END IF;
        IF NOT life_financial.swap_snapshot_current(p_account,snapshot) THEN reason:='FUNDING_POSITION_MISMATCH';RAISE EXCEPTION 'invalid' USING ERRCODE='PZ001';END IF;
        FOR payment IN SELECT value FROM jsonb_array_elements(b#>'{funding,payments}') LOOP
            debits:=debits+greatest(0,-(payment->>'signed_payment_quote')::numeric);
            IF (payment->>'settled_at_ms')::bigint<opening_ms THEN CONTINUE;END IF;
            SELECT * INTO prior_payment FROM life_financial.funding_cash_payments WHERE account_id=p_account AND bill_id=payment->>'bill_id';
            IF FOUND THEN
                IF prior_payment.settled_at_ms<>(payment->>'settled_at_ms')::bigint OR prior_payment.signed_payment_usdt<>(payment->>'signed_payment_quote')::numeric THEN
                    reason:='FUNDING_BILL_IDENTITY_CONFLICT';RAISE EXCEPTION 'invalid' USING ERRCODE='PZ001';END IF;
            ELSE delta:=delta+(payment->>'signed_payment_quote')::numeric;END IF;
        END LOOP;
        IF EXISTS(SELECT 1 FROM life_financial.funding_cash_payments pay WHERE pay.account_id=p_account AND NOT EXISTS(
            SELECT 1 FROM jsonb_array_elements(b#>'{funding,payments}') item WHERE item->>'bill_id'=pay.bill_id
                AND (item->>'settled_at_ms')::bigint=pay.settled_at_ms AND (item->>'signed_payment_quote')::numeric=pay.signed_payment_usdt)) THEN
            reason:='FUNDING_HISTORY_INCOMPLETE';RAISE EXCEPTION 'invalid' USING ERRCODE='PZ001';END IF;
        SELECT total INTO STRICT wallet FROM life_financial.balances WHERE account_id=p_account AND asset='USDT';
        IF wallet+delta<0 OR (snapshot->>'spot_cash_quote')::numeric<>wallet+delta
            OR (snapshot->>'spot_life_base')::numeric<>(SELECT total FROM life_financial.balances WHERE account_id=p_account AND asset='LIFE') THEN
            reason:='FUNDING_ACCOUNT_REPLAY_MISMATCH';RAISE EXCEPTION 'invalid' USING ERRCODE='PZ001';END IF;
        IF EXISTS(SELECT 1 FROM life_financial.funding_observations WHERE account_id=p_account AND state='RECOVERY_REQUIRED') THEN
            reason:='FUNDING_PRIOR_PENDING';RAISE EXCEPTION 'invalid' USING ERRCODE='PZ001';END IF;
        INSERT INTO life_financial.funding_observations VALUES(p_account,op,b,previous_funding,previous_carry,at_ms,'APPLIED',NULL,delta,debits);
        FOR payment IN SELECT value FROM jsonb_array_elements(b#>'{funding,payments}') LOOP
            IF (payment->>'settled_at_ms')::bigint<opening_ms OR EXISTS(SELECT 1 FROM life_financial.funding_cash_payments
                WHERE account_id=p_account AND bill_id=payment->>'bill_id') THEN CONTINUE;END IF;
            source_hash:=encode(sha256(convert_to(life_financial.canonical_json_v1(payment),'UTF8')),'hex');
            INSERT INTO life_financial.funding_cash_payments VALUES(p_account,payment->>'bill_id',(payment->>'settled_at_ms')::bigint,
                (payment->>'signed_payment_quote')::numeric,op,source_hash);
            INSERT INTO life_financial.financial_events(account_id,event_id,product,kind,exchange_identity,occurred_at,source_hash,base_delta,quote_delta)
                VALUES(p_account,'funding:'||encode(sha256(convert_to(payment->>'bill_id','UTF8')),'hex'),'ACCOUNT','FUNDING',payment->>'bill_id',
                    to_timestamp((payment->>'settled_at_ms')::bigint/1000.0),source_hash,0,(payment->>'signed_payment_quote')::numeric);
        END LOOP;
        UPDATE life_financial.balances SET total=total+delta,funding_deficit=greatest(0,reserved-total-delta),version=version+1
            WHERE account_id=p_account AND asset='USDT';
        view:=life_financial.spot_capital_replay(p_account);
        IF (view->>'quote')::numeric<>(SELECT total FROM life_financial.balances WHERE account_id=p_account AND asset='USDT') THEN
            reason:='FUNDING_CAUSAL_WALLET_MISMATCH';RAISE EXCEPTION 'invalid' USING ERRCODE='PZ001';END IF;
        IF debits>0 AND debits>=(SELECT (body->>'funding_budget_quote')::numeric FROM life_financial.carry_policies WHERE account_id=p_account)
            OR EXISTS(SELECT 1 FROM life_financial.balances WHERE account_id=p_account AND funding_deficit>0) THEN
            UPDATE life_financial.accounts SET halted=true WHERE account_id=p_account;END IF;
        reason:=NULL;
    EXCEPTION WHEN SQLSTATE 'PZ001' OR raise_exception OR check_violation OR numeric_value_out_of_range OR unique_violation THEN
        IF reason IS NULL OR reason='FUNDING_QUALIFIED' THEN reason:='FUNDING_FINANCIAL_CONSTRAINT_REJECTED';END IF;
        result_state:='RECOVERY_REQUIRED';delta:=0;debits:=0;
        INSERT INTO life_financial.funding_observations VALUES(p_account,op,b,previous_funding,previous_carry,at_ms,result_state,reason,0,0);
        UPDATE life_financial.accounts SET halted=true WHERE account_id=p_account;
    END;
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    result:=jsonb_build_object('kind','PAID_FUNDING','operation_id',op,'state',result_state,'cash_delta_usdt',delta::text,'gross_debits_usdt',debits::text,
        'version',p_version+1,'replayed',false,'trading_permission',false);
    IF reason IS NOT NULL THEN result:=result||jsonb_build_object('reason',reason);END IF;
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version,recorded_at)
        VALUES(p_account,op,p_fingerprint,request,result,p_version+1,source_time);
    RETURN result;
END; $$;

CREATE OR REPLACE FUNCTION life_financial.spot_accounting_verified(p_account uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT life_financial.funding_verified(p_account) AND life_financial.swap_accounting_verified(p_account) AND NOT EXISTS (
    SELECT 1 FROM life_financial.spot_preparations d JOIN life_financial.send_attempts t USING(account_id,intent_id)
    LEFT JOIN life_financial.spot_accounting_progress g USING(account_id,intent_id)
    LEFT JOIN LATERAL (SELECT COALESCE(sum(f.quantity),0) AS filled,
        COALESCE(sum(greatest(0,-(x.observation->>'signed_fee')::numeric)) FILTER(WHERE x.observation->>'fee_currency'='LIFE'),0) AS paid_base,
        COALESCE(sum(greatest(0,-(x.observation->>'signed_fee')::numeric)) FILTER(WHERE x.observation->>'fee_currency'='USDT'),0) AS paid_quote,
        COALESCE(sum(x.execution_loss),0) AS loss,
        count(DISTINCT x.exchange_order_id) AS order_ids,min(x.exchange_order_id) AS order_id
        FROM life_financial.spot_fill_facts x JOIN life_financial.fills f USING(account_id,product,trade_id)
        WHERE x.account_id=t.account_id AND x.intent_id=t.intent_id) totals ON true
    WHERE d.account_id=p_account AND (COALESCE(g.filled_base,0)<>totals.filled OR COALESCE(g.paid_base,0)<>totals.paid_base
        OR COALESCE(g.paid_quote,0)<>totals.paid_quote OR COALESCE(g.execution_loss,0)<>totals.loss
        OR COALESCE(g.filled_base,0)>t.quantity OR totals.order_ids>1
        OR totals.order_ids>0 AND (g.observed_order_id IS DISTINCT FROM totals.order_id
            OR t.exchange_order_id IS NOT NULL AND t.exchange_order_id<>totals.order_id))
) AND NOT EXISTS (
    SELECT 1 FROM life_financial.fills f LEFT JOIN life_financial.spot_fill_facts x USING(account_id,product,trade_id)
    LEFT JOIN life_financial.fees fee ON fee.account_id=f.account_id AND fee.payment_id='fee:'||encode(sha256(convert_to(f.trade_id,'UTF8')),'hex')
    LEFT JOIN life_financial.send_attempts t ON t.account_id=x.account_id AND t.intent_id=x.intent_id
    LEFT JOIN life_financial.financial_events e ON e.account_id=f.account_id AND e.product='SPOT' AND e.kind='FILL' AND e.exchange_identity=f.trade_id
    WHERE f.account_id=p_account AND f.product='SPOT' AND (f.product<>'SPOT' OR x.trade_id IS NULL OR fee.payment_id IS NULL OR e.event_id IS NULL
        OR f.wire_id<>t.wire_id OR f.quantity<>(x.observation->>'quantity_base_life')::numeric
        OR f.price<>(x.observation->>'price_usdt')::numeric OR f.independent_price<>(x.observation->>'independent_price_usdt')::numeric
        OR fee.product<>'SPOT' OR fee.trade_id<>f.trade_id OR fee.asset<>x.observation->>'fee_currency'
        OR fee.signed_fee<>(x.observation->>'signed_fee')::numeric
        OR f.source_hash<>encode(sha256(convert_to(x.observation::text,'UTF8')),'hex') OR e.source_hash<>f.source_hash
        OR e.base_delta<>(CASE WHEN t.side='BUY' THEN f.quantity ELSE -f.quantity END+CASE WHEN fee.asset='LIFE' THEN fee.signed_fee ELSE 0 END)
        OR e.quote_delta<>(CASE WHEN t.side='BUY' THEN -f.quantity*f.price ELSE f.quantity*f.price END+CASE WHEN fee.asset='USDT' THEN fee.signed_fee ELSE 0 END))
) AND NOT EXISTS (
    SELECT 1 FROM life_financial.fees fee WHERE fee.account_id=p_account AND fee.product='SPOT' AND NOT EXISTS(
        SELECT 1 FROM life_financial.spot_fill_facts x WHERE x.account_id=p_account AND fee.product='SPOT'
            AND fee.trade_id=x.trade_id AND fee.payment_id='fee:'||encode(sha256(convert_to(x.trade_id,'UTF8')),'hex'))
);
$$;
CREATE OR REPLACE FUNCTION life_financial.spot_capital_replay(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.spot_capital_policies; e record; j jsonb;
    base numeric; quote numeric; initial numeric; realized numeric:=0; net numeric:=0; loss numeric:=0;
    amount numeric; price numeric; reference numeric; fee numeric; sign numeric; consumed numeric;
    next_base numeric; next_quote numeric; n bigint;
BEGIN
    SELECT * INTO STRICT p FROM life_financial.spot_capital_policies WHERE account_id=p_account;
    IF p.policy_hash<>(SELECT policy_hash FROM life_financial.accounts WHERE account_id=p_account) THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_POLICY_MISMATCH' USING ERRCODE='P0001'; END IF;
    SELECT (SELECT count(*) FROM life_financial.spot_fill_facts WHERE account_id=p_account)+
           (SELECT count(*) FROM life_financial.spot_cashflow_facts WHERE account_id=p_account)+
           (SELECT count(*) FROM life_financial.swap_fill_facts WHERE account_id=p_account)+
           (SELECT count(*) FROM life_financial.funding_cash_payments WHERE account_id=p_account) INTO n;
    IF n>p.max_replay_events THEN RAISE EXCEPTION 'LIFE_CAPITAL_REPLAY_BOUND' USING ERRCODE='P0001'; END IF;
    base:=p.opening_base; quote:=p.opening_quote; initial:=base;
    FOR e IN SELECT * FROM (
        SELECT (x.command->>'transfer_at_ms')::bigint AS at_ms,0 AS kind,x.bill_id::text AS id,x.command AS data,NULL::text AS side
            FROM life_financial.spot_cashflow_facts x WHERE account_id=p_account AND state='APPLIED'
        UNION ALL
        SELECT (x.observation->>'fill_at_ms')::bigint,1,x.trade_id::text,x.observation,t.side
            FROM life_financial.spot_fill_facts x JOIN life_financial.send_attempts t USING(account_id,intent_id) WHERE x.account_id=p_account
        UNION ALL
        SELECT (x.observation->>'fill_at_ms')::bigint,2,('JOINT:'||(x.observation->>'fill_at_ms')||':TRADE:'||x.trade_id)::text,x.observation,NULL::text
            FROM life_financial.swap_fill_facts x WHERE x.account_id=p_account
        UNION ALL
        SELECT x.settled_at_ms,2,('JOINT:'||x.settled_at_ms||':FUNDING:'||x.bill_id)::text,
            jsonb_build_object('signed_payment_quote',x.signed_payment_usdt::text),NULL::text
            FROM life_financial.funding_cash_payments x WHERE x.account_id=p_account
        ) timeline ORDER BY at_ms,kind,id COLLATE "C"
    LOOP
        j:=e.data;
        IF e.at_ms<floor(extract(epoch FROM p.opened_at)*1000) THEN
            RAISE EXCEPTION 'LIFE_CAPITAL_CAUSAL_INVALID' USING ERRCODE='P0001'; END IF;
        reference:=(j->>'independent_price_usdt')::numeric;
        IF e.kind=2 THEN
            quote:=quote+CASE WHEN j ? 'signed_payment_quote' THEN (j->>'signed_payment_quote')::numeric
                ELSE (j->>'realized_pnl_usdt')::numeric+(j->>'signed_fee')::numeric END;
        ELSIF e.kind=0 THEN
            amount:=(j->>'signed_amount')::numeric;
            IF j->>'currency'='LIFE' THEN
                base:=base+amount;
                IF amount<0 THEN
                    consumed:=least(initial,-amount); initial:=initial-consumed;
                    realized:=realized+consumed*(reference-p.opening_price);
                END IF;
            ELSE quote:=quote+amount; END IF;
            net:=net+amount*reference;
        ELSE
            amount:=(j->>'quantity_base_life')::numeric; price:=(j->>'price_usdt')::numeric;
            fee:=(j->>'signed_fee')::numeric; sign:=CASE WHEN e.side='BUY' THEN 1 ELSE -1 END;
            IF e.side='SELL' AND amount>base OR e.side='BUY' AND amount*price-(CASE WHEN j->>'fee_currency'='USDT' THEN fee ELSE 0 END)>quote THEN
                RAISE EXCEPTION 'LIFE_CAPITAL_CAUSAL_INVALID' USING ERRCODE='P0001'; END IF;
            next_base:=base+sign*amount+CASE WHEN j->>'fee_currency'='LIFE' THEN fee ELSE 0 END;
            next_quote:=quote-sign*amount*price+CASE WHEN j->>'fee_currency'='USDT' THEN fee ELSE 0 END;
            base:=next_base; quote:=next_quote;
            loss:=loss+greatest(0,-(sign*(reference-price)*amount+fee*CASE WHEN j->>'fee_currency'='LIFE' THEN reference ELSE 1 END));
            IF e.side='SELL' THEN
                consumed:=least(initial,amount); initial:=initial-consumed;
                realized:=realized+consumed*(reference-p.opening_price);
                IF j->>'fee_currency'='LIFE' AND fee<0 THEN
                    consumed:=least(initial,-fee); initial:=initial-consumed;
                    realized:=realized+consumed*(reference-p.opening_price);
                END IF;
            END IF;
        END IF;
        IF base<0 OR quote<0 THEN RAISE EXCEPTION 'LIFE_CAPITAL_CAUSAL_INVALID' USING ERRCODE='P0001'; END IF;
    END LOOP;
    RETURN jsonb_build_object('base',base::text,'quote',quote::text,'initial',initial::text,'realized',realized::text,
        'net',net::text,'loss',loss::text,'opening_price',p.opening_price::text,'opening_nav',(p.opening_quote+p.opening_base*p.opening_price)::text);
END; $$;
CREATE OR REPLACE FUNCTION life_financial.spot_state_v7(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts; s life_financial.sessions; p life_financial.spot_policies;
    snapshot_body jsonb; b jsonb; budgets jsonb; policy jsonb; unknown boolean; unverified boolean; today text;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account;
    SELECT * INTO STRICT s FROM life_financial.sessions WHERE account_id=p_account AND session_id=a.current_session;
    SELECT * INTO p FROM life_financial.spot_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
    IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_SPOT_POLICY_MISSING' USING ERRCODE='P0001'; END IF;
    today := to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD');
    SELECT jsonb_object_agg(asset,jsonb_build_object('total',total::text,'reserved',reserved::text,'liability',liability::text,'deficit',funding_deficit::text,'version',version,
        'fees',COALESCE((SELECT sum(r.remaining) FROM life_financial.reservations r JOIN life_financial.spot_preparations d
            ON d.account_id=r.account_id AND d.intent_id=r.intent_id WHERE r.account_id=p_account AND r.asset=b.asset
            AND r.state<>'TERMINAL' AND r.reservation_id=d.operation_id||':fee:'||r.asset),0)::text)) INTO b
        FROM life_financial.balances b WHERE account_id=p_account;
    IF b IS NULL OR NOT b ?& ARRAY['LIFE','USDT'] THEN RAISE EXCEPTION 'LIFE_SPOT_BALANCES_MISSING' USING ERRCODE='P0001'; END IF;
    SELECT COALESCE(jsonb_agg(jsonb_build_object('family',family,'scope',scope,'key',window_key,
        'limit',budget_limit::text,'committed',committed::text,'incurred',incurred::text,'version',version)
        ORDER BY family,scope),'[]'::jsonb) INTO budgets FROM life_financial.budget_buckets
        WHERE account_id=p_account AND (scope='SESSION' AND window_key=a.current_session::text OR scope='DAY' AND window_key=today
            OR scope='CAMPAIGN' AND window_key=p.campaign_id);
    unknown := EXISTS(SELECT 1 FROM life_financial.send_attempts WHERE account_id=p_account AND state IN ('ARMED','SEND_UNKNOWN'));
    unverified := NOT life_financial.swap_verified(p_account) OR EXISTS(SELECT 1 FROM life_financial.send_attempts t WHERE t.account_id=p_account AND t.state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND')
        AND (t.session_id<>a.current_session OR t.epoch<>a.owner_epoch
            OR t.process_id<>a.owner_process OR t.generation<>a.owner_generation
            OR t.product='SPOT' AND NOT EXISTS(SELECT 1 FROM life_financial.spot_preparations d JOIN life_financial.operations o USING(account_id,operation_id)
                WHERE d.account_id=t.account_id AND d.intent_id=t.intent_id AND (d.request->>'config_version')::bigint=s.config_version
                AND o.command#>>'{binding,policy_hash}'=a.policy_hash)
            OR t.product='SWAP' AND (NOT EXISTS(
                SELECT 1 FROM life_financial.swap_preparations sd WHERE sd.account_id=t.account_id AND sd.intent_id=t.intent_id
                    AND (sd.permit->>'config_version')::bigint=s.config_version))))
        OR EXISTS(SELECT 1 FROM life_financial.capital_claims WHERE account_id=p_account AND state='HELD')
        OR EXISTS(SELECT 1 FROM life_financial.positions WHERE account_id=p_account AND quantity<>0)
        OR NOT life_financial.spot_accounting_verified(p_account)
        OR EXISTS(SELECT 1 FROM life_financial.financial_events e WHERE e.account_id=p_account AND
            (e.product<>'SPOT' AND e.product<>'SWAP' AND NOT (e.product='ACCOUNT' AND e.kind IN ('CASHFLOW','FUNDING')) OR e.kind NOT IN ('ACK','FILL','CASHFLOW','FUNDING') OR e.kind='ACK' AND NOT EXISTS(
                SELECT 1 FROM life_financial.send_attempts t WHERE t.account_id=p_account AND t.product='SPOT' AND t.exchange_order_id=e.exchange_identity)
            OR e.kind='CASHFLOW' AND NOT EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts x WHERE x.account_id=p_account AND x.bill_id=e.exchange_identity AND x.state='APPLIED')
            OR e.kind='FILL' AND e.product='SPOT' AND NOT EXISTS(SELECT 1 FROM life_financial.spot_fill_facts x WHERE x.account_id=p_account AND x.trade_id=e.exchange_identity)))
        OR NOT life_financial.spot_capital_verified(p_account)
        OR NOT life_financial.spot_actions_verified(p_account)
        OR EXISTS(SELECT 1 FROM life_financial.balances b WHERE b.account_id=p_account AND
            b.reserved<>COALESCE((SELECT sum(r.remaining) FROM life_financial.reservations r WHERE r.account_id=p_account AND r.asset=b.asset AND r.state<>'TERMINAL'),0))
        OR EXISTS(SELECT 1 FROM life_financial.reservations r WHERE r.account_id=p_account AND r.state<>'TERMINAL'
            AND NOT EXISTS(SELECT 1 FROM life_financial.spot_preparations d WHERE d.account_id=r.account_id AND d.intent_id=r.intent_id
                AND (r.reservation_id=d.operation_id||':principal' OR r.reservation_id=d.operation_id||':fee:'||r.asset)))
        OR EXISTS(SELECT 1 FROM (
            SELECT w.scope,w.window_key,sum(greatest(d.subsidy,COALESCE(g.execution_loss,0))) AS held FROM life_financial.spot_preparations d
            JOIN life_financial.send_attempts t USING(account_id,intent_id)
            LEFT JOIN life_financial.spot_accounting_progress g USING(account_id,intent_id)
            CROSS JOIN LATERAL (VALUES ('CAMPAIGN',p.campaign_id::text),
                ('DAY',to_char(d.prepared_at AT TIME ZONE 'UTC','YYYY-MM-DD')),('SESSION',t.session_id::text)) AS w(scope,window_key)
            WHERE d.account_id=p_account AND t.state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND') GROUP BY w.scope,w.window_key
        ) expected LEFT JOIN life_financial.budget_buckets b ON b.account_id=p_account AND b.family='SUBSIDY'
            AND b.scope=expected.scope AND b.window_key=expected.window_key WHERE b.account_id IS NULL OR b.committed<expected.held)
        OR EXISTS(SELECT 1 FROM life_financial.spot_preparations d JOIN life_financial.send_attempts t USING(account_id,intent_id)
            LEFT JOIN life_financial.spot_accounting_progress g USING(account_id,intent_id)
            WHERE d.account_id=p_account AND t.state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND') AND (
                NOT EXISTS(SELECT 1 FROM life_financial.reservations r WHERE r.account_id=p_account AND r.reservation_id=d.operation_id||':principal'
                    AND r.intent_id=t.intent_id AND r.state='HELD' AND r.remaining=(t.quantity-COALESCE(g.filled_base,0))*CASE WHEN t.side='BUY' THEN t.price ELSE 1 END
                    AND r.asset=CASE WHEN t.side='BUY' THEN 'USDT' ELSE 'LIFE' END
                    AND r.amount=CASE WHEN t.side='BUY' THEN t.quantity*t.price ELSE t.quantity END)
                OR greatest(0,d.fee_base-COALESCE(g.paid_base,0))<>COALESCE((SELECT remaining FROM life_financial.reservations WHERE account_id=p_account AND reservation_id=d.operation_id||':fee:LIFE' AND state='HELD'),0)
                OR greatest(0,d.fee_quote-COALESCE(g.paid_quote,0))<>COALESCE((SELECT remaining FROM life_financial.reservations WHERE account_id=p_account AND reservation_id=d.operation_id||':fee:USDT' AND state='HELD'),0)));
    SELECT jsonb_object_agg(k,CASE WHEN jsonb_typeof(v)='number' AND k<>'max_quote_levels' THEN to_jsonb(v::text) ELSE v END)
        INTO policy FROM jsonb_each(to_jsonb(p)) AS fields(k,v);
    snapshot_body := jsonb_build_object('account',to_jsonb(a),'session',to_jsonb(s),'policy',policy,'fill_policy',(SELECT to_jsonb(fp) FROM life_financial.spot_fill_policies fp WHERE fp.account_id=p_account AND fp.policy_hash=a.policy_hash),'capital_policy',(SELECT to_jsonb(cp) FROM life_financial.spot_capital_policies cp WHERE cp.account_id=p_account),'balances',b,'budgets',budgets,
        'day',today,'recovery_required',unknown OR unverified
            OR EXISTS(SELECT 1 FROM life_financial.spot_pending_observations WHERE account_id=p_account)
            OR EXISTS(SELECT 1 FROM life_financial.balances WHERE account_id=p_account AND funding_deficit>0)
            OR EXISTS(SELECT 1 FROM life_financial.spot_preparations d JOIN life_financial.spot_accounting_progress g USING(account_id,intent_id)
                WHERE d.account_id=p_account AND (g.paid_base>d.fee_base OR g.paid_quote>d.fee_quote)),'unverified',unverified);
    RETURN snapshot_body||jsonb_build_object('digest',encode(sha256(convert_to(snapshot_body::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
END; $$;
CREATE OR REPLACE FUNCTION life_financial.commit_owner_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts; expiry timestamptz; at timestamptz; op life_financial.operations;
BEGIN
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=NEW.account_id;
    SELECT * INTO op FROM life_financial.operations WHERE account_id=NEW.account_id AND committed_version=NEW.version;
    IF a.owner_generation IS DISTINCT FROM NEW.owner_generation OR a.owner_process IS DISTINCT FROM NEW.owner_process
        OR a.owner_epoch<>NEW.owner_epoch OR a.current_session IS DISTINCT FROM NEW.current_session
        OR a.lineage<>NEW.lineage OR a.policy_hash<>NEW.policy_hash OR a.config_hash<>NEW.config_hash THEN
        RAISE EXCEPTION 'LIFE_OWNER_EXPIRED_OR_CHANGED_AT_COMMIT' USING ERRCODE='P0001'; END IF;
    IF op.command->>'kind' IN ('PAID_FUNDING','SWAP_FILL','SWAP_ACK','CARRY_CHECK','RECORDER_POSITION_STAGE','RECORDER_POSITION_APPLY','RECORDER_EVENT_STAGE','RECORDER_EVENT_APPLY','RECORDER_CONFIG_STAGE','RECORDER_CONFIG_APPLY','RECORDER_STAGE','RECORDER_APPLY','SPOT_MARKOUT','SPOT_INVENTORY_SETTLE','SPOT_ACK','SPOT_FILL','SPOT_CASHFLOW','SPOT_NAV','SPOT_SESSION_TICK','SPOT_LIFECYCLE','SPOT_TERMINAL') OR op.command->>'kind' IN ('SPOT_ACTION_TRANSITION','SPOT_ACTION_ABANDON')
        AND op.command#>>'{command,state}'<>'DISPATCHED' THEN
        PERFORM life_financial.guard_spot_observation(NEW.account_id,op.command->'binding',op.command->'owner',a.version);
        IF op.command->>'kind' IN ('SPOT_LIFECYCLE','SPOT_TERMINAL') THEN
            PERFORM life_financial.spot_order_commit_guard(NEW.account_id,op.command); END IF;
        IF op.command->>'kind'='SPOT_SESSION_TICK' AND ((op.command#>>'{command,reference_ready}')::boolean
            OR (op.command#>>'{command,all_gates_ready}')::boolean
            OR (op.command#>>'{command,market_reference_ready}')::boolean) THEN
            PERFORM life_financial.guard_account(NEW.account_id,op.command->'binding',op.command->'owner',a.version);
        END IF;
        IF op.command->>'kind'='SPOT_SESSION_TICK' AND op.receipt->>'state'='ACTIVE' THEN
            PERFORM life_financial.guard_spot_session(NEW.account_id); END IF;
    ELSE
        SELECT expires_at INTO STRICT expiry FROM life_financial.sessions WHERE account_id=a.account_id AND session_id=a.current_session;
        at:=clock_timestamp();
        IF a.mode<>'ACTIVE' OR a.halted OR at<a.owner_since OR at>=a.owner_until OR at>=expiry
            OR current_setting('synchronous_commit')<>'on' OR current_setting('fsync')<>'on'
            OR current_setting('full_page_writes')<>'on' OR pg_is_in_recovery() THEN
            RAISE EXCEPTION 'LIFE_OWNER_EXPIRED_OR_CHANGED_AT_COMMIT' USING ERRCODE='P0001'; END IF;
        IF op.receipt->>'kind' IN ('SPOT_PREPARE','SPOT_ARM') THEN PERFORM life_financial.guard_spot_session(NEW.account_id); END IF;
    END IF;
    IF op.command->>'kind'='SPOT_MARKOUT' THEN
        PERFORM life_financial.spot_markout_commit_guard(NEW.account_id,op.command); END IF;
    IF op.command->>'kind'='SPOT_INVENTORY_SETTLE' THEN
        PERFORM life_financial.spot_inventory_commit_guard(NEW.account_id,op.command); END IF;
    IF op.command->>'kind' IN ('SPOT_ACTION_CLAIM','SPOT_ACTION_TRANSITION','SPOT_ACTION_ABANDON') THEN
        PERFORM life_financial.spot_action_commit_guard(NEW.account_id,op.command); END IF;
    IF op.command->>'kind'='SPOT_PREPARE' AND op.command ? 'action' THEN
        PERFORM life_financial.spot_action_prepare_commit_guard(NEW.account_id,op.command); END IF;
    RETURN NULL;
END; $$;
CREATE OR REPLACE FUNCTION life_financial.swap_snapshot_current(p_account uuid,snapshot jsonb) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r jsonb;
BEGIN
    IF NOT EXISTS(SELECT 1 FROM life_financial.swap_fill_policies WHERE account_id=p_account) THEN RETURN true;END IF;
    IF NOT life_financial.swap_accounting_verified(p_account)
        OR EXISTS(SELECT 1 FROM life_financial.swap_pending_fills WHERE account_id=p_account) THEN RETURN false;END IF;
    r:=life_financial.swap_fill_replay(p_account);
    RETURN COALESCE((snapshot->>'observed_at_ms')::bigint>=COALESCE((SELECT max(settled_at_ms)
            FROM life_financial.funding_cash_payments WHERE account_id=p_account),0)
        AND (snapshot->>'long_base')::numeric=(r->>'long_base')::numeric
        AND (snapshot->>'short_base')::numeric=(r->>'short_base')::numeric
        AND (snapshot->>'observed_at_ms')::bigint>=COALESCE((SELECT max((observation->>'fill_at_ms')::bigint)
            FROM life_financial.swap_fill_facts WHERE account_id=p_account),0),false);
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.funding_carry_current(p_account uuid,b jsonb) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM life_financial.funding_accounting_policies WHERE account_id=p_account) THEN RETURN true;END IF;
    RETURN life_financial.funding_verified(p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.funding_observations WHERE account_id=p_account AND state='RECOVERY_REQUIRED')
        AND NOT EXISTS(SELECT 1 FROM life_financial.funding_cash_payments pay WHERE pay.account_id=p_account AND NOT EXISTS(
            SELECT 1 FROM jsonb_array_elements(b#>'{funding,payments}') value WHERE value->>'bill_id'=pay.bill_id
                AND (value->>'settled_at_ms')::bigint=pay.settled_at_ms AND (value->>'signed_payment_quote')::numeric=pay.signed_payment_usdt));
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE OR REPLACE FUNCTION life_financial.carry_current_ready(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE h life_financial.carry_heads;r life_financial.carry_checks;op life_financial.operations;p life_financial.carry_policies;cp jsonb;previous jsonb;
BEGIN
    SELECT * INTO p FROM life_financial.carry_policies WHERE account_id=p_account;
    IF NOT FOUND THEN RETURN true; END IF;
    IF NOT life_financial.carry_verified(p_account) THEN RETURN false; END IF;
    SELECT * INTO STRICT h FROM life_financial.carry_heads WHERE account_id=p_account;
    SELECT * INTO STRICT r FROM life_financial.carry_checks WHERE account_id=p_account AND operation_id=h.operation_id;
    SELECT * INTO STRICT op FROM life_financial.operations WHERE account_id=p_account AND operation_id=h.operation_id;
    SELECT body INTO STRICT cp FROM life_financial.shared_capital_policies WHERE account_id=p_account;
    SELECT bundle INTO previous FROM life_financial.carry_checks WHERE account_id=p_account AND operation_id=r.previous_qualified;
    RETURN life_financial.funding_carry_current(p_account,r.bundle) AND life_financial.swap_snapshot_current(p_account,r.bundle#>'{account,snapshot}') AND r.qualified AND op.receipt#>'{decision,allowed}'='true'::jsonb
        AND (r.bundle#>>'{account,snapshot,spot_life_base}')::numeric=(SELECT total FROM life_financial.balances WHERE account_id=p_account AND asset='LIFE')
        AND (r.bundle#>>'{account,snapshot,spot_cash_quote}')::numeric=(SELECT total FROM life_financial.balances WHERE account_id=p_account AND asset='USDT')
        AND r.capital_operation_id=(SELECT operation_id FROM life_financial.shared_capital_heads WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.shared_capital_claims c JOIN life_financial.operations o USING(account_id,operation_id)
            WHERE c.account_id=p_account AND o.committed_version>op.committed_version)
        AND life_financial.carry_bundle_reason(p.body,cp,r.bundle,previous,r.bundle#>'{account,snapshot}',
            floor(extract(epoch FROM clock_timestamp())*1000)::bigint,p.max_payments)='CARRY_QUALIFIED';
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
CREATE FUNCTION life_financial.spot_state_before_paid_funding(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b jsonb;verified boolean;pending boolean;
BEGIN
    b:=life_financial.spot_state_before_swap_accounting(p_account)-'digest'-'observed_at';
    verified:=life_financial.swap_accounting_verified(p_account);
    pending:=EXISTS(SELECT 1 FROM life_financial.swap_pending_fills WHERE account_id=p_account);
    b:=b||jsonb_build_object('swap_accounting',jsonb_build_object('verified',verified,'pending',pending,
        'policy',(SELECT to_jsonb(p) FROM life_financial.swap_fill_policies p WHERE account_id=p_account),
        'facts',COALESCE((SELECT jsonb_agg(to_jsonb(f) ORDER BY trade_id) FROM life_financial.swap_fill_facts f WHERE account_id=p_account),'[]'::jsonb),
        'progress',COALESCE((SELECT jsonb_agg(to_jsonb(g) ORDER BY intent_id) FROM life_financial.swap_fill_progress g WHERE account_id=p_account),'[]'::jsonb),
        'pending_facts',COALESCE((SELECT jsonb_agg(to_jsonb(f) ORDER BY operation_id) FROM life_financial.swap_pending_fills f WHERE account_id=p_account),'[]'::jsonb)),
        'financial_recovery_required',(b->>'financial_recovery_required')::boolean OR NOT verified OR pending,
        'recovery_required',(b->>'recovery_required')::boolean OR NOT verified OR pending,
        'unverified',(b->>'unverified')::boolean OR NOT verified);
    RETURN b||jsonb_build_object('digest',encode(sha256(convert_to(b::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
END; $$;
CREATE OR REPLACE FUNCTION life_financial.spot_state(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE body jsonb;verified boolean;pending boolean;
BEGIN
    body:=life_financial.spot_state_before_paid_funding(p_account)-'digest'-'observed_at';verified:=life_financial.funding_verified(p_account);
    pending:=EXISTS(SELECT 1 FROM life_financial.funding_observations WHERE account_id=p_account AND state='RECOVERY_REQUIRED');
    body:=body||jsonb_build_object('paid_funding',jsonb_build_object('verified',verified,'pending',pending,
        'policy',(SELECT to_jsonb(p) FROM life_financial.funding_accounting_policies p WHERE account_id=p_account),
        'operations',COALESCE((SELECT jsonb_agg(to_jsonb(o) ORDER BY operation_id) FROM life_financial.funding_observations o WHERE account_id=p_account),'[]'::jsonb),
        'payments',COALESCE((SELECT jsonb_agg(to_jsonb(p) ORDER BY bill_id) FROM life_financial.funding_cash_payments p WHERE account_id=p_account),'[]'::jsonb)),
        'financial_recovery_required',(body->>'financial_recovery_required')::boolean OR NOT verified OR pending,
        'recovery_required',(body->>'recovery_required')::boolean OR NOT verified OR pending,
        'unverified',(body->>'unverified')::boolean OR NOT verified);
    RETURN body||jsonb_build_object('digest',encode(sha256(convert_to(body::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
END; $$;
CREATE FUNCTION life_financial.funding_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF NOT life_financial.funding_verified(NEW.account_id) OR EXISTS(SELECT 1 FROM life_financial.funding_accounting_policies WHERE account_id=NEW.account_id)
        AND NOT life_financial.spot_capital_verified(NEW.account_id) THEN
        RAISE EXCEPTION 'LIFE_FUNDING_UNVERIFIED_AT_COMMIT' USING ERRCODE='P0001';END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER funding_account_commit AFTER UPDATE ON life_financial.accounts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN(NEW.version<>OLD.version) EXECUTE FUNCTION life_financial.funding_commit_guard();
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
