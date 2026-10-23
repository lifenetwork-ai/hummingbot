-- Disposable monetary-source bindings for other native product writers.
-- Does not prove position replay, complete bill/bundle history, approvals or
-- current claims. Original product audits and COMMIT guards remain required.
BEGIN;
CREATE UNIQUE INDEX prototype_product_money ON fill_projection_spike.money
    (account_id,event_id,source_sequence,product,kind,exchange_identity,occurred_at,source_hash,base_delta,quote_delta);
CREATE UNIQUE INDEX prototype_swap_fact_source ON life_financial.swap_fill_facts
    (account_id,trade_id,intent_id,exchange_order_id,source_operation_id,source_hash);
ALTER TABLE life_financial.swap_fill_facts ADD CONSTRAINT prototype_swap_observation_hash
    CHECK(source_hash=encode(sha256(convert_to(life_financial.canonical_json_v1(observation),'UTF8')),'hex'));
CREATE TABLE fill_projection_spike.swap_source (
    account_id uuid NOT NULL,trade_id text NOT NULL,event_id text NOT NULL,sequence bigint NOT NULL,
    product text NOT NULL CHECK(product='SWAP'),kind text NOT NULL CHECK(kind='FILL'),
    wire_id text NOT NULL,quantity numeric NOT NULL,price numeric NOT NULL,independent_price numeric NOT NULL,
    fill_at timestamptz NOT NULL,value_at timestamptz NOT NULL,source_hash text NOT NULL,
    intent_id text NOT NULL,exchange_order_id text NOT NULL,source_operation_id text NOT NULL,
    payment_id text NOT NULL,asset text NOT NULL CHECK(asset='USDT'),signed_fee numeric NOT NULL,
    observation jsonb NOT NULL,pnl numeric NOT NULL,base_delta numeric NOT NULL CHECK(base_delta=0),quote_delta numeric NOT NULL,
    PRIMARY KEY(account_id,trade_id),UNIQUE(account_id,event_id),UNIQUE(account_id,payment_id),
    CHECK((observation->>'trade_id'=trade_id AND observation->>'fee_currency'=asset
        AND (observation->>'quantity_base_life')::numeric=quantity AND (observation->>'price_usdt')::numeric=price
        AND (observation->>'independent_price_usdt')::numeric=independent_price
        AND (observation->>'signed_fee')::numeric=signed_fee AND (observation->>'realized_pnl_usdt')::numeric=pnl
        AND to_timestamp((observation->>'fill_at_ms')::bigint/1000.0)=fill_at
        AND to_timestamp((observation->>'independent_value_at_ms')::bigint/1000.0)=value_at
        AND source_hash=encode(sha256(convert_to(life_financial.canonical_json_v1(observation),'UTF8')),'hex')) IS TRUE),
    CHECK(quote_delta=pnl+signed_fee),
    FOREIGN KEY(account_id,product,trade_id,wire_id,quantity,price,independent_price,fill_at,value_at,source_hash)
        REFERENCES life_financial.fills(account_id,product,trade_id,wire_id,quantity,price,independent_price,fill_at,value_at,source_hash),
    FOREIGN KEY(account_id,payment_id,product,trade_id,asset,signed_fee)
        REFERENCES life_financial.fees(account_id,payment_id,product,trade_id,asset,signed_fee),
    FOREIGN KEY(account_id,trade_id,intent_id,exchange_order_id,source_operation_id,source_hash)
        REFERENCES life_financial.swap_fill_facts(account_id,trade_id,intent_id,exchange_order_id,source_operation_id,source_hash),
    FOREIGN KEY(account_id,event_id,sequence,product,kind,trade_id,fill_at,source_hash,base_delta,quote_delta)
        REFERENCES fill_projection_spike.money(account_id,event_id,source_sequence,product,kind,exchange_identity,occurred_at,source_hash,base_delta,quote_delta)
);
ALTER TABLE life_financial.swap_fill_facts ADD CONSTRAINT prototype_swap_fact_complete
    FOREIGN KEY(account_id,trade_id) REFERENCES fill_projection_spike.swap_source DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE life_financial.fills
    ADD COLUMN prototype_swap_trade text GENERATED ALWAYS AS (CASE WHEN product='SWAP' THEN trade_id END) STORED,
    ADD CONSTRAINT prototype_raw_swap_fill_complete FOREIGN KEY(account_id,prototype_swap_trade)
        REFERENCES fill_projection_spike.swap_source(account_id,trade_id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE life_financial.fees
    ADD COLUMN prototype_swap_payment text GENERATED ALWAYS AS (CASE WHEN product='SWAP' THEN payment_id END) STORED,
    ADD CONSTRAINT prototype_raw_swap_fee_complete FOREIGN KEY(account_id,prototype_swap_payment)
        REFERENCES fill_projection_spike.swap_source(account_id,payment_id) DEFERRABLE INITIALLY DEFERRED;

CREATE UNIQUE INDEX prototype_funding_source ON life_financial.funding_cash_payments
    (account_id,bill_id,settled_at_ms,signed_payment_usdt,source_operation_id,source_hash);
CREATE TABLE fill_projection_spike.funding_source (
    account_id uuid NOT NULL,bill_id text NOT NULL,event_id text NOT NULL,sequence bigint NOT NULL,
    product text NOT NULL CHECK(product='ACCOUNT'),kind text NOT NULL CHECK(kind='FUNDING'),
    occurred_at timestamptz NOT NULL,source_hash text NOT NULL,base_delta numeric NOT NULL CHECK(base_delta=0),quote_delta numeric NOT NULL,
    settled_at_ms bigint NOT NULL,source_operation_id text NOT NULL,
    PRIMARY KEY(account_id,bill_id),UNIQUE(account_id,event_id),
    CHECK(occurred_at=to_timestamp(settled_at_ms/1000.0)),
    FOREIGN KEY(account_id,bill_id,settled_at_ms,quote_delta,source_operation_id,source_hash)
        REFERENCES life_financial.funding_cash_payments(account_id,bill_id,settled_at_ms,signed_payment_usdt,source_operation_id,source_hash),
    FOREIGN KEY(account_id,event_id,sequence,product,kind,bill_id,occurred_at,source_hash,base_delta,quote_delta)
        REFERENCES fill_projection_spike.money(account_id,event_id,source_sequence,product,kind,exchange_identity,occurred_at,source_hash,base_delta,quote_delta)
);
ALTER TABLE life_financial.funding_cash_payments ADD CONSTRAINT prototype_funding_complete
    FOREIGN KEY(account_id,bill_id) REFERENCES fill_projection_spike.funding_source DEFERRABLE INITIALLY DEFERRED;

CREATE UNIQUE INDEX prototype_cashflow_source ON life_financial.cashflows(account_id,bill_id,independent_price,value_usdt,source_hash);
CREATE UNIQUE INDEX prototype_bill_source ON life_financial.bill_approvals(account_id,bill_id,asset,signed_amount,transfer_at);
CREATE TABLE fill_projection_spike.cashflow_source (
    account_id uuid NOT NULL,bill_id text NOT NULL,event_id text NOT NULL,sequence bigint NOT NULL,
    product text NOT NULL CHECK(product='ACCOUNT'),kind text NOT NULL CHECK(kind='CASHFLOW'),
    occurred_at timestamptz NOT NULL,source_hash text NOT NULL,base_delta numeric NOT NULL,quote_delta numeric NOT NULL,
    asset text NOT NULL CHECK(asset IN ('LIFE','USDT')),amount numeric NOT NULL,independent_price numeric NOT NULL,value_usdt numeric NOT NULL,
    PRIMARY KEY(account_id,bill_id),UNIQUE(account_id,event_id),
    CHECK(base_delta=CASE asset WHEN 'LIFE' THEN amount ELSE 0 END AND quote_delta=CASE asset WHEN 'USDT' THEN amount ELSE 0 END),
    CHECK(value_usdt=amount*independent_price AND (asset<>'USDT' OR independent_price=1)),
    FOREIGN KEY(account_id,bill_id,asset,amount,occurred_at)
        REFERENCES life_financial.bill_approvals(account_id,bill_id,asset,signed_amount,transfer_at),
    FOREIGN KEY(account_id,bill_id,independent_price,value_usdt,source_hash)
        REFERENCES life_financial.cashflows(account_id,bill_id,independent_price,value_usdt,source_hash),
    FOREIGN KEY(account_id,event_id,sequence,product,kind,bill_id,occurred_at,source_hash,base_delta,quote_delta)
        REFERENCES fill_projection_spike.money(account_id,event_id,source_sequence,product,kind,exchange_identity,occurred_at,source_hash,base_delta,quote_delta)
);
ALTER TABLE life_financial.cashflows ADD CONSTRAINT prototype_cashflow_complete
    FOREIGN KEY(account_id,bill_id) REFERENCES fill_projection_spike.cashflow_source DEFERRABLE INITIALLY DEFERRED;

-- Even omitted native events must not evade publication of these sources.
ALTER TABLE life_financial.financial_events
    ADD COLUMN prototype_swap_event text GENERATED ALWAYS AS (CASE WHEN product='SWAP' AND kind='FILL' THEN event_id END) STORED,
    ADD COLUMN prototype_funding_event text GENERATED ALWAYS AS (CASE WHEN product='ACCOUNT' AND kind='FUNDING' THEN event_id END) STORED,
    ADD COLUMN prototype_cashflow_event text GENERATED ALWAYS AS (CASE WHEN product='ACCOUNT' AND kind='CASHFLOW' THEN event_id END) STORED,
    ADD CONSTRAINT prototype_swap_event_complete FOREIGN KEY(account_id,prototype_swap_event)
        REFERENCES fill_projection_spike.swap_source(account_id,event_id) DEFERRABLE INITIALLY DEFERRED,
    ADD CONSTRAINT prototype_funding_event_complete FOREIGN KEY(account_id,prototype_funding_event)
        REFERENCES fill_projection_spike.funding_source(account_id,event_id) DEFERRABLE INITIALLY DEFERRED,
    ADD CONSTRAINT prototype_cashflow_event_complete FOREIGN KEY(account_id,prototype_cashflow_event)
        REFERENCES fill_projection_spike.cashflow_source(account_id,event_id) DEFERRABLE INITIALLY DEFERRED;

CREATE FUNCTION fill_projection_spike.record_product_source() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF NEW.product='SWAP' AND NEW.kind='FILL' THEN
        INSERT INTO fill_projection_spike.swap_source
        SELECT f.account_id,f.trade_id,NEW.event_id,NEW.sequence,NEW.product,NEW.kind,f.wire_id,f.quantity,f.price,f.independent_price,
            f.fill_at,f.value_at,f.source_hash,x.intent_id,x.exchange_order_id,x.source_operation_id,fee.payment_id,fee.asset,fee.signed_fee,
            x.observation,(x.observation->>'realized_pnl_usdt')::numeric,NEW.base_delta,NEW.quote_delta
        FROM life_financial.fills f JOIN life_financial.swap_fill_facts x USING(account_id,trade_id)
        JOIN life_financial.fees fee ON fee.account_id=f.account_id AND fee.product='SWAP' AND fee.trade_id=f.trade_id
        WHERE f.account_id=NEW.account_id AND f.product='SWAP' AND f.trade_id=NEW.exchange_identity;
        IF NOT FOUND THEN RAISE EXCEPTION 'PROTOTYPE_SWAP_SOURCE_MISSING';END IF;
    ELSIF NEW.product='ACCOUNT' AND NEW.kind='FUNDING' THEN
        INSERT INTO fill_projection_spike.funding_source
        SELECT account_id,bill_id,NEW.event_id,NEW.sequence,NEW.product,NEW.kind,NEW.occurred_at,NEW.source_hash,NEW.base_delta,NEW.quote_delta,
            settled_at_ms,source_operation_id FROM life_financial.funding_cash_payments WHERE account_id=NEW.account_id AND bill_id=NEW.exchange_identity;
        IF NOT FOUND THEN RAISE EXCEPTION 'PROTOTYPE_FUNDING_SOURCE_MISSING';END IF;
    ELSIF NEW.product='ACCOUNT' AND NEW.kind='CASHFLOW' THEN
        INSERT INTO fill_projection_spike.cashflow_source
        SELECT b.account_id,b.bill_id,NEW.event_id,NEW.sequence,NEW.product,NEW.kind,NEW.occurred_at,NEW.source_hash,NEW.base_delta,NEW.quote_delta,
            b.asset,b.signed_amount,f.independent_price,f.value_usdt FROM life_financial.bill_approvals b
        JOIN life_financial.cashflows f USING(account_id,bill_id) WHERE b.account_id=NEW.account_id AND b.bill_id=NEW.exchange_identity;
        IF NOT FOUND THEN RAISE EXCEPTION 'PROTOTYPE_CASHFLOW_SOURCE_MISSING';END IF;
    END IF;
    RETURN NULL;
END; $$;
CREATE TRIGGER prototype_product_source AFTER INSERT ON life_financial.financial_events
    FOR EACH ROW EXECUTE FUNCTION fill_projection_spike.record_product_source();
REVOKE ALL ON ALL TABLES IN SCHEMA fill_projection_spike FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA fill_projection_spike FROM PUBLIC;
COMMIT;
