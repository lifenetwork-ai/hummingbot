-- Independent monotone carry streams and permanent settled funding evidence.
-- Settled funding is already in observed collateral: this never credits wallets.
CREATE FUNCTION life_financial.carry_policy_valid(p jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE k text; n numeric;
BEGIN
    IF jsonb_typeof(p) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(p))<>10
        OR NOT p ?& ARRAY['account_uid','instrument','anchor_ms','max_age_ms','max_skew_ms','horizon_ms','max_schedule_gap_ms',
            'absolute_rate_stress','funding_budget_quote','max_basis_bps'] THEN RETURN false; END IF;
    IF jsonb_typeof(p->'account_uid') IS DISTINCT FROM 'string' OR p->>'account_uid' !~ '^[0-9]{1,64}$'
        OR p->'instrument' IS DISTINCT FROM '"LIFE-USDT-SWAP"'::jsonb THEN RETURN false; END IF;
    FOREACH k IN ARRAY ARRAY['anchor_ms','max_age_ms','max_skew_ms','horizon_ms','max_schedule_gap_ms'] LOOP
        IF jsonb_typeof(p->k) IS DISTINCT FROM 'number' OR p->>k !~ '^[0-9]+$' THEN RETURN false; END IF;
        n:=(p->>k)::bigint;
        IF n>253402300799999 OR n=0 AND k<>'anchor_ms' THEN RETURN false; END IF;
    END LOOP;
    FOREACH k IN ARRAY ARRAY['absolute_rate_stress','funding_budget_quote','max_basis_bps'] LOOP
        IF jsonb_typeof(p->k) IS DISTINCT FROM 'string' THEN RETURN false; END IF;
        n:=(p->>k)::life_financial.amount;
    END LOOP;
    RETURN (p->>'absolute_rate_stress')::numeric<=1;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE TABLE life_financial.carry_policies (
    account_id uuid PRIMARY KEY REFERENCES life_financial.shared_capital_policies,
    policy_hash life_financial.digest NOT NULL, body jsonb NOT NULL,
    model_hash life_financial.digest NOT NULL, max_payments integer NOT NULL CHECK(max_payments BETWEEN 1 AND 4096),
    FOREIGN KEY(account_id,policy_hash) REFERENCES life_financial.policies,
    CHECK(life_financial.carry_policy_valid(body)),
    CHECK(model_hash=encode(sha256(convert_to(life_financial.canonical_json_v1(jsonb_build_object('policy',body,'max_payments',max_payments)),'UTF8')),'hex'))
);
CREATE TABLE life_financial.carry_checks (
    account_id uuid NOT NULL REFERENCES life_financial.accounts, operation_id life_financial.identity NOT NULL,
    bundle jsonb NOT NULL CHECK(octet_length(bundle::text)<=1048576), prospective jsonb NOT NULL,
    checked_at_ms bigint NOT NULL CHECK(checked_at_ms BETWEEN 1 AND 253402300799999),
    capital_operation_id life_financial.identity NOT NULL, previous_qualified life_financial.identity,
    qualified boolean NOT NULL, reason text NOT NULL, PRIMARY KEY(account_id,operation_id),
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(account_id,capital_operation_id) REFERENCES life_financial.operations,
    FOREIGN KEY(account_id,previous_qualified) REFERENCES life_financial.carry_checks
);
CREATE TABLE life_financial.carry_payments (
    account_id uuid NOT NULL REFERENCES life_financial.accounts, bill_id life_financial.identity NOT NULL,
    settled_at_ms bigint NOT NULL CHECK(settled_at_ms BETWEEN 0 AND 253402300799999),
    signed_payment_quote life_financial.signed_amount NOT NULL, operation_id life_financial.identity NOT NULL,
    PRIMARY KEY(account_id,bill_id), FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.carry_checks
);
CREATE TABLE life_financial.carry_heads (
    account_id uuid PRIMARY KEY REFERENCES life_financial.accounts,
    last_checked_at_ms bigint NOT NULL CHECK(last_checked_at_ms>0), check_count bigint NOT NULL CHECK(check_count>0),
    operation_id life_financial.identity NOT NULL, qualified_operation_id life_financial.identity,
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.carry_checks,
    FOREIGN KEY(account_id,qualified_operation_id) REFERENCES life_financial.carry_checks
);
CREATE INDEX carry_operation_history ON life_financial.operations(account_id,committed_version)
    WHERE command->>'kind'='CARRY_CHECK';

-- Returns the first qualification failure. Source names are structural checks,
-- never authentication: only a qualified owning application may supply bundles.
CREATE FUNCTION life_financial.carry_bundle_reason(p jsonb,cp jsonb,b jsonb,previous jsonb,snapshot jsonb,at_ms bigint,p_max integer) RETURNS text
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE k text; r jsonb; old jsonb; value jsonb; stamp jsonb; past jsonb; payment jsonb; maximum bigint:=0; minimum bigint:=253402300799999;
    n numeric; ids jsonb; dynamic jsonb;
