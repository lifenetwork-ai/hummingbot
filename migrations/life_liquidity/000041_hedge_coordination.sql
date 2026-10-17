-- Native hedge coordination. Sources remain externally qualified; these
-- commands cannot authenticate an exchange response or grant transport.
CREATE FUNCTION life_financial.hedge_policy_valid(p jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE h jsonb:=p->'hedge';k text;n numeric;
BEGIN
    IF jsonb_typeof(p) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(p))<>5
        OR NOT p ?& ARRAY['hedge','initial_target_base','cumulative_cost_budget_quote','max_market_age_ms','urgent_policy']
        OR p->>'urgent_policy' IS DISTINCT FROM 'bounded_maker_and_pause'
        OR jsonb_typeof(h) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(h))<>10
        OR NOT h ?& ARRAY['life_swap_instrument','deadband_base','batch_base','max_child_base','max_unhedged_base',
            'max_unhedged_ms','max_retry_attempts','max_hedge_cost_quote','max_basis_bps','maker_fee_rate']
        OR h->>'life_swap_instrument' IS DISTINCT FROM 'LIFE-USDT-SWAP' THEN RETURN false;END IF;
    FOREACH k IN ARRAY ARRAY['initial_target_base','cumulative_cost_budget_quote'] LOOP
        IF jsonb_typeof(p->k) IS DISTINCT FROM 'string' THEN RETURN false;END IF;n:=(p->>k)::life_financial.amount;
    END LOOP;
    FOREACH k IN ARRAY ARRAY['deadband_base','batch_base','max_child_base','max_unhedged_base','max_hedge_cost_quote','max_basis_bps','maker_fee_rate'] LOOP
        IF jsonb_typeof(h->k) IS DISTINCT FROM 'string' THEN RETURN false;END IF;n:=(h->>k)::life_financial.amount;
    END LOOP;
    FOREACH k IN ARRAY ARRAY['max_unhedged_ms','max_retry_attempts'] LOOP
        IF jsonb_typeof(h->k) IS DISTINCT FROM 'number' OR h->>k !~ '^[1-9][0-9]*$' OR (h->>k)::numeric>2147483647 THEN RETURN false;END IF;
    END LOOP;
    RETURN jsonb_typeof(p->'max_market_age_ms')='number' AND p->>'max_market_age_ms' ~ '^[1-9][0-9]*$'
        AND (p->>'max_market_age_ms')::numeric<=2147483647 AND (h->>'batch_base')::numeric>0
        AND (h->>'batch_base')::numeric<=(h->>'max_child_base')::numeric AND (h->>'maker_fee_rate')::numeric<=1;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
