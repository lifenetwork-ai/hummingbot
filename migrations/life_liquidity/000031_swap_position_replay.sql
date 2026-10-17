-- Private causal SWAP arithmetic. No wallet writer, source authentication or
-- execution authority is installed by these functions. The joint settlement
-- command must supply verified immutable native facts before publishing money.

-- Match the File joint replay's Decimal(precision=28, ROUND_HALF_EVEN) at each
-- operation. Integer quotient/remainder determines ties exactly: neither a
-- NUMERIC division's selected scale nor a logarithm determines rounding.
CREATE FUNCTION life_financial.joint_decimal28(n numeric,d numeric DEFAULT 1) RETURNS numeric
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE a numeric;b numeric;exponent integer;places integer;whole numeric;rest numeric;direction integer;
BEGIN
    IF n IS NULL OR d IS NULL OR NOT (n>'-Infinity'::numeric AND n<'Infinity'::numeric)
        OR NOT (d>'-Infinity'::numeric AND d<'Infinity'::numeric) OR d=0
        OR abs(n)>=1e200::numeric OR abs(d)>=1e200::numeric OR scale(n)>200 OR scale(d)>200 THEN
        RAISE EXCEPTION 'LIFE_JOINT_DECIMAL_INVALID' USING ERRCODE='P0001'; END IF;
    IF n=0 THEN RETURN 0; END IF;
    direction:=CASE WHEN (n<0) <> (d<0) THEN -1 ELSE 1 END;a:=abs(n);b:=abs(d);
    -- Length difference gives an upper candidate for floor(log10(a/b)).
    -- Leading fractional zeros are handled by scaling to integers first.
    places:=greatest(scale(a),scale(b));a:=a*power(10::numeric,places);b:=b*power(10::numeric,places);
    exponent:=length(trunc(a)::text)-length(trunc(b)::text);
    IF exponent>=0 THEN
        IF a<b*power(10::numeric,exponent) THEN exponent:=exponent-1; END IF;
    ELSIF a*power(10::numeric,-exponent)<b THEN exponent:=exponent-1; END IF;
    places:=27-exponent;
    IF places>=0 THEN a:=a*power(10::numeric,places);ELSE b:=b*power(10::numeric,-places);END IF;
    whole:=div(a,b);rest:=mod(a,b);
    IF rest*2>b OR rest*2=b AND mod(whole,2)<>0 THEN whole:=whole+1;END IF;
    RETURN trim_scale(direction*whole*power(10::numeric,-places));
END; $$;

CREATE FUNCTION life_financial.swap_position_replay(anchor jsonb,trades jsonb,payments jsonb,
    mode text,through_ms bigint,max_events integer) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE t jsonb;p jsonb;k text;leg text;size numeric;entry numeric;quantity numeric;price numeric;fee numeric;pnl numeric;
    long_size numeric;short_size numeric;long_entry numeric;short_entry numeric;cash numeric:=0;fees numeric:=0;
    realized numeric:=0;funding numeric:=0;loss numeric:=0;at_ms bigint;start_ms bigint;events jsonb:='[]'::jsonb;
    ids text[]:=ARRAY[]::text[];bills text[]:=ARRAY[]::text[];
