-- Disposable extension: causal SPOT loss, immutable operation/basis and holds.
-- Original commands and all original runtime/COMMIT verifiers still execute.
BEGIN;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM life_financial.operations) OR EXISTS(SELECT 1 FROM life_financial.spot_preparations) THEN
        RAISE EXCEPTION 'PROTOTYPE_ECONOMICS_REQUIRES_EMPTY_OPERATIONS';
    END IF;
END; $$;

-- Fixed UTF-8 encoding, mirroring the already qualified immutable canonical
-- fingerprint kernel. No search_path, locale or caller-selected encoding.
CREATE FUNCTION fill_projection_spike.utf8_hash(p_text text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
    SELECT encode(sha256(convert_to(p_text,'UTF8')),'hex');
$$;

ALTER TABLE life_financial.operations
    ADD COLUMN prototype_receipt_hash text GENERATED ALWAYS AS (life_financial.command_fingerprint_v1(receipt)) STORED;
CREATE UNIQUE INDEX prototype_operation_source ON life_financial.operations
    (account_id,operation_id,fingerprint,committed_version,recorded_at,canonical_fingerprint,prototype_receipt_hash);
CREATE TABLE fill_projection_spike.operation_proof (
    account_id uuid NOT NULL, operation_id life_financial.identity NOT NULL,
    fingerprint life_financial.digest NOT NULL, version bigint NOT NULL, recorded_at timestamptz NOT NULL,
    command_hash text NOT NULL, receipt_hash text NOT NULL,
    PRIMARY KEY(account_id,operation_id),
    FOREIGN KEY(account_id,operation_id,fingerprint,version,recorded_at,command_hash,receipt_hash)
        REFERENCES life_financial.operations(account_id,operation_id,fingerprint,committed_version,recorded_at,canonical_fingerprint,prototype_receipt_hash)
);
ALTER TABLE life_financial.operations ADD CONSTRAINT prototype_operation_complete
    FOREIGN KEY(account_id,operation_id) REFERENCES fill_projection_spike.operation_proof DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE life_financial.spot_preparations
    ADD COLUMN prototype_request_hash text GENERATED ALWAYS AS (life_financial.command_fingerprint_v1(request)) STORED;
CREATE UNIQUE INDEX prototype_preparation_source ON life_financial.spot_preparations
    (account_id,operation_id,intent_id,quote_level,fee_base,fee_quote,subsidy,prepared_at,prototype_request_hash);
CREATE UNIQUE INDEX prototype_order_source ON life_financial.send_attempts
    (account_id,intent_id,operation_id,wire_id,side,session_id,quantity,price);
CREATE TABLE fill_projection_spike.order_basis (
    account_id uuid NOT NULL, intent_id life_financial.identity NOT NULL,
    operation_id life_financial.identity NOT NULL, wire_id life_financial.identity NOT NULL,
    side text NOT NULL CHECK(side IN ('BUY','SELL')), session_id uuid NOT NULL,
    quantity life_financial.positive_amount NOT NULL, price life_financial.positive_amount NOT NULL,
    quote_level integer NOT NULL, fee_base life_financial.amount NOT NULL, fee_quote life_financial.amount NOT NULL,
    subsidy life_financial.amount NOT NULL, prepared_at timestamptz NOT NULL, request_hash text NOT NULL,
    PRIMARY KEY(account_id,intent_id),
    UNIQUE(account_id,intent_id,operation_id,side,quantity,price,fee_base,fee_quote),
    UNIQUE(account_id,intent_id,session_id),
    FOREIGN KEY(account_id,operation_id) REFERENCES fill_projection_spike.operation_proof DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(account_id,intent_id,operation_id,wire_id,side,session_id,quantity,price)
        REFERENCES life_financial.send_attempts(account_id,intent_id,operation_id,wire_id,side,session_id,quantity,price),
    FOREIGN KEY(account_id,operation_id,intent_id,quote_level,fee_base,fee_quote,subsidy,prepared_at,request_hash)
        REFERENCES life_financial.spot_preparations(account_id,operation_id,intent_id,quote_level,fee_base,fee_quote,subsidy,prepared_at,prototype_request_hash)
);
ALTER TABLE life_financial.spot_preparations ADD CONSTRAINT prototype_basis_complete
    FOREIGN KEY(account_id,intent_id) REFERENCES fill_projection_spike.order_basis DEFERRABLE INITIALLY DEFERRED;
CREATE UNIQUE INDEX prototype_basis_by_operation ON fill_projection_spike.order_basis(account_id,operation_id);

CREATE UNIQUE INDEX prototype_fill_progress_source ON fill_projection_spike.fill_source
    (account_id,trade_id,intent_id,quantity,asset,signed_fee,execution_loss);
CREATE INDEX prototype_fills_by_operation ON fill_projection_spike.fill_source(account_id,source_operation_id,trade_id);
CREATE TABLE fill_projection_spike.intent_prefix (
    account_id uuid NOT NULL, intent_id life_financial.identity NOT NULL, ordinal bigint NOT NULL CHECK(ordinal>=0),
    previous bigint, successor bigint NOT NULL DEFAULT 0 CHECK(successor=0 OR successor=ordinal+1),
    trade_id life_financial.identity,
    quantity life_financial.amount NOT NULL, asset text,
    signed_fee life_financial.signed_amount NOT NULL, execution_loss life_financial.amount NOT NULL,
    filled_before numeric NOT NULL, paid_base_before numeric NOT NULL, paid_quote_before numeric NOT NULL, loss_before numeric NOT NULL,
    filled_after numeric NOT NULL, paid_base_after numeric NOT NULL, paid_quote_after numeric NOT NULL, loss_after numeric NOT NULL,
    PRIMARY KEY(account_id,intent_id,ordinal), UNIQUE(account_id,trade_id),
    UNIQUE(account_id,intent_id,ordinal,filled_after,paid_base_after,paid_quote_after,loss_after),
    UNIQUE(account_id,intent_id,ordinal,successor),
    CHECK((ordinal=0 AND previous IS NULL AND trade_id IS NULL AND quantity=0 AND asset IS NULL AND signed_fee=0 AND execution_loss=0
        AND filled_before=0 AND paid_base_before=0 AND paid_quote_before=0 AND loss_before=0)
        OR (ordinal>0 AND previous IS NOT NULL AND previous=ordinal-1 AND trade_id IS NOT NULL AND quantity>0 AND asset IS NOT NULL AND asset IN ('LIFE','USDT'))),
    CHECK(filled_after=filled_before+quantity AND loss_after=loss_before+execution_loss),
    CHECK(paid_base_after=paid_base_before+CASE asset WHEN 'LIFE' THEN greatest(0,-signed_fee) ELSE 0 END),
    CHECK(paid_quote_after=paid_quote_before+CASE asset WHEN 'USDT' THEN greatest(0,-signed_fee) ELSE 0 END),
    FOREIGN KEY(account_id,trade_id,intent_id,quantity,asset,signed_fee,execution_loss)
        REFERENCES fill_projection_spike.fill_source(account_id,trade_id,intent_id,quantity,asset,signed_fee,execution_loss),
    FOREIGN KEY(account_id,intent_id,previous,filled_before,paid_base_before,paid_quote_before,loss_before)
        REFERENCES fill_projection_spike.intent_prefix(account_id,intent_id,ordinal,filled_after,paid_base_after,paid_quote_after,loss_after),
    FOREIGN KEY(account_id,intent_id,previous,ordinal)
        REFERENCES fill_projection_spike.intent_prefix(account_id,intent_id,ordinal,successor)
);
ALTER TABLE fill_projection_spike.fill_source ADD CONSTRAINT prototype_intent_complete
    FOREIGN KEY(account_id,trade_id) REFERENCES fill_projection_spike.intent_prefix(account_id,trade_id) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE fill_projection_spike.fill_head (
    account_id uuid NOT NULL, intent_id life_financial.identity NOT NULL,
    ordinal bigint NOT NULL CHECK(ordinal>=0), filled numeric NOT NULL, paid_base numeric NOT NULL, paid_quote numeric NOT NULL, loss numeric NOT NULL,
    leaf_marker bigint GENERATED ALWAYS AS (0) STORED,
    PRIMARY KEY(account_id,intent_id), UNIQUE(account_id,intent_id,filled,paid_base,paid_quote,loss),
    FOREIGN KEY(account_id,intent_id) REFERENCES fill_projection_spike.order_basis,
    FOREIGN KEY(account_id,intent_id,ordinal,filled,paid_base,paid_quote,loss)
        REFERENCES fill_projection_spike.intent_prefix(account_id,intent_id,ordinal,filled_after,paid_base_after,paid_quote_after,loss_after),
    FOREIGN KEY(account_id,intent_id,ordinal,leaf_marker)
        REFERENCES fill_projection_spike.intent_prefix(account_id,intent_id,ordinal,successor) DEFERRABLE INITIALLY DEFERRED
);
CREATE UNIQUE INDEX prototype_progress_source ON life_financial.spot_accounting_progress
    (account_id,intent_id,filled_base,paid_base,paid_quote,execution_loss);
ALTER TABLE fill_projection_spike.fill_head ADD COLUMN recorded_intent life_financial.identity
    GENERATED ALWAYS AS (CASE WHEN ordinal>0 THEN intent_id END) STORED;
ALTER TABLE fill_projection_spike.fill_head ADD CONSTRAINT prototype_nonzero_progress_exists
    FOREIGN KEY(account_id,recorded_intent,filled,paid_base,paid_quote,loss)
        REFERENCES life_financial.spot_accounting_progress(account_id,intent_id,filled_base,paid_base,paid_quote,execution_loss)
        DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE life_financial.spot_accounting_progress ADD CONSTRAINT prototype_progress_current
    FOREIGN KEY(account_id,intent_id,filled_base,paid_base,paid_quote,execution_loss)
        REFERENCES fill_projection_spike.fill_head(account_id,intent_id,filled,paid_base,paid_quote,loss)
        DEFERRABLE INITIALLY DEFERRED;

CREATE UNIQUE INDEX prototype_loss_source ON fill_projection_spike.fill_source(account_id,trade_id,intent_id,fill_at,execution_loss);
CREATE UNIQUE INDEX prototype_campaign_source ON life_financial.spot_policies(account_id,policy_hash,campaign_id);
CREATE TABLE fill_projection_spike.loss_proof (
    account_id uuid NOT NULL, trade_id life_financial.identity NOT NULL, intent_id life_financial.identity NOT NULL,
    fill_at timestamptz NOT NULL, amount life_financial.amount NOT NULL,
    policy_hash life_financial.digest NOT NULL, campaign_key life_financial.identity NOT NULL,
    session_id uuid NOT NULL, session_key life_financial.identity NOT NULL, day_key life_financial.identity NOT NULL,
    family text NOT NULL DEFAULT 'LOSS' CHECK(family='LOSS'), kind text NOT NULL DEFAULT 'SPEND' CHECK(kind='SPEND'),
    campaign_scope text NOT NULL DEFAULT 'CAMPAIGN' CHECK(campaign_scope='CAMPAIGN'),
    session_scope text NOT NULL DEFAULT 'SESSION' CHECK(session_scope='SESSION'), day_scope text NOT NULL DEFAULT 'DAY' CHECK(day_scope='DAY'),
    campaign_event text GENERATED ALWAYS AS ('loss:'||fill_projection_spike.utf8_hash(trade_id::text)||':CAMPAIGN') STORED,
    session_event text GENERATED ALWAYS AS ('loss:'||fill_projection_spike.utf8_hash(trade_id::text)||':SESSION') STORED,
    day_event text GENERATED ALWAYS AS ('loss:'||fill_projection_spike.utf8_hash(trade_id::text)||':DAY') STORED,
    PRIMARY KEY(account_id,trade_id),
    CHECK(session_key=session_id::text AND day_key=to_char(fill_at AT TIME ZONE 'UTC','YYYY-MM-DD')),
    FOREIGN KEY(account_id,trade_id,intent_id,fill_at,amount)
        REFERENCES fill_projection_spike.fill_source(account_id,trade_id,intent_id,fill_at,execution_loss),
    FOREIGN KEY(account_id,intent_id,session_id) REFERENCES fill_projection_spike.order_basis(account_id,intent_id,session_id),
    FOREIGN KEY(account_id,policy_hash,campaign_key) REFERENCES life_financial.spot_policies(account_id,policy_hash,campaign_id),
    FOREIGN KEY(account_id,campaign_event,family,campaign_scope,campaign_key,kind,amount)
        REFERENCES life_financial.budget_events(account_id,event_id,family,scope,window_key,kind,amount),
    FOREIGN KEY(account_id,session_event,family,session_scope,session_key,kind,amount)
        REFERENCES life_financial.budget_events(account_id,event_id,family,scope,window_key,kind,amount),
    FOREIGN KEY(account_id,day_event,family,day_scope,day_key,kind,amount)
        REFERENCES life_financial.budget_events(account_id,event_id,family,scope,window_key,kind,amount)
);
ALTER TABLE fill_projection_spike.fill_source ADD CONSTRAINT prototype_loss_complete
    FOREIGN KEY(account_id,trade_id) REFERENCES fill_projection_spike.loss_proof DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE fill_projection_spike.fill_source ADD CONSTRAINT prototype_fill_operation
    FOREIGN KEY(account_id,source_operation_id) REFERENCES fill_projection_spike.operation_proof DEFERRABLE INITIALLY DEFERRED;

CREATE UNIQUE INDEX prototype_reservation_source ON life_financial.reservations
    (account_id,reservation_id,intent_id,asset,amount,remaining,state,version);
CREATE UNIQUE INDEX prototype_attempt_state_source ON life_financial.send_attempts(account_id,intent_id,state);
CREATE TABLE fill_projection_spike.hold_proof (
    account_id uuid NOT NULL, reservation_id life_financial.identity NOT NULL, intent_id life_financial.identity NOT NULL,
    asset text NOT NULL, amount life_financial.positive_amount NOT NULL, remaining life_financial.amount NOT NULL,
    state text NOT NULL, version bigint NOT NULL,
    operation_id life_financial.identity NOT NULL, side text NOT NULL, quantity numeric NOT NULL, price numeric NOT NULL,
    fee_base numeric NOT NULL, fee_quote numeric NOT NULL,
    filled numeric NOT NULL, paid_base numeric NOT NULL, paid_quote numeric NOT NULL, loss numeric NOT NULL,
    attempt_state text NOT NULL,
    PRIMARY KEY(account_id,reservation_id),
    UNIQUE(account_id,reservation_id,intent_id,asset,amount,remaining,state,version),
    CHECK(filled<=quantity),
    CHECK((state='TERMINAL')=(attempt_state IN ('TERMINAL','ABORTED_BEFORE_SEND'))),
    CHECK((reservation_id=operation_id||':principal' AND asset=CASE side WHEN 'BUY' THEN 'USDT' ELSE 'LIFE' END
            AND amount=quantity*CASE side WHEN 'BUY' THEN price ELSE 1 END
            AND remaining=CASE state WHEN 'TERMINAL' THEN 0 ELSE (quantity-filled)*CASE side WHEN 'BUY' THEN price ELSE 1 END END)
        OR (reservation_id=operation_id||':fee:LIFE' AND asset='LIFE' AND amount=fee_base
            AND remaining=CASE state WHEN 'TERMINAL' THEN 0 ELSE greatest(0,fee_base-paid_base) END)
        OR (reservation_id=operation_id||':fee:USDT' AND asset='USDT' AND amount=fee_quote
            AND remaining=CASE state WHEN 'TERMINAL' THEN 0 ELSE greatest(0,fee_quote-paid_quote) END)),
    FOREIGN KEY(account_id,intent_id,operation_id,side,quantity,price,fee_base,fee_quote)
        REFERENCES fill_projection_spike.order_basis(account_id,intent_id,operation_id,side,quantity,price,fee_base,fee_quote),
    FOREIGN KEY(account_id,intent_id,attempt_state) REFERENCES life_financial.send_attempts(account_id,intent_id,state) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(account_id,intent_id,filled,paid_base,paid_quote,loss)
        REFERENCES fill_projection_spike.fill_head(account_id,intent_id,filled,paid_base,paid_quote,loss) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(account_id,reservation_id,intent_id,asset,amount,remaining,state,version)
        REFERENCES life_financial.reservations(account_id,reservation_id,intent_id,asset,amount,remaining,state,version) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX prototype_active_holds ON fill_projection_spike.hold_proof(account_id,asset,reservation_id) INCLUDE(remaining) WHERE state<>'TERMINAL';
-- Updating one fill head must find its few reservation witnesses without
-- scanning all the account's retained holds during deferred FK enforcement.
CREATE INDEX prototype_hold_by_intent ON fill_projection_spike.hold_proof(account_id,intent_id);
ALTER TABLE life_financial.reservations ADD CONSTRAINT prototype_hold_complete
    FOREIGN KEY(account_id,reservation_id,intent_id,asset,amount,remaining,state,version)
        REFERENCES fill_projection_spike.hold_proof(account_id,reservation_id,intent_id,asset,amount,remaining,state,version)
        DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE fill_projection_spike.dirty_intents (
    account_id uuid NOT NULL,intent_id life_financial.identity NOT NULL,PRIMARY KEY(account_id,intent_id)
);

CREATE FUNCTION fill_projection_spike.record_basis() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t life_financial.send_attempts;
BEGIN
    SELECT * INTO STRICT t FROM life_financial.send_attempts WHERE account_id=NEW.account_id AND intent_id=NEW.intent_id;
    IF t.product<>'SPOT' THEN RAISE EXCEPTION 'PROTOTYPE_SPOT_ONLY';END IF;
    INSERT INTO fill_projection_spike.order_basis VALUES(NEW.account_id,NEW.intent_id,NEW.operation_id,t.wire_id,t.side,t.session_id,
        t.quantity,t.price,NEW.quote_level,NEW.fee_base,NEW.fee_quote,NEW.subsidy,NEW.prepared_at,NEW.prototype_request_hash);
    INSERT INTO fill_projection_spike.intent_prefix VALUES(NEW.account_id,NEW.intent_id,0,NULL,0,NULL,0,NULL,0,0,0,0,0,0,0,0,0,0);
    INSERT INTO fill_projection_spike.fill_head(account_id,intent_id,ordinal,filled,paid_base,paid_quote,loss)
        VALUES(NEW.account_id,NEW.intent_id,0,0,0,0,0);
    RETURN NULL;
END; $$;
CREATE TRIGGER prototype_order_basis AFTER INSERT ON life_financial.spot_preparations
    FOR EACH ROW EXECUTE FUNCTION fill_projection_spike.record_basis();

CREATE FUNCTION fill_projection_spike.record_intent_fill() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p fill_projection_spike.fill_head; n bigint;
BEGIN
    SELECT * INTO STRICT p FROM fill_projection_spike.fill_head WHERE account_id=NEW.account_id AND intent_id=NEW.intent_id FOR UPDATE;
    n:=p.ordinal+1;
    -- The child references the parent's exact successor. The published head
    -- must reference successor=0, so even a coordinated rewind of mutable
    -- heads/progress cannot point behind a retained child.
    UPDATE fill_projection_spike.intent_prefix SET successor=n
        WHERE account_id=NEW.account_id AND intent_id=NEW.intent_id AND ordinal=p.ordinal;
    INSERT INTO fill_projection_spike.intent_prefix VALUES(NEW.account_id,NEW.intent_id,n,p.ordinal,0,NEW.trade_id,
        NEW.quantity,NEW.asset,NEW.signed_fee,NEW.execution_loss,p.filled,p.paid_base,p.paid_quote,p.loss,
        p.filled+NEW.quantity,p.paid_base+CASE NEW.asset WHEN 'LIFE' THEN greatest(0,-NEW.signed_fee) ELSE 0 END,
        p.paid_quote+CASE NEW.asset WHEN 'USDT' THEN greatest(0,-NEW.signed_fee) ELSE 0 END,p.loss+NEW.execution_loss);
    UPDATE fill_projection_spike.fill_head SET ordinal=n,filled=p.filled+NEW.quantity,
        paid_base=p.paid_base+CASE NEW.asset WHEN 'LIFE' THEN greatest(0,-NEW.signed_fee) ELSE 0 END,
        paid_quote=p.paid_quote+CASE NEW.asset WHEN 'USDT' THEN greatest(0,-NEW.signed_fee) ELSE 0 END,loss=p.loss+NEW.execution_loss
        WHERE account_id=NEW.account_id AND intent_id=NEW.intent_id;
    RETURN NULL;
END; $$;
CREATE TRIGGER prototype_intent_fill AFTER INSERT ON fill_projection_spike.fill_source
    FOR EACH ROW EXECUTE FUNCTION fill_projection_spike.record_intent_fill();

CREATE FUNCTION fill_projection_spike.dirty_hold() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF NEW.intent_id IS NULL THEN RAISE EXCEPTION 'PROTOTYPE_SPOT_HOLD_ONLY';END IF;
    INSERT INTO fill_projection_spike.dirty_intents VALUES(NEW.account_id,NEW.intent_id) ON CONFLICT DO NOTHING;
    RETURN NULL;
END; $$;
CREATE TRIGGER prototype_dirty_hold AFTER INSERT OR UPDATE ON life_financial.reservations
    FOR EACH ROW EXECUTE FUNCTION fill_projection_spike.dirty_hold();
CREATE TRIGGER prototype_dirty_attempt AFTER INSERT OR UPDATE ON life_financial.send_attempts
    FOR EACH ROW EXECUTE FUNCTION fill_projection_spike.dirty_hold();

CREATE FUNCTION fill_projection_spike.record_operation() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE f fill_projection_spike.fill_source;b fill_projection_spike.order_basis;policy life_financial.spot_policies;
    item record;d record;head fill_projection_spike.fill_head;cmd jsonb;
BEGIN
    INSERT INTO fill_projection_spike.operation_proof VALUES(NEW.account_id,NEW.operation_id,NEW.fingerprint,NEW.committed_version,
        NEW.recorded_at,NEW.canonical_fingerprint,NEW.prototype_receipt_hash);
    IF NEW.command->>'kind'='SPOT_FILL' AND NEW.receipt->>'state'='APPLIED' THEN
        cmd:=NEW.command->'command';
        SELECT * INTO STRICT head FROM fill_projection_spike.fill_head WHERE account_id=NEW.account_id AND intent_id=cmd->>'intent_id';
        IF NEW.fingerprint IS DISTINCT FROM NEW.canonical_fingerprint
            OR NEW.receipt->>'kind' IS DISTINCT FROM 'SPOT_FILL'
            OR NEW.receipt->>'operation_id' IS DISTINCT FROM NEW.operation_id
            OR NEW.receipt->>'intent_id' IS DISTINCT FROM head.intent_id
            OR (NEW.receipt->>'version')::bigint IS DISTINCT FROM NEW.committed_version
            OR NEW.receipt->'trading_permission' IS DISTINCT FROM 'false'::jsonb
            OR (NEW.receipt->>'cumulative_base_life')::numeric IS DISTINCT FROM head.filled
            OR (NEW.receipt->>'execution_loss_usdt')::numeric IS DISTINCT FROM head.loss
            OR (cmd->>'cumulative_base_life')::numeric IS DISTINCT FROM head.filled
            OR jsonb_typeof(cmd->'fills') IS DISTINCT FROM 'array'
            OR jsonb_array_length(cmd->'fills') IS DISTINCT FROM head.ordinal
            OR EXISTS(SELECT 1 FROM jsonb_array_elements(cmd->'fills') x
                LEFT JOIN fill_projection_spike.fill_source s ON s.account_id=NEW.account_id AND s.trade_id=x.value->>'trade_id'
                WHERE s.trade_id IS NULL OR s.intent_id<>head.intent_id OR s.observation IS DISTINCT FROM x.value) THEN
            RAISE EXCEPTION 'PROTOTYPE_FILL_RECEIPT_UNQUALIFIED';END IF;
    END IF;
    FOR f IN SELECT * FROM fill_projection_spike.fill_source WHERE account_id=NEW.account_id AND source_operation_id=NEW.operation_id LOOP
        IF NEW.command->>'kind' IS DISTINCT FROM 'SPOT_FILL' OR NEW.receipt->>'state' IS DISTINCT FROM 'APPLIED' THEN
            RAISE EXCEPTION 'PROTOTYPE_FILL_FAMILY_UNSUPPORTED';END IF;
        SELECT * INTO STRICT b FROM fill_projection_spike.order_basis WHERE account_id=f.account_id AND intent_id=f.intent_id;
        SELECT p.* INTO STRICT policy FROM life_financial.spot_policies p JOIN life_financial.accounts a USING(account_id,policy_hash) WHERE a.account_id=f.account_id;
        INSERT INTO fill_projection_spike.loss_proof(account_id,trade_id,intent_id,fill_at,amount,policy_hash,campaign_key,session_id,session_key,day_key)
            VALUES(f.account_id,f.trade_id,f.intent_id,f.fill_at,f.execution_loss,policy.policy_hash,policy.campaign_id,b.session_id,b.session_id::text,
                to_char(f.fill_at AT TIME ZONE 'UTC','YYYY-MM-DD'));
    END LOOP;
    -- Fixed experimental scope. This is a refusal bound, not an implicit live
    -- default. A command cannot turn the finalizer into an unbounded scan.
    IF (SELECT count(*) FROM (SELECT 1 FROM fill_projection_spike.dirty_intents WHERE account_id=NEW.account_id LIMIT 65) q)>64 THEN
        RAISE EXCEPTION 'PROTOTYPE_DIRTY_INTENT_BOUND';END IF;
    FOR d IN DELETE FROM fill_projection_spike.dirty_intents WHERE account_id=NEW.account_id RETURNING * LOOP
        FOR item IN SELECT r.*,x.operation_id AS prepare_operation,x.side,x.quantity,x.price,x.fee_base,x.fee_quote,
            h.filled,h.paid_base,h.paid_quote,h.loss,t.state AS attempt_state
            FROM life_financial.reservations r JOIN fill_projection_spike.order_basis x USING(account_id,intent_id)
            JOIN fill_projection_spike.fill_head h USING(account_id,intent_id)
            JOIN life_financial.send_attempts t USING(account_id,intent_id)
            WHERE r.account_id=d.account_id AND r.intent_id=d.intent_id LOOP
            IF (item.state='TERMINAL') IS DISTINCT FROM (item.attempt_state IN ('TERMINAL','ABORTED_BEFORE_SEND')) THEN
                RAISE EXCEPTION 'PROTOTYPE_HOLD_TERMINAL_UNQUALIFIED';END IF;
            INSERT INTO fill_projection_spike.hold_proof VALUES(item.account_id,item.reservation_id,item.intent_id,item.asset,item.amount,item.remaining,item.state,item.version,
                item.prepare_operation,item.side,item.quantity,item.price,item.fee_base,item.fee_quote,item.filled,item.paid_base,item.paid_quote,item.loss,item.attempt_state)
            ON CONFLICT(account_id,reservation_id) DO UPDATE SET asset=excluded.asset,amount=excluded.amount,remaining=excluded.remaining,
                state=excluded.state,version=excluded.version,filled=excluded.filled,paid_base=excluded.paid_base,paid_quote=excluded.paid_quote,loss=excluded.loss,attempt_state=excluded.attempt_state;
        END LOOP;
    END LOOP;
    RETURN NULL;
END; $$;
CREATE TRIGGER prototype_operation AFTER INSERT ON life_financial.operations
    FOR EACH ROW EXECUTE FUNCTION fill_projection_spike.record_operation();
REVOKE ALL ON ALL TABLES IN SCHEMA fill_projection_spike FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA fill_projection_spike FROM PUBLIC;
COMMIT;