CREATE TABLE life_financial.hedge_policies (
    account_id uuid PRIMARY KEY REFERENCES life_financial.swap_policies,policy_hash life_financial.digest NOT NULL,
    body jsonb NOT NULL CHECK(life_financial.hedge_policy_valid(body)),model_hash life_financial.digest NOT NULL,
    max_targets integer NOT NULL CHECK(max_targets BETWEEN 1 AND 4096),max_plans integer NOT NULL CHECK(max_plans BETWEEN 1 AND 4096),
    max_total_bytes integer NOT NULL CHECK(max_total_bytes BETWEEN 1 AND 16777216),
    FOREIGN KEY(account_id,policy_hash) REFERENCES life_financial.policies,
    CHECK(model_hash=encode(sha256(convert_to(life_financial.canonical_json_v1(body),'UTF8')),'hex'))
);
CREATE TABLE life_financial.hedge_targets (
    account_id uuid NOT NULL REFERENCES life_financial.hedge_policies,operation_id life_financial.identity NOT NULL,
    expected_version bigint NOT NULL CHECK(expected_version>=0),target_base life_financial.amount NOT NULL,
    received_at_ms bigint NOT NULL CHECK(received_at_ms>0),PRIMARY KEY(account_id,operation_id),UNIQUE(account_id,expected_version),
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE life_financial.hedge_plan_facts (
    account_id uuid NOT NULL REFERENCES life_financial.hedge_policies,operation_id life_financial.identity NOT NULL,
    start_version bigint NOT NULL CHECK(start_version>=0),received_at_ms bigint NOT NULL CHECK(received_at_ms>0),
    target_version bigint NOT NULL CHECK(target_version>=0),target_base life_financial.amount NOT NULL,
    previous_operation_id life_financial.identity,capital_operation_id life_financial.identity,carry_operation_id life_financial.identity,
    context jsonb NOT NULL,decision jsonb NOT NULL,progress jsonb NOT NULL,child jsonb,child_state text CHECK(child_state IN ('PREPARED','REFUSED')),
    PRIMARY KEY(account_id,operation_id),FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(account_id,previous_operation_id) REFERENCES life_financial.hedge_plan_facts,
    FOREIGN KEY(account_id,capital_operation_id) REFERENCES life_financial.operations,
    FOREIGN KEY(account_id,carry_operation_id) REFERENCES life_financial.operations,
    CHECK((child IS NULL)=(child_state IS NULL))
);
CREATE TABLE life_financial.hedge_settlements (
    account_id uuid NOT NULL,operation_id life_financial.identity NOT NULL,plan_operation_id life_financial.identity NOT NULL,
    start_version bigint NOT NULL CHECK(start_version>=0),received_at_ms bigint NOT NULL CHECK(received_at_ms>0),
    source jsonb NOT NULL,filled_base life_financial.amount NOT NULL,actual_cost_quote life_financial.amount NOT NULL,
    terminal boolean NOT NULL,cost_breach boolean NOT NULL,
    PRIMARY KEY(account_id,operation_id),FOREIGN KEY(account_id,plan_operation_id) REFERENCES life_financial.hedge_plan_facts,
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE life_financial.hedge_staging (
    account_id uuid PRIMARY KEY REFERENCES life_financial.accounts,transaction_id xid8 NOT NULL,
    verified_version bigint NOT NULL CHECK(verified_version>=0),child jsonb NOT NULL
);
DO $$ DECLARE t text;BEGIN
    FOREACH t IN ARRAY ARRAY['hedge_policies','hedge_targets','hedge_plan_facts','hedge_settlements'] LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY scoped_read ON life_financial.%I FOR SELECT USING(life_financial.can_access(account_id))',t);
        EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',t);
    END LOOP;
END; $$;
CREATE FUNCTION life_financial.hedge_context(p_account uuid,p_version bigint) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT jsonb_build_object('money',life_financial.portfolio_money_at(p_account,p_version),
        'positions',life_financial.portfolio_positions_at(p_account,p_version),
        'owned_spot',COALESCE((SELECT jsonb_agg(life_financial.shared_claim_body(c) ORDER BY c.intent_id)
            FROM life_financial.shared_capital_claims c JOIN life_financial.send_attempts t USING(account_id,intent_id)
            JOIN life_financial.operations origin ON origin.account_id=t.account_id AND origin.operation_id=t.operation_id
            WHERE c.account_id=p_account AND t.product='SPOT' AND c.wire_id=t.wire_id AND origin.committed_version<=p_version
                AND life_financial.shared_claim_active_at(p_account,c.intent_id,p_version)
                AND EXISTS(SELECT 1 FROM life_financial.operations armed WHERE armed.account_id=p_account AND armed.committed_version<=p_version
                    AND armed.receipt->>'intent_id'=t.intent_id AND armed.receipt->>'state' IN ('SEND_UNKNOWN','ACKED'))
                AND NOT EXISTS(SELECT 1 FROM life_financial.operations done WHERE done.account_id=p_account AND done.committed_version<=p_version
                    AND done.receipt->>'intent_id'=t.intent_id AND done.receipt->>'state' IN ('TERMINAL','ABORTED_BEFORE_SEND'))),'[]'::jsonb),
        'pending',COALESCE((SELECT jsonb_agg(t.intent_id ORDER BY t.intent_id) FROM life_financial.send_attempts t
            JOIN life_financial.operations origin ON origin.account_id=t.account_id AND origin.operation_id=t.operation_id
            WHERE t.account_id=p_account AND origin.committed_version<=p_version AND NOT EXISTS(
                SELECT 1 FROM life_financial.operations done WHERE done.account_id=p_account AND done.committed_version<=p_version
                    AND done.receipt->>'state' IN ('TERMINAL','ABORTED_BEFORE_SEND') AND done.receipt->>'intent_id'=t.intent_id)),'[]'::jsonb),
        'hedges_pending',EXISTS(SELECT 1 FROM life_financial.hedge_plan_facts h JOIN life_financial.operations o USING(account_id,operation_id)
            WHERE h.account_id=p_account AND h.child_state='PREPARED' AND o.committed_version<=p_version
                AND NOT EXISTS(SELECT 1 FROM life_financial.hedge_settlements s JOIN life_financial.operations done USING(account_id,operation_id)
                    WHERE s.account_id=p_account AND s.plan_operation_id=h.operation_id AND s.terminal AND done.committed_version<=p_version)),
        'cost_fault',EXISTS(SELECT 1 FROM life_financial.hedge_settlements s JOIN life_financial.operations o USING(account_id,operation_id)
            WHERE s.account_id=p_account AND s.cost_breach AND o.committed_version<=p_version));
$$;
-- Bounded deterministic arithmetic, independently recomputed by SQL. No caller
-- estimated cost or pending position is accepted as completed hedge inventory.
CREATE FUNCTION life_financial.hedge_evaluate(p jsonb,cp jsonb,snapshot jsonb,carry jsonb,market jsonb,context jsonb,
    previous jsonb,target numeric,at_ms bigint,source_ready boolean) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE h jsonb:=p->'hedge';residual numeric;since_ms bigint;urgent boolean;attempts integer;hold numeric;result jsonb;state jsonb;
    levels jsonb;level jsonb;last_price numeric;price numeric;qty numeric;remaining numeric;total numeric:=0;executable numeric;
    amount numeric;contracts numeric;lot numeric;ct numeric;mark numeric;idx numeric;basis numeric;cost numeric;fee numeric;funding numeric;
    side text;action text;reason text;accepted boolean:=false;old_market jsonb:=previous->'market';close_base numeric;
BEGIN
    residual:=(context#>>'{money,base}')::numeric+(context#>>'{positions,long_base}')::numeric-(context#>>'{positions,short_base}')::numeric-target;
    since_ms:=(previous->>'since_ms')::bigint;attempts:=COALESCE((previous->>'attempts')::integer,0);hold:=COALESCE((previous->>'cost_hold')::numeric,0);
    IF residual=0 THEN since_ms:=NULL;IF NOT (context->>'hedges_pending')::boolean THEN attempts:=0;END IF;
    ELSIF since_ms IS NULL THEN since_ms:=at_ms;END IF;
    urgent:=residual<>0 AND (abs(residual)>(h->>'max_unhedged_base')::numeric OR at_ms-since_ms>=(h->>'max_unhedged_ms')::bigint);
    result:=jsonb_build_object('reason_code','HEDGE_EVIDENCE_UNAVAILABLE','side',NULL,'position_action',NULL,'quantity_base','0',
        'price_usdt','0','estimated_cost_quote','0','urgent',urgent,'allow_spot_risk_increase',false);
    state:=jsonb_build_object('since_ms',since_ms,'last_ms',at_ms,'attempts',attempts,'cost_hold',hold::text,'market',old_market);
    BEGIN
        IF NOT source_ready OR (context->>'cost_fault')::boolean THEN RAISE EXCEPTION 'HEDGE_CARRY_UNAVAILABLE';END IF;
        IF jsonb_typeof(market) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(market))<>11
            OR market->>'account_uid' IS DISTINCT FROM cp->>'account_uid' OR market->>'instrument' IS DISTINCT FROM 'LIFE-USDT-SWAP'
            OR market->>'source' IS DISTINCT FROM 'okx_depth_fee' OR market->'scope_complete' IS DISTINCT FROM 'true'::jsonb
            OR jsonb_typeof(market->'observed_at_ms') IS DISTINCT FROM 'number' OR market->>'observed_at_ms' !~ '^[1-9][0-9]*$'
            OR jsonb_typeof(market->'sequence') IS DISTINCT FROM 'number' OR market->>'sequence' !~ '^[1-9][0-9]*$'
            OR at_ms<(market->>'observed_at_ms')::bigint OR at_ms-(market->>'observed_at_ms')::bigint>(p->>'max_market_age_ms')::bigint
            OR jsonb_typeof(market->'fee_rate') IS DISTINCT FROM 'string' OR (market->>'fee_rate')::life_financial.amount>1
            OR jsonb_typeof(market->'funding_cost_quote') IS DISTINCT FROM 'string' OR (market->>'funding_cost_quote')::life_financial.amount<0
            OR jsonb_typeof(market->'available_edge_quote') IS DISTINCT FROM 'string' OR (market->>'available_edge_quote')::life_financial.amount<0 THEN
            RAISE EXCEPTION 'HEDGE_MARKET_UNQUALIFIED';END IF;
        FOREACH levels IN ARRAY ARRAY[market->'bids',market->'asks'] LOOP
            IF jsonb_typeof(levels) IS DISTINCT FROM 'array' OR jsonb_array_length(levels) NOT BETWEEN 1 AND 64 THEN RAISE EXCEPTION 'HEDGE_DEPTH_INVALID';END IF;
            last_price:=NULL;
            FOR level IN SELECT value FROM jsonb_array_elements(levels) LOOP
                IF jsonb_typeof(level) IS DISTINCT FROM 'array' OR jsonb_array_length(level)<>2
                    OR jsonb_typeof(level->0) IS DISTINCT FROM 'string' OR jsonb_typeof(level->1) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'HEDGE_DEPTH_INVALID';END IF;
                price:=(level->>0)::life_financial.positive_amount;qty:=(level->>1)::life_financial.positive_amount;
                IF last_price IS NOT NULL AND (levels=market->'bids' AND price>=last_price OR levels=market->'asks' AND price<=last_price) THEN RAISE EXCEPTION 'HEDGE_DEPTH_INVALID';END IF;
                last_price:=price;
            END LOOP;
        END LOOP;
        IF (market#>>'{bids,0,0}')::numeric>=(market#>>'{asks,0,0}')::numeric THEN RAISE EXCEPTION 'HEDGE_DEPTH_CROSSED';END IF;
        IF old_market IS NOT NULL AND old_market<>'null'::jsonb AND (
            (market->>'sequence')::bigint<(old_market->>'sequence')::bigint
            OR (market->>'sequence')::bigint=(old_market->>'sequence')::bigint AND market<>old_market
            OR (market->>'observed_at_ms')::bigint<(old_market->>'observed_at_ms')::bigint) THEN RAISE EXCEPTION 'HEDGE_MARKET_REPLAY_OR_MUTATION';END IF;
        accepted:=true;state:=state||jsonb_build_object('market',market);
        IF (context->>'hedges_pending')::boolean THEN RAISE EXCEPTION 'HEDGE_IN_FLIGHT';END IF;
        IF abs(residual)<=(h->>'deadband_base')::numeric AND NOT urgent THEN
            IF EXISTS(SELECT 1 FROM jsonb_array_elements(snapshot->'pending') pending WHERE NOT EXISTS(
                SELECT 1 FROM jsonb_array_elements(context->'owned_spot') owned WHERE life_financial.shared_claim_equal(pending,owned))) THEN
                RAISE EXCEPTION 'HEDGE_WAIT_SPOT_RECONCILIATION';END IF;
            result:=result||jsonb_build_object('reason_code','HEDGE_DEADBAND','allow_spot_risk_increase',true);
            RETURN jsonb_build_object('decision',result,'progress',state,'market_accepted',accepted);END IF;
        IF jsonb_array_length(context->'pending')>0 OR jsonb_array_length(snapshot->'pending')>0 THEN RAISE EXCEPTION 'HEDGE_WAIT_SPOT_RECONCILIATION';END IF;
        IF attempts>=(h->>'max_retry_attempts')::integer THEN RAISE EXCEPTION 'HEDGE_RETRY_CAP';END IF;
        side:=CASE WHEN residual>0 THEN 'SELL' ELSE 'BUY' END;
        close_base:=CASE side WHEN 'SELL' THEN (context#>>'{positions,long_base}')::numeric ELSE (context#>>'{positions,short_base}')::numeric END;
        action:=CASE WHEN close_base>0 THEN 'CLOSE' ELSE 'OPEN' END;
        amount:=least(abs(residual),(h->>'max_child_base')::numeric);IF close_base>0 THEN amount:=least(amount,close_base);END IF;
        ct:=(cp->>'contract_value_life')::numeric;lot:=(cp->>'lot_contracts')::numeric;
        contracts:=div(amount,ct*lot)*lot;amount:=contracts*ct;
        IF contracts<=0 OR amount<(h->>'batch_base')::numeric AND NOT urgent THEN RAISE EXCEPTION 'HEDGE_DUST_OR_BATCH_LIMIT';END IF;
        remaining:=amount;
        FOR level IN SELECT value FROM jsonb_array_elements(CASE side WHEN 'SELL' THEN market->'bids' ELSE market->'asks' END) LOOP
            qty:=least(remaining,(level->>1)::numeric);total:=life_financial.joint_decimal28(total+life_financial.joint_decimal28(qty*(level->>0)::numeric));
            remaining:=remaining-qty;EXIT WHEN remaining=0;
        END LOOP;
        IF remaining>0 THEN RAISE EXCEPTION 'HEDGE_DEPTH_INSUFFICIENT';END IF;
        executable:=life_financial.joint_decimal28(total,amount);mark:=(snapshot->>'mark_price_usdt')::numeric;idx:=(snapshot->>'index_price_usdt')::numeric;
        basis:=abs(mark-idx);
        IF life_financial.joint_decimal28(basis,idx)*10000>(h->>'max_basis_bps')::numeric THEN RAISE EXCEPTION 'HEDGE_BASIS_LIMIT';END IF;
        price:=CASE side WHEN 'SELL' THEN (market#>>'{asks,0,0}')::numeric ELSE (market#>>'{bids,0,0}')::numeric END;
        fee:=greatest((market->>'fee_rate')::numeric,(h->>'maker_fee_rate')::numeric,(carry->>'fee_rate')::numeric);
        funding:=greatest((market->>'funding_cost_quote')::numeric,life_financial.joint_decimal28(life_financial.joint_decimal28(amount*(carry->>'funding_cost_per_base')::numeric)
            *greatest(1,life_financial.joint_decimal28(price,(carry->>'funding_reference_price_usdt')::numeric))));
        cost:=life_financial.joint_decimal28(life_financial.joint_decimal28((greatest(0,CASE side WHEN 'SELL' THEN mark-executable ELSE executable-mark END)+basis)*amount)
            +life_financial.joint_decimal28(life_financial.joint_decimal28(greatest(price,executable,mark)*amount)*fee)+funding);
        IF cost>(h->>'max_hedge_cost_quote')::numeric OR cost>(market->>'available_edge_quote')::numeric THEN RAISE EXCEPTION 'HEDGE_COST_OR_NET_EDGE_LIMIT';END IF;
        IF hold+cost>(p->>'cumulative_cost_budget_quote')::numeric THEN RAISE EXCEPTION 'HEDGE_CAMPAIGN_COST_LIMIT';END IF;
        result:=result||jsonb_build_object('reason_code',CASE WHEN urgent THEN 'HEDGE_URGENT_BOUNDED_MAKER' ELSE 'HEDGE_ACTION_READY' END,
            'side',side,'position_action',action,'quantity_base',amount::text,'price_usdt',price::text,'estimated_cost_quote',cost::text);
    EXCEPTION WHEN raise_exception THEN
        GET STACKED DIAGNOSTICS reason=MESSAGE_TEXT;
        IF reason !~ '^HEDGE_[A-Z_]+$' THEN reason:='HEDGE_EVIDENCE_UNAVAILABLE';END IF;
        result:=result||jsonb_build_object('reason_code',reason);
    END;
    RETURN jsonb_build_object('decision',result,'progress',state,'market_accepted',accepted);
EXCEPTION WHEN OTHERS THEN
    RETURN jsonb_build_object('decision',result||jsonb_build_object('reason_code','HEDGE_EVIDENCE_UNAVAILABLE'),'progress',state,'market_accepted',accepted);
END; $$;

CREATE FUNCTION life_financial.hedge_source_qualified(p_account uuid,p_capital text,p_carry text,p_version bigint,at_ms bigint) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE cap life_financial.operations;c life_financial.carry_checks;co life_financial.operations;cp life_financial.carry_policies;
    sp jsonb;previous jsonb;snapshot jsonb;money jsonb;legs jsonb;
BEGIN
    SELECT * INTO STRICT cap FROM life_financial.operations WHERE account_id=p_account AND operation_id=p_capital AND committed_version<=p_version AND command->>'kind'='SHARED_CAPITAL';
    SELECT * INTO STRICT co FROM life_financial.operations WHERE account_id=p_account AND operation_id=p_carry AND committed_version<=p_version AND command->>'kind'='CARRY_CHECK';
    SELECT * INTO STRICT c FROM life_financial.carry_checks WHERE account_id=p_account AND operation_id=p_carry;
    SELECT * INTO STRICT cp FROM life_financial.carry_policies WHERE account_id=p_account;
    SELECT body INTO STRICT sp FROM life_financial.shared_capital_policies WHERE account_id=p_account;
    SELECT bundle INTO previous FROM life_financial.carry_checks WHERE account_id=p_account AND operation_id=c.previous_qualified;
    snapshot:=cap.command#>'{command,snapshot}';money:=life_financial.portfolio_money_at(p_account,p_version);legs:=life_financial.portfolio_positions_at(p_account,p_version);
    RETURN c.qualified AND co.receipt#>'{decision,allowed}'='true'::jsonb AND c.capital_operation_id=p_capital
        AND c.bundle#>'{account,snapshot}'=snapshot AND life_financial.shared_snapshot_valid(snapshot,sp,at_ms,4096)
        AND (snapshot->>'spot_life_base')::numeric=(money->>'base')::numeric AND (snapshot->>'spot_cash_quote')::numeric=(money->>'quote')::numeric
        AND (snapshot->>'long_base')::numeric=(legs->>'long_base')::numeric AND (snapshot->>'short_base')::numeric=(legs->>'short_base')::numeric
        AND life_financial.carry_bundle_reason(cp.body,sp,c.bundle,previous,snapshot,at_ms,cp.max_payments)='CARRY_QUALIFIED';
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
CREATE FUNCTION life_financial.hedge_settlement_source(p_account uuid,p_plan text,p_version bigint,at_ms bigint) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE r life_financial.hedge_plan_facts;cap text;carry text;snapshot jsonb;trades jsonb;fact jsonb;before_legs jsonb;legs jsonb;
    filled numeric:=0;cost numeric:=0;mark numeric;side text;terminal boolean;prepare_id text;intent text;qty numeric;fee numeric;price numeric;
BEGIN
    SELECT h.* INTO STRICT r FROM life_financial.hedge_plan_facts h JOIN life_financial.operations o USING(account_id,operation_id)
        WHERE h.account_id=p_account AND h.operation_id=p_plan AND h.child_state='PREPARED' AND o.committed_version<=p_version;
    SELECT operation_id INTO STRICT cap FROM life_financial.operations WHERE account_id=p_account AND command->>'kind'='SHARED_CAPITAL' AND committed_version<=p_version ORDER BY committed_version DESC LIMIT 1;
    SELECT operation_id INTO STRICT carry FROM life_financial.operations WHERE account_id=p_account AND command->>'kind'='CARRY_CHECK' AND committed_version<=p_version ORDER BY committed_version DESC LIMIT 1;
    IF NOT life_financial.hedge_source_qualified(p_account,cap,carry,p_version,at_ms) THEN RAISE EXCEPTION 'LIFE_HEDGE_SETTLEMENT_SOURCE_UNQUALIFIED' USING ERRCODE='P0001';END IF;
    intent:=r.child#>>'{prepare,config,id}';prepare_id:=r.child#>>'{prepare,operation_id}';
    IF NOT EXISTS(SELECT 1 FROM life_financial.swap_intent_events a JOIN life_financial.operations o USING(account_id,operation_id)
        WHERE a.account_id=p_account AND a.prepare_operation_id=prepare_id AND a.kind='SWAP_ACK' AND o.committed_version<=p_version) THEN
        RAISE EXCEPTION 'LIFE_HEDGE_SETTLEMENT_ACK_REQUIRED' USING ERRCODE='P0001';END IF;
    SELECT command#>'{command,snapshot}' INTO STRICT snapshot FROM life_financial.operations WHERE account_id=p_account AND operation_id=cap;
    SELECT command#>>'{command,snapshot,mark_price_usdt}' INTO STRICT mark FROM life_financial.operations WHERE account_id=p_account AND operation_id=r.capital_operation_id;
    side:=r.decision->>'side';
    SELECT COALESCE(jsonb_agg(jsonb_build_object('trade_id',f.trade_id,'observation',f.observation,'source_operation_id',f.source_operation_id) ORDER BY f.trade_id),'[]'::jsonb)
        INTO trades FROM life_financial.swap_fill_facts f JOIN life_financial.operations o ON o.account_id=f.account_id AND o.operation_id=f.source_operation_id
        WHERE f.account_id=p_account AND f.intent_id=intent AND o.committed_version<=p_version;
    FOR fact IN SELECT value->'observation' FROM jsonb_array_elements(trades) LOOP
        qty:=(fact->>'quantity_base_life')::numeric;price:=(fact->>'price_usdt')::numeric;fee:=(fact->>'signed_fee')::numeric;
        filled:=filled+qty;cost:=life_financial.joint_decimal28(cost+life_financial.joint_decimal28(greatest(0,CASE side WHEN 'SELL' THEN mark-price ELSE price-mark END)*qty)+greatest(0,-fee));
    END LOOP;
    IF filled>(r.decision->>'quantity_base')::numeric THEN RAISE EXCEPTION 'LIFE_HEDGE_SETTLEMENT_QUANTITY_INVALID' USING ERRCODE='P0001';END IF;
    before_legs:=r.context->'positions';legs:=life_financial.portfolio_positions_at(p_account,p_version);
    IF (legs->>'long_base')::numeric<>(before_legs->>'long_base')::numeric+(CASE WHEN side='BUY' AND r.decision->>'position_action'='OPEN' THEN filled WHEN side='SELL' AND r.decision->>'position_action'='CLOSE' THEN -filled ELSE 0 END)
        OR (legs->>'short_base')::numeric<>(before_legs->>'short_base')::numeric+(CASE WHEN side='SELL' AND r.decision->>'position_action'='OPEN' THEN filled WHEN side='BUY' AND r.decision->>'position_action'='CLOSE' THEN -filled ELSE 0 END) THEN
        RAISE EXCEPTION 'LIFE_HEDGE_SETTLEMENT_POSITION_MISMATCH' USING ERRCODE='P0001';END IF;
    terminal:=EXISTS(SELECT 1 FROM life_financial.swap_terminal_facts f JOIN life_financial.operations o USING(account_id,operation_id)
        WHERE f.account_id=p_account AND f.intent_id=intent AND f.state='TERMINAL' AND o.committed_version<=p_version);
    RETURN jsonb_build_object('capital_operation_id',cap,'carry_operation_id',carry,'trades',trades,'filled_base',filled::text,
        'actual_cost_quote',cost::text,'terminal',terminal,'cost_breach',cost>(r.decision->>'estimated_cost_quote')::numeric);
END; $$;

CREATE FUNCTION life_financial.hedge_retained_size(p_account uuid) RETURNS bigint
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT (SELECT COALESCE(sum(octet_length(to_jsonb(x)::text)),0) FROM life_financial.hedge_plan_facts x WHERE account_id=p_account)
        +(SELECT COALESCE(sum(octet_length(to_jsonb(x)::text)),0) FROM life_financial.hedge_targets x WHERE account_id=p_account)
        +(SELECT COALESCE(sum(octet_length(to_jsonb(x)::text)),0) FROM life_financial.hedge_settlements x WHERE account_id=p_account)
        +(SELECT COALESCE(sum(octet_length(command::text)+octet_length(receipt::text)),0) FROM life_financial.operations WHERE account_id=p_account AND command->>'kind' IN ('HEDGE_PLAN','HEDGE_TARGET','HEDGE_SETTLE'));
$$;
CREATE FUNCTION life_financial.hedge_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE p life_financial.hedge_policies;t life_financial.hedge_targets;r life_financial.hedge_plan_facts;o life_financial.operations;
    prior life_financial.hedge_plan_facts;source life_financial.operations;carry life_financial.operations;cp jsonb;snapshot jsonb;expected jsonb;
    version bigint:=0;target numeric;progress jsonb;child life_financial.operations;child_cap life_financial.operations;child_carry life_financial.operations;
    settlement life_financial.hedge_settlements;latest life_financial.hedge_settlements;
BEGIN
    -- The account lock and immutable prior facts make the verified prefix
    -- stable during these three fixed native child commands. Only this private
    -- coordinator can create the capability, after its full entry proof. It is
    -- deleted before root publication; final and COMMIT proofs never use it.
    IF EXISTS(SELECT 1 FROM life_financial.hedge_staging s JOIN life_financial.accounts a USING(account_id)
        WHERE s.account_id=p_account AND s.transaction_id=pg_current_xact_id_if_assigned()
            AND a.version BETWEEN s.verified_version AND s.verified_version+3
            AND NOT EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=p_account AND o.committed_version>s.verified_version
                AND NOT (o.operation_id=s.child->>'capital_operation_id' AND o.command->>'kind'='SHARED_CAPITAL'
                    OR o.operation_id=s.child->>'carry_operation_id' AND o.command->>'kind'='CARRY_CHECK'
                    OR o.operation_id=s.child#>>'{prepare,operation_id}' AND o.command->>'kind'='SWAP_PREPARE'))) THEN RETURN true;END IF;
    SELECT * INTO p FROM life_financial.hedge_policies WHERE account_id=p_account;
    IF NOT FOUND THEN RETURN NOT EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=p_account AND command->>'kind' IN ('HEDGE_PLAN','HEDGE_TARGET','HEDGE_SETTLE'));END IF;
    IF p.policy_hash IS DISTINCT FROM (SELECT policy_hash FROM life_financial.accounts WHERE account_id=p_account)
        OR NOT life_financial.hedge_policy_valid(p.body) OR p.model_hash<>encode(sha256(convert_to(life_financial.canonical_json_v1(p.body),'UTF8')),'hex')
        OR (SELECT count(*) FROM life_financial.hedge_plan_facts WHERE account_id=p_account)>p.max_plans
        OR (SELECT count(*) FROM life_financial.hedge_targets WHERE account_id=p_account)>p.max_targets
        OR life_financial.hedge_retained_size(p_account)>p.max_total_bytes THEN RETURN false;END IF;
    FOR t IN SELECT * FROM life_financial.hedge_targets WHERE account_id=p_account ORDER BY expected_version LOOP
        SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=t.operation_id;
        IF t.expected_version<>version OR o.command->>'kind'<>'HEDGE_TARGET' OR o.command->'command' IS DISTINCT FROM
            jsonb_build_object('operation_id',t.operation_id,'expected_version',t.expected_version,'target_base',t.target_base::text)
            OR o.receipt IS DISTINCT FROM jsonb_build_object('kind','HEDGE_TARGET','operation_id',t.operation_id,'state','TARGET_UPDATED',
                'target_version',t.expected_version+1,'version',o.committed_version,'replayed',false,'trading_permission',false)
            OR o.fingerprint<>encode(sha256(convert_to(life_financial.canonical_json_v1(o.command),'UTF8')),'hex') THEN RETURN false;END IF;
        version:=version+1;
    END LOOP;
    SELECT body INTO STRICT cp FROM life_financial.shared_capital_policies WHERE account_id=p_account;
    FOR r IN SELECT h.* FROM life_financial.hedge_plan_facts h JOIN life_financial.operations op USING(account_id,operation_id) WHERE h.account_id=p_account ORDER BY op.committed_version LOOP
        SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=r.operation_id;
        SELECT h.* INTO prior FROM life_financial.hedge_plan_facts h JOIN life_financial.operations op USING(account_id,operation_id)
            WHERE h.account_id=p_account AND op.committed_version<=r.start_version ORDER BY op.committed_version DESC LIMIT 1;
        SELECT count(*),COALESCE((array_agg(t.target_base ORDER BY t.expected_version DESC))[1],(p.body->>'initial_target_base')::numeric) INTO version,target
            FROM life_financial.hedge_targets t JOIN life_financial.operations op USING(account_id,operation_id) WHERE t.account_id=p_account AND op.committed_version<=r.start_version;
        SELECT * INTO source FROM life_financial.operations WHERE account_id=p_account AND operation_id=r.capital_operation_id;
        SELECT * INTO carry FROM life_financial.operations WHERE account_id=p_account AND operation_id=r.carry_operation_id;
        snapshot:=source.command#>'{command,snapshot}';
        expected:=life_financial.hedge_evaluate(p.body,cp,snapshot,carry.receipt->'decision',o.command#>'{command,market}',r.context,
            prior.progress,r.target_base,r.received_at_ms,(r.context->>'source_ready')::boolean);
        progress:=expected->'progress';
        IF r.child IS NOT NULL THEN progress:=progress||jsonb_build_object('attempts',(progress->>'attempts')::integer+1,
            'cost_hold',((progress->>'cost_hold')::numeric+(r.decision->>'estimated_cost_quote')::numeric)::text);END IF;
        IF o.command->>'kind'<>'HEDGE_PLAN' OR (o.command->>'version')::bigint<>r.start_version
            OR r.previous_operation_id IS DISTINCT FROM prior.operation_id OR r.target_version<>version OR r.target_base<>target
            OR r.child IS DISTINCT FROM nullif(o.command#>'{command,child}','null'::jsonb)
            OR (r.context-'source_ready') IS DISTINCT FROM life_financial.hedge_context(p_account,r.start_version)
            OR (r.context->>'source_ready')::boolean AND NOT life_financial.hedge_source_qualified(p_account,r.capital_operation_id,r.carry_operation_id,r.start_version,r.received_at_ms)
            OR r.decision IS DISTINCT FROM expected->'decision' OR r.progress IS DISTINCT FROM progress
            OR NOT EXISTS(SELECT 1 FROM life_financial.hedge_plans projection WHERE projection.account_id=p_account AND projection.plan_id=r.operation_id
                AND projection.target_base=r.target_base AND projection.gross_attempts=CASE WHEN r.child IS NULL THEN 0 ELSE 1 END
                AND projection.incurred_usdt=COALESCE((SELECT s.actual_cost_quote FROM life_financial.hedge_settlements s JOIN life_financial.operations so USING(account_id,operation_id) WHERE s.account_id=p_account AND s.plan_operation_id=r.operation_id ORDER BY so.committed_version DESC LIMIT 1),0)
                AND projection.state=CASE WHEN EXISTS(SELECT 1 FROM life_financial.hedge_settlements s WHERE s.account_id=p_account AND s.plan_operation_id=r.operation_id AND s.terminal) THEN 'TERMINAL' ELSE 'PLANNED' END
                AND projection.version=1+(SELECT count(*) FROM life_financial.hedge_settlements s WHERE s.account_id=p_account AND s.plan_operation_id=r.operation_id))
            OR o.fingerprint<>encode(sha256(convert_to(life_financial.canonical_json_v1(o.command),'UTF8')),'hex')
            OR o.receipt IS DISTINCT FROM jsonb_build_object('kind','HEDGE_PLAN','operation_id',r.operation_id,'state','PLANNED','decision',r.decision,
                'child_operation_id',CASE WHEN r.child_state='PREPARED' THEN r.child#>>'{prepare,operation_id}' END,'child_state',r.child_state,
                'version',o.committed_version,'replayed',false,'trading_permission',false) THEN RETURN false;END IF;
        IF r.child_state='PREPARED' THEN
            SELECT * INTO STRICT child FROM life_financial.operations WHERE account_id=p_account AND operation_id=r.child#>>'{prepare,operation_id}';
            SELECT * INTO STRICT child_cap FROM life_financial.operations WHERE account_id=p_account AND operation_id=r.child->>'capital_operation_id';
            SELECT * INTO STRICT child_carry FROM life_financial.operations WHERE account_id=p_account AND operation_id=r.child->>'carry_operation_id';
            IF child.command->>'kind'<>'SWAP_PREPARE' OR child.command->'command' IS DISTINCT FROM r.child->'prepare'
                OR child.command->'owner' IS DISTINCT FROM o.command->'owner' OR child.command->'binding' IS DISTINCT FROM o.command->'binding'
                OR child.committed_version>=o.committed_version OR child_cap.committed_version<=r.start_version
                OR child_cap.committed_version>=child_carry.committed_version OR child_carry.committed_version>=child.committed_version
                OR child_cap.command->>'kind'<>'SHARED_CAPITAL' OR child_cap.command#>>'{command,kind}'<>'RESERVE'
                OR child_cap.command#>'{command,claim}' IS DISTINCT FROM r.child->'claim'
                OR child_cap.command#>'{command,snapshot}' IS DISTINCT FROM snapshot
                OR child_carry.command->>'kind'<>'CARRY_CHECK' OR child_carry.command#>'{command,bundle}' IS DISTINCT FROM carry.command#>'{command,bundle}'
                OR child_carry.command#>'{command,prospective}' IS DISTINCT FROM 'null'::jsonb
                OR child_cap.command->'owner' IS DISTINCT FROM o.command->'owner' OR child_cap.command->'binding' IS DISTINCT FROM o.command->'binding'
                OR child_carry.command->'owner' IS DISTINCT FROM o.command->'owner' OR child_carry.command->'binding' IS DISTINCT FROM o.command->'binding'
                OR (r.child#>>'{prepare,config,amount}')::numeric IS DISTINCT FROM (r.decision->>'quantity_base')::numeric
                OR (r.child#>>'{prepare,config,price}')::numeric IS DISTINCT FROM (r.decision->>'price_usdt')::numeric
                OR (CASE r.child#>>'{prepare,config,side}' WHEN '1' THEN 'BUY' WHEN '2' THEN 'SELL' END) IS DISTINCT FROM r.decision->>'side'
                OR r.child#>>'{prepare,config,position_action}' IS DISTINCT FROM r.decision->>'position_action' THEN RETURN false;END IF;
        ELSIF r.child_state='REFUSED' AND (EXISTS(SELECT 1 FROM life_financial.operations used WHERE used.account_id=p_account
            AND used.operation_id IN (r.child->>'capital_operation_id',r.child->>'carry_operation_id',r.child#>>'{prepare,operation_id}'))
            OR EXISTS(SELECT 1 FROM life_financial.send_attempts used WHERE used.account_id=p_account
                AND (used.intent_id=r.child#>>'{prepare,config,id}' OR used.wire_id=r.child#>>'{prepare,client_order_id}'))) THEN RETURN false;
        END IF;
    END LOOP;
    FOR settlement IN SELECT s.* FROM life_financial.hedge_settlements s JOIN life_financial.operations so USING(account_id,operation_id) WHERE s.account_id=p_account ORDER BY so.committed_version LOOP
        SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=settlement.operation_id;
        expected:=life_financial.hedge_settlement_source(p_account,settlement.plan_operation_id,settlement.start_version,settlement.received_at_ms);
        SELECT s.* INTO latest FROM life_financial.hedge_settlements s JOIN life_financial.operations so USING(account_id,operation_id)
            WHERE s.account_id=p_account AND s.plan_operation_id=settlement.plan_operation_id AND so.committed_version<=settlement.start_version ORDER BY so.committed_version DESC LIMIT 1;
        IF settlement.source IS DISTINCT FROM expected OR settlement.filled_base<>(expected->>'filled_base')::numeric
            OR settlement.actual_cost_quote<>(expected->>'actual_cost_quote')::numeric OR settlement.terminal<>(expected->>'terminal')::boolean
            OR settlement.cost_breach<>(expected->>'cost_breach')::boolean OR settlement.filled_base<latest.filled_base
            OR latest.terminal OR o.command->>'kind'<>'HEDGE_SETTLE' OR (o.command->>'version')::bigint<>settlement.start_version
            OR o.command->'command' IS DISTINCT FROM jsonb_build_object('operation_id',settlement.operation_id,'plan_operation_id',settlement.plan_operation_id)
            OR o.fingerprint<>encode(sha256(convert_to(life_financial.canonical_json_v1(o.command),'UTF8')),'hex')
            OR o.receipt IS DISTINCT FROM jsonb_build_object('kind','HEDGE_SETTLE','operation_id',settlement.operation_id,'plan_operation_id',settlement.plan_operation_id,
                'state',CASE WHEN settlement.terminal THEN 'TERMINAL' ELSE 'PARTIAL' END,'version',o.committed_version,'replayed',false,'trading_permission',false) THEN RETURN false;END IF;
    END LOOP;
    RETURN NOT EXISTS(SELECT 1 FROM life_financial.hedge_plans projection WHERE projection.account_id=p_account
        AND NOT EXISTS(SELECT 1 FROM life_financial.hedge_plan_facts f WHERE f.account_id=p_account AND f.operation_id=projection.plan_id))
        AND NOT EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=p_account AND (
        o.command->>'kind'='HEDGE_TARGET' AND NOT EXISTS(SELECT 1 FROM life_financial.hedge_targets t WHERE t.account_id=p_account AND t.operation_id=o.operation_id)
        OR o.command->>'kind'='HEDGE_PLAN' AND NOT EXISTS(SELECT 1 FROM life_financial.hedge_plan_facts h WHERE h.account_id=p_account AND h.operation_id=o.operation_id)
        OR o.command->>'kind'='HEDGE_SETTLE' AND NOT EXISTS(SELECT 1 FROM life_financial.hedge_settlements h WHERE h.account_id=p_account AND h.operation_id=o.operation_id)));
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.hedge_command(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,p_cut life_financial.digest,
    p_fingerprint life_financial.digest,p_kind text,cmd jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE a life_financial.accounts;p life_financial.hedge_policies;prior life_financial.hedge_plan_facts;cap life_financial.operations;carry life_financial.operations;
    old life_financial.operations;request jsonb;view jsonb;result jsonb;context jsonb;evaluation jsonb;decision jsonb;progress jsonb;snapshot jsonb;cp jsonb;
    version bigint;target numeric;at_ms bigint;v bigint;op text;child jsonb;cfg jsonb;claim jsonb;sub jsonb;receipt jsonb;child_state text;reason text;ready boolean;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR p_kind IS NULL OR p_kind NOT IN ('HEDGE_PLAN','HEDGE_TARGET','HEDGE_SETTLE') OR jsonb_typeof(cmd) IS DISTINCT FROM 'object'
        OR (SELECT count(*) FROM jsonb_object_keys(cmd))<>(CASE WHEN p_kind='HEDGE_SETTLE' THEN 2 ELSE 3 END) OR jsonb_typeof(cmd->'operation_id') IS DISTINCT FROM 'string'
        OR cmd->>'operation_id' !~ '^[A-Za-z0-9_.:-]{1,96}$' OR octet_length(cmd::text)>1048576 THEN RAISE EXCEPTION 'LIFE_HEDGE_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
    op:=cmd->>'operation_id';request:=jsonb_build_object('kind',p_kind,'binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',cmd);
    IF p_fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(request),'UTF8')),'hex') THEN RAISE EXCEPTION 'LIFE_HEDGE_FINGERPRINT_INVALID' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,a.version);
    IF NOT life_financial.hedge_verified(p_account) THEN RAISE EXCEPTION 'LIFE_HEDGE_UNVERIFIED' USING ERRCODE='P0001';END IF;
    SELECT * INTO old FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN IF old.command<>request OR old.fingerprint<>p_fingerprint THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001';END IF;
        RETURN old.receipt||'{"replayed":true}'::jsonb;END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    view:=life_financial.spot_state(p_account);
    IF view->>'digest' IS DISTINCT FROM p_cut OR (view->>'unverified')::boolean THEN RAISE EXCEPTION 'LIFE_HEDGE_CUT_STALE' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT p FROM life_financial.hedge_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
    SELECT count(*),COALESCE((array_agg(t.target_base ORDER BY t.expected_version DESC))[1],(p.body->>'initial_target_base')::numeric) INTO version,target FROM life_financial.hedge_targets t WHERE account_id=p_account;
    SELECT h.* INTO prior FROM life_financial.hedge_plan_facts h JOIN life_financial.operations o USING(account_id,operation_id) WHERE h.account_id=p_account ORDER BY o.committed_version DESC LIMIT 1;
    at_ms:=floor(extract(epoch FROM clock_timestamp())*1000)::bigint;
    IF at_ms<prior.received_at_ms THEN RAISE EXCEPTION 'LIFE_HEDGE_CLOCK_ROLLBACK' USING ERRCODE='P0001';END IF;
    context:=life_financial.hedge_context(p_account,p_version);
    IF p_kind='HEDGE_TARGET' THEN
        PERFORM life_financial.guard_account(p_account,p_binding,p_owner,p_version);PERFORM life_financial.guard_spot_session(p_account);
        IF NOT cmd ?& ARRAY['expected_version','target_base'] OR jsonb_typeof(cmd->'expected_version') IS DISTINCT FROM 'number'
            OR cmd->>'expected_version' !~ '^[0-9]+$' OR jsonb_typeof(cmd->'target_base') IS DISTINCT FROM 'string'
            OR (cmd->>'target_base')::life_financial.amount<0 THEN RAISE EXCEPTION 'LIFE_HEDGE_TARGET_INVALID' USING ERRCODE='P0001';END IF;
        IF (cmd->>'expected_version')::bigint<>version THEN RAISE EXCEPTION 'LIFE_HEDGE_TARGET_VERSION' USING ERRCODE='P0001';END IF;
        IF (context->>'hedges_pending')::boolean OR (context->>'cost_fault')::boolean THEN RAISE EXCEPTION 'LIFE_HEDGE_TARGET_PENDING' USING ERRCODE='P0001';END IF;
        IF version>=p.max_targets THEN RAISE EXCEPTION 'LIFE_HEDGE_BOUND' USING ERRCODE='P0001';END IF;
        INSERT INTO life_financial.hedge_targets VALUES(p_account,op,version,(cmd->>'target_base')::numeric,at_ms);
        UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account RETURNING accounts.version INTO v;
        result:=jsonb_build_object('kind',p_kind,'operation_id',op,'state','TARGET_UPDATED','target_version',version+1,'version',v,'replayed',false,'trading_permission',false);
    ELSIF p_kind='HEDGE_SETTLE' THEN
        IF NOT cmd ? 'plan_operation_id' OR cmd->>'plan_operation_id'=op THEN RAISE EXCEPTION 'LIFE_HEDGE_SETTLEMENT_INVALID' USING ERRCODE='P0001';END IF;
        IF (SELECT count(*) FROM life_financial.hedge_settlements WHERE account_id=p_account)>=p.max_plans
            OR EXISTS(SELECT 1 FROM life_financial.hedge_settlements WHERE account_id=p_account AND plan_operation_id=cmd->>'plan_operation_id' AND terminal) THEN
            RAISE EXCEPTION 'LIFE_HEDGE_SETTLEMENT_CLOSED_OR_FULL' USING ERRCODE='P0001';END IF;
        sub:=life_financial.hedge_settlement_source(p_account,cmd->>'plan_operation_id',p_version,at_ms);
        INSERT INTO life_financial.hedge_settlements VALUES(p_account,op,cmd->>'plan_operation_id',p_version,at_ms,sub,
            (sub->>'filled_base')::numeric,(sub->>'actual_cost_quote')::numeric,(sub->>'terminal')::boolean,(sub->>'cost_breach')::boolean);
        UPDATE life_financial.hedge_plans SET incurred_usdt=(sub->>'actual_cost_quote')::numeric,
            state=CASE WHEN (sub->>'terminal')::boolean THEN 'TERMINAL' ELSE 'PLANNED' END,version=hedge_plans.version+1
            WHERE account_id=p_account AND plan_id=cmd->>'plan_operation_id';
        IF (sub->>'cost_breach')::boolean THEN UPDATE life_financial.accounts SET halted=true WHERE account_id=p_account;END IF;
        UPDATE life_financial.accounts SET version=accounts.version+1 WHERE account_id=p_account RETURNING accounts.version INTO v;
        result:=jsonb_build_object('kind',p_kind,'operation_id',op,'plan_operation_id',cmd->>'plan_operation_id',
            'state',CASE WHEN (sub->>'terminal')::boolean THEN 'TERMINAL' ELSE 'PARTIAL' END,'version',v,'replayed',false,'trading_permission',false);
    ELSE
        IF NOT cmd ?& ARRAY['market','child'] THEN RAISE EXCEPTION 'LIFE_HEDGE_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
        IF (SELECT count(*) FROM life_financial.hedge_plan_facts WHERE account_id=p_account)>=p.max_plans THEN RAISE EXCEPTION 'LIFE_HEDGE_BOUND' USING ERRCODE='P0001';END IF;
        SELECT o.* INTO cap FROM life_financial.shared_capital_heads h JOIN life_financial.operations o USING(account_id,operation_id) WHERE h.account_id=p_account;
        SELECT o.* INTO carry FROM life_financial.carry_heads h JOIN life_financial.operations o USING(account_id,operation_id) WHERE h.account_id=p_account;
        SELECT body INTO STRICT cp FROM life_financial.shared_capital_policies WHERE account_id=p_account;
        snapshot:=cap.command#>'{command,snapshot}';
        ready:=a.mode='ACTIVE' AND NOT a.halted AND NOT (view->>'recovery_required')::boolean AND life_financial.carry_current_ready(p_account)
            AND life_financial.swap_snapshot_current(p_account,snapshot);
        context:=context||jsonb_build_object('source_ready',ready);
        evaluation:=life_financial.hedge_evaluate(p.body,cp,snapshot,carry.receipt->'decision',cmd->'market',context,prior.progress,target,at_ms,ready);
        decision:=evaluation->'decision';progress:=evaluation->'progress';child:=nullif(cmd->'child','null'::jsonb);
        IF child IS NOT NULL THEN
            IF decision->>'side' IS NULL THEN RAISE EXCEPTION 'LIFE_HEDGE_CHILD_NOT_READY' USING ERRCODE='P0001';END IF;
            PERFORM life_financial.guard_account(p_account,p_binding,p_owner,p_version);PERFORM life_financial.guard_spot_session(p_account);
            cfg:=child#>'{prepare,config}';claim:=child->'claim';
            IF jsonb_typeof(child) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(child))<>4
                OR NOT child ?& ARRAY['capital_operation_id','carry_operation_id','prepare','claim']
                OR EXISTS(SELECT 1 FROM (SELECT op id UNION ALL SELECT child->>'capital_operation_id' UNION ALL SELECT child->>'carry_operation_id'
                    UNION ALL SELECT child#>>'{prepare,operation_id}') x GROUP BY id HAVING count(*)>1 OR id IS NULL)
                OR (cfg->>'amount')::numeric IS DISTINCT FROM (decision->>'quantity_base')::numeric
                OR (cfg->>'price')::numeric IS DISTINCT FROM (decision->>'price_usdt')::numeric
                OR (CASE cfg->>'side' WHEN '1' THEN 'BUY' WHEN '2' THEN 'SELL' END) IS DISTINCT FROM decision->>'side'
                OR cfg->>'position_action' IS DISTINCT FROM decision->>'position_action'
                OR claim->>'intent_id' IS DISTINCT FROM cfg->>'id' OR claim->>'wire_id' IS DISTINCT FROM child#>>'{prepare,client_order_id}'
                OR claim->>'product' IS DISTINCT FROM 'SWAP' OR claim->>'side' IS DISTINCT FROM decision->>'side'
                OR claim->>'position_action' IS DISTINCT FROM decision->>'position_action'
                OR (claim->>'quantity_base')::numeric IS DISTINCT FROM (decision->>'quantity_base')::numeric
                OR (claim->>'price_usdt')::numeric IS DISTINCT FROM (decision->>'price_usdt')::numeric THEN RAISE EXCEPTION 'LIFE_HEDGE_CHILD_MISMATCH' USING ERRCODE='P0001';END IF;
            IF EXISTS(SELECT 1 FROM life_financial.hedge_plan_facts h WHERE h.account_id=p_account AND h.child IS NOT NULL AND (
                h.child->>'capital_operation_id'=ANY(ARRAY[op,(cmd->'child')->>'capital_operation_id',(cmd->'child')->>'carry_operation_id',(cmd->'child')#>>'{prepare,operation_id}'])
                OR h.child->>'carry_operation_id'=ANY(ARRAY[op,(cmd->'child')->>'capital_operation_id',(cmd->'child')->>'carry_operation_id',(cmd->'child')#>>'{prepare,operation_id}'])
                OR h.child#>>'{prepare,operation_id}'=ANY(ARRAY[op,(cmd->'child')->>'capital_operation_id',(cmd->'child')->>'carry_operation_id',(cmd->'child')#>>'{prepare,operation_id}'])
                OR h.child#>>'{prepare,config,id}'=cfg->>'id' OR h.child#>>'{prepare,client_order_id}'=(cmd->'child')#>>'{prepare,client_order_id}')) THEN
                RAISE EXCEPTION 'LIFE_HEDGE_CHILD_ID_RETIRED' USING ERRCODE='P0001';END IF;
            IF EXISTS(SELECT 1 FROM life_financial.operations used WHERE used.account_id=p_account
                AND used.operation_id IN (child->>'capital_operation_id',(cmd->'child')->>'carry_operation_id',(cmd->'child')#>>'{prepare,operation_id}'))
                OR EXISTS(SELECT 1 FROM life_financial.send_attempts used WHERE used.account_id=p_account
                    AND (used.intent_id=cfg->>'id' OR used.wire_id=(cmd->'child')#>>'{prepare,client_order_id}')) THEN
                RAISE EXCEPTION 'LIFE_HEDGE_CHILD_ID_RETIRED' USING ERRCODE='P0001';END IF;
            progress:=progress||jsonb_build_object('attempts',(progress->>'attempts')::integer+1,
                'cost_hold',((progress->>'cost_hold')::numeric+(decision->>'estimated_cost_quote')::numeric)::text);
            BEGIN
                INSERT INTO life_financial.hedge_staging VALUES(p_account,pg_current_xact_id(),p_version,child);
                sub:=jsonb_build_object('kind','RESERVE','operation_id',child->>'capital_operation_id','snapshot',snapshot,'claim',claim);
                request:=jsonb_build_object('kind','SHARED_CAPITAL','binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',sub);
                receipt:=life_financial.shared_capital_command(p_account,p_binding,p_owner,p_version,p_cut,encode(sha256(convert_to(life_financial.canonical_json_v1(request),'UTF8')),'hex'),sub);
                IF receipt#>'{decision,allowed}' IS DISTINCT FROM 'true'::jsonb THEN RAISE EXCEPTION 'LIFE_HEDGE_CHILD_CAPITAL_REFUSED';END IF;
                SELECT accounts.version INTO v FROM life_financial.accounts WHERE account_id=p_account;view:=life_financial.spot_state(p_account);
                sub:=jsonb_build_object('operation_id',child->>'carry_operation_id','bundle',(SELECT bundle FROM life_financial.carry_checks WHERE account_id=p_account AND operation_id=carry.operation_id),'prospective',NULL);
                request:=jsonb_build_object('kind','CARRY_CHECK','binding',p_binding,'owner',p_owner,'version',v,'cut',view->>'digest','command',sub);
                receipt:=life_financial.carry_check(p_account,p_binding,p_owner,v,(view->>'digest')::life_financial.digest,encode(sha256(convert_to(life_financial.canonical_json_v1(request),'UTF8')),'hex'),sub);
                IF receipt#>'{decision,allowed}' IS DISTINCT FROM 'true'::jsonb THEN RAISE EXCEPTION 'LIFE_HEDGE_CHILD_CARRY_REFUSED';END IF;
                SELECT accounts.version INTO v FROM life_financial.accounts WHERE account_id=p_account;view:=life_financial.spot_state(p_account);
                request:=jsonb_build_object('kind','SWAP_PREPARE','binding',p_binding,'owner',p_owner,'version',v,'cut',view->>'digest','command',child->'prepare');
                receipt:=life_financial.swap_command(p_account,p_binding,p_owner,v,(view->>'digest')::life_financial.digest,encode(sha256(convert_to(life_financial.canonical_json_v1(request),'UTF8')),'hex'),'SWAP_PREPARE',child->'prepare');
                child_state:='PREPARED';DELETE FROM life_financial.hedge_staging WHERE account_id=p_account;
            EXCEPTION WHEN raise_exception OR check_violation OR numeric_value_out_of_range OR unique_violation THEN
                child_state:='REFUSED';
            END;
        END IF;
        request:=jsonb_build_object('kind',p_kind,'binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',cmd);
        INSERT INTO life_financial.hedge_plan_facts VALUES(p_account,op,p_version,at_ms,version,target,prior.operation_id,cap.operation_id,carry.operation_id,context,decision,progress,child,child_state);
        UPDATE life_financial.accounts SET version=accounts.version+1 WHERE account_id=p_account RETURNING accounts.version INTO v;
        result:=jsonb_build_object('kind',p_kind,'operation_id',op,'state','PLANNED','decision',decision,'child_operation_id',
            CASE WHEN child_state='PREPARED' THEN child#>>'{prepare,operation_id}' END,'child_state',child_state,'version',v,'replayed',false,'trading_permission',false);
    END IF;
    IF p_kind='HEDGE_PLAN' THEN INSERT INTO life_financial.hedge_plans VALUES(p_account,op,target,CASE WHEN child IS NULL THEN 0 ELSE 1 END,0,'PLANNED',1);END IF;
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version) VALUES(p_account,op,p_fingerprint,request,result,v);
    IF life_financial.hedge_retained_size(p_account)>p.max_total_bytes THEN RAISE EXCEPTION 'LIFE_HEDGE_BOUND' USING ERRCODE='P0001';END IF;
    IF NOT life_financial.hedge_verified(p_account) THEN RAISE EXCEPTION 'LIFE_HEDGE_UNVERIFIED' USING ERRCODE='P0001';END IF;
    RETURN result;
END; $$;

CREATE FUNCTION life_financial.hedge_read(p_account uuid,p_rows integer,p_bytes integer,p_operation text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE result jsonb;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_ACCESS_DENIED' USING ERRCODE='P0001';END IF;
    IF p_rows IS NULL OR p_bytes IS NULL OR p_rows NOT BETWEEN 1 AND 4096 OR p_bytes NOT BETWEEN 1 AND 16777216
        OR (SELECT count(*) FROM life_financial.hedge_plan_facts WHERE account_id=p_account)>p_rows
        OR (SELECT count(*) FROM life_financial.hedge_targets WHERE account_id=p_account)>p_rows
        OR (SELECT count(*) FROM life_financial.hedge_settlements WHERE account_id=p_account)>p_rows THEN RAISE EXCEPTION 'LIFE_HEDGE_READ_BOUND' USING ERRCODE='P0001';END IF;
    IF NOT life_financial.hedge_verified(p_account) THEN RAISE EXCEPTION 'LIFE_HEDGE_UNVERIFIED' USING ERRCODE='P0001';END IF;
    IF p_operation IS NULL THEN result:=jsonb_build_object('targets',COALESCE((SELECT jsonb_agg(to_jsonb(t) ORDER BY expected_version) FROM life_financial.hedge_targets t WHERE account_id=p_account),'[]'::jsonb),
        'plans',COALESCE((SELECT jsonb_agg(to_jsonb(h) ORDER BY received_at_ms,operation_id) FROM life_financial.hedge_plan_facts h WHERE account_id=p_account),'[]'::jsonb),
        'settlements',COALESCE((SELECT jsonb_agg(to_jsonb(h) ORDER BY received_at_ms,operation_id) FROM life_financial.hedge_settlements h WHERE account_id=p_account),'[]'::jsonb),'trading_permission',false);
    ELSE SELECT jsonb_build_object('command',o.command,'receipt',o.receipt) INTO result FROM life_financial.operations o
        WHERE o.account_id=p_account AND o.operation_id=p_operation AND o.command->>'kind' IN ('HEDGE_TARGET','HEDGE_PLAN','HEDGE_SETTLE');END IF;
    IF octet_length(result::text)>p_bytes THEN RAISE EXCEPTION 'LIFE_HEDGE_READ_BOUND' USING ERRCODE='P0001';END IF;RETURN result;
END; $$;

-- Retaining the previous good market is necessary for audit/replay, but it
-- cannot implicitly renew admission after a failed newer capture. A new explicit
-- qualified plan is required; HALT and all other gates remain independent.
CREATE FUNCTION life_financial.hedge_market_captured(p_account uuid,p_plan text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT COALESCE((SELECT (h.context->>'source_ready')::boolean
        AND h.progress->'market' IS NOT NULL AND h.progress->'market'<>'null'::jsonb
        AND h.progress->'market'=o.command#>'{command,market}'
        FROM life_financial.hedge_plan_facts h JOIN life_financial.operations o USING(account_id,operation_id)
        WHERE h.account_id=p_account AND h.operation_id=p_plan),false);
$$;

CREATE FUNCTION life_financial.hedge_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE o life_financial.operations;r life_financial.hedge_plan_facts;p life_financial.hedge_policies;now_ms bigint;latest life_financial.hedge_plan_facts;settlement life_financial.hedge_settlements;
BEGIN
    IF EXISTS(SELECT 1 FROM life_financial.hedge_staging WHERE account_id=NEW.account_id) THEN
        RAISE EXCEPTION 'LIFE_HEDGE_UNVERIFIED_AT_COMMIT' USING ERRCODE='P0001';END IF;
    -- Every account-version update schedules this trigger. Prove the final
    -- history once for its final account version, while retaining EACH original
    -- operation's independent deadline checks below. No cache survives COMMIT.
    IF TG_TABLE_NAME<>'accounts' THEN
        IF NOT life_financial.hedge_verified(NEW.account_id) THEN RAISE EXCEPTION 'LIFE_HEDGE_UNVERIFIED_AT_COMMIT' USING ERRCODE='P0001';END IF;
    ELSIF NEW.version=(SELECT version FROM life_financial.accounts WHERE account_id=NEW.account_id) AND NOT life_financial.hedge_verified(NEW.account_id) THEN
        RAISE EXCEPTION 'LIFE_HEDGE_UNVERIFIED_AT_COMMIT' USING ERRCODE='P0001';END IF;
    IF TG_TABLE_NAME<>'accounts' THEN RETURN NULL;END IF;
    SELECT * INTO o FROM life_financial.operations WHERE account_id=NEW.account_id AND committed_version=NEW.version;
    IF o.command->>'kind'='HEDGE_PLAN' THEN
        SELECT * INTO STRICT r FROM life_financial.hedge_plan_facts WHERE account_id=NEW.account_id AND operation_id=o.operation_id;
        now_ms:=floor(extract(epoch FROM clock_timestamp())*1000)::bigint;
        IF now_ms<r.received_at_ms THEN RAISE EXCEPTION 'LIFE_HEDGE_CLOCK_ROLLBACK_AT_COMMIT' USING ERRCODE='P0001';END IF;
        IF (r.context->>'source_ready')::boolean THEN
            SELECT * INTO STRICT p FROM life_financial.hedge_policies WHERE account_id=NEW.account_id;
            IF NOT life_financial.hedge_source_qualified(NEW.account_id,r.capital_operation_id,r.carry_operation_id,r.start_version,now_ms) THEN
                RAISE EXCEPTION 'LIFE_HEDGE_SOURCE_EXPIRED_AT_COMMIT' USING ERRCODE='P0001';END IF;
            IF r.progress->'market'=o.command#>'{command,market}' AND now_ms-(o.command#>>'{command,market,observed_at_ms}')::bigint>(p.body->>'max_market_age_ms')::bigint THEN
                RAISE EXCEPTION 'LIFE_HEDGE_MARKET_EXPIRED_AT_COMMIT' USING ERRCODE='P0001';END IF;
        END IF;
    ELSIF o.command->>'kind'='SWAP_ARM' AND EXISTS(SELECT 1 FROM life_financial.hedge_policies WHERE account_id=NEW.account_id) THEN
        SELECT h.* INTO STRICT latest FROM life_financial.hedge_plan_facts h JOIN life_financial.operations op USING(account_id,operation_id) WHERE h.account_id=NEW.account_id ORDER BY op.committed_version DESC LIMIT 1;
        SELECT * INTO STRICT p FROM life_financial.hedge_policies WHERE account_id=NEW.account_id;
        now_ms:=floor(extract(epoch FROM clock_timestamp())*1000)::bigint;
        IF NOT life_financial.hedge_market_captured(NEW.account_id,latest.operation_id) OR latest.progress->'market' IS NULL OR latest.progress->'market'='null'::jsonb
            OR now_ms<(latest.progress->>'last_ms')::bigint OR now_ms-(latest.progress#>>'{market,observed_at_ms}')::bigint>(p.body->>'max_market_age_ms')::bigint THEN
            RAISE EXCEPTION 'LIFE_HEDGE_ARM_REVOKED_AT_COMMIT' USING ERRCODE='P0001';END IF;
    ELSIF o.command->>'kind' IN ('SPOT_PREPARE','SPOT_ARM','SPOT_QUOTE_ARM') AND NOT life_financial.hedge_spot_ready(NEW.account_id) THEN
        RAISE EXCEPTION 'LIFE_HEDGE_SPOT_PAUSED_AT_COMMIT' USING ERRCODE='P0001';
    ELSIF o.command->>'kind'='HEDGE_SETTLE' THEN
        SELECT * INTO STRICT settlement FROM life_financial.hedge_settlements WHERE account_id=NEW.account_id AND operation_id=o.operation_id;
        now_ms:=floor(extract(epoch FROM clock_timestamp())*1000)::bigint;
        IF now_ms<settlement.received_at_ms OR settlement.source IS DISTINCT FROM life_financial.hedge_settlement_source(NEW.account_id,settlement.plan_operation_id,settlement.start_version,now_ms) THEN
            RAISE EXCEPTION 'LIFE_HEDGE_SETTLEMENT_REVOKED_AT_COMMIT' USING ERRCODE='P0001';END IF;
    END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER hedge_account_commit AFTER UPDATE ON life_financial.accounts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN(NEW.version<>OLD.version) EXECUTE FUNCTION life_financial.hedge_commit_guard();
CREATE CONSTRAINT TRIGGER hedge_stage_commit AFTER INSERT ON life_financial.hedge_staging
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.hedge_commit_guard();

-- Every configured hedge child must originate in the same account transaction
-- as its coordinator cost/attempt hold, claim, carry and preparation.
CREATE FUNCTION life_financial.hedge_claim_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF NEW.product='SWAP' AND EXISTS(SELECT 1 FROM life_financial.hedge_policies WHERE account_id=NEW.account_id)
        AND NOT EXISTS(SELECT 1 FROM life_financial.hedge_staging s WHERE s.account_id=NEW.account_id AND s.transaction_id=pg_current_xact_id()
            AND s.child->>'capital_operation_id'=NEW.operation_id AND s.child->'claim'=life_financial.shared_claim_body(NEW)) THEN
        RAISE EXCEPTION 'LIFE_HEDGE_CHILD_CONTEXT_REQUIRED' USING ERRCODE='P0001';END IF;
    RETURN NEW;
END; $$;
CREATE TRIGGER hedge_before_claim BEFORE INSERT ON life_financial.shared_capital_claims FOR EACH ROW EXECUTE FUNCTION life_financial.hedge_claim_guard();
CREATE FUNCTION life_financial.hedge_spot_ready(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE p life_financial.hedge_policies;r life_financial.hedge_plan_facts;a life_financial.accounts;cap life_financial.operations;
    carry life_financial.operations;cp jsonb;context jsonb;e jsonb;target numeric;at_ms bigint;
BEGIN
    SELECT * INTO p FROM life_financial.hedge_policies WHERE account_id=p_account;IF NOT FOUND THEN RETURN true;END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account;
    IF a.halted OR a.mode<>'ACTIVE' OR NOT life_financial.hedge_verified(p_account) OR NOT life_financial.carry_current_ready(p_account) THEN RETURN false;END IF;
    SELECT h.* INTO STRICT r FROM life_financial.hedge_plan_facts h JOIN life_financial.operations o USING(account_id,operation_id) WHERE h.account_id=p_account ORDER BY o.committed_version DESC LIMIT 1;
    IF NOT life_financial.hedge_market_captured(p_account,r.operation_id) THEN RETURN false;END IF;
    SELECT o.* INTO STRICT cap FROM life_financial.shared_capital_heads h JOIN life_financial.operations o USING(account_id,operation_id) WHERE h.account_id=p_account;
    SELECT o.* INTO STRICT carry FROM life_financial.carry_heads h JOIN life_financial.operations o USING(account_id,operation_id) WHERE h.account_id=p_account;
    SELECT body INTO STRICT cp FROM life_financial.shared_capital_policies WHERE account_id=p_account;
    SELECT COALESCE((SELECT t.target_base FROM life_financial.hedge_targets t WHERE t.account_id=p_account ORDER BY expected_version DESC LIMIT 1),(p.body->>'initial_target_base')::numeric) INTO target;
    at_ms:=floor(extract(epoch FROM clock_timestamp())*1000)::bigint;
    IF at_ms<(r.progress->>'last_ms')::bigint OR NOT life_financial.hedge_source_qualified(p_account,cap.operation_id,carry.operation_id,a.version,at_ms)
        OR EXISTS(SELECT 1 FROM life_financial.send_attempts t WHERE t.account_id=p_account AND t.product='SWAP' AND t.state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND')) THEN RETURN false;END IF;
    context:=life_financial.hedge_context(p_account,a.version);
    e:=life_financial.hedge_evaluate(p.body,cp,cap.command#>'{command,snapshot}',carry.receipt->'decision',r.progress->'market',context,r.progress,target,at_ms,true);
    RETURN e#>'{decision,allow_spot_risk_increase}'='true'::jsonb;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
CREATE FUNCTION life_financial.hedge_attempt_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE r life_financial.hedge_plan_facts;p life_financial.hedge_policies;a life_financial.accounts;prior life_financial.hedge_plan_facts;last_plan life_financial.hedge_plan_facts;
    cap life_financial.operations;carry life_financial.operations;cp jsonb;context jsonb;e jsonb;target numeric;at_ms bigint;
BEGIN
    IF NEW.state NOT IN ('PREPARED','ARMED','SEND_UNKNOWN') THEN RETURN NEW;END IF;
    SELECT * INTO p FROM life_financial.hedge_policies WHERE account_id=NEW.account_id;IF NOT FOUND THEN RETURN NEW;END IF;
    IF NEW.product='SPOT' THEN
        IF NOT life_financial.hedge_spot_ready(NEW.account_id) THEN RAISE EXCEPTION 'LIFE_HEDGE_SPOT_PAUSED' USING ERRCODE='P0001';END IF;
        RETURN NEW;
    END IF;
    IF TG_OP='INSERT' THEN
        IF NOT EXISTS(SELECT 1 FROM life_financial.hedge_staging s WHERE s.account_id=NEW.account_id AND s.transaction_id=pg_current_xact_id()
            AND s.child#>>'{prepare,operation_id}'=NEW.operation_id AND s.child#>>'{prepare,config,id}'=NEW.intent_id) THEN
            RAISE EXCEPTION 'LIFE_HEDGE_CHILD_CONTEXT_REQUIRED' USING ERRCODE='P0001';END IF;
        RETURN NEW;
    END IF;
    SELECT h.* INTO STRICT r FROM life_financial.hedge_plan_facts h WHERE h.account_id=NEW.account_id AND h.child_state='PREPARED' AND h.child#>>'{prepare,config,id}'=NEW.intent_id;
    SELECT * INTO prior FROM life_financial.hedge_plan_facts WHERE account_id=NEW.account_id AND operation_id=r.previous_operation_id;
    SELECT h.* INTO STRICT last_plan FROM life_financial.hedge_plan_facts h JOIN life_financial.operations o USING(account_id,operation_id) WHERE h.account_id=NEW.account_id ORDER BY o.committed_version DESC LIMIT 1;
    IF NOT life_financial.hedge_market_captured(NEW.account_id,last_plan.operation_id) THEN
        RAISE EXCEPTION 'LIFE_HEDGE_MARKET_REVALIDATION_REQUIRED' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=NEW.account_id;
    SELECT o.* INTO STRICT cap FROM life_financial.shared_capital_heads h JOIN life_financial.operations o USING(account_id,operation_id) WHERE h.account_id=NEW.account_id;
    SELECT o.* INTO STRICT carry FROM life_financial.carry_heads h JOIN life_financial.operations o USING(account_id,operation_id) WHERE h.account_id=NEW.account_id;
    SELECT body INTO STRICT cp FROM life_financial.shared_capital_policies WHERE account_id=NEW.account_id;
    SELECT COALESCE((SELECT t.target_base FROM life_financial.hedge_targets t WHERE t.account_id=NEW.account_id ORDER BY expected_version DESC LIMIT 1),(p.body->>'initial_target_base')::numeric) INTO target;
    at_ms:=floor(extract(epoch FROM clock_timestamp())*1000)::bigint;
    IF at_ms<(last_plan.progress->>'last_ms')::bigint OR NOT life_financial.hedge_source_qualified(NEW.account_id,cap.operation_id,carry.operation_id,a.version,at_ms) THEN
        RAISE EXCEPTION 'LIFE_HEDGE_ARM_SOURCE_UNQUALIFIED' USING ERRCODE='P0001';END IF;
    context:=life_financial.hedge_context(NEW.account_id,a.version);
    context:=context||jsonb_build_object('hedges_pending',false,'pending',(context->'pending')-NEW.intent_id);
    e:=life_financial.hedge_evaluate(p.body,cp,cap.command#>'{command,snapshot}',carry.receipt->'decision',last_plan.progress->'market',context,prior.progress,target,at_ms,true);
    IF e#>>'{decision,side}' IS DISTINCT FROM r.decision->>'side' OR e#>>'{decision,position_action}' IS DISTINCT FROM r.decision->>'position_action'
        OR (e#>>'{decision,quantity_base}')::numeric IS DISTINCT FROM NEW.quantity OR (e#>>'{decision,price_usdt}')::numeric IS DISTINCT FROM NEW.price
        OR (e#>>'{decision,estimated_cost_quote}')::numeric>(r.decision->>'estimated_cost_quote')::numeric THEN
        RAISE EXCEPTION 'LIFE_HEDGE_ARM_GEOMETRY_OR_COST_CHANGED' USING ERRCODE='P0001';END IF;
    RETURN NEW;
END; $$;
CREATE TRIGGER hedge_before_attempt BEFORE INSERT OR UPDATE OF state ON life_financial.send_attempts FOR EACH ROW EXECUTE FUNCTION life_financial.hedge_attempt_guard();

-- A rolled-back child attempt still owns its captured identities. A caller
-- cannot recycle a refused hedge as another product or reset its cost history.
CREATE FUNCTION life_financial.hedge_retired_identity_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF TG_TABLE_NAME='operations' THEN
        IF EXISTS(SELECT 1 FROM life_financial.hedge_plan_facts h WHERE h.account_id=NEW.account_id AND h.child_state='REFUSED'
            AND NEW.operation_id IN (h.child->>'capital_operation_id',h.child->>'carry_operation_id',h.child#>>'{prepare,operation_id}')) THEN
            RAISE EXCEPTION 'LIFE_HEDGE_CHILD_ID_RETIRED' USING ERRCODE='P0001';END IF;
    ELSE
        IF EXISTS(SELECT 1 FROM life_financial.hedge_plan_facts h WHERE h.account_id=NEW.account_id AND h.child_state='REFUSED'
            AND (NEW.intent_id=h.child#>>'{prepare,config,id}' OR NEW.wire_id=h.child#>>'{prepare,client_order_id}')) THEN
            RAISE EXCEPTION 'LIFE_HEDGE_CHILD_ID_RETIRED' USING ERRCODE='P0001';END IF;
    END IF;
    RETURN NEW;
END; $$;
CREATE TRIGGER hedge_retired_operation BEFORE INSERT ON life_financial.operations FOR EACH ROW EXECUTE FUNCTION life_financial.hedge_retired_identity_guard();
CREATE TRIGGER hedge_retired_claim BEFORE INSERT ON life_financial.shared_capital_claims FOR EACH ROW EXECUTE FUNCTION life_financial.hedge_retired_identity_guard();
CREATE TRIGGER hedge_retired_attempt BEFORE INSERT ON life_financial.send_attempts FOR EACH ROW EXECUTE FUNCTION life_financial.hedge_retired_identity_guard();

CREATE FUNCTION life_financial.spot_state_before_hedge(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE b jsonb;verified boolean;pending boolean;
BEGIN
    b:=life_financial.spot_state_before_joint_batch(p_account)-'digest'-'observed_at';
    verified:=life_financial.joint_batch_verified(p_account) AND life_financial.joint_repair_verified(p_account);
    pending:=EXISTS(SELECT 1 FROM life_financial.joint_financial_batches batch WHERE batch.account_id=p_account AND batch.state='RECOVERY_REQUIRED'
        AND NOT EXISTS(SELECT 1 FROM life_financial.joint_repair_facts r WHERE r.account_id=p_account AND r.pending_operation_id=batch.operation_id));
    b:=b||jsonb_build_object('joint_batches',jsonb_build_object('verified',verified,'pending',pending,
        'policy',(SELECT to_jsonb(p) FROM life_financial.joint_batch_policies p WHERE p.account_id=p_account),
        'batches',COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY operation_id) FROM life_financial.joint_financial_batches r WHERE r.account_id=p_account),'[]'::jsonb)),
        'joint_repairs',jsonb_build_object('policy',(SELECT to_jsonb(p) FROM life_financial.joint_repair_policies p WHERE p.account_id=p_account),
            'repairs',COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY operation_id) FROM life_financial.joint_repair_facts r WHERE r.account_id=p_account),'[]'::jsonb)),
        'financial_recovery_required',(b->>'financial_recovery_required')::boolean OR NOT verified OR pending,
        'recovery_required',(b->>'recovery_required')::boolean OR NOT verified OR pending,'unverified',(b->>'unverified')::boolean OR NOT verified);
    RETURN b||jsonb_build_object('digest',encode(sha256(convert_to(b::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
END; $$;
CREATE OR REPLACE FUNCTION life_financial.spot_state(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b jsonb;verified boolean;
BEGIN
    b:=life_financial.spot_state_before_hedge(p_account)-'digest'-'observed_at';verified:=life_financial.hedge_verified(p_account);
    b:=b||jsonb_build_object('hedge',jsonb_build_object('verified',verified,'policy',(SELECT to_jsonb(p) FROM life_financial.hedge_policies p WHERE p.account_id=p_account),
        'targets',COALESCE((SELECT jsonb_agg(to_jsonb(t) ORDER BY expected_version) FROM life_financial.hedge_targets t WHERE account_id=p_account),'[]'::jsonb),
        'plans',COALESCE((SELECT jsonb_agg(to_jsonb(h) ORDER BY received_at_ms,operation_id) FROM life_financial.hedge_plan_facts h WHERE account_id=p_account),'[]'::jsonb),
        'settlements',COALESCE((SELECT jsonb_agg(to_jsonb(h) ORDER BY received_at_ms,operation_id) FROM life_financial.hedge_settlements h WHERE account_id=p_account),'[]'::jsonb)),
        'financial_recovery_required',(b->>'financial_recovery_required')::boolean OR NOT verified,
        'recovery_required',(b->>'recovery_required')::boolean OR NOT verified,'unverified',(b->>'unverified')::boolean OR NOT verified);
    RETURN b||jsonb_build_object('digest',encode(sha256(convert_to(b::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
END; $$;
DO $$ DECLARE definition text;BEGIN
    definition:=pg_get_functiondef('life_financial.commit_owner_guard()'::regprocedure);
    IF position('IN (''JOINT_REPAIR'',' IN definition)=0 THEN RAISE EXCEPTION 'LIFE_HEDGE_MIGRATION_GUARD_MISMATCH';END IF;
    EXECUTE replace(definition,'IN (''JOINT_REPAIR'',','IN (''HEDGE_PLAN'',''HEDGE_SETTLE'',''JOINT_REPAIR'',');
END; $$;
REVOKE ALL ON life_financial.hedge_policies,life_financial.hedge_targets,life_financial.hedge_plan_facts,life_financial.hedge_settlements,life_financial.hedge_staging FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
