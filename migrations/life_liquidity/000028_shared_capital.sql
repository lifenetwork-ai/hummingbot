-- Exact shared SPOT/linear SWAP allocation. No observation/source/send grant.
CREATE FUNCTION life_financial.shared_capital_policy_valid(p jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE k text; v numeric; tier jsonb; previous_notional numeric:=0; previous_im numeric:=0; previous_mm numeric:=0;
BEGIN
    IF jsonb_typeof(p) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(p))<>13
        OR NOT p ?& ARRAY['account_uid','account_mode','collateral_currency','position_mode','leverage','contract_value_life','lot_contracts',
            'max_age_ms','fee_rate','price_stress_bps','max_stress_loss_quote','limits','tiers']
        OR p->>'account_uid' !~ '^[0-9]{1,64}$' OR p->>'account_mode' IS DISTINCT FROM '2'
        OR p->>'collateral_currency' IS DISTINCT FROM 'USDT' OR p->>'position_mode' NOT IN ('ONEWAY','HEDGE')
        OR EXISTS(SELECT 1 FROM jsonb_each(p) WHERE value='null'::jsonb)
        OR jsonb_typeof(p->'leverage') IS DISTINCT FROM 'number' OR p->>'leverage' !~ '^[1-9][0-9]*$'
        OR (p->>'leverage')::numeric>2147483647
        OR jsonb_typeof(p->'max_age_ms') IS DISTINCT FROM 'number' OR p->>'max_age_ms' !~ '^[1-9][0-9]*$'
        OR (p->>'max_age_ms')::numeric>2147483647
        OR jsonb_typeof(p->'limits') IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(p->'limits'))<>6
        OR NOT (p->'limits') ?& ARRAY['max_abs_delta_base','max_gross_base','min_margin_buffer_quote','max_basis_loss_quote','max_funding_quote','adverse_basis_bps']
        OR jsonb_typeof(p->'tiers') IS DISTINCT FROM 'array' OR jsonb_array_length(p->'tiers') NOT BETWEEN 1 AND 64
        THEN RETURN false; END IF;
    FOREACH k IN ARRAY ARRAY['account_uid','account_mode','collateral_currency','position_mode'] LOOP
        IF jsonb_typeof(p->k) IS DISTINCT FROM 'string' THEN RETURN false; END IF;
    END LOOP;
    FOREACH k IN ARRAY ARRAY['contract_value_life','lot_contracts','fee_rate','price_stress_bps','max_stress_loss_quote'] LOOP
        IF jsonb_typeof(p->k) IS DISTINCT FROM 'string' THEN RETURN false; END IF;
        v:=(p->>k)::life_financial.amount;
        IF k IN ('contract_value_life','lot_contracts') AND v=0 THEN RETURN false; END IF;
    END LOOP;
    IF (p->>'fee_rate')::numeric>1 OR (p->>'price_stress_bps')::numeric>=10000 THEN RETURN false; END IF;
    FOR k IN SELECT jsonb_object_keys(p->'limits') LOOP
        IF jsonb_typeof(p#>ARRAY['limits',k]) IS DISTINCT FROM 'string' THEN RETURN false; END IF;
        v:=(p#>>ARRAY['limits',k])::life_financial.amount;
    END LOOP;
    FOR tier IN SELECT value FROM jsonb_array_elements(p->'tiers') LOOP
        IF jsonb_typeof(tier) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(tier))<>3
            OR NOT tier ?& ARRAY['max_notional_quote','initial_margin_rate','maintenance_margin_rate']
            OR EXISTS(SELECT 1 FROM jsonb_each(tier) WHERE jsonb_typeof(value)<>'string') THEN RETURN false; END IF;
        PERFORM (tier->>'max_notional_quote')::life_financial.positive_amount;
        PERFORM (tier->>'initial_margin_rate')::life_financial.positive_amount;
        PERFORM (tier->>'maintenance_margin_rate')::life_financial.positive_amount;
        IF (tier->>'max_notional_quote')::numeric<=previous_notional
            OR (tier->>'initial_margin_rate')::numeric<previous_im OR (tier->>'maintenance_margin_rate')::numeric<previous_mm
            OR (tier->>'maintenance_margin_rate')::numeric>(tier->>'initial_margin_rate')::numeric
            OR (tier->>'initial_margin_rate')::numeric>1 THEN RETURN false; END IF;
        previous_notional:=(tier->>'max_notional_quote')::numeric;
        previous_im:=(tier->>'initial_margin_rate')::numeric;
        previous_mm:=(tier->>'maintenance_margin_rate')::numeric;
    END LOOP;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE TABLE life_financial.shared_capital_policies (
    account_id uuid PRIMARY KEY REFERENCES life_financial.accounts,
    policy_hash life_financial.digest NOT NULL, body jsonb NOT NULL,
    model_hash life_financial.digest NOT NULL,
    max_claims integer NOT NULL CHECK(max_claims BETWEEN 1 AND 4096),
    FOREIGN KEY(account_id,policy_hash) REFERENCES life_financial.policies,
    CHECK(life_financial.shared_capital_policy_valid(body)),
    CHECK(model_hash=encode(sha256(convert_to(life_financial.canonical_json_v1(jsonb_build_object('policy',body,'max_claims',max_claims)),'UTF8')),'hex'))
);
CREATE TABLE life_financial.shared_capital_observations (
    account_id uuid NOT NULL REFERENCES life_financial.accounts, sequence bigint NOT NULL CHECK(sequence>0),
    body jsonb NOT NULL CHECK(jsonb_typeof(body)='object' AND octet_length(body::text)<=1048576),
    observed_at_ms bigint NOT NULL CHECK(observed_at_ms BETWEEN 1 AND 253402300799999),
    source_hash life_financial.digest NOT NULL, operation_id life_financial.identity NOT NULL,
    PRIMARY KEY(account_id,sequence),
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE life_financial.shared_capital_claims (
    account_id uuid NOT NULL REFERENCES life_financial.accounts, intent_id life_financial.identity NOT NULL,
    wire_id life_financial.identity NOT NULL, account_uid text NOT NULL,
    session_id uuid NOT NULL, epoch bigint NOT NULL CHECK(epoch>0),config_version bigint NOT NULL CHECK(config_version>0),risk_epoch bigint NOT NULL CHECK(risk_epoch>0),
    product text NOT NULL CHECK(product IN ('SPOT','SWAP')),side text NOT NULL CHECK(side IN ('BUY','SELL')),
    position_action text NOT NULL CHECK(position_action IN ('OPEN','CLOSE')),
    quantity_base life_financial.positive_amount NOT NULL,price_usdt life_financial.positive_amount NOT NULL,leverage integer NOT NULL CHECK(leverage>0),
    operation_id life_financial.identity NOT NULL,
    PRIMARY KEY(account_id,intent_id),UNIQUE(account_id,wire_id),
    FOREIGN KEY(account_id,session_id) REFERENCES life_financial.sessions,
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED,
    CHECK(product<>'SPOT' OR position_action='OPEN' AND leverage=1)
);
CREATE TABLE life_financial.shared_capital_heads (
    account_id uuid PRIMARY KEY REFERENCES life_financial.accounts,
    sequence bigint NOT NULL CHECK(sequence>0),last_checked_at timestamptz NOT NULL CHECK(isfinite(last_checked_at)),
    observation_count bigint NOT NULL CHECK(observation_count>0),operation_count bigint NOT NULL CHECK(operation_count>0),
    claim_count integer NOT NULL CHECK(claim_count>=0),operation_id life_financial.identity NOT NULL,
    FOREIGN KEY(account_id,sequence) REFERENCES life_financial.shared_capital_observations,
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX shared_capital_operations ON life_financial.operations(account_id,operation_id)
    WHERE command->>'kind'='SHARED_CAPITAL';

CREATE FUNCTION life_financial.shared_claim_body(r life_financial.shared_capital_claims) RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
    SELECT jsonb_build_object('intent_id',r.intent_id,'wire_id',r.wire_id,'account_uid',r.account_uid,'session_id',r.session_id,
        'epoch',r.epoch,'config_version',r.config_version,'risk_epoch',r.risk_epoch,'product',r.product,'side',r.side,
        'position_action',r.position_action,'quantity_base',r.quantity_base::text,'price_usdt',r.price_usdt::text,'leverage',r.leverage)
$$;

CREATE FUNCTION life_financial.shared_claim_valid(c jsonb,p jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE k text; v life_financial.positive_amount; number bigint;
BEGIN
    IF jsonb_typeof(c) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(c))<>13
        OR NOT c ?& ARRAY['intent_id','wire_id','account_uid','session_id','epoch','config_version','risk_epoch','product','side','position_action','quantity_base','price_usdt','leverage']
        OR c->>'account_uid' IS DISTINCT FROM p->>'account_uid' OR c->>'product' NOT IN ('SPOT','SWAP')
        OR c->>'side' NOT IN ('BUY','SELL') OR c->>'position_action' NOT IN ('OPEN','CLOSE')
        OR EXISTS(SELECT 1 FROM jsonb_each(c) WHERE value='null'::jsonb) THEN RETURN false; END IF;
    FOREACH k IN ARRAY ARRAY['intent_id','wire_id','account_uid','session_id','product','side','position_action','quantity_base','price_usdt'] LOOP
        IF jsonb_typeof(c->k) IS DISTINCT FROM 'string' THEN RETURN false; END IF;
    END LOOP;
    PERFORM (c->>'intent_id')::life_financial.identity; PERFORM (c->>'wire_id')::life_financial.identity; PERFORM (c->>'session_id')::uuid;
    FOREACH k IN ARRAY ARRAY['epoch','config_version','risk_epoch','leverage'] LOOP
        IF jsonb_typeof(c->k) IS DISTINCT FROM 'number' OR c->>k !~ '^[1-9][0-9]*$' THEN RETURN false; END IF;
        number:=(c->>k)::bigint;
    END LOOP;
    FOREACH k IN ARRAY ARRAY['quantity_base','price_usdt'] LOOP v:=(c->>k)::life_financial.positive_amount; END LOOP;
    IF c->>'product'='SPOT' THEN
        RETURN c->>'position_action'='OPEN' AND (c->>'leverage')::bigint=1;
    END IF;
    RETURN c->'leverage'=p->'leverage'
        AND mod((c->>'quantity_base')::numeric,(p->>'contract_value_life')::numeric*(p->>'lot_contracts')::numeric)=0;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.shared_snapshot_valid(s jsonb,p jsonb,p_now bigint,p_max integer) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE k text; v numeric; c jsonb;
BEGIN
    IF jsonb_typeof(s) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(s))<>23
        OR NOT s ?& ARRAY['account_uid','account_mode','collateral_currency','position_mode','margin_mode','leverage',
            'observed_at_ms','sequence','scope_complete','account_source','spot_life_base','spot_cash_quote','long_base','short_base',
            'collateral_quote','initial_margin_quote','maintenance_margin_quote','funding_liability_quote','mark_price_usdt','index_price_usdt','mark_source','index_source','pending']
        OR EXISTS(SELECT 1 FROM jsonb_each(s) WHERE value='null'::jsonb)
        OR jsonb_typeof(s->'leverage') IS DISTINCT FROM 'number' OR s->>'leverage' !~ '^[1-9][0-9]*$'
        OR s->>'margin_mode' IS DISTINCT FROM 'cross' OR s->'scope_complete' IS DISTINCT FROM 'true'::jsonb
        OR s->>'account_source' IS DISTINCT FROM 'okx_account' OR s->>'mark_source' IS DISTINCT FROM 'okx_mark'
        OR s->>'index_source' IS DISTINCT FROM 'okx_index' THEN RETURN false; END IF;
    FOREACH k IN ARRAY ARRAY['account_uid','account_mode','collateral_currency','position_mode','leverage'] LOOP
        IF s->k IS DISTINCT FROM p->k THEN RETURN false; END IF;
    END LOOP;
    FOREACH k IN ARRAY ARRAY['account_uid','account_mode','collateral_currency','position_mode','margin_mode','account_source','mark_source','index_source'] LOOP
        IF jsonb_typeof(s->k) IS DISTINCT FROM 'string' THEN RETURN false; END IF;
    END LOOP;
    FOREACH k IN ARRAY ARRAY['observed_at_ms','sequence'] LOOP
        IF jsonb_typeof(s->k) IS DISTINCT FROM 'number' OR s->>k !~ '^[1-9][0-9]*$' THEN RETURN false; END IF;
        IF (s->>k)::numeric>(CASE WHEN k='sequence' THEN 9223372036854775807 ELSE 253402300799999 END) THEN RETURN false; END IF;
    END LOOP;
    IF p_now<(s->>'observed_at_ms')::bigint OR p_now-(s->>'observed_at_ms')::bigint>(p->>'max_age_ms')::bigint
        OR jsonb_typeof(s->'pending') IS DISTINCT FROM 'array' OR jsonb_array_length(s->'pending')>p_max THEN RETURN false; END IF;
    FOREACH k IN ARRAY ARRAY['spot_life_base','spot_cash_quote','long_base','short_base','collateral_quote','initial_margin_quote',
        'maintenance_margin_quote','funding_liability_quote','mark_price_usdt','index_price_usdt'] LOOP
        IF jsonb_typeof(s->k) IS DISTINCT FROM 'string' THEN RETURN false; END IF;
        v:=(s->>k)::life_financial.amount;
        IF k IN ('mark_price_usdt','index_price_usdt') AND v=0 THEN RETURN false; END IF;
    END LOOP;
    IF p->>'position_mode'='ONEWAY' AND (s->>'long_base')::numeric>0 AND (s->>'short_base')::numeric>0 THEN RETURN false; END IF;
    FOR c IN SELECT value FROM jsonb_array_elements(s->'pending') LOOP
        IF NOT life_financial.shared_claim_valid(c,p) THEN RETURN false; END IF;
    END LOOP;
    RETURN jsonb_array_length(s->'pending')=(SELECT count(DISTINCT item.value->>'intent_id') FROM jsonb_array_elements(s->'pending') item);
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

-- Integer div/mod then exact multiplication: numeric division's default result
-- scale must not discard small quote amounts, even for a large integral part.
CREATE FUNCTION life_financial.shared_quote_ceiling(p_num numeric,p_den numeric) RETURNS numeric
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
    SELECT (div(p_num*1000000000000000000,p_den)
        +CASE WHEN mod(p_num*1000000000000000000,p_den)>0 THEN 1 ELSE 0 END)*0.000000000000000001
$$;

-- Exact IM numerator/denominator and exact MM. Round only after combining the
-- original account requirement and stress increment; subtracting rounded IMs
-- can otherwise understate that increment by one quote unit.
CREATE FUNCTION life_financial.shared_margin(p_notional numeric,p_tiers jsonb,p_leverage integer) RETURNS numeric[]
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE t jsonb;
BEGIN
    IF p_notional=0 THEN RETURN ARRAY[0::numeric,1::numeric,0::numeric]; END IF;
    FOR t IN SELECT value FROM jsonb_array_elements(p_tiers) LOOP
        IF p_notional<=(t->>'max_notional_quote')::numeric THEN
            IF (t->>'initial_margin_rate')::numeric*p_leverage>=1 THEN
                RETURN ARRAY[p_notional*(t->>'initial_margin_rate')::numeric,1::numeric,p_notional*(t->>'maintenance_margin_rate')::numeric];
            END IF;
            RETURN ARRAY[p_notional,p_leverage::numeric,p_notional*(t->>'maintenance_margin_rate')::numeric];
        END IF;
    END LOOP;
    RAISE EXCEPTION 'LIFE_CAPITAL_TIER_UNCOVERED' USING ERRCODE='P0001';
END; $$;

CREATE FUNCTION life_financial.shared_denial(p_reason text) RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
    SELECT jsonb_build_object('allowed',false,'reason_code',p_reason,'net_delta_worst_base','0','gross_worst_base','0',
        'basis_loss_quote','0','initial_margin_quote','0','maintenance_margin_quote','0','initial_buffer_quote','0',
        'maintenance_buffer_quote','0','spot_buy_hold_quote','0','fees_quote','0','directional_stress_quote','0','stress_loss_quote','0')
$$;

-- Monetary decimal spellings are equivalent; all identity/unit fields are exact.
CREATE FUNCTION life_financial.shared_claim_equal(a jsonb,b jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
    SELECT (a-ARRAY['quantity_base','price_usdt'])=(b-ARRAY['quantity_base','price_usdt'])
        AND (a->>'quantity_base')::numeric=(b->>'quantity_base')::numeric
        AND (a->>'price_usdt')::numeric=(b->>'price_usdt')::numeric
$$;

CREATE FUNCTION life_financial.shared_evaluate(p jsonb,s jsonb,p_claims jsonb) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE c jsonb; orders jsonb; price numeric; qty numeric; spend numeric:=0; fees numeric:=0;
    buy numeric:=0;sell numeric:=0;oplong numeric:=0;opshort numeric:=0;cllong numeric:=0;clshort numeric:=0;
    sp numeric; lg numeric; sh numeric; delta numeric:=0; gross numeric:=0; perpetual numeric; stressed numeric;
    basis numeric; directional numeric; im numeric; mm numeric; available numeric; stress numeric;
    actual numeric[]; future numeric[]; result jsonb; reason text:='CAPITAL_READY';
BEGIN
    IF EXISTS(SELECT 1 FROM jsonb_array_elements(p_claims||(s->'pending')) a JOIN jsonb_array_elements(p_claims||(s->'pending')) b
        ON a->>'intent_id'=b->>'intent_id' WHERE NOT life_financial.shared_claim_equal(a,b)) THEN RETURN life_financial.shared_denial('CAPITAL_PENDING_IDENTITY_MISMATCH'); END IF;
    SELECT COALESCE(jsonb_agg(value ORDER BY value->>'intent_id'),'[]'::jsonb) INTO orders FROM (SELECT DISTINCT ON (e.value->>'intent_id') e.value FROM jsonb_array_elements(p_claims||(s->'pending')) WITH ORDINALITY e(value,ordinal) ORDER BY e.value->>'intent_id',e.ordinal) x;
    IF jsonb_array_length(orders)<>(SELECT count(DISTINCT item.value->>'wire_id') FROM jsonb_array_elements(orders) item) THEN
        RETURN life_financial.shared_denial('CAPITAL_WIRE_ID_DUPLICATE'); END IF;
    price:=greatest((s->>'mark_price_usdt')::numeric,(s->>'index_price_usdt')::numeric,
        COALESCE((SELECT max((item.value->>'price_usdt')::numeric) FROM jsonb_array_elements(orders) item),0));
    FOR c IN SELECT value FROM jsonb_array_elements(orders) LOOP
        qty:=(c->>'quantity_base')::numeric; fees:=fees+qty*greatest((c->>'price_usdt')::numeric,price)*(p->>'fee_rate')::numeric;
        IF c->>'product'='SPOT' THEN
            IF c->>'side'='BUY' THEN buy:=buy+qty;spend:=spend+qty*(c->>'price_usdt')::numeric; ELSE sell:=sell+qty; END IF;
        ELSIF c->>'position_action'='OPEN' THEN
            IF c->>'side'='BUY' THEN oplong:=oplong+qty; ELSE opshort:=opshort+qty; END IF;
        ELSIF c->>'side'='SELL' THEN cllong:=cllong+qty; ELSE clshort:=clshort+qty; END IF;
    END LOOP;
    -- Unknown fee asset: a SELL can debit LIFE as well as the traded base.
    IF sell*(1+(p->>'fee_rate')::numeric)>(s->>'spot_life_base')::numeric THEN RETURN life_financial.shared_denial('CAPITAL_SPOT_INVENTORY_UNAVAILABLE'); END IF;
    IF spend+fees>(s->>'spot_cash_quote')::numeric THEN RETURN life_financial.shared_denial('CAPITAL_CASH_UNAVAILABLE'); END IF;
    IF cllong>(s->>'long_base')::numeric OR clshort>(s->>'short_base')::numeric THEN RETURN life_financial.shared_denial('CAPITAL_CLOSE_EXCEEDS_POSITION'); END IF;
    FOREACH sp IN ARRAY ARRAY[(s->>'spot_life_base')::numeric-sell*(1+(p->>'fee_rate')::numeric),(s->>'spot_life_base')::numeric+buy] LOOP
        FOREACH lg IN ARRAY ARRAY[(s->>'long_base')::numeric-cllong,(s->>'long_base')::numeric+oplong] LOOP
            FOREACH sh IN ARRAY ARRAY[(s->>'short_base')::numeric-clshort,(s->>'short_base')::numeric+opshort] LOOP
                delta:=greatest(delta,abs(sp+lg-sh));gross:=greatest(gross,sp+lg+sh);
            END LOOP;
        END LOOP;
    END LOOP;
    perpetual:=(s->>'long_base')::numeric+oplong+(s->>'short_base')::numeric+opshort;
    stressed:=price*(1+(p->>'price_stress_bps')::numeric*0.0001);
    basis:=perpetual*(stressed*(p#>>'{limits,adverse_basis_bps}')::numeric*0.0001+abs((s->>'mark_price_usdt')::numeric-(s->>'index_price_usdt')::numeric));
    directional:=delta*price*(p->>'price_stress_bps')::numeric*0.0001;
    BEGIN
        actual:=life_financial.shared_margin((s->>'long_base')::numeric*greatest((s->>'mark_price_usdt')::numeric,(s->>'index_price_usdt')::numeric)
            +(s->>'short_base')::numeric*greatest((s->>'mark_price_usdt')::numeric,(s->>'index_price_usdt')::numeric),p->'tiers',(p->>'leverage')::integer);
        future:=life_financial.shared_margin(perpetual*stressed,p->'tiers',(p->>'leverage')::integer);
    EXCEPTION WHEN raise_exception THEN RETURN life_financial.shared_denial('CAPITAL_TIER_UNCOVERED'); END;
    im:=life_financial.shared_quote_ceiling(greatest((s->>'initial_margin_quote')::numeric*actual[2],actual[1])*future[2]
        +greatest(0,future[1]*actual[2]-actual[1]*future[2]),actual[2]*future[2]);
    mm:=life_financial.shared_quote_ceiling(greatest((s->>'maintenance_margin_quote')::numeric,actual[3])+greatest(0,future[3]-actual[3]),1);
    available:=(s->>'collateral_quote')::numeric-spend-fees-basis-directional-(s->>'funding_liability_quote')::numeric;
    stress:=fees+basis+directional+(s->>'funding_liability_quote')::numeric;
    IF delta>(p#>>'{limits,max_abs_delta_base}')::numeric THEN reason:='CAPITAL_DELTA_LIMIT';
    ELSIF gross>(p#>>'{limits,max_gross_base}')::numeric THEN reason:='CAPITAL_GROSS_LIMIT';
    ELSIF basis>(p#>>'{limits,max_basis_loss_quote}')::numeric THEN reason:='CAPITAL_BASIS_LIMIT';
    ELSIF (s->>'funding_liability_quote')::numeric>(p#>>'{limits,max_funding_quote}')::numeric THEN reason:='CAPITAL_FUNDING_LIMIT';
    ELSIF stress>(p->>'max_stress_loss_quote')::numeric THEN reason:='CAPITAL_STRESS_LOSS_LIMIT';
    ELSIF available-im<(p#>>'{limits,min_margin_buffer_quote}')::numeric THEN reason:='CAPITAL_INITIAL_MARGIN_LOW';
    ELSIF available-mm<(p#>>'{limits,min_margin_buffer_quote}')::numeric THEN reason:='CAPITAL_MAINTENANCE_MARGIN_LOW'; END IF;
    result:=jsonb_build_object('allowed',reason='CAPITAL_READY','reason_code',reason,'net_delta_worst_base',delta::text,'gross_worst_base',gross::text,
        'basis_loss_quote',basis::text,'initial_margin_quote',im::text,'maintenance_margin_quote',mm::text,'initial_buffer_quote',(available-im)::text,
        'maintenance_buffer_quote',(available-mm)::text,'spot_buy_hold_quote',spend::text,'fees_quote',fees::text,'directional_stress_quote',directional::text,'stress_loss_quote',stress::text);
    RETURN result;
END; $$;

-- Recompute one permanent result from its original immutable observation and
-- the claims already allocated at that version. Later claims never rewrite it.
CREATE FUNCTION life_financial.shared_operation_verified(p_account uuid,p_operation text) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE op life_financial.operations;p life_financial.shared_capital_policies;orders jsonb;candidate jsonb;decision jsonb;
    known boolean;changed boolean;expected jsonb;s jsonb;
BEGIN
    SELECT * INTO STRICT op FROM life_financial.operations WHERE account_id=p_account AND operation_id=p_operation;
    SELECT * INTO STRICT p FROM life_financial.shared_capital_policies WHERE account_id=p_account;
    IF op.command->>'kind' IS DISTINCT FROM 'SHARED_CAPITAL' OR (SELECT count(*) FROM jsonb_object_keys(op.command))<>6
        OR op.command#>>'{command,operation_id}' IS DISTINCT FROM p_operation
        OR (op.command->>'version')::bigint<>op.committed_version-1
        OR op.command#>>'{binding,account_uid}' IS DISTINCT FROM p.body->>'account_uid'
        OR op.command#>>'{binding,policy_hash}' IS DISTINCT FROM p.policy_hash
        OR op.fingerprint<>encode(sha256(convert_to(life_financial.canonical_json_v1(op.command),'UTF8')),'hex') THEN RETURN false; END IF;
    s:=op.command#>'{command,snapshot}';candidate:=op.command#>'{command,claim}';
    IF NOT life_financial.shared_snapshot_valid(s,p.body,floor(extract(epoch FROM op.recorded_at)*1000)::bigint,p.max_claims)
        OR NOT EXISTS(SELECT 1 FROM life_financial.shared_capital_observations x
            WHERE x.account_id=p_account AND x.sequence=(s->>'sequence')::bigint AND x.body=s)
        OR op.command#>>'{command,kind}' NOT IN ('RESERVE','OBSERVE') THEN RETURN false; END IF;
    SELECT COALESCE(jsonb_agg(life_financial.shared_claim_body(c) ORDER BY c.intent_id),'[]'::jsonb) INTO orders
        FROM life_financial.shared_capital_claims c JOIN life_financial.operations origin USING(account_id,operation_id)
        WHERE c.account_id=p_account AND origin.committed_version<op.committed_version;
    known:=EXISTS(SELECT 1 FROM jsonb_array_elements(orders) e WHERE e.value->>'intent_id'=candidate->>'intent_id');
    IF op.command#>>'{command,kind}'='RESERVE' THEN
        IF NOT life_financial.shared_claim_valid(candidate,p.body)
            OR candidate->>'session_id' IS DISTINCT FROM op.command#>>'{owner,session_id}'
            OR candidate->'epoch' IS DISTINCT FROM op.command#>'{owner,epoch}'
            OR known AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(orders) e WHERE life_financial.shared_claim_equal(e.value,candidate)) THEN RETURN false; END IF;
        IF NOT known THEN orders:=orders||jsonb_build_array(candidate); END IF;
    ELSIF candidate IS DISTINCT FROM 'null'::jsonb THEN RETURN false; END IF;
    decision:=life_financial.shared_evaluate(p.body,s,orders);
    changed:=op.command#>>'{command,kind}'='RESERVE' AND (decision->>'allowed')::boolean AND NOT known;
    IF (SELECT count(*) FROM life_financial.shared_capital_claims WHERE account_id=p_account AND operation_id=p_operation)
        <>(CASE WHEN changed THEN 1 ELSE 0 END) THEN RETURN false; END IF;
    IF changed AND NOT EXISTS(SELECT 1 FROM life_financial.shared_capital_claims c WHERE c.account_id=p_account AND c.operation_id=p_operation
        AND life_financial.shared_claim_equal(life_financial.shared_claim_body(c),candidate)) THEN RETURN false; END IF;
    expected:=jsonb_build_object('kind','SHARED_CAPITAL','operation_id',p_operation,'version',op.committed_version,
        'decision',decision,'changed',changed,'replayed',false,'trading_permission',false);
    RETURN op.receipt=expected;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.shared_capital_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.shared_capital_policies; h life_financial.shared_capital_heads; obs life_financial.shared_capital_observations;
    c life_financial.shared_capital_claims; op life_financial.operations; data jsonb; n bigint; f bigint; g bigint;
BEGIN
    SELECT * INTO p FROM life_financial.shared_capital_policies WHERE account_id=p_account;
    SELECT count(*) INTO n FROM life_financial.shared_capital_observations WHERE account_id=p_account;
    SELECT count(*) INTO f FROM life_financial.shared_capital_claims WHERE account_id=p_account;
    SELECT count(*) INTO g FROM life_financial.operations WHERE account_id=p_account AND command->>'kind'='SHARED_CAPITAL';
    SELECT * INTO h FROM life_financial.shared_capital_heads WHERE account_id=p_account;
    IF p.account_id IS NULL THEN RETURN h.account_id IS NULL AND n+f+g=0; END IF;
    IF NOT life_financial.shared_capital_policy_valid(p.body) OR p.model_hash<>encode(sha256(convert_to(life_financial.canonical_json_v1(jsonb_build_object('policy',p.body,'max_claims',p.max_claims)),'UTF8')),'hex')
        OR NOT EXISTS(SELECT 1 FROM life_financial.accounts a WHERE a.account_id=p_account AND a.policy_hash=p.policy_hash AND a.account_uid=p.body->>'account_uid') THEN RETURN false; END IF;
    IF h.account_id IS NULL THEN RETURN n+f+g=0; END IF;
    IF (n,f,g) IS DISTINCT FROM (h.observation_count,h.claim_count::bigint,h.operation_count) OR f>p.max_claims THEN RETURN false; END IF;
    SELECT * INTO STRICT obs FROM life_financial.shared_capital_observations WHERE account_id=p_account AND sequence=h.sequence;
    IF obs.sequence<>(obs.body->>'sequence')::bigint OR obs.observed_at_ms<>(obs.body->>'observed_at_ms')::bigint
        OR obs.source_hash<>encode(sha256(convert_to(life_financial.canonical_json_v1(obs.body),'UTF8')),'hex')
        OR NOT life_financial.shared_snapshot_valid(obs.body,p.body,obs.observed_at_ms,p.max_claims)
        OR floor(extract(epoch FROM h.last_checked_at)*1000)::bigint<obs.observed_at_ms THEN RETURN false; END IF;
    IF h.sequence<>(SELECT max(sequence) FROM life_financial.shared_capital_observations WHERE account_id=p_account) THEN RETURN false; END IF;
    SELECT * INTO STRICT op FROM life_financial.operations WHERE account_id=p_account AND operation_id=h.operation_id;
    IF op.committed_version<>(SELECT max(committed_version) FROM life_financial.operations WHERE account_id=p_account AND command->>'kind'='SHARED_CAPITAL') THEN RETURN false; END IF;
    IF NOT EXISTS(SELECT 1 FROM life_financial.operations origin WHERE origin.account_id=p_account AND origin.operation_id=obs.operation_id
        AND origin.command#>'{command,snapshot}'=obs.body
        AND origin.fingerprint=encode(sha256(convert_to(life_financial.canonical_json_v1(origin.command),'UTF8')),'hex')) THEN RETURN false; END IF;
    IF NOT life_financial.shared_operation_verified(p_account,op.operation_id) OR op.command#>'{command,snapshot}'<>obs.body
        OR op.receipt->'trading_permission' IS DISTINCT FROM 'false'::jsonb OR op.receipt->'replayed' IS DISTINCT FROM 'false'::jsonb
        OR op.receipt->>'kind' IS DISTINCT FROM 'SHARED_CAPITAL' OR op.receipt->>'operation_id' IS DISTINCT FROM op.operation_id
        OR (op.receipt->>'version')::bigint IS DISTINCT FROM op.committed_version
        OR op.fingerprint<>encode(sha256(convert_to(life_financial.canonical_json_v1(op.command),'UTF8')),'hex') THEN RETURN false; END IF;
    FOR c IN SELECT * FROM life_financial.shared_capital_claims WHERE account_id=p_account LOOP
        data:=life_financial.shared_claim_body(c);
        SELECT * INTO STRICT op FROM life_financial.operations WHERE account_id=p_account AND operation_id=c.operation_id;
        IF NOT life_financial.shared_claim_valid(data,p.body) OR op.command->>'kind'<>'SHARED_CAPITAL'
            OR NOT life_financial.shared_claim_equal(op.command#>'{command,claim}',data) OR op.receipt#>'{decision,allowed}' IS DISTINCT FROM 'true'::jsonb
            OR op.receipt->'changed' IS DISTINCT FROM 'true'::jsonb OR op.receipt->'trading_permission' IS DISTINCT FROM 'false'::jsonb
            OR op.fingerprint<>encode(sha256(convert_to(life_financial.canonical_json_v1(op.command),'UTF8')),'hex') THEN RETURN false; END IF;
    END LOOP;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.shared_capital_frame(p_account uuid,p_rows integer,p_bytes integer) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE h life_financial.shared_capital_heads; result jsonb; n bigint;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
    IF p_rows IS NULL OR p_rows NOT BETWEEN 1 AND 4096 OR p_bytes IS NULL OR p_bytes NOT BETWEEN 1 AND 16777216 THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_READ_BOUND_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT count(*) INTO n FROM (SELECT 1 FROM life_financial.shared_capital_claims WHERE account_id=p_account LIMIT p_rows+1) x;
    SELECT * INTO h FROM life_financial.shared_capital_heads WHERE account_id=p_account;
    IF n>p_rows OR (SELECT octet_length(body::text) FROM life_financial.shared_capital_observations WHERE account_id=p_account AND sequence=h.sequence)>p_bytes THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_READ_BOUND' USING ERRCODE='P0001'; END IF;
    IF NOT life_financial.shared_capital_verified(p_account) THEN RAISE EXCEPTION 'LIFE_CAPITAL_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    result:=jsonb_build_object('head',to_jsonb(h),'policy',(SELECT body FROM life_financial.shared_capital_policies WHERE account_id=p_account),
        'snapshot',(SELECT body FROM life_financial.shared_capital_observations WHERE account_id=p_account AND sequence=h.sequence),
        'claims',COALESCE((SELECT jsonb_agg(life_financial.shared_claim_body(c) ORDER BY intent_id) FROM life_financial.shared_capital_claims c WHERE account_id=p_account),'[]'::jsonb),
        'trading_permission',false);
    IF octet_length(result::text)>p_bytes THEN RAISE EXCEPTION 'LIFE_CAPITAL_READ_BOUND' USING ERRCODE='P0001'; END IF;
    RETURN result;
END; $$;

CREATE FUNCTION life_financial.shared_capital_read_operation(p_account uuid,p_operation text,p_rows integer,p_bytes integer) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE op life_financial.operations;
BEGIN
    PERFORM life_financial.shared_capital_frame(p_account,p_rows,p_bytes);
    SELECT * INTO op FROM life_financial.operations WHERE account_id=p_account AND operation_id=p_operation;
    IF NOT FOUND THEN RETURN NULL; END IF;
    IF NOT life_financial.shared_operation_verified(p_account,p_operation) THEN RAISE EXCEPTION 'LIFE_CAPITAL_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    IF octet_length(op.command::text)+octet_length(op.receipt::text)>p_bytes THEN RAISE EXCEPTION 'LIFE_CAPITAL_READ_BOUND' USING ERRCODE='P0001'; END IF;
    RETURN jsonb_build_object('command',op.command,'receipt',op.receipt);
END; $$;

CREATE FUNCTION life_financial.shared_capital_command(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,cmd jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts;p life_financial.shared_capital_policies;h life_financial.shared_capital_heads;
    previous life_financial.operations;observed life_financial.shared_capital_observations;known life_financial.shared_capital_claims;
    request jsonb;view jsonb;orders jsonb;candidate jsonb:=cmd->'claim';snapshot jsonb:=cmd->'snapshot';decision jsonb;result jsonb;
    op text;at timestamptz;now_ms bigint;new_observation boolean:=false;changed boolean:=false;source_hash text;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR jsonb_typeof(cmd) IS DISTINCT FROM 'object'
        OR (SELECT count(*) FROM jsonb_object_keys(cmd))<>4 OR NOT cmd ?& ARRAY['kind','operation_id','snapshot','claim']
        OR cmd->>'kind' NOT IN ('RESERVE','OBSERVE') OR jsonb_typeof(cmd->'operation_id') IS DISTINCT FROM 'string'
        OR cmd->>'operation_id' !~ '^[A-Za-z0-9_.:-]{1,96}$' OR octet_length(cmd::text)>1048576
        OR cmd->>'kind'='OBSERVE' AND candidate IS DISTINCT FROM 'null'::jsonb THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    op:=cmd->>'operation_id';
    request:=jsonb_build_object('kind','SHARED_CAPITAL','binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',cmd);
    IF p_fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(request),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_FINGERPRINT_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    IF NOT life_financial.shared_capital_verified(p_account) THEN RAISE EXCEPTION 'LIFE_CAPITAL_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO previous FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF NOT life_financial.shared_operation_verified(p_account,op) THEN RAISE EXCEPTION 'LIFE_CAPITAL_UNVERIFIED' USING ERRCODE='P0001'; END IF;
        IF previous.command<>request OR previous.fingerprint<>p_fingerprint THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001'; END IF;
        RETURN previous.receipt||jsonb_build_object('replayed',true);
    END IF;
    PERFORM life_financial.guard_account(p_account,p_binding,p_owner,p_version); PERFORM life_financial.guard_spot_session(p_account);
    view:=life_financial.spot_state(p_account);
    IF view->>'digest' IS DISTINCT FROM p_cut THEN RAISE EXCEPTION 'LIFE_CAPITAL_CUT_STALE' USING ERRCODE='P0001'; END IF;
    IF (view->>'financial_recovery_required')::boolean OR (view->>'unverified')::boolean THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_FINANCIAL_RECOVERY_REQUIRED' USING ERRCODE='P0001'; END IF;
    SELECT p0.* INTO p FROM life_financial.shared_capital_policies p0 JOIN life_financial.accounts a0 USING(account_id,policy_hash) WHERE p0.account_id=p_account;
    IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_CAPITAL_POLICY_REQUIRED' USING ERRCODE='P0001'; END IF;
    at:=clock_timestamp();now_ms:=floor(extract(epoch FROM at)*1000)::bigint;
    SELECT * INTO h FROM life_financial.shared_capital_heads WHERE account_id=p_account;
    IF h.account_id IS NOT NULL AND at<h.last_checked_at THEN RAISE EXCEPTION 'LIFE_CAPITAL_CLOCK_ROLLBACK' USING ERRCODE='P0001'; END IF;
    IF NOT life_financial.shared_snapshot_valid(snapshot,p.body,now_ms,p.max_claims) THEN RAISE EXCEPTION 'LIFE_CAPITAL_SNAPSHOT_UNQUALIFIED' USING ERRCODE='P0001'; END IF;
    IF (snapshot->>'spot_life_base')::numeric<>(view#>>'{balances,LIFE,total}')::numeric
        OR (snapshot->>'spot_cash_quote')::numeric<>(view#>>'{balances,USDT,total}')::numeric THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_SPOT_BALANCE_MISMATCH' USING ERRCODE='P0001'; END IF;
    IF h.account_id IS NOT NULL AND ((snapshot->>'sequence')::bigint<h.sequence
        OR (snapshot->>'observed_at_ms')::bigint<(SELECT observed_at_ms FROM life_financial.shared_capital_observations WHERE account_id=p_account AND sequence=h.sequence)) THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_SNAPSHOT_REPLAY_OR_MUTATION' USING ERRCODE='P0001'; END IF;
    SELECT * INTO observed FROM life_financial.shared_capital_observations WHERE account_id=p_account AND sequence=(snapshot->>'sequence')::bigint;
    source_hash:=encode(sha256(convert_to(life_financial.canonical_json_v1(snapshot),'UTF8')),'hex');
    IF FOUND THEN
        IF observed.body<>snapshot OR observed.source_hash<>source_hash THEN RAISE EXCEPTION 'LIFE_CAPITAL_SNAPSHOT_REPLAY_OR_MUTATION' USING ERRCODE='P0001'; END IF;
    ELSE new_observation:=true; END IF;
    SELECT COALESCE(jsonb_agg(life_financial.shared_claim_body(c) ORDER BY c.intent_id),'[]'::jsonb) INTO orders FROM life_financial.shared_capital_claims c WHERE c.account_id=p_account;
    IF cmd->>'kind'='RESERVE' THEN
        IF NOT life_financial.shared_claim_valid(candidate,p.body) OR candidate->>'session_id'<>a.current_session::text
            OR (candidate->>'epoch')::bigint<>a.owner_epoch OR (candidate->>'risk_epoch')::bigint<>a.risk_epoch
            OR NOT EXISTS(SELECT 1 FROM life_financial.sessions WHERE account_id=p_account AND session_id=a.current_session AND config_version=(candidate->>'config_version')::bigint) THEN
            RAISE EXCEPTION 'LIFE_CAPITAL_CLAIM_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
        SELECT * INTO known FROM life_financial.shared_capital_claims WHERE account_id=p_account AND intent_id=candidate->>'intent_id';
        IF FOUND THEN
            IF NOT life_financial.shared_claim_equal(life_financial.shared_claim_body(known),candidate) OR NOT EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=p_account
                AND operation_id=known.operation_id AND command->'owner'=p_owner) THEN RAISE EXCEPTION 'LIFE_CAPITAL_CLAIM_CHANGED' USING ERRCODE='P0001'; END IF;
        ELSE
            IF jsonb_array_length(orders)>=p.max_claims THEN RAISE EXCEPTION 'LIFE_CAPITAL_CLAIM_BOUND' USING ERRCODE='P0001'; END IF;
            IF EXISTS(SELECT 1 FROM jsonb_array_elements(snapshot->'pending') c WHERE c->>'intent_id'=candidate->>'intent_id') THEN
                RAISE EXCEPTION 'LIFE_CAPITAL_UNOWNED_PENDING_ID' USING ERRCODE='P0001'; END IF;
            orders:=orders||jsonb_build_array(candidate);
        END IF;
        IF EXISTS(SELECT 1 FROM jsonb_array_elements(orders||(snapshot->'pending')) c WHERE c->>'session_id'<>a.current_session::text OR (c->>'epoch')::bigint<>a.owner_epoch) THEN
            RAISE EXCEPTION 'LIFE_CAPITAL_OLD_SESSION_CLAIMS' USING ERRCODE='P0001'; END IF;
    END IF;
    -- Native working orders cannot vanish from a caller-qualified account DTO.
    IF EXISTS(SELECT 1 FROM life_financial.send_attempts t WHERE t.account_id=p_account AND t.state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND') AND NOT EXISTS(
        SELECT 1 FROM jsonb_array_elements(orders||(snapshot->'pending')) c WHERE c->>'intent_id'=t.intent_id AND c->>'wire_id'=t.wire_id
            AND c->>'product'=t.product AND c->>'side'=t.side AND (c->>'quantity_base')::numeric=t.quantity AND (c->>'price_usdt')::numeric=t.price)) THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_NATIVE_ORDER_SCOPE_INCOMPLETE' USING ERRCODE='P0001'; END IF;
    decision:=life_financial.shared_evaluate(p.body,snapshot,orders);
    changed:=cmd->>'kind'='RESERVE' AND (decision->>'allowed')::boolean AND known.account_id IS NULL;
    IF new_observation THEN INSERT INTO life_financial.shared_capital_observations VALUES(p_account,(snapshot->>'sequence')::bigint,snapshot,(snapshot->>'observed_at_ms')::bigint,source_hash,op); END IF;
    IF changed THEN
        INSERT INTO life_financial.shared_capital_claims VALUES(p_account,candidate->>'intent_id',candidate->>'wire_id',candidate->>'account_uid',
            (candidate->>'session_id')::uuid,(candidate->>'epoch')::bigint,(candidate->>'config_version')::bigint,(candidate->>'risk_epoch')::bigint,
            candidate->>'product',candidate->>'side',candidate->>'position_action',(candidate->>'quantity_base')::numeric,(candidate->>'price_usdt')::numeric,(candidate->>'leverage')::integer,op);
    END IF;
    INSERT INTO life_financial.shared_capital_heads VALUES(p_account,(snapshot->>'sequence')::bigint,at,1,1,CASE WHEN changed THEN 1 ELSE 0 END,op)
        ON CONFLICT(account_id) DO UPDATE SET sequence=EXCLUDED.sequence,last_checked_at=EXCLUDED.last_checked_at,
            observation_count=life_financial.shared_capital_heads.observation_count+CASE WHEN new_observation THEN 1 ELSE 0 END,
            operation_count=life_financial.shared_capital_heads.operation_count+1,
            claim_count=life_financial.shared_capital_heads.claim_count+CASE WHEN changed THEN 1 ELSE 0 END,operation_id=op;
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    result:=jsonb_build_object('kind','SHARED_CAPITAL','operation_id',op,'version',p_version+1,'decision',decision,'changed',changed,'replayed',false,'trading_permission',false);
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version) VALUES(p_account,op,p_fingerprint,request,result,p_version+1);
    RETURN result;
END; $$;

CREATE FUNCTION life_financial.shared_capital_attempt_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.shared_capital_policies;h life_financial.shared_capital_heads;c life_financial.shared_capital_claims;s jsonb;orders jsonb;decision jsonb;
    at_ms bigint;request jsonb;fees numeric;
BEGIN
    SELECT * INTO p FROM life_financial.shared_capital_policies WHERE account_id=NEW.account_id;
    IF NOT FOUND OR NEW.state NOT IN ('PREPARED','ARMED','SEND_UNKNOWN') THEN RETURN NEW; END IF;
    SELECT * INTO c FROM life_financial.shared_capital_claims WHERE account_id=NEW.account_id AND intent_id=NEW.intent_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_CAPITAL_CLAIM_REQUIRED' USING ERRCODE='P0001'; END IF;
    IF (c.wire_id,c.product,c.side,c.quantity_base,c.price_usdt,c.session_id,c.epoch)
        IS DISTINCT FROM (NEW.wire_id,NEW.product,NEW.side,NEW.quantity,NEW.price,NEW.session_id,NEW.epoch) THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_NATIVE_CLAIM_MISMATCH' USING ERRCODE='P0001'; END IF;
    request:=convert_from(NEW.payload,'UTF8')::jsonb;
    IF c.config_version IS DISTINCT FROM (request->>'config_version')::bigint
        OR c.risk_epoch IS DISTINCT FROM (request->>'risk_epoch')::bigint
        OR NOT EXISTS(SELECT 1 FROM life_financial.accounts a WHERE a.account_id=NEW.account_id AND a.risk_epoch=c.risk_epoch) THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_NATIVE_CLAIM_MISMATCH' USING ERRCODE='P0001'; END IF;
    IF NOT life_financial.shared_capital_verified(NEW.account_id) THEN RAISE EXCEPTION 'LIFE_CAPITAL_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT h FROM life_financial.shared_capital_heads WHERE account_id=NEW.account_id;
    SELECT body INTO STRICT s FROM life_financial.shared_capital_observations WHERE account_id=NEW.account_id AND sequence=h.sequence;
    at_ms:=floor(extract(epoch FROM clock_timestamp())*1000)::bigint;
    IF NOT life_financial.shared_snapshot_valid(s,p.body,at_ms,p.max_claims) THEN RAISE EXCEPTION 'LIFE_CAPITAL_SNAPSHOT_UNQUALIFIED' USING ERRCODE='P0001'; END IF;
    IF (s->>'spot_life_base')::numeric<>(SELECT total FROM life_financial.balances WHERE account_id=NEW.account_id AND asset='LIFE')
        OR (s->>'spot_cash_quote')::numeric<>(SELECT total FROM life_financial.balances WHERE account_id=NEW.account_id AND asset='USDT') THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_SPOT_BALANCE_MISMATCH' USING ERRCODE='P0001'; END IF;
    SELECT COALESCE(jsonb_agg(life_financial.shared_claim_body(x)),'[]'::jsonb) INTO orders FROM life_financial.shared_capital_claims x WHERE account_id=NEW.account_id;
    decision:=life_financial.shared_evaluate(p.body,s,orders);
    IF decision->'allowed' IS DISTINCT FROM 'true'::jsonb THEN RAISE EXCEPTION 'LIFE_CAPITAL_RISK_DENIED' USING ERRCODE='P0001'; END IF;
    -- The model's fee allowance must cover the actual native SPOT buffers too.
    -- A lower configured rate cannot lend fee-reserved cash to another product.
    IF NEW.product='SPOT' THEN
        request:=convert_from(NEW.payload,'UTF8')::jsonb;
        fees:=(request->>'fee_buffer_quote_usdt')::numeric+(request->>'fee_buffer_base_life')::numeric*NEW.price;
        IF fees>NEW.quantity*NEW.price*(p.body->>'fee_rate')::numeric THEN
            RAISE EXCEPTION 'LIFE_CAPITAL_FEE_BUFFER_UNCOVERED' USING ERRCODE='P0001'; END IF;
    END IF;
    RETURN NEW;
END; $$;
CREATE TRIGGER shared_capital_before_attempt BEFORE INSERT OR UPDATE OF state ON life_financial.send_attempts
    FOR EACH ROW EXECUTE FUNCTION life_financial.shared_capital_attempt_guard();

CREATE FUNCTION life_financial.shared_capital_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE op life_financial.operations;p life_financial.shared_capital_policies;h life_financial.shared_capital_heads;s jsonb;at_ms bigint;
BEGIN
    IF NOT life_financial.shared_capital_verified(NEW.account_id) THEN RAISE EXCEPTION 'LIFE_CAPITAL_UNVERIFIED_AT_COMMIT' USING ERRCODE='P0001'; END IF;
    SELECT * INTO p FROM life_financial.shared_capital_policies WHERE account_id=NEW.account_id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    SELECT * INTO op FROM life_financial.operations WHERE account_id=NEW.account_id AND committed_version=NEW.version;
    IF op.command->>'kind'='SHARED_CAPITAL' OR op.receipt->>'kind' IN ('SPOT_PREPARE','SPOT_ARM','SPOT_QUOTE_ARM') THEN
        SELECT * INTO STRICT h FROM life_financial.shared_capital_heads WHERE account_id=NEW.account_id;
        SELECT body INTO STRICT s FROM life_financial.shared_capital_observations WHERE account_id=NEW.account_id AND sequence=h.sequence;
        at_ms:=floor(extract(epoch FROM clock_timestamp())*1000)::bigint;
        IF NOT life_financial.shared_snapshot_valid(s,p.body,at_ms,p.max_claims)
            OR clock_timestamp()<h.last_checked_at THEN RAISE EXCEPTION 'LIFE_CAPITAL_FACTS_STALE_AT_COMMIT' USING ERRCODE='P0001'; END IF;
    END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER shared_capital_account_commit AFTER UPDATE ON life_financial.accounts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.version<>OLD.version)
    EXECUTE FUNCTION life_financial.shared_capital_commit_guard();

CREATE FUNCTION life_financial.shared_capital_head_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
    IF NEW.account_id<>OLD.account_id OR NEW.sequence<OLD.sequence OR NEW.last_checked_at<OLD.last_checked_at
        OR NEW.operation_count<>OLD.operation_count+1 OR NEW.observation_count<OLD.observation_count
        OR NEW.observation_count>OLD.observation_count+1 OR NEW.claim_count<OLD.claim_count OR NEW.claim_count>OLD.claim_count+1 THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_PROGRESS_REGRESSION' USING ERRCODE='P0001'; END IF;
    RETURN NEW;
END; $$;
CREATE TRIGGER monotone_progress BEFORE UPDATE ON life_financial.shared_capital_heads
    FOR EACH ROW EXECUTE FUNCTION life_financial.shared_capital_head_guard();
DO $$ DECLARE name text; BEGIN
    FOREACH name IN ARRAY ARRAY['shared_capital_policies','shared_capital_observations','shared_capital_claims','shared_capital_heads'] LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',name);
        EXECUTE format('CREATE POLICY scoped_read ON life_financial.%I FOR SELECT USING(life_financial.can_access(account_id))',name);
        IF name='shared_capital_heads' THEN
            EXECUTE format('CREATE TRIGGER retained_history BEFORE DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',name);
        ELSE
            EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',name);
        END IF;
    END LOOP;
END; $$;
REVOKE ALL ON life_financial.shared_capital_policies,life_financial.shared_capital_observations,life_financial.shared_capital_claims,life_financial.shared_capital_heads FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;

-- Preserve the snapshot OID/ACL and all prior accounting/ingress guards.
CREATE OR REPLACE FUNCTION life_financial.spot_state(p_account uuid) RETURNS jsonb
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

-- Preserve all native SPOT holds, economics, slots and receipt logic.
-- An imported shared model additionally requires an explicit risk epoch.
CREATE OR REPLACE FUNCTION life_financial.spot_prepare_action_core(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,p_command jsonb,p_context jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE previous life_financial.operations; a life_financial.accounts; s life_financial.sessions; p life_financial.spot_policies;
    view jsonb; request jsonb; result jsonb; op life_financial.identity; intent life_financial.identity; wire life_financial.identity;
    quantity life_financial.positive_amount; price life_financial.positive_amount; reference life_financial.positive_amount;
    fee_base life_financial.amount; fee_quote life_financial.amount; subsidy life_financial.amount;
    base_hold life_financial.amount; quote_hold life_financial.amount; buy_base numeric; sell_base numeric; gross numeric;
    scope_name text; key text; subsidy_limit numeric; loss_limit numeric; bucket life_financial.budget_buckets; at timestamptz;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR p_fingerprint IS NULL OR p_cut IS NULL OR p_command IS NULL
        OR jsonb_typeof(p_command)<>'object' OR octet_length(p_command::text)>16384 THEN
        RAISE EXCEPTION 'LIFE_SPOT_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    IF (SELECT count(*) FROM jsonb_object_keys(p_command))<>
            (CASE WHEN EXISTS(SELECT 1 FROM life_financial.shared_capital_policies WHERE account_id=p_account) THEN 16 ELSE 15 END)
        OR EXISTS(SELECT 1 FROM life_financial.shared_capital_policies WHERE account_id=p_account) AND (NOT p_command ? 'risk_epoch'
            OR jsonb_typeof(p_command->'risk_epoch') IS DISTINCT FROM 'number' OR p_command->>'risk_epoch' !~ '^[1-9][0-9]*$')
        OR NOT p_command ?& ARRAY[
        'operation_id','intent_id','client_order_id','market','side','quantity_base','limit_price_usdt','reference_price_usdt',
        'subsidy_quote_usdt','session_id','epoch','config_version','quote_level','fee_buffer_quote_usdt','fee_buffer_base_life']
        OR EXISTS(SELECT 1 FROM jsonb_each(p_command) WHERE value='null'::jsonb) THEN
        RAISE EXCEPTION 'LIFE_SPOT_MODE_UNSUPPORTED' USING ERRCODE='P0001'; END IF;
    op := p_command->>'operation_id'; intent := p_command->>'intent_id'; wire := p_command->>'client_order_id';
    IF length(op)>96 OR wire !~ '^[A-Za-z0-9]{1,32}$' OR p_command->>'market'<>'LIFE-USDT' OR p_command->>'side' NOT IN ('BUY','SELL') THEN
        RAISE EXCEPTION 'LIFE_SPOT_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    -- Match the file port's current-owner/recovery gate even on exact replay.
    -- Historical receipt lookup remains available without a new-risk command.
    PERFORM life_financial.guard_account(p_account,p_binding,p_owner,a.version);
    request := jsonb_build_object('kind','SPOT_PREPARE','binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',p_command);
    IF p_context IS NOT NULL THEN request:=request||p_context; END IF;
    SELECT * INTO previous FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF previous.command<>request OR previous.fingerprint<>p_fingerprint THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001'; END IF;
        IF NOT EXISTS(SELECT 1 FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=intent AND state='PREPARED')
            OR (life_financial.spot_state(p_account)->>(CASE WHEN p_context IS NULL THEN 'recovery_required' ELSE 'financial_recovery_required' END))::boolean THEN
            RAISE EXCEPTION 'LIFE_SPOT_RECONCILE_BEFORE_RETRY' USING ERRCODE='P0001'; END IF;
        RETURN previous.receipt||'{"replayed":true}'::jsonb;
    END IF;
    PERFORM life_financial.guard_account(p_account,p_binding,p_owner,p_version);
    view := life_financial.spot_state(p_account);
    IF view->>'digest'<>p_cut THEN RAISE EXCEPTION 'LIFE_SPOT_CUT_CONFLICT' USING ERRCODE='P0001'; END IF;
    IF (view->>(CASE WHEN p_context IS NULL THEN 'recovery_required' ELSE 'financial_recovery_required' END))::boolean THEN RAISE EXCEPTION 'LIFE_SPOT_RECOVERY_REQUIRED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT s FROM life_financial.sessions WHERE account_id=p_account AND session_id=a.current_session;
    SELECT * INTO STRICT p FROM life_financial.spot_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
    IF (p_command->>'session_id')::uuid<>s.session_id OR (p_command->>'epoch')::bigint<>s.epoch
        OR (p_command->>'config_version')::bigint<>s.config_version OR (p_command->>'quote_level')::integer NOT BETWEEN 0 AND p.max_quote_levels-1 THEN
        RAISE EXCEPTION 'LIFE_SPOT_SESSION_OR_SLOT_INVALID' USING ERRCODE='P0001'; END IF;
    IF EXISTS(SELECT 1 FROM life_financial.send_attempts t JOIN life_financial.spot_preparations d USING(account_id,intent_id)
        WHERE t.account_id=p_account AND t.state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND')
        AND t.side=p_command->>'side' AND d.quote_level=(p_command->>'quote_level')::integer) THEN
        RAISE EXCEPTION 'LIFE_SPOT_SLOT_CONFLICT' USING ERRCODE='P0001'; END IF;
    quantity := (p_command->>'quantity_base')::numeric; price := (p_command->>'limit_price_usdt')::numeric;
    reference := (p_command->>'reference_price_usdt')::numeric; fee_base := (p_command->>'fee_buffer_base_life')::numeric;
    fee_quote := (p_command->>'fee_buffer_quote_usdt')::numeric; subsidy := (p_command->>'subsidy_quote_usdt')::numeric;
    base_hold := CASE WHEN p_command->>'side'='SELL' THEN quantity ELSE 0 END;
    quote_hold := CASE WHEN p_command->>'side'='BUY' THEN quantity*price ELSE 0 END;
    IF (view#>>'{balances,LIFE,total}')::numeric-(view#>>'{balances,LIFE,reserved}')::numeric-(view#>>'{balances,LIFE,liability}')::numeric-base_hold-fee_base<p.min_inventory_base
        OR (view#>>'{balances,USDT,total}')::numeric-(view#>>'{balances,USDT,reserved}')::numeric-(view#>>'{balances,USDT,liability}')::numeric<quote_hold+fee_quote THEN
        RAISE EXCEPTION 'LIFE_SPOT_CAPITAL_INCLUDING_FEES_DENIED' USING ERRCODE='P0001'; END IF;
    SELECT COALESCE(sum(t.quantity-COALESCE(g.filled_base,0)) FILTER(WHERE t.side='BUY'),0),
        COALESCE(sum(t.quantity-COALESCE(g.filled_base,0)) FILTER(WHERE t.side='SELL'),0),
        COALESCE(sum((t.quantity-COALESCE(g.filled_base,0))*t.price),0)
        INTO buy_base,sell_base,gross FROM life_financial.send_attempts t
        LEFT JOIN life_financial.spot_accounting_progress g USING(account_id,intent_id)
        WHERE t.account_id=p_account AND t.product='SPOT' AND t.state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND');
    buy_base := buy_base+CASE WHEN p_command->>'side'='BUY' THEN quantity ELSE 0 END;
    sell_base := sell_base+CASE WHEN p_command->>'side'='SELL' THEN quantity ELSE 0 END;
    IF (view#>>'{balances,LIFE,total}')::numeric+buy_base>p.max_inventory_base
        OR (view#>>'{balances,LIFE,total}')::numeric-sell_base<p.min_inventory_base
        OR greatest(abs((view#>>'{balances,LIFE,total}')::numeric+buy_base),abs((view#>>'{balances,LIFE,total}')::numeric-sell_base))>p.max_net_base
        OR (view#>>'{balances,LIFE,total}')::numeric*reference+gross+quantity*price>p.max_gross_quote THEN
        RAISE EXCEPTION 'LIFE_SPOT_EXPOSURE_DENIED' USING ERRCODE='P0001'; END IF;
    at := clock_timestamp();
    FOREACH scope_name IN ARRAY ARRAY['CAMPAIGN','DAY','SESSION'] LOOP
        key := CASE scope_name WHEN 'CAMPAIGN' THEN p.campaign_id WHEN 'DAY' THEN to_char(at AT TIME ZONE 'UTC','YYYY-MM-DD') ELSE s.session_id::text END;
        subsidy_limit := CASE scope_name WHEN 'CAMPAIGN' THEN p.subsidy_campaign WHEN 'DAY' THEN p.subsidy_day ELSE p.subsidy_session END;
        loss_limit := CASE scope_name WHEN 'CAMPAIGN' THEN p.loss_campaign WHEN 'DAY' THEN p.loss_day ELSE p.loss_session END;
        IF EXISTS(SELECT 1 FROM life_financial.budget_buckets WHERE account_id=p_account AND family='LOSS' AND scope=scope_name
            AND window_key=key AND (budget_limit<>loss_limit OR incurred+committed>=loss_limit)) THEN
            RAISE EXCEPTION 'LIFE_SPOT_LOSS_BUDGET_DENIED' USING ERRCODE='P0001'; END IF;
        INSERT INTO life_financial.budget_buckets VALUES(p_account,'SUBSIDY',scope_name,key,subsidy_limit,0,0,0) ON CONFLICT DO NOTHING;
        SELECT * INTO STRICT bucket FROM life_financial.budget_buckets WHERE account_id=p_account AND family='SUBSIDY' AND scope=scope_name AND window_key=key FOR UPDATE;
        IF bucket.budget_limit<>subsidy_limit OR bucket.committed+bucket.incurred+subsidy>subsidy_limit THEN
            RAISE EXCEPTION 'LIFE_SPOT_SUBSIDY_BUDGET_DENIED' USING ERRCODE='P0001'; END IF;
        UPDATE life_financial.budget_buckets SET committed=committed+subsidy,version=version+1
            WHERE account_id=p_account AND family='SUBSIDY' AND scope=scope_name AND window_key=key;
        INSERT INTO life_financial.budget_events VALUES(p_account,op||':subsidy:'||scope_name,'SUBSIDY',scope_name,key,'COMMIT',subsidy);
    END LOOP;
    -- Canonical storage intent; final transport byte equivalence is a later PG3 sender gate.
    INSERT INTO life_financial.send_attempts(account_id,intent_id,operation_id,wire_id,product,side,session_id,process_id,generation,epoch,payload,payload_hash,quantity,price,state)
        VALUES(p_account,intent,op,wire,'SPOT',p_command->>'side',s.session_id,a.owner_process,a.owner_generation,a.owner_epoch,
            convert_to(p_command::text,'UTF8'),encode(sha256(convert_to(p_command::text,'UTF8')),'hex'),quantity,price,'PREPARED');
    INSERT INTO life_financial.spot_preparations VALUES(p_account,op,intent,p_command,(p_command->>'quote_level')::integer,fee_base,fee_quote,subsidy,at);
    INSERT INTO life_financial.reservations(account_id,reservation_id,operation_id,intent_id,asset,amount,remaining,state)
        VALUES(p_account,op||':principal',op,intent,CASE WHEN base_hold>0 THEN 'LIFE' ELSE 'USDT' END,quantity*CASE WHEN base_hold>0 THEN 1 ELSE price END,
            quantity*CASE WHEN base_hold>0 THEN 1 ELSE price END,'HELD');
    IF fee_base>0 THEN INSERT INTO life_financial.reservations(account_id,reservation_id,operation_id,intent_id,asset,amount,remaining,state)
        VALUES(p_account,op||':fee:LIFE',op,intent,'LIFE',fee_base,fee_base,'HELD'); END IF;
    IF fee_quote>0 THEN INSERT INTO life_financial.reservations(account_id,reservation_id,operation_id,intent_id,asset,amount,remaining,state)
        VALUES(p_account,op||':fee:USDT',op,intent,'USDT',fee_quote,fee_quote,'HELD'); END IF;
    UPDATE life_financial.balances SET reserved=reserved+CASE asset WHEN 'LIFE' THEN base_hold+fee_base ELSE quote_hold+fee_quote END,version=version+1 WHERE account_id=p_account;
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    result := jsonb_build_object('kind','SPOT_PREPARE','operation_id',op,'intent_id',intent,'state','PREPARED','version',p_version+1,'replayed',false,'trading_permission',false);
    IF p_context IS NOT NULL THEN result:=result||jsonb_build_object('states',jsonb_build_array(jsonb_build_array(intent,'PREPARED'))); END IF;
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version)
        VALUES(p_account,op,p_fingerprint,request,result,p_version+1);
    RETURN result;
END; $$;