BEGIN
    IF max_events IS NULL OR max_events NOT BETWEEN 1 AND 100000 OR jsonb_typeof(trades) IS DISTINCT FROM 'array'
        OR jsonb_typeof(payments) IS DISTINCT FROM 'array' OR jsonb_array_length(trades)+jsonb_array_length(payments)>max_events THEN
        RAISE EXCEPTION 'LIFE_JOINT_REPLAY_BOUND' USING ERRCODE='P0001'; END IF;
    IF mode IS NULL OR mode NOT IN ('ONEWAY','HEDGE') OR jsonb_typeof(anchor) IS DISTINCT FROM 'object'
        OR (SELECT count(*) FROM jsonb_object_keys(anchor))<>5
        OR NOT anchor ?& ARRAY['long_base','short_base','opening_long_entry','opening_short_entry','observed_at_ms']
        OR anchor->>'observed_at_ms' !~ '^[1-9][0-9]*$' OR jsonb_typeof(anchor->'observed_at_ms') IS DISTINCT FROM 'number'
        OR through_ms IS NULL OR through_ms>253402300799999 THEN
        RAISE EXCEPTION 'LIFE_JOINT_ANCHOR_INVALID' USING ERRCODE='P0001'; END IF;
    BEGIN
        FOREACH k IN ARRAY ARRAY['long_base','short_base','opening_long_entry','opening_short_entry'] LOOP
            IF jsonb_typeof(anchor->k) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'invalid';END IF;
            PERFORM (anchor->>k)::life_financial.amount;
        END LOOP;
        long_size:=(anchor->>'long_base')::numeric;short_size:=(anchor->>'short_base')::numeric;
        long_entry:=(anchor->>'opening_long_entry')::numeric;short_entry:=(anchor->>'opening_short_entry')::numeric;
        start_ms:=(anchor->>'observed_at_ms')::bigint;
        IF start_ms>through_ms OR long_size>0 AND long_entry=0 OR short_size>0 AND short_entry=0
            OR mode='ONEWAY' AND long_size>0 AND short_size>0 THEN RAISE EXCEPTION 'invalid';END IF;
    EXCEPTION WHEN OTHERS THEN RAISE EXCEPTION 'LIFE_JOINT_ANCHOR_INVALID' USING ERRCODE='P0001';END;
    -- Validate all rows before casting or choosing their causal order. IDs
    -- establish permanence; they never provide exchange chronology.
    FOR t IN SELECT value FROM jsonb_array_elements(trades) LOOP
        BEGIN
            IF jsonb_typeof(t) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(t))<>11
                OR NOT t ?& ARRAY['trade_id','intent_id','side','position_action','quantity_base','price_usdt','fee_currency',
                    'signed_fee','realized_pnl_quote','at_ms','sequence'] THEN RAISE EXCEPTION 'invalid';END IF;
        EXCEPTION WHEN OTHERS THEN RAISE EXCEPTION 'LIFE_JOINT_TRADE_INVALID' USING ERRCODE='P0001';END;
    END LOOP;
    FOR t IN SELECT value FROM jsonb_array_elements(trades) LOOP
        BEGIN
            IF t->>'side' IS NULL OR t->>'side' NOT IN ('BUY','SELL') OR t->>'position_action' IS NULL
                OR t->>'position_action' NOT IN ('OPEN','CLOSE') OR t->'fee_currency' IS DISTINCT FROM '"USDT"'::jsonb
                OR jsonb_typeof(t->'trade_id') IS DISTINCT FROM 'string' OR t->>'trade_id' !~ '^[A-Za-z0-9_.-]{1,64}$'
                OR jsonb_typeof(t->'intent_id') IS DISTINCT FROM 'string' OR t->>'intent_id' !~ '^[A-Za-z0-9_.:-]{1,128}$'
                OR jsonb_typeof(t->'at_ms') IS DISTINCT FROM 'number' OR t->>'at_ms' !~ '^[1-9][0-9]*$'
                OR t->'sequence' IS DISTINCT FROM 'null'::jsonb AND (jsonb_typeof(t->'sequence') IS DISTINCT FROM 'number'
                    OR t->>'sequence' !~ '^(0|[1-9][0-9]*)$' OR (t->>'sequence')::numeric>9223372036854775807) THEN RAISE EXCEPTION 'invalid';END IF;
            FOREACH k IN ARRAY ARRAY['quantity_base','price_usdt','signed_fee','realized_pnl_quote'] LOOP
                IF jsonb_typeof(t->k) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'invalid';END IF;
                PERFORM (t->>k)::life_financial.signed_amount;
            END LOOP;
            IF (t->>'quantity_base')::numeric<=0 OR (t->>'price_usdt')::numeric<=0
                OR (t->>'at_ms')::numeric NOT BETWEEN start_ms AND through_ms THEN RAISE EXCEPTION 'invalid';END IF;
        EXCEPTION WHEN OTHERS THEN RAISE EXCEPTION 'LIFE_JOINT_TRADE_INVALID' USING ERRCODE='P0001';END;
        IF t->>'trade_id'=ANY(ids) THEN RAISE EXCEPTION 'LIFE_JOINT_TRADE_IDENTITY_CONFLICT' USING ERRCODE='P0001';END IF;
        ids:=array_append(ids,t->>'trade_id');
    END LOOP;
    IF EXISTS(SELECT 1 FROM jsonb_array_elements(trades) AS items(item) GROUP BY item->>'at_ms'
        HAVING (count(*)>1 OR count(*) FILTER(WHERE item->'sequence'<>'null'::jsonb)>0)
            AND (count(*) FILTER(WHERE item->'sequence'='null'::jsonb)>0 OR count(DISTINCT item->>'sequence')<>count(*))) THEN
        RAISE EXCEPTION 'LIFE_JOINT_TRADE_ORDER_AMBIGUOUS' USING ERRCODE='P0001';END IF;
    FOR t IN SELECT value FROM jsonb_array_elements(trades) ORDER BY (value->>'at_ms')::bigint,(value->>'sequence')::bigint NULLS FIRST LOOP
        quantity:=(t->>'quantity_base')::numeric;price:=(t->>'price_usdt')::numeric;
        fee:=(t->>'signed_fee')::numeric;pnl:=(t->>'realized_pnl_quote')::numeric;at_ms:=(t->>'at_ms')::bigint;
        leg:=CASE WHEN t->>'position_action'='OPEN' THEN CASE WHEN t->>'side'='BUY' THEN 'long' ELSE 'short' END
            ELSE CASE WHEN t->>'side'='SELL' THEN 'long' ELSE 'short' END END;
        size:=CASE WHEN leg='long' THEN long_size ELSE short_size END;entry:=CASE WHEN leg='long' THEN long_entry ELSE short_entry END;
        IF t->>'position_action'='OPEN' THEN
            IF pnl<>0 THEN RAISE EXCEPTION 'LIFE_JOINT_OPEN_PNL_INVALID' USING ERRCODE='P0001';END IF;
            entry:=life_financial.joint_decimal28(life_financial.joint_decimal28(life_financial.joint_decimal28(entry*size)
                +life_financial.joint_decimal28(price*quantity)),life_financial.joint_decimal28(size+quantity));
            size:=life_financial.joint_decimal28(size+quantity);
        ELSE
            IF size<quantity OR pnl<>life_financial.joint_decimal28(life_financial.joint_decimal28(price-entry)*quantity)
                *(CASE WHEN leg='long' THEN 1 ELSE -1 END) THEN RAISE EXCEPTION 'LIFE_JOINT_CLOSE_PNL_INVALID' USING ERRCODE='P0001';END IF;
            size:=life_financial.joint_decimal28(size-quantity);
        END IF;
        IF leg='long' THEN long_size:=size;long_entry:=entry;ELSE short_size:=size;short_entry:=entry;END IF;
        IF mode='ONEWAY' AND long_size>0 AND short_size>0 THEN RAISE EXCEPTION 'LIFE_JOINT_ONEWAY_REVERSE_INVALID' USING ERRCODE='P0001';END IF;
        realized:=life_financial.joint_decimal28(realized+pnl);fees:=life_financial.joint_decimal28(fees+fee);
        cash:=life_financial.joint_decimal28(cash+life_financial.joint_decimal28(pnl+fee));
        loss:=life_financial.joint_decimal28(loss+greatest(0,-pnl)+greatest(0,-fee));
        events:=events||jsonb_build_array(jsonb_build_object('event_id','JOINT:'||at_ms||':TRADE:'||(t->>'trade_id'),
            'at_ms',at_ms,'signed_amount_usdt',life_financial.joint_decimal28(pnl+fee)::text));
    END LOOP;
    FOR p IN SELECT value FROM jsonb_array_elements(payments) LOOP
        BEGIN
            IF jsonb_typeof(p) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(p))<>3
                OR NOT p ?& ARRAY['bill_id','signed_amount_quote','at_ms'] OR jsonb_typeof(p->'bill_id') IS DISTINCT FROM 'string'
                OR p->>'bill_id' !~ '^[0-9]{1,64}$' OR p->>'bill_id'=ANY(bills) OR jsonb_typeof(p->'at_ms') IS DISTINCT FROM 'number'
                OR p->>'at_ms' !~ '^[1-9][0-9]*$' OR (p->>'at_ms')::numeric NOT BETWEEN start_ms AND through_ms
                OR jsonb_typeof(p->'signed_amount_quote') IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'invalid';END IF;
            fee:=(p->>'signed_amount_quote')::life_financial.signed_amount;at_ms:=(p->>'at_ms')::bigint;
        EXCEPTION WHEN OTHERS THEN RAISE EXCEPTION 'LIFE_JOINT_FUNDING_INVALID' USING ERRCODE='P0001';END;
        bills:=array_append(bills,p->>'bill_id');
    END LOOP;
    FOR p IN SELECT value FROM jsonb_array_elements(payments) ORDER BY (value->>'at_ms')::bigint,value->>'bill_id' COLLATE "C" LOOP
        fee:=(p->>'signed_amount_quote')::numeric;at_ms:=(p->>'at_ms')::bigint;funding:=life_financial.joint_decimal28(funding+fee);cash:=life_financial.joint_decimal28(cash+fee);
        events:=events||jsonb_build_array(jsonb_build_object('event_id','JOINT:'||at_ms||':FUNDING:'||(p->>'bill_id'),
            'at_ms',at_ms,'signed_amount_usdt',fee::text));
    END LOOP;
    RETURN jsonb_build_object('long_base',long_size::text,'short_base',short_size::text,'long_entry',long_entry::text,'short_entry',short_entry::text,
        'realized_pnl_quote',realized::text,'signed_fees_quote',fees::text,'paid_funding_quote',funding::text,
        'cash_delta_quote',cash::text,'execution_loss_quote',loss::text,'events',events,'trading_permission',false);
END; $$;

REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
