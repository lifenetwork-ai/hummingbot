-- Distinct static time/identity denial codes. Unchanged amounts, clocks and age thresholds.
CREATE OR REPLACE FUNCTION life_financial.guard_quote_admission(p_account uuid,p_binding jsonb,p_action jsonb,p_prepare jsonb,p_facts jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p life_financial.spot_quote_admission_policies; cfg jsonb; at timestamptz; observed timestamptz; event_at timestamptz;
    quantity life_financial.positive_amount; price life_financial.positive_amount; exit_value life_financial.positive_amount;
    reference life_financial.positive_amount; bid life_financial.positive_amount; ask life_financial.positive_amount;
    maker life_financial.signed_amount; exit_fee life_financial.signed_amount; impact life_financial.amount;
    carry life_financial.amount; inventory life_financial.amount; net numeric; subsidy numeric; item text;
BEGIN
    SELECT p0.* INTO p FROM life_financial.spot_quote_admission_policies p0 JOIN life_financial.accounts a USING(account_id,policy_hash) WHERE a.account_id=p_account;
    IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_QUOTE_ADMISSION_POLICY_REQUIRED' USING ERRCODE='P0001'; END IF;
    IF jsonb_typeof(p_facts) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(p_facts))<>16
        OR NOT p_facts ?& ARRAY['binding','observed_at','reference_at','exit_value_at','fee_at','reference_price_usdt','exit_value_usdt',
            'best_bid_usdt','best_ask_usdt','maker_fee_rate','exit_fee_rate','impact_cost_usdt','carry_cost_usdt','inventory_risk_usdt',
            'exit_value_includes_impact','exit_source_kind']
        OR EXISTS(SELECT 1 FROM jsonb_each(p_facts) WHERE value='null'::jsonb)
        OR p_facts->'binding' IS DISTINCT FROM p_binding OR p_facts->>'exit_source_kind'<>'independent_market'
        OR jsonb_typeof(p_facts->'exit_value_includes_impact')<>'boolean'
        OR EXISTS(SELECT 1 FROM jsonb_each(p_facts) WHERE key NOT IN ('binding','exit_value_includes_impact') AND jsonb_typeof(value)<>'string') THEN
        RAISE EXCEPTION 'LIFE_QUOTE_ADMISSION_FACTS_INVALID' USING ERRCODE='P0001'; END IF;
    at:=clock_timestamp(); observed:=(p_facts->>'observed_at')::timestamptz;
    IF NOT isfinite(observed) OR observed>at OR extract(epoch FROM at-observed)*1000>p.max_fact_age_ms THEN
        RAISE EXCEPTION 'LIFE_QUOTE_ADMISSION_STALE' USING ERRCODE='P0001'; END IF;
    FOREACH item IN ARRAY ARRAY['reference_at','exit_value_at','fee_at'] LOOP
        event_at:=(p_facts->>item)::timestamptz;
        IF NOT isfinite(event_at) OR event_at>observed OR extract(epoch FROM at-event_at)*1000>
            (CASE WHEN item='fee_at' THEN p.max_fee_age_ms ELSE p.max_reference_age_ms END) THEN
            RAISE EXCEPTION 'LIFE_QUOTE_SOURCE_STALE' USING ERRCODE='P0001'; END IF;
    END LOOP;
    cfg:=(p_action->>'payload_json')::jsonb;
    IF jsonb_typeof(cfg) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(cfg))<>17
        OR NOT cfg ?& ARRAY['id','type','timestamp','controller_id','trading_pair','connector_name','side','amount','position_action','price',
            'chaser_config','execution_strategy','leverage','level_id','slippage_pct','slippage_multiplier','max_slippage_pct']
        OR EXISTS(SELECT 1 FROM jsonb_each(cfg) WHERE key<>'chaser_config' AND value='null'::jsonb)
        OR EXISTS(SELECT 1 FROM jsonb_each(cfg) WHERE key NOT IN ('timestamp','side','leverage','chaser_config') AND jsonb_typeof(value)<>'string')
        OR jsonb_typeof(cfg->'timestamp')<>'number'
        OR cfg->>'id' IS DISTINCT FROM p_action->>'intent_id' OR cfg->>'controller_id' IS DISTINCT FROM p_action->>'controller_id'
        OR cfg->>'type'<>'order_executor' OR cfg->>'connector_name'<>'okx' OR cfg->>'trading_pair'<>'LIFE-USDT'
        OR cfg->>'execution_strategy'<>'LIMIT_MAKER' OR cfg->>'position_action'<>'OPEN' OR cfg->'leverage'<>'1'::jsonb
        OR cfg->'chaser_config'<>'null'::jsonb OR cfg->'side'<>(CASE p_action->>'side' WHEN 'BUY' THEN '1'::jsonb ELSE '2'::jsonb END)
        OR (cfg->>'slippage_pct')::life_financial.positive_amount>(cfg->>'max_slippage_pct')::life_financial.positive_amount
        OR (cfg->>'slippage_multiplier')::life_financial.positive_amount<=1
        OR cfg->>'level_id' IS DISTINCT FROM p_action->>'level'
        OR (cfg->>'amount')::life_financial.positive_amount<>(p_prepare->>'quantity_base')::life_financial.positive_amount
        OR (cfg->>'price')::life_financial.positive_amount<>(p_prepare->>'limit_price_usdt')::life_financial.positive_amount THEN
        RAISE EXCEPTION 'LIFE_QUOTE_CAPTURE_MISMATCH' USING ERRCODE='P0001'; END IF;
    IF (cfg->>'timestamp')::life_financial.positive_amount>extract(epoch FROM observed) THEN
        RAISE EXCEPTION 'LIFE_QUOTE_CAPTURE_FUTURE' USING ERRCODE='P0001'; END IF;
    IF extract(epoch FROM at)*1000-(cfg->>'timestamp')::numeric*1000>p.max_capture_age_ms THEN
        RAISE EXCEPTION 'LIFE_QUOTE_CAPTURE_STALE' USING ERRCODE='P0001'; END IF;
    quantity:=(p_prepare->>'quantity_base')::life_financial.positive_amount;
    price:=(p_prepare->>'limit_price_usdt')::life_financial.positive_amount;
    reference:=(p_facts->>'reference_price_usdt')::life_financial.positive_amount;
    exit_value:=(p_facts->>'exit_value_usdt')::life_financial.positive_amount;
    bid:=(p_facts->>'best_bid_usdt')::life_financial.positive_amount; ask:=(p_facts->>'best_ask_usdt')::life_financial.positive_amount;
    maker:=(p_facts->>'maker_fee_rate')::life_financial.signed_amount; exit_fee:=(p_facts->>'exit_fee_rate')::life_financial.signed_amount;
    impact:=(p_facts->>'impact_cost_usdt')::life_financial.amount; carry:=(p_facts->>'carry_cost_usdt')::life_financial.amount;
    inventory:=(p_facts->>'inventory_risk_usdt')::life_financial.amount;
    IF (p_prepare->>'reference_price_usdt')::life_financial.positive_amount<>reference OR bid>=ask
        OR mod(price,p.tick_size)<>0 OR mod(quantity,p.lot_size)<>0 OR quantity<p.min_size
        OR p_action->>'side'='BUY' AND price>=ask OR p_action->>'side'='SELL' AND price<=bid
        OR (p_facts->>'exit_value_includes_impact')::boolean AND impact>0
        OR (p_prepare->>'fee_buffer_quote_usdt')::life_financial.amount<greatest(0,maker)*price*quantity
        OR (p_prepare->>'fee_buffer_base_life')::life_financial.amount<greatest(0,maker)*quantity THEN
        RAISE EXCEPTION 'LIFE_QUOTE_RULE_BOOK_OR_FEES_DENIED' USING ERRCODE='P0001'; END IF;
    net:=CASE p_action->>'side' WHEN 'BUY' THEN exit_value-price ELSE price-exit_value END*quantity
        -greatest(0,maker)*price*quantity-greatest(0,exit_fee)*exit_value*quantity-impact-carry-inventory-p.uncertainty_bps*0.0001*exit_value*quantity;
    subsidy:=CASE WHEN p.objective='liquidity_service' THEN greatest(0,-net) ELSE 0 END;
    IF p.objective='profit_mm' AND net<p.min_net_edge_bps*0.0001*exit_value*quantity
        OR (p_prepare->>'subsidy_quote_usdt')::life_financial.amount<>subsidy THEN
        RAISE EXCEPTION 'LIFE_QUOTE_ECONOMICS_DENIED' USING ERRCODE='P0001'; END IF;
END; $$;
