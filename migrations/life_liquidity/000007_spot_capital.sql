-- Approved SPOT cashflows and causal capital; no approval/grant/bootstrap API.
CREATE TABLE life_financial.spot_capital_policies (
    account_id uuid PRIMARY KEY REFERENCES life_financial.accounts,
    policy_hash life_financial.digest NOT NULL,
    opening_base life_financial.amount NOT NULL, opening_quote life_financial.amount NOT NULL,
    opening_price life_financial.positive_amount NOT NULL,
    opened_at timestamptz NOT NULL CHECK (isfinite(opened_at)),
    anchor_bill text NOT NULL CHECK (anchor_bill ~ '^[1-9][0-9]{0,63}$'),
    max_replay_events integer NOT NULL CHECK (max_replay_events BETWEEN 1 AND 100000),
    CHECK (opening_base>0 OR opening_quote>0),
    FOREIGN KEY(account_id,policy_hash) REFERENCES life_financial.policies
);
CREATE TABLE life_financial.spot_cashflow_facts (
    account_id uuid NOT NULL, bill_id life_financial.identity NOT NULL,
    command jsonb NOT NULL CHECK (jsonb_typeof(command)='object'),
    state text NOT NULL CHECK (state IN ('APPLIED','RECOVERY_REQUIRED')),
    reason life_financial.identity,
    source_operation_id life_financial.identity NOT NULL, source_hash life_financial.digest NOT NULL,
    PRIMARY KEY(account_id,bill_id),
    CHECK (state='APPLIED' AND reason IS NULL OR state='RECOVERY_REQUIRED' AND reason IS NOT NULL),
    FOREIGN KEY(account_id,bill_id) REFERENCES life_financial.bill_approvals,
    FOREIGN KEY(account_id,source_operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE life_financial.spot_nav_observations (
    account_id uuid NOT NULL REFERENCES life_financial.spot_capital_policies,
    operation_id life_financial.identity NOT NULL,
    command jsonb NOT NULL, measurement jsonb NOT NULL,
    money_digest life_financial.digest NOT NULL,
    highwater life_financial.positive_amount NOT NULL,
    sequence bigint GENERATED ALWAYS AS IDENTITY,
    PRIMARY KEY(account_id,operation_id),
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX spot_nav_latest ON life_financial.spot_nav_observations(account_id,sequence DESC);
DO $$ DECLARE t text; BEGIN
    FOREACH t IN ARRAY ARRAY['spot_capital_policies','spot_cashflow_facts','spot_nav_observations'] LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY scoped_read ON life_financial.%I FOR SELECT USING (life_financial.can_access(account_id))',t);
        EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',t);
    END LOOP;
END; $$;

-- Internal helpers are not callable by runtime roles. The replay bound is an
-- explicitly imported policy input, never a live default or an O.8 claim.
CREATE FUNCTION life_financial.spot_capital_digest(p_account uuid) RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE bound integer; n bigint;
BEGIN
SELECT max_replay_events INTO STRICT bound FROM life_financial.spot_capital_policies WHERE account_id=p_account;
SELECT (SELECT count(*) FROM life_financial.spot_fill_facts WHERE account_id=p_account)+
    (SELECT count(*) FROM life_financial.spot_cashflow_facts WHERE account_id=p_account) INTO n;
IF n>bound THEN RAISE EXCEPTION 'LIFE_CAPITAL_REPLAY_BOUND' USING ERRCODE='P0001'; END IF;
RETURN encode(sha256(convert_to(jsonb_build_object(
    'policy',(SELECT to_jsonb(p) FROM life_financial.spot_capital_policies p WHERE account_id=p_account),
    'fills',COALESCE((SELECT jsonb_agg(to_jsonb(x) ORDER BY trade_id COLLATE "C") FROM life_financial.spot_fill_facts x WHERE account_id=p_account),'[]'::jsonb),
    'cashflows',COALESCE((SELECT jsonb_agg(to_jsonb(x) ORDER BY bill_id COLLATE "C") FROM life_financial.spot_cashflow_facts x WHERE account_id=p_account),'[]'::jsonb)
)::text,'UTF8')),'hex');
END; $$;

CREATE FUNCTION life_financial.spot_drawdown_round(p_value numeric) RETURNS numeric
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE places integer; factor numeric; scaled numeric; lower_value numeric;
BEGIN
    IF NOT (p_value>=0 AND p_value<'Infinity'::numeric) THEN
        RAISE EXCEPTION 'LIFE_NAV_VALUE_INVALID' USING ERRCODE='P0001'; END IF;
    IF p_value=0 THEN RETURN 0; END IF;
    places:=27-floor(log(10,p_value))::integer;
    factor:=power(10::numeric,places); scaled:=p_value*factor; lower_value:=floor(scaled);
    IF scaled-lower_value=0.5 AND mod(lower_value,2)=0 THEN RETURN round(lower_value::numeric(200,140)/factor,places); END IF;
    RETURN round(p_value,places);
END; $$;

CREATE FUNCTION life_financial.spot_capital_replay(p_account uuid) RETURNS jsonb
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
           (SELECT count(*) FROM life_financial.spot_cashflow_facts WHERE account_id=p_account) INTO n;
    IF n>p.max_replay_events THEN RAISE EXCEPTION 'LIFE_CAPITAL_REPLAY_BOUND' USING ERRCODE='P0001'; END IF;
    base:=p.opening_base; quote:=p.opening_quote; initial:=base;
    FOR e IN SELECT * FROM (
        SELECT (x.command->>'transfer_at_ms')::bigint AS at_ms,0 AS kind,x.bill_id::text AS id,x.command AS data,NULL::text AS side
            FROM life_financial.spot_cashflow_facts x WHERE account_id=p_account AND state='APPLIED'
        UNION ALL
        SELECT (x.observation->>'fill_at_ms')::bigint,1,x.trade_id::text,x.observation,t.side
            FROM life_financial.spot_fill_facts x JOIN life_financial.send_attempts t USING(account_id,intent_id) WHERE x.account_id=p_account
        ) timeline ORDER BY at_ms,kind,id COLLATE "C"
    LOOP
        j:=e.data;
        IF e.at_ms<floor(extract(epoch FROM p.opened_at)*1000) THEN
            RAISE EXCEPTION 'LIFE_CAPITAL_CAUSAL_INVALID' USING ERRCODE='P0001'; END IF;
        reference:=(j->>'independent_price_usdt')::numeric;
        IF e.kind=0 THEN
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

CREATE FUNCTION life_financial.spot_capital_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r jsonb;
BEGIN
    IF NOT EXISTS(SELECT 1 FROM life_financial.spot_capital_policies WHERE account_id=p_account) THEN
        RETURN NOT EXISTS(SELECT 1 FROM life_financial.cashflows WHERE account_id=p_account)
            AND NOT EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts WHERE account_id=p_account);
    END IF;
    IF EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts WHERE account_id=p_account AND state<>'APPLIED')
        OR EXISTS(SELECT 1 FROM life_financial.positions WHERE account_id=p_account AND quantity<>0)
        OR NOT life_financial.spot_accounting_verified(p_account)
        OR EXISTS(SELECT 1 FROM life_financial.fills f WHERE f.account_id=p_account AND (f.product<>'SPOT' OR NOT EXISTS(
            SELECT 1 FROM life_financial.spot_fill_facts x WHERE x.account_id=f.account_id AND x.trade_id=f.trade_id)))
        OR EXISTS(SELECT 1 FROM life_financial.cashflows c FULL JOIN life_financial.spot_cashflow_facts x USING(account_id,bill_id)
            LEFT JOIN life_financial.bill_approvals b USING(account_id,bill_id)
            WHERE COALESCE(c.account_id,x.account_id)=p_account AND (c.account_id IS NULL OR x.account_id IS NULL
                OR x.state<>'APPLIED' OR b.asset<>x.command->>'currency' OR b.signed_amount<>(x.command->>'signed_amount')::numeric
                OR floor(extract(epoch FROM b.transfer_at)*1000)<>(x.command->>'transfer_at_ms')::bigint
                OR c.independent_price<>(x.command->>'independent_price_usdt')::numeric OR c.value_usdt<>b.signed_amount*c.independent_price
                OR c.source_hash<>x.source_hash OR NOT EXISTS(SELECT 1 FROM life_financial.financial_events e
                    WHERE e.account_id=x.account_id AND e.product='ACCOUNT' AND e.kind='CASHFLOW' AND e.exchange_identity=x.bill_id
                    AND e.source_hash=x.source_hash AND e.occurred_at=b.transfer_at
                    AND e.base_delta=CASE WHEN b.asset='LIFE' THEN b.signed_amount ELSE 0 END
                    AND e.quote_delta=CASE WHEN b.asset='USDT' THEN b.signed_amount ELSE 0 END))) THEN RETURN false; END IF;
    r:=life_financial.spot_capital_replay(p_account);
    RETURN (r->>'base')::numeric=(SELECT total FROM life_financial.balances WHERE account_id=p_account AND asset='LIFE')
        AND (r->>'quote')::numeric=(SELECT total FROM life_financial.balances WHERE account_id=p_account AND asset='USDT');
EXCEPTION WHEN raise_exception OR no_data_found OR check_violation OR numeric_value_out_of_range THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.spot_capital_read(p_account uuid,p_operation text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE n life_financial.spot_nav_observations; view jsonb; result jsonb; valid boolean:=false;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_ACCESS_DENIED' USING ERRCODE='P0001'; END IF;
    SELECT * INTO n FROM life_financial.spot_nav_observations WHERE account_id=p_account
        AND (p_operation IS NULL OR operation_id=p_operation) ORDER BY sequence DESC LIMIT 1;
    IF NOT FOUND THEN RETURN NULL; END IF;
    view:=life_financial.spot_state(p_account);
    IF life_financial.spot_capital_verified(p_account) AND NOT (view->>'unverified')::boolean THEN
        valid:=n.money_digest=life_financial.spot_capital_digest(p_account)
            AND NOT EXISTS(SELECT 1 FROM life_financial.balances WHERE account_id=p_account AND funding_deficit>0);
    END IF;
    result:=jsonb_build_object('command',n.command,'measurement',n.measurement,'snapshot',view,
        'current',valid);
    RETURN result;
END; $$;

CREATE FUNCTION life_financial.spot_capital_command(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,p_kind text,p_command jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts; p life_financial.spot_capital_policies; fp life_financial.spot_fill_policies;
    previous life_financial.operations; b life_financial.bill_approvals; fact life_financial.spot_cashflow_facts;
    old_nav life_financial.spot_nav_observations; request jsonb; result jsonb; view jsonb; r jsonb; measurement jsonb;
    op life_financial.identity; bill life_financial.identity; observed timestamptz; at_ms bigint; value_ms bigint; ref_ms bigint;
    reference life_financial.positive_amount; amount life_financial.signed_amount; state text:='APPLIED';
    stable jsonb; nav numeric; adjusted numeric; highwater numeric; drawdown numeric; inventory numeric;
    reason text;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR p_fingerprint IS NULL OR p_cut IS NULL OR p_command IS NULL
        OR p_kind IS NULL OR p_kind NOT IN ('SPOT_CASHFLOW','SPOT_NAV') OR jsonb_typeof(p_command)<>'object' OR octet_length(p_command::text)>16384
        OR EXISTS(SELECT 1 FROM jsonb_each(p_command) WHERE value='null'::jsonb) THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    IF p_kind='SPOT_CASHFLOW' AND ((SELECT count(*) FROM jsonb_object_keys(p_command))<>10 OR NOT p_command ?& ARRAY[
        'operation_id','bill_id','currency','signed_amount','transfer_at_ms','independent_price_usdt','value_at_ms','reference_observed_at_ms','source_kind','observed_at'])
        OR p_kind='SPOT_NAV' AND ((SELECT count(*) FROM jsonb_object_keys(p_command))<>6 OR NOT p_command ?& ARRAY[
        'operation_id','independent_price_usdt','value_at_ms','reference_observed_at_ms','source_kind','observed_at']) THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_MODE_UNSUPPORTED' USING ERRCODE='P0001'; END IF;
    op:=p_command->>'operation_id'; IF length(op)>96 THEN RAISE EXCEPTION 'LIFE_CAPITAL_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,a.version);
    request:=jsonb_build_object('kind',p_kind,'binding',p_binding,'owner',p_owner,'command',p_command);
    SELECT * INTO previous FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF previous.command<>request OR previous.fingerprint<>p_fingerprint THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001'; END IF;
        RETURN previous.receipt||'{"replayed":true}'::jsonb;
    END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    view:=life_financial.spot_state(p_account);
    IF view->>'digest'<>p_cut THEN RAISE EXCEPTION 'LIFE_SPOT_CUT_CONFLICT' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT p FROM life_financial.spot_capital_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
    SELECT * INTO STRICT fp FROM life_financial.spot_fill_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
    observed:=(p_command->>'observed_at')::timestamptz;
    value_ms:=(p_command->>'value_at_ms')::bigint; ref_ms:=(p_command->>'reference_observed_at_ms')::bigint;
    reference:=(p_command->>'independent_price_usdt')::numeric;
    IF NOT isfinite(observed) OR observed>clock_timestamp() OR value_ms<=0 OR ref_ms<value_ms
        OR ref_ms>floor(extract(epoch FROM observed)*1000) THEN
        RAISE EXCEPTION 'LIFE_CAPITAL_VALUE_UNQUALIFIED' USING ERRCODE='P0001'; END IF;
    IF p_kind='SPOT_CASHFLOW' THEN
        bill:=p_command->>'bill_id'; amount:=(p_command->>'signed_amount')::numeric; at_ms:=(p_command->>'transfer_at_ms')::bigint;
        SELECT * INTO b FROM life_financial.bill_approvals WHERE account_id=p_account AND bill_id=bill;
        IF NOT FOUND OR bill !~ '^[1-9][0-9]{0,63}$' OR bill::numeric<=p.anchor_bill::numeric OR amount=0
            OR octet_length(b.signature)=0 OR b.asset<>p_command->>'currency' OR b.signed_amount<>amount
            OR floor(extract(epoch FROM b.transfer_at)*1000)<>at_ms OR b.transfer_at<p.opened_at THEN
            RAISE EXCEPTION 'LIFE_CASHFLOW_UNAPPROVED' USING ERRCODE='P0001'; END IF;
        IF at_ms>floor(extract(epoch FROM observed)*1000) OR abs(value_ms-at_ms)>fp.max_reference_skew_ms
            OR abs(ref_ms-at_ms)>fp.max_reference_skew_ms
            OR b.asset='USDT' AND (reference<>1 OR p_command->>'source_kind'<>'quote_unit')
            OR b.asset='LIFE' AND p_command->>'source_kind'<>'independent_market' THEN
            RAISE EXCEPTION 'LIFE_CAPITAL_VALUE_UNQUALIFIED' USING ERRCODE='P0001'; END IF;
        stable:=p_command-ARRAY['operation_id','observed_at'];
        SELECT * INTO fact FROM life_financial.spot_cashflow_facts WHERE account_id=p_account AND bill_id=bill;
        IF FOUND THEN
            IF fact.command-ARRAY['operation_id','observed_at']<>stable THEN RAISE EXCEPTION 'LIFE_CASHFLOW_IDENTITY_CONFLICT' USING ERRCODE='P0001'; END IF;
            state:=fact.state;
        ELSE
          BEGIN
            IF NOT life_financial.spot_capital_verified(p_account) THEN RAISE EXCEPTION 'LIFE_CAPITAL_CAUSAL_INVALID' USING ERRCODE='P0001'; END IF;
            INSERT INTO life_financial.spot_cashflow_facts VALUES(p_account,bill,p_command,'APPLIED',NULL,op,p_fingerprint);
            INSERT INTO life_financial.cashflows VALUES(p_account,bill,reference,amount*reference,p_fingerprint);
            INSERT INTO life_financial.financial_events(account_id,event_id,product,kind,exchange_identity,occurred_at,source_hash,base_delta,quote_delta)
                VALUES(p_account,op||':cash','ACCOUNT','CASHFLOW',bill,b.transfer_at,p_fingerprint,
                    CASE WHEN b.asset='LIFE' THEN amount ELSE 0 END,CASE WHEN b.asset='USDT' THEN amount ELSE 0 END);
            r:=life_financial.spot_capital_replay(p_account);
            IF (SELECT total+amount FROM life_financial.balances WHERE account_id=p_account AND asset=b.asset)<0 THEN
                RAISE EXCEPTION 'LIFE_CAPITAL_CAUSAL_INVALID' USING ERRCODE='P0001'; END IF;
            UPDATE life_financial.balances SET total=total+amount,funding_deficit=greatest(0,reserved-total-amount),version=version+1
                WHERE account_id=p_account AND asset=b.asset;
            IF NOT life_financial.spot_capital_verified(p_account) THEN RAISE EXCEPTION 'LIFE_CAPITAL_CAUSAL_INVALID' USING ERRCODE='P0001'; END IF;
          EXCEPTION WHEN raise_exception THEN
            GET STACKED DIAGNOSTICS reason=MESSAGE_TEXT;
            IF reason NOT IN ('LIFE_CAPITAL_CAUSAL_INVALID','LIFE_CAPITAL_REPLAY_BOUND') THEN RAISE; END IF;
            INSERT INTO life_financial.spot_cashflow_facts VALUES(p_account,bill,p_command,'RECOVERY_REQUIRED',reason,op,p_fingerprint);
            state:='RECOVERY_REQUIRED';
          END;
        END IF;
        IF state<>'APPLIED' OR EXISTS(SELECT 1 FROM life_financial.balances WHERE account_id=p_account AND funding_deficit>0) THEN
            UPDATE life_financial.accounts SET halted=true WHERE account_id=p_account;
        END IF;
        result:=jsonb_build_object('bill_id',bill,'state',state);
    ELSE
        IF p_command->>'source_kind'<>'independent_market'
            OR abs(floor(extract(epoch FROM clock_timestamp())*1000)-value_ms)>fp.max_reference_skew_ms
            OR abs(floor(extract(epoch FROM clock_timestamp())*1000)-ref_ms)>fp.max_reference_skew_ms
            OR NOT life_financial.spot_capital_verified(p_account) OR (view->>'unverified')::boolean THEN
            RAISE EXCEPTION 'LIFE_NAV_UNQUALIFIED' USING ERRCODE='P0001'; END IF;
        SELECT * INTO old_nav FROM life_financial.spot_nav_observations WHERE account_id=p_account ORDER BY sequence DESC LIMIT 1;
        IF FOUND AND (observed<(old_nav.command->>'observed_at')::timestamptz
            OR value_ms<(old_nav.command->>'value_at_ms')::bigint OR ref_ms<(old_nav.command->>'reference_observed_at_ms')::bigint) THEN
            RAISE EXCEPTION 'LIFE_NAV_REGRESSION' USING ERRCODE='P0001'; END IF;
        r:=life_financial.spot_capital_replay(p_account);
        nav:=(r->>'quote')::numeric+(r->>'base')::numeric*reference; adjusted:=nav-(r->>'net')::numeric;
        highwater:=greatest((r->>'opening_nav')::numeric,COALESCE(old_nav.highwater,0),adjusted);
        -- Match the existing 28-significant-digit Decimal drawdown contract.
        drawdown:=greatest(0,highwater-adjusted)::numeric(200,140)/highwater*10000;
        drawdown:=life_financial.spot_drawdown_round(drawdown);
        inventory:=(r->>'initial')::numeric*(reference-(r->>'opening_price')::numeric)+(r->>'realized')::numeric;
        measurement:=jsonb_build_object('nav_usdt',nav::text,'adjusted_nav_usdt',adjusted::text,'highwater_usdt',highwater::text,
            'drawdown_bps',drawdown::text,'execution_loss_usdt',r->>'loss','starting_inventory_pnl_usdt',inventory::text,'net_cashflows_usdt',r->>'net');
        INSERT INTO life_financial.spot_nav_observations(account_id,operation_id,command,measurement,money_digest,highwater)
            VALUES(p_account,op,p_command,measurement,life_financial.spot_capital_digest(p_account),highwater);
        result:=jsonb_build_object('state','APPLIED');
    END IF;
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    result:=result||jsonb_build_object('kind',p_kind,'operation_id',op,'version',p_version+1,'replayed',false,'trading_permission',false);
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version)
        VALUES(p_account,op,p_fingerprint,request,result,p_version+1);
    RETURN result;
END; $$;

REVOKE ALL ON life_financial.spot_capital_policies,life_financial.spot_cashflow_facts,life_financial.spot_nav_observations FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;

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
    IF op.command->>'kind' IN ('SPOT_ACK','SPOT_FILL','SPOT_CASHFLOW','SPOT_NAV') THEN
        PERFORM life_financial.guard_spot_observation(NEW.account_id,op.command->'binding',op.command->'owner',a.version);
    ELSE
        SELECT expires_at INTO STRICT expiry FROM life_financial.sessions WHERE account_id=a.account_id AND session_id=a.current_session;
        at := clock_timestamp();
        IF a.mode<>'ACTIVE' OR a.halted OR at<a.owner_since OR at>=a.owner_until OR at>=expiry
            OR current_setting('synchronous_commit')<>'on' OR current_setting('fsync')<>'on'
            OR current_setting('full_page_writes')<>'on' OR pg_is_in_recovery() THEN
            RAISE EXCEPTION 'LIFE_OWNER_EXPIRED_OR_CHANGED_AT_COMMIT' USING ERRCODE='P0001'; END IF;
    END IF;
    RETURN NULL;
END; $$;

CREATE OR REPLACE FUNCTION life_financial.spot_state(p_account uuid) RETURNS jsonb
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
    unverified := EXISTS(SELECT 1 FROM life_financial.send_attempts t WHERE t.account_id=p_account AND t.state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND')
        AND (t.product<>'SPOT' OR t.session_id<>a.current_session OR t.epoch<>a.owner_epoch
            OR t.process_id<>a.owner_process OR t.generation<>a.owner_generation
            OR NOT EXISTS(SELECT 1 FROM life_financial.spot_preparations d JOIN life_financial.operations o USING(account_id,operation_id)
                WHERE d.account_id=t.account_id AND d.intent_id=t.intent_id AND (d.request->>'config_version')::bigint=s.config_version
                AND o.command#>>'{binding,policy_hash}'=a.policy_hash)))
        OR EXISTS(SELECT 1 FROM life_financial.capital_claims WHERE account_id=p_account AND state='HELD')
        OR EXISTS(SELECT 1 FROM life_financial.positions WHERE account_id=p_account AND quantity<>0)
        OR NOT life_financial.spot_accounting_verified(p_account)
        OR EXISTS(SELECT 1 FROM life_financial.financial_events e WHERE e.account_id=p_account AND
            (e.product<>'SPOT' AND NOT (e.product='ACCOUNT' AND e.kind='CASHFLOW') OR e.kind NOT IN ('ACK','FILL','CASHFLOW') OR e.kind='ACK' AND NOT EXISTS(
                SELECT 1 FROM life_financial.send_attempts t WHERE t.account_id=p_account AND t.product='SPOT' AND t.exchange_order_id=e.exchange_identity)
            OR e.kind='CASHFLOW' AND NOT EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts x WHERE x.account_id=p_account AND x.bill_id=e.exchange_identity AND x.state='APPLIED')
            OR e.kind='FILL' AND NOT EXISTS(SELECT 1 FROM life_financial.spot_fill_facts x WHERE x.account_id=p_account AND x.trade_id=e.exchange_identity)))
        OR NOT life_financial.spot_capital_verified(p_account)
        OR EXISTS(SELECT 1 FROM life_financial.recorder_outbox o WHERE o.account_id=p_account
            AND NOT EXISTS(SELECT 1 FROM life_financial.projection_receipts r WHERE r.account_id=o.account_id AND r.projection_id=o.projection_id))
        OR EXISTS(SELECT 1 FROM life_financial.quote_actions WHERE account_id=p_account AND state NOT IN ('REJECTED','RECONCILED'))
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

REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
