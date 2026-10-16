-- Empty relational contracts. No account, balance, owner or activation bootstrap.
-- Forward-only runner supplies idempotency and rejects unregistered schema drift.
CREATE SCHEMA life_financial;
REVOKE ALL ON SCHEMA life_financial FROM PUBLIC;

-- Unconstrained NUMERIC plus checks avoids typmod's silent input rounding.
CREATE DOMAIN life_financial.signed_amount AS numeric
    CHECK (VALUE > '-Infinity'::numeric AND VALUE < 'Infinity'::numeric
           AND abs(VALUE) < 1e38::numeric AND scale(VALUE) <= 18);
CREATE DOMAIN life_financial.amount AS life_financial.signed_amount CHECK (VALUE >= 0);
CREATE DOMAIN life_financial.positive_amount AS life_financial.amount CHECK (VALUE > 0);
CREATE DOMAIN life_financial.digest AS text CHECK (VALUE ~ '^[0-9a-f]{64}$');
CREATE DOMAIN life_financial.identity AS text CHECK (VALUE ~ '^[A-Za-z0-9_.:-]{1,128}$');

CREATE TABLE life_financial.schema_contract (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    format_version integer NOT NULL CHECK (format_version = 1),
    decimal_scale integer NOT NULL CHECK (decimal_scale = 18)
);
INSERT INTO life_financial.schema_contract VALUES (true, 1, 18);

