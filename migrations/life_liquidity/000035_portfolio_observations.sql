-- Portfolio measurements retain their original economic cut and independent
-- mark/index/account sources. No source bootstrap, wallet write or claim release.
CREATE TABLE life_financial.portfolio_policies (
    account_id uuid PRIMARY KEY REFERENCES life_financial.swap_fill_policies,
    policy_hash life_financial.digest NOT NULL,opening_mark life_financial.positive_amount NOT NULL,
    max_drawdown_bps numeric NOT NULL CHECK(max_drawdown_bps>0 AND max_drawdown_bps<=10000),
    max_age_ms integer NOT NULL CHECK(max_age_ms BETWEEN 1 AND 2147483647),
    max_observations integer NOT NULL CHECK(max_observations BETWEEN 1 AND 1024),
    max_total_bytes integer NOT NULL CHECK(max_total_bytes BETWEEN 1 AND 16777216),
    FOREIGN KEY(account_id,policy_hash) REFERENCES life_financial.policies
);
CREATE TABLE life_financial.portfolio_observations (
    account_id uuid NOT NULL REFERENCES life_financial.portfolio_policies,
    operation_id life_financial.identity NOT NULL,basis jsonb NOT NULL,measurement jsonb NOT NULL,
    received_at_ms bigint NOT NULL CHECK(received_at_ms>0),halted boolean NOT NULL,
    PRIMARY KEY(account_id,operation_id),
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED
);
DO $$ DECLARE t text;BEGIN
    FOREACH t IN ARRAY ARRAY['portfolio_policies','portfolio_observations'] LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY scoped_read ON life_financial.%I FOR SELECT USING(life_financial.can_access(account_id))',t);
        EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',t);
    END LOOP;
END; $$;

CREATE FUNCTION life_financial.portfolio_money_at(p_account uuid,p_version bigint) RETURNS jsonb
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
            FROM life_financial.spot_cashflow_facts x WHERE account_id=p_account AND state='APPLIED' AND EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=x.account_id AND o.operation_id=x.source_operation_id AND o.committed_version<=p_version)
        UNION ALL
        SELECT (x.observation->>'fill_at_ms')::bigint,1,x.trade_id::text,x.observation,t.side
            FROM life_financial.spot_fill_facts x JOIN life_financial.send_attempts t USING(account_id,intent_id) WHERE x.account_id=p_account AND EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=x.account_id AND o.operation_id=x.source_operation_id AND o.committed_version<=p_version)
        UNION ALL
        SELECT (x.observation->>'fill_at_ms')::bigint,2,('JOINT:'||(x.observation->>'fill_at_ms')||':TRADE:'||x.trade_id)::text,x.observation,NULL::text
            FROM life_financial.swap_fill_facts x WHERE x.account_id=p_account AND EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=x.account_id AND o.operation_id=x.source_operation_id AND o.committed_version<=p_version)
        UNION ALL
        SELECT x.settled_at_ms,2,('JOINT:'||x.settled_at_ms||':FUNDING:'||x.bill_id)::text,
            jsonb_build_object('signed_payment_quote',x.signed_payment_usdt::text),NULL::text
            FROM life_financial.funding_cash_payments x WHERE x.account_id=p_account AND EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=x.account_id AND o.operation_id=x.source_operation_id AND o.committed_version<=p_version)
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

