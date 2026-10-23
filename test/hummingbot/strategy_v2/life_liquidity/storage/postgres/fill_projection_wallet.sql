-- Disposable fresh-account wallet closure. Initial owner provisioning is the
-- trusted origin; native capital-policy imports must agree with that origin.
-- Only zero liabilities are supported by this experimental SPOT profile.
BEGIN;
CREATE TABLE fill_projection_spike.wallet_origin (
    account_id uuid NOT NULL REFERENCES life_financial.accounts,asset text NOT NULL CHECK(asset IN ('LIFE','USDT')),
    opening_total life_financial.amount NOT NULL,PRIMARY KEY(account_id,asset),UNIQUE(account_id,asset,opening_total)
);
CREATE TABLE fill_projection_spike.money_frontier (
    account_id uuid NOT NULL,ordinal bigint NOT NULL CHECK(ordinal>=0),
    previous bigint GENERATED ALWAYS AS (CASE WHEN ordinal>0 THEN ordinal-1 END) STORED,
    source_ordinal bigint GENERATED ALWAYS AS (CASE WHEN ordinal>0 THEN ordinal END) STORED,
    successor bigint NOT NULL DEFAULT 0 CHECK(successor=0 OR successor=ordinal+1),
    base_after numeric NOT NULL,quote_after numeric NOT NULL,
    PRIMARY KEY(account_id,ordinal),UNIQUE(account_id,ordinal,successor),UNIQUE(account_id,ordinal,base_after,quote_after),
    CHECK(ordinal>0 OR base_after=0 AND quote_after=0),
    FOREIGN KEY(account_id,previous,ordinal) REFERENCES fill_projection_spike.money_frontier(account_id,ordinal,successor),
    FOREIGN KEY(account_id,source_ordinal,base_after,quote_after) REFERENCES fill_projection_spike.money(account_id,ordinal,base_after,quote_after)
);
ALTER TABLE fill_projection_spike.money ADD CONSTRAINT prototype_wallet_frontier_complete
    FOREIGN KEY(account_id,ordinal) REFERENCES fill_projection_spike.money_frontier DEFERRABLE INITIALLY DEFERRED;
CREATE UNIQUE INDEX prototype_balance_source ON life_financial.balances(account_id,asset,total,reserved,liability,funding_deficit,version);
CREATE TABLE fill_projection_spike.wallet_proof (
    account_id uuid NOT NULL,asset text NOT NULL CHECK(asset IN ('LIFE','USDT')),
    total life_financial.amount NOT NULL,reserved life_financial.amount NOT NULL,
    liability life_financial.amount NOT NULL CHECK(liability=0),funding_deficit life_financial.amount NOT NULL,version bigint NOT NULL,
    opening_total life_financial.amount NOT NULL,money_ordinal bigint NOT NULL,base_after numeric NOT NULL,quote_after numeric NOT NULL,
    leaf_marker bigint GENERATED ALWAYS AS (0) STORED,
    PRIMARY KEY(account_id,asset),UNIQUE(account_id,asset,total,reserved,liability,funding_deficit,version),
    CHECK(total=opening_total+CASE asset WHEN 'LIFE' THEN base_after ELSE quote_after END),
    CHECK(funding_deficit=greatest(0,reserved-total)),
    FOREIGN KEY(account_id,asset,opening_total) REFERENCES fill_projection_spike.wallet_origin(account_id,asset,opening_total),
    FOREIGN KEY(account_id,money_ordinal,base_after,quote_after) REFERENCES fill_projection_spike.money_frontier(account_id,ordinal,base_after,quote_after),
    FOREIGN KEY(account_id,money_ordinal,leaf_marker) REFERENCES fill_projection_spike.money_frontier(account_id,ordinal,successor) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(account_id,asset,total,reserved,liability,funding_deficit,version)
        REFERENCES life_financial.balances(account_id,asset,total,reserved,liability,funding_deficit,version) DEFERRABLE INITIALLY DEFERRED
);
ALTER TABLE life_financial.balances ADD CONSTRAINT prototype_wallet_current
    FOREIGN KEY(account_id,asset,total,reserved,liability,funding_deficit,version)
        REFERENCES fill_projection_spike.wallet_proof(account_id,asset,total,reserved,liability,funding_deficit,version)
        DEFERRABLE INITIALLY DEFERRED;
-- Constant-asset parents live outside the native policy row. Adding helper
-- columns there would change to_jsonb(policy), the native body and its cut.
CREATE TABLE fill_projection_spike.wallet_base_origin (
    account_id uuid PRIMARY KEY,opening_total life_financial.amount NOT NULL,
    asset text GENERATED ALWAYS AS ('LIFE') STORED,UNIQUE(account_id,opening_total),
    FOREIGN KEY(account_id,asset,opening_total) REFERENCES fill_projection_spike.wallet_origin(account_id,asset,opening_total)
);
CREATE TABLE fill_projection_spike.wallet_quote_origin (
    account_id uuid PRIMARY KEY,opening_total life_financial.amount NOT NULL,
    asset text GENERATED ALWAYS AS ('USDT') STORED,UNIQUE(account_id,opening_total),
    FOREIGN KEY(account_id,asset,opening_total) REFERENCES fill_projection_spike.wallet_origin(account_id,asset,opening_total)
);
ALTER TABLE life_financial.spot_capital_policies ADD CONSTRAINT prototype_opening_base
    FOREIGN KEY(account_id,opening_base) REFERENCES fill_projection_spike.wallet_base_origin(account_id,opening_total);