BEGIN
    IF b IS NULL OR b='null'::jsonb THEN RETURN 'CARRY_EVIDENCE_UNAVAILABLE'; END IF;
    IF jsonb_typeof(b) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(b))<>5
        OR NOT b ?& ARRAY['account','mark','index','funding','terms']
        OR jsonb_typeof(b->'account') IS DISTINCT FROM 'object'
        OR (SELECT count(*) FROM jsonb_object_keys(b->'account'))<>4
        OR b#>'{account,snapshot}' IS DISTINCT FROM snapshot
        OR b#>'{account,settled_funding_included}' IS DISTINCT FROM 'true'::jsonb
        OR b#>'{account,unsettled_funding_only}' IS DISTINCT FROM 'true'::jsonb
        OR jsonb_typeof(b#>'{account,included_funding_bill_ids}') IS DISTINCT FROM 'array'
        OR jsonb_array_length(b#>'{account,included_funding_bill_ids}')>p_max
        OR EXISTS(SELECT 1 FROM jsonb_array_elements(b#>'{account,included_funding_bill_ids}') x WHERE jsonb_typeof(x)<>'string') THEN RETURN 'CARRY_PRICE_OR_ACCOUNT_UNQUALIFIED'; END IF;
    IF NOT life_financial.shared_snapshot_valid(snapshot,cp,at_ms,4096) THEN RETURN 'CARRY_PRICE_OR_ACCOUNT_UNQUALIFIED'; END IF;
    FOREACH k IN ARRAY ARRAY['mark','index'] LOOP
        r:=b->k;
        IF jsonb_typeof(r) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(r))<>5
            OR r->>'source' IS DISTINCT FROM 'okx_'||k OR jsonb_typeof(r->'price_usdt') IS DISTINCT FROM 'string'
            OR (r->>'price_usdt')::life_financial.positive_amount<>(snapshot->>(k||'_price_usdt'))::numeric THEN
            RETURN 'CARRY_PRICE_OR_ACCOUNT_UNQUALIFIED'; END IF;
    END LOOP;
    FOREACH k IN ARRAY ARRAY['account','mark','index','funding','terms'] LOOP
        value:=CASE WHEN k='account' THEN b#>'{account,snapshot}' ELSE b->k END;
        IF jsonb_typeof(value->'sequence') IS DISTINCT FROM 'number' OR value->>'sequence' !~ '^[1-9][0-9]*$'
            OR (value->>'sequence')::numeric>9223372036854775807
            OR jsonb_typeof(value->'observed_at_ms') IS DISTINCT FROM 'number' OR value->>'observed_at_ms' !~ '^[0-9]+$'
            OR (value->>'observed_at_ms')::numeric>253402300799999
            OR (value->>'observed_at_ms')::bigint>at_ms OR at_ms-(value->>'observed_at_ms')::bigint>(p->>'max_age_ms')::bigint THEN
            RETURN 'CARRY_STREAM_STALE_OR_INVALID'; END IF;
        IF k<>'account' AND value->'instrument' IS DISTINCT FROM p->'instrument' THEN RETURN 'CARRY_INSTRUMENT_MISMATCH'; END IF;
        maximum:=greatest(maximum,(value->>'observed_at_ms')::bigint); minimum:=least(minimum,(value->>'observed_at_ms')::bigint);
    END LOOP;
    IF maximum-minimum>(p->>'max_skew_ms')::bigint THEN RETURN 'CARRY_STREAM_SKEW'; END IF;
    r:=b->'funding'; old:=b->'terms';
    IF jsonb_typeof(r) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(r))<>12
        OR jsonb_typeof(old) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(old))<>9
        OR r->'account_uid' IS DISTINCT FROM p->'account_uid' OR old->'account_uid' IS DISTINCT FROM p->'account_uid'
        OR r->'scope_complete' IS DISTINCT FROM 'true'::jsonb OR old->'scope_complete' IS DISTINCT FROM 'true'::jsonb
        OR r->'source' IS DISTINCT FROM '"okx_funding"'::jsonb OR old->'source' IS DISTINCT FROM '"okx_account_terms"'::jsonb
        OR jsonb_typeof(r->'rate') IS DISTINCT FROM 'string' OR abs((r->>'rate')::life_financial.signed_amount)>1
        OR jsonb_typeof(old->'maker_fee_rate') IS DISTINCT FROM 'string' OR jsonb_typeof(old->'taker_fee_rate') IS DISTINCT FROM 'string'
        OR (old->>'maker_fee_rate')::life_financial.amount>1 OR (old->>'taker_fee_rate')::life_financial.amount>1
        OR jsonb_typeof(r->'payments') IS DISTINCT FROM 'array' OR jsonb_array_length(r->'payments')>p_max THEN
        RETURN 'CARRY_FUNDING_OR_TERMS_UNQUALIFIED'; END IF;
    FOREACH k IN ARRAY ARRAY['settlement_ms','next_settlement_ms','coverage_from_ms','coverage_through_ms'] LOOP
        IF jsonb_typeof(r->k) IS DISTINCT FROM 'number' OR r->>k !~ '^[0-9]+$' OR (r->>k)::numeric>253402300799999 THEN
            RETURN 'CARRY_FUNDING_OR_TERMS_UNQUALIFIED'; END IF;
    END LOOP;
    IF NOT at_ms<(r->>'settlement_ms')::bigint OR NOT (r->>'settlement_ms')::bigint<(r->>'next_settlement_ms')::bigint
        OR (r->>'settlement_ms')::bigint-at_ms>(p->>'max_schedule_gap_ms')::bigint
        OR (r->>'next_settlement_ms')::bigint-(r->>'settlement_ms')::bigint>(p->>'max_schedule_gap_ms')::bigint
        OR r->'coverage_from_ms' IS DISTINCT FROM p->'anchor_ms'
        OR (r->>'coverage_through_ms')::bigint<(p->>'anchor_ms')::bigint
        OR (r->>'coverage_through_ms')::bigint>(r->>'observed_at_ms')::bigint
        OR at_ms-(r->>'coverage_through_ms')::bigint>(p->>'max_age_ms')::bigint THEN RETURN 'CARRY_FUNDING_OR_TERMS_UNQUALIFIED'; END IF;
    dynamic:=cp||jsonb_build_object('tiers',old->'tiers','fee_rate',greatest((cp->>'fee_rate')::numeric,(old->>'maker_fee_rate')::numeric,(old->>'taker_fee_rate')::numeric)::text);
    IF NOT life_financial.shared_capital_policy_valid(dynamic) THEN RETURN 'CARRY_FUNDING_OR_TERMS_UNQUALIFIED'; END IF;
    FOR payment IN SELECT x FROM jsonb_array_elements(r->'payments') x LOOP
        IF jsonb_typeof(payment) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(payment))<>3
            OR jsonb_typeof(payment->'bill_id') IS DISTINCT FROM 'string' OR payment->>'bill_id' !~ '^[A-Za-z0-9_.:-]{1,128}$'
            OR jsonb_typeof(payment->'settled_at_ms') IS DISTINCT FROM 'number' OR payment->>'settled_at_ms' !~ '^[0-9]+$'
            OR (payment->>'settled_at_ms')::numeric<(p->>'anchor_ms')::bigint OR (payment->>'settled_at_ms')::numeric>(r->>'coverage_through_ms')::bigint
            OR jsonb_typeof(payment->'signed_payment_quote') IS DISTINCT FROM 'string' THEN RETURN 'CARRY_PAYMENT_INVALID'; END IF;
        n:=(payment->>'signed_payment_quote')::life_financial.signed_amount;
    END LOOP;
    IF (SELECT count(DISTINCT x->>'bill_id') FROM jsonb_array_elements(r->'payments') x)<>jsonb_array_length(r->'payments') THEN
        RETURN 'CARRY_PAYMENT_DUPLICATE'; END IF;
    IF jsonb_array_length(b#>'{account,included_funding_bill_ids}')<>(SELECT count(DISTINCT x) FROM jsonb_array_elements(b#>'{account,included_funding_bill_ids}') x)
        OR (SELECT COALESCE(jsonb_agg(x->>'bill_id' ORDER BY x->>'bill_id'),'[]'::jsonb) FROM jsonb_array_elements(r->'payments') x)
            IS DISTINCT FROM (SELECT COALESCE(jsonb_agg(x ORDER BY x#>>'{}'),'[]'::jsonb) FROM jsonb_array_elements(b#>'{account,included_funding_bill_ids}') x)
        OR EXISTS(SELECT 1 FROM jsonb_array_elements(previous#>'{funding,payments}') x WHERE NOT EXISTS(
            SELECT 1 FROM jsonb_array_elements(r->'payments') y WHERE y=x)) THEN RETURN 'CARRY_FUNDING_HISTORY_OR_ACCOUNT_MISMATCH'; END IF;
    IF previous IS NOT NULL AND ((r->>'coverage_through_ms')::bigint<(previous#>>'{funding,coverage_through_ms}')::bigint
        OR (previous#>>'{funding,settlement_ms}')::bigint<=at_ms AND (r->>'coverage_through_ms')::bigint<(previous#>>'{funding,settlement_ms}')::bigint) THEN
        RETURN 'CARRY_SETTLEMENT_COVERAGE_GAP'; END IF;
    FOREACH k IN ARRAY ARRAY['account','mark','index','funding','terms'] LOOP
        value:=b->k; past:=previous->k;
        stamp:=CASE WHEN k='account' THEN value->'snapshot' ELSE value END;
        old:=CASE WHEN k='account' THEN past->'snapshot' ELSE past END;
        IF past IS NOT NULL AND ((stamp->>'sequence')::bigint<(old->>'sequence')::bigint
            OR (stamp->>'observed_at_ms')::bigint<(old->>'observed_at_ms')::bigint
            OR stamp->'sequence'=old->'sequence' AND value<>past) THEN RETURN 'CARRY_STREAM_REPLAY_OR_MUTATION'; END IF;
    END LOOP;
    RETURN 'CARRY_QUALIFIED';
EXCEPTION WHEN OTHERS THEN RETURN 'CARRY_FUNDING_OR_TERMS_UNQUALIFIED';
END; $$;

CREATE FUNCTION life_financial.carry_denial(reason text) RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
    SELECT jsonb_build_object('allowed',false,'reason_code',reason,'funding_reserve_quote','0','expected_payment_quote','0',
        'settled_payment_quote','0','funding_debits_quote','0','funding_events',0,'fee_rate','0','funding_cost_per_base','0',
        'capital',NULL,'funding_reference_price_usdt','0')
$$;
CREATE FUNCTION life_financial.carry_evaluate(p jsonb,cp jsonb,b jsonb,claims jsonb,candidate jsonb) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE orders jsonb; financial_claims jsonb:=claims; s jsonb:=b#>'{account,snapshot}'; f jsonb:=b->'funding';
    dynamic jsonb; financial jsonb; c jsonb; lg numeric; sh numeric; price numeric; events bigint; rate numeric; per_base numeric;
    reserve numeric; paid numeric; debits numeric; expected numeric; liability numeric; reason text;
BEGIN
    IF candidate<>'null'::jsonb THEN
        IF EXISTS(SELECT 1 FROM jsonb_array_elements(claims) x WHERE x->>'intent_id'=candidate->>'intent_id' AND NOT life_financial.shared_claim_equal(x,candidate)) THEN
            RETURN life_financial.carry_denial('CARRY_CLAIM_CHANGED'); END IF;
        IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(claims) x WHERE x->>'intent_id'=candidate->>'intent_id') THEN financial_claims:=claims||jsonb_build_array(candidate); END IF;
    END IF;
    IF EXISTS(SELECT 1 FROM jsonb_array_elements(financial_claims) x JOIN jsonb_array_elements(s->'pending') y
        ON x->>'intent_id'=y->>'intent_id' WHERE NOT life_financial.shared_claim_equal(x,y)) THEN RETURN life_financial.carry_denial('CARRY_PENDING_CONFLICT'); END IF;
    SELECT COALESCE(jsonb_agg(value ORDER BY value->>'intent_id'),'[]'::jsonb) INTO orders FROM (
        SELECT DISTINCT ON (x->>'intent_id') x AS value FROM jsonb_array_elements(financial_claims||(s->'pending')) x ORDER BY x->>'intent_id') y;
    lg:=(s->>'long_base')::numeric;sh:=(s->>'short_base')::numeric;
    FOR c IN SELECT x FROM jsonb_array_elements(orders) x LOOP
        IF c->>'product'='SWAP' AND c->>'position_action'='OPEN' THEN
            IF c->>'side'='BUY' THEN lg:=lg+(c->>'quantity_base')::numeric; ELSE sh:=sh+(c->>'quantity_base')::numeric; END IF;
        END IF;
    END LOOP;
    events:=1+div((p->>'horizon_ms')::numeric,(f->>'next_settlement_ms')::numeric-(f->>'settlement_ms')::numeric)::bigint;
    rate:=greatest(abs((f->>'rate')::numeric),(p->>'absolute_rate_stress')::numeric);
    price:=greatest((s->>'mark_price_usdt')::numeric,(s->>'index_price_usdt')::numeric,
        COALESCE((SELECT max((x->>'price_usdt')::numeric) FROM jsonb_array_elements(orders) x),0));
    per_base:=price*(1+(cp->>'price_stress_bps')::numeric*0.0001)*rate*events;
    reserve:=(lg+sh)*per_base;
    SELECT COALESCE(sum((x->>'signed_payment_quote')::numeric),0),COALESCE(sum(greatest(0,-(x->>'signed_payment_quote')::numeric)),0)
        INTO paid,debits FROM jsonb_array_elements(f->'payments') x;
    expected:=-((s->>'long_base')::numeric-(s->>'short_base')::numeric)*(s->>'mark_price_usdt')::numeric*(f->>'rate')::numeric;
    liability:=(s->>'funding_liability_quote')::numeric+reserve;
    dynamic:=cp||jsonb_build_object('tiers',b#>'{terms,tiers}','fee_rate',greatest((cp->>'fee_rate')::numeric,
        (b#>>'{terms,maker_fee_rate}')::numeric,(b#>>'{terms,taker_fee_rate}')::numeric)::text);
    financial:=life_financial.shared_evaluate(dynamic,s||jsonb_build_object('funding_liability_quote',liability::text),financial_claims);
    reason:=CASE WHEN abs((s->>'mark_price_usdt')::numeric-(s->>'index_price_usdt')::numeric)*10000>(p->>'max_basis_bps')::numeric*(s->>'index_price_usdt')::numeric THEN 'CARRY_BASIS_LIMIT'
        WHEN debits+liability>(p->>'funding_budget_quote')::numeric THEN 'CARRY_FUNDING_BUDGET'
        WHEN financial->'allowed' IS DISTINCT FROM 'true'::jsonb THEN financial->>'reason_code' ELSE 'CARRY_READY' END;
    RETURN jsonb_build_object('allowed',reason='CARRY_READY','reason_code',reason,'funding_reserve_quote',reserve::text,
        'expected_payment_quote',expected::text,'settled_payment_quote',paid::text,'funding_debits_quote',debits::text,
        'funding_events',events,'fee_rate',dynamic->>'fee_rate','funding_cost_per_base',per_base::text,'capital',financial,'funding_reference_price_usdt',price::text);
END; $$;

CREATE FUNCTION life_financial.carry_check_verified(p_account uuid,p_operation text) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r life_financial.carry_checks;op life_financial.operations;cap life_financial.operations;before life_financial.operations;
    p life_financial.carry_policies;cp life_financial.shared_capital_policies;previous jsonb;claims jsonb;decision jsonb;reason text;
BEGIN
    SELECT * INTO STRICT r FROM life_financial.carry_checks WHERE account_id=p_account AND operation_id=p_operation;
    SELECT * INTO STRICT op FROM life_financial.operations WHERE account_id=p_account AND operation_id=p_operation;
    SELECT * INTO STRICT cap FROM life_financial.operations WHERE account_id=p_account AND operation_id=r.capital_operation_id;
    SELECT * INTO STRICT p FROM life_financial.carry_policies WHERE account_id=p_account;
    SELECT * INTO STRICT cp FROM life_financial.shared_capital_policies WHERE account_id=p_account;
    IF op.command->>'kind' IS DISTINCT FROM 'CARRY_CHECK' OR op.command#>'{command,bundle}' IS DISTINCT FROM r.bundle
        OR op.command#>'{command,prospective}' IS DISTINCT FROM r.prospective
        OR op.command#>>'{command,operation_id}' IS DISTINCT FROM r.operation_id
        OR op.fingerprint<>encode(sha256(convert_to(life_financial.canonical_json_v1(op.command),'UTF8')),'hex')
        OR cap.committed_version>=op.committed_version OR NOT life_financial.shared_operation_verified(p_account,cap.operation_id) THEN RETURN false; END IF;
    IF r.previous_qualified IS NOT NULL THEN
        SELECT q.bundle INTO STRICT previous FROM life_financial.carry_checks q WHERE account_id=p_account AND operation_id=r.previous_qualified AND q.qualified;
        SELECT * INTO STRICT before FROM life_financial.operations WHERE account_id=p_account AND operation_id=r.previous_qualified;
        IF before.committed_version>=op.committed_version THEN RETURN false; END IF;
    END IF;
    reason:=life_financial.carry_bundle_reason(p.body,cp.body,r.bundle,previous,cap.command#>'{command,snapshot}',r.checked_at_ms,p.max_payments);
    IF r.reason IS DISTINCT FROM reason OR r.qualified IS DISTINCT FROM (reason='CARRY_QUALIFIED') THEN RETURN false; END IF;
    SELECT COALESCE(jsonb_agg(life_financial.shared_claim_body(c) ORDER BY c.intent_id),'[]'::jsonb) INTO claims
        FROM life_financial.shared_capital_claims c JOIN life_financial.operations origin ON origin.account_id=c.account_id AND origin.operation_id=c.operation_id
        WHERE c.account_id=p_account AND origin.committed_version<op.committed_version;
    decision:=CASE WHEN r.qualified THEN life_financial.carry_evaluate(p.body,cp.body,r.bundle,claims,r.prospective) ELSE life_financial.carry_denial(reason) END;
    RETURN op.receipt=jsonb_build_object('kind','CARRY_CHECK','operation_id',op.operation_id,'version',op.committed_version,'decision',decision,'checked_at_ms',r.checked_at_ms,'replayed',false,'trading_permission',false);
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.carry_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.carry_policies;h life_financial.carry_heads;r life_financial.carry_checks;pay life_financial.carry_payments;
    n bigint;m bigint;g bigint;v bigint;bundle jsonb;latest life_financial.operations;
BEGIN
    SELECT * INTO p FROM life_financial.carry_policies WHERE account_id=p_account;
    SELECT * INTO h FROM life_financial.carry_heads WHERE account_id=p_account;
    SELECT count(*) INTO n FROM life_financial.carry_checks WHERE account_id=p_account;
    SELECT count(*) INTO m FROM life_financial.carry_payments WHERE account_id=p_account;
    SELECT count(*) INTO g FROM life_financial.operations WHERE account_id=p_account AND command->>'kind'='CARRY_CHECK';
    IF p.account_id IS NULL THEN RETURN h.account_id IS NULL AND n+m+g=0; END IF;
    IF NOT life_financial.carry_policy_valid(p.body) OR p.model_hash<>encode(sha256(convert_to(life_financial.canonical_json_v1(jsonb_build_object('policy',p.body,'max_payments',p.max_payments)),'UTF8')),'hex')
        OR NOT EXISTS(SELECT 1 FROM life_financial.accounts a WHERE a.account_id=p_account AND a.account_uid=p.body->>'account_uid' AND a.policy_hash=p.policy_hash) THEN RETURN false; END IF;
    IF h.account_id IS NULL THEN RETURN n+m+g=0; END IF;
    IF n<>h.check_count OR g<>n OR m>p.max_payments THEN RETURN false; END IF;
    SELECT * INTO STRICT r FROM life_financial.carry_checks WHERE account_id=p_account AND operation_id=h.operation_id;
    SELECT * INTO STRICT latest FROM life_financial.operations WHERE account_id=p_account AND operation_id=h.operation_id;
    IF h.last_checked_at_ms<>r.checked_at_ms OR latest.committed_version<>(SELECT max(committed_version) FROM life_financial.operations WHERE account_id=p_account AND command->>'kind'='CARRY_CHECK') THEN RETURN false; END IF;
    -- Every retained check is independently reproducible from immutable inputs.
    FOR r IN SELECT * FROM life_financial.carry_checks WHERE account_id=p_account LOOP
        IF NOT life_financial.carry_check_verified(p_account,r.operation_id) THEN RETURN false; END IF;
    END LOOP;
    SELECT c.bundle INTO bundle FROM life_financial.carry_checks c WHERE account_id=p_account AND operation_id=h.qualified_operation_id AND qualified;
    IF h.qualified_operation_id IS DISTINCT FROM (SELECT c.operation_id FROM life_financial.carry_checks c JOIN life_financial.operations o USING(account_id,operation_id)
        WHERE c.account_id=p_account AND c.qualified ORDER BY o.committed_version DESC LIMIT 1) THEN RETURN false; END IF;
    IF m<>COALESCE(jsonb_array_length(bundle#>'{funding,payments}'),0) THEN RETURN false; END IF;
    FOR pay IN SELECT * FROM life_financial.carry_payments WHERE account_id=p_account LOOP
        IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(bundle#>'{funding,payments}') x
            WHERE x->>'bill_id'=pay.bill_id AND (x->>'settled_at_ms')::bigint=pay.settled_at_ms AND (x->>'signed_payment_quote')::numeric=pay.signed_payment_quote)
            OR NOT EXISTS(SELECT 1 FROM life_financial.carry_checks origin WHERE origin.account_id=p_account AND origin.operation_id=pay.operation_id AND origin.qualified
                AND EXISTS(SELECT 1 FROM jsonb_array_elements(origin.bundle#>'{funding,payments}') x WHERE x->>'bill_id'=pay.bill_id
                    AND (x->>'settled_at_ms')::bigint=pay.settled_at_ms AND (x->>'signed_payment_quote')::numeric=pay.signed_payment_quote)) THEN RETURN false; END IF;
    END LOOP;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.carry_current_ready(p_account uuid) RETURNS boolean
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
    RETURN r.qualified AND op.receipt#>'{decision,allowed}'='true'::jsonb
        AND (r.bundle#>>'{account,snapshot,spot_life_base}')::numeric=(SELECT total FROM life_financial.balances WHERE account_id=p_account AND asset='LIFE')
        AND (r.bundle#>>'{account,snapshot,spot_cash_quote}')::numeric=(SELECT total FROM life_financial.balances WHERE account_id=p_account AND asset='USDT')
        AND r.capital_operation_id=(SELECT operation_id FROM life_financial.shared_capital_heads WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.shared_capital_claims c JOIN life_financial.operations o USING(account_id,operation_id)
            WHERE c.account_id=p_account AND o.committed_version>op.committed_version)
        AND life_financial.carry_bundle_reason(p.body,cp,r.bundle,previous,r.bundle#>'{account,snapshot}',
            floor(extract(epoch FROM clock_timestamp())*1000)::bigint,p.max_payments)='CARRY_QUALIFIED';
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.carry_read(p_account uuid,p_rows integer,p_bytes integer,p_operation text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb;h life_financial.carry_heads;op life_financial.operations;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
    IF p_rows IS NULL OR p_rows NOT BETWEEN 1 AND 4096 OR p_bytes IS NULL OR p_bytes NOT BETWEEN 1 AND 16777216 THEN RAISE EXCEPTION 'LIFE_CARRY_READ_BOUND_INVALID' USING ERRCODE='P0001'; END IF;
    IF (SELECT count(*) FROM (SELECT 1 FROM life_financial.carry_checks WHERE account_id=p_account LIMIT p_rows+1) x)>p_rows
        OR (SELECT count(*) FROM (SELECT 1 FROM life_financial.carry_payments WHERE account_id=p_account LIMIT p_rows+1) x)>p_rows THEN
        RAISE EXCEPTION 'LIFE_CARRY_READ_BOUND' USING ERRCODE='P0001'; END IF;
    IF NOT life_financial.carry_verified(p_account) OR NOT life_financial.shared_capital_verified(p_account) THEN RAISE EXCEPTION 'LIFE_CARRY_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO h FROM life_financial.carry_heads WHERE account_id=p_account;
    IF p_operation IS NOT NULL THEN
        SELECT * INTO op FROM life_financial.operations WHERE account_id=p_account AND operation_id=p_operation;
        IF NOT FOUND THEN RETURN NULL; END IF;
        IF op.command->>'kind' IS DISTINCT FROM 'CARRY_CHECK' OR NOT life_financial.carry_check_verified(p_account,p_operation) THEN RAISE EXCEPTION 'LIFE_CARRY_UNVERIFIED' USING ERRCODE='P0001'; END IF;
        result:=jsonb_build_object('command',op.command,'receipt',op.receipt);
    ELSE
        result:=jsonb_build_object('head',to_jsonb(h),'policy',(SELECT body FROM life_financial.carry_policies WHERE account_id=p_account),
            'bundle',(SELECT bundle FROM life_financial.carry_checks WHERE account_id=p_account AND operation_id=h.qualified_operation_id),
            'payments',COALESCE((SELECT jsonb_agg(jsonb_build_object('bill_id',bill_id,'settled_at_ms',settled_at_ms,'signed_payment_quote',signed_payment_quote::text) ORDER BY bill_id) FROM life_financial.carry_payments WHERE account_id=p_account),'[]'::jsonb),
            'latest_receipt',(SELECT receipt FROM life_financial.operations WHERE account_id=p_account AND operation_id=h.operation_id),'trading_permission',false);
    END IF;
    IF octet_length(result::text)>p_bytes THEN RAISE EXCEPTION 'LIFE_CARRY_READ_BOUND' USING ERRCODE='P0001'; END IF;
    RETURN result;
END; $$;

CREATE FUNCTION life_financial.carry_check(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,cmd jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts;h life_financial.carry_heads;p life_financial.carry_policies;cp life_financial.shared_capital_policies;
    cap life_financial.operations;prior life_financial.operations;request jsonb;view jsonb;previous jsonb;claims jsonb;decision jsonb;result jsonb;pay jsonb;
    reason text;op text;now_ms bigint;candidate jsonb:=cmd->'prospective';b jsonb:=cmd->'bundle';
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR jsonb_typeof(cmd) IS DISTINCT FROM 'object'
        OR (SELECT count(*) FROM jsonb_object_keys(cmd))<>3 OR NOT cmd ?& ARRAY['operation_id','bundle','prospective']
        OR jsonb_typeof(cmd->'operation_id') IS DISTINCT FROM 'string' OR cmd->>'operation_id' !~ '^[A-Za-z0-9_.:-]{1,96}$'
        OR octet_length(cmd::text)>1048576 THEN RAISE EXCEPTION 'LIFE_CARRY_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    op:=cmd->>'operation_id';request:=jsonb_build_object('kind','CARRY_CHECK','binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',cmd);
    IF p_fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(request),'UTF8')),'hex') THEN RAISE EXCEPTION 'LIFE_CARRY_FINGERPRINT_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    IF NOT life_financial.carry_verified(p_account) OR NOT life_financial.shared_capital_verified(p_account) THEN RAISE EXCEPTION 'LIFE_CARRY_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO prior FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF prior.command<>request OR prior.fingerprint<>p_fingerprint THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001'; END IF;
        RETURN prior.receipt||jsonb_build_object('replayed',true);
    END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    view:=life_financial.spot_state(p_account);
    IF view->>'digest' IS DISTINCT FROM p_cut THEN RAISE EXCEPTION 'LIFE_CARRY_CUT_STALE' USING ERRCODE='P0001'; END IF;
    IF (view->>'unverified')::boolean THEN RAISE EXCEPTION 'LIFE_CARRY_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT p FROM life_financial.carry_policies WHERE account_id=p_account;
    SELECT * INTO STRICT cp FROM life_financial.shared_capital_policies WHERE account_id=p_account;
    SELECT o.* INTO STRICT cap FROM life_financial.shared_capital_heads sh JOIN life_financial.operations o USING(account_id,operation_id) WHERE sh.account_id=p_account;
    IF (cap.command#>>'{command,snapshot,spot_life_base}')::numeric<>(view#>>'{balances,LIFE,total}')::numeric
        OR (cap.command#>>'{command,snapshot,spot_cash_quote}')::numeric<>(view#>>'{balances,USDT,total}')::numeric THEN
        RAISE EXCEPTION 'LIFE_CARRY_ACCOUNT_CONTEXT_STALE' USING ERRCODE='P0001'; END IF;
    SELECT * INTO h FROM life_financial.carry_heads WHERE account_id=p_account;
    now_ms:=floor(extract(epoch FROM clock_timestamp())*1000)::bigint;
    IF now_ms<(p.body->>'anchor_ms')::bigint OR h.account_id IS NOT NULL AND now_ms<h.last_checked_at_ms THEN RAISE EXCEPTION 'LIFE_CARRY_CLOCK_ROLLBACK' USING ERRCODE='P0001'; END IF;
    IF candidate IS DISTINCT FROM 'null'::jsonb AND (NOT life_financial.shared_claim_valid(candidate,cp.body)
        OR candidate->>'session_id' IS DISTINCT FROM a.current_session::text OR (candidate->>'epoch')::bigint IS DISTINCT FROM a.owner_epoch
        OR (candidate->>'risk_epoch')::bigint IS DISTINCT FROM a.risk_epoch OR NOT EXISTS(SELECT 1 FROM life_financial.sessions s
            WHERE s.account_id=p_account AND s.session_id=a.current_session AND s.config_version=(candidate->>'config_version')::bigint)) THEN
        RAISE EXCEPTION 'LIFE_CARRY_CLAIM_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT bundle INTO previous FROM life_financial.carry_checks WHERE account_id=p_account AND operation_id=h.qualified_operation_id;
    reason:=life_financial.carry_bundle_reason(p.body,cp.body,b,previous,cap.command#>'{command,snapshot}',now_ms,p.max_payments);
    SELECT COALESCE(jsonb_agg(life_financial.shared_claim_body(c) ORDER BY c.intent_id),'[]'::jsonb) INTO claims FROM life_financial.shared_capital_claims c WHERE c.account_id=p_account;
    decision:=CASE WHEN reason='CARRY_QUALIFIED' THEN life_financial.carry_evaluate(p.body,cp.body,b,claims,candidate) ELSE life_financial.carry_denial(reason) END;
    INSERT INTO life_financial.carry_checks VALUES(p_account,op,b,candidate,now_ms,cap.operation_id,h.qualified_operation_id,reason='CARRY_QUALIFIED',reason);
    IF reason='CARRY_QUALIFIED' THEN
        FOR pay IN SELECT x FROM jsonb_array_elements(b#>'{funding,payments}') x LOOP
            INSERT INTO life_financial.carry_payments VALUES(p_account,pay->>'bill_id',(pay->>'settled_at_ms')::bigint,(pay->>'signed_payment_quote')::numeric,op) ON CONFLICT DO NOTHING;
        END LOOP;
    END IF;
    INSERT INTO life_financial.carry_heads VALUES(p_account,now_ms,1,op,CASE WHEN reason='CARRY_QUALIFIED' THEN op ELSE NULL END)
        ON CONFLICT(account_id) DO UPDATE SET last_checked_at_ms=EXCLUDED.last_checked_at_ms,
            check_count=life_financial.carry_heads.check_count+1,operation_id=op,
            qualified_operation_id=CASE WHEN reason='CARRY_QUALIFIED' THEN op ELSE life_financial.carry_heads.qualified_operation_id END;
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    result:=jsonb_build_object('kind','CARRY_CHECK','operation_id',op,'version',p_version+1,'decision',decision,'checked_at_ms',now_ms,'replayed',false,'trading_permission',false);
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version) VALUES(p_account,op,p_fingerprint,request,result,p_version+1);
    RETURN result;
END; $$;

CREATE FUNCTION life_financial.carry_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r life_financial.carry_checks;p life_financial.carry_policies;cp jsonb;previous jsonb;op life_financial.operations;now_ms bigint;
BEGIN
    IF NOT life_financial.carry_verified(NEW.account_id) THEN RAISE EXCEPTION 'LIFE_CARRY_UNVERIFIED_AT_COMMIT' USING ERRCODE='P0001'; END IF;
    SELECT * INTO p FROM life_financial.carry_policies WHERE account_id=NEW.account_id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    SELECT * INTO op FROM life_financial.operations WHERE account_id=NEW.account_id AND committed_version=NEW.version;
    IF op.command->>'kind'='CARRY_CHECK' THEN
        SELECT * INTO STRICT r FROM life_financial.carry_checks WHERE account_id=NEW.account_id AND operation_id=op.operation_id;
        now_ms:=floor(extract(epoch FROM clock_timestamp())*1000)::bigint;
        IF now_ms<r.checked_at_ms THEN RAISE EXCEPTION 'LIFE_CARRY_CLOCK_ROLLBACK_AT_COMMIT' USING ERRCODE='P0001'; END IF;
        IF r.qualified THEN
            SELECT body INTO STRICT cp FROM life_financial.shared_capital_policies WHERE account_id=NEW.account_id;
            SELECT bundle INTO previous FROM life_financial.carry_checks WHERE account_id=NEW.account_id AND operation_id=r.previous_qualified;
            IF life_financial.carry_bundle_reason(p.body,cp,r.bundle,previous,r.bundle#>'{account,snapshot}',now_ms,p.max_payments)<>'CARRY_QUALIFIED' THEN
                RAISE EXCEPTION 'LIFE_CARRY_FACTS_STALE_AT_COMMIT' USING ERRCODE='P0001'; END IF;
        END IF;
    ELSIF op.receipt->>'kind' IN ('SPOT_PREPARE','SPOT_ARM','SPOT_QUOTE_ARM') AND NOT life_financial.carry_current_ready(NEW.account_id) THEN
        RAISE EXCEPTION 'LIFE_CARRY_REVOKED_AT_COMMIT' USING ERRCODE='P0001';
    END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER carry_account_commit AFTER UPDATE ON life_financial.accounts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.version<>OLD.version) EXECUTE FUNCTION life_financial.carry_commit_guard();

CREATE FUNCTION life_financial.carry_attempt_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF NEW.state IN ('PREPARED','ARMED','SEND_UNKNOWN') AND NOT life_financial.carry_current_ready(NEW.account_id) THEN
        RAISE EXCEPTION 'LIFE_CARRY_NOT_READY' USING ERRCODE='P0001'; END IF;
    RETURN NEW;
END; $$;
CREATE TRIGGER carry_before_attempt BEFORE INSERT OR UPDATE OF state ON life_financial.send_attempts
    FOR EACH ROW EXECUTE FUNCTION life_financial.carry_attempt_guard();

CREATE FUNCTION life_financial.carry_progress_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
    IF NEW.account_id<>OLD.account_id OR NEW.last_checked_at_ms<OLD.last_checked_at_ms OR NEW.check_count<>OLD.check_count+1 THEN
        RAISE EXCEPTION 'LIFE_CARRY_PROGRESS_REGRESSION' USING ERRCODE='P0001'; END IF;
    RETURN NEW;
END; $$;
CREATE TRIGGER monotone_progress BEFORE UPDATE ON life_financial.carry_heads FOR EACH ROW EXECUTE FUNCTION life_financial.carry_progress_guard();
DO $$ DECLARE name text; BEGIN
    FOREACH name IN ARRAY ARRAY['carry_policies','carry_checks','carry_payments','carry_heads'] LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',name);
        EXECUTE format('CREATE POLICY scoped_read ON life_financial.%I FOR SELECT USING(life_financial.can_access(account_id))',name);
        IF name='carry_heads' THEN
            EXECUTE format('CREATE TRIGGER retained_history BEFORE DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',name);
        ELSE
            EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',name);
        END IF;
    END LOOP;
END; $$;
REVOKE ALL ON life_financial.carry_policies,life_financial.carry_checks,life_financial.carry_payments,life_financial.carry_heads FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;

-- Preserve the original snapshot OID/ACL, primary digest and every prior guard.
CREATE FUNCTION life_financial.spot_state_before_carry(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE body jsonb; verified boolean; pending boolean; ingress_verified boolean; ingress_pending boolean; financial_verified boolean; financial_pending boolean; markout_verified boolean; shared_verified boolean;
BEGIN
    body:=life_financial.spot_state_v15(p_account)-'digest'-'observed_at';
    verified:=life_financial.recorder_executor_integrity(p_account);
    pending:=EXISTS(SELECT 1 FROM life_financial.recorder_outbox o WHERE o.account_id=p_account
        AND NOT EXISTS(SELECT 1 FROM life_financial.projection_receipts r WHERE r.account_id=p_account AND r.projection_id=o.projection_id));
    body:=body||jsonb_build_object('recorder_verified',verified,'recorder_pending',pending,
        'financial_recovery_required',(body->>'financial_recovery_required')::boolean OR NOT verified OR pending,
        'recovery_required',(body->>'recovery_required')::boolean OR NOT verified OR pending,
        'unverified',(body->>'unverified')::boolean OR NOT verified);
    ingress_verified:=life_financial.spot_ack_ingress_verified(p_account);
    ingress_pending:=EXISTS(SELECT 1 FROM life_financial.spot_ack_ingress r WHERE r.account_id=p_account AND NOT EXISTS(
        SELECT 1 FROM life_financial.spot_ack_ingress_receipts d WHERE d.account_id=p_account AND d.ingress_id=r.ingress_id));
    body:=body||jsonb_build_object('ack_ingress_verified',ingress_verified,'ack_ingress_pending',ingress_pending,
        'ack_ingress_version',(SELECT count(*) FROM life_financial.spot_ack_ingress WHERE account_id=p_account)
            +(SELECT count(*) FROM life_financial.spot_ack_ingress_receipts WHERE account_id=p_account),
        'financial_recovery_required',(body->>'financial_recovery_required')::boolean OR NOT ingress_verified OR ingress_pending,
        'recovery_required',(body->>'recovery_required')::boolean OR NOT ingress_verified OR ingress_pending,
        'unverified',(body->>'unverified')::boolean OR NOT ingress_verified);
    financial_verified:=life_financial.spot_financial_ingress_verified(p_account);
    financial_pending:=EXISTS(SELECT 1 FROM life_financial.spot_financial_ingress r WHERE r.account_id=p_account AND NOT EXISTS(
        SELECT 1 FROM life_financial.spot_financial_ingress_receipts d WHERE d.account_id=p_account AND d.ingress_id=r.ingress_id));
    body:=body||jsonb_build_object('financial_ingress_verified',financial_verified,'financial_ingress_pending',financial_pending,
        'ingress_progress',(SELECT to_jsonb(g) FROM life_financial.spot_ingress_progress g WHERE g.account_id=p_account),
        'financial_recovery_required',(body->>'financial_recovery_required')::boolean OR NOT financial_verified OR financial_pending,
        'recovery_required',(body->>'recovery_required')::boolean OR NOT financial_verified OR financial_pending,
        'unverified',(body->>'unverified')::boolean OR NOT financial_verified);
    markout_verified:=life_financial.spot_markouts_verified(p_account);
    body:=body||jsonb_build_object('markout_verified',markout_verified,
        'recovery_required',(body->>'recovery_required')::boolean OR NOT markout_verified,
        'unverified',(body->>'unverified')::boolean OR NOT markout_verified);
    shared_verified:=life_financial.shared_capital_verified(p_account);
    body:=body||jsonb_build_object('shared_capital',jsonb_build_object('verified',shared_verified,
        'head',(SELECT to_jsonb(h) FROM life_financial.shared_capital_heads h WHERE account_id=p_account),
        'model_hash',(SELECT model_hash FROM life_financial.shared_capital_policies WHERE account_id=p_account)),
        'recovery_required',(body->>'recovery_required')::boolean OR NOT shared_verified,
        'unverified',(body->>'unverified')::boolean OR NOT shared_verified);
    RETURN body||jsonb_build_object('digest',encode(sha256(convert_to(body::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
END; $$;
CREATE OR REPLACE FUNCTION life_financial.spot_state(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b jsonb;verified boolean;ready boolean;
BEGIN
    b:=life_financial.spot_state_before_carry(p_account)-'digest'-'observed_at';
    verified:=life_financial.carry_verified(p_account); ready:=life_financial.carry_current_ready(p_account);
    b:=b||jsonb_build_object('carry',jsonb_build_object('verified',verified,'ready',ready,
        'head',(SELECT to_jsonb(h) FROM life_financial.carry_heads h WHERE account_id=p_account)),
        'recovery_required',(b->>'recovery_required')::boolean OR NOT verified OR NOT ready,
        'unverified',(b->>'unverified')::boolean OR NOT verified);
    RETURN b||jsonb_build_object('digest',encode(sha256(convert_to(b::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
END; $$;

REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;

-- A late carry observation can revoke readiness; it cannot create admission.
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
    IF op.command->>'kind' IN ('CARRY_CHECK','RECORDER_POSITION_STAGE','RECORDER_POSITION_APPLY','RECORDER_EVENT_STAGE','RECORDER_EVENT_APPLY','RECORDER_CONFIG_STAGE','RECORDER_CONFIG_APPLY','RECORDER_STAGE','RECORDER_APPLY','SPOT_MARKOUT','SPOT_INVENTORY_SETTLE','SPOT_ACK','SPOT_FILL','SPOT_CASHFLOW','SPOT_NAV','SPOT_SESSION_TICK','SPOT_LIFECYCLE','SPOT_TERMINAL') OR op.command->>'kind' IN ('SPOT_ACTION_TRANSITION','SPOT_ACTION_ABANDON')
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