CREATE FUNCTION life_financial.portfolio_positions_at(p_account uuid,p_version bigint) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.swap_fill_policies;anchor jsonb;trades jsonb;mode text;through_ms bigint;
BEGIN
    SELECT * INTO p FROM life_financial.swap_fill_policies WHERE account_id=p_account;
    IF NOT FOUND THEN RETURN NULL;END IF;
    SELECT jsonb_build_object('long_base',p.opening_long_base::text,'short_base',p.opening_short_base::text,
        'opening_long_entry',p.opening_long_entry::text,'opening_short_entry',p.opening_short_entry::text,
        'observed_at_ms',floor(extract(epoch FROM opened_at)*1000)::bigint) INTO STRICT anchor
        FROM life_financial.spot_capital_policies WHERE account_id=p_account AND policy_hash=p.policy_hash;
    SELECT body->>'position_mode' INTO STRICT mode FROM life_financial.shared_capital_policies WHERE account_id=p_account AND policy_hash=p.policy_hash;
    SELECT COALESCE(jsonb_agg(jsonb_build_object('trade_id',f.trade_id,'intent_id',f.intent_id,'side',t.side,
        'position_action',d.permit->>'position_action','quantity_base',f.observation->>'quantity_base_life',
        'price_usdt',f.observation->>'price_usdt','fee_currency',f.observation->>'fee_currency','signed_fee',f.observation->>'signed_fee',
        'realized_pnl_quote',f.observation->>'realized_pnl_usdt','at_ms',f.observation->'fill_at_ms','sequence',f.observation->'sequence')),'[]'::jsonb),
        greatest((anchor->>'observed_at_ms')::bigint,COALESCE(max((f.observation->>'fill_at_ms')::bigint),0)) INTO trades,through_ms
        FROM life_financial.swap_fill_facts f JOIN life_financial.send_attempts t USING(account_id,intent_id)
        JOIN life_financial.swap_preparations d USING(account_id,intent_id) WHERE f.account_id=p_account AND EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=f.account_id AND o.operation_id=f.source_operation_id AND o.committed_version<=p_version);
    RETURN life_financial.swap_position_replay(anchor,trades,'[]'::jsonb,mode,through_ms,p.max_total_fills);
END; $$;

CREATE FUNCTION life_financial.portfolio_basis(p_account uuid,p_version bigint) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE money jsonb;legs jsonb;r life_financial.carry_checks;snapshot jsonb;latest_ms bigint;
BEGIN
    money:=life_financial.portfolio_money_at(p_account,p_version);
    legs:=life_financial.portfolio_positions_at(p_account,p_version);
    SELECT c.* INTO STRICT r FROM life_financial.carry_checks c JOIN life_financial.operations o USING(account_id,operation_id)
        WHERE c.account_id=p_account AND o.committed_version<=p_version ORDER BY o.committed_version DESC LIMIT 1;
    snapshot:=r.bundle#>'{account,snapshot}';
    IF NOT r.qualified OR r.capital_operation_id IS DISTINCT FROM (SELECT o.operation_id FROM life_financial.operations o
        WHERE o.account_id=p_account AND o.command->>'kind'='SHARED_CAPITAL' AND o.committed_version<=p_version
        ORDER BY o.committed_version DESC LIMIT 1)
        OR (snapshot->>'spot_life_base')::numeric<>(money->>'base')::numeric
        OR (snapshot->>'spot_cash_quote')::numeric<>(money->>'quote')::numeric
        OR (snapshot->>'long_base')::numeric<>(legs->>'long_base')::numeric
        OR (snapshot->>'short_base')::numeric<>(legs->>'short_base')::numeric THEN
        RAISE EXCEPTION 'LIFE_PORTFOLIO_SOURCE_UNQUALIFIED' USING ERRCODE='P0001';END IF;
    SELECT max(at_ms) INTO latest_ms FROM (
        SELECT (f.observation->>'fill_at_ms')::bigint AS at_ms FROM life_financial.swap_fill_facts f JOIN life_financial.operations o
            ON o.account_id=f.account_id AND o.operation_id=f.source_operation_id WHERE f.account_id=p_account AND o.committed_version<=p_version
        UNION ALL SELECT (f.observation->>'fill_at_ms')::bigint FROM life_financial.spot_fill_facts f JOIN life_financial.operations o
            ON o.account_id=f.account_id AND o.operation_id=f.source_operation_id WHERE f.account_id=p_account AND o.committed_version<=p_version
        UNION ALL SELECT (f.command->>'transfer_at_ms')::bigint FROM life_financial.spot_cashflow_facts f JOIN life_financial.operations o
            ON o.account_id=f.account_id AND o.operation_id=f.source_operation_id WHERE f.account_id=p_account AND f.state='APPLIED' AND o.committed_version<=p_version
        UNION ALL SELECT f.settled_at_ms FROM life_financial.funding_cash_payments f JOIN life_financial.operations o
            ON o.account_id=f.account_id AND o.operation_id=f.source_operation_id WHERE f.account_id=p_account AND o.committed_version<=p_version) times;
    IF (snapshot->>'observed_at_ms')::bigint<COALESCE(latest_ms,0) THEN
        RAISE EXCEPTION 'LIFE_PORTFOLIO_SOURCE_UNQUALIFIED' USING ERRCODE='P0001';END IF;
    RETURN jsonb_build_object('money',money,'positions',legs,'carry_operation_id',r.operation_id,'capital_operation_id',r.capital_operation_id,
        'mark',r.bundle#>>'{mark,price_usdt}','index',r.bundle#>>'{index,price_usdt}',
        'collateral_quote',snapshot->>'collateral_quote','observed_at_ms',snapshot->'observed_at_ms');
