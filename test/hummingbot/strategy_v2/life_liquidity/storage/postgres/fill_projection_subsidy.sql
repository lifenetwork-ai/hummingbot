-- Disposable subsidy obligations. Original NAV/settlement/full-state audits
-- remain mandatory; these exact event links do not grant spending permission.
BEGIN;
CREATE UNIQUE INDEX prototype_basis_budget_source ON fill_projection_spike.order_basis
    (account_id,intent_id,session_id,prepared_at,subsidy);
CREATE UNIQUE INDEX prototype_prefix_loss_source ON fill_projection_spike.intent_prefix(account_id,intent_id,ordinal,loss_after);
CREATE UNIQUE INDEX prototype_settlement_member_source ON life_financial.spot_inventory_members
    (account_id,intent_id,operation_id,actual_usdt,held_before_usdt);
CREATE INDEX prototype_settlement_members_by_operation ON life_financial.spot_inventory_members(account_id,operation_id,intent_id);
CREATE TABLE fill_projection_spike.subsidy_obligation (
    account_id uuid NOT NULL, operation_id life_financial.identity NOT NULL, intent_id life_financial.identity NOT NULL,
    role text NOT NULL CHECK(role IN ('PREPARE','FLOOR','RELEASE','SETTLE_RELEASE','SETTLE_SPEND')),
    amount life_financial.amount NOT NULL, policy_hash life_financial.digest NOT NULL, campaign_key life_financial.identity NOT NULL,
    session_id uuid NOT NULL, session_key life_financial.identity NOT NULL, prepared_at timestamptz NOT NULL,
    day_key life_financial.identity NOT NULL, subsidy life_financial.amount NOT NULL,
    before_ordinal bigint NOT NULL, before_loss life_financial.amount NOT NULL,
    after_ordinal bigint NOT NULL, after_loss life_financial.amount NOT NULL,
    actual_usdt life_financial.amount, held_before_usdt life_financial.amount,
    settlement_operation life_financial.identity GENERATED ALWAYS AS (CASE WHEN role IN ('SETTLE_RELEASE','SETTLE_SPEND') THEN operation_id END) STORED,
    family text NOT NULL DEFAULT 'SUBSIDY' CHECK(family='SUBSIDY'),
    kind text GENERATED ALWAYS AS (CASE role WHEN 'RELEASE' THEN 'RELEASE' WHEN 'SETTLE_RELEASE' THEN 'RELEASE' WHEN 'SETTLE_SPEND' THEN 'SPEND' ELSE 'COMMIT' END) STORED,
    campaign_scope text NOT NULL DEFAULT 'CAMPAIGN' CHECK(campaign_scope='CAMPAIGN'),
    session_scope text NOT NULL DEFAULT 'SESSION' CHECK(session_scope='SESSION'),day_scope text NOT NULL DEFAULT 'DAY' CHECK(day_scope='DAY'),
    campaign_event text GENERATED ALWAYS AS (CASE role
        WHEN 'PREPARE' THEN operation_id||':subsidy:CAMPAIGN' WHEN 'FLOOR' THEN operation_id||':floor:CAMPAIGN'
        WHEN 'RELEASE' THEN operation_id||':release:CAMPAIGN'
        WHEN 'SETTLE_RELEASE' THEN 'settle-release:'||fill_projection_spike.utf8_hash(operation_id||':'||intent_id||':CAMPAIGN')
        ELSE 'settle-spend:'||fill_projection_spike.utf8_hash(operation_id||':'||intent_id||':CAMPAIGN') END) STORED,
    session_event text GENERATED ALWAYS AS (CASE role
        WHEN 'PREPARE' THEN operation_id||':subsidy:SESSION' WHEN 'FLOOR' THEN operation_id||':floor:SESSION'
        WHEN 'RELEASE' THEN operation_id||':release:SESSION'
        WHEN 'SETTLE_RELEASE' THEN 'settle-release:'||fill_projection_spike.utf8_hash(operation_id||':'||intent_id||':SESSION')
        ELSE 'settle-spend:'||fill_projection_spike.utf8_hash(operation_id||':'||intent_id||':SESSION') END) STORED,
    day_event text GENERATED ALWAYS AS (CASE role
        WHEN 'PREPARE' THEN operation_id||':subsidy:DAY' WHEN 'FLOOR' THEN operation_id||':floor:DAY'
        WHEN 'RELEASE' THEN operation_id||':release:DAY'
        WHEN 'SETTLE_RELEASE' THEN 'settle-release:'||fill_projection_spike.utf8_hash(operation_id||':'||intent_id||':DAY')
        ELSE 'settle-spend:'||fill_projection_spike.utf8_hash(operation_id||':'||intent_id||':DAY') END) STORED,
    PRIMARY KEY(account_id,operation_id,intent_id,role),
    CHECK(session_key=session_id::text AND day_key=to_char(prepared_at AT TIME ZONE 'UTC','YYYY-MM-DD')),
    CHECK(before_ordinal>=0 AND after_ordinal>=before_ordinal),
    CHECK((role='PREPARE' AND before_ordinal=0 AND after_ordinal=0 AND amount=subsidy)
        OR (role='FLOOR' AND after_ordinal>0 AND amount=greatest(subsidy,after_loss)-greatest(subsidy,before_loss))
        OR (role='RELEASE' AND before_ordinal=0 AND after_ordinal=0 AND amount=subsidy)
        OR (role='SETTLE_RELEASE' AND held_before_usdt IS NOT NULL AND actual_usdt IS NOT NULL AND amount=held_before_usdt)
        OR (role='SETTLE_SPEND' AND held_before_usdt IS NOT NULL AND actual_usdt IS NOT NULL AND amount=actual_usdt)),
    FOREIGN KEY(account_id,operation_id) REFERENCES fill_projection_spike.operation_proof,
    FOREIGN KEY(account_id,intent_id,session_id,prepared_at,subsidy)
        REFERENCES fill_projection_spike.order_basis(account_id,intent_id,session_id,prepared_at,subsidy),
    FOREIGN KEY(account_id,intent_id,before_ordinal,before_loss)
        REFERENCES fill_projection_spike.intent_prefix(account_id,intent_id,ordinal,loss_after),
    FOREIGN KEY(account_id,intent_id,after_ordinal,after_loss)
        REFERENCES fill_projection_spike.intent_prefix(account_id,intent_id,ordinal,loss_after),
    FOREIGN KEY(account_id,intent_id,settlement_operation,actual_usdt,held_before_usdt)
        REFERENCES life_financial.spot_inventory_members(account_id,intent_id,operation_id,actual_usdt,held_before_usdt),
    FOREIGN KEY(account_id,policy_hash,campaign_key) REFERENCES life_financial.spot_policies(account_id,policy_hash,campaign_id),
    FOREIGN KEY(account_id,campaign_event,family,campaign_scope,campaign_key,kind,amount)
        REFERENCES life_financial.budget_events(account_id,event_id,family,scope,window_key,kind,amount),
    FOREIGN KEY(account_id,session_event,family,session_scope,session_key,kind,amount)
        REFERENCES life_financial.budget_events(account_id,event_id,family,scope,window_key,kind,amount),
    FOREIGN KEY(account_id,day_event,family,day_scope,day_key,kind,amount)
        REFERENCES life_financial.budget_events(account_id,event_id,family,scope,window_key,kind,amount)
);
ALTER TABLE fill_projection_spike.order_basis ADD COLUMN prepare_role text GENERATED ALWAYS AS ('PREPARE') STORED;
ALTER TABLE fill_projection_spike.order_basis ADD CONSTRAINT prototype_prepare_subsidy_complete
    FOREIGN KEY(account_id,operation_id,intent_id,prepare_role)
        REFERENCES fill_projection_spike.subsidy_obligation DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE life_financial.operations
    ADD COLUMN prototype_subsidy_intent text GENERATED ALWAYS AS (CASE
        WHEN command->>'kind'='SPOT_FILL' AND receipt->>'state'='APPLIED' THEN command#>>'{command,intent_id}'
        WHEN command->>'kind'='SPOT_LIFECYCLE' AND command#>>'{command,action}'='ABORT_PREPARED' THEN command#>>'{command,intent_id}'
        WHEN command->>'kind'='SPOT_TERMINAL' AND receipt->'subsidy_settled'='true'::jsonb THEN command#>>'{command,intent_id}' END) STORED,
    ADD COLUMN prototype_subsidy_role text GENERATED ALWAYS AS (CASE WHEN command->>'kind'='SPOT_FILL' THEN 'FLOOR' ELSE 'RELEASE' END) STORED;
ALTER TABLE life_financial.operations ADD CONSTRAINT prototype_subsidy_operation_complete
    FOREIGN KEY(account_id,operation_id,prototype_subsidy_intent,prototype_subsidy_role)
        REFERENCES fill_projection_spike.subsidy_obligation DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE life_financial.spot_inventory_members
    ADD COLUMN prototype_release_role text GENERATED ALWAYS AS ('SETTLE_RELEASE') STORED,
    ADD COLUMN prototype_spend_role text GENERATED ALWAYS AS ('SETTLE_SPEND') STORED;
ALTER TABLE life_financial.spot_inventory_members ADD CONSTRAINT prototype_settlement_release_complete
    FOREIGN KEY(account_id,operation_id,intent_id,prototype_release_role)
        REFERENCES fill_projection_spike.subsidy_obligation DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE life_financial.spot_inventory_members ADD CONSTRAINT prototype_settlement_spend_complete
    FOREIGN KEY(account_id,operation_id,intent_id,prototype_spend_role)
        REFERENCES fill_projection_spike.subsidy_obligation DEFERRABLE INITIALLY DEFERRED;

CREATE FUNCTION fill_projection_spike.record_subsidy() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b fill_projection_spike.order_basis;h fill_projection_spike.fill_head;policy life_financial.spot_policies;
    role_name text;before_ordinal bigint;before_loss numeric;amount numeric;item record;
BEGIN
    FOR item IN
        SELECT x.intent_id,'PREPARE'::text AS role,NULL::numeric AS actual,NULL::numeric AS held FROM fill_projection_spike.order_basis x
            WHERE x.account_id=NEW.account_id AND x.operation_id=NEW.operation_id
        UNION ALL SELECT NEW.prototype_subsidy_intent,NEW.prototype_subsidy_role,NULL::numeric,NULL::numeric
            WHERE NEW.prototype_subsidy_intent IS NOT NULL
        UNION ALL SELECT m.intent_id,k.role,m.actual_usdt,m.held_before_usdt FROM life_financial.spot_inventory_members m
            CROSS JOIN (VALUES('SETTLE_RELEASE'),('SETTLE_SPEND')) k(role)
            WHERE m.account_id=NEW.account_id AND m.operation_id=NEW.operation_id LOOP
        role_name:=item.role;
        SELECT * INTO STRICT b FROM fill_projection_spike.order_basis WHERE account_id=NEW.account_id AND intent_id=item.intent_id;
        SELECT * INTO STRICT h FROM fill_projection_spike.fill_head WHERE account_id=NEW.account_id AND intent_id=item.intent_id;
        SELECT p.* INTO STRICT policy FROM life_financial.spot_policies p JOIN life_financial.accounts a USING(account_id,policy_hash) WHERE a.account_id=NEW.account_id;
        before_ordinal:=h.ordinal;
        IF role_name='FLOOR' THEN
            SELECT h.ordinal-count(*) INTO before_ordinal FROM fill_projection_spike.fill_source WHERE account_id=NEW.account_id AND source_operation_id=NEW.operation_id;
        END IF;
        SELECT loss_after INTO STRICT before_loss FROM fill_projection_spike.intent_prefix WHERE account_id=NEW.account_id
            AND intent_id=item.intent_id AND ordinal=before_ordinal;
        amount:=CASE role_name WHEN 'PREPARE' THEN b.subsidy WHEN 'RELEASE' THEN b.subsidy
            WHEN 'FLOOR' THEN greatest(b.subsidy,h.loss)-greatest(b.subsidy,before_loss)
            WHEN 'SETTLE_RELEASE' THEN item.held ELSE item.actual END;
        INSERT INTO fill_projection_spike.subsidy_obligation(account_id,operation_id,intent_id,role,amount,policy_hash,campaign_key,
            session_id,session_key,prepared_at,day_key,subsidy,before_ordinal,before_loss,after_ordinal,after_loss,actual_usdt,held_before_usdt)
        VALUES(NEW.account_id,NEW.operation_id,item.intent_id,role_name,amount,policy.policy_hash,policy.campaign_id,
            b.session_id,b.session_id::text,b.prepared_at,to_char(b.prepared_at AT TIME ZONE 'UTC','YYYY-MM-DD'),b.subsidy,
            before_ordinal,before_loss,h.ordinal,h.loss,item.actual,item.held);
    END LOOP;
    RETURN NULL;
END; $$;
-- PostgreSQL runs same-event triggers by name; original operation sealing and
-- head/hold publication must precede this causal obligation publication.
CREATE TRIGGER prototype_subsidy AFTER INSERT ON life_financial.operations
    FOR EACH ROW EXECUTE FUNCTION fill_projection_spike.record_subsidy();
REVOKE ALL ON ALL TABLES IN SCHEMA fill_projection_spike FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA fill_projection_spike FROM PUBLIC;
COMMIT;
