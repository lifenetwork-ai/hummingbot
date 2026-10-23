-- Disposable closure of materialized budget endpoints against their event
-- prefix. The zero origin and original imported limits cannot be reset on read.
BEGIN;
CREATE TABLE fill_projection_spike.budget_origin (
    account_id uuid NOT NULL,family text NOT NULL,scope text NOT NULL,window_key life_financial.identity NOT NULL,
    budget_limit life_financial.positive_amount NOT NULL,PRIMARY KEY(account_id,family,scope,window_key),
    UNIQUE(account_id,family,scope,window_key,budget_limit)
);
CREATE TABLE fill_projection_spike.budget_frontier (
    account_id uuid NOT NULL,family text NOT NULL,scope text NOT NULL,window_key life_financial.identity NOT NULL,
    ordinal bigint NOT NULL CHECK(ordinal>=0),successor bigint NOT NULL DEFAULT 0 CHECK(successor=0 OR successor=ordinal+1),
    previous bigint GENERATED ALWAYS AS (CASE WHEN ordinal>0 THEN ordinal-1 END) STORED,
    source_ordinal bigint GENERATED ALWAYS AS (CASE WHEN ordinal>0 THEN ordinal END) STORED,
    committed numeric NOT NULL,incurred numeric NOT NULL,
    PRIMARY KEY(account_id,family,scope,window_key,ordinal),
    UNIQUE(account_id,family,scope,window_key,ordinal,successor),
    UNIQUE(account_id,family,scope,window_key,ordinal,committed,incurred),
    CHECK(ordinal>0 OR committed=0 AND incurred=0),
    FOREIGN KEY(account_id,family,scope,window_key,previous,ordinal)
        REFERENCES fill_projection_spike.budget_frontier(account_id,family,scope,window_key,ordinal,successor),
    FOREIGN KEY(account_id,family,scope,window_key,source_ordinal,committed,incurred)
        REFERENCES fill_projection_spike.budget(account_id,family,scope,window_key,ordinal,committed_after,incurred_after)
);
ALTER TABLE fill_projection_spike.budget ADD CONSTRAINT prototype_budget_frontier_complete
    FOREIGN KEY(account_id,family,scope,window_key,ordinal) REFERENCES fill_projection_spike.budget_frontier DEFERRABLE INITIALLY DEFERRED;
CREATE UNIQUE INDEX prototype_bucket_source ON life_financial.budget_buckets
    (account_id,family,scope,window_key,budget_limit,committed,incurred,version);
CREATE TABLE fill_projection_spike.budget_current (
    account_id uuid NOT NULL,family text NOT NULL,scope text NOT NULL,window_key life_financial.identity NOT NULL,
    budget_limit life_financial.positive_amount NOT NULL,committed life_financial.amount NOT NULL,incurred life_financial.amount NOT NULL,
    version bigint NOT NULL,ordinal bigint NOT NULL,leaf_marker bigint GENERATED ALWAYS AS (0) STORED,
    PRIMARY KEY(account_id,family,scope,window_key),UNIQUE(account_id,family,scope,window_key,budget_limit,committed,incurred,version),
    FOREIGN KEY(account_id,family,scope,window_key,budget_limit) REFERENCES fill_projection_spike.budget_origin(account_id,family,scope,window_key,budget_limit),
    FOREIGN KEY(account_id,family,scope,window_key,ordinal,committed,incurred) REFERENCES fill_projection_spike.budget_frontier(account_id,family,scope,window_key,ordinal,committed,incurred),
    FOREIGN KEY(account_id,family,scope,window_key,ordinal,leaf_marker) REFERENCES fill_projection_spike.budget_frontier(account_id,family,scope,window_key,ordinal,successor) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(account_id,family,scope,window_key,budget_limit,committed,incurred,version) REFERENCES life_financial.budget_buckets(account_id,family,scope,window_key,budget_limit,committed,incurred,version) DEFERRABLE INITIALLY DEFERRED
);
ALTER TABLE life_financial.budget_buckets ADD CONSTRAINT prototype_budget_current
    FOREIGN KEY(account_id,family,scope,window_key,budget_limit,committed,incurred,version)
        REFERENCES fill_projection_spike.budget_current(account_id,family,scope,window_key,budget_limit,committed,incurred,version) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE fill_projection_spike.dirty_budgets (
    account_id uuid NOT NULL,family text NOT NULL,scope text NOT NULL,window_key life_financial.identity NOT NULL,
    PRIMARY KEY(account_id,family,scope,window_key)
);