EXCEPTION WHEN no_data_found THEN RAISE EXCEPTION 'LIFE_PORTFOLIO_SOURCE_UNQUALIFIED' USING ERRCODE='P0001';
END; $$;

CREATE FUNCTION life_financial.portfolio_source_qualified_at(p_account uuid,basis jsonb,at_ms bigint) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.portfolio_policies;r life_financial.carry_checks;cp life_financial.carry_policies;sp jsonb;previous jsonb;t bigint;
BEGIN
    SELECT * INTO STRICT p FROM life_financial.portfolio_policies WHERE account_id=p_account;
    SELECT * INTO STRICT r FROM life_financial.carry_checks WHERE account_id=p_account AND operation_id=basis->>'carry_operation_id';
    SELECT * INTO STRICT cp FROM life_financial.carry_policies WHERE account_id=p_account;
    SELECT body INTO STRICT sp FROM life_financial.shared_capital_policies WHERE account_id=p_account;
    SELECT bundle INTO previous FROM life_financial.carry_checks WHERE account_id=p_account AND operation_id=r.previous_qualified;
    FOREACH t IN ARRAY ARRAY[(r.bundle#>>'{account,snapshot,observed_at_ms}')::bigint,(r.bundle#>>'{mark,observed_at_ms}')::bigint,
        (r.bundle#>>'{index,observed_at_ms}')::bigint,(r.bundle#>>'{funding,observed_at_ms}')::bigint,(r.bundle#>>'{terms,observed_at_ms}')::bigint] LOOP
        IF t IS NULL OR t>at_ms OR at_ms-t>p.max_age_ms THEN RETURN false;END IF;
    END LOOP;
    RETURN r.qualified AND life_financial.carry_bundle_reason(cp.body,sp,r.bundle,previous,r.bundle#>'{account,snapshot}',at_ms,cp.max_payments)='CARRY_QUALIFIED';
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.portfolio_source_current(p_account uuid,basis jsonb,at_ms bigint) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT life_financial.portfolio_source_qualified_at(p_account,basis,at_ms)
    AND life_financial.funding_carry_current(p_account,r.bundle)
    AND life_financial.swap_snapshot_current(p_account,r.bundle#>'{account,snapshot}')
    FROM life_financial.carry_checks r WHERE r.account_id=p_account AND r.operation_id=basis->>'carry_operation_id';
$$;

CREATE FUNCTION life_financial.portfolio_measure_basis(p_account uuid,basis jsonb,prior_highwater numeric) RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
SELECT life_financial.portfolio_measure((basis#>>'{money,quote}')::numeric,(basis#>>'{money,base}')::numeric,
    (basis#>>'{positions,long_base}')::numeric,(basis#>>'{positions,short_base}')::numeric,
    (basis#>>'{positions,long_entry}')::numeric,(basis#>>'{positions,short_entry}')::numeric,
    (basis->>'mark')::numeric,(basis->>'index')::numeric,(basis#>>'{money,net}')::numeric,prior_highwater);
$$;
CREATE FUNCTION life_financial.portfolio_opening_highwater(p_account uuid) RETURNS numeric
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT (life_financial.portfolio_measure(c.opening_quote,c.opening_base,s.opening_long_base,s.opening_short_base,
    s.opening_long_entry,s.opening_short_entry,p.opening_mark,c.opening_price,0,1)->>'nav_quote')::numeric
    FROM life_financial.portfolio_policies p JOIN life_financial.spot_capital_policies c USING(account_id,policy_hash)
    JOIN life_financial.swap_fill_policies s USING(account_id,policy_hash) WHERE p.account_id=p_account;
$$;

CREATE FUNCTION life_financial.portfolio_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE p life_financial.portfolio_policies;r life_financial.portfolio_observations;o life_financial.operations;
    basis jsonb;expected jsonb;highwater numeric;last_ms bigint:=0;count_rows integer:=0;bytes bigint:=0;
BEGIN
    SELECT * INTO p FROM life_financial.portfolio_policies WHERE account_id=p_account;
    IF NOT FOUND THEN RETURN NOT EXISTS(SELECT 1 FROM life_financial.portfolio_observations WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=p_account AND command->>'kind'='PORTFOLIO_NAV');END IF;
    IF p.policy_hash IS DISTINCT FROM (SELECT policy_hash FROM life_financial.accounts WHERE account_id=p_account)
        OR NOT life_financial.carry_verified(p_account) OR NOT life_financial.shared_capital_verified(p_account)
        OR NOT life_financial.spot_capital_verified(p_account) THEN RETURN false;END IF;
    highwater:=life_financial.portfolio_opening_highwater(p_account);
    IF highwater IS NULL OR highwater<=0 THEN RETURN false;END IF;
    FOR r IN SELECT n.* FROM life_financial.portfolio_observations n JOIN life_financial.operations op USING(account_id,operation_id)
        WHERE n.account_id=p_account ORDER BY op.committed_version LOOP
        count_rows:=count_rows+1;bytes:=bytes+octet_length(r.basis::text)+octet_length(r.measurement::text);
        SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=r.operation_id;
        basis:=life_financial.portfolio_basis(p_account,o.committed_version-1);
        expected:=life_financial.portfolio_measure_basis(p_account,basis,highwater);
        IF count_rows>p.max_observations OR bytes>p.max_total_bytes OR r.basis IS DISTINCT FROM basis OR r.measurement IS DISTINCT FROM expected
            OR (basis->>'observed_at_ms')::bigint<last_ms
            OR o.command->>'kind' IS DISTINCT FROM 'PORTFOLIO_NAV' OR o.command->'command' IS DISTINCT FROM jsonb_build_object('operation_id',r.operation_id)
            OR (o.command->>'version')::bigint<>o.committed_version-1
            OR o.fingerprint<>encode(sha256(convert_to(life_financial.canonical_json_v1(o.command),'UTF8')),'hex')
            OR r.received_at_ms<>floor(extract(epoch FROM o.recorded_at)*1000)::bigint
            OR r.halted IS DISTINCT FROM ((expected->>'drawdown_bps')::numeric>=p.max_drawdown_bps)
            OR o.receipt IS DISTINCT FROM jsonb_build_object('kind','PORTFOLIO_NAV','operation_id',r.operation_id,'measurement',expected,
                'halted',r.halted,'version',o.committed_version,'replayed',false,'trading_permission',false) THEN RETURN false;END IF;
        highwater:=(expected->>'highwater_quote')::numeric;last_ms:=(basis->>'observed_at_ms')::bigint;
        -- Qualify original source freshness at the original primary receipt time.
        IF NOT life_financial.portfolio_source_qualified_at(p_account,basis,r.received_at_ms) THEN RETURN false;END IF;
    END LOOP;
    RETURN NOT EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=p_account AND o.command->>'kind'='PORTFOLIO_NAV'
        AND NOT EXISTS(SELECT 1 FROM life_financial.portfolio_observations n WHERE n.account_id=o.account_id AND n.operation_id=o.operation_id));
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.portfolio_command(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,cmd jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts;p life_financial.portfolio_policies;previous life_financial.operations;n life_financial.portfolio_observations;
    op text;request jsonb;result jsonb;view jsonb;basis jsonb;measurement jsonb;highwater numeric;source_time timestamptz;at_ms bigint;should_halt boolean;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR jsonb_typeof(cmd) IS DISTINCT FROM 'object'
        OR (SELECT count(*) FROM jsonb_object_keys(cmd))<>1 OR jsonb_typeof(cmd->'operation_id') IS DISTINCT FROM 'string'
        OR cmd->>'operation_id' !~ '^[A-Za-z0-9_.:-]{1,96}$' THEN RAISE EXCEPTION 'LIFE_PORTFOLIO_COMMAND_INVALID' USING ERRCODE='P0001';END IF;
    op:=cmd->>'operation_id';request:=jsonb_build_object('kind','PORTFOLIO_NAV','binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',cmd);
    IF p_fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(request),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'LIFE_PORTFOLIO_FINGERPRINT_INVALID' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,a.version);
    IF NOT life_financial.portfolio_verified(p_account) THEN RAISE EXCEPTION 'LIFE_PORTFOLIO_UNVERIFIED' USING ERRCODE='P0001';END IF;
    SELECT * INTO previous FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF previous.command<>request OR previous.fingerprint<>p_fingerprint THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT' USING ERRCODE='P0001';END IF;
        RETURN previous.receipt||'{"replayed":true}'::jsonb;END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    view:=life_financial.spot_state(p_account);
    IF view->>'digest' IS DISTINCT FROM p_cut THEN RAISE EXCEPTION 'LIFE_SPOT_CUT_CONFLICT' USING ERRCODE='P0001';END IF;
    IF (view->>'unverified')::boolean OR (view->>'financial_recovery_required')::boolean THEN
        RAISE EXCEPTION 'LIFE_PORTFOLIO_UNVERIFIED' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT p FROM life_financial.portfolio_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
    IF (SELECT count(*) FROM life_financial.portfolio_observations WHERE account_id=p_account)>=p.max_observations THEN
        RAISE EXCEPTION 'LIFE_PORTFOLIO_OBSERVATION_BOUND' USING ERRCODE='P0001';END IF;
    source_time:=clock_timestamp();at_ms:=floor(extract(epoch FROM source_time)*1000)::bigint;
    basis:=life_financial.portfolio_basis(p_account,p_version);
    IF NOT life_financial.portfolio_source_current(p_account,basis,at_ms) THEN
        RAISE EXCEPTION 'LIFE_PORTFOLIO_SOURCE_UNQUALIFIED' USING ERRCODE='P0001';END IF;
    SELECT x.* INTO n FROM life_financial.portfolio_observations x JOIN life_financial.operations o USING(account_id,operation_id)
        WHERE x.account_id=p_account ORDER BY o.committed_version DESC LIMIT 1;
    IF FOUND THEN
        highwater:=(n.measurement->>'highwater_quote')::numeric;
        IF (basis->>'observed_at_ms')::bigint<(n.basis->>'observed_at_ms')::bigint THEN
            RAISE EXCEPTION 'LIFE_PORTFOLIO_SOURCE_UNQUALIFIED' USING ERRCODE='P0001';END IF;
    ELSE highwater:=life_financial.portfolio_opening_highwater(p_account);END IF;
    measurement:=life_financial.portfolio_measure_basis(p_account,basis,highwater);
    IF (SELECT COALESCE(sum(octet_length(x.basis::text)+octet_length(x.measurement::text)),0) FROM life_financial.portfolio_observations x
        WHERE x.account_id=p_account)+octet_length(basis::text)+octet_length(measurement::text)>p.max_total_bytes THEN
        RAISE EXCEPTION 'LIFE_PORTFOLIO_OBSERVATION_BOUND' USING ERRCODE='P0001';END IF;
    should_halt:=(measurement->>'drawdown_bps')::numeric>=p.max_drawdown_bps;
    INSERT INTO life_financial.portfolio_observations VALUES(p_account,op,basis,measurement,at_ms,should_halt);
    UPDATE life_financial.accounts SET version=version+1,halted=accounts.halted OR should_halt WHERE account_id=p_account;
    result:=jsonb_build_object('kind','PORTFOLIO_NAV','operation_id',op,'measurement',measurement,'halted',should_halt,
        'version',p_version+1,'replayed',false,'trading_permission',false);
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version,recorded_at)
        VALUES(p_account,op,p_fingerprint,request,result,p_version+1,source_time);
    RETURN result;
END; $$;

CREATE FUNCTION life_financial.portfolio_read(p_account uuid,p_rows integer,p_bytes integer,p_operation text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE n life_financial.portfolio_observations;result jsonb;basis jsonb;version bigint;is_current boolean:=false;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_ACCESS_DENIED' USING ERRCODE='P0001';END IF;
    IF p_rows IS NULL OR p_bytes IS NULL OR p_rows NOT BETWEEN 1 AND 4096 OR p_bytes NOT BETWEEN 1 AND 16777216
        OR (SELECT count(*) FROM life_financial.portfolio_observations WHERE account_id=p_account)>p_rows THEN
        RAISE EXCEPTION 'LIFE_PORTFOLIO_READ_BOUND' USING ERRCODE='P0001';END IF;
    IF NOT life_financial.portfolio_verified(p_account) THEN RAISE EXCEPTION 'LIFE_PORTFOLIO_UNVERIFIED' USING ERRCODE='P0001';END IF;
    IF p_operation IS NOT NULL THEN
        SELECT jsonb_build_object('command',o.command,'receipt',o.receipt) INTO result FROM life_financial.operations o
            WHERE o.account_id=p_account AND o.operation_id=p_operation AND o.command->>'kind'='PORTFOLIO_NAV';
    ELSE
        SELECT x.* INTO n FROM life_financial.portfolio_observations x JOIN life_financial.operations o USING(account_id,operation_id)
            WHERE x.account_id=p_account ORDER BY o.committed_version DESC LIMIT 1;
        IF FOUND THEN
            SELECT a.version INTO STRICT version FROM life_financial.accounts a WHERE a.account_id=p_account;
            BEGIN
                basis:=life_financial.portfolio_basis(p_account,version);
                is_current:=basis=n.basis AND life_financial.portfolio_source_current(p_account,basis,floor(extract(epoch FROM clock_timestamp())*1000)::bigint);
            EXCEPTION WHEN raise_exception THEN is_current:=false;END;
            result:=jsonb_build_object('operation_id',n.operation_id,'measurement',n.measurement,'basis',n.basis,'current',is_current,'trading_permission',false);
        ELSE result:=jsonb_build_object('current',false,'trading_permission',false);END IF;
    END IF;
    IF octet_length(result::text)>p_bytes THEN RAISE EXCEPTION 'LIFE_PORTFOLIO_READ_BOUND' USING ERRCODE='P0001';END IF;
    RETURN result;
END; $$;

CREATE FUNCTION life_financial.spot_state_before_portfolio(p_account uuid) RETURNS jsonb
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
    IF op.command->>'kind' IN ('PORTFOLIO_NAV','PAID_FUNDING','SWAP_FILL','SWAP_ACK','CARRY_CHECK','RECORDER_POSITION_STAGE','RECORDER_POSITION_APPLY','RECORDER_EVENT_STAGE','RECORDER_EVENT_APPLY','RECORDER_CONFIG_STAGE','RECORDER_CONFIG_APPLY','RECORDER_STAGE','RECORDER_APPLY','SPOT_MARKOUT','SPOT_INVENTORY_SETTLE','SPOT_ACK','SPOT_FILL','SPOT_CASHFLOW','SPOT_NAV','SPOT_SESSION_TICK','SPOT_LIFECYCLE','SPOT_TERMINAL') OR op.command->>'kind' IN ('SPOT_ACTION_TRANSITION','SPOT_ACTION_ABANDON')
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
CREATE OR REPLACE FUNCTION life_financial.spot_state(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b jsonb;verified boolean;
BEGIN
    b:=life_financial.spot_state_before_portfolio(p_account)-'digest'-'observed_at';verified:=life_financial.portfolio_verified(p_account);
    b:=b||jsonb_build_object('portfolio',jsonb_build_object('verified',verified,
        'policy',(SELECT to_jsonb(p) FROM life_financial.portfolio_policies p WHERE p.account_id=p_account),
        'observations',COALESCE((SELECT jsonb_agg(to_jsonb(n) ORDER BY operation_id) FROM life_financial.portfolio_observations n WHERE n.account_id=p_account),'[]'::jsonb)),
        'financial_recovery_required',(b->>'financial_recovery_required')::boolean OR NOT verified,
        'recovery_required',(b->>'recovery_required')::boolean OR NOT verified,'unverified',(b->>'unverified')::boolean OR NOT verified);
    RETURN b||jsonb_build_object('digest',encode(sha256(convert_to(b::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
END; $$;
CREATE FUNCTION life_financial.portfolio_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE o life_financial.operations;n life_financial.portfolio_observations;
BEGIN
    IF NOT life_financial.portfolio_verified(NEW.account_id) THEN RAISE EXCEPTION 'LIFE_PORTFOLIO_UNVERIFIED_AT_COMMIT' USING ERRCODE='P0001';END IF;
    SELECT * INTO o FROM life_financial.operations WHERE account_id=NEW.account_id AND committed_version=NEW.version;
    IF o.command->>'kind'='PORTFOLIO_NAV' THEN
        SELECT * INTO STRICT n FROM life_financial.portfolio_observations WHERE account_id=NEW.account_id AND operation_id=o.operation_id;
        IF NOT life_financial.portfolio_source_current(NEW.account_id,n.basis,floor(extract(epoch FROM clock_timestamp())*1000)::bigint) THEN
            RAISE EXCEPTION 'LIFE_PORTFOLIO_SOURCE_STALE_AT_COMMIT' USING ERRCODE='P0001';END IF;
    END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER portfolio_account_commit AFTER UPDATE ON life_financial.accounts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN(NEW.version<>OLD.version) EXECUTE FUNCTION life_financial.portfolio_commit_guard();
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
