-- Test-only design experiment, installed solely in a disposable database.
-- Original commands, audits and deferred COMMIT guards remain active.
-- These prefixes are not a verified snapshot, financial cut or send authority.
BEGIN;
CREATE SCHEMA fill_projection_spike;
REVOKE ALL ON SCHEMA fill_projection_spike FROM PUBLIC;

-- Install before source events exist. Never silently bless existing history.
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM life_financial.financial_events)
        OR EXISTS(SELECT 1 FROM life_financial.budget_events) THEN
        RAISE EXCEPTION 'PROTOTYPE_REQUIRES_EMPTY_EVENTS';
    END IF;
END; $$;

-- Native composite keys let internal FK checks pin the exact source values.
-- DISABLE TRIGGER USER does not disable PostgreSQL's internal FK triggers.
CREATE UNIQUE INDEX prototype_money_source ON life_financial.financial_events
    (account_id,event_id,sequence,product,kind,exchange_identity,occurred_at,source_hash,base_delta,quote_delta);
CREATE UNIQUE INDEX prototype_budget_source ON life_financial.budget_events
    (account_id,event_id,family,scope,window_key,kind,amount);

CREATE TABLE fill_projection_spike.money (
    account_id uuid NOT NULL, ordinal bigint NOT NULL CHECK(ordinal>0),
    previous bigint, base_before numeric NOT NULL, quote_before numeric NOT NULL,
    base_after numeric NOT NULL, quote_after numeric NOT NULL,
    event_id life_financial.identity NOT NULL, source_sequence bigint NOT NULL,
    product text NOT NULL, kind text NOT NULL, exchange_identity text NOT NULL,
    occurred_at timestamptz NOT NULL, source_hash life_financial.digest NOT NULL,
    base_delta life_financial.signed_amount NOT NULL, quote_delta life_financial.signed_amount NOT NULL,
    PRIMARY KEY(account_id,ordinal), UNIQUE(account_id,event_id),
    UNIQUE(account_id,ordinal,base_after,quote_after),
    CHECK((ordinal=1 AND previous IS NULL AND base_before=0 AND quote_before=0)
        OR (ordinal>1 AND previous IS NOT NULL AND previous=ordinal-1)),
    CHECK(base_after=base_before+base_delta AND quote_after=quote_before+quote_delta),
    FOREIGN KEY(account_id,previous,base_before,quote_before)
        REFERENCES fill_projection_spike.money(account_id,ordinal,base_after,quote_after),
    FOREIGN KEY(account_id,event_id,source_sequence,product,kind,exchange_identity,occurred_at,source_hash,base_delta,quote_delta)
        REFERENCES life_financial.financial_events(account_id,event_id,sequence,product,kind,exchange_identity,occurred_at,source_hash,base_delta,quote_delta)
);
-- A committed original event must have exactly one witness. This detects a
-- skipped AFTER INSERT capture even with every USER trigger disabled.
ALTER TABLE life_financial.financial_events ADD CONSTRAINT prototype_money_complete
    FOREIGN KEY(account_id,event_id) REFERENCES fill_projection_spike.money(account_id,event_id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE fill_projection_spike.budget (
    account_id uuid NOT NULL, family text NOT NULL, scope text NOT NULL,
    window_key life_financial.identity NOT NULL, ordinal bigint NOT NULL CHECK(ordinal>0),
    previous bigint, committed_before numeric NOT NULL, incurred_before numeric NOT NULL,
    committed_after numeric NOT NULL, incurred_after numeric NOT NULL,
    event_id life_financial.identity NOT NULL, kind text NOT NULL,
    amount life_financial.amount NOT NULL,
    PRIMARY KEY(account_id,family,scope,window_key,ordinal), UNIQUE(account_id,event_id),
    UNIQUE(account_id,family,scope,window_key,ordinal,committed_after,incurred_after),
    CHECK((ordinal=1 AND previous IS NULL AND committed_before=0 AND incurred_before=0)
        OR (ordinal>1 AND previous IS NOT NULL AND previous=ordinal-1)),
    CHECK(committed_after=committed_before+CASE kind WHEN 'COMMIT' THEN amount WHEN 'RELEASE' THEN -amount ELSE 0 END),
    CHECK(incurred_after=incurred_before+CASE kind WHEN 'SPEND' THEN amount ELSE 0 END),
    FOREIGN KEY(account_id,family,scope,window_key,previous,committed_before,incurred_before)
        REFERENCES fill_projection_spike.budget(account_id,family,scope,window_key,ordinal,committed_after,incurred_after),
    FOREIGN KEY(account_id,event_id,family,scope,window_key,kind,amount)
        REFERENCES life_financial.budget_events(account_id,event_id,family,scope,window_key,kind,amount)
);
ALTER TABLE life_financial.budget_events ADD CONSTRAINT prototype_budget_complete
    FOREIGN KEY(account_id,event_id) REFERENCES fill_projection_spike.budget(account_id,event_id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE FUNCTION fill_projection_spike.record_money() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p fill_projection_spike.money;
BEGIN
    -- Production commands already serialize here. Direct test inserts must
    -- use the same exclusion, including concurrent allocation of the ordinal.
    PERFORM 1 FROM life_financial.accounts WHERE account_id=NEW.account_id FOR UPDATE;
    SELECT * INTO p FROM fill_projection_spike.money WHERE account_id=NEW.account_id ORDER BY ordinal DESC LIMIT 1;
    INSERT INTO fill_projection_spike.money VALUES(NEW.account_id,COALESCE(p.ordinal,0)+1,p.ordinal,
        COALESCE(p.base_after,0),COALESCE(p.quote_after,0),
        COALESCE(p.base_after,0)+NEW.base_delta,COALESCE(p.quote_after,0)+NEW.quote_delta,
        NEW.event_id,NEW.sequence,NEW.product,NEW.kind,NEW.exchange_identity,NEW.occurred_at,
        NEW.source_hash,NEW.base_delta,NEW.quote_delta);
    RETURN NULL;
END; $$;
CREATE FUNCTION fill_projection_spike.record_budget() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p fill_projection_spike.budget;
BEGIN
    PERFORM 1 FROM life_financial.accounts WHERE account_id=NEW.account_id FOR UPDATE;
    SELECT * INTO p FROM fill_projection_spike.budget
        WHERE (account_id,family,scope,window_key)=(NEW.account_id,NEW.family,NEW.scope,NEW.window_key)
        ORDER BY ordinal DESC LIMIT 1;
    INSERT INTO fill_projection_spike.budget VALUES(NEW.account_id,NEW.family,NEW.scope,NEW.window_key,
        COALESCE(p.ordinal,0)+1,p.ordinal,COALESCE(p.committed_after,0),COALESCE(p.incurred_after,0),
        COALESCE(p.committed_after,0)+CASE NEW.kind WHEN 'COMMIT' THEN NEW.amount WHEN 'RELEASE' THEN -NEW.amount ELSE 0 END,
        COALESCE(p.incurred_after,0)+CASE NEW.kind WHEN 'SPEND' THEN NEW.amount ELSE 0 END,
        NEW.event_id,NEW.kind,NEW.amount);
    RETURN NULL;
END; $$;
CREATE TRIGGER prototype_money AFTER INSERT ON life_financial.financial_events
    FOR EACH ROW EXECUTE FUNCTION fill_projection_spike.record_money();
CREATE TRIGGER prototype_budget AFTER INSERT ON life_financial.budget_events
    FOR EACH ROW EXECUTE FUNCTION fill_projection_spike.record_budget();
REVOKE ALL ON ALL TABLES IN SCHEMA fill_projection_spike FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA fill_projection_spike FROM PUBLIC;
COMMIT;