ALTER TABLE life_financial.spot_capital_policies ADD CONSTRAINT prototype_opening_quote
    FOREIGN KEY(account_id,opening_quote) REFERENCES fill_projection_spike.wallet_quote_origin(account_id,opening_total);

CREATE FUNCTION fill_projection_spike.record_wallet_origin() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF NEW.reserved<>0 OR NEW.liability<>0 OR EXISTS(SELECT 1 FROM fill_projection_spike.money WHERE account_id=NEW.account_id) THEN
        RAISE EXCEPTION 'PROTOTYPE_WALLET_REQUIRES_FRESH_ORIGIN';END IF;
    INSERT INTO fill_projection_spike.wallet_origin VALUES(NEW.account_id,NEW.asset,NEW.total);
    IF NEW.asset='LIFE' THEN INSERT INTO fill_projection_spike.wallet_base_origin(account_id,opening_total) VALUES(NEW.account_id,NEW.total);
    ELSE INSERT INTO fill_projection_spike.wallet_quote_origin(account_id,opening_total) VALUES(NEW.account_id,NEW.total);END IF;
    INSERT INTO fill_projection_spike.money_frontier(account_id,ordinal,base_after,quote_after) VALUES(NEW.account_id,0,0,0) ON CONFLICT DO NOTHING;
    INSERT INTO fill_projection_spike.wallet_proof(account_id,asset,total,reserved,liability,funding_deficit,version,opening_total,money_ordinal,base_after,quote_after)
        VALUES(NEW.account_id,NEW.asset,NEW.total,0,0,0,NEW.version,NEW.total,0,0,0);
    RETURN NULL;
END; $$;
CREATE TRIGGER prototype_wallet_origin AFTER INSERT ON life_financial.balances
    FOR EACH ROW EXECUTE FUNCTION fill_projection_spike.record_wallet_origin();

CREATE FUNCTION fill_projection_spike.record_frontier() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    UPDATE fill_projection_spike.money_frontier SET successor=NEW.ordinal WHERE account_id=NEW.account_id AND ordinal=NEW.ordinal-1;
    IF NOT FOUND THEN RAISE EXCEPTION 'PROTOTYPE_MONEY_ORIGIN_MISSING';END IF;
    INSERT INTO fill_projection_spike.money_frontier(account_id,ordinal,base_after,quote_after)
        VALUES(NEW.account_id,NEW.ordinal,NEW.base_after,NEW.quote_after);
    RETURN NULL;
END; $$;
CREATE TRIGGER prototype_money_frontier AFTER INSERT ON fill_projection_spike.money
    FOR EACH ROW EXECUTE FUNCTION fill_projection_spike.record_frontier();

CREATE FUNCTION fill_projection_spike.publish_wallet() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE f fill_projection_spike.money_frontier;b record;held numeric;
BEGIN
    SELECT * INTO STRICT f FROM fill_projection_spike.money_frontier WHERE account_id=NEW.account_id ORDER BY ordinal DESC LIMIT 1;
    -- Explicit experimental maximum: 128 two-sided quote slots * 3 holds.
    -- The indexed scan covers active holds only, regardless of settled history.
    IF (SELECT count(*) FROM (SELECT 1 FROM fill_projection_spike.hold_proof WHERE account_id=NEW.account_id AND state<>'TERMINAL' LIMIT 385) q)>384 THEN
        RAISE EXCEPTION 'PROTOTYPE_ACTIVE_HOLD_BOUND';END IF;
    FOR b IN SELECT n.*,o.opening_total FROM life_financial.balances n JOIN fill_projection_spike.wallet_origin o USING(account_id,asset) WHERE n.account_id=NEW.account_id LOOP
        SELECT COALESCE(sum(remaining),0) INTO held FROM fill_projection_spike.hold_proof WHERE account_id=NEW.account_id AND asset=b.asset AND state<>'TERMINAL';
        IF held<>b.reserved THEN RAISE EXCEPTION 'PROTOTYPE_WALLET_HOLDS_MISMATCH';END IF;
        INSERT INTO fill_projection_spike.wallet_proof(account_id,asset,total,reserved,liability,funding_deficit,version,opening_total,money_ordinal,base_after,quote_after)
            VALUES(NEW.account_id,b.asset,b.total,b.reserved,b.liability,b.funding_deficit,b.version,b.opening_total,f.ordinal,f.base_after,f.quote_after)
        ON CONFLICT(account_id,asset) DO UPDATE SET total=excluded.total,reserved=excluded.reserved,liability=excluded.liability,
            funding_deficit=excluded.funding_deficit,version=excluded.version,money_ordinal=excluded.money_ordinal,base_after=excluded.base_after,quote_after=excluded.quote_after;
    END LOOP;
    RETURN NULL;
END; $$;
CREATE TRIGGER zz_prototype_wallet AFTER INSERT ON life_financial.operations
    FOR EACH ROW EXECUTE FUNCTION fill_projection_spike.publish_wallet();
REVOKE ALL ON ALL TABLES IN SCHEMA fill_projection_spike FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA fill_projection_spike FROM PUBLIC;
COMMIT;