CREATE FUNCTION fill_projection_spike.record_budget_endpoint() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF TG_OP='INSERT' THEN
        IF NEW.committed<>0 OR NEW.incurred<>0 THEN RAISE EXCEPTION 'PROTOTYPE_BUDGET_REQUIRES_ZERO_ORIGIN';END IF;
        INSERT INTO fill_projection_spike.budget_origin VALUES(NEW.account_id,NEW.family,NEW.scope,NEW.window_key,NEW.budget_limit);
        INSERT INTO fill_projection_spike.budget_frontier(account_id,family,scope,window_key,ordinal,committed,incurred)
            VALUES(NEW.account_id,NEW.family,NEW.scope,NEW.window_key,0,0,0);
        INSERT INTO fill_projection_spike.budget_current(account_id,family,scope,window_key,budget_limit,committed,incurred,version,ordinal)
            VALUES(NEW.account_id,NEW.family,NEW.scope,NEW.window_key,NEW.budget_limit,0,0,NEW.version,0);
    END IF;
    INSERT INTO fill_projection_spike.dirty_budgets VALUES(NEW.account_id,NEW.family,NEW.scope,NEW.window_key) ON CONFLICT DO NOTHING;
    RETURN NULL;
END; $$;
CREATE TRIGGER prototype_budget_endpoint AFTER INSERT OR UPDATE ON life_financial.budget_buckets
    FOR EACH ROW EXECUTE FUNCTION fill_projection_spike.record_budget_endpoint();

CREATE FUNCTION fill_projection_spike.record_budget_frontier() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    UPDATE fill_projection_spike.budget_frontier SET successor=NEW.ordinal WHERE account_id=NEW.account_id AND family=NEW.family
        AND scope=NEW.scope AND window_key=NEW.window_key AND ordinal=NEW.ordinal-1;
    IF NOT FOUND THEN RAISE EXCEPTION 'PROTOTYPE_BUDGET_ORIGIN_MISSING';END IF;
    INSERT INTO fill_projection_spike.budget_frontier(account_id,family,scope,window_key,ordinal,committed,incurred)
        VALUES(NEW.account_id,NEW.family,NEW.scope,NEW.window_key,NEW.ordinal,NEW.committed_after,NEW.incurred_after);
    INSERT INTO fill_projection_spike.dirty_budgets VALUES(NEW.account_id,NEW.family,NEW.scope,NEW.window_key) ON CONFLICT DO NOTHING;
    RETURN NULL;
END; $$;
CREATE TRIGGER prototype_budget_frontier AFTER INSERT ON fill_projection_spike.budget
    FOR EACH ROW EXECUTE FUNCTION fill_projection_spike.record_budget_frontier();

CREATE FUNCTION fill_projection_spike.publish_budgets() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE d record;b life_financial.budget_buckets;f fill_projection_spike.budget_frontier;
BEGIN
    IF (SELECT count(*) FROM (SELECT 1 FROM fill_projection_spike.dirty_budgets WHERE account_id=NEW.account_id LIMIT 385) q)>384 THEN
        RAISE EXCEPTION 'PROTOTYPE_DIRTY_BUDGET_BOUND';END IF;
    FOR d IN DELETE FROM fill_projection_spike.dirty_budgets WHERE account_id=NEW.account_id RETURNING * LOOP
        SELECT * INTO STRICT b FROM life_financial.budget_buckets WHERE account_id=d.account_id AND family=d.family AND scope=d.scope AND window_key=d.window_key;
        SELECT * INTO STRICT f FROM fill_projection_spike.budget_frontier WHERE account_id=d.account_id AND family=d.family AND scope=d.scope AND window_key=d.window_key ORDER BY ordinal DESC LIMIT 1;
        INSERT INTO fill_projection_spike.budget_current(account_id,family,scope,window_key,budget_limit,committed,incurred,version,ordinal)
            VALUES(b.account_id,b.family,b.scope,b.window_key,b.budget_limit,b.committed,b.incurred,b.version,f.ordinal)
        ON CONFLICT(account_id,family,scope,window_key) DO UPDATE SET budget_limit=excluded.budget_limit,committed=excluded.committed,
            incurred=excluded.incurred,version=excluded.version,ordinal=excluded.ordinal;
    END LOOP;
    RETURN NULL;
END; $$;
CREATE TRIGGER zzz_prototype_budget AFTER INSERT ON life_financial.operations
    FOR EACH ROW EXECUTE FUNCTION fill_projection_spike.publish_budgets();
REVOKE ALL ON ALL TABLES IN SCHEMA fill_projection_spike FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA fill_projection_spike FROM PUBLIC;
COMMIT;
