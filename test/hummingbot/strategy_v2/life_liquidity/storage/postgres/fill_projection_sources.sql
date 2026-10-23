-- Second stage of the disposable prototype: bind the monetary event to its
-- original SPOT fill, fee, independent valuation, intent and loss arithmetic.
-- It still does not replace operation/recorder/control/capital-wide audits.
BEGIN;
CREATE UNIQUE INDEX prototype_fill_source ON life_financial.fills
    (account_id,product,trade_id,wire_id,quantity,price,independent_price,fill_at,value_at,source_hash);
CREATE UNIQUE INDEX prototype_fee_source ON life_financial.fees
    (account_id,payment_id,product,trade_id,asset,signed_fee);
CREATE UNIQUE INDEX prototype_fact_source ON life_financial.spot_fill_facts
    (account_id,trade_id,product,intent_id,exchange_order_id,observation,execution_loss,source_operation_id);
CREATE UNIQUE INDEX prototype_intent_source ON life_financial.send_attempts
    (account_id,intent_id,wire_id,product,side);

CREATE TABLE fill_projection_spike.fill_source (
    account_id uuid NOT NULL, trade_id life_financial.identity NOT NULL,
    product text NOT NULL CHECK(product='SPOT'), kind text NOT NULL CHECK(kind='FILL'),
    event_id life_financial.identity NOT NULL, source_sequence bigint NOT NULL, event_at timestamptz NOT NULL,
    wire_id life_financial.identity NOT NULL, quantity life_financial.positive_amount NOT NULL,
    price life_financial.positive_amount NOT NULL, independent_price life_financial.positive_amount NOT NULL,
    fill_at timestamptz NOT NULL, value_at timestamptz NOT NULL, source_hash life_financial.digest NOT NULL,
    intent_id life_financial.identity NOT NULL, exchange_order_id text NOT NULL, observation jsonb NOT NULL,
    execution_loss life_financial.amount NOT NULL, source_operation_id life_financial.identity NOT NULL,
    payment_id life_financial.identity NOT NULL, asset text NOT NULL CHECK(asset IN ('LIFE','USDT')),
    signed_fee life_financial.signed_amount NOT NULL, side text NOT NULL CHECK(side IN ('BUY','SELL')),
    base_delta life_financial.signed_amount NOT NULL, quote_delta life_financial.signed_amount NOT NULL,
    PRIMARY KEY(account_id,trade_id), UNIQUE(account_id,event_id),
    FOREIGN KEY(account_id,product,trade_id,wire_id,quantity,price,independent_price,fill_at,value_at,source_hash)
        REFERENCES life_financial.fills(account_id,product,trade_id,wire_id,quantity,price,independent_price,fill_at,value_at,source_hash),
    FOREIGN KEY(account_id,payment_id,product,trade_id,asset,signed_fee)
        REFERENCES life_financial.fees(account_id,payment_id,product,trade_id,asset,signed_fee),
    FOREIGN KEY(account_id,trade_id,product,intent_id,exchange_order_id,observation,execution_loss,source_operation_id)
        REFERENCES life_financial.spot_fill_facts(account_id,trade_id,product,intent_id,exchange_order_id,observation,execution_loss,source_operation_id),
    FOREIGN KEY(account_id,intent_id,wire_id,product,side)
        REFERENCES life_financial.send_attempts(account_id,intent_id,wire_id,product,side),
    FOREIGN KEY(account_id,event_id,source_sequence,product,kind,trade_id,event_at,source_hash,base_delta,quote_delta)
        REFERENCES life_financial.financial_events(account_id,event_id,sequence,product,kind,exchange_identity,occurred_at,source_hash,base_delta,quote_delta),
    CHECK(event_at=fill_at AND source_hash=encode(sha256(convert_to(observation::text,'UTF8')),'hex')),
    CHECK((jsonb_typeof(observation)='object'
        AND observation ?& ARRAY['trade_id','quantity_base_life','price_usdt','independent_price_usdt','fee_currency','signed_fee']
        AND NOT observation @> '{"signed_fee":null}'::jsonb
        AND (observation->>'trade_id',observation->>'fee_currency')=(trade_id,asset)
        AND (observation->>'quantity_base_life')::numeric=quantity
        AND (observation->>'price_usdt')::numeric=price
        AND (observation->>'independent_price_usdt')::numeric=independent_price
        AND (observation->>'signed_fee')::numeric=signed_fee) IS TRUE),
    CHECK(base_delta=CASE side WHEN 'BUY' THEN quantity ELSE -quantity END+CASE asset WHEN 'LIFE' THEN signed_fee ELSE 0 END),
    CHECK(quote_delta=CASE side WHEN 'BUY' THEN -quantity*price ELSE quantity*price END+CASE asset WHEN 'USDT' THEN signed_fee ELSE 0 END),
    CHECK(execution_loss=greatest(0,-signed_fee*CASE asset WHEN 'LIFE' THEN independent_price ELSE 1 END
        -CASE side WHEN 'BUY' THEN 1 ELSE -1 END*(independent_price-price)*quantity))
);
ALTER TABLE life_financial.spot_fill_facts ADD CONSTRAINT prototype_fill_complete
    FOREIGN KEY(account_id,trade_id) REFERENCES fill_projection_spike.fill_source(account_id,trade_id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE FUNCTION fill_projection_spike.record_fill() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF NEW.product='SPOT' AND NEW.kind='FILL' THEN
        INSERT INTO fill_projection_spike.fill_source
        SELECT f.account_id,f.trade_id,f.product,NEW.kind,NEW.event_id,NEW.sequence,NEW.occurred_at,
            f.wire_id,f.quantity,f.price,f.independent_price,f.fill_at,f.value_at,f.source_hash,
            x.intent_id,x.exchange_order_id,x.observation,x.execution_loss,x.source_operation_id,
            fee.payment_id,fee.asset,fee.signed_fee,t.side,NEW.base_delta,NEW.quote_delta
        FROM life_financial.fills f
        JOIN life_financial.spot_fill_facts x USING(account_id,product,trade_id)
        JOIN life_financial.fees fee ON fee.account_id=f.account_id AND fee.product=f.product AND fee.trade_id=f.trade_id
            AND fee.payment_id='fee:'||encode(sha256(convert_to(f.trade_id,'UTF8')),'hex')
        JOIN life_financial.send_attempts t ON t.account_id=x.account_id AND t.intent_id=x.intent_id
        WHERE f.account_id=NEW.account_id AND f.product=NEW.product AND f.trade_id=NEW.exchange_identity;
        IF NOT FOUND THEN RAISE EXCEPTION 'PROTOTYPE_FILL_SOURCE_MISSING'; END IF;
    END IF;
    RETURN NULL;
END; $$;
CREATE TRIGGER prototype_fill_source AFTER INSERT ON life_financial.financial_events
    FOR EACH ROW EXECUTE FUNCTION fill_projection_spike.record_fill();
REVOKE ALL ON ALL TABLES IN SCHEMA fill_projection_spike FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA fill_projection_spike FROM PUBLIC;
COMMIT;