CREATE TABLE life_financial.accounts (
    account_id uuid PRIMARY KEY,
    database_lineage uuid NOT NULL REFERENCES life_storage.storage_metadata(lineage),
    environment text NOT NULL CHECK (environment IN ('OFFLINE_SIMULATION','DEMO','PRODUCTION')),
    exchange text NOT NULL CHECK (exchange = 'okx'),
    account_uid text NOT NULL CHECK (account_uid ~ '^[0-9]{1,64}$'),
    risk_pool life_financial.identity NOT NULL,
    lineage uuid NOT NULL,
    schema_version integer NOT NULL CHECK (schema_version = 1),
    policy_hash life_financial.digest NOT NULL,
    config_hash life_financial.digest NOT NULL,
    mode text NOT NULL DEFAULT 'UNARMED' CHECK (mode IN ('UNARMED','ACTIVE','DRAINING','RECOVERY_ONLY')),
    owner_process uuid, owner_generation uuid, owner_epoch bigint NOT NULL DEFAULT 0 CHECK (owner_epoch >= 0),
    current_session uuid, owner_since timestamptz, owner_until timestamptz,
    halted boolean NOT NULL DEFAULT true,
    version bigint NOT NULL DEFAULT 0 CHECK (version >= 0),
    risk_epoch bigint NOT NULL DEFAULT 0 CHECK (risk_epoch >= 0),
    UNIQUE (environment,exchange,account_uid,risk_pool,lineage),
    CHECK ((mode='UNARMED' AND owner_process IS NULL AND owner_generation IS NULL AND current_session IS NULL
            AND owner_since IS NULL AND owner_until IS NULL)
        OR (mode<>'UNARMED' AND owner_process IS NOT NULL AND owner_generation IS NOT NULL AND owner_epoch > 0
            AND current_session IS NOT NULL AND owner_since IS NOT NULL AND owner_until IS NOT NULL
            AND isfinite(owner_since) AND isfinite(owner_until) AND owner_until > owner_since))
);
CREATE TABLE life_financial.policies (
    account_id uuid NOT NULL REFERENCES life_financial.accounts,
    policy_hash life_financial.digest NOT NULL, body jsonb NOT NULL CHECK (jsonb_typeof(body)='object'),
    PRIMARY KEY (account_id,policy_hash)
);
CREATE TABLE life_financial.configurations (
    account_id uuid NOT NULL REFERENCES life_financial.accounts,
    config_version bigint NOT NULL CHECK (config_version > 0),
    config_hash life_financial.digest NOT NULL, policy_hash life_financial.digest NOT NULL,
    body jsonb NOT NULL CHECK (jsonb_typeof(body)='object'),
    PRIMARY KEY (account_id,config_version), UNIQUE (account_id,config_hash),
    FOREIGN KEY (account_id,policy_hash) REFERENCES life_financial.policies
);
CREATE TABLE life_financial.sessions (
    account_id uuid NOT NULL REFERENCES life_financial.accounts, session_id uuid NOT NULL,
    epoch bigint NOT NULL CHECK (epoch > 0), config_version bigint NOT NULL,
    started_at timestamptz NOT NULL CHECK (isfinite(started_at)),
    expires_at timestamptz NOT NULL CHECK (isfinite(expires_at) AND expires_at > started_at),
    reference_mode text NOT NULL, model_version text NOT NULL,
    anchors jsonb NOT NULL CHECK (jsonb_typeof(anchors)='object'),
    PRIMARY KEY (account_id,session_id), UNIQUE (account_id,epoch),
    FOREIGN KEY (account_id,config_version) REFERENCES life_financial.configurations
);
ALTER TABLE life_financial.accounts ADD CONSTRAINT current_session_origin
    FOREIGN KEY (account_id,current_session) REFERENCES life_financial.sessions DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE life_financial.accounts ADD CONSTRAINT current_policy_origin
    FOREIGN KEY (account_id,policy_hash) REFERENCES life_financial.policies DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE life_financial.accounts ADD CONSTRAINT current_configuration_origin
    FOREIGN KEY (account_id,config_hash) REFERENCES life_financial.configurations(account_id,config_hash) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE life_financial.activation_grants (
    account_id uuid NOT NULL REFERENCES life_financial.accounts, grant_id uuid NOT NULL,
    process_id uuid NOT NULL, generation uuid NOT NULL, epoch bigint NOT NULL CHECK (epoch>0), session_id uuid NOT NULL,
    issued_at timestamptz NOT NULL CHECK (isfinite(issued_at)),
    expires_at timestamptz NOT NULL CHECK (isfinite(expires_at) AND expires_at>issued_at),
    signature bytea NOT NULL, PRIMARY KEY (account_id,grant_id),
    FOREIGN KEY (account_id,session_id) REFERENCES life_financial.sessions
);
CREATE TABLE life_financial.grant_consumptions (
    account_id uuid NOT NULL, grant_id uuid NOT NULL, operation_id life_financial.identity NOT NULL,
    PRIMARY KEY (account_id,grant_id), FOREIGN KEY (account_id,grant_id) REFERENCES life_financial.activation_grants
);
CREATE TABLE life_financial.operations (
    account_id uuid NOT NULL REFERENCES life_financial.accounts, operation_id life_financial.identity NOT NULL,
    fingerprint life_financial.digest NOT NULL, command jsonb NOT NULL, receipt jsonb NOT NULL,
    committed_version bigint NOT NULL CHECK (committed_version>0),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (account_id,operation_id), UNIQUE (account_id,committed_version)
);
CREATE TABLE life_financial.send_attempts (
    account_id uuid NOT NULL REFERENCES life_financial.accounts, intent_id life_financial.identity NOT NULL,
    operation_id life_financial.identity NOT NULL, wire_id life_financial.identity NOT NULL,
    product text NOT NULL CHECK (product IN ('SPOT','SWAP')), side text NOT NULL CHECK (side IN ('BUY','SELL')),
    session_id uuid NOT NULL, process_id uuid NOT NULL, generation uuid NOT NULL, epoch bigint NOT NULL CHECK (epoch>0),
    payload bytea NOT NULL CHECK (octet_length(payload) BETWEEN 1 AND 65536), payload_hash life_financial.digest NOT NULL,
    quantity life_financial.positive_amount NOT NULL, price life_financial.positive_amount NOT NULL,
    CHECK (payload_hash=encode(sha256(payload),'hex')),
    state text NOT NULL CHECK (state IN ('PREPARED','ARMED','SEND_UNKNOWN','ACKED','TERMINAL','ABORTED_BEFORE_SEND')),
    exchange_order_id text, version bigint NOT NULL DEFAULT 0 CHECK (version>=0),
    PRIMARY KEY (account_id,intent_id), UNIQUE (account_id,wire_id), UNIQUE (account_id,product,exchange_order_id),
    UNIQUE (account_id,product,intent_id), UNIQUE (account_id,product,wire_id),
    FOREIGN KEY (account_id,session_id) REFERENCES life_financial.sessions,
    FOREIGN KEY (account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX active_send_attempts ON life_financial.send_attempts(account_id,product,intent_id)
    WHERE state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND');
CREATE TABLE life_financial.balances (
    account_id uuid NOT NULL REFERENCES life_financial.accounts,
    asset text NOT NULL CHECK (asset IN ('LIFE','USDT')),
    total life_financial.amount NOT NULL, reserved life_financial.amount NOT NULL DEFAULT 0,
    liability life_financial.amount NOT NULL DEFAULT 0, version bigint NOT NULL DEFAULT 0 CHECK (version>=0),
    PRIMARY KEY (account_id,asset), CHECK (reserved <= total)
);
CREATE TABLE life_financial.reservations (
    account_id uuid NOT NULL REFERENCES life_financial.accounts, reservation_id life_financial.identity NOT NULL,
    operation_id life_financial.identity NOT NULL, intent_id life_financial.identity,
    asset text NOT NULL CHECK (asset IN ('LIFE','USDT')), amount life_financial.positive_amount NOT NULL,
    remaining life_financial.amount NOT NULL, state text NOT NULL CHECK (state IN ('HELD','CANCEL_REQUESTED','TERMINAL')),
    version bigint NOT NULL DEFAULT 0 CHECK (version>=0),
    PRIMARY KEY (account_id,reservation_id), CHECK (remaining<=amount), CHECK (state<>'TERMINAL' OR remaining=0),
    FOREIGN KEY (account_id,intent_id) REFERENCES life_financial.send_attempts,
    FOREIGN KEY (account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX held_reservations ON life_financial.reservations(account_id,asset,reservation_id) WHERE state<>'TERMINAL';
CREATE TABLE life_financial.capital_claims (
    account_id uuid NOT NULL REFERENCES life_financial.accounts, claim_id life_financial.identity NOT NULL,
    intent_id life_financial.identity NOT NULL, product text NOT NULL CHECK (product IN ('SPOT','SWAP')),
    margin_usdt life_financial.amount NOT NULL, fee_usdt life_financial.amount NOT NULL,
    stress_usdt life_financial.amount NOT NULL, state text NOT NULL CHECK (state IN ('HELD','TERMINAL')),
    version bigint NOT NULL DEFAULT 0 CHECK (version>=0), PRIMARY KEY (account_id,claim_id),
    FOREIGN KEY (account_id,product,intent_id) REFERENCES life_financial.send_attempts(account_id,product,intent_id)
);
CREATE INDEX active_capital_claims ON life_financial.capital_claims(account_id,product,claim_id) WHERE state='HELD';
CREATE TABLE life_financial.positions (
    account_id uuid NOT NULL REFERENCES life_financial.accounts, product text NOT NULL CHECK (product IN ('SPOT','SWAP')),
    position_key life_financial.identity NOT NULL, quantity life_financial.signed_amount NOT NULL,
    entry_price life_financial.amount NOT NULL, realized_pnl_usdt life_financial.signed_amount NOT NULL,
    fees_usdt life_financial.signed_amount NOT NULL, version bigint NOT NULL DEFAULT 0 CHECK (version>=0),
    PRIMARY KEY (account_id,product,position_key)
);
CREATE TABLE life_financial.financial_events (
    account_id uuid NOT NULL REFERENCES life_financial.accounts, event_id life_financial.identity NOT NULL,
    sequence bigint GENERATED ALWAYS AS IDENTITY,
    product text NOT NULL CHECK (product IN ('SPOT','SWAP','ACCOUNT')),
    kind text NOT NULL CHECK (kind IN ('ACK','FILL','FEE','CASHFLOW','FUNDING','PNL','TERMINAL','BUDGET','NAV')),
    exchange_identity text NOT NULL, occurred_at timestamptz NOT NULL CHECK (isfinite(occurred_at)),
    source_hash life_financial.digest NOT NULL, base_delta life_financial.signed_amount NOT NULL,
    quote_delta life_financial.signed_amount NOT NULL,
    PRIMARY KEY (account_id,event_id), UNIQUE (account_id,sequence), UNIQUE (account_id,product,kind,exchange_identity)
);
CREATE TABLE life_financial.fills (
    account_id uuid NOT NULL, product text NOT NULL CHECK (product IN ('SPOT','SWAP')), trade_id life_financial.identity NOT NULL,
    wire_id life_financial.identity NOT NULL, quantity life_financial.positive_amount NOT NULL,
    price life_financial.positive_amount NOT NULL, independent_price life_financial.positive_amount NOT NULL,
    fill_at timestamptz NOT NULL CHECK (isfinite(fill_at)), value_at timestamptz NOT NULL CHECK (isfinite(value_at)),
    source_hash life_financial.digest NOT NULL, PRIMARY KEY (account_id,product,trade_id),
    FOREIGN KEY (account_id,product,wire_id) REFERENCES life_financial.send_attempts(account_id,product,wire_id)
);
CREATE TABLE life_financial.fees (
    account_id uuid NOT NULL REFERENCES life_financial.accounts, payment_id life_financial.identity NOT NULL,
    product text NOT NULL CHECK (product IN ('SPOT','SWAP')), trade_id life_financial.identity NOT NULL,
    asset text NOT NULL CHECK (asset IN ('LIFE','USDT')), signed_fee life_financial.signed_amount NOT NULL,
    PRIMARY KEY (account_id,payment_id), FOREIGN KEY (account_id,product,trade_id) REFERENCES life_financial.fills
);
CREATE TABLE life_financial.bill_approvals (
    account_id uuid NOT NULL REFERENCES life_financial.accounts, bill_id life_financial.identity NOT NULL,
    asset text NOT NULL CHECK (asset IN ('LIFE','USDT')), signed_amount life_financial.signed_amount NOT NULL,
    transfer_at timestamptz NOT NULL CHECK (isfinite(transfer_at)), signature bytea NOT NULL,
    PRIMARY KEY (account_id,bill_id)
);
CREATE TABLE life_financial.cashflows (
    account_id uuid NOT NULL, bill_id life_financial.identity NOT NULL,
    independent_price life_financial.positive_amount NOT NULL, value_usdt life_financial.signed_amount NOT NULL,
    source_hash life_financial.digest NOT NULL, PRIMARY KEY (account_id,bill_id),
    FOREIGN KEY (account_id,bill_id) REFERENCES life_financial.bill_approvals
);
CREATE TABLE life_financial.attribution (
    account_id uuid NOT NULL, product text NOT NULL, trade_id life_financial.identity NOT NULL,
    horizon_ms bigint NOT NULL CHECK (horizon_ms>0), value_at timestamptz NOT NULL CHECK (isfinite(value_at)),
    markout_usdt life_financial.signed_amount NOT NULL, source_hash life_financial.digest NOT NULL,
    PRIMARY KEY (account_id,product,trade_id,horizon_ms), FOREIGN KEY (account_id,product,trade_id) REFERENCES life_financial.fills
);
CREATE TABLE life_financial.budget_buckets (
    account_id uuid NOT NULL REFERENCES life_financial.accounts, family text NOT NULL CHECK (family IN ('LOSS','SUBSIDY','STRESS')),
    scope text NOT NULL CHECK (scope IN ('SESSION','DAY','CAMPAIGN')), window_key life_financial.identity NOT NULL,
    budget_limit life_financial.positive_amount NOT NULL, committed life_financial.amount NOT NULL, incurred life_financial.amount NOT NULL,
    version bigint NOT NULL DEFAULT 0 CHECK (version>=0), PRIMARY KEY (account_id,family,scope,window_key)
    -- Actual incurred losses may exceed a limit: record truth, then deny new risk.
);
CREATE TABLE life_financial.budget_events (
    account_id uuid NOT NULL REFERENCES life_financial.accounts, event_id life_financial.identity NOT NULL,
    family text NOT NULL, scope text NOT NULL, window_key life_financial.identity NOT NULL,
    kind text NOT NULL CHECK (kind IN ('COMMIT','SPEND','RELEASE')), amount life_financial.amount NOT NULL,
    PRIMARY KEY (account_id,event_id), FOREIGN KEY (account_id,family,scope,window_key) REFERENCES life_financial.budget_buckets
);
CREATE TABLE life_financial.quote_actions (
    account_id uuid NOT NULL REFERENCES life_financial.accounts, action_id life_financial.identity NOT NULL,
    session_id uuid NOT NULL, side text NOT NULL CHECK (side IN ('BUY','SELL')), level integer NOT NULL CHECK (level>=0),
    state text NOT NULL CHECK (state IN ('PROPOSED','DISPATCHED','PREPARED','REJECTED','RECONCILED')),
    version bigint NOT NULL DEFAULT 0 CHECK (version>=0), PRIMARY KEY (account_id,action_id),
    FOREIGN KEY (account_id,session_id) REFERENCES life_financial.sessions
);
CREATE INDEX pending_quote_actions ON life_financial.quote_actions(account_id,session_id,action_id) WHERE state NOT IN ('REJECTED','RECONCILED');
CREATE TABLE life_financial.hedge_plans (
    account_id uuid NOT NULL REFERENCES life_financial.accounts, plan_id life_financial.identity NOT NULL,
    target_base life_financial.signed_amount NOT NULL, gross_attempts integer NOT NULL CHECK (gross_attempts>=0),
    incurred_usdt life_financial.amount NOT NULL, state text NOT NULL CHECK (state IN ('PLANNING','PLANNED','APPLYING','TERMINAL')),
    version bigint NOT NULL DEFAULT 0 CHECK (version>=0), PRIMARY KEY (account_id,plan_id)
);
CREATE TABLE life_financial.inventory_campaigns (
    account_id uuid NOT NULL REFERENCES life_financial.accounts, campaign_id life_financial.identity NOT NULL,
    deadline timestamptz NOT NULL CHECK (isfinite(deadline)), gross_turnover_base life_financial.amount NOT NULL,
    turnover_limit_base life_financial.positive_amount NOT NULL, version bigint NOT NULL DEFAULT 0 CHECK (version>=0),
    PRIMARY KEY (account_id,campaign_id)
);
CREATE TABLE life_financial.joint_receipts (
    account_id uuid NOT NULL REFERENCES life_financial.accounts, operation_id life_financial.identity NOT NULL,
    proof_hash life_financial.digest NOT NULL, state text NOT NULL CHECK (state IN ('APPLYING','APPLIED')),
    version bigint NOT NULL DEFAULT 0 CHECK (version>=0), PRIMARY KEY (account_id,operation_id)
);
CREATE TABLE life_financial.carry_observations (
    account_id uuid NOT NULL REFERENCES life_financial.accounts, observation_id life_financial.identity NOT NULL,
    observed_at timestamptz NOT NULL CHECK (isfinite(observed_at)), funding_rate life_financial.signed_amount NOT NULL,
    basis_bps life_financial.signed_amount NOT NULL, source_hash life_financial.digest NOT NULL,
    PRIMARY KEY (account_id,observation_id)
);
CREATE TABLE life_financial.recorder_outbox (
    account_id uuid NOT NULL REFERENCES life_financial.accounts, projection_id life_financial.identity NOT NULL,
    stream life_financial.identity NOT NULL, sequence bigint NOT NULL CHECK (sequence>0),
    body jsonb NOT NULL CHECK (jsonb_typeof(body)='object'), source_hash life_financial.digest NOT NULL,
    PRIMARY KEY (account_id,projection_id), UNIQUE (account_id,stream,sequence)
);
CREATE TABLE life_financial.projection_receipts (
    account_id uuid NOT NULL, projection_id life_financial.identity NOT NULL, applied_hash life_financial.digest NOT NULL,
    PRIMARY KEY (account_id,projection_id), FOREIGN KEY (account_id,projection_id) REFERENCES life_financial.recorder_outbox
);
CREATE TABLE life_financial.import_runs (
    account_id uuid NOT NULL REFERENCES life_financial.accounts, import_id life_financial.identity NOT NULL,
    source_hash life_financial.digest NOT NULL, target_lineage uuid NOT NULL,
    state text NOT NULL CHECK (state IN ('STAGING','APPLYING','APPLIED','FAILED')),
    version bigint NOT NULL DEFAULT 0 CHECK (version>=0), PRIMARY KEY (account_id,import_id)
);
CREATE TABLE life_financial.audit_checkpoints (
    account_id uuid NOT NULL REFERENCES life_financial.accounts, checkpoint_id life_financial.identity NOT NULL,
    through_sequence bigint NOT NULL CHECK (through_sequence>=0), history_hash life_financial.digest NOT NULL,
    independent_signature bytea, PRIMARY KEY (account_id,checkpoint_id)
);
CREATE TABLE life_financial.access_bindings (
    principal name NOT NULL, account_id uuid NOT NULL REFERENCES life_financial.accounts,
    can_write boolean NOT NULL DEFAULT false, PRIMARY KEY (principal,account_id)
);

CREATE FUNCTION life_financial.immutable_record() RETURNS trigger LANGUAGE plpgsql
    SET search_path=pg_catalog AS $$ BEGIN
    RAISE EXCEPTION 'LIFE_IMMUTABLE_HISTORY' USING ERRCODE='55000'; END; $$;
CREATE FUNCTION life_financial.version_record() RETURNS trigger LANGUAGE plpgsql
    SET search_path=pg_catalog AS $$ DECLARE k text; BEGIN
    IF NEW.version<>OLD.version+1 THEN
        RAISE EXCEPTION 'LIFE_ROW_VERSION_CONFLICT' USING ERRCODE='40001';
    END IF;
    IF NEW.account_id<>OLD.account_id THEN RAISE EXCEPTION 'LIFE_ACCOUNT_IMMUTABLE' USING ERRCODE='55000'; END IF;
    FOR k IN SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey)
        WHERE i.indrelid=TG_RELID AND i.indisprimary LOOP
        IF to_jsonb(NEW)->k IS DISTINCT FROM to_jsonb(OLD)->k THEN
            RAISE EXCEPTION 'LIFE_PRIMARY_IDENTITY_IMMUTABLE' USING ERRCODE='55000';
        END IF;
    END LOOP;
    RETURN NEW; END; $$;
DO $$ DECLARE t text; BEGIN
    FOREACH t IN ARRAY ARRAY['policies','configurations','sessions','activation_grants','grant_consumptions','operations',
        'financial_events','fills','fees','bill_approvals','cashflows','attribution','budget_events','carry_observations',
        'recorder_outbox','projection_receipts','audit_checkpoints'] LOOP
        EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',t);
    END LOOP;
    FOREACH t IN ARRAY ARRAY['balances','send_attempts','reservations','capital_claims','positions','budget_buckets',
        'quote_actions','hedge_plans','inventory_campaigns','joint_receipts','import_runs'] LOOP
        EXECUTE format('CREATE TRIGGER monotonic_version BEFORE UPDATE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.version_record()',t);
        EXECUTE format('CREATE TRIGGER retained_identity BEFORE DELETE ON life_financial.%I FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record()',t);
    END LOOP;
END; $$;
REVOKE ALL ON ALL TABLES IN SCHEMA life_financial FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA life_financial FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
